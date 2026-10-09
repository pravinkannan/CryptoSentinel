"""
PaperTradingService manages simulated futures positions via SQLite in the unified sentinel.db.
"""

import aiosqlite
import logging
import re
from datetime import datetime, timezone
from core.exceptions import DatabaseError
from core.db import get_db_connection

logger = logging.getLogger(__name__)

class PaperTradingService:
    """Service to track simulated trading records."""
    
    def __init__(self, db_path: str = "data/sentinel.db", initial_capital: float = 10000.0):
        self.db_path = db_path
        self.initial_capital = initial_capital
        
    async def init_db(self) -> None:
        # Schema is now managed by JournalManager in sentinel.db
        pass
            
    async def log_trade(self, **kwargs) -> None:
        # Trade logging is now entirely handled by JournalManager in sentinel.db
        pass
            
    async def update_trades(self, symbol: str, df_5m, journal_manager=None, notifier=None) -> None:
        """Check all pending trades for the given symbol against the current candle."""
        if df_5m is None or df_5m.empty: return
        candle = df_5m.iloc[-1]
        c_high, c_low, c_close = float(candle['high']), float(candle['low']), float(candle['close'])
        
        TAKER_FEE_RATE = 0.0005
        DEFAULT_SLIPPAGE_PCT = 0.0003
        
        try:
            async with get_db_connection(self.db_path) as conn:
                cursor = await conn.execute('''
                    SELECT t.trade_id, t.direction, t.entry_price, t.sl_price, t.tp1_price, t.tp2_price, t.tp1_hit, t.leverage, e.mae_pct, e.mfe_pct, t.timestamp
                    FROM trades t
                    JOIN evaluations e ON t.trade_id = e.trade_id
                    WHERE t.symbol = ? AND t.status = ?
                ''', (symbol, 'PENDING'))
                pending_trades = await cursor.fetchall()
                
                for trade in pending_trades:
                    trade_id, direction, entry, sl, tp1, tp2, tp1_hit, lev, mae, mfe, ts_str = trade
                    if mae is None: mae = 0.0
                    if mfe is None: mfe = 0.0
                    status, exit_price = 'PENDING', 0.0
                    
                    fee_drag = (TAKER_FEE_RATE * 2 + DEFAULT_SLIPPAGE_PCT) * lev * 100
                    
                    if direction.upper() == 'LONG':
                        current_mae = max(0.0, (entry - c_low) / entry * 100)
                        current_mfe = max(0.0, (c_high - entry) / entry * 100)
                        
                        long_hit_both = (c_high >= tp1) and (c_low <= sl) if not tp1_hit else (c_high >= tp2) and (c_low <= sl)
                        
                        if long_hit_both:
                            logger.warning(f"[EXECUTION: DUAL_WICK_RESOLVED] Symbol: {symbol} breached both TP and SL in candle. Result: Pessimistic SL Hit.")
                            status, exit_price = 'LOSS', sl
                            if tp1_hit: status = 'WIN_TP1'
                        elif c_low <= sl:
                            status, exit_price = 'LOSS', sl
                            if tp1_hit: status = 'WIN_TP1'
                        elif c_high >= tp1 and not tp1_hit:
                            tp1_hit = 1
                            sl = entry
                            logger.info(f"TP1 hit for LONG {symbol}! Moving SL to BE ({entry}).")
                            if c_high >= tp2:
                                status, exit_price = 'WIN_TP2', tp2
                        elif c_high >= tp2 and tp1_hit:
                            status, exit_price = 'WIN_TP2', tp2
                            
                    elif direction.upper() == 'SHORT':
                        current_mae = max(0.0, (c_high - entry) / entry * 100)
                        current_mfe = max(0.0, (entry - c_low) / entry * 100)
                        
                        short_hit_both = (c_low <= tp1) and (c_high >= sl) if not tp1_hit else (c_low <= tp2) and (c_high >= sl)
                        
                        if short_hit_both:
                            logger.warning(f"[EXECUTION: DUAL_WICK_RESOLVED] Symbol: {symbol} breached both TP and SL in candle. Result: Pessimistic SL Hit.")
                            status, exit_price = 'LOSS', sl
                            if tp1_hit: status = 'WIN_TP1'
                        elif c_high >= sl:
                            status, exit_price = 'LOSS', sl
                            if tp1_hit: status = 'WIN_TP1'
                        elif c_low <= tp1 and not tp1_hit:
                            tp1_hit = 1
                            sl = entry
                            logger.info(f"TP1 hit for SHORT {symbol}! Moving SL to BE ({entry}).")
                            if c_low <= tp2:
                                status, exit_price = 'WIN_TP2', tp2
                        elif c_low <= tp2 and tp1_hit:
                            status, exit_price = 'WIN_TP2', tp2
                            
                    new_mae = max(mae, current_mae)
                    new_mfe = max(mfe, current_mfe)
                            
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
                        
                        await conn.execute('UPDATE trades SET status = ?, pnl_pct = ?, net_pnl_pct = ?, gross_pnl_pct = ? WHERE trade_id = ?', (status, net_pnl_pct, net_pnl_pct, gross_pnl_pct, trade_id))
                        await conn.execute('UPDATE evaluations SET mae_pct = ?, mfe_pct = ? WHERE trade_id = ?', (new_mae, new_mfe, trade_id))
                        logger.info(f"Paper trade closed: {trade_id} {symbol} -> {status} with Net PnL {net_pnl_pct:.2f}% (Gross: {gross_pnl_pct:.2f}%) (MAE: {new_mae:.2f}%, MFE: {new_mfe:.2f}%)")
                        
                    else:
                        await conn.execute('UPDATE trades SET tp1_hit = ?, sl_price = ? WHERE trade_id = ?', (tp1_hit, sl, trade_id))
                        await conn.execute('UPDATE evaluations SET mae_pct = ?, mfe_pct = ? WHERE trade_id = ?', (new_mae, new_mfe, trade_id))
                await conn.commit()
        except Exception as e:
            logger.error(f"Failed to update trades: {e}")

    async def get_open_positions(self, symbol: str):
        try:
            async with get_db_connection(self.db_path) as conn:
                cursor = await conn.execute('SELECT trade_id, direction, entry_price, sl_price, leverage, pnl_pct FROM trades WHERE symbol = ? AND status = "PENDING"', (symbol,))
                rows = await cursor.fetchall()
                positions = []
                for r in rows:
                    positions.append({'id': r[0], 'direction': r[1], 'entry': r[2], 'stop_loss': r[3], 'leverage': r[4], 'pnl_pct': r[5]})
                return positions
        except:
            return []

    async def move_sl_to_be(self, trade_id: str, entry_price: float):
        try:
            async with get_db_connection(self.db_path) as conn:
                await conn.execute('UPDATE trades SET sl_price = ? WHERE trade_id = ?', (entry_price, trade_id))
                await conn.commit()
        except:
            pass

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

