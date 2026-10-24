"""
Unit and Integration Tests for Confluence & Deduction Telemetry Audit
=====================================================================
Validates that the ConfluenceAuditor properly detects dead confluences,
monitors deduction frequencies, and verifies that the end-to-end signal
pipeline produces non-zero institutional confluences.
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from core.confluence_auditor import ConfluenceAuditor
from engine.signal_generator import SignalGeneratorService
from engine.ai_evaluator import evaluate_confluence_score


def test_confluence_auditor_dead_confluence_detection():
    """Verify that auditor correctly identifies dead confluences and generates warnings."""
    auditor = ConfluenceAuditor()

    # Synthetic mock traces where VWAP and Shield are missing
    mock_traces = [
        {
            "vol_points": 5.0,
            "level_points": 4.0,
            "vwap_points": 0.0,  # Dead
            "breakdown": {
                "macro_trend_aligned": True,
                "btc_aligned": True,
                "funding_rate_tailwind": True,
                "imbalance_ratio": 1.5,
                "ob_defensive_shield": False,  # Dead
                "oi_delta_15m": 0.0,           # Dead
                "volume_ratio": 1.5,
                "vwap_aligned": False
            }
        }
        for _ in range(10)
    ]

    result = auditor.audit_traces(mock_traces)
    assert result["health_status"] == "DEFECTS_DETECTED"
    assert "Session VWAP Acceptance" in result["dead_confluences"]
    assert "Order Book Defensive Shield" in result["dead_confluences"]
    assert "Open Interest Expansion" in result["dead_confluences"]
    assert len(result["warnings"]) >= 3


def test_confluence_auditor_healthy_pipeline():
    """Verify that auditor reports HEALTHY when all confluences fire across traces."""
    auditor = ConfluenceAuditor()

    # Traces where all confluences fire at least once
    mock_traces = [
        {
            "vol_points": 8.0,
            "level_points": 6.0,
            "vwap_points": 4.0,
            "catalyst_points": 5.5,
            "breakdown": {
                "macro_trend_aligned": True,
                "btc_aligned": True,
                "funding_rate_tailwind": True,
                "imbalance_ratio": 2.0,
                "ob_defensive_shield": True,
                "rsi_divergence": True,
                "target_magnet": True,
                "oi_delta_15m": 3.0,
                "rsi_momentum_room": True,
                "volume_ratio": 2.0,
                "vwap_aligned": True,
                "is_squeezed": True
            }
        },
        {
            "vol_points": 4.0,
            "level_points": 5.0,
            "vwap_points": 4.0,
            "catalyst_points": 2.0,
            "breakdown": {
                "macro_trend_aligned": True,
                "btc_aligned": True,
                "funding_rate_tailwind": False,
                "imbalance_ratio": 1.2,
                "ob_defensive_shield": True,
                "rsi_divergence": False,
                "target_magnet": False,
                "oi_delta_15m": 2.0,
                "rsi_momentum_room": True,
                "volume_ratio": 1.2,
                "vwap_aligned": True,
                "is_squeezed": False
            }
        }
    ]

    result = auditor.audit_traces(mock_traces)
    report = auditor.generate_markdown_report(result)
    assert "HEALTHY" in result["health_status"]
    assert len(result["dead_confluences"]) == 0
    assert "[OK]" in report


def test_end_to_end_pipeline_confluences_live_data_simulation():
    """
    End-to-end integration test verifying that:
    1. Robust VWAP is computed and aligned
    2. Live Open Interest delta computes from history
    3. Order book clusters produce shields and targets
    4. Short imbalance awards positive points
    5. Final evaluation qualifies for VIP tier (>= 92%)
    """
    sg = SignalGeneratorService(percentage_trigger=0.5, static_alerts={})

    # 1. Create 60 candles with oscillating market waves (RSI ~50) and decisive breakdown
    now = datetime.now(timezone.utc)
    timestamps = [now - timedelta(minutes=5 * (60 - i)) for i in range(60)]
    closes = [2200.0 + (1.5 if i % 2 == 0 else -1.5) for i in range(59)] + [2185.0]
    opens = [2200.0 + (-1.5 if i % 2 == 0 else 1.5) for i in range(59)] + [2199.0]
    highs = [max(o, c) + 1.0 for o, c in zip(opens, closes)]
    lows = [min(o, c) - 1.0 for o, c in zip(opens, closes)]
    volumes = [1000.0] * 59 + [3000.0]  # Last candle 3x volume surge

    df = pd.DataFrame({
        'timestamp': timestamps,
        'open': opens,
        'high': highs,
        'low': lows,
        'close': closes,
        'volume': volumes
    })

    # 2. Build realistic order book around entry ~2185.0
    entry = closes[-1]
    bids = [[entry - (i * 0.2), 10.0] for i in range(1, 100)]
    asks = [[entry + (i * 0.2), 10.0] for i in range(1, 100)]
    asks[5] = [entry + 1.2, 500.0]  # Massive ask wall: 500 * 2186 = ~$1.09M USD

    microstructure = {
        'funding_rate': 0.015,  # Positive funding (tailwinds for short)
        'imbalance_ratio': 0.40,  # Heavy sell pressure (asks >> bids)
        'open_interest': 50000.0,
        'order_book': {
            'bids': bids,
            'asks': asks
        }
    }

    # Simulate prior OI reading 15 mins ago (45000 -> 50000 = +11.1% expansion)
    sg.oi_history['ETH/USDT'] = [
        (now - timedelta(minutes=15), 45000.0)
    ]

    regime_data = {'regime': 'NORMAL', 'phase': 'NORMAL'}
    htf_trend = 'Macro Downtrend (Bearish)'
    htf_4h_trend = 'Macro Downtrend (Bearish)'
    btc_trend = 'Macro Downtrend (Bearish)'

    # Generate signal
    sig = sg.analyze_patterns(
        df=df,
        timeframe="5m",
        htf_trend=htf_trend,
        htf_4h_trend=htf_4h_trend,
        microstructure=microstructure,
        symbol="ETH/USDT",
        btc_trend=btc_trend,
        regime_data=regime_data
    )

    assert sig is not None, "Signal should be generated for clear breakout"
    
    # Verify feature extraction
    assert sig['vwap_aligned'] is True, "Short entry below VWAP should be aligned"
    assert sig['oi_delta_pct'] > 5.0, "OI delta should calculate ~11.1%"
    assert sig['oi_direction'] == "Expanding"
    assert sig['macro_trend_aligned'] is True
    assert sig['btc_trend_aligned'] is True
    assert sig['ob_defensive_shield'] is True, "Massive ask wall behind stop loss should act as shield"

    # Evaluate confluence score
    eval_res = evaluate_confluence_score("ETH/USDT", sig)
    trace = eval_res["eval_trace"]

    # Verify score components
    assert trace["vol_points"] == 8.0, "3.0x volume surge should earn max 8.0 pts"
    assert trace["vwap_points"] == 4.0, "Aligned VWAP should earn 4.0 pts"
    assert trace["trend_tailwinds"] >= 6.5, "Macro + BTC + Funding should max out at 6.5 pts"
    assert trace["ob_points"] >= 4.0, "Defensive shield + Short imbalance should earn >= 4.0 pts"
    assert trace["catalyst_points"] >= 1.5, "OI expansion should earn catalyst pts"
    assert trace["friction"] == 0.0, "No opposing friction should be applied"
    assert eval_res["final_score"] >= 92.0, f"Expected VIP score >= 92.0%, got {eval_res['final_score']}%"
    assert eval_res["tier"] == "VIP", f"Expected VIP tier, got {eval_res['tier']}"
