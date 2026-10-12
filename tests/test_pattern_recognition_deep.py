"""
Comprehensive unit tests for PatternRecognitionService.
Covers positive, negative, and edge cases for:
- FVG Tap & Go setups (Bullish & Bearish in Phase 2 High Volatility)
- RSI Divergence detection (Bullish, Bearish, Conforming, Insufficient data)
- Volatility Squeeze detection (Bollinger Bands vs Keltner Channels)
- EMA 20/50 Golden Cross & Death Cross
- Massive price moves and Open Interest (OI) dynamics
"""

import pytest
import pandas as pd
import numpy as np
from engine.pattern_recognition import PatternRecognitionService


@pytest.fixture
def pattern_service():
    """Returns a PatternRecognitionService with 2.0% massive move trigger."""
    return PatternRecognitionService(percentage_trigger=2.0)


# =========================================================================
# FVG Tap & Go Tests (High Volatility Phase 2)
# =========================================================================

def test_fvg_tap_and_go_bullish_positive(pattern_service):
    """
    Positive case: Bullish FVG created between bar[-4] and bar[-2],
    tapped into by bar[-1] low and closed back above the FVG top in Phase 2.
    """
    # 4 candles:
    # c1 (bar -4): high = 100.0, low = 95.0
    # c2 (bar -3): high = 110.0, low = 101.0 (large push)
    # c3 (bar -2): high = 115.0, low = 108.0 -> c1 high (100.0) < c3 low (108.0) -> Bullish FVG [100.0, 108.0]
    # c4 (bar -1): low = 106.0 (taps <= 108.0), close = 112.0 (closes > 108.0)
    data = {
        'open': [96.0, 102.0, 109.0, 107.0],
        'high': [100.0, 110.0, 115.0, 113.0],
        'low': [95.0, 101.0, 108.0, 106.0],
        'close': [99.0, 108.0, 112.0, 112.0],
        'volume': [100.0, 200.0, 150.0, 250.0],
        'VOL_SMA_20': [100.0, 100.0, 100.0, 100.0]
    }
    df = pd.DataFrame(data)
    regime_data = {"regime": "HIGH_VOLATILITY", "phase": "PHASE 2"}

    signals, rationale, direction, oi_delta, oi_dir = pattern_service.detect_patterns(df, regime_data)

    assert "Bullish FVG Tap & Go (Momentum Continuation)" in signals
    assert direction == "Bullish"
    assert any("Bullish FVG imbalance" in r for r in rationale)


def test_fvg_tap_and_go_bearish_positive(pattern_service):
    """
    Positive case: Bearish FVG created between bar[-4] and bar[-2],
    tapped into by bar[-1] high and closed back below the FVG bottom in Phase 2.
    """
    # 4 candles:
    # c1 (bar -4): high = 105.0, low = 100.0
    # c2 (bar -3): high = 99.0, low = 90.0 (dump)
    # c3 (bar -2): high = 92.0, low = 88.0 -> c1 low (100.0) > c3 high (92.0) -> Bearish FVG [92.0, 100.0]
    # c4 (bar -1): high = 94.0 (taps >= 92.0), close = 89.0 (closes < 92.0)
    data = {
        'open': [104.0, 98.0, 91.0, 93.0],
        'high': [105.0, 99.0, 92.0, 94.0],
        'low': [100.0, 90.0, 88.0, 87.0],
        'close': [101.0, 91.0, 89.0, 89.0],
        'volume': [100.0, 200.0, 150.0, 250.0],
        'VOL_SMA_20': [100.0, 100.0, 100.0, 100.0]
    }
    df = pd.DataFrame(data)
    regime_data = {"regime": "HIGH_VOLATILITY", "phase": "PHASE 2"}

    signals, rationale, direction, oi_delta, oi_dir = pattern_service.detect_patterns(df, regime_data)

    assert "Bearish FVG Tap & Go (Momentum Continuation)" in signals
    assert direction == "Bearish"
    assert any("Bearish FVG imbalance" in r for r in rationale)


def test_fvg_tap_and_go_wrong_regime_or_phase_negative(pattern_service):
    """
    Negative case: Valid FVG geometry exists, but regime is NOT HIGH_VOLATILITY PHASE 2.
    The setup should strictly not trigger.
    """
    data = {
        'open': [96.0, 102.0, 109.0, 107.0],
        'high': [100.0, 110.0, 115.0, 113.0],
        'low': [95.0, 101.0, 108.0, 106.0],
        'close': [99.0, 108.0, 112.0, 112.0],
        'volume': [100.0, 200.0, 150.0, 250.0],
        'VOL_SMA_20': [100.0, 100.0, 100.0, 100.0]
    }
    df = pd.DataFrame(data)

    # Subcase 1: NORMAL regime
    signals1, _, _, _, _ = pattern_service.detect_patterns(df, {"regime": "NORMAL", "phase": "PHASE 2"})
    assert not any("FVG Tap & Go" in s for s in signals1)

    # Subcase 2: PHASE 1 (breakout phase, not retest phase)
    signals2, _, _, _, _ = pattern_service.detect_patterns(df, {"regime": "HIGH_VOLATILITY", "phase": "PHASE 1"})
    assert not any("FVG Tap & Go" in s for s in signals2)

    # Subcase 3: No regime data provided
    signals3, _, _, _, _ = pattern_service.detect_patterns(df, None)
    assert not any("FVG Tap & Go" in s for s in signals3)


def test_fvg_tap_and_go_geometry_failures_negative(pattern_service):
    """
    Negative cases for geometry:
    1. Never tapped into FVG (c4 low stays above FVG top).
    2. Failed to reclaim (c4 close stays trapped inside or below FVG top).
    """
    regime_data = {"regime": "HIGH_VOLATILITY", "phase": "PHASE 2"}

    # Subcase 1: c4 low = 109.0 > fvg_top 108.0 (No tap)
    df_no_tap = pd.DataFrame({
        'open': [96.0, 102.0, 109.0, 110.0],
        'high': [100.0, 110.0, 115.0, 116.0],
        'low': [95.0, 101.0, 108.0, 109.0],
        'close': [99.0, 108.0, 112.0, 114.0],
        'volume': [100.0, 100.0, 100.0, 100.0],
        'VOL_SMA_20': [100.0, 100.0, 100.0, 100.0]
    })
    signals, _, _, _, _ = pattern_service.detect_patterns(df_no_tap, regime_data)
    assert not any("FVG Tap & Go" in s for s in signals)

    # Subcase 2: c4 low = 105.0 (taps), but close = 107.0 <= 108.0 (Failed reclaim)
    df_failed_reclaim = pd.DataFrame({
        'open': [96.0, 102.0, 109.0, 107.0],
        'high': [100.0, 110.0, 115.0, 108.0],
        'low': [95.0, 101.0, 108.0, 105.0],
        'close': [99.0, 108.0, 112.0, 107.0],
        'volume': [100.0, 100.0, 100.0, 100.0],
        'VOL_SMA_20': [100.0, 100.0, 100.0, 100.0]
    })
    signals, _, _, _, _ = pattern_service.detect_patterns(df_failed_reclaim, regime_data)
    assert not any("FVG Tap & Go" in s for s in signals)


# =========================================================================
# RSI Divergence Tests
# =========================================================================

def test_rsi_bullish_divergence_positive(pattern_service):
    """
    Positive case: Price makes a new lower low, while RSI forms a higher low.
    """
    n = 20
    # Construct base series
    lows = [100.0] * n
    highs = [105.0] * n
    opens = [102.0] * n
    closes = [103.0] * n
    volumes = [50.0] * n
    rsi = [40.0] * n

    # Index 10: Prior low = 90.0, RSI = 22.0
    lows[10] = 90.0
    rsi[10] = 22.0

    # Index 19 (latest): Lower low = 88.0 <= 90.0, Higher RSI = 28.0 > 22.0
    lows[-1] = 88.0
    rsi[-1] = 28.0

    df = pd.DataFrame({
        'open': opens, 'high': highs, 'low': lows, 'close': closes,
        'volume': volumes, 'RSI_14': rsi, 'VOL_SMA_20': [50.0]*n
    })

    signals, _, direction, _, _ = pattern_service.detect_patterns(df)
    assert "Bullish RSI Divergence" in signals


def test_rsi_bearish_divergence_positive(pattern_service):
    """
    Positive case: Price makes a new higher high, while RSI forms a lower high.
    """
    n = 20
    lows = [90.0] * n
    highs = [100.0] * n
    opens = [95.0] * n
    closes = [96.0] * n
    volumes = [50.0] * n
    rsi = [50.0] * n

    # Index 8: Prior high = 110.0, RSI = 78.0
    highs[8] = 110.0
    rsi[8] = 78.0

    # Index 19 (latest): Higher high = 115.0 >= 110.0, Lower RSI = 71.0 < 78.0
    highs[-1] = 115.0
    rsi[-1] = 71.0

    df = pd.DataFrame({
        'open': opens, 'high': highs, 'low': lows, 'close': closes,
        'volume': volumes, 'RSI_14': rsi, 'VOL_SMA_20': [50.0]*n
    })

    signals, _, direction, _, _ = pattern_service.detect_patterns(df)
    assert "Bearish RSI Divergence" in signals


def test_rsi_divergence_insufficient_bars_edge_case(pattern_service):
    """
    Edge case: DataFrame with <= 15 bars must safely skip RSI divergence
    without raising IndexError or crashing.
    """
    n = 10
    df = pd.DataFrame({
        'open': [100.0] * n,
        'high': [105.0] * n,
        'low': [95.0] * n,
        'close': [101.0] * n,
        'volume': [50.0] * n,
        'RSI_14': [45.0] * n,
        'VOL_SMA_20': [50.0] * n
    })
    signals, _, _, _, _ = pattern_service.detect_patterns(df)
    assert not any("RSI Divergence" in s for s in signals)


def test_rsi_conforming_momentum_negative(pattern_service):
    """
    Negative case: Price makes a lower low, but RSI ALSO makes a lower low (trend confirmed, no divergence).
    """
    n = 20
    lows = [100.0] * n
    rsi = [50.0] * n

    # Prior low at index 10: low = 92.0, RSI = 35.0
    lows[10] = 92.0
    rsi[10] = 35.0

    # Latest: lower price low = 88.0, AND lower RSI = 25.0
    lows[-1] = 88.0
    rsi[-1] = 25.0

    df = pd.DataFrame({
        'open': [100.0] * n, 'high': [105.0] * n, 'low': lows, 'close': [99.0] * n,
        'volume': [50.0] * n, 'RSI_14': rsi, 'VOL_SMA_20': [50.0] * n
    })
    signals, _, _, _, _ = pattern_service.detect_patterns(df)
    assert "Bullish RSI Divergence" not in signals


# =========================================================================
# Volatility Squeeze Tests
# =========================================================================

def test_volatility_squeeze_positive_and_negative(pattern_service):
    """
    Verify Volatility Squeeze detection:
    - Positive: Bollinger Bands inside Keltner Channels (BBL > KCL and BBU < KCU).
    - Negative: Bollinger Bands expanded beyond Keltner Channels.
    - Edge: Missing or zero Keltner Channel values.
    """
    # Positive case: Squeeze active
    df_squeezed = pd.DataFrame({
        'open': [100.0, 100.0], 'high': [101.0, 101.0], 'low': [99.0, 99.0], 'close': [100.0, 100.0],
        'volume': [50.0, 50.0], 'VOL_SMA_20': [50.0, 50.0],
        'BBL_20_2.0': [98.0, 98.0],
        'BBU_20_2.0': [102.0, 102.0],
        'KCLe_20_1.5': [95.0, 95.0],   # BBL (98) > KCL (95)
        'KCUe_20_1.5': [105.0, 105.0]  # BBU (102) < KCU (105)
    })
    _, rationale_sq, _, _, _ = pattern_service.detect_patterns(df_squeezed)
    assert any("Volatility Squeeze (BB inside Keltner)" in r for r in rationale_sq)

    # Negative case: Bands expanded outside Keltner
    df_expanded = pd.DataFrame({
        'open': [100.0, 100.0], 'high': [101.0, 101.0], 'low': [99.0, 99.0], 'close': [100.0, 100.0],
        'volume': [50.0, 50.0], 'VOL_SMA_20': [50.0, 50.0],
        'BBL_20_2.0': [92.0, 92.0],
        'BBU_20_2.0': [108.0, 108.0],
        'KCLe_20_1.5': [95.0, 95.0],
        'KCUe_20_1.5': [105.0, 105.0]
    })
    _, rationale_exp, _, _, _ = pattern_service.detect_patterns(df_expanded)
    assert not any("Volatility Squeeze" in r for r in rationale_exp)

    # Edge case: Missing Keltner values
    df_missing_kc = pd.DataFrame({
        'open': [100.0, 100.0], 'high': [101.0, 101.0], 'low': [99.0, 99.0], 'close': [100.0, 100.0],
        'volume': [50.0, 50.0], 'VOL_SMA_20': [50.0, 50.0],
        'BBL_20_2.0': [98.0, 98.0], 'BBU_20_2.0': [102.0, 102.0]
    })
    _, rationale_missing, _, _, _ = pattern_service.detect_patterns(df_missing_kc)
    assert not any("Volatility Squeeze" in r for r in rationale_missing)


# =========================================================================
# EMA 20/50 Crossover Tests
# =========================================================================

def test_ema_crossover_bullish_and_bearish(pattern_service):
    """
    Verify EMA 20/50 Crossover:
    - Bullish Golden Cross: prev EMA20 < prev EMA50, latest EMA20 > latest EMA50.
    - Bearish Death Cross: prev EMA20 > prev EMA50, latest EMA20 < latest EMA50.
    - Negative: Ongoing separation without cross.
    """
    # Bullish Cross
    df_golden = pd.DataFrame({
        'open': [100.0, 100.0], 'high': [101.0, 101.0], 'low': [99.0, 99.0], 'close': [100.0, 100.0],
        'volume': [50.0, 50.0], 'VOL_SMA_20': [50.0, 50.0],
        'EMA_20': [99.0, 101.0],
        'EMA_50': [100.0, 100.0]
    })
    signals_g, _, _, _, _ = pattern_service.detect_patterns(df_golden)
    assert "Bullish 20/50 EMA Crossover" in signals_g

    # Bearish Cross
    df_death = pd.DataFrame({
        'open': [100.0, 100.0], 'high': [101.0, 101.0], 'low': [99.0, 99.0], 'close': [100.0, 100.0],
        'volume': [50.0, 50.0], 'VOL_SMA_20': [50.0, 50.0],
        'EMA_20': [101.0, 99.0],
        'EMA_50': [100.0, 100.0]
    })
    signals_d, _, _, _, _ = pattern_service.detect_patterns(df_death)
    assert "Bearish 20/50 EMA Crossover" in signals_d

    # Negative: Flat / no cross
    df_no_cross = pd.DataFrame({
        'open': [100.0, 100.0], 'high': [101.0, 101.0], 'low': [99.0, 99.0], 'close': [100.0, 100.0],
        'volume': [50.0, 50.0], 'VOL_SMA_20': [50.0, 50.0],
        'EMA_20': [102.0, 103.0],
        'EMA_50': [100.0, 100.5]
    })
    signals_nc, _, _, _, _ = pattern_service.detect_patterns(df_no_cross)
    assert not any("EMA Crossover" in s for s in signals_nc)


# =========================================================================
# Massive Move & Open Interest Tests
# =========================================================================

def test_massive_move_and_open_interest_dynamics(pattern_service):
    """
    Test percentage move triggers and Open Interest delta calculations.
    """
    # Massive Move Up: 3% gain with trigger set to 2%
    df_move_up = pd.DataFrame({
        'open': [100.0, 100.0], 'high': [101.0, 104.0], 'low': [99.0, 100.0], 'close': [100.0, 103.0],
        'volume': [50.0, 50.0], 'VOL_SMA_20': [50.0, 50.0]
    })
    signals, _, _, _, _ = pattern_service.detect_patterns(df_move_up)
    assert any("Massive Move Up (3.00%)" in s for s in signals)

    # Open Interest Expanding: 4 bars, past OI = 1000, current = 1100 (+10%)
    df_oi_expanding = pd.DataFrame({
        'open': [100.0]*4, 'high': [101.0]*4, 'low': [99.0]*4, 'close': [100.0]*4,
        'volume': [50.0]*4, 'VOL_SMA_20': [50.0]*4,
        'open_interest': [1000.0, 1020.0, 1050.0, 1100.0]
    })
    _, _, _, oi_delta_exp, oi_dir_exp = pattern_service.detect_patterns(df_oi_expanding)
    assert oi_delta_exp == pytest.approx(10.0, 0.01)
    assert oi_dir_exp == "Expanding"

    # Open Interest Declining: past OI = 1000, current = 950 (-5%)
    df_oi_declining = pd.DataFrame({
        'open': [100.0]*4, 'high': [101.0]*4, 'low': [99.0]*4, 'close': [100.0]*4,
        'volume': [50.0]*4, 'VOL_SMA_20': [50.0]*4,
        'open_interest': [1000.0, 980.0, 960.0, 950.0]
    })
    _, _, _, oi_delta_dec, oi_dir_dec = pattern_service.detect_patterns(df_oi_declining)
    assert oi_delta_dec == pytest.approx(-5.0, 0.01)
    assert oi_dir_dec == "Declining"

    # Edge Case: Zero past open interest does not cause ZeroDivisionError
    df_oi_zero = pd.DataFrame({
        'open': [100.0]*4, 'high': [101.0]*4, 'low': [99.0]*4, 'close': [100.0]*4,
        'volume': [50.0]*4, 'VOL_SMA_20': [50.0]*4,
        'open_interest': [0.0, 0.0, 0.0, 500.0]
    })
    _, _, _, oi_delta_zero, oi_dir_zero = pattern_service.detect_patterns(df_oi_zero)
    assert oi_delta_zero == 0.0
    assert oi_dir_zero == "Neutral"
