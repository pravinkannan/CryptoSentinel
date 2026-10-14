import pytest
import asyncio
import pandas as pd
import json
from unittest.mock import AsyncMock, MagicMock

from engine.signal_generator import SignalGeneratorService
from engine.ai_evaluator import AIEvaluatorService, evaluate_confluence_score


def create_mock_ai_evaluator(ai_payload=None):
    """Helper to instantiate AIEvaluatorService with mocked Gemini response."""
    evaluator = AIEvaluatorService(api_key="dummy_key")
    default_payload = {
        "trade_direction": "LONG",
        "overall_verdict": "APPROVE",
        "strategy_analysis": "Automated parametric test audit.",
        "live_news_penalty": 0,
        "institutional_wall_penalty": 0,
        "audit_notes": []
    }
    payload = ai_payload or default_payload
    mock_response = MagicMock()
    mock_response.text = json.dumps(payload)
    evaluator.client.aio.models.generate_content = AsyncMock(return_value=mock_response)
    return evaluator


# ==============================================================================
# 1. HTF TREND CALCULATION TESTS
# ==============================================================================

def test_htf_trend_calculation_ema200_and_fallback():
    """Verify get_htf_trend correctly computes EMA 200 and EMA 50 fallback."""
    sg = SignalGeneratorService(percentage_trigger=0.5, static_alerts={})

    # Case 1: 250 candles (EMA 200 path - Positive Uptrend)
    prices = [100.0 + i * 0.5 for i in range(250)]
    df_250 = pd.DataFrame({
        'timestamp': pd.date_range('2026-01-01', periods=250, freq='1h'),
        'close': prices,
        'open': prices,
        'high': [p + 1.0 for p in prices],
        'low': [p - 1.0 for p in prices],
        'volume': [1000.0] * 250
    })
    trend = sg.get_htf_trend(df_250)
    assert trend == "Macro Uptrend (Bullish)"

    # Case 2: 100 candles (EMA 50 fallback path - Positive Downtrend)
    prices_down = [200.0 - i * 0.5 for i in range(100)]
    df_100 = pd.DataFrame({
        'timestamp': pd.date_range('2026-01-01', periods=100, freq='1h'),
        'close': prices_down,
        'open': prices_down,
        'high': [p + 1.0 for p in prices_down],
        'low': [p - 1.0 for p in prices_down],
        'volume': [1000.0] * 100
    })
    trend_50 = sg.get_htf_trend(df_100)
    assert trend_50 == "Macro Downtrend (Bearish)"

    # Case 3: Under 50 candles -> Unknown
    df_30 = pd.DataFrame({'close': [100.0] * 30})
    assert sg.get_htf_trend(df_30) == "Unknown (Macro Trend)"

    # Case 4: None DataFrame -> Unknown
    assert sg.get_htf_trend(None) == "Unknown (Macro Trend)"


# ==============================================================================
# 2. STAGE 1: HARD BINARY SANITY GATE (THE CIRCUIT BREAKER)
# ==============================================================================

def test_stage_1_macro_4h_conflict_direct_flag():
    """Circuit Breaker 1: Explicit macro_4h_conflict=True halts evaluation."""
    sig = {
        'direction': 'LONG',
        'macro_4h_conflict': True,
        'volume': 1500.0,
        'vol_sma_20': 1000.0,
        'key_level_rejection': True
    }
    result = evaluate_confluence_score("BTC/USDT", sig)
    assert result["tier"] == "REJECTED"
    assert result["final_score"] == 0.0
    assert result["eval_trace"]["stage1_passed"] is False
    assert result["eval_trace"]["circuit_breaker"] == "4H Macro Conflict"


def test_stage_1_macro_4h_conflict_inferred_from_htf():
    """Circuit Breaker 1: Inferred 4H macro conflict for Long and Short."""
    # Long against Macro Downtrend
    sig_long = {
        'direction': 'LONG',
        'htf_4h_trend': 'Macro Downtrend (Bearish)',
        'volume': 1500.0,
        'vol_sma_20': 1000.0,
        'key_level_rejection': True
    }
    res_l = evaluate_confluence_score("BTC/USDT", sig_long)
    assert res_l["tier"] == "REJECTED"
    assert res_l["final_score"] == 0.0
    assert res_l["eval_trace"]["stage1_passed"] is False

    # Short against Macro Uptrend
    sig_short = {
        'direction': 'SHORT',
        'htf_4h_trend': 'Macro Uptrend (Bullish)',
        'volume': 1500.0,
        'vol_sma_20': 1000.0,
        'key_level_rejection': True
    }
    res_s = evaluate_confluence_score("BTC/USDT", sig_short)
    assert res_s["tier"] == "REJECTED"
    assert res_s["final_score"] == 0.0
    assert res_s["eval_trace"]["stage1_passed"] is False


def test_stage_1_rsi_exhaustion_circuit_breaker():
    """Circuit Breaker 2: RSI > 75 for Long, RSI < 25 for Short."""
    # Long with RSI 76.0 (exhausted)
    sig_long_ex = {
        'direction': 'LONG',
        'rsi_14': 76.0,
        'volume': 1500.0,
        'vol_sma_20': 1000.0,
        'key_level_rejection': True
    }
    res_l = evaluate_confluence_score("BTC/USDT", sig_long_ex)
    assert res_l["tier"] == "REJECTED"
    assert res_l["final_score"] == 0.0
    assert "RSI Exhaustion" in res_l["eval_trace"]["circuit_breaker"]

    # Long with RSI 74.5 (not exhausted -> passes Stage 1)
    sig_long_ok = {
        'direction': 'LONG',
        'rsi_14': 74.5,
        'volume': 1500.0,
        'vol_sma_20': 1000.0,
        'key_level_rejection': True
    }
    res_ok = evaluate_confluence_score("BTC/USDT", sig_long_ok)
    assert res_ok["eval_trace"]["stage1_passed"] is True
    assert res_ok["final_score"] > 0.0

    # Short with RSI 24.0 (exhausted)
    sig_short_ex = {
        'direction': 'SHORT',
        'rsi_14': 24.0,
        'volume': 1500.0,
        'vol_sma_20': 1000.0,
        'key_level_rejection': True
    }
    res_s = evaluate_confluence_score("ETH/USDT", sig_short_ex)
    assert res_s["tier"] == "REJECTED"
    assert res_s["final_score"] == 0.0
    assert "RSI Exhaustion" in res_s["eval_trace"]["circuit_breaker"]


def test_stage_1_anemic_volume_trap_circuit_breaker():
    """Circuit Breaker 3: Volume < 0.55 * SMA AND key_level_rejection == False."""
    # Anemic volume without key level rejection -> Circuit Breaker trips
    sig_trap = {
        'direction': 'LONG',
        'volume': 500.0,
        'vol_sma_20': 1000.0,  # 0.50x < 0.55x
        'key_level_rejection': False
    }
    res_trap = evaluate_confluence_score("SOL/USDT", sig_trap)
    assert res_trap["tier"] == "REJECTED"
    assert res_trap["final_score"] == 0.0
    assert res_trap["eval_trace"]["stage1_passed"] is False
    assert res_trap["eval_trace"]["circuit_breaker"] == "Anemic Volume Trap"

    # Anemic volume BUT key_level_rejection == True -> Passes Stage 1
    sig_saved = {
        'direction': 'LONG',
        'volume': 500.0,
        'vol_sma_20': 1000.0,
        'key_level_rejection': True,
        'dist_to_key_level_pct': 0.10
    }
    res_saved = evaluate_confluence_score("SOL/USDT", sig_saved)
    assert res_saved["eval_trace"]["stage1_passed"] is True
    assert res_saved["final_score"] > 0.0

    # Normal volume (>= 0.55x SMA) without key level rejection -> Passes Stage 1
    sig_normal = {
        'direction': 'LONG',
        'volume': 600.0,
        'vol_sma_20': 1000.0,  # 0.60x >= 0.55x
        'key_level_rejection': False
    }
    res_normal = evaluate_confluence_score("SOL/USDT", sig_normal)
    assert res_normal["eval_trace"]["stage1_passed"] is True


# ==============================================================================
# 3. STAGE 2: PARAMETRIC CONTINUOUS SCORING RULES
# ==============================================================================

def test_stage_2_base_score():
    """Base score begins at 68.0 without any modifiers."""
    sig = {
        'direction': 'LONG',
        'volume': 1000.0,
        'vol_sma_20': 1000.0,  # 1.0x ratio -> 0 vol_points
        'open': 100.0,
        'close': 102.0,
        'key_level_rejection': False
    }
    res = evaluate_confluence_score("BTC/USDT", sig)
    assert res["eval_trace"]["base_score"] == 68.0
    assert res["eval_trace"]["vol_points"] == 0.0
    assert res["eval_trace"]["level_points"] == 0.0
    assert res["final_score"] == 68.0
    assert res["tier"] == "REJECTED"  # Below 80.0% threshold


def test_stage_2_dynamic_institutional_volume_formula():
    """Formula: vol_points = min(12.0, max(0.0, ((volume / vol_sma_20) - 1.0) * 8.0))."""
    # 1.5x volume: (1.5 - 1.0) * 8.0 = 4.0 points
    sig_1_5x = {
        'direction': 'LONG',
        'open': 100.0,
        'close': 105.0,
        'volume': 1500.0,
        'vol_sma_20': 1000.0,
        'key_level_rejection': True
    }
    res_1_5x = evaluate_confluence_score("BTC/USDT", sig_1_5x)
    assert res_1_5x["eval_trace"]["vol_points"] == 4.0

    # 2.5x volume: (2.5 - 1.0) * 8.0 = 12.0 points (max limit)
    sig_2_5x = {
        'direction': 'LONG',
        'open': 100.0,
        'close': 105.0,
        'volume': 2500.0,
        'vol_sma_20': 1000.0,
        'key_level_rejection': True
    }
    res_2_5x = evaluate_confluence_score("BTC/USDT", sig_2_5x)
    assert res_2_5x["eval_trace"]["vol_points"] == 12.0

    # 3.0x volume clamped at +12.0%
    sig_3x = {
        'direction': 'LONG',
        'open': 100.0,
        'close': 105.0,
        'volume': 3000.0,
        'vol_sma_20': 1000.0,
        'key_level_rejection': True
    }
    res_3x = evaluate_confluence_score("BTC/USDT", sig_3x)
    assert res_3x["eval_trace"]["vol_points"] == 12.0

    # Sub-SMA volume (0.9x): max(0.0, (0.9 - 1.0) * 8.0) = 0.0 points
    sig_sub = {
        'direction': 'LONG',
        'open': 100.0,
        'close': 105.0,
        'volume': 900.0,
        'vol_sma_20': 1000.0,
        'key_level_rejection': True
    }
    res_sub = evaluate_confluence_score("BTC/USDT", sig_sub)
    assert res_sub["eval_trace"]["vol_points"] == 0.0


def test_stage_2_volume_directional_fix():
    """Directional Fix: Only award volume points if candle closed strongly in trade direction."""
    # Long trade with red candle (close < open) -> 0 volume points despite 2.0x volume!
    sig_long_red = {
        'direction': 'LONG',
        'open': 105.0,
        'close': 100.0,
        'volume': 2000.0,
        'vol_sma_20': 1000.0,
        'key_level_rejection': True
    }
    res_lr = evaluate_confluence_score("BTC/USDT", sig_long_red)
    assert res_lr["eval_trace"]["vol_points"] == 0.0
    assert res_lr["eval_trace"]["breakdown"]["directional_aligned"] is False

    # Short trade with green candle (close > open) -> 0 volume points
    sig_short_green = {
        'direction': 'SHORT',
        'open': 100.0,
        'close': 105.0,
        'volume': 2000.0,
        'vol_sma_20': 1000.0,
        'key_level_rejection': True
    }
    res_sg = evaluate_confluence_score("BTC/USDT", sig_short_green)
    assert res_sg["eval_trace"]["vol_points"] == 0.0
    assert res_sg["eval_trace"]["breakdown"]["directional_aligned"] is False

    # Short trade with red candle (close < open) -> Awards full points
    sig_short_red = {
        'direction': 'SHORT',
        'open': 105.0,
        'close': 100.0,
        'volume': 2000.0,
        'vol_sma_20': 1000.0,
        'key_level_rejection': True
    }
    res_sr = evaluate_confluence_score("BTC/USDT", sig_short_red)
    assert res_sr["eval_trace"]["vol_points"] == 8.0
    assert res_sr["eval_trace"]["breakdown"]["directional_aligned"] is True


def test_stage_2_structural_level_precision_formula_and_rejection_fix():
    """
    Structural Level Precision:
    - Rejection Fix: Only award if key_level_rejection == True
    - level_points = max(0.0, 10.0 * (1.0 - (dist / 0.30))) if dist <= 0.30 else 0.0
    """
    # Exact pin (dist = 0.0): 10.0 points
    sig_exact = {
        'direction': 'LONG',
        'key_level_rejection': True,
        'dist_to_key_level_pct': 0.0
    }
    res_exact = evaluate_confluence_score("BTC/USDT", sig_exact)
    assert res_exact["eval_trace"]["level_points"] == 10.0

    # Halfway (dist = 0.15): 10.0 * (1 - 0.5) = 5.0 points
    sig_half = {
        'direction': 'LONG',
        'key_level_rejection': True,
        'dist_to_key_level_pct': 0.15
    }
    res_half = evaluate_confluence_score("BTC/USDT", sig_half)
    assert res_half["eval_trace"]["level_points"] == 5.0

    # At boundary (dist = 0.30): 0.0 points
    sig_boundary = {
        'direction': 'LONG',
        'key_level_rejection': True,
        'dist_to_key_level_pct': 0.30
    }
    res_boundary = evaluate_confluence_score("BTC/USDT", sig_boundary)
    assert res_boundary["eval_trace"]["level_points"] == 0.0

    # Outside threshold (dist = 0.40): 0.0 points
    sig_outside = {
        'direction': 'LONG',
        'key_level_rejection': True,
        'dist_to_key_level_pct': 0.40
    }
    res_outside = evaluate_confluence_score("BTC/USDT", sig_outside)
    assert res_outside["eval_trace"]["level_points"] == 0.0

    # Rejection Fix: No key_level_rejection -> 0.0 points even with dist = 0.0
    sig_no_rej = {
        'direction': 'LONG',
        'key_level_rejection': False,
        'dist_to_key_level_pct': 0.0
    }
    res_no_rej = evaluate_confluence_score("BTC/USDT", sig_no_rej)
    assert res_no_rej["eval_trace"]["level_points"] == 0.0


def test_stage_2_order_book_depth_and_shield():
    """
    Order Book Depth (+0.0% to +8.0%):
    - Thin Book Fix: Only calculate imbalance points if total_book_depth_usd > 100000
    - ob_points = min(5.0, max(0.0, (imbalance_ratio - 1.0) * 3.5))
    - Add +3.0 if ob_defensive_shield == True
    """
    # Thin Book (< $100,000): Imbalance points ignored
    sig_thin = {
        'direction': 'LONG',
        'total_book_depth_usd': 80000.0,
        'imbalance_ratio': 2.0,
        'key_level_rejection': True
    }
    res_thin = evaluate_confluence_score("BTC/USDT", sig_thin)
    assert res_thin["eval_trace"]["ob_points"] == 0.0

    # Deep Book with 2.0x imbalance: (2.0 - 1.0) * 3.5 = 3.5 points
    sig_deep = {
        'direction': 'LONG',
        'total_book_depth_usd': 200000.0,
        'imbalance_ratio': 2.0,
        'key_level_rejection': True
    }
    res_deep = evaluate_confluence_score("BTC/USDT", sig_deep)
    assert res_deep["eval_trace"]["ob_points"] == 3.5

    # Deep Book with defensive shield: 3.5 + 3.0 = 6.5 points
    sig_shield = {
        'direction': 'LONG',
        'total_book_depth_usd': 200000.0,
        'imbalance_ratio': 2.0,
        'ob_defensive_shield': True,
        'key_level_rejection': True
    }
    res_shield = evaluate_confluence_score("BTC/USDT", sig_shield)
    assert res_shield["eval_trace"]["ob_points"] == 6.5

    # Deep Book capped at 8.0%: 3.0x imbalance (capped at 5.0) + 3.0 shield = 8.0 points
    sig_max_ob = {
        'direction': 'LONG',
        'total_book_depth_usd': 200000.0,
        'imbalance_ratio': 3.0,
        'ob_defensive_shield': True,
        'key_level_rejection': True
    }
    res_max_ob = evaluate_confluence_score("BTC/USDT", sig_max_ob)
    assert res_max_ob["eval_trace"]["ob_points"] == 8.0


def test_stage_2_trend_and_derivatives():
    """
    Trend & Derivatives (+0.0% to +10.0%):
    - +4.0 if macro_trend_aligned == True
    - +2.0 if funding_rate_tailwind == True
    - +2.0 if oi_delta_15m > 1.5
    - +2.0 if RSI Momentum Room (40-60)
    """
    sig = {
        'direction': 'LONG',
        'macro_trend_aligned': True,       # +4.0
        'funding_rate_tailwind': True,     # +2.0
        'oi_delta_15m': 2.1,               # +2.0
        'rsi_14': 52.0,                    # +2.0
        'key_level_rejection': True
    }
    res = evaluate_confluence_score("BTC/USDT", sig)
    assert res["eval_trace"]["trend_tailwinds"] == 10.0


def test_stage_2_friction_absorption_rule():
    """
    Microstructure Friction (0.0% to -6.0%):
    - -3.0 if volume < 0.80 * SMA
    - -3.0 if blocking_ob_wall == True
    - Absorption Rule: If vol_points > 6.0, cut friction penalty in half (* 0.5)
    """
    # Standard friction: blocking wall without high volume -> -3.0
    sig_wall = {
        'direction': 'LONG',
        'blocking_ob_wall': True,
        'volume': 1000.0,
        'vol_sma_20': 1000.0,  # vol_points = 0.0
        'open': 100.0,
        'close': 102.0,
        'key_level_rejection': True
    }
    res_wall = evaluate_confluence_score("BTC/USDT", sig_wall)
    assert res_wall["eval_trace"]["friction"] == -3.0

    # Low volume drag: volume = 750 (0.75x < 0.80x) -> -3.0
    sig_drag = {
        'direction': 'LONG',
        'volume': 750.0,
        'vol_sma_20': 1000.0,
        'key_level_rejection': True
    }
    res_drag = evaluate_confluence_score("BTC/USDT", sig_drag)
    assert res_drag["eval_trace"]["friction"] == -3.0

    # Both penalties: -6.0
    sig_both = {
        'direction': 'LONG',
        'volume': 750.0,
        'vol_sma_20': 1000.0,
        'blocking_ob_wall': True,
        'key_level_rejection': True
    }
    res_both = evaluate_confluence_score("BTC/USDT", sig_both)
    assert res_both["eval_trace"]["friction"] == -6.0

    # Absorption Rule: vol_points > 6.0 halves friction penalty
    # 2.0x volume on green candle -> vol_points = (2.0 - 1.0) * 8.0 = 8.0 (> 6.0)
    sig_absorb = {
        'direction': 'LONG',
        'open': 100.0,
        'close': 105.0,
        'volume': 2000.0,
        'vol_sma_20': 1000.0,
        'blocking_ob_wall': True,  # normal penalty -3.0
        'key_level_rejection': True
    }
    res_absorb = evaluate_confluence_score("BTC/USDT", sig_absorb)
    assert res_absorb["eval_trace"]["vol_points"] == 8.0
    # Penalty halved from -3.0 to -1.5
    assert res_absorb["eval_trace"]["friction"] == -1.5
    assert res_absorb["eval_trace"]["breakdown"]["friction_absorbed"] is True


# ==============================================================================
# 4. STAGE 3: THE VIP GATE (TIER CLASSIFICATION)
# ==============================================================================

def test_stage_3_vip_gate_qualification_continuous_score():
    """VIP Tier: Score >= 90.0% AND vol_points > 0 AND level_points > 0."""
    sig_vip = {
        'direction': 'LONG',
        'open': 100.0,
        'close': 105.0,
        'volume': 2000.0,
        'vol_sma_20': 1000.0,          # vol_points = 8.0 (> 0)
        'key_level_rejection': True,
        'dist_to_key_level_pct': 0.05, # level_points = 10 * (1 - 0.05/0.30) = 8.33
        'total_book_depth_usd': 200000.0,
        'imbalance_ratio': 1.5,        # ob_points = (1.5 - 1.0) * 3.5 = 1.75
        'macro_trend_aligned': True,   # +4.0
        'funding_rate_tailwind': True, # +2.0
    }
    # Net: 68.0 + 8.0 + 8.33 + 1.75 + 6.0 = 92.08 -> round(92.1, 1)
    res = evaluate_confluence_score("BTC/USDT", sig_vip)
    assert res["final_score"] >= 90.0
    assert res["tier"] == "VIP"
    assert res["eval_trace"]["vip_capped"] is False
    assert res["final_score"] == 92.1


def test_stage_3_vip_gate_capping_missing_volume():
    """The 3-Pillar Mandate: Score >= 90.0% but vol_points == 0 caps at 89.9% STANDARD."""
    sig_no_vol = {
        'direction': 'LONG',
        'open': 105.0,
        'close': 100.0,                # Red candle -> vol_points = 0.0!
        'volume': 2000.0,
        'vol_sma_20': 1000.0,
        'key_level_rejection': True,
        'dist_to_key_level_pct': 0.0,  # level_points = 10.0
        'total_book_depth_usd': 200000.0,
        'imbalance_ratio': 2.0,        # ob_points = 3.5
        'ob_defensive_shield': True,   # ob_points += 3.0 -> 6.5
        'macro_trend_aligned': True,   # +4.0
        'funding_rate_tailwind': True, # +2.0
        'oi_delta_15m': 2.0,           # +2.0
    }
    # Net: 68.0 + 0.0 + 10.0 + 6.5 + 8.0 = 92.5
    res = evaluate_confluence_score("BTC/USDT", sig_no_vol)
    assert res["eval_trace"]["vol_points"] == 0.0
    assert res["eval_trace"]["level_points"] == 10.0
    assert res["eval_trace"]["net_score"] == 92.5
    assert res["final_score"] == 89.9  # Capped at 89.9!
    assert res["tier"] == "STANDARD"
    assert res["eval_trace"]["vip_capped"] is True


def test_stage_3_vip_gate_capping_missing_level_rejection():
    """The 3-Pillar Mandate: Score >= 90.0% but level_points == 0 caps at 89.9% STANDARD."""
    sig_no_level = {
        'direction': 'LONG',
        'open': 100.0,
        'close': 105.0,
        'volume': 2500.0,
        'vol_sma_20': 1000.0,          # vol_points = 12.0
        'key_level_rejection': False,  # level_points = 0.0!
        'total_book_depth_usd': 200000.0,
        'imbalance_ratio': 2.0,        # ob_points = 3.5
        'ob_defensive_shield': True,   # ob_points += 3.0 -> 6.5
        'macro_trend_aligned': True,   # +4.0
        'funding_rate_tailwind': True, # +2.0
    }
    # Net: 68.0 + 12.0 + 0.0 + 6.5 + 6.0 = 92.5
    res = evaluate_confluence_score("BTC/USDT", sig_no_level)
    assert res["eval_trace"]["vol_points"] == 12.0
    assert res["eval_trace"]["level_points"] == 0.0
    assert res["eval_trace"]["net_score"] == 92.5
    assert res["final_score"] == 89.9  # Capped at 89.9!
    assert res["tier"] == "STANDARD"
    assert res["eval_trace"]["vip_capped"] is True


def test_stage_3_standard_tier_range():
    """Scores between 80.0% and 89.9% are classified as STANDARD tier."""
    sig_std = {
        'direction': 'LONG',
        'open': 100.0,
        'close': 105.0,
        'volume': 1500.0,
        'vol_sma_20': 1000.0,          # vol_points = 4.0
        'key_level_rejection': True,
        'dist_to_key_level_pct': 0.15, # level_points = 5.0
        'macro_trend_aligned': True,   # +4.0
        'funding_rate_tailwind': True, # +2.0
    }
    # Net: 68.0 + 4.0 + 5.0 + 0.0 + 6.0 = 83.0
    res = evaluate_confluence_score("BTC/USDT", sig_std)
    assert res["final_score"] == 83.0
    assert res["tier"] == "STANDARD"


def test_stage_3_rejected_tier_below_80():
    """Scores below 80.0% are classified as REJECTED."""
    sig_rej = {
        'direction': 'LONG',
        'open': 100.0,
        'close': 105.0,
        'volume': 1500.0,
        'vol_sma_20': 1000.0,          # vol_points = 4.0
        'key_level_rejection': True,
        'dist_to_key_level_pct': 0.15, # level_points = 5.0
    }
    # Net: 68.0 + 4.0 + 5.0 = 77.0
    res = evaluate_confluence_score("BTC/USDT", sig_rej)
    assert res["final_score"] == 77.0
    assert res["tier"] == "REJECTED"


# ==============================================================================
# 5. AUDIT TRAIL LOGGING (EVAL_TRACE)
# ==============================================================================

def test_eval_trace_detailed_logging():
    """Verify eval_trace records exact decimal calculations for forensic auditing."""
    sig = {
        'direction': 'LONG',
        'open': 100.0,
        'close': 105.0,
        'volume': 1500.0,
        'vol_sma_20': 1000.0,
        'key_level_rejection': True,
        'dist_to_key_level_pct': 0.09,
        'total_book_depth_usd': 180000.0,
        'imbalance_ratio': 1.6,
        'ob_defensive_shield': True,
        'macro_trend_aligned': True,
        'funding_rate_tailwind': True,
        'oi_delta_15m': 2.0,
        'rsi_14': 48.0,
        'blocking_ob_wall': False
    }
    res = evaluate_confluence_score("BTC/USDT", sig)
    trace = res["eval_trace"]
    
    assert trace["stage1_passed"] is True
    assert trace["base_score"] == 68.0
    assert isinstance(trace["vol_points"], float)
    assert isinstance(trace["level_points"], float)
    assert isinstance(trace["ob_points"], float)
    assert isinstance(trace["trend_tailwinds"], float)
    assert isinstance(trace["friction"], float)
    assert isinstance(trace["net_score"], float)
    assert isinstance(trace["final_score"], float)
    assert trace["tier"] in ["VIP", "STANDARD", "REJECTED"]
    
    # Verify breakdown fields
    bd = trace["breakdown"]
    assert bd["volume_ratio"] == 1.5
    assert bd["directional_aligned"] is True
    assert bd["key_level_rejection"] is True
    assert bd["dist_to_key_level_pct"] == 0.09
    assert bd["imbalance_ratio"] == 1.6
    assert bd["total_book_depth_usd"] == 180000.0
    assert bd["ob_defensive_shield"] is True
    assert bd["macro_trend_aligned"] is True
    assert bd["funding_rate_tailwind"] is True
    assert bd["oi_delta_15m"] == 2.0
    assert bd["rsi_14"] == 48.0
    assert bd["rsi_momentum_room"] is True


# ==============================================================================
# 6. INTEGRATION WITH AI EVALUATOR SERVICE
# ==============================================================================

def test_circuit_breaker_short_circuits_llm_call():
    """Verify that Stage 1 circuit breakers return immediately without invoking Gemini."""
    async def run():
        evaluator = create_mock_ai_evaluator()
        sig = {
            'direction': 'LONG',
            'macro_4h_conflict': True,
            'patterns': ['Bullish Engulfing']
        }
        res_raw = await evaluator.evaluate_signal('BTC/USDT', sig, news_data=[])
        assert res_raw is not None
        res = json.loads(res_raw)
        
        assert res["tier"] == "REJECTED"
        assert res["final_confidence_rating"] == 0.0
        assert "Circuit breaker tripped" in res["strategy_analysis"]
        # LLM call was never executed!
        evaluator.client.aio.models.generate_content.assert_not_called()

    asyncio.run(run())


def test_ai_qualitative_adjustments_on_parametric_score():
    """Verify AI qualitative live news penalty applies cleanly to parametric score."""
    async def run():
        evaluator = create_mock_ai_evaluator({
            "trade_direction": "LONG",
            "overall_verdict": "APPROVE",
            "strategy_analysis": "Negative regulatory headline noted.",
            "live_news_penalty": -10,
            "institutional_wall_penalty": 0,
            "audit_notes": []
        })
        sig = {
            'direction': 'LONG',
            'open': 100.0,
            'close': 105.0,
            'volume': 2000.0,
            'vol_sma_20': 1000.0,          # vol_points = 8.0
            'key_level_rejection': True,
            'dist_to_key_level_pct': 0.05, # level_points = 8.33
            'total_book_depth_usd': 200000.0,
            'imbalance_ratio': 1.5,        # ob_points = 1.75
            'macro_trend_aligned': True,   # +4.0
            'funding_rate_tailwind': True, # +2.0
            'patterns': ['Engulfing']
        }
        # Parametric score: 92.1 -> live news penalty -10 -> 82.1
        res_raw = await evaluator.evaluate_signal('BTC/USDT', sig, news_data=[])
        assert res_raw is not None
        res = json.loads(res_raw)
        
        assert res["final_confidence_rating"] == 82.1
        assert res["tier"] == "STANDARD"
        assert "-10% Live News Conflict" in res["scoring_checklist"]

    asyncio.run(run())


# ==============================================================================
# 7. PARAMETERIZED BOUNDARY FIXTURES & EDGE CASES
# ==============================================================================

@pytest.mark.parametrize("direction, rsi_val, expected_rejected", [
    ("LONG", 74.99, False),
    ("LONG", 75.0, False),     # Boundary: <= 75 passes
    ("LONG", 75.01, True),     # Tripped: > 75 fails
    ("SHORT", 25.01, False),
    ("SHORT", 25.0, False),    # Boundary: >= 25 passes
    ("SHORT", 24.99, True),    # Tripped: < 25 fails
])
def test_stage_1_rsi_boundaries_parameterized(direction, rsi_val, expected_rejected):
    """Verify exact decimal boundary transitions for RSI exhaustion circuit breaker."""
    sig = {
        'direction': direction,
        'rsi_14': rsi_val,
        'volume': 1500.0,
        'vol_sma_20': 1000.0,
        'key_level_rejection': True
    }
    res = evaluate_confluence_score("BTC/USDT", sig)
    if expected_rejected:
        assert res["tier"] == "REJECTED"
        assert res["final_score"] == 0.0
        assert res["eval_trace"]["stage1_passed"] is False
    else:
        assert res["eval_trace"]["stage1_passed"] is True


@pytest.mark.parametrize("vol, sma, key_rejection, expected_passed", [
    (550.0, 1000.0, False, True),    # 0.55x SMA: Exactly at boundary, passes without rejection
    (549.9, 1000.0, False, False),   # < 0.55x SMA: Below boundary, trips circuit breaker
    (549.9, 1000.0, True, True),     # < 0.55x SMA but key level rejection holds: saved!
    (0.0, 1000.0, False, False),     # Zero volume without rejection: fails
    (0.0, 1000.0, True, True),       # Zero volume with rejection: passes Stage 1
])
def test_stage_1_anemic_volume_boundaries_parameterized(vol, sma, key_rejection, expected_passed):
    """Verify exact decimal volume boundaries for anemic volume trap."""
    sig = {
        'direction': 'LONG',
        'volume': vol,
        'vol_sma_20': sma,
        'key_level_rejection': key_rejection
    }
    res = evaluate_confluence_score("BTC/USDT", sig)
    assert res["eval_trace"]["stage1_passed"] is expected_passed


@pytest.mark.parametrize("dist, expected_points", [
    (0.0, 10.0),
    (0.15, 5.0),
    (0.30, 0.0),      # Exact boundary
    (0.3001, 0.0),    # Beyond boundary
    (0.50, 0.0),
])
def test_stage_2_structural_level_distance_boundaries_parameterized(dist, expected_points):
    """Verify continuous linear decay of structural level precision points."""
    sig = {
        'direction': 'LONG',
        'key_level_rejection': True,
        'dist_to_key_level_pct': dist
    }
    res = evaluate_confluence_score("BTC/USDT", sig)
    assert pytest.approx(res["eval_trace"]["level_points"], abs=0.01) == expected_points


@pytest.mark.parametrize("depth_usd, imbalance, expected_ob_pts", [
    (99_999.0, 2.0, 0.0),      # Thin book (< 100k) ignores imbalance
    (100_000.0, 2.0, 0.0),     # Exactly at 100k (boundary: > 100k required)
    (100_001.0, 2.0, 3.5),     # Deep book awards imbalance points: (2 - 1) * 3.5 = 3.5
])
def test_stage_2_thin_book_depth_boundaries_parameterized(depth_usd, imbalance, expected_ob_pts):
    """Verify thin book filter boundary at exactly $100,000 USD."""
    sig = {
        'direction': 'LONG',
        'total_book_depth_usd': depth_usd,
        'imbalance_ratio': imbalance,
        'key_level_rejection': True
    }
    res = evaluate_confluence_score("BTC/USDT", sig)
    assert pytest.approx(res["eval_trace"]["ob_points"], abs=0.01) == expected_ob_pts


@pytest.mark.parametrize("vol_points, expected_friction", [
    (6.0, -3.0),     # At threshold (not > 6.0): friction is NOT halved
    (6.01, -1.5),    # Above threshold (> 6.0): smart money absorbs resistance, halved!
    (8.0, -1.5),     # Heavy volume: halved!
])
def test_stage_2_absorption_boundary_parameterized(vol_points, expected_friction):
    """Verify smart money absorption boundary at exactly 6.0 volume points."""
    # Custom signal mocking volume points directly
    ratio = 1.0 + (vol_points / 8.0)
    sig = {
        'direction': 'LONG',
        'open': 100.0,
        'close': 105.0,
        'volume': ratio * 1000.0,
        'vol_sma_20': 1000.0,
        'blocking_ob_wall': True,
        'key_level_rejection': True
    }
    res = evaluate_confluence_score("BTC/USDT", sig)
    assert pytest.approx(res["eval_trace"]["vol_points"], abs=0.05) == vol_points
    assert pytest.approx(res["eval_trace"]["friction"], abs=0.01) == expected_friction


def test_zero_and_edge_case_inputs():
    """Verify robustness against zero/empty values and extreme inputs without exceptions."""
    edge_cases = [
        {'direction': 'LONG', 'volume': 0.0, 'vol_sma_20': 0.0},
        {'direction': 'LONG', 'open': 0.0, 'close': 0.0, 'volume': 1000.0, 'vol_sma_20': 1000.0},
        {'direction': 'SHORT', 'total_book_depth_usd': 0.0, 'imbalance_ratio': 0.0},
        {'direction': 'NEUTRAL', 'entry': 0.0},
        {}
    ]
    for ec in edge_cases:
        res = evaluate_confluence_score("BTC/USDT", ec)
        assert isinstance(res, dict)
        assert "final_score" in res
        assert "eval_trace" in res
        assert 0.0 <= res["final_score"] <= 100.0

