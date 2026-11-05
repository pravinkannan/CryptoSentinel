import pytest
import pandas as pd
from datetime import datetime, timezone
from unittest.mock import MagicMock, AsyncMock

from application.pipeline.pipeline import SignalContext
from application.pipeline.steps import RiskAndPositionSizingStep
from engine.risk_manager import RiskManager
from domain.confluence.engine import evaluate_confluence_score
from core.config import settings


# ------------------------------------------------------------------------------
# FIX 1: Asset-Level Position Lock Tests
# ------------------------------------------------------------------------------
@pytest.mark.anyio
async def test_asset_level_position_lock():
    """Verify strict Maximum 1 Open Trade Per Symbol rule across all timeframes."""
    risk_manager = RiskManager()
    state_manager = MagicMock()
    # Active trade already open on SOL/USDT 15m
    state_manager.state = {
        "active_signals": {
            "SOL/USDT_15m": {
                "symbol": "SOL/USDT",
                "timeframe": "15m",
                "direction": "SHORT",
                "confidence": 88.0,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "trade_id": "test-trade-123"
            }
        }
    }

    step = RiskAndPositionSizingStep(risk_manager=risk_manager, state_manager=state_manager)

    # Attempt to process a new trade for SOL/USDT on 5m
    ctx = SignalContext(
        symbol="SOL/USDT",
        timeframe="5m",
        signal_data={"close": 150.0, "entry": 150.0, "direction": "SHORT"},
        evaluated_signal={"trade_direction": "SHORT", "final_confidence_rating": 89.0}
    )

    result = await step.process(ctx)
    assert result is None  # Must reject because SOL/USDT already has an open position!


# ------------------------------------------------------------------------------
# FIX 2: Anti-Sweep Stop-Loss Buffer (+0.35%) Tests
# ------------------------------------------------------------------------------
def test_anti_sweep_stop_loss_buffer():
    """Verify +0.35% anti-sweep buffer beyond structural swing high/low."""
    rm = RiskManager()
    
    # 20 candles where swing high = 153.0, swing low = 147.0, tiny ATR so swing SL dominates
    df = pd.DataFrame({
        'open': [150.0] * 20,
        'high': [150.0] * 19 + [153.0],
        'low': [150.0] * 19 + [147.0],
        'close': [150.0] * 20,
        'volume': [1000] * 20,
        'ATRr_14': [0.01] * 20
    })

    # Dynamic ATR Stop-Loss (Pillar 4): Clamped by SOL tier_min_sl_pct floor of 1.50%
    bearish_params = rm.compute_risk_parameters(df, "SOL/USDT", "15m", "Bearish", 150.0, "NORMAL")
    assert bearish_params is not None
    assert bearish_params['stop_loss'] == 152.25  # 150.0 * 1.015 (1.50% floor)

    bullish_params = rm.compute_risk_parameters(df, "SOL/USDT", "15m", "Bullish", 150.0, "NORMAL")
    assert bullish_params is not None
    assert bullish_params['stop_loss'] == 147.75  # 150.0 * (1 - 0.015) (1.50% floor)


# ------------------------------------------------------------------------------
# FIX 3: Dynamic Leverage Caps Tests
# ------------------------------------------------------------------------------
def test_dynamic_leverage_caps_by_asset_class():
    """Verify BTC max 20x, ETH max 15x, and Altcoins max 10x."""
    rm = RiskManager()
    
    # Minimal volatility setup that would naturally demand maximum leverage
    df = pd.DataFrame({
        'open': [100.0] * 20,
        'high': [100.1] * 20,
        'low': [99.9] * 20,
        'close': [100.0] * 20,
        'volume': [1000] * 20,
        'ATRr_14': [0.05] * 20
    })

    btc_params = rm.compute_risk_parameters(df, "BTC/USDT", "5m", "Bullish", 100.0, "NORMAL")
    eth_params = rm.compute_risk_parameters(df, "ETH/USDT", "5m", "Bullish", 100.0, "NORMAL")
    sol_params = rm.compute_risk_parameters(df, "SOL/USDT", "5m", "Bullish", 100.0, "NORMAL")

    assert btc_params['leverage_val'] <= 20.0
    assert eth_params['leverage_val'] <= 15.0
    assert sol_params['leverage_val'] <= 10.0


# ------------------------------------------------------------------------------
# FIX 4: Range-Bound Counter-Trend Mean Reversion Bypass Tests
# ------------------------------------------------------------------------------
def test_mean_reversion_bypass_long_in_bearish_macro():
    """Verify 5m/15m LONG bypasses 4H Bearish gate when VWAP extended (>= 1.5%) and RSI <= 30."""
    signal_data = {
        "symbol": "SOL/USDT",
        "timeframe": "15m",
        "direction": "LONG",
        "entry": 140.0,
        "close": 140.0,
        "vwap": 145.0,  # entry is 3.45% below VWAP (> 1.5%)
        "rsi_14": 28.0,  # oversold (< 30.0)
        "htf_4h_trend": "Macro Down (Bearish)",
        "patterns": ["Bullish Hammer"],
        "key_level_rejection": True,
        "volume": 2000,
        "vol_sma_20": 1000,
        "volume_ratio": 2.0
    }

    res = evaluate_confluence_score("SOL/USDT", signal_data)
    # Circuit breaker must NOT trip on 4H Macro Conflict because bypass is active
    assert res.get("circuit_breaker_tripped") is False
    assert res.get("eval_trace", {}).get("fatal_flaw") != "Phase 1 Gate Failed: 4H Macro Conflict"
    assert any("Mean Reversion Bypass" in c for c in res.get("scoring_checklist", []))


def test_mean_reversion_bypass_short_in_bullish_macro():
    """Verify 5m/15m SHORT bypasses 4H Bullish gate when VWAP extended (>= 1.5%) and RSI >= 70."""
    signal_data = {
        "symbol": "SOL/USDT",
        "timeframe": "5m",
        "direction": "SHORT",
        "entry": 160.0,
        "close": 160.0,
        "vwap": 155.0,  # entry is 3.23% above VWAP (> 1.5%)
        "rsi_14": 74.0,  # overbought (>= 70.0)
        "htf_4h_trend": "Macro Up (Bullish)",
        "patterns": ["Shooting Star"],
        "key_level_rejection": True,
        "volume": 2000,
        "vol_sma_20": 1000,
        "volume_ratio": 2.0
    }

    res = evaluate_confluence_score("SOL/USDT", signal_data)
    assert res.get("circuit_breaker_tripped") is False
    assert res.get("eval_trace", {}).get("fatal_flaw") != "Phase 1 Gate Failed: 4H Macro Conflict"
    assert any("Mean Reversion Bypass" in c for c in res.get("scoring_checklist", []))


# ------------------------------------------------------------------------------
# FIX 5: Portfolio Basket Correlation Guard Tests
# ------------------------------------------------------------------------------
@pytest.mark.anyio
async def test_portfolio_basket_correlation_guard(monkeypatch):
    """Verify that when 2 SHORT positions are already active, a 3rd SHORT candidate is blocked."""
    risk_manager = RiskManager()
    monkeypatch.setattr(settings, 'max_same_direction_positions', 2)
    state_manager = MagicMock()
    # 2 active SHORT positions already open across portfolio (BTC and ETH)
    state_manager.state = {
        "active_signals": {
            "BTC/USDT_15m": {
                "symbol": "BTC/USDT",
                "timeframe": "15m",
                "direction": "SHORT",
                "confidence": 88.0,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "trade_id": "test-btc-1"
            },
            "ETH/USDT_15m": {
                "symbol": "ETH/USDT",
                "timeframe": "15m",
                "direction": "SHORT",
                "confidence": 89.0,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "trade_id": "test-eth-1"
            }
        }
    }

    step = RiskAndPositionSizingStep(risk_manager=risk_manager, state_manager=state_manager)

    # 3rd SHORT candidate on SOL/USDT must be rejected to prevent correlated basket clustering
    ctx_short = SignalContext(
        symbol="SOL/USDT",
        timeframe="15m",
        signal_data={"close": 150.0, "entry": 150.0, "direction": "SHORT"},
        evaluated_signal={"trade_direction": "SHORT", "final_confidence_rating": 89.0}
    )
    result_short = await step.process(ctx_short)
    assert result_short is None

    # But a LONG candidate on SOL/USDT is permitted because it doesn't add to the SHORT cluster
    ctx_long = SignalContext(
        symbol="SOL/USDT",
        timeframe="15m",
        signal_data={"close": 150.0, "entry": 150.0, "direction": "LONG", "stop_loss": 147.0, "tp1": 153.0},
        evaluated_signal={"trade_direction": "LONG", "final_confidence_rating": 89.0}
    )
    result_long = await step.process(ctx_long)
    assert result_long is not None



@pytest.mark.anyio
async def test_portfolio_basket_correlation_guard_allows_multiple_directed_positions():
    """Verify that multiple directed positions are allowed under default max_same_direction_positions=5."""
    risk_manager = RiskManager()
    state_manager = MagicMock()
    state_manager.state = {
        "active_signals": {
            "BTC/USDT_15m": {
                "symbol": "BTC/USDT",
                "timeframe": "15m",
                "direction": "SHORT",
                "confidence": 88.0,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "trade_id": "test-btc-1"
            },
            "ETH/USDT_15m": {
                "symbol": "ETH/USDT",
                "timeframe": "15m",
                "direction": "SHORT",
                "confidence": 89.0,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "trade_id": "test-eth-1"
            }
        }
    }

    step = RiskAndPositionSizingStep(risk_manager=risk_manager, state_manager=state_manager)

    # 3rd SHORT candidate on SOL/USDT must succeed because default cap is 5
    ctx_short = SignalContext(
        symbol="SOL/USDT",
        timeframe="15m",
        signal_data={"close": 150.0, "entry": 150.0, "direction": "SHORT", "stop_loss": 153.0, "tp1": 147.0},
        evaluated_signal={"trade_direction": "SHORT", "final_confidence_rating": 89.0}
    )
    result_short = await step.process(ctx_short)
    assert result_short is not None


@pytest.mark.anyio
async def test_asset_level_position_lock_multi_tf_enabled(monkeypatch):
    """Verify that when allow_multi_timeframe_positions_per_symbol is True, concurrent same-direction trades are allowed."""
    monkeypatch.setattr(settings, 'allow_multi_timeframe_positions_per_symbol', True)
    risk_manager = RiskManager()
    state_manager = MagicMock()
    state_manager.state = {
        "active_signals": {
            "SOL/USDT_15m": {
                "symbol": "SOL/USDT",
                "timeframe": "15m",
                "direction": "SHORT",
                "confidence": 88.0,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "trade_id": "test-trade-123"
            }
        }
    }

    step = RiskAndPositionSizingStep(risk_manager=risk_manager, state_manager=state_manager)

    # 5m SHORT candidate on SOL/USDT is permitted when allow_multi_timeframe_positions_per_symbol is True
    ctx = SignalContext(
        symbol="SOL/USDT",
        timeframe="5m",
        signal_data={"close": 150.0, "entry": 150.0, "direction": "SHORT", "stop_loss": 153.0, "tp1": 147.0},
        evaluated_signal={"trade_direction": "SHORT", "final_confidence_rating": 89.0}
    )

    result = await step.process(ctx)
    assert result is not None
