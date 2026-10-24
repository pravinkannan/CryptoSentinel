import asyncio
import aiosqlite
import pandas as pd
from datetime import datetime, timezone, timedelta

from execution.paper_trader import PaperTradingService
from engine.journal_manager import JournalManager

def test_paper_trader_trailing_sl_does_not_prematurely_stop_out_on_entry_candle(tmp_path):
    """
    Verify that when Auto-BE moves trailing_sl to Breakeven, a subsequent update_trades pass
    replaying from entry does not falsely trigger the tightened stop loss on the entry candle.
    """
    async def run_test():
        db_file = str(tmp_path / "sentinel.db")
        trader = PaperTradingService(db_path=db_file)
        journal = JournalManager(db_path=db_file)
        await journal.ensure_db_exists()

        base_time = datetime.now(timezone.utc) - timedelta(hours=1)
        
        # 1. Log a SHORT trade: Entry 1126.28, Initial SL 1143.17, TP1 1100.94, TP2 1075.60
        # Auto-BE trigger is at entry - (initial_risk * 1.0) = 1126.28 - (16.89) = 1109.39
        trade_id = await journal.log_trade({
            'timestamp': base_time.strftime('%Y-%m-%d %H:%M:%S UTC'),
            'symbol': 'ZEC/USDT',
            'direction': 'SHORT',
            'regime': 'NORMAL',
            'pattern_name': 'Bearish Belthold',
            'entry_price': 1126.28,
            'sl_price': 1143.17,
            'tp1_price': 1100.94,
            'tp2_price': 1075.60,
            'leverage': 12.0,
            'ttl_mins': 1080,
            'confidence_score': 86.9,
            'scoring_checklist': [],
            'outcome': 'PENDING'
        })

        # Pass 1: Candles up to 13:25 UTC (where low reaches 1108.12, triggering Auto-BE to 1125.15)
        # Entry candle (12:55) has High 1129.46.
        c1_time = base_time
        c2_time = base_time + timedelta(minutes=30)
        
        df_pass1 = pd.DataFrame([
            {
                'timestamp': c1_time,
                'open': 1129.29,
                'high': 1129.46,
                'low': 1125.63,
                'close': 1126.51,
                'volume': 1000.0
            },
            {
                'timestamp': c2_time,
                'open': 1118.18,
                'high': 1118.71,
                'low': 1108.12, # triggers Auto-BE!
                'close': 1113.48,
                'volume': 2000.0
            }
        ])

        await trader.update_trades('ZEC/USDT', df_pass1)

        # Verify trailing_sl was moved to breakeven (1125.15) and trade remains PENDING
        async with aiosqlite.connect(db_file) as conn:
            cur = await conn.execute("SELECT status, trailing_sl, tp1_hit FROM trades WHERE trade_id = ?", (trade_id,))
            row = await cur.fetchone()
            assert row[0] == 'PENDING', f"Expected PENDING, got {row[0]}"
            assert row[1] == 1125.15, f"Expected trailing_sl 1125.15, got {row[1]}"
            assert row[2] == 0

        # Pass 2: Next poll cycle, df contains candles from 12:55 up to 14:30 (where low reaches 1087.71, hitting TP1!)
        c3_time = base_time + timedelta(minutes=95)
        df_pass2 = pd.DataFrame([
            {
                'timestamp': c1_time,
                'open': 1129.29,
                'high': 1129.46, # High 1129.46 > 1125.15 (trailing_sl)
                'low': 1125.63,
                'close': 1126.51,
                'volume': 1000.0
            },
            {
                'timestamp': c2_time,
                'open': 1118.18,
                'high': 1118.71,
                'low': 1108.12,
                'close': 1113.48,
                'volume': 2000.0
            },
            {
                'timestamp': c3_time,
                'open': 1109.35,
                'high': 1109.39,
                'low': 1087.71, # hits TP1 (1100.94)!
                'close': 1095.01,
                'volume': 3000.0
            }
        ])

        await trader.update_trades('ZEC/USDT', df_pass2)

        # Verify trade did NOT falsely exit as BREAKEVEN on candle 1!
        async with aiosqlite.connect(db_file) as conn:
            cur = await conn.execute("SELECT status, tp1_hit, trailing_sl FROM trades WHERE trade_id = ?", (trade_id,))
            row2 = await cur.fetchone()
            # Trade should have successfully recorded TP1 hit!
            assert row2[1] == 1, f"Expected tp1_hit == 1, got {row2[1]} with status {row2[0]}"

    asyncio.run(run_test())
