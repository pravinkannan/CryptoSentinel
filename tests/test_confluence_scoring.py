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
    """Base score begins at 64.0 without any modifiers."""
    sig = {
        'direction': 'LONG',
        'volume': 1000.0,
        'vol_sma_20': 1000.0,  # 1.0x ratio -> 0 vol_points
        'open': 100.0,
        'close': 102.0,
        'key_level_rejection': False
    }
    res = evaluate_confluence_score("BTC/USDT", sig)
    assert res["eval_trace"]["base_score"] == 64.0
    assert res["eval_trace"]["vol_points"] == 0.0
    assert res["eval_trace"]["level_points"] == 0.0
    assert res["final_score"] == 64.0
    assert res["tier"] == "REJECTED"  # Below 86.0% threshold


def test_stage_2_dynamic_institutional_volume_formula():
    """Formula: vol_points = min(8.0, max(0.0, round(((volume / vol_sma_20) - 1.0) * 5.333, 2)))."""
    # 1.5x volume: (1.5 - 1.0) * 5.333333333333334 = 2.67 points
    sig_1_5x = {
        'direction': 'LONG',
        'open': 100.0,
        'close': 105.0,
        'volume': 1500.0,
        'vol_sma_20': 1000.0,
        'key_level_rejection': True
    }
    res_1_5x = evaluate_confluence_score("BTC/USDT", sig_1_5x)
    assert res_1_5x["eval_trace"]["vol_points"] == 2.67

    # 2.5x volume: (2.5 - 1.0) * 5.333333333333334 = 8.0 points (max limit)
    sig_2_5x = {
        'direction': 'LONG',
        'open': 100.0,
        'close': 105.0,
        'volume': 2500.0,
        'vol_sma_20': 1000.0,
        'key_level_rejection': True
    }
    res_2_5x = evaluate_confluence_score("BTC/USDT", sig_2_5x)
    assert res_2_5x["eval_trace"]["vol_points"] == 8.0

    # 3.0x volume clamped at +8.0%
    sig_3x = {
        'direction': 'LONG',
        'open': 100.0,
        'close': 105.0,
        'volume': 3000.0,
        'vol_sma_20': 1000.0,
        'key_level_rejection': True
    }
    res_3x = evaluate_confluence_score("BTC/USDT", sig_3x)
    assert res_3x["eval_trace"]["vol_points"] == 8.0

    # Sub-SMA volume (0.9x): max(0.0, (0.9 - 1.0) * 5.333) = 0.0 points
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
        'volume': 2500.0,
        'vol_sma_20': 1000.0,
        'key_level_rejection': True
    }
    res_sr = evaluate_confluence_score("BTC/USDT", sig_short_red)
    assert res_sr["eval_trace"]["vol_points"] == 8.0
    assert res_sr["eval_trace"]["breakdown"]["directional_aligned"] is True


def test_stage_2_structural_level_precision_formula_and_rejection_fix():
    """
    Structural Level Precision (0.0% to +6.0%):
    - Rejection Fix: Only award if key_level_rejection == True
    - level_points = max(0.0, 6.0 * (1.0 - (dist / 0.30))) if dist <= 0.30 else 0.0
    """
    # Exact pin (dist = 0.0): 6.0 points
    sig_exact = {
        'direction': 'LONG',
        'key_level_rejection': True,
        'dist_to_key_level_pct': 0.0
    }
    res_exact = evaluate_confluence_score("BTC/USDT", sig_exact)
    assert res_exact["eval_trace"]["level_points"] == 6.0

    # Halfway (dist = 0.15): 6.0 * (1 - 0.5) = 3.0 points
    sig_half = {
        'direction': 'LONG',
        'key_level_rejection': True,
        'dist_to_key_level_pct': 0.15
    }
    res_half = evaluate_confluence_score("BTC/USDT", sig_half)
    assert res_half["eval_trace"]["level_points"] == 3.0

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
    Order Book Depth (+0.0% to +4.5%):
    - Thin Book Fix: Only calculate imbalance points if total_book_depth_usd > 100000
    - ob_points = min(1.5, max(0.0, (imbalance_ratio - 1.0) * 1.5))
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

    # Deep Book with 2.0x imbalance: (2.0 - 1.0) * 1.5 = 1.5 points
    sig_deep = {
        'direction': 'LONG',
        'total_book_depth_usd': 200000.0,
        'imbalance_ratio': 2.0,
        'key_level_rejection': True
    }
    res_deep = evaluate_confluence_score("BTC/USDT", sig_deep)
    assert res_deep["eval_trace"]["ob_points"] == 1.5

    # Deep Book with defensive shield: 1.5 + 3.0 = 4.5 points
    sig_shield = {
        'direction': 'LONG',
        'total_book_depth_usd': 200000.0,
        'imbalance_ratio': 2.0,
        'ob_defensive_shield': True,
        'key_level_rejection': True
    }
    res_shield = evaluate_confluence_score("BTC/USDT", sig_shield)
    assert res_shield["eval_trace"]["ob_points"] == 4.5

    # Deep Book capped at 4.5%: 3.0x imbalance (capped at 1.5) + 3.0 shield = 4.5 points
    sig_max_ob = {
        'direction': 'LONG',
        'total_book_depth_usd': 200000.0,
        'imbalance_ratio': 3.0,
        'ob_defensive_shield': True,
        'key_level_rejection': True
    }
    res_max_ob = evaluate_confluence_score("BTC/USDT", sig_max_ob)
    assert res_max_ob["eval_trace"]["ob_points"] == 4.5


def test_stage_2_trend_and_derivatives():
    """
    Trend & Macro (+0.0% to +6.5%):
    - +3.0 if macro_trend_aligned == True
    - +2.5 if btc_trend_aligned == True (Altcoin)
    - +1.0 if funding_rate_tailwind == True
    """
    sig = {
        'direction': 'LONG',
        'macro_trend_aligned': True,       # +3.0
        'btc_trend_aligned': True,         # +2.5
        'funding_rate_tailwind': True,     # +1.0
        'key_level_rejection': True
    }
    res = evaluate_confluence_score("ETH/USDT", sig)
    assert res["eval_trace"]["trend_tailwinds"] == 6.5


def test_stage_2_friction_absorption_rule():
    """
    Microstructure Friction:
    - -2.0 if volume < 0.80 * SMA
    - -3.0 if blocking_ob_wall == True
    - Absorption Rule: If vol_points >= 5.0, friction penalty is cut in half (* 0.5)
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

    # Low volume drag: volume = 750 (0.75x < 0.80x) -> -2.0
    sig_drag = {
        'direction': 'LONG',
        'volume': 750.0,
        'vol_sma_20': 1000.0,
        'key_level_rejection': True
    }
    res_drag = evaluate_confluence_score("BTC/USDT", sig_drag)
    assert res_drag["eval_trace"]["friction"] == -2.0

    # Both penalties: -3.0 + -2.0 = -5.0
    sig_both = {
        'direction': 'LONG',
        'volume': 750.0,
        'vol_sma_20': 1000.0,
        'blocking_ob_wall': True,
        'key_level_rejection': True
    }
    res_both = evaluate_confluence_score("BTC/USDT", sig_both)
    assert res_both["eval_trace"]["friction"] == -5.0

    # Absorption Rule: vol_points >= 5.0 halves friction penalty
    # 2.5x volume on green candle -> vol_points = 8.0 (>= 5.0)
    sig_absorb = {
        'direction': 'LONG',
        'open': 100.0,
        'close': 105.0,
        'volume': 2500.0,
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
    """VIP Tier: Score >= 92.0% AND vol_points > 0 AND level_points > 0 AND no sweep risk AND macro aligned AND depth >= $250k."""
    sig_vip = {
        'direction': 'LONG',
        'open': 100.0,
        'close': 105.0,
        'volume': 2500.0,
        'vol_sma_20': 1000.0,          # vol_points = 8.0 (> 0)
        'key_level_rejection': True,
        'dist_to_key_level_pct': 0.05, # level_points = 6.0 * (1 - 0.05/0.30) = 5.0
        'vwap_aligned': True,          # +4.0
        'total_book_depth_usd': 300000.0, # Meets VIP >= $250k requirement
        'imbalance_ratio': 2.0,        # ob_points = 1.5
        'ob_defensive_shield': True,   # +3.0 -> ob_points = 4.5
        'macro_trend_aligned': True,   # +3.0
        'btc_trend_aligned': True,     # +2.5
        'funding_rate_tailwind': True, # +1.0
        'patterns': ['Bullish RSI Divergence'] # +3.5
    }
    # Net: 64.0 + 8.0 + 5.0 + 4.0 + 4.5 + 6.5 + 3.5 = 95.5
    res = evaluate_confluence_score("ETH/USDT", sig_vip)
    assert res["final_score"] >= 92.0
    assert res["tier"] == "VIP"
    assert res["eval_trace"]["vip_capped"] is False
    assert res["final_score"] == 95.5


def test_stage_3_vip_gate_capping_missing_volume():
    """The 5-Pillar Mandate: Score >= 92.0% but vol_points == 0 caps at 91.9% STANDARD."""
    sig_no_vol = {
        'direction': 'LONG',
        'open': 105.0,
        'close': 100.0,                # Red candle -> vol_points = 0.0!
        'volume': 2000.0,
        'vol_sma_20': 1000.0,
        'key_level_rejection': True,
        'dist_to_key_level_pct': 0.0,  # level_points = 6.0
        'vwap_aligned': True,          # +4.0
        'total_book_depth_usd': 300000.0,
        'imbalance_ratio': 2.0,
        'ob_defensive_shield': True,   # ob_points = 4.5
        'macro_trend_aligned': True,   # +3.0
        'btc_trend_aligned': True,     # +2.5
        'funding_rate_tailwind': True, # +1.0
        'patterns': ['Bullish RSI Divergence'], # +3.5
        'target_magnet_present': True, # +2.0
        'oi_delta_15m': 2.0            # +1.5 -> catalyst capped at 7.0
    }
    # Net: 64.0 + 0.0 + 6.0 + 4.0 + 4.5 + 6.5 + 7.0 = 92.0
    res = evaluate_confluence_score("ETH/USDT", sig_no_vol)
    assert res["eval_trace"]["vol_points"] == 0.0
    assert res["eval_trace"]["level_points"] == 6.0
    assert res["eval_trace"]["net_score"] == 92.0
    assert res["final_score"] == 91.9  # Capped at 91.9!
    assert res["tier"] == "STANDARD"
    assert res["eval_trace"]["vip_capped"] is True


def test_stage_3_vip_gate_capping_missing_level_rejection():
    """The 5-Pillar Mandate: Score >= 92.0% but level_points == 0 caps at 91.9% STANDARD."""
    sig_no_level = {
        'direction': 'LONG',
        'open': 100.0,
        'close': 105.0,
        'volume': 2500.0,
        'vol_sma_20': 1000.0,          # vol_points = 8.0
        'key_level_rejection': False,  # level_points = 0.0!
        'vwap_aligned': True,          # +4.0
        'total_book_depth_usd': 300000.0,
        'imbalance_ratio': 2.0,
        'ob_defensive_shield': True,   # ob_points = 4.5
        'macro_trend_aligned': True,   # +3.0
        'btc_trend_aligned': True,     # +2.5
        'funding_rate_tailwind': True, # +1.0
        'patterns': ['Bullish RSI Divergence'], # +3.5
        'target_magnet_present': True  # +2.0
    }
    # Net: 64.0 + 8.0 + 0.0 + 4.0 + 4.5 + 6.5 + 5.5 = 92.5
    res = evaluate_confluence_score("ETH/USDT", sig_no_level)
    assert res["eval_trace"]["vol_points"] == 8.0
    assert res["eval_trace"]["level_points"] == 0.0
    assert res["eval_trace"]["net_score"] == 92.5
    assert res["final_score"] == 91.9  # Capped at 91.9!
    assert res["tier"] == "STANDARD"
    assert res["eval_trace"]["vip_capped"] is True


def test_stage_3_vip_gate_capping_insufficient_book_depth():
    """VIP Gate: Book depth < $250k caps score at 91.9% STANDARD."""
    sig_thin_vip = {
        'direction': 'LONG',
        'open': 100.0,
        'close': 105.0,
        'volume': 2500.0,
        'vol_sma_20': 1000.0,          # vol_points = 8.0
        'key_level_rejection': True,
        'dist_to_key_level_pct': 0.0,  # level_points = 6.0
        'vwap_aligned': True,          # +4.0
        'total_book_depth_usd': 200000.0, # < $250k threshold!
        'imbalance_ratio': 2.0,
        'ob_defensive_shield': True,   # ob_points = 4.5
        'macro_trend_aligned': True,   # +3.0
        'btc_trend_aligned': True,     # +2.5
        'funding_rate_tailwind': True, # +1.0
    }
    # Net: 64.0 + 8.0 + 6.0 + 4.0 + 4.5 + 6.5 = 93.0 (>= 92.0)
    res = evaluate_confluence_score("ETH/USDT", sig_thin_vip)
    assert res["final_score"] == 91.9
    assert res["tier"] == "STANDARD"
    assert res["eval_trace"]["vip_capped"] is True
    assert any("Order Book Depth" in c for c in res["scoring_checklist"])


def test_stage_3_vip_gate_capping_has_sweep_risk():
    """VIP Gate: Opposing SWEEP_RISK presence caps score at 91.9% STANDARD."""
    sig_sweep = {
        'direction': 'LONG',
        'open': 100.0,
        'close': 105.0,
        'volume': 2500.0,
        'vol_sma_20': 1000.0,          # vol_points = 8.0
        'key_level_rejection': True,
        'dist_to_key_level_pct': 0.0,  # level_points = 6.0
        'vwap_aligned': True,          # +4.0
        'total_book_depth_usd': 300000.0,
        'imbalance_ratio': 2.0,
        'ob_defensive_shield': True,   # ob_points = 4.5
        'macro_trend_aligned': True,   # +3.0
        'btc_trend_aligned': True,     # +2.5
        'funding_rate_tailwind': True, # +1.0
        'patterns': ['Bullish RSI Divergence'], # +3.5
        'target_magnet_present': True, # +2.0
        'liquidity_magnets': [{'role': 'SWEEP_RISK', 'price': 99.5, 'side': 'BID'}] # -3.0 hard friction -> Net = 92.0
    }
    # Net: 64.0 + 8.0 + 6.0 + 4.0 + 4.5 + 6.5 + 5.5 - 3.0 = 95.0 - 3.0 = 92.0
    res = evaluate_confluence_score("ETH/USDT", sig_sweep)
    assert res["final_score"] == 91.9
    assert res["tier"] == "STANDARD"
    assert res["eval_trace"]["vip_capped"] is True
    assert any("No Sweep Risk" in c for c in res["scoring_checklist"])


def test_stage_3_vip_gate_capping_macro_trend_not_aligned():
    """VIP Gate: Missing macro trend alignment caps score at 91.9% STANDARD."""
    sig_not_aligned = {
        'direction': 'LONG',
        'open': 100.0,
        'close': 105.0,
        'volume': 2500.0,
        'vol_sma_20': 1000.0,          # vol_points = 8.0
        'key_level_rejection': True,
        'dist_to_key_level_pct': 0.0,  # level_points = 6.0
        'vwap_aligned': True,          # +4.0
        'total_book_depth_usd': 300000.0,
        'imbalance_ratio': 2.0,
        'ob_defensive_shield': True,   # ob_points = 4.5
        'macro_trend_aligned': False,  # Missing macro alignment! (trend_points = 3.5: btc 2.5 + funding 1.0)
        'btc_trend_aligned': True,     # +2.5
        'funding_rate_tailwind': True, # +1.0
        'patterns': ['Bullish RSI Divergence'], # +3.5
        'target_magnet_present': True  # +2.0
    }
    # Net: 64.0 + 8.0 + 6.0 + 4.0 + 4.5 + 3.5 + 5.5 = 95.5
    res = evaluate_confluence_score("ETH/USDT", sig_not_aligned)
    assert res["final_score"] == 91.9
    assert res["tier"] == "STANDARD"
    assert res["eval_trace"]["vip_capped"] is True
    assert any("Macro Alignment" in c for c in res["scoring_checklist"])



def test_stage_3_standard_tier_range():
    """Scores between 86.0% and 91.9% are classified as STANDARD tier."""
    sig_std = {
        'direction': 'LONG',
        'open': 100.0,
        'close': 105.0,
        'volume': 2500.0,
        'vol_sma_20': 1000.0,          # vol_points = 8.0
        'key_level_rejection': True,
        'dist_to_key_level_pct': 0.0,  # level_points = 6.0
        'vwap_aligned': True,          # +4.0
        'macro_trend_aligned': True,   # +3.0
        'btc_trend_aligned': True,     # +2.5
        'total_book_depth_usd': 300000.0,
        'imbalance_ratio': 1.0,        # 0.0
    }
    # Net: 64.0 + 8.0 + 6.0 + 4.0 + 5.5 = 87.5
    res = evaluate_confluence_score("ETH/USDT", sig_std)
    assert res["final_score"] == 87.5
    assert res["tier"] == "STANDARD"


def test_stage_3_rejected_tier_below_86():
    """Scores below 86.0% are classified as REJECTED."""
    sig_rej = {
        'direction': 'LONG',
        'open': 100.0,
        'close': 105.0,
        'volume': 1500.0,
        'vol_sma_20': 1000.0,          # vol_points = 2.67
        'key_level_rejection': True,
        'dist_to_key_level_pct': 0.15, # level_points = 3.0
    }
    # Net: 64.0 + 2.67 + 3.0 = 69.67 -> 69.7
    res = evaluate_confluence_score("BTC/USDT", sig_rej)
    assert res["final_score"] == 69.7
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
    assert trace["base_score"] == 64.0
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
            "strategy_analysis": "Minor regulatory headline noted.",
            "live_news_penalty": -5,
            "institutional_wall_penalty": 0,
            "audit_notes": []
        })
        sig = {
            'direction': 'LONG',
            'open': 100.0,
            'close': 105.0,
            'volume': 2500.0,
            'vol_sma_20': 1000.0,          # vol_points = 8.0
            'key_level_rejection': True,
            'dist_to_key_level_pct': 0.0,  # level_points = 6.0
            'vwap_aligned': True,          # +4.0
            'total_book_depth_usd': 300000.0,
            'imbalance_ratio': 2.0,        # ob_points = 4.5 (1.5 + 3.0 shield)
            'ob_defensive_shield': True,
            'macro_trend_aligned': True,   # +3.0
            'btc_trend_aligned': True,     # +2.5
            'funding_rate_tailwind': True, # +1.0 (trend = 6.5)
            'target_magnet_present': True, # +2.0 (catalyst = 2.0)
            'patterns': ['Engulfing']
        }

        # Parametric score: 95.0 -> live news penalty -5 -> 90.0
        res_raw = await evaluator.evaluate_signal('ETH/USDT', sig, news_data=[])
        assert res_raw is not None
        res = json.loads(res_raw)
        
        assert res["final_confidence_rating"] == 90.0
        assert res["tier"] == "STANDARD"
        assert "-5% Live News Conflict" in res["scoring_checklist"]

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
    (0.0, 6.0),
    (0.15, 3.0),
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
    (100_001.0, 2.0, 1.5),     # Deep book awards imbalance points: (2 - 1) * 1.5 = 1.5
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
    (4.9, -3.0),     # Below threshold (< 5.0): friction is NOT halved
    (5.0, -1.5),     # At threshold (>= 5.0): smart money absorbs resistance, halved!
    (8.0, -1.5),     # Heavy volume: halved!
])
def test_stage_2_absorption_boundary_parameterized(vol_points, expected_friction):
    """Verify smart money absorption boundary at exactly 5.0 volume points."""
    # Custom signal mocking volume points directly
    ratio = 1.0 + (vol_points / 5.333333333333334)
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


# ==============================================================================
# 11. MULTI-MODEL FALLBACK CASCADE & 429 RESILIENCE TESTS
# ==============================================================================

def test_ai_evaluator_fallback_cascade_on_429():
    """Verify primary model 429 RESOURCE_EXHAUSTED cascades to fallback model."""
    async def _run():
        evaluator = AIEvaluatorService(
            api_key="dummy_key", 
            model_name="gemini-3.8-flash",
            fallback_models=["gemini-3.5-flash-lite"]
        )
        
        mock_success_response = MagicMock()
        mock_success_response.text = json.dumps({
            "trade_direction": "LONG",
            "overall_verdict": "APPROVE",
            "strategy_analysis": "Fallback audit succeeded.",
            "live_news_penalty": 0,
            "institutional_wall_penalty": 0,
            "audit_notes": []
        })

        call_counts = {"count": 0}
        async def mock_generate_content(*args, **kwargs):
            call_counts["count"] += 1
            model = kwargs.get("model", "")
            if model == "gemini-3.8-flash":
                raise Exception("429 RESOURCE_EXHAUSTED. Quota exceeded for metric: generativelanguage.googleapis.com")
            return mock_success_response

        evaluator.client.aio.models.generate_content = mock_generate_content

        sig = {
            'direction': 'LONG',
            'volume': 1500.0,
            'vol_sma_20': 1000.0,
            'key_level_rejection': True
        }
        res_raw = await evaluator.evaluate_signal("BTC/USDT", sig)
        assert res_raw is not None
        data = json.loads(res_raw)
        assert data["trade_direction"] == "LONG"
        assert data["overall_verdict"] == "APPROVE"
        assert call_counts["count"] == 2

    asyncio.run(_run())


def test_ai_evaluator_deterministic_fallback_when_all_models_fail():
    """Verify that when all models fail (e.g. 429 on all), parametric score is preserved instead of dropping trade."""
    async def _run():
        evaluator = AIEvaluatorService(
            api_key="dummy_key", 
            model_name="gemini-3.8-flash",
            fallback_models=["gemini-3.5-flash-lite"]
        )

        async def mock_generate_content(*args, **kwargs):
            raise Exception("429 RESOURCE_EXHAUSTED. All quotas exhausted.")

        evaluator.client.aio.models.generate_content = mock_generate_content

        sig = {
            'direction': 'LONG',
            'volume': 1500.0,
            'vol_sma_20': 1000.0,
            'key_level_rejection': True
        }
        res_raw = await evaluator.evaluate_signal("BTC/USDT", sig)
        assert res_raw is not None
        data = json.loads(res_raw)
        assert data["trade_direction"] == "LONG"
        assert data["final_confidence_rating"] > 0.0
        assert any("Parametric Preserved" in c for c in data["scoring_checklist"])

    asyncio.run(_run())


def test_ai_autopsy_fallback_on_429():
    """Verify autopsy cascades on 429 and returns fallback autopsy if all models fail."""
    async def _run():
        evaluator = AIEvaluatorService(
            api_key="dummy_key", 
            model_name="gemini-3.8-flash",
            fallback_models=["gemini-3.5-flash-lite"]
        )

        async def mock_generate_content(*args, **kwargs):
            raise Exception("429 RESOURCE_EXHAUSTED.")

        evaluator.client.aio.models.generate_content = mock_generate_content

        trade_summary = {
            "symbol": "BTC/USDT",
            "direction": "LONG",
            "entry_price": 81000.0,
            "sl_price": 80500.0,
            "trade_id": "test-id"
        }
        sl_candle = {"open": 80600.0, "high": 80650.0, "low": 80450.0, "close": 80480.0, "volume": 120.0}

        autopsy = await evaluator.generate_autopsy(trade_summary, sl_candle)
        assert isinstance(autopsy, dict)
        assert autopsy["failure_category"] == "SL_TOO_TIGHT"

    asyncio.run(_run())


def test_stage_1_counter_trend_scalp_permitted():
    """5m/15m counter-trend scalp is permitted ONLY with key level rejection AND volume >= 2.5x SMA."""
    sig_permitted = {
        'direction': 'LONG',
        'timeframe': '5m',
        'htf_4h_trend': 'Macro Down (Bearish)',
        'open': 100.0,
        'close': 105.0,
        'volume': 2600.0,
        'vol_sma_20': 1000.0,          # volume_ratio = 2.6x (>= 2.5x)
        'key_level_rejection': True,   # Confirmed rejection
        'dist_to_key_level_pct': 0.05, # level_points = 5.0
        'vwap_aligned': True,          # +4.0
        'ob_defensive_shield': True,   # +3.0
        'patterns': ['Bullish RSI Divergence'], # +3.5
    }
    # Net: 64.0 + 8.0 + 5.0 + 4.0 + 3.0 + 3.5 = 87.5% -> STANDARD
    res = evaluate_confluence_score("BTC/USDT", sig_permitted)
    assert res["status"] != "REJECTED"
    assert res["eval_trace"]["stage1_passed"] is True


def test_stage_1_counter_trend_scalp_rejected_insufficient_volume():
    """5m counter-trend scalp with volume < 2.5x SMA is rejected by 4H macro conflict."""
    sig_low_vol = {
        'direction': 'LONG',
        'timeframe': '5m',
        'htf_4h_trend': 'Macro Down (Bearish)',
        'open': 100.0,
        'close': 105.0,
        'volume': 2000.0,
        'vol_sma_20': 1000.0,          # volume_ratio = 2.0x (< 2.5x)
        'key_level_rejection': True,
        'dist_to_key_level_pct': 0.05
    }
    res = evaluate_confluence_score("BTC/USDT", sig_low_vol)
    assert res["status"] == "REJECTED"
    assert res["eval_trace"]["stage1_passed"] is False
    assert res["eval_trace"]["circuit_breaker"] == "4H Macro Conflict"


def test_stage_1_counter_trend_scalp_rejected_no_rejection():
    """5m counter-trend scalp without key level rejection is rejected even with volume >= 2.5x SMA."""
    sig_no_rej = {
        'direction': 'LONG',
        'timeframe': '5m',
        'htf_4h_trend': 'Macro Down (Bearish)',
        'open': 100.0,
        'close': 105.0,
        'volume': 3000.0,
        'vol_sma_20': 1000.0,          # volume_ratio = 3.0x
        'key_level_rejection': False,  # Missing key level rejection!
        'dist_to_key_level_pct': 0.50
    }
    res = evaluate_confluence_score("BTC/USDT", sig_no_rej)
    assert res["status"] == "REJECTED"
    assert res["eval_trace"]["stage1_passed"] is False
    assert res["eval_trace"]["circuit_breaker"] == "4H Macro Conflict"


# ==============================================================================
# 12. POLAR BIDIRECTIONAL SCORING & FLAWLESS 100% CONFLUENCE TESTS
# ==============================================================================

def test_stage_2_session_vwap_polar_scoring():
    """Verify bidirectional VWAP: +4.0% confluence if aligned, -3.0% friction penalty if opposing."""
    # A. Bullish above VWAP -> +4.0% confluence
    sig_aligned = {
        'direction': 'LONG',
        'open': 100.0,
        'close': 105.0,
        'vwap': 100.0,
        'key_level_rejection': True,
        'dist_to_key_level_pct': 0.15
    }
    res_a = evaluate_confluence_score("ETH/USDT", sig_aligned)
    assert res_a["eval_trace"]["vwap_points"] == 4.0
    assert any("Session VWAP Acceptance" in c for c in res_a["scoring_checklist"])

    # B. Bullish below VWAP -> 0 confluence, -3.0% friction
    sig_opposed = {
        'direction': 'LONG',
        'open': 100.0,
        'close': 105.0,
        'vwap': 110.0,
        'key_level_rejection': True,
        'dist_to_key_level_pct': 0.15
    }
    res_b = evaluate_confluence_score("ETH/USDT", sig_opposed)
    assert res_b["eval_trace"]["vwap_points"] == 0.0
    assert res_b["eval_trace"]["friction"] <= -3.0
    assert any("Fighting Session VWAP" in c for c in res_b["scoring_checklist"])


def test_stage_2_btc_macro_polar_scoring():
    """Verify BTC macro beta: +2.5% tailwind for altcoins if aligned, -3.5% friction if opposing."""
    # A. ETH Long with Bullish BTC -> +2.5% tailwind
    sig_btc_aligned = {
        'direction': 'LONG',
        'key_level_rejection': True,
        'dist_to_key_level_pct': 0.15,
        'btc_trend': 'Macro Up (Bullish)'
    }
    res_a = evaluate_confluence_score("ETH/USDT", sig_btc_aligned)
    assert any("BTC Macro Beta Alignment" in c for c in res_a["scoring_checklist"])

    # B. ETH Long with Bearish BTC -> -3.5% friction deduction
    sig_btc_opposed = {
        'direction': 'LONG',
        'key_level_rejection': True,
        'dist_to_key_level_pct': 0.15,
        'btc_trend': 'Macro Down (Bearish)'
    }
    res_b = evaluate_confluence_score("ETH/USDT", sig_btc_opposed)
    assert res_b["eval_trace"]["friction"] <= -3.5
    assert any("Fighting BTC Macro Trend" in c for c in res_b["scoring_checklist"])


def test_stage_2_structural_rsi_divergence():
    """Verify +3.5% catalyst points awarded for structural RSI divergence."""
    # Bullish divergence on Long
    sig_bull_div = {
        'direction': 'LONG',
        'key_level_rejection': True,
        'patterns': ['Bullish RSI Divergence']
    }
    res_bull = evaluate_confluence_score("ETH/USDT", sig_bull_div)
    assert res_bull["eval_trace"]["catalyst_points"] >= 3.5
    assert any("Structural RSI Divergence" in c for c in res_bull["scoring_checklist"])

    # Bearish divergence on Short
    sig_bear_div = {
        'direction': 'SHORT',
        'key_level_rejection': True,
        'patterns': ['Bearish RSI Divergence']
    }
    res_bear = evaluate_confluence_score("ETH/USDT", sig_bear_div)
    assert res_bear["eval_trace"]["catalyst_points"] >= 3.5
    assert any("Structural RSI Divergence" in c for c in res_bear["scoring_checklist"])


def test_stage_2_target_liquidity_magnet():
    """Verify +2.0% catalyst points awarded when a target liquidity magnet accelerates the move."""
    sig_magnet = {
        'direction': 'LONG',
        'key_level_rejection': True,
        'target_magnet_present': True
    }
    res = evaluate_confluence_score("BTC/USDT", sig_magnet)
    assert res["eval_trace"]["catalyst_points"] >= 2.0
    assert any("Target Liquidity Magnet Pull" in c for c in res["scoring_checklist"])


def test_stage_2_flawless_100_percent_confluence():
    """
    Verify flawless 100.0% theoretical score when all 3 institutional confluence tiers align:
    Base: 64.0%
    Tier 1: Vol (+8.0%) + Level (+6.0%) + VWAP (+4.0%) = +18.0%
    Tier 2: Macro Trend (+3.0%) + BTC Beta (+2.5%) + Funding (+1.0%) + OB Shield (+3.0%) + OB Imbalance (+1.5%) = +11.0%
    Tier 3: RSI Divergence (+3.5%) + Target Magnet (+2.0%) + OI Expansion (+1.5%) = +7.0%
    Friction: 0.0%
    Total: 64.0 + 18.0 + 11.0 + 7.0 = 100.0% (VIP Tier)
    """
    sig_flawless = {
        'direction': 'LONG',
        'open': 100.0,
        'close': 105.0,
        'volume': 2500.0,
        'vol_sma_20': 1000.0,          # Tier 1: vol_points = 8.0 (2.5x SMA)
        'key_level_rejection': True,
        'dist_to_key_level_pct': 0.0,  # Tier 1: level_points = 6.0
        'vwap_aligned': True,          # Tier 1: vwap_points = 4.0
        'macro_trend_aligned': True,   # Tier 2: trend_macro = 3.0
        'btc_trend_aligned': True,     # Tier 2: btc_aligned = 2.5
        'funding_rate_tailwind': True, # Tier 2: funding_tailwind = 1.0
        'ob_defensive_shield': True,   # Tier 2: ob_shield = 3.0
        'total_book_depth_usd': 350000.0,
        'imbalance_ratio': 2.0,        # Tier 2: ob_imbalance = 1.5
        'patterns': ['Bullish RSI Divergence'], # Tier 3: rsi_div = 3.5
        'target_magnet_present': True, # Tier 3: target_magnet = 2.0
        'oi_delta_15m': 2.0,           # Tier 3: oi_expansion = 1.5
        'blocking_ob_wall': False,
        'has_sweep_risk': False
    }

    res = evaluate_confluence_score("ETH/USDT", sig_flawless)
    trace = res["eval_trace"]

    assert trace["base_score"] == 64.0
    assert trace["vol_points"] == 8.0
    assert trace["level_points"] == 6.0
    assert trace["vwap_points"] == 4.0
    assert trace["ob_points"] == 4.5
    assert trace["trend_tailwinds"] == 6.5
    assert trace["catalyst_points"] == 7.0
    assert trace["friction"] == 0.0
    assert trace["net_score"] == 100.0
    assert res["final_score"] == 100.0
    assert res["tier"] == "VIP"
    assert trace["vip_capped"] is False




