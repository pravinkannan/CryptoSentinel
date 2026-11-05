"""
tests/test_backtester.py - Institutional Unit & Integration Tests for Backtesting Engine
"""

from datetime import datetime, timezone, timedelta
import os
import tempfile
import pytest
import numpy as np
import pandas as pd

from backtesting.data_ingestion import (
    load_ohlcv_csv,
    resample_ohlcv,
    normalize_ohlcv_columns,
    validate_ohlcv_integrity,
    DataValidationError,
)
from backtesting.calibration import (
    ScoreCalibrator,
    ScoreBandMetrics,
    CalibrationSummary,
    split_walk_forward,
)
from backtesting.execution_simulator import (
    BacktestExecutionSimulator,
    ScoredSetupRecord,
)
from backtesting.tear_sheet import ConsoleTearSheet
from scripts.time_machine import SimulatedTrade


# ==============================================================================
# 1. DATA INGESTION TESTS
# ==============================================================================

def test_load_ohlcv_csv_valid(tmp_path):
    """Verifies that standard and aliased CSV headers parse correctly into UTC timestamps."""
    csv_file = tmp_path / "test_ohlcv.csv"
    now = datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc)
    rows = []
    for i in range(10):
        t = now + timedelta(hours=i)
        rows.append({
            "Time": t.isoformat(),
            "Open": 50000.0 + i * 10,
            "High": 50100.0 + i * 10,
            "Low": 49950.0 + i * 10,
            "Close": 50050.0 + i * 10,
            "Volume": 100.0 + i,
        })
    pd.DataFrame(rows).to_csv(csv_file, index=False)

    df = load_ohlcv_csv(str(csv_file), symbol="BTC/USDT", timeframe="1h")
    assert len(df) == 10
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    assert df["timestamp"].iloc[0].tzinfo == timezone.utc
    assert df["close"].iloc[0] == 50050.0


def test_load_ohlcv_csv_missing_column(tmp_path):
    """Verifies that missing essential price columns raise DataValidationError."""
    csv_file = tmp_path / "invalid.csv"
    df = pd.DataFrame({
        "timestamp": ["2025-01-01T00:00:00Z"],
        "open": [100.0],
        "close": [105.0],
    })
    df.to_csv(csv_file, index=False)

    with pytest.raises(DataValidationError, match="Missing required OHLCV column"):
        load_ohlcv_csv(str(csv_file))


def test_validate_ohlcv_integrity_anomalies():
    """Asserts that invalid price geometry (High < Low) triggers DataValidationError."""
    df_bad = pd.DataFrame({
        "timestamp": [datetime.now(timezone.utc)],
        "open": [100.0],
        "high": [90.0],  # High < Low / Open
        "low": [95.0],
        "close": [98.0],
        "volume": [10.0],
    })
    with pytest.raises(DataValidationError):
        validate_ohlcv_integrity(df_bad)


def test_resample_ohlcv():
    """Verifies resampling 5m data to 15m aggregates OHLC correctly."""
    base_t = datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc)
    data = [
        {"timestamp": base_t + timedelta(minutes=0), "open": 100.0, "high": 105.0, "low": 99.0, "close": 102.0, "volume": 10.0},
        {"timestamp": base_t + timedelta(minutes=5), "open": 102.0, "high": 108.0, "low": 101.0, "close": 107.0, "volume": 20.0},
        {"timestamp": base_t + timedelta(minutes=10), "open": 107.0, "high": 110.0, "low": 106.0, "close": 109.0, "volume": 30.0},
    ]
    df_5m = pd.DataFrame(data)
    df_15m = resample_ohlcv(df_5m, "15m")

    assert len(df_15m) == 1
    assert df_15m["open"].iloc[0] == 100.0
    assert df_15m["high"].iloc[0] == 110.0
    assert df_15m["low"].iloc[0] == 99.0
    assert df_15m["close"].iloc[0] == 109.0
    assert df_15m["volume"].iloc[0] == 60.0


# ==============================================================================
# 2. CALIBRATION & METRICS TESTS
# ==============================================================================

def test_score_calibrator_metrics():
    """Tests grouping by score bands and calculation of Win Rate, Profit Factor, and Sharpe."""
    trades = [
        SimulatedTrade(
            trade_id="t1",
            symbol="BTC/USDT",
            direction="LONG",
            tier="PREMIUM",
            entry_price=100.0,
            sl_price=95.0,
            tp1_price=105.0,
            tp2_price=110.0,
            leverage=1.0,
            confidence_score=88.0,
            entry_time=datetime.now(timezone.utc),
            ttl_mins=60,
            net_pnl_pct=5.0,
            status="WIN_TP1",
        ),
        SimulatedTrade(
            trade_id="t2",
            symbol="BTC/USDT",
            direction="LONG",
            tier="PREMIUM",
            entry_price=100.0,
            sl_price=95.0,
            tp1_price=105.0,
            tp2_price=110.0,
            leverage=1.0,
            confidence_score=86.0,
            entry_time=datetime.now(timezone.utc),
            ttl_mins=60,
            net_pnl_pct=-2.5,
            status="LOSS",
        ),
        SimulatedTrade(
            trade_id="t3",
            symbol="BTC/USDT",
            direction="LONG",
            tier="STANDARD",
            entry_price=100.0,
            sl_price=95.0,
            tp1_price=105.0,
            tp2_price=110.0,
            leverage=1.0,
            confidence_score=68.0,
            entry_time=datetime.now(timezone.utc),
            ttl_mins=60,
            net_pnl_pct=2.0,
            status="WIN_TP1",
        ),
    ]

    calibrator = ScoreCalibrator()
    summary = calibrator.calibrate(trades)

    assert summary.total_trades == 3
    assert summary.overall_win_rate_pct == pytest.approx(66.67, rel=1e-2)
    assert summary.overall_net_pnl_pct == pytest.approx(4.5, rel=1e-2)
    # Gains: 5.0 + 2.0 = 7.0, Losses: 2.5 -> PF = 7.0 / 2.5 = 2.8
    assert summary.overall_profit_factor == pytest.approx(2.8, rel=1e-2)

    # Check band 85% - 90%
    band_85_90 = next(b for b in summary.bands if b.band_name == "85% - 90%")
    assert band_85_90.sample_size == 2
    assert band_85_90.wins == 1
    assert band_85_90.losses == 1
    assert band_85_90.win_rate_pct == 50.0
    assert band_85_90.profit_factor == pytest.approx(2.0, rel=1e-2)


def test_split_walk_forward():
    """Verifies chronological out-of-sample split preserves ordering."""
    dates = pd.date_range("2025-01-01", periods=100, freq="1h", tz="UTC")
    df = pd.DataFrame({
        "timestamp": dates,
        "open": range(100),
        "high": range(100),
        "low": range(100),
        "close": range(100),
        "volume": range(100),
    })

    in_sample, out_of_sample = split_walk_forward(df, in_sample_pct=0.70)
    assert len(in_sample) == 70
    assert len(out_of_sample) == 30
    assert in_sample["timestamp"].iloc[-1] < out_of_sample["timestamp"].iloc[0]


# ==============================================================================
# 3. TEAR SHEET FORMATTING TESTS
# ==============================================================================

def test_console_tear_sheet_render():
    """Verifies that the institutional console tear sheet renders without throwing errors."""
    calibrator = ScoreCalibrator()
    summary = calibrator.calibrate([])
    sheet = ConsoleTearSheet(summary, metadata={"symbol": "BTC/USDT", "timeframe": "1h"})
    output = sheet.render()

    assert "CRYPTO SENTINEL: QUANTITATIVE CALIBRATION TEAR SHEET" in output
    assert "BTC/USDT" in output
    assert "EMPIRICAL SCORE-BAND CALIBRATION MATRIX" in output
    assert "RELIABILITY DIAGRAM" in output

