import pytest
import asyncio
import pandas as pd
import json
from unittest.mock import AsyncMock, MagicMock

from engine.signal_generator import SignalGeneratorService
from engine.ai_evaluator import AIEvaluatorService


def create_mock_ai_evaluator(ai_payload=None):
    """Helper to instantiate AIEvaluatorService with mocked Gemini response."""
    evaluator = AIEvaluatorService(api_key="dummy_key")
    default_payload = {
        "trade_direction": "LONG",
        "overall_verdict": "APPROVE",
        "strategy_analysis": "Automated test audit.",
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
# 1. HTF TREND CALCULATION TESTS (Positive, Negative, Fallback)
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
# 2. ISOLATED CONFLUENCE RULES (Positive and Negative Testing)
# ==============================================================================

def test_rule_macro_trend_alignment():
    """Rule 1: Macro Trend Alignment (+5%) for Long and Short."""
    async def run():
        ai = create_mock_ai_evaluator()

        # POSITIVE LONG: 1H & 4H Macro Up
        sig_long = {
            'direction': 'Bullish',
            'htf_trend': 'Macro Uptrend (Bullish)',
            'htf_4h_trend': 'Macro Uptrend (Bullish)',
            'patterns': ['Engulfing']
        }
        raw_l = await ai.evaluate_signal('BTC/USDT', sig_long, news_data=[])
        assert raw_l is not None
        res_l = json.loads(raw_l)
        assert "+5% Macro Trend Alignment" in res_l['scoring_checklist']
        assert res_l['final_confidence_rating'] == 80  # 75 base + 5

        # POSITIVE SHORT: 1H & 4H Macro Down
        sig_short = {
            'direction': 'Bearish',
            'htf_trend': 'Macro Downtrend (Bearish)',
            'htf_4h_trend': 'Macro Downtrend (Bearish)',
            'patterns': ['Engulfing']
        }
        raw_s = await ai.evaluate_signal('BTC/USDT', sig_short, news_data=[])
        assert raw_s is not None
        res_s = json.loads(raw_s)
        assert "+5% Macro Trend Alignment" in res_s['scoring_checklist']
        assert res_s['final_confidence_rating'] == 80

        # NEGATIVE: 1H Up but 4H Down (Mixed)
        sig_mixed = {
            'direction': 'Bullish',
            'htf_trend': 'Macro Uptrend (Bullish)',
            'htf_4h_trend': 'Macro Downtrend (Bearish)',
            'patterns': ['Engulfing']
        }
        raw_m = await ai.evaluate_signal('BTC/USDT', sig_mixed, news_data=[])
        assert raw_m is not None
        res_m = json.loads(raw_m)
        assert "+5% Macro Trend Alignment" not in res_m['scoring_checklist']

    asyncio.run(run())


def test_rule_btc_global_trend_support():
    """Rule 2: BTC Global Trend Support (+5%)."""
    async def run():
        ai = create_mock_ai_evaluator()

        # POSITIVE LONG
        sig_long = {'direction': 'Bullish', 'btc_trend': 'Macro Uptrend (Bullish)', 'patterns': ['Hammer']}
        res_l = json.loads(await ai.evaluate_signal('ETH/USDT', sig_long, news_data=[]))
        assert "+5% BTC Global Trend Support" in res_l['scoring_checklist']

        # POSITIVE SHORT
        sig_short = {'direction': 'Bearish', 'btc_trend': 'Macro Downtrend (Bearish)', 'patterns': ['Hammer']}
        res_s = json.loads(await ai.evaluate_signal('ETH/USDT', sig_short, news_data=[]))
        assert "+5% BTC Global Trend Support" in res_s['scoring_checklist']

        # NEGATIVE: BTC opposes direction
        sig_neg = {'direction': 'Bullish', 'btc_trend': 'Macro Downtrend (Bearish)', 'patterns': ['Hammer']}
        res_n = json.loads(await ai.evaluate_signal('ETH/USDT', sig_neg, news_data=[]))
        assert "+5% BTC Global Trend Support" not in res_n['scoring_checklist']

    asyncio.run(run())


def test_rule_order_book_dominance():
    """Rule 3: Order Book Dominance (+5%) (Imbalance >= 1.5 for Long, <= 0.66 for Short)."""
    async def run():
        ai = create_mock_ai_evaluator()

        # POSITIVE LONG (imbalance = 1.5 boundary)
        sig_l1 = {'direction': 'Bullish', 'imbalance_ratio': 1.5, 'patterns': ['Hammer']}
        res_l1 = json.loads(await ai.evaluate_signal('BTC/USDT', sig_l1, news_data=[]))
        assert "+5% Order Book Dominance" in res_l1['scoring_checklist']

        # POSITIVE SHORT (imbalance = 0.66 boundary)
        sig_s1 = {'direction': 'Bearish', 'imbalance_ratio': 0.66, 'patterns': ['Hammer']}
        res_s1 = json.loads(await ai.evaluate_signal('BTC/USDT', sig_s1, news_data=[]))
        assert "+5% Order Book Dominance" in res_s1['scoring_checklist']

        # NEGATIVE LONG (imbalance = 1.49, below 1.5)
        sig_l2 = {'direction': 'Bullish', 'imbalance_ratio': 1.49, 'patterns': ['Hammer']}
        res_l2 = json.loads(await ai.evaluate_signal('BTC/USDT', sig_l2, news_data=[]))
        assert "+5% Order Book Dominance" not in res_l2['scoring_checklist']

        # NEGATIVE SHORT (imbalance = 0.67, above 0.66)
        sig_s2 = {'direction': 'Bearish', 'imbalance_ratio': 0.67, 'patterns': ['Hammer']}
        res_s2 = json.loads(await ai.evaluate_signal('BTC/USDT', sig_s2, news_data=[]))
        assert "+5% Order Book Dominance" not in res_s2['scoring_checklist']

    asyncio.run(run())


def test_rule_exceptional_risk_reward():
    """Rule 4: Exceptional Risk/Reward (+5%) (RR >= 2.5)."""
    async def run():
        ai = create_mock_ai_evaluator()

        # POSITIVE (RR = 2.5 boundary)
        sig_pos = {'direction': 'Bullish', 'rr_ratio': 2.5, 'patterns': ['Hammer']}
        res_p = json.loads(await ai.evaluate_signal('BTC/USDT', sig_pos, news_data=[]))
        assert "+5% Exceptional Risk/Reward" in res_p['scoring_checklist']

        # NEGATIVE (RR = 2.49)
        sig_neg = {'direction': 'Bullish', 'rr_ratio': 2.49, 'patterns': ['Hammer']}
        res_n = json.loads(await ai.evaluate_signal('BTC/USDT', sig_neg, news_data=[]))
        assert "+5% Exceptional Risk/Reward" not in res_n['scoring_checklist']

    asyncio.run(run())


def test_rule_funding_rate_tailwind():
    """Rule 5: Funding Rate Tailwind (+5%) (Negative funding for Long, Positive for Short)."""
    async def run():
        ai = create_mock_ai_evaluator()

        # POSITIVE LONG (fr = -0.01%)
        sig_l = {'direction': 'Bullish', 'funding_rate': -0.01, 'patterns': ['Hammer']}
        res_l = json.loads(await ai.evaluate_signal('BTC/USDT', sig_l, news_data=[]))
        assert "+5% Funding Rate Tailwind" in res_l['scoring_checklist']

        # POSITIVE SHORT (fr = +0.02%)
        sig_s = {'direction': 'Bearish', 'funding_rate': 0.02, 'patterns': ['Hammer']}
        res_s = json.loads(await ai.evaluate_signal('BTC/USDT', sig_s, news_data=[]))
        assert "+5% Funding Rate Tailwind" in res_s['scoring_checklist']

        # NEGATIVE LONG (fr = 0.00% or positive)
        sig_n = {'direction': 'Bullish', 'funding_rate': 0.01, 'patterns': ['Hammer']}
        res_n = json.loads(await ai.evaluate_signal('BTC/USDT', sig_n, news_data=[]))
        assert "+5% Funding Rate Tailwind" not in res_n['scoring_checklist']

    asyncio.run(run())


def test_rule_multiple_pattern_confluence():
    """Rule 6: Multiple Pattern Confluence (+5%) (len(patterns) > 1)."""
    async def run():
        ai = create_mock_ai_evaluator()

        # POSITIVE (2 patterns)
        sig_p = {'direction': 'Bullish', 'patterns': ['Bullish Engulfing', 'Bullish Belthold']}
        res_p = json.loads(await ai.evaluate_signal('BTC/USDT', sig_p, news_data=[]))
        assert "+5% Multiple Pattern Confluence" in res_p['scoring_checklist']

        # NEGATIVE (1 pattern)
        sig_1 = {'direction': 'Bullish', 'patterns': ['Bullish Engulfing']}
        res_1 = json.loads(await ai.evaluate_signal('BTC/USDT', sig_1, news_data=[]))
        assert "+5% Multiple Pattern Confluence" not in res_1['scoring_checklist']

        # NEGATIVE (0 patterns)
        sig_0 = {'direction': 'Bullish', 'patterns': []}
        res_0 = json.loads(await ai.evaluate_signal('BTC/USDT', sig_0, news_data=[]))
        assert "+5% Multiple Pattern Confluence" not in res_0['scoring_checklist']

    asyncio.run(run())


def test_rule_magnetic_liquidity_pools():
    """Rule 7: Magnetic Liquidity Pools (+5%)."""
    async def run():
        ai = create_mock_ai_evaluator()

        # POSITIVE LONG (Short liquidation pool above entry)
        sig_l = {'direction': 'Bullish', 'short_liq_zones': '84000.0, 84500.0', 'patterns': ['Hammer']}
        res_l = json.loads(await ai.evaluate_signal('BTC/USDT', sig_l, news_data=[]))
        assert "+5% Magnetic Liquidity Pools" in res_l['scoring_checklist']

        # POSITIVE SHORT (Long liquidation pool below entry)
        sig_s = {'direction': 'Bearish', 'long_liq_zones': '80000.0, 79500.0', 'patterns': ['Hammer']}
        res_s = json.loads(await ai.evaluate_signal('BTC/USDT', sig_s, news_data=[]))
        assert "+5% Magnetic Liquidity Pools" in res_s['scoring_checklist']

        # NEGATIVE (Empty or None)
        sig_n = {'direction': 'Bullish', 'short_liq_zones': 'None', 'patterns': ['Hammer']}
        res_n = json.loads(await ai.evaluate_signal('BTC/USDT', sig_n, news_data=[]))
        assert "+5% Magnetic Liquidity Pools" not in res_n['scoring_checklist']

    asyncio.run(run())


def test_rule_order_book_liquidity_magnets():
    """Rule 8: Order Book Liquidity Magnet (+5%)."""
    async def run():
        ai = create_mock_ai_evaluator()

        # POSITIVE LONG: Ask pool above current close
        sig_l = {
            'direction': 'Bullish',
            'close': 82000.0,
            'liquidity_magnets': [{'side': 'ASK', 'price': 83500.0}],
            'patterns': ['Hammer']
        }
        res_l = json.loads(await ai.evaluate_signal('BTC/USDT', sig_l, news_data=[]))
        assert "+5% Order Book Liquidity Magnet" in res_l['scoring_checklist']

        # POSITIVE SHORT: Bid pool below current close
        sig_s = {
            'direction': 'Bearish',
            'close': 82000.0,
            'liquidity_magnets': [{'side': 'BID', 'price': 80500.0}],
            'patterns': ['Hammer']
        }
        res_s = json.loads(await ai.evaluate_signal('BTC/USDT', sig_s, news_data=[]))
        assert "+5% Order Book Liquidity Magnet" in res_s['scoring_checklist']

        # NEGATIVE LONG: Bid pool below current close (Defensive shield, not target pull)
        sig_neg = {
            'direction': 'Bullish',
            'close': 82000.0,
            'liquidity_magnets': [{'side': 'BID', 'price': 81500.0}],
            'patterns': ['Hammer']
        }
        res_neg = json.loads(await ai.evaluate_signal('BTC/USDT', sig_neg, news_data=[]))
        assert "+5% Order Book Liquidity Magnet" not in res_neg['scoring_checklist']

    asyncio.run(run())


def test_rule_open_interest_expansion():
    """Rule 9: Open Interest Expansion (+5%) (oi_delta > 1.5% and Expanding)."""
    async def run():
        ai = create_mock_ai_evaluator()

        # POSITIVE (delta > 1.5 and expanding)
        sig_p = {'direction': 'Bullish', 'oi_delta_pct': 1.8, 'oi_direction': 'Expanding', 'patterns': ['Hammer']}
        res_p = json.loads(await ai.evaluate_signal('BTC/USDT', sig_p, news_data=[]))
        assert "+5% Open Interest Expansion" in res_p['scoring_checklist']

        # NEGATIVE (delta <= 1.5)
        sig_n1 = {'direction': 'Bullish', 'oi_delta_pct': 1.4, 'oi_direction': 'Expanding', 'patterns': ['Hammer']}
        res_n1 = json.loads(await ai.evaluate_signal('BTC/USDT', sig_n1, news_data=[]))
        assert "+5% Open Interest Expansion" not in res_n1['scoring_checklist']

        # NEGATIVE (delta > 1.5 but Declining)
        sig_n2 = {'direction': 'Bullish', 'oi_delta_pct': 2.5, 'oi_direction': 'Declining', 'patterns': ['Hammer']}
        res_n2 = json.loads(await ai.evaluate_signal('BTC/USDT', sig_n2, news_data=[]))
        assert "+5% Open Interest Expansion" not in res_n2['scoring_checklist']

    asyncio.run(run())


def test_rule_phase_2_momentum_volume_expansion():
    """Rule 10: Phase 2 Momentum Volume Expansion (+10%)."""
    async def run():
        ai = create_mock_ai_evaluator()

        # POSITIVE: Regime is HIGH_VOLATILITY and Phase is PHASE 2
        sig = {'direction': 'Bullish', 'patterns': ['Marubozu']}
        regime_p2 = {'regime': 'HIGH_VOLATILITY', 'phase': 'PHASE 2'}
        res = json.loads(await ai.evaluate_signal('BTC/USDT', sig, news_data=[], regime_data=regime_p2))
        assert "+10% PHASE 2 Momentum Volume Expansion" in res['scoring_checklist']
        assert res['final_confidence_rating'] == 85  # 75 base + 10

        # NEGATIVE: Normal regime
        regime_norm = {'regime': 'NORMAL', 'phase': 'NORMAL'}
        res_norm = json.loads(await ai.evaluate_signal('BTC/USDT', sig, news_data=[], regime_data=regime_norm))
        assert "+10% PHASE 2 Momentum Volume Expansion" not in res_norm['scoring_checklist']

    asyncio.run(run())


# ==============================================================================
# 3. DEDUCTIONS & PENALTIES TESTING (Positive and Negative)
# ==============================================================================

def test_deduction_macro_trend_conflict():
    """Deduction: -15% Macro Trend Conflict for counter-trend trades."""
    async def run():
        ai = create_mock_ai_evaluator()

        # POSITIVE TRIGGER: Shorting into a 4H Macro Uptrend
        sig_short_conflict = {
            'direction': 'Bearish',
            'htf_4h_trend': 'Macro Uptrend (Bullish)',
            'patterns': ['Engulfing']
        }
        res_sc = json.loads(await ai.evaluate_signal('BTC/USDT', sig_short_conflict, news_data=[]))
        assert "-15% Macro Trend Conflict" in res_sc['scoring_checklist']
        # 75 base - 15 deduction = 60%
        assert res_sc['final_confidence_rating'] == 60

        # POSITIVE TRIGGER: Longing into a 4H Macro Downtrend
        sig_long_conflict = {
            'direction': 'Bullish',
            'htf_4h_trend': 'Macro Downtrend (Bearish)',
            'patterns': ['Engulfing']
        }
        res_lc = json.loads(await ai.evaluate_signal('BTC/USDT', sig_long_conflict, news_data=[]))
        assert "-15% Macro Trend Conflict" in res_lc['scoring_checklist']
        assert res_lc['final_confidence_rating'] == 60

        # NEGATIVE (NO CONFLICT): Longing in a 4H Macro Uptrend
        sig_aligned = {
            'direction': 'Bullish',
            'htf_4h_trend': 'Macro Uptrend (Bullish)',
            'patterns': ['Engulfing']
        }
        res_al = json.loads(await ai.evaluate_signal('BTC/USDT', sig_aligned, news_data=[]))
        assert "-15% Macro Trend Conflict" not in res_al['scoring_checklist']

    asyncio.run(run())


def test_deduction_degraded_pattern():
    """Deduction: -10% Degraded Pattern for decayed win rate (<40%)."""
    async def run():
        ai = create_mock_ai_evaluator()

        # POSITIVE: Pattern is DEGRADED
        sig_deg = {
            'direction': 'Bullish',
            'pattern_drift_status': 'DEGRADED',
            'patterns': ['Hammer']
        }
        res_d = json.loads(await ai.evaluate_signal('BTC/USDT', sig_deg, news_data=[]))
        assert "-10% Degraded Pattern" in res_d['scoring_checklist']
        assert res_d['is_degraded'] is True
        # 75 base - 10 deduction = 65%
        assert res_d['final_confidence_rating'] == 65

        # NEGATIVE: Pattern is HEALTHY
        sig_h = {
            'direction': 'Bullish',
            'pattern_drift_status': 'HEALTHY',
            'patterns': ['Hammer']
        }
        res_h = json.loads(await ai.evaluate_signal('BTC/USDT', sig_h, news_data=[]))
        assert "-10% Degraded Pattern" not in res_h['scoring_checklist']
        assert res_h['is_degraded'] is False

    asyncio.run(run())


def test_deductions_from_ai_audit():
    """Verify live news penalty and institutional wall penalty from AI response."""
    async def run():
        ai = create_mock_ai_evaluator({
            "trade_direction": "LONG",
            "overall_verdict": "APPROVE",
            "strategy_analysis": "News conflict detected.",
            "live_news_penalty": -20,
            "institutional_wall_penalty": -10,
            "audit_notes": ["Regulatory crackdown", "Massive ask wall"]
        })

        sig = {'direction': 'Bullish', 'patterns': ['Hammer']}
        res = json.loads(await ai.evaluate_signal('BTC/USDT', sig, news_data=[]))
        checklist = res['scoring_checklist']
        assert "-20% Live News Conflict" in checklist
        assert "-10% Institutional Wall Blocking" in checklist
        # 75 base - 20 - 10 = 45%
        assert res['final_confidence_rating'] == 45

    asyncio.run(run())


# ==============================================================================
# 4. EDGE CASES & CLAMPING TESTS
# ==============================================================================

def test_edge_cases_direction_casing():
    """Edge Case: Ensure all variations of direction string are handled seamlessly."""
    async def run():
        ai = create_mock_ai_evaluator()

        variations = ['bullish', 'BULLISH', 'Bullish', 'long', 'LONG', 'Long', 'buy', 'BUY']
        for v in variations:
            sig = {
                'direction': v,
                'htf_trend': 'Macro Uptrend (Bullish)',
                'htf_4h_trend': 'Macro Uptrend (Bullish)',
                'patterns': ['Hammer']
            }
            res = json.loads(await ai.evaluate_signal('BTC/USDT', sig, news_data=[]))
            assert "+5% Macro Trend Alignment" in res['scoring_checklist'], f"Failed for variation '{v}'"
            assert res['trade_direction'] == 'LONG'

        short_variations = ['bearish', 'BEARISH', 'Bearish', 'short', 'SHORT', 'Short', 'sell', 'SELL']
        for sv in short_variations:
            sig_s = {
                'direction': sv,
                'htf_trend': 'Macro Downtrend (Bearish)',
                'htf_4h_trend': 'Macro Downtrend (Bearish)',
                'patterns': ['Hammer']
            }
            res_s = json.loads(await ai.evaluate_signal('BTC/USDT', sig_s, news_data=[]))
            assert "+5% Macro Trend Alignment" in res_s['scoring_checklist'], f"Failed for short variation '{sv}'"

    asyncio.run(run())


def test_edge_cases_score_bounds_and_clamping():
    """Edge Case: Verify score clamping to [0, 100] and stackable bonus cap to 25%."""
    async def run():
        # Case A: Capped at 100% when all 8 confluences trigger (+40% potential capped to +25%)
        ai_max = create_mock_ai_evaluator()
        sig_max = {
            'direction': 'Bullish',
            'close': 82000.0,
            'patterns': ['Pattern1', 'Pattern2'],  # +5%
            'htf_trend': 'Macro Uptrend (Bullish)',
            'htf_4h_trend': 'Macro Uptrend (Bullish)',  # +5%
            'btc_trend': 'Macro Uptrend (Bullish)',  # +5%
            'imbalance_ratio': 2.0,  # +5%
            'rr_ratio': 3.0,  # +5%
            'funding_rate': -0.02,  # +5%
            'short_liq_zones': '83000',  # +5%
            'oi_delta_pct': 2.5,
            'oi_direction': 'Expanding',  # +5%
            'liquidity_magnets': [{'side': 'ASK', 'price': 84000.0}]
        }
        res_max = json.loads(await ai_max.evaluate_signal('BTC/USDT', sig_max, news_data=[]))
        assert res_max['final_confidence_rating'] == 100  # Strictly clamped at 100

        # Case B: Clamped at 0% when severe penalties exceed 75%
        ai_min = create_mock_ai_evaluator({
            "trade_direction": "SHORT",
            "overall_verdict": "REJECT",
            "strategy_analysis": "Severe conflict.",
            "live_news_penalty": -50,
            "institutional_wall_penalty": -50,
            "audit_notes": []
        })
        sig_min = {
            'direction': 'Bearish',
            'htf_4h_trend': 'Macro Uptrend (Bullish)',  # -15%
            'pattern_drift_status': 'DEGRADED',  # -10%
            'patterns': []
        }
        # 75 base - 15 - 10 - 50 - 50 = -50% -> clamped to 0%
        res_min = json.loads(await ai_min.evaluate_signal('BTC/USDT', sig_min, news_data=[]))
        assert res_min['final_confidence_rating'] == 0

    asyncio.run(run())


def test_edge_cases_missing_and_none_fields():
    """Edge Case: Ensure missing or None fields do not cause uncaught exceptions."""
    async def run():
        ai = create_mock_ai_evaluator()
        empty_sig = {'patterns': []}

        res_raw = await ai.evaluate_signal('BTC/USDT', empty_sig, news_data=[])
        assert res_raw is not None
        res = json.loads(res_raw)
        assert res['final_confidence_rating'] == 75  # Base score intact
        assert res['scoring_checklist'] == []

    asyncio.run(run())
