"""
PaperTradingService manages simulated futures positions via SQLite in the unified sentinel.db.
"""

import logging
import asyncio
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
import pandas as pd
from core.db import get_db_connection
from core.config import settings

logger = logging.getLogger(__name__)

class PaperTradingService:
    """Service to track simulated trading records."""
    
    def __init__(self, db_path: str = "data/sentinel.db", initial_capital: float = 10000.0) -> None:
        self.db_path: str = db_path
        self.initial_capital: float = initial_capital
        
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
        
        try:
            async with get_db_connection(self.db_path) as conn:
                cursor = await conn.execute('''
                    SELECT t.trade_id, t.direction, t.entry_price, t.sl_price, t.tp1_price, t.tp2_price, t.tp1_hit, t.leverage, e.mae_pct, e.mfe_pct, t.timestamp, t.ttl_mins
                    FROM trades t
                    JOIN evaluations e ON t.trade_id = e.trade_id
                    WHERE t.symbol = ? AND t.status = ?
                ''', (symbol, 'PENDING'))
                pending_trades = await cursor.fetchall()

                for trade in pending_trades:
                    trade_id, direction, entry, sl, tp1, tp2, tp1_hit, lev, mae, mfe, ts_str, ttl_mins = trade
                    if mae is None: mae = 0.0
                    if mfe is None: mfe = 0.0
                    if ttl_mins is None: ttl_mins = settings.trade_ttl_minutes
                    status, exit_price = 'PENDING', 0.0

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

                    fee_drag = (TAKER_FEE_RATE * 2 + DEFAULT_SLIPPAGE_PCT) * lev * 100
                    exit_candle = eval_df.iloc[-1]
                    
                    for _, candle in eval_df.iterrows():
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
                                
                        current_mae = 0.0
                        current_mfe = 0.0
                        
                        if direction.upper() == 'LONG':
                            current_mae = max(0.0, (entry - c_low) / entry * 100)
                            current_mfe = max(0.0, (c_high - entry) / entry * 100)
                            
                            long_hit_both = (c_high >= tp1) and (c_low <= sl) if not tp1_hit else (c_high >= tp2) and (c_low <= sl)
                            
                            if long_hit_both:
                                logger.warning(f"[EXECUTION: DUAL_WICK_RESOLVED] Symbol: {symbol} breached both TP and SL in candle. Result: Pessimistic SL Hit.")
                                status, exit_price = 'LOSS', sl
                                if tp1_hit: status = 'WIN_TP1'
                                exit_candle = candle
                                break
                            elif c_low <= sl:
                                status, exit_price = 'LOSS', sl
                                if tp1_hit: status = 'WIN_TP1'
                                exit_candle = candle
                                break
                            elif c_high >= tp1 and not tp1_hit:
                                tp1_hit = 1
                                sl = entry
                                logger.info(f"TP1 hit for LONG {symbol}! Moving SL to BE ({entry}).")
                                if c_high >= tp2:
                                    status, exit_price = 'WIN_TP2', tp2
                                    exit_candle = candle
                                    break
                            elif c_high >= tp2 and tp1_hit:
                                status, exit_price = 'WIN_TP2', tp2
                                exit_candle = candle
                                break
                                
                        elif direction.upper() == 'SHORT':
                            current_mae = max(0.0, (c_high - entry) / entry * 100)
                            current_mfe = max(0.0, (entry - c_low) / entry * 100)
                            
                            short_hit_both = (c_low <= tp1) and (c_high >= sl) if not tp1_hit else (c_low <= tp2) and (c_high >= sl)
                            
                            if short_hit_both:
                                logger.warning(f"[EXECUTION: DUAL_WICK_RESOLVED] Symbol: {symbol} breached both TP and SL in candle. Result: Pessimistic SL Hit.")
                                status, exit_price = 'LOSS', sl
                                if tp1_hit: status = 'WIN_TP1'
                                exit_candle = candle
                                break
                            elif c_high >= sl:
                                status, exit_price = 'LOSS', sl
                                if tp1_hit: status = 'WIN_TP1'
                                exit_candle = candle
                                break
                            elif c_low <= tp1 and not tp1_hit:
                                tp1_hit = 1
                                sl = entry
                                logger.info(f"TP1 hit for SHORT {symbol}! Moving SL to BE ({entry}).")
                                if c_low <= tp2:
                                    status, exit_price = 'WIN_TP2', tp2
                                    exit_candle = candle
                                    break
                            elif c_low <= tp2 and tp1_hit:
                                status, exit_price = 'WIN_TP2', tp2
                                exit_candle = candle
                                break
                                
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
                        loss_pct = (entry - sl) / entry if direction.upper() == 'LONG' else (sl - entry) / entry
                        
                        if status == 'LOSS':
                            gross_pnl_pct = -1.0 * loss_pct * lev * 100
                            net_pnl_pct = gross_pnl_pct - fee_drag
                        elif status == 'WIN_TP1':
                            gross_pnl_pct = (0.5 * tp1_gain_pct) * lev * 100
                            net_pnl_pct = gross_pnl_pct - (fee_drag * 0.75)
                        elif status == 'WIN_TP2':
                            gross_pnl_pct = (0.5 * tp1_gain_pct + 0.5 * tp2_gain_pct) * lev * 100
                            net_pnl_pct = gross_pnl_pct - fee_drag
                        elif status == 'EXPIRED':
                            pct_move = (exit_price - entry) / entry if direction.upper() == 'LONG' else (entry - exit_price) / entry
                            gross_pnl_pct = pct_move * lev * 100
                            net_pnl_pct = gross_pnl_pct - fee_drag
                            status = 'EXPIRED_PROFIT' if net_pnl_pct > 0 else 'EXPIRED_LOSS'
                        
                        await conn.execute('UPDATE trades SET status = ?, exit_price = ?, pnl_pct = ?, net_pnl_pct = ?, gross_pnl_pct = ? WHERE trade_id = ?', (status, exit_price, net_pnl_pct, net_pnl_pct, gross_pnl_pct, trade_id))
                        await conn.execute('UPDATE evaluations SET mae_pct = ?, mfe_pct = ? WHERE trade_id = ?', (mae, mfe, trade_id))
                        logger.info(f"Paper trade closed: {trade_id} {symbol} -> {status} with Net PnL {net_pnl_pct:.2f}% (Gross: {gross_pnl_pct:.2f}%) (MAE: {mae:.2f}%, MFE: {mfe:.2f}%)")

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

                        # Trigger Post-Trade Forensic Autopsy on LOSS
                        if status == 'LOSS' and journal_manager is not None and ai_evaluator is not None:
                            try:
                                trade_summary = {
                                    'trade_id': str(trade_id),
                                    'symbol': symbol,
                                    'direction': direction,
                                    'entry_price': entry,
                                    'sl_price': sl,
                                    'leverage': lev,
                                    'net_pnl_pct': net_pnl_pct
                                }
                                sl_candle = {
                                    'open': float(exit_candle['open']) if 'open' in exit_candle else float(exit_candle['close']),
                                    'high': float(exit_candle['high']),
                                    'low': float(exit_candle['low']),
                                    'close': float(exit_candle['close']),
                                    'volume': float(exit_candle['volume']) if 'volume' in exit_candle else 0.0
                                }
                                asyncio.create_task(
                                    self._trigger_autopsy(trade_summary, sl_candle, journal_manager, ai_evaluator, market_data, symbol)
                                )
                            except Exception as e:
                                logger.error(f"Failed to initiate autopsy task for {trade_id}: {e}")
                        
                    else:
                        await conn.execute('UPDATE trades SET tp1_hit = ?, sl_price = ? WHERE trade_id = ?', (tp1_hit, sl, trade_id))
                        await conn.execute('UPDATE evaluations SET mae_pct = ?, mfe_pct = ? WHERE trade_id = ?', (mae, mfe, trade_id))
                await conn.commit()
        except Exception as e:
            logger.error(f"Failed to update trades: {e}")

    async def get_open_positions(self, symbol: str):
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
                await conn.execute('UPDATE trades SET sl_price = ? WHERE trade_id = ?', (entry_price, trade_id))
                await conn.commit()
        except Exception as e:
            logger.error(f"Failed to move SL to BE for trade {trade_id}: {e}")

    async def get_current_drawdown(self) -> float:
        """Calculate maximum capital drawdown across completed trades."""
        try:
            async with get_db_connection(self.db_path) as conn:
                cursor = await conn.execute('SELECT pnl_pct FROM trades WHERE status != "PENDING" AND status != "REJECTED" AND status != "SHADOW"')
                trades = await cursor.fetchall()
                
                if not trades: return 0.0
                    
                capital = self.initial_capital
                peak = capital
                
                for trade in trades:
                    pnl_pct = trade[0]
                    profit = (capital * 0.01) * (pnl_pct / 100.0)
                    capital += profit
                    if capital > peak: peak = capital
                        
                if peak == 0: return 0.0
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
                    
                    await conn.execute('UPDATE trades SET status = ?, exit_price = ?, pnl_pct = ?, net_pnl_pct = ?, gross_pnl_pct = ? WHERE trade_id = ?', (reason, exit_price, net_pnl_pct, net_pnl_pct, gross_pnl_pct, trade_id))
                    await conn.commit()
                    logger.info(f"Position {trade_id} forcefully closed at {exit_price} ({reason}). Net PnL: {net_pnl_pct:.2f}% (Gross: {gross_pnl_pct:.2f}%)")
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

