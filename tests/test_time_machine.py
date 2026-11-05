"""
Unit & Integration Tests for scripts/time_machine.py (The Time Machine).
Validates intra-candle path simulation, pessimistic dual-wick resolution,
database isolation, and zero-lookahead historical adapter slicing.
"""

import os
from datetime import datetime, timezone, timedelta
import pandas as pd
import pytest

from scripts.time_machine import (
    SimulatedTrade,
    IntraCandlePathSimulator,
    HistoricalMarketDataAdapter,
    TimeMachine,
)


def test_intra_candle_dual_wick_pessimistic_resolution():
    """
    Validates CRITICAL edge case:
    If a 5m candle spans both TP and SL, the simulator must conservatively
    resolve it as a Stop Loss to prevent false wins.
    """
    sim = IntraCandlePathSimulator()
    entry_time = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)

    trade = SimulatedTrade(
        trade_id="BTC_TEST_001",
        symbol="BTC/USDT",
        direction="LONG",
        tier="STANDARD",
        entry_price=100.0,
        sl_price=95.0,
        tp1_price=110.0,
        tp2_price=120.0,
        leverage=1.0,
        confidence_score=87.0,
        entry_time=entry_time,
        ttl_mins=180,
    )
    sim.register_trade(trade)

    # 5m candle that breaches BOTH TP1 (110.0) and SL (95.0)
    df_dual_wick = pd.DataFrame([
        {
            "timestamp": entry_time + timedelta(minutes=5),
            "open": 100.0,
            "high": 112.0,  # >= TP1 (110.0)
            "low": 93.0,    # <= SL (95.0)
            "close": 105.0,
            "volume": 50.0,
        }
    ])

    sim.step_trades_through_candles("BTC/USDT", df_dual_wick)

    assert len(sim.closed_trades) == 1
    closed = sim.closed_trades[0]
    # Pessimistic resolution guarantees LOSS (not WIN)
    assert closed.status == "LOSS"
    assert closed.exit_price == 95.0
    assert closed.net_pnl_pct < 0


def test_intra_candle_tp1_and_tp2_ratcheting():
    """
    Validates that hitting TP1 ratchets Stop Loss to Breakeven (+0.1% buffer)
    and subsequent candle reaching TP2 closes out remaining 30%.
    """
    sim = IntraCandlePathSimulator()
    entry_time = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)

    trade = SimulatedTrade(
        trade_id="BTC_TEST_002",
        symbol="BTC/USDT",
        direction="LONG",
        tier="PREMIUM",
        entry_price=100.0,
        sl_price=95.0,
        tp1_price=110.0,
        tp2_price=120.0,
        leverage=1.0,
        confidence_score=92.0,
        entry_time=entry_time,
        ttl_mins=180,
    )
    sim.register_trade(trade)

    # Candle 1: hits TP1 only
    c1 = pd.DataFrame([
        {
            "timestamp": entry_time + timedelta(minutes=5),
            "open": 100.0,
            "high": 111.0,
            "low": 98.0,
            "close": 109.0,
            "volume": 20.0,
        }
    ])
    sim.step_trades_through_candles("BTC/USDT", c1)

    assert len(sim.open_trades) == 1
    assert sim.open_trades[0].tp1_hit is True
    assert sim.open_trades[0].current_sl == 100.28  # True Breakeven buffer (100 * (1 + 0.00276))

    # Candle 2: reaches TP2
    c2 = pd.DataFrame([
        {
            "timestamp": entry_time + timedelta(minutes=10),
            "open": 109.0,
            "high": 122.0,
            "low": 108.0,
            "close": 121.0,
            "volume": 35.0,
        }
    ])
    sim.step_trades_through_candles("BTC/USDT", c2)

    assert len(sim.closed_trades) == 1
    closed = sim.closed_trades[0]
    assert closed.status == "WIN_TP2"
    assert closed.exit_price == 120.0
    assert closed.net_pnl_pct > 0


def test_mark_to_market_force_close():
    """
    Validates Mark-to-Market: Open trades remaining at simulation end are
    force-closed at final candle's close price.
    """
    sim = IntraCandlePathSimulator()
    entry_time = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)

    trade = SimulatedTrade(
        trade_id="BTC_TEST_003",
        symbol="BTC/USDT",
        direction="LONG",
        tier="STANDARD",
        entry_price=100.0,
        sl_price=90.0,
        tp1_price=120.0,
        tp2_price=130.0,
        leverage=1.0,
        confidence_score=88.0,
        entry_time=entry_time,
        ttl_mins=180,
    )
    sim.register_trade(trade)

    final_time = entry_time + timedelta(hours=2)
    final_prices = {"BTC/USDT": 107.5}
    sim.force_close_all_mark_to_market(final_prices, final_time)

    assert len(sim.open_trades) == 0
    assert len(sim.closed_trades) == 1
    closed = sim.closed_trades[0]
    assert closed.status == "MARK_TO_MARKET"
    assert closed.exit_price == 107.5
    assert closed.exit_time == final_time
    assert closed.gross_pnl_pct == pytest.approx(7.5, 0.01)


def test_historical_market_data_adapter_zero_lookahead():
    """
    Validates that HistoricalMarketDataAdapter strictly filters candles <= cursor.
    """
    import asyncio

    async def _test():
        t0 = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 5, 1, 13, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)

        df_sample = pd.DataFrame([
            {"timestamp": t0, "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 10},
            {"timestamp": t1, "open": 100.5, "high": 103, "low": 100, "close": 102.0, "volume": 15},
            {"timestamp": t2, "open": 102, "high": 105, "low": 101, "close": 104.0, "volume": 20},
        ])

        adapter = HistoricalMarketDataAdapter({"BTC/USDT": {"1h": df_sample}})

        # Set cursor to t1
        adapter.set_cursor(t1)
        res_t1 = await adapter.fetch_ohlcv("BTC/USDT", "1h", limit=10)
        assert len(res_t1) == 2
        assert res_t1["timestamp"].iloc[-1] == t1

        # Advance cursor to t2
        adapter.set_cursor(t2)
        res_t2 = await adapter.fetch_ohlcv("BTC/USDT", "1h", limit=10)
        assert len(res_t2) == 3
        assert res_t2["timestamp"].iloc[-1] == t2

    asyncio.run(_test())


def test_time_machine_db_isolation_path_protection():
    """
    Validates that passing production sentinel.db or :memory: is safely diverted/sanitized
    to protect production DB and eliminate Windows path errors.
    """
    tm = TimeMachine(
        symbols=["BTC/USDT"],
        days=1,
        db_path="data/sentinel.db",
    )
    assert tm.db_path != "data/sentinel.db"
    assert "sentinel.db" not in tm.db_path

    tm_mem = TimeMachine(
        symbols=["BTC/USDT"],
        days=1,
        db_path=":memory:",
    )
    assert tm_mem.db_path == "data/backtest_memory.db"
