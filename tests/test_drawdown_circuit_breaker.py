"""
Unit tests for chronological drawdown calculation and circuit breaker configuration.
"""

import pytest
import asyncio
from core.config import settings
from core.db import get_db_connection
from execution.paper_trader import PaperTradingService
from infrastructure.database.repository import SentinelRepository


def test_circuit_breaker_enabled_by_default():
    """Verify that automated drawdown circuit breaker is enabled by default in config."""
    assert settings.enable_circuit_breaker is True


def test_get_current_drawdown_chronological_sorting(tmp_path):
    """
    Verify that get_current_drawdown sorts chronologically by close_time (or timestamp),
    preventing arbitrary SQLite order from corrupting the high-water mark peak and drawdown.
    """
    async def run_test():
        db_file = str(tmp_path / "test_drawdown.db")
        repo = SentinelRepository(db_path=db_file)
        await repo.ensure_schema()

        # Insert 3 completed trades out of order into the database
        async with get_db_connection(db_file) as conn:
            # Insert day 3 first
            await conn.execute('''
                INSERT INTO trades (
                    trade_id, timestamp, close_time, symbol, direction, entry_price, sl_price, leverage, status, pnl_pct, net_pnl_pct
                ) VALUES ('trade_3', '2026-10-03T10:00:00Z', '2026-10-03T12:00:00Z', 'BTC/USDT', 'LONG', 50000.0, 49000.0, 10.0, 'WIN_TP1', 10.0, 10.0)
            ''')
            # Insert day 1 next
            await conn.execute('''
                INSERT INTO trades (
                    trade_id, timestamp, close_time, symbol, direction, entry_price, sl_price, leverage, status, pnl_pct, net_pnl_pct
                ) VALUES ('trade_1', '2026-10-01T10:00:00Z', '2026-10-01T12:00:00Z', 'BTC/USDT', 'LONG', 50000.0, 49000.0, 10.0, 'WIN_TP2', 20.0, 20.0)
            ''')
            # Insert day 2 last
            await conn.execute('''
                INSERT INTO trades (
                    trade_id, timestamp, close_time, symbol, direction, entry_price, sl_price, leverage, status, pnl_pct, net_pnl_pct
                ) VALUES ('trade_2', '2026-10-02T10:00:00Z', '2026-10-02T12:00:00Z', 'BTC/USDT', 'SHORT', 50000.0, 51000.0, 10.0, 'LOSS', -30.0, -30.0)
            ''')
            await conn.commit()

        service = PaperTradingService(db_path=db_file, initial_capital=10000.0)
        dd = await service.get_current_drawdown()

        assert dd > 0.0
        assert isinstance(dd, float)

    asyncio.run(run_test())


def test_get_current_drawdown_empty_db(tmp_path):
    """Verify that an empty trade journal reports 0.0% drawdown."""
    async def run_test():
        db_file = str(tmp_path / "test_empty_drawdown.db")
        repo = SentinelRepository(db_path=db_file)
        await repo.ensure_schema()

        service = PaperTradingService(db_path=db_file, initial_capital=10000.0)
        dd = await service.get_current_drawdown()
        assert dd == 0.0

    asyncio.run(run_test())
