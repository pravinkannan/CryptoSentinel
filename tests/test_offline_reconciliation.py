import asyncio
import pytest
import pandas as pd
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

from core.state_manager import StateManager
from engine.journal_manager import JournalManager
from execution.paper_trader import PaperTradingService


def test_state_manager_purge_expired_signals(tmp_path):
    """Verify that StateManager.purge_expired_signals removes signals whose TTL has lapsed."""
    state_file = str(tmp_path / "state.json")
    sm = StateManager(filename=state_file)
    now = datetime.now(timezone.utc)

    sm.state["active_signals"] = {
        "BTC/USDT_5m": {
            "trade_id": "trade_expired",
            "direction": "LONG",
            "confidence": 90,
            "timestamp": (now - timedelta(hours=20)).isoformat(),
            "timeframe": "5m",
            "ttl_mins": 1080  # 18 hours (expired 2 hours ago)
        },
        "ETH/USDT_15m": {
            "trade_id": "trade_fresh",
            "direction": "SHORT",
            "confidence": 88,
            "timestamp": (now - timedelta(hours=2)).isoformat(),
            "timeframe": "15m",
            "ttl_mins": 1080  # 18 hours (still alive)
        }
    }

    purged = sm.purge_expired_signals(now_utc=now)
    assert purged == 1
    assert "BTC/USDT_5m" not in sm.state["active_signals"]
    assert "ETH/USDT_15m" in sm.state["active_signals"]


def test_reconcile_offline_gap_no_pending(tmp_path):
    """Verify reconcile_offline_gap returns CLEAN when no pending trades exist."""
    async def run():
        db_file = str(tmp_path / "test_recon.db")
        journal = JournalManager(db_path=db_file)
        await journal.ensure_db_exists()
        trader = PaperTradingService(db_path=db_file)

        mock_market_data = MagicMock()
        res = await trader.reconcile_offline_gap(market_data=mock_market_data)
        assert res["status"] == "CLEAN"
        assert res["total_checked"] == 0

    asyncio.run(run())


def test_reconcile_offline_gap_exceeded_ttl_without_data(tmp_path):
    """Verify that if a trade exceeded TTL while offline and no candle data is found, it expires safely."""
    async def run():
        db_file = str(tmp_path / "test_ttl_recon.db")
        journal = JournalManager(db_path=db_file)
        await journal.ensure_db_exists()
        trader = PaperTradingService(db_path=db_file)

        now = datetime.now(timezone.utc)
        trade_id = await journal.log_trade({
            'timestamp': (now - timedelta(days=4)).isoformat(),
            'symbol': 'BTC/USDT',
            'direction': 'LONG',
            'pattern_name': 'Bullish Belthold',
            'entry_price': 80000.0,
            'sl_price': 79000.0,
            'tp1_price': 81500.0,
            'tp2_price': 83000.0,
            'leverage': 20.0,
            'ttl_mins': 1080,
            'confidence_score': 90
        })

        sm = StateManager(filename=str(tmp_path / "state.json"))
        sm.state["active_signals"] = {
            "BTC/USDT_5m": {
                "trade_id": trade_id,
                "direction": "LONG",
                "timestamp": (now - timedelta(days=4)).isoformat(),
                "ttl_mins": 1080
            }
        }

        # Market data returns None (e.g. offline exchange)
        mock_market_data = MagicMock()
        mock_market_data.fetch_ohlcv = AsyncMock(return_value=None)

        res = await trader.reconcile_offline_gap(
            market_data=mock_market_data,
            journal_manager=journal,
            state_manager=sm
        )

        assert res["status"] == "RECONCILED"
        assert res["closed"] == 1
        # Trade must no longer be active
        assert await trader.is_trade_active('BTC/USDT', trade_id=trade_id) is False
        # Must be evicted from active_signals
        assert "BTC/USDT_5m" not in sm.state["active_signals"]

    asyncio.run(run())


def test_reconcile_offline_gap_replays_candles_and_hits_sl(tmp_path):
    """Verify that catch-up historical candles correctly trigger Stop Loss at the exact historical SL."""
    async def run():
        db_file = str(tmp_path / "test_sl_recon.db")
        journal = JournalManager(db_path=db_file)
        await journal.ensure_db_exists()
        trader = PaperTradingService(db_path=db_file)

        now = datetime.now(timezone.utc)
        entry_time = now - timedelta(hours=3)
        trade_id = await journal.log_trade({
            'timestamp': entry_time.isoformat(),
            'symbol': 'ETH/USDT',
            'direction': 'LONG',
            'pattern_name': 'Bullish Engulfing',
            'entry_price': 2500.0,
            'sl_price': 2450.0,
            'tp1_price': 2600.0,
            'tp2_price': 2700.0,
            'leverage': 10.0,
            'ttl_mins': 1080,
            'confidence_score': 90
        })

        # Historical candles during the offline period where candle 2 breaches SL (low = 2440 < 2450)
        df_catchup = pd.DataFrame([
            {
                'timestamp': entry_time + timedelta(minutes=15),
                'open': 2505.0, 'high': 2520.0, 'low': 2495.0, 'close': 2510.0, 'volume': 100.0
            },
            {
                'timestamp': entry_time + timedelta(minutes=30),
                'open': 2510.0, 'high': 2515.0, 'low': 2440.0, 'close': 2445.0, 'volume': 300.0
            }
        ])

        mock_market_data = MagicMock()
        mock_market_data.fetch_ohlcv = AsyncMock(return_value=df_catchup)

        res = await trader.reconcile_offline_gap(
            market_data=mock_market_data,
            journal_manager=journal
        )

        assert res["status"] == "RECONCILED"
        assert res["closed"] == 1
        # Trade is now closed as LOSS
        assert await trader.is_trade_active('ETH/USDT', trade_id=trade_id) is False

    asyncio.run(run())

