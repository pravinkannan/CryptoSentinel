"""
PaperTradingService manages simulated futures positions via SQLite in the unified sentinel.db.
"""

import logging
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
import pandas as pd
from core.db import get_db_connection
from core.config import settings
from core.events import TradeClosedEvent
from core.event_bus import EventBus, get_event_bus

logger = logging.getLogger(__name__)

class PaperTradingService:
    """Service to track simulated trading records."""
    
    def __init__(
        self, 
        db_path: Optional[str] = None, 
        initial_capital: float = 10000.0,
        event_bus: Optional[EventBus] = None
    ) -> None:
        self.db_path: str = db_path if db_path is not None else getattr(settings, 'db_path', "data/sentinel.db")
        self.initial_capital: float = initial_capital
        self.event_bus: Optional[EventBus] = event_bus if event_bus is not None else get_event_bus()
        
    async def init_db(self) -> None:
        """Schema is managed by JournalManager in sentinel.db."""
        pass
            
    async def log_trade(self, **kwargs: Any) -> None:
        """Trade logging is handled by JournalManager in sentinel.db."""
        pass
            
    async def update_trades(
        self, 
        symbol: str, 
        df_5m: Optional[pd.DataFrame], 
        journal_manager: Any = None, 
        notifier: Any = None, 
        ai_evaluator: Any = None, 
        market_data: Any = None, 
        state_manager: Any = None
    ) -> None:
        """Check all pending trades for the given symbol against candles chronologically."""
        if df_5m is None or df_5m.empty: return
        
        TAKER_FEE_RATE = 0.0005
        DEFAULT_SLIPPAGE_PCT = 0.0003
        
        events_to_publish: List[TradeClosedEvent] = []
        try:
            async with get_db_connection(self.db_path) as conn:
                cursor = await conn.execute('''
                    SELECT t.trade_id, t.direction, t.entry_price, t.sl_price, t.tp1_price, t.tp2_price, t.tp1_hit, t.leverage, e.mae_pct, e.mfe_pct, t.timestamp, t.ttl_mins, t.trailing_sl
                    FROM trades t
                    JOIN evaluations e ON t.trade_id = e.trade_id
                    WHERE t.symbol = ? AND t.status = ?
                ''', (symbol, 'PENDING'))
                pending_trades = await cursor.fetchall()

                for trade in pending_trades:
                    trade_id, direction, entry, sl, tp1, tp2, tp1_hit, lev, mae, mfe, ts_str, ttl_mins, trailing_sl = trade
                    if mae is None: mae = 0.0
                    if mfe is None: mfe = 0.0
                    if ttl_mins is None: ttl_mins = settings.trade_ttl_minutes
                    current_sl = float(trailing_sl) if (trailing_sl is not None and float(trailing_sl) > 0) else float(sl)
                    status, exit_price = 'PENDING', 0.0

                    initial_risk = abs(entry - sl)
                    is_commodity = any(c in symbol for c in ["XAU", "PAXG", "GOLD"])
                    be_r_ratio = getattr(settings, 'be_trigger_rr_ratio_commodity', 0.30) if is_commodity else getattr(settings, 'be_trigger_rr_ratio', 0.50)
                    close_ratio = getattr(settings, 'tp1_close_ratio', 0.50)
                    rem_ratio = 1.0 - close_ratio

                    TRUE_BE_BUFFER = 0.00276  # Pillar 4: Fee + slippage buffer
                    if direction.upper() == 'LONG':
                        be_trigger = entry + (initial_risk * be_r_ratio)
                        be_sl_price = round(entry * (1.0 + TRUE_BE_BUFFER), 4 if entry < 10 else 2)
                    else:
                        be_trigger = entry - (initial_risk * be_r_ratio)
                        be_sl_price = round(entry * (1.0 - TRUE_BE_BUFFER), 4 if entry < 10 else 2)

                    already_be = (trailing_sl is not None) and (
                        (direction.upper() == 'LONG' and float(trailing_sl) >= be_sl_price) or
                        (direction.upper() == 'SHORT' and float(trailing_sl) <= be_sl_price)
                    )
                    already_tp1 = bool(tp1_hit)

                    try:
                        trade_time = datetime.fromisoformat(ts_str.replace(' UTC', '+00:00'))
                        if trade_time.tzinfo is None:
                            trade_time = trade_time.replace(tzinfo=timezone.utc)
                    except Exception:
                        trade_time = datetime.now(timezone.utc)

                    # Determine candidate candles for this trade
                    has_timestamps = 'timestamp' in df_5m.columns
                    if has_timestamps:
                        c_times = pd.to_datetime(df_5m['timestamp'], utc=True)
                        eval_df = df_5m[c_times >= trade_time]
                        if eval_df.empty:
                            if c_times.iloc[-1] < trade_time:
                                continue
                            eval_df = df_5m
                    else:
                        eval_df = df_5m

                    # Chronological Replay Guard:
                    # If candidate candles include the trade entry time (full replay from entry),
                    # initialize current_sl to initial sl and reset tp1_hit = 0 for this simulation pass.
                    # This prevents historical candles from prematurely triggering a stop-loss that
                    # was only ratcheted forward after reaching 1.0R / TP1.
                    is_full_replay = False
                    if has_timestamps and not eval_df.empty:
                        first_candle_time = pd.to_datetime(eval_df.iloc[0]['timestamp'], utc=True)
                        if (first_candle_time - trade_time).total_seconds() <= 300:
                            is_full_replay = True

                    if is_full_replay:
                        current_sl = float(sl)
                        tp1_hit = 0
                    else:
                        current_sl = float(trailing_sl) if (trailing_sl is not None and float(trailing_sl) > 0) else float(sl)

                    fee_drag = (TAKER_FEE_RATE * 2 + DEFAULT_SLIPPAGE_PCT) * lev * 100
                    exit_candle = eval_df.iloc[-1]
                    
                    candle_count = 0
                    max_bars = getattr(settings, 'momentum_decay_bars', 8)
                    for _, candle in eval_df.iterrows():
                        candle_count += 1
                        c_high = float(candle['high'])
                        c_low = float(candle['low'])
                        c_close = float(candle['close'])
                        
                        # Check expiration at this candle
                        if has_timestamps and 'timestamp' in candle:
                            c_time = candle['timestamp']
                            if isinstance(c_time, str):
                                c_time = pd.to_datetime(c_time)
                            if hasattr(c_time, 'tz_localize') and c_time.tzinfo is None:
                                c_time = c_time.tz_localize('UTC')
                            elif hasattr(c_time, 'tz_convert') and c_time.tzinfo is not None:
                                c_time = c_time.tz_convert('UTC')
                            
                            if (c_time - trade_time).total_seconds() > ttl_mins * 60:
                                status, exit_price = 'EXPIRED', c_close
                                exit_candle = candle
                                logger.info(f"Trade {trade_id} for {symbol} expired at candle {c_time} after {ttl_mins} mins. Closing at market ({c_close}).")
                                break
                        else:
                            if (datetime.now(timezone.utc) - trade_time).total_seconds() > ttl_mins * 60:
                                status, exit_price = 'EXPIRED', c_close
                                exit_candle = candle
                                logger.info(f"Trade {trade_id} for {symbol} expired after {ttl_mins} mins. Closing at market ({c_close}).")
                                break
                                
                        # Natural trade lifecycle to TP1, TP2, BE, or TTL
                        pass

                        current_mae = 0.0
                        current_mfe = 0.0
                        
                        if direction.upper() == 'LONG':
                            current_mae = max(0.0, (entry - c_low) / entry * 100)
                            current_mfe = max(0.0, (c_high - entry) / entry * 100)
                            
                            candle_sl = current_sl
                            target_tp = tp2 if tp1_hit else tp1
                            long_hit_both = (c_high >= target_tp) and (c_low <= candle_sl)
                            
                            if long_hit_both:
                                logger.warning(f"[EXECUTION: DUAL_WICK_RESOLVED] Symbol: {symbol} breached both TP and SL in candle. Result: Pessimistic SL Hit.")
                                if tp1_hit:
                                    status, exit_price = 'WIN_TP1', candle_sl
                                elif candle_sl >= entry:
                                    status, exit_price = 'BREAKEVEN', candle_sl
                                else:
                                    status, exit_price = 'LOSS', candle_sl
                                exit_candle = candle
                                break
                            elif c_low <= candle_sl:
                                if tp1_hit:
                                    status, exit_price = 'WIN_TP1', candle_sl
                                elif candle_sl >= entry:
                                    status, exit_price = 'BREAKEVEN', candle_sl
                                else:
                                    status, exit_price = 'LOSS', candle_sl
                                exit_candle = candle
                                break
                            elif c_high >= tp1 and not tp1_hit:
                                tp1_hit = 1
                                current_sl = max(current_sl, be_sl_price)
                                if not already_tp1:
                                    already_tp1 = True
                                    logger.info(f"🎯 TP1 Hit for LONG {symbol}! Moving SL to BE ({be_sl_price}).")
                                if c_high >= tp2:
                                    status, exit_price = 'WIN_TP2', tp2
                                    exit_candle = candle
                                    break
                            elif c_high >= tp2 and tp1_hit:
                                status, exit_price = 'WIN_TP2', tp2
                                exit_candle = candle
                                break
                            elif c_high >= be_trigger and current_sl < be_sl_price:
                                current_sl = be_sl_price
                                if not already_be:
                                    already_be = True
                                    logger.info(f"🛡️ Auto-Breakeven 1.0R reached for LONG {symbol}! Moving SL to BE ({be_sl_price}).")
                                
                        elif direction.upper() == 'SHORT':
                            current_mae = max(0.0, (c_high - entry) / entry * 100)
                            current_mfe = max(0.0, (entry - c_low) / entry * 100)
                            
                            candle_sl = current_sl
                            target_tp = tp2 if tp1_hit else tp1
                            short_hit_both = (c_low <= target_tp) and (c_high >= candle_sl)
                            
                            if short_hit_both:
                                logger.warning(f"[EXECUTION: DUAL_WICK_RESOLVED] Symbol: {symbol} breached both TP and SL in candle. Result: Pessimistic SL Hit.")
                                if tp1_hit:
                                    status, exit_price = 'WIN_TP1', candle_sl
                                elif candle_sl <= entry:
                                    status, exit_price = 'BREAKEVEN', candle_sl
                                else:
                                    status, exit_price = 'LOSS', candle_sl
                                exit_candle = candle
                                break
                            elif c_high >= candle_sl:
                                if tp1_hit:
                                    status, exit_price = 'WIN_TP1', candle_sl
                                elif candle_sl <= entry:
                                    status, exit_price = 'BREAKEVEN', candle_sl
                                else:
                                    status, exit_price = 'LOSS', candle_sl
                                exit_candle = candle
                                break
                            elif c_low <= tp1 and not tp1_hit:
                                tp1_hit = 1
                                current_sl = min(current_sl, be_sl_price)
                                if not already_tp1:
                                    already_tp1 = True
                                    logger.info(f"🎯 TP1 Hit for SHORT {symbol}! Moving SL to BE ({be_sl_price}).")
                                if c_low <= tp2:
                                    status, exit_price = 'WIN_TP2', tp2
                                    exit_candle = candle
                                    break
                            elif c_low <= tp2 and tp1_hit:
                                status, exit_price = 'WIN_TP2', tp2
                                exit_candle = candle
                                break
                            elif c_low <= be_trigger and current_sl > be_sl_price:
                                current_sl = be_sl_price
                                if not already_be:
                                    already_be = True
                                    logger.info(f"🛡️ Auto-Breakeven 1.0R reached for SHORT {symbol}! Moving SL to BE ({be_sl_price}).")
                                
                        mae = max(mae, current_mae)
                        mfe = max(mfe, current_mfe)
                        
                    if status == 'PENDING':
                        if (datetime.now(timezone.utc) - trade_time).total_seconds() > ttl_mins * 60:
                            c_close_latest = float(eval_df.iloc[-1]['close'])
                            status, exit_price = 'EXPIRED', c_close_latest
                            exit_candle = eval_df.iloc[-1]
                            logger.info(f"Trade {trade_id} for {symbol} expired by elapsed wall-clock ({ttl_mins} mins). Closing at market ({exit_price}).")

                    if status != 'PENDING':
                        tp1_gain_pct = (tp1 - entry) / entry if direction.upper() == 'LONG' else (entry - tp1) / entry
                        tp2_gain_pct = (tp2 - entry) / entry if direction.upper() == 'LONG' else (entry - tp2) / entry
                        rem_gain_pct = (exit_price - entry) / entry if direction.upper() == 'LONG' else (entry - exit_price) / entry
                        loss_pct = (entry - exit_price) / entry if direction.upper() == 'LONG' else (exit_price - entry) / entry
                        
                        if status == 'LOSS':
                            gross_pnl_pct = -1.0 * loss_pct * lev * 100
                            net_pnl_pct = gross_pnl_pct - fee_drag
                        elif status == 'BREAKEVEN':
                            gross_pnl_pct = rem_gain_pct * lev * 100
                            net_pnl_pct = gross_pnl_pct - fee_drag
                        elif status == 'WIN_TP1':
                            gross_pnl_pct = (close_ratio * tp1_gain_pct + rem_ratio * rem_gain_pct) * lev * 100
                            net_pnl_pct = gross_pnl_pct - fee_drag
                        elif status == 'WIN_TP2':
                            gross_pnl_pct = (close_ratio * tp1_gain_pct + rem_ratio * tp2_gain_pct) * lev * 100
                            net_pnl_pct = gross_pnl_pct - fee_drag
                        elif status in ['EXPIRED', 'MOMENTUM_DECAY']:
                            pct_move = (exit_price - entry) / entry if direction.upper() == 'LONG' else (entry - exit_price) / entry
                            gross_pnl_pct = pct_move * lev * 100
                            net_pnl_pct = gross_pnl_pct - fee_drag
                            if status == 'EXPIRED':
                                status = 'EXPIRED_PROFIT' if net_pnl_pct > 0 else 'EXPIRED_LOSS'
                            else:
                                status = 'MOMENTUM_DECAY_PROFIT' if net_pnl_pct > 0 else 'MOMENTUM_DECAY_LOSS'
                        
                        now_close_iso = datetime.now(timezone.utc).isoformat()
                        try:
                            await conn.execute('UPDATE trades SET status = ?, exit_price = ?, pnl_pct = ?, net_pnl_pct = ?, gross_pnl_pct = ?, close_time = ? WHERE trade_id = ?', (status, exit_price, net_pnl_pct, net_pnl_pct, gross_pnl_pct, now_close_iso, trade_id))
                        except Exception:
                            await conn.execute('UPDATE trades SET status = ?, exit_price = ?, pnl_pct = ?, net_pnl_pct = ?, gross_pnl_pct = ? WHERE trade_id = ?', (status, exit_price, net_pnl_pct, net_pnl_pct, gross_pnl_pct, trade_id))
                        await conn.execute('UPDATE evaluations SET mae_pct = ?, mfe_pct = ? WHERE trade_id = ?', (mae, mfe, trade_id))
                        logger.info(f"Paper trade closed: {trade_id} {symbol} -> {status} with Net PnL {net_pnl_pct:.2f}% (Gross: {gross_pnl_pct:.2f}%) (MAE: {mae:.2f}%, MFE: {mfe:.2f}%)")

                        # Buffer TradeClosedEvent to publish after DB commit to prevent database lock contention
                        if self.event_bus:
                            exit_candle_dict = None
                            if exit_candle is not None:
                                try:
                                    exit_candle_dict = {
                                        'open': float(exit_candle['open']) if 'open' in exit_candle else float(exit_candle['close']),
                                        'high': float(exit_candle['high']),
                                        'low': float(exit_candle['low']),
                                        'close': float(exit_candle['close']),
                                        'volume': float(exit_candle['volume']) if 'volume' in exit_candle else 0.0
                                    }
                                except Exception:
                                    exit_candle_dict = None

                            closed_event = TradeClosedEvent(
                                trade_id=str(trade_id),
                                symbol=symbol,
                                direction=direction,
                                exit_price=exit_price,
                                pnl_pct=net_pnl_pct,
                                outcome=status,
                                exit_reason=status,
                                entry_price=entry,
                                leverage=lev,
                                gross_pnl_pct=gross_pnl_pct,
                                net_pnl_pct=net_pnl_pct,
                                mae_pct=mae,
                                mfe_pct=mfe,
                                exit_candle=exit_candle_dict
                            )
                            events_to_publish.append(closed_event)

                        # Evict closed trade from active_signals state immediately so it no longer blocks future setups
                        if state_manager and hasattr(state_manager, 'state') and isinstance(state_manager.state.get("active_signals"), dict):
                            active_sigs = state_manager.state["active_signals"]
                            keys_to_remove = [
                                k for k, sig in active_sigs.items()
                                if (sig.get('trade_id') == str(trade_id)) or (k.startswith(f"{symbol}_") and sig.get('direction') == direction)
                            ]
                            for k in keys_to_remove:
                                del active_sigs[k]
                                logger.info(f"Evicted closed trade {trade_id} ({k}) from active_signals state (Outcome: {status}).")

                    else:
                        await conn.execute('UPDATE trades SET tp1_hit = ?, trailing_sl = ? WHERE trade_id = ?', (tp1_hit, current_sl, trade_id))
                        await conn.execute('UPDATE evaluations SET mae_pct = ?, mfe_pct = ? WHERE trade_id = ?', (mae, mfe, trade_id))
                await conn.commit()

            # Publish all closed trade events after DB transaction is committed and closed
            for event in events_to_publish:
                try:
                    await self.event_bus.publish(event)
                except Exception as e:
                    logger.error(f"Failed to publish TradeClosedEvent for {event.trade_id}: {e}")
        except Exception as e:
            logger.error(f"Failed to update trades: {e}")

    async def get_open_positions(self, symbol: str) -> List[Dict[str, Any]]:
        try:
            async with get_db_connection(self.db_path) as conn:
                cursor = await conn.execute('''
                    SELECT t.trade_id, t.direction, t.entry_price, t.sl_price, t.leverage, t.pnl_pct, COALESCE(e.confidence_score, 0.0)
                    FROM trades t
                    LEFT JOIN evaluations e ON t.trade_id = e.trade_id
                    WHERE t.symbol = ? AND t.status = "PENDING"
                ''', (symbol,))
                rows = await cursor.fetchall()
                positions = []
                for r in rows:
                    positions.append({
                        'id': r[0],
                        'direction': r[1],
                        'entry': r[2],
                        'stop_loss': r[3],
                        'leverage': r[4],
                        'pnl_pct': r[5],
                        'confidence_score': float(r[6])
                    })
                return positions
        except Exception as e:
            logger.error(f"Failed to fetch open positions for {symbol}: {e}")
            return []

    async def is_trade_active(self, symbol: str, trade_id: Optional[str] = None, direction: Optional[str] = None) -> Optional[bool]:
        """
        Check if a trade is currently active (PENDING).
        Returns:
            True: If the trade is currently PENDING in the DB.
            False: If the trade exists and has completed (e.g. LOSS, WIN_TP1, WIN_TP2, EXPIRED, REVERSAL_FLIP)
                   or if there are closed trades but zero PENDING trades for this symbol/direction.
            None: If no trade records exist for this symbol/direction.
        """
        try:
            async with get_db_connection(self.db_path) as conn:
                if trade_id:
                    cursor = await conn.execute(
                        'SELECT status FROM trades WHERE trade_id = ?', (trade_id,)
                    )
                    row = await cursor.fetchone()
                    if row:
                        return bool(row[0] == 'PENDING')

                if direction:
                    cursor = await conn.execute(
                        'SELECT status FROM trades WHERE symbol = ? AND direction = ? ORDER BY timestamp DESC LIMIT 1',
                        (symbol, direction)
                    )
                    row = await cursor.fetchone()
                    if row:
                        if row[0] == 'PENDING':
                            return True
                        else:
                            c2 = await conn.execute(
                                'SELECT COUNT(*) FROM trades WHERE symbol = ? AND direction = ? AND status = "PENDING"',
                                (symbol, direction)
                            )
                            row_count = await c2.fetchone()
                            pending_count = int(row_count[0]) if row_count else 0
                            return pending_count > 0
                return None
        except Exception as e:
            logger.error(f"Error checking is_trade_active for {symbol} ({trade_id}): {e}")
            return None

    async def move_sl_to_be(self, trade_id: str, entry_price: float):
        try:
            async with get_db_connection(self.db_path) as conn:
                await conn.execute('UPDATE trades SET sl_price = ?, trailing_sl = ? WHERE trade_id = ?', (entry_price, entry_price, trade_id))
                await conn.commit()
                logger.info(f"Stop loss ratcheted to Breakeven (${entry_price}) for trade {trade_id}")
        except Exception as e:
            logger.error(f"Failed to move SL to BE for trade {trade_id}: {e}")

    async def get_current_drawdown(self) -> float:
        """
        Calculate current peak-to-trough equity drawdown across completed trades,
        chronologically sorted by close_time (or timestamp) with dynamic position sizing.
        """
        try:
            async with get_db_connection(self.db_path) as conn:
                cursor = await conn.execute('''
                    SELECT 
                        COALESCE(net_pnl_pct, pnl_pct, 0.0), 
                        COALESCE(leverage, 1.0), 
                        COALESCE(entry_price, 0.0), 
                        COALESCE(sl_price, 0.0)
                    FROM trades 
                    WHERE status NOT IN ('PENDING', 'REJECTED', 'SHADOW')
                    ORDER BY COALESCE(close_time, timestamp) ASC
                ''')
                trades = await cursor.fetchall()
                
                if not trades:
                    return 0.0
                    
                capital = self.initial_capital
                peak = capital
                risk_pct = getattr(settings, 'risk_per_trade_percent', 2.0)
                default_pos_pct = getattr(settings, 'position_size_percent', 10.0)
                
                for row in trades:
                    return_pct = float(row[0] or 0.0)
                    leverage = max(1.0, float(row[1] or 1.0))
                    entry = float(row[2] or 0.0)
                    sl = float(row[3] or 0.0)
                    
                    if entry > 0 and sl > 0 and abs(entry - sl) > 0:
                        sl_distance_pct = abs(entry - sl) / entry
                        loss_at_sl = sl_distance_pct * leverage
                        if loss_at_sl > 0:
                            risk_dollars = capital * (risk_pct / 100.0)
                            margin_allocated = min(capital * 0.5, risk_dollars / loss_at_sl)
                        else:
                            margin_allocated = capital * (default_pos_pct / 100.0)
                    else:
                        margin_allocated = capital * (default_pos_pct / 100.0)
                        
                    profit = margin_allocated * (return_pct / 100.0)
                    capital = max(0.0, capital + profit)
                    if capital > peak:
                        peak = capital
                        
                if peak <= 0:
                    return 0.0
                return max(0.0, ((peak - capital) / peak) * 100.0)
        except Exception as e:
            logger.error(f"Failed to calculate drawdown: {e}")
            return 0.0

    async def close_position(self, trade_id: str, exit_price: float, reason: str = "MARKET_CLOSE"):
        try:
            async with get_db_connection(self.db_path) as conn:
                cursor = await conn.execute('SELECT direction, entry_price, leverage FROM trades WHERE trade_id = ? AND status = "PENDING"', (trade_id,))
                trade = await cursor.fetchone()
                if trade:
                    direction, entry, lev = trade
                    pct_move = (exit_price - entry) / entry if direction.upper() == 'LONG' else (entry - exit_price) / entry
                    
                    TAKER_FEE_RATE = 0.0005
                    DEFAULT_SLIPPAGE_PCT = 0.0003
                    fee_drag = (TAKER_FEE_RATE * 2 + DEFAULT_SLIPPAGE_PCT) * lev * 100
                    
                    gross_pnl_pct = pct_move * lev * 100
                    net_pnl_pct = gross_pnl_pct - fee_drag
                    
                    now_close_iso = datetime.now(timezone.utc).isoformat()
                    try:
                        await conn.execute('UPDATE trades SET status = ?, exit_price = ?, pnl_pct = ?, net_pnl_pct = ?, gross_pnl_pct = ?, close_time = ? WHERE trade_id = ?', (reason, exit_price, net_pnl_pct, net_pnl_pct, gross_pnl_pct, now_close_iso, trade_id))
                    except Exception:
                        await conn.execute('UPDATE trades SET status = ?, exit_price = ?, pnl_pct = ?, net_pnl_pct = ?, gross_pnl_pct = ? WHERE trade_id = ?', (reason, exit_price, net_pnl_pct, net_pnl_pct, gross_pnl_pct, trade_id))
                    await conn.commit()
                    logger.info(f"Position {trade_id} forcefully closed at {exit_price} ({reason}). Net PnL: {net_pnl_pct:.2f}% (Gross: {gross_pnl_pct:.2f}%)")

                    if self.event_bus:
                        closed_event = TradeClosedEvent(
                            trade_id=trade_id,
                            symbol=trade_id.split('_')[0] if '_' in trade_id else "UNKNOWN",
                            direction=direction,
                            exit_price=exit_price,
                            pnl_pct=net_pnl_pct,
                            outcome=reason,
                            exit_reason=reason,
                            entry_price=entry,
                            leverage=lev,
                            gross_pnl_pct=gross_pnl_pct,
                            net_pnl_pct=net_pnl_pct
                        )
                        await self.event_bus.publish(closed_event)
        except Exception as e:
            logger.error(f"Failed to close position: {e}")

    async def _trigger_autopsy(
        self, 
        trade_summary: Dict[str, Any], 
        sl_candle: Dict[str, Any], 
        journal_manager: Any, 
        ai_evaluator: Any, 
        market_data: Any, 
        symbol: str
    ) -> None:
        """Asynchronously requests an AI autopsy and saves it to sentinel.db."""
        try:
            microstructure = {}
            if market_data:
                microstructure = await market_data.get_market_microstructure(symbol)
            autopsy_report = await ai_evaluator.generate_autopsy(trade_summary, sl_candle, microstructure)
            autopsy_data = {**trade_summary, **autopsy_report}
            await journal_manager.save_autopsy(autopsy_data)
            logger.info(f"Post-trade autopsy saved for trade {trade_summary['trade_id']}: {autopsy_report.get('failure_category')}")
        except Exception as e:
            logger.error(f"Error executing autopsy task for trade {trade_summary.get('trade_id')}: {e}")

    async def reconcile_offline_gap(
        self,
        market_data: Any,
        journal_manager: Any = None,
        notifier: Any = None,
        ai_evaluator: Any = None,
        state_manager: Any = None
    ) -> Dict[str, Any]:
        """
        Reconciles pending trades across an offline polling gap.
        When Sentinel boots up after being offline, fetches missing historical candles
        since each trade's entry and replays them through update_trades so Stop Losses,
        Take Profits, and TTL expirations resolve accurately at their historical price
        instead of lagging or teleporting to today's price.
        """
        reconciled_count = 0
        closed_count = 0
        still_pending_count = 0

        try:
            async with get_db_connection(self.db_path) as conn:
                cursor = await conn.execute('''
                    SELECT trade_id, symbol, direction, entry_price, sl_price, tp1_price, tp2_price, leverage, timestamp, ttl_mins
                    FROM trades
                    WHERE status = 'PENDING'
                    ORDER BY COALESCE(timestamp, '') ASC
                ''')
                pending_trades = await cursor.fetchall()

            if not pending_trades:
                logger.info("🛡️ [RECONCILIATION] No pending trades found to reconcile across offline gap.")
                return {
                    "status": "CLEAN",
                    "total_checked": 0,
                    "closed": 0,
                    "active": 0
                }

            logger.info(f"🔄 [RECONCILIATION] Auditing {len(pending_trades)} pending trade(s) for offline gap catch-up...")
            now_utc = datetime.now(timezone.utc)

            for trade in pending_trades:
                trade_id, symbol, direction, entry, sl, tp1, tp2, lev, ts_str, ttl_mins = trade
                ttl_mins = float(ttl_mins or getattr(settings, 'trade_ttl_minutes', 1080))
                
                try:
                    trade_time = datetime.fromisoformat(str(ts_str).replace(' UTC', '+00:00'))
                    if trade_time.tzinfo is None:
                        trade_time = trade_time.replace(tzinfo=timezone.utc)
                except Exception:
                    trade_time = now_utc

                gap_seconds = (now_utc - trade_time).total_seconds()
                gap_mins = gap_seconds / 60.0

                # If the trade is very fresh (< 10 mins), normal cycle handles it
                if gap_mins < 10.0:
                    still_pending_count += 1
                    continue

                reconciled_count += 1
                sym_tfs = settings.symbol_timeframes.get(symbol, settings.timeframes) if (hasattr(settings, 'symbol_timeframes') and symbol in settings.symbol_timeframes) else getattr(settings, 'timeframes', ['5m'])
                base_tf = sym_tfs[0] if sym_tfs else '5m'

                df_catchup = None
                if market_data:
                    since_ms = int(trade_time.timestamp() * 1000)
                    try:
                        # Fetch up to 1000 candles starting from trade entry
                        df_catchup = await market_data.fetch_ohlcv(symbol, base_tf, limit=1000, since=since_ms)
                    except Exception as ex:
                        logger.warning(f"Failed to fetch catch-up candles for {symbol} ({trade_id}): {ex}")

                if df_catchup is not None and not df_catchup.empty:
                    logger.info(f"🔄 [RECONCILIATION] Replaying {len(df_catchup)} {base_tf} candles for {symbol} ({trade_id}) to bridge {gap_mins:.1f}m offline gap...")
                    await self.update_trades(
                        symbol=symbol,
                        df_5m=df_catchup,
                        journal_manager=journal_manager,
                        notifier=notifier,
                        ai_evaluator=ai_evaluator,
                        market_data=market_data,
                        state_manager=state_manager
                    )
                else:
                    # If exchange data could not be retrieved and trade exceeded TTL, close as EXPIRED
                    if gap_mins > ttl_mins:
                        logger.warning(f"⚠️ [RECONCILIATION] Trade {trade_id} exceeded TTL ({ttl_mins}m) during offline gap ({gap_mins:.1f}m) with no candle data. Closing as EXPIRED.")
                        await self.close_position(trade_id, exit_price=float(entry or 0.0), reason="EXPIRED_OFFLINE")
                        if state_manager and "active_signals" in state_manager.state:
                            state_manager.state["active_signals"] = {
                                k: v for k, v in state_manager.state["active_signals"].items()
                                if v.get("trade_id") != trade_id
                            }
                            await state_manager.save_state()

                # Check if it was closed during reconciliation
                is_still_active = await self.is_trade_active(symbol, trade_id=trade_id)
                if is_still_active:
                    still_pending_count += 1
                else:
                    closed_count += 1

            logger.info(f"✅ [RECONCILIATION] Offline gap reconciliation finished: {closed_count} closed, {still_pending_count} active.")
            return {
                "status": "RECONCILED",
                "total_checked": len(pending_trades),
                "reconciled": reconciled_count,
                "closed": closed_count,
                "active": still_pending_count
            }

        except Exception as e:
            logger.error(f"❌ [RECONCILIATION] Error during offline reconciliation: {e}", exc_info=True)
            return {"status": "ERROR", "error": str(e)}

