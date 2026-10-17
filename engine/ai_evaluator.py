"""
AIEvaluatorService interfaces with the Google GenAI SDK and hosts the
Two-Stage Parametric Confluence Scoring Engine for Crypto Sentinel.
"""

import logging
import json
import asyncio
from typing import Optional, Dict, List, Any
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
logging.getLogger("google_genai.models").setLevel(logging.ERROR)

# ==============================================================================
# PARAMETRIC CONFLUENCE ENGINE CONFIGURATION CONSTANTS
# ==============================================================================

# Stage 1: Hard Binary Sanity Gate (Circuit Breaker)
STAGE1_RSI_OVERBOUGHT: float = 75.0
STAGE1_RSI_OVERSOLD: float = 25.0
STAGE1_ANEMIC_VOL_RATIO: float = 0.55

# Stage 2: Parametric Continuous Scoring Constants (Calibrated to 64.0 Base / 100.0 Max)
STAGE2_BASE_SCORE: float = 64.0

# Tier 1: Foundational Order Flow & Execution Zone (Max +18.0%)
STAGE2_VOL_MAX_POINTS: float = 8.0
STAGE2_VOL_SCALING_FACTOR: float = 5.333333333333334  # reaches 8.0 at 2.5x SMA ((2.5 - 1.0) * 5.333 = 8.0)
STAGE2_LEVEL_MAX_POINTS: float = 6.0
STAGE2_LEVEL_MAX_DISTANCE_PCT: float = 0.30
STAGE2_VWAP_MAX_POINTS: float = 4.0

# Tier 2: Institutional Positioning & Macro (Max +11.0%)
STAGE2_TREND_MACRO_POINTS: float = 3.0
STAGE2_BTC_MACRO_POINTS: float = 2.5
STAGE2_TREND_FUNDING_POINTS: float = 1.0
STAGE2_MIN_BOOK_DEPTH_USD: float = 100_000.0
STAGE2_OB_DEFENSIVE_SHIELD_POINTS: float = 3.0
STAGE2_OB_MAX_IMBALANCE_POINTS: float = 1.5
STAGE2_OB_IMBALANCE_SCALING: float = 1.5  # reaches 1.5 at 2.0x ratio ((2.0 - 1.0) * 1.5)
STAGE2_OB_TOTAL_MAX_POINTS: float = 4.5
STAGE2_TREND_MAX_POINTS: float = 6.5

# Tier 3: Momentum Catalysts & Exit Gravity (Max +7.0%)
STAGE2_RSI_DIVERGENCE_POINTS: float = 3.5
STAGE2_TARGET_MAGNET_POINTS: float = 2.0
STAGE2_TREND_OI_EXPANSION_POINTS: float = 1.5
STAGE2_TREND_OI_DELTA_THRESHOLD: float = 1.5
STAGE2_TREND_RSI_ROOM_POINTS: float = 1.0
STAGE2_TREND_RSI_ROOM_MIN: float = 40.0
STAGE2_TREND_RSI_ROOM_MAX: float = 60.0
STAGE2_SQUEEZE_POINTS: float = 1.0
STAGE2_CATALYST_MAX_POINTS: float = 7.0

# Microstructure Friction Floor & Penalties (Capped at -10.0% max)
STAGE2_MAX_FRICTION_FLOOR: float = -10.0
STAGE2_FRICTION_VOL_DRAG_THRESHOLD: float = 0.80
STAGE2_FRICTION_VOL_DRAG_PENALTY: float = -2.0
STAGE2_FRICTION_WALL_PENALTY: float = -3.0
STAGE2_FRICTION_SWEEP_RISK_PENALTY: float = -3.0
STAGE2_FRICTION_VWAP_PENALTY: float = -3.0
STAGE2_FRICTION_BTC_PENALTY: float = -3.5
STAGE2_FRICTION_FUNDING_PENALTY: float = -2.5
STAGE2_FRICTION_PHASE3_PENALTY: float = -2.0
STAGE2_ABSORPTION_VOL_THRESHOLD: float = 5.0
STAGE2_ABSORPTION_MULTIPLIER: float = 0.5

# Stage 3: Tier Thresholds & VIP Gate
STAGE3_VIP_THRESHOLD: float = 92.0
STAGE3_STANDARD_THRESHOLD: float = 86.0
STAGE3_VIP_CAPPED_SCORE: float = 91.9
STAGE3_VIP_MIN_BOOK_DEPTH_USD: float = 250000.0



# ==============================================================================
# PYDANTIC RESPONSE SCHEMAS
# ==============================================================================

class SignalEvaluation(BaseModel):
    trade_direction: str = Field(description="Strictly 'LONG', 'SHORT', or 'NEUTRAL'.")
    overall_verdict: str = Field(description="Final decision resolving Macro Trend vs News Bias conflicts.")
    news_impact_bias: str = Field(description="Bullish / Bearish / Neutral")
    live_news_penalty: int = Field(description="0 if no conflict, -10 or -20 if live news directly opposes the technical setup.")
    institutional_wall_penalty: int = Field(description="-10 if a massive limit-order wall sits directly in the path of the target, else 0.")
    strategy_analysis: str = Field(description="Detailed explanation of the setup, institutional flow, and news sentiment.")
    audit_notes: List[str] = Field(description="Notes on why penalties were or were not applied.")


class AutopsyReport(BaseModel):
    failure_category: str = Field(description="Strictly one of: 'STOP_HUNT_SWEEP', 'MACRO_TREND_BREAK', 'SL_TOO_TIGHT', 'MOMENTUM_EXHAUSTION', 'UNEXPECTED_NEWS'.")
    summary: str = Field(description="2-3 sentence executive breakdown of why the setup failed.")
    key_culprit: str = Field(description="The primary microstructural or technical cause.")
    actionable_lesson: str = Field(description="Specific adjustment for future trades with this pattern.")


# ==============================================================================
# TWO-STAGE PARAMETRIC CONFLUENCE ENGINE
# ==============================================================================

def evaluate_confluence_score(symbol: str, signal_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Two-Stage Parametric Confluence Engine:
    - Stage 1: Hard Binary Sanity Gate (The Circuit Breaker)
    - Stage 2: Parametric Continuous Scoring (3-Tier Institutional Hierarchy)
    - Stage 3: The VIP Gate (Tier Classification)
    """
    direction_raw = str(signal_data.get('direction', 'NEUTRAL')).upper()
    is_long = direction_raw in ['LONG', 'BULLISH', 'BUY']
    is_short = direction_raw in ['SHORT', 'BEARISH', 'SELL']
    
    # --------------------------------------------------------------------------
    # STAGE 1: Hard Binary Sanity Gate (The Circuit Breaker)
    # --------------------------------------------------------------------------
    
    # 1. 4H Macro Conflict
    htf4h = str(signal_data.get('htf_4h_trend', ''))
    macro_4h_conflict = signal_data.get('macro_4h_conflict')
    if macro_4h_conflict is None:
        opposes_4h = (htf4h.startswith('Macro Down') and is_long) or (htf4h.startswith('Macro Up') and is_short)
        tf = str(signal_data.get('timeframe', ''))
        if opposes_4h and tf in ['5m', '15m']:
            v = float(signal_data.get('volume', 0.0))
            vs = float(signal_data.get('vol_sma_20', 0.0))
            v_ratio = float(signal_data.get('volume_ratio', (v / vs) if vs > 0 else 1.0))
            rejection = bool(signal_data.get('key_level_rejection', False))
            # Counter-trend scalps require key level rejection AND volume >= 2.5x SMA
            macro_4h_conflict = not (rejection and v_ratio >= 2.5)
        else:
            macro_4h_conflict = opposes_4h
        
    if macro_4h_conflict:
        return {
            "status": "REJECTED",
            "tier": "REJECTED",
            "final_score": 0.0,
            "net_score": 0.0,
            "rejection_reason": "Stage 1 Gate Failed: 4H Macro Conflict",
            "scoring_checklist": ["❌ Circuit Breaker: 4H Macro Conflict"],
            "eval_trace": {
                "stage1_passed": False,
                "circuit_breaker": "4H Macro Conflict",
                "base_score": STAGE2_BASE_SCORE,
                "vol_points": 0.0,
                "level_points": 0.0,
                "vwap_points": 0.0,
                "ob_points": 0.0,
                "trend_tailwinds": 0.0,
                "catalyst_points": 0.0,
                "friction": 0.0,
                "net_score": 0.0,
                "final_score": 0.0,
                "tier": "REJECTED"
            }
        }
        
    # 2. RSI Exhaustion
    rsi_14 = float(signal_data.get('rsi_14', signal_data.get('rsi_val', 50.0)))
    rsi_exhaustion = (is_long and rsi_14 > STAGE1_RSI_OVERBOUGHT) or (is_short and rsi_14 < STAGE1_RSI_OVERSOLD)
    if rsi_exhaustion:
        reason = f"Stage 1 Gate Failed: RSI Exhaustion ({rsi_14:.1f})"
        return {
            "status": "REJECTED",
            "tier": "REJECTED",
            "final_score": 0.0,
            "net_score": 0.0,
            "rejection_reason": reason,
            "scoring_checklist": [f"❌ Circuit Breaker: RSI Exhaustion ({rsi_14:.1f})"],
            "eval_trace": {
                "stage1_passed": False,
                "circuit_breaker": f"RSI Exhaustion ({rsi_14:.1f})",
                "base_score": STAGE2_BASE_SCORE,
                "vol_points": 0.0,
                "level_points": 0.0,
                "vwap_points": 0.0,
                "ob_points": 0.0,
                "trend_tailwinds": 0.0,
                "catalyst_points": 0.0,
                "friction": 0.0,
                "net_score": 0.0,
                "final_score": 0.0,
                "tier": "REJECTED"
            }
        }
        
    # 3. Anemic Volume Trap
    vol = float(signal_data.get('volume', 0.0))
    vol_sma = float(signal_data.get('vol_sma_20', 0.0))
    vol_ratio = float(signal_data.get('volume_ratio', (vol / vol_sma) if vol_sma > 0 else 1.0))
    if vol_sma > 0 and vol > 0:
        is_anemic_vol = vol < (STAGE1_ANEMIC_VOL_RATIO * vol_sma)
    else:
        is_anemic_vol = vol_ratio < STAGE1_ANEMIC_VOL_RATIO
        
    key_level_rejection = bool(signal_data.get(
        'key_level_rejection', 
        signal_data.get('dynamic_level_reaction', False) or signal_data.get('bb_bounce', False)
    ))
    
    if is_anemic_vol and not key_level_rejection:
        return {
            "status": "REJECTED",
            "tier": "REJECTED",
            "final_score": 0.0,
            "net_score": 0.0,
            "rejection_reason": "Stage 1 Gate Failed: Anemic Volume Trap with No Key Level Rejection",
            "scoring_checklist": ["❌ Circuit Breaker: Anemic Volume Trap without Key Level Rejection"],
            "eval_trace": {
                "stage1_passed": False,
                "circuit_breaker": "Anemic Volume Trap",
                "base_score": STAGE2_BASE_SCORE,
                "vol_points": 0.0,
                "level_points": 0.0,
                "vwap_points": 0.0,
                "ob_points": 0.0,
                "trend_tailwinds": 0.0,
                "catalyst_points": 0.0,
                "friction": 0.0,
                "net_score": 0.0,
                "final_score": 0.0,
                "tier": "REJECTED"
            }
        }

    # --------------------------------------------------------------------------
    # STAGE 2: Parametric Continuous Scoring
    # --------------------------------------------------------------------------
    base_score = STAGE2_BASE_SCORE
    checklist: List[str] = [f"Base Setup Score: {base_score:.1f}%"]
    
    # --------------------------------------------------------------------------
    # TIER 1: Foundational Order Flow & Execution Zone (Max +18.0%)
    # --------------------------------------------------------------------------
    # 1. Dynamic Institutional Volume (0.0% to +8.0%)
    open_p = float(signal_data.get('open', 0.0))
    close_p = float(signal_data.get('close', signal_data.get('entry', 0.0)))
    if open_p > 0 and close_p > 0:
        directional_aligned = (is_long and close_p > open_p) or (is_short and close_p < open_p)
    else:
        directional_aligned = True  # Default to True if candle open is omitted
        
    ratio = (vol / vol_sma) if (vol_sma > 0 and vol > 0) else vol_ratio
    if directional_aligned:
        vol_points = min(STAGE2_VOL_MAX_POINTS, max(0.0, round((ratio - 1.0) * STAGE2_VOL_SCALING_FACTOR, 2)))
    else:
        vol_points = 0.0
        
    if vol_points > 0:
        checklist.append(f"+{vol_points:.1f}% Dynamic Volume Surge ({ratio:.2f}x SMA)")
        
    # 2. Structural Level Precision (0.0% to +6.0%)
    dist = float(signal_data.get('dist_to_key_level_pct', 0.0 if key_level_rejection else 1.0))
    if key_level_rejection and dist <= STAGE2_LEVEL_MAX_DISTANCE_PCT:
        level_points = round(max(0.0, STAGE2_LEVEL_MAX_POINTS * (1.0 - (dist / STAGE2_LEVEL_MAX_DISTANCE_PCT))), 2)
    else:
        level_points = 0.0
        
    if level_points > 0:
        checklist.append(f"+{level_points:.1f}% Key Level Precision ({dist:.2%} distance)")

    # 3. Session VWAP Acceptance (0.0% to +4.0%)
    vwap_val = float(signal_data.get('vwap', signal_data.get('VWAP', 0.0)))
    vwap_aligned = signal_data.get('vwap_aligned')
    if vwap_aligned is None and vwap_val > 0 and close_p > 0:
        vwap_aligned = (is_long and close_p >= vwap_val) or (is_short and close_p <= vwap_val)
    elif vwap_aligned is None:
        vwap_aligned = False

    vwap_points = 0.0
    if vwap_aligned:
        vwap_points = STAGE2_VWAP_MAX_POINTS
        checklist.append(f"+{STAGE2_VWAP_MAX_POINTS:.1f}% Session VWAP Acceptance")

    # --------------------------------------------------------------------------
    # TIER 2: Institutional Positioning & Macro (Max +11.0%)
    # --------------------------------------------------------------------------
    # 4. Multi-Timeframe Trend & Derivatives (Max +6.5%)
    trend_tailwinds = 0.0
    htf = str(signal_data.get('htf_trend', ''))
    macro_trend_aligned = bool(signal_data.get('macro_trend_aligned', (
        (htf.startswith('Macro Up') and htf4h.startswith('Macro Up') and is_long) or
        (htf.startswith('Macro Down') and htf4h.startswith('Macro Down') and is_short)
    )))
    if macro_trend_aligned:
        trend_tailwinds += STAGE2_TREND_MACRO_POINTS
        checklist.append(f"+{STAGE2_TREND_MACRO_POINTS:.1f}% Multi-Timeframe Macro Trend Alignment")

    # BTC Macro Beta Alignment (+2.5%)
    btc_trend = str(signal_data.get('btc_trend', ''))
    symbol_upper = str(symbol).upper()
    is_btc = "BTC" in symbol_upper

    btc_aligned = signal_data.get('btc_trend_aligned')
    btc_opposes = False
    if btc_aligned is None and btc_trend:
        if is_btc:
            btc_aligned = True
        else:
            btc_aligned = (is_long and 'Up' in btc_trend) or (is_short and 'Down' in btc_trend)
            btc_opposes = (is_long and 'Down' in btc_trend) or (is_short and 'Up' in btc_trend)
    elif btc_aligned is False:
        btc_opposes = True

    if not is_btc and btc_aligned:
        trend_tailwinds += STAGE2_BTC_MACRO_POINTS
        checklist.append(f"+{STAGE2_BTC_MACRO_POINTS:.1f}% BTC Macro Beta Alignment")

    # Funding Rate Tailwind (+1.0%)
    fr = float(signal_data.get('funding_rate', 0.0))
    funding_rate_tailwind = bool(signal_data.get('funding_rate_tailwind', (
        (is_long and fr <= -0.005) or (is_short and fr >= 0.005)
    )))
    if funding_rate_tailwind:
        trend_tailwinds += STAGE2_TREND_FUNDING_POINTS
        checklist.append(f"+{STAGE2_TREND_FUNDING_POINTS:.1f}% Funding Rate Tailwind")

    trend_tailwinds = min(STAGE2_TREND_MAX_POINTS, trend_tailwinds)

    # 5. Order Book Depth & Shield (Max +4.5%)
    book_depth = float(signal_data.get('total_book_depth_usd', 150000.0))
    imbalance_ratio = float(signal_data.get('imbalance_ratio', 1.0))
    if book_depth > STAGE2_MIN_BOOK_DEPTH_USD:
        ob_points = min(STAGE2_OB_MAX_IMBALANCE_POINTS, max(0.0, round((imbalance_ratio - 1.0) * STAGE2_OB_IMBALANCE_SCALING, 2)))
    else:
        ob_points = 0.0

    magnets = signal_data.get('liquidity_magnets', [])
    ob_defensive_shield = bool(signal_data.get(
        'ob_defensive_shield', 
        any(m.get('role') == 'DEFENSIVE_SHIELD' for m in magnets)
    ))
    if ob_defensive_shield:
        ob_points += STAGE2_OB_DEFENSIVE_SHIELD_POINTS
        checklist.append(f"+{STAGE2_OB_DEFENSIVE_SHIELD_POINTS:.1f}% Order Book Defensive Shield")
    ob_points = min(STAGE2_OB_TOTAL_MAX_POINTS, ob_points)

    shield_pts = STAGE2_OB_DEFENSIVE_SHIELD_POINTS if ob_defensive_shield else 0.0
    if ob_points > shield_pts:
        imbalance_pts = ob_points - shield_pts
        checklist.append(f"+{imbalance_pts:.1f}% Order Book Imbalance ({imbalance_ratio:.2f}x)")

    # --------------------------------------------------------------------------
    # TIER 3: Momentum Catalysts & Exit Gravity (Max +7.0%)
    # --------------------------------------------------------------------------
    catalyst_points = 0.0
    patterns = signal_data.get('patterns', [])
    rsi_div = signal_data.get('rsi_divergence')
    if rsi_div is None:
        if is_long:
            rsi_div = any('Bullish RSI Divergence' in str(p) or 'Bullish Divergence' in str(p) for p in patterns)
        elif is_short:
            rsi_div = any('Bearish RSI Divergence' in str(p) or 'Bearish Divergence' in str(p) for p in patterns)
        else:
            rsi_div = False

    if rsi_div:
        catalyst_points += STAGE2_RSI_DIVERGENCE_POINTS
        checklist.append(f"+{STAGE2_RSI_DIVERGENCE_POINTS:.1f}% Structural RSI Divergence")

    target_magnet = signal_data.get('target_magnet_present')
    if target_magnet is None:
        target_magnet = any(m.get('role') in ['TARGET_ACCELERATOR', 'MAGNET', 'ACCELERATOR'] for m in magnets)

    if target_magnet:
        catalyst_points += STAGE2_TARGET_MAGNET_POINTS
        checklist.append(f"+{STAGE2_TARGET_MAGNET_POINTS:.1f}% Target Liquidity Magnet Pull")

    oi_delta = float(signal_data.get('oi_delta_15m', signal_data.get('oi_delta_pct', 0.0)))
    if oi_delta > STAGE2_TREND_OI_DELTA_THRESHOLD:
        catalyst_points += STAGE2_TREND_OI_EXPANSION_POINTS
        checklist.append(f"+{STAGE2_TREND_OI_EXPANSION_POINTS:.1f}% Open Interest Expansion")

    if 'rsi_momentum_room' in signal_data:
        rsi_room = bool(signal_data['rsi_momentum_room'])
    elif 'rsi_14' in signal_data or 'rsi_val' in signal_data:
        rsi_room = (STAGE2_TREND_RSI_ROOM_MIN <= rsi_14 <= STAGE2_TREND_RSI_ROOM_MAX)
    else:
        rsi_room = False

    if rsi_room and not rsi_div:
        catalyst_points += STAGE2_TREND_RSI_ROOM_POINTS
        checklist.append(f"+{STAGE2_TREND_RSI_ROOM_POINTS:.1f}% RSI Momentum Trajectory Room")

    is_squeezed = bool(signal_data.get('is_squeezed', any('Squeeze' in str(p) for p in patterns)))
    if is_squeezed and directional_aligned and vol_points > 0:
        catalyst_points += STAGE2_SQUEEZE_POINTS
        checklist.append(f"+{STAGE2_SQUEEZE_POINTS:.1f}% Volatility Squeeze Expansion")

    catalyst_points = min(STAGE2_CATALYST_MAX_POINTS, catalyst_points)

    # --------------------------------------------------------------------------
    # MICROSTRUCTURE FRICTION & PENALTIES (Capped at -10.0% max)
    # --------------------------------------------------------------------------
    hard_friction = 0.0
    soft_friction = 0.0

    # Hard physical barrier: Blocking order book wall
    blocking_ob_wall = bool(signal_data.get(
        'blocking_ob_wall', 
        any(m.get('role') in ['BLOCKING_RESISTANCE', 'BLOCKING_SUPPORT'] for m in magnets)
    ))
    if blocking_ob_wall:
        hard_friction += STAGE2_FRICTION_WALL_PENALTY
        checklist.append(f"{STAGE2_FRICTION_WALL_PENALTY:.1f}% Blocking Order Book Wall")

    # Hard risk: Opposing liquidity sweep risk pool
    has_sweep_risk = bool(signal_data.get(
        'has_sweep_risk',
        any(m.get('role') == 'SWEEP_RISK' for m in magnets)
    ))
    if has_sweep_risk:
        hard_friction += STAGE2_FRICTION_SWEEP_RISK_PENALTY
        checklist.append(f"{STAGE2_FRICTION_SWEEP_RISK_PENALTY:.1f}% Order Book Sweep Risk Friction")

    # Hard macro conflict: Fighting BTC Macro trend (Altcoin only)
    if not is_btc and btc_opposes:
        hard_friction += STAGE2_FRICTION_BTC_PENALTY
        checklist.append(f"{STAGE2_FRICTION_BTC_PENALTY:.1f}% Fighting BTC Macro Trend")

    # Soft friction: Low volume drag (< 0.80x SMA)
    if (vol_sma > 0 and vol > 0 and vol < STAGE2_FRICTION_VOL_DRAG_THRESHOLD * vol_sma) or (vol_sma == 0 and ratio < STAGE2_FRICTION_VOL_DRAG_THRESHOLD):
        soft_friction += STAGE2_FRICTION_VOL_DRAG_PENALTY
        checklist.append(f"{STAGE2_FRICTION_VOL_DRAG_PENALTY:.1f}% Low Volume Drag (<0.80x SMA)")

    # Soft friction: Fighting Session VWAP
    if not vwap_aligned and vwap_val > 0 and close_p > 0:
        soft_friction += STAGE2_FRICTION_VWAP_PENALTY
        checklist.append(f"{STAGE2_FRICTION_VWAP_PENALTY:.1f}% Fighting Session VWAP")

    # Soft friction: Overcrowded funding trap
    overcrowded_funding = (is_long and fr > 0.03) or (is_short and fr < -0.03)
    if overcrowded_funding:
        soft_friction += STAGE2_FRICTION_FUNDING_PENALTY
        checklist.append(f"{STAGE2_FRICTION_FUNDING_PENALTY:.1f}% Overcrowded Funding Squeeze Trap ({fr:.4f}%)")

    # Soft friction: Phase 3 Momentum Exhaustion Trap
    phase = str(signal_data.get('phase', '')).upper()
    if phase in ['PHASE 3', 'PHASE_3', 'EXHAUSTION']:
        soft_friction += STAGE2_FRICTION_PHASE3_PENALTY
        checklist.append(f"{STAGE2_FRICTION_PHASE3_PENALTY:.1f}% Phase 3 Momentum Exhaustion Trap")

    # Smart Money Absorption: If institutional volume is strong (vol_points >= 5.0), halve friction
    raw_friction = hard_friction + soft_friction
    friction_absorbed = False
    if vol_points >= STAGE2_ABSORPTION_VOL_THRESHOLD and raw_friction < 0.0:
        raw_friction *= STAGE2_ABSORPTION_MULTIPLIER
        friction_absorbed = True
        checklist.append("⚡ Smart Money Absorption: Strong volume halved friction penalty")

    friction = max(STAGE2_MAX_FRICTION_FLOOR, raw_friction)

    # Net Score Calculation
    net_score = base_score + vol_points + level_points + vwap_points + ob_points + trend_tailwinds + catalyst_points + friction
    final_score = round(max(0.0, min(100.0, net_score)), 1)
    
    # --------------------------------------------------------------------------
    # STAGE 3: The VIP Gate (Tier Classification)
    # --------------------------------------------------------------------------
    vip_capped = False
    if final_score >= STAGE3_VIP_THRESHOLD:
        missing = []
        if vol_points <= 0.0:
            missing.append("Volume Surge")
        if level_points <= 0.0:
            missing.append("Key Level Rejection")
        if has_sweep_risk:
            missing.append("No Sweep Risk (Opposing pool detected)")
        if not macro_trend_aligned:
            missing.append("Multi-Timeframe Macro Alignment")
        if book_depth < STAGE3_VIP_MIN_BOOK_DEPTH_USD:
            missing.append(f"Order Book Depth (>= ${STAGE3_VIP_MIN_BOOK_DEPTH_USD/1000:.0f}k)")

        if not missing:
            tier = "VIP"
        else:
            final_score = STAGE3_VIP_CAPPED_SCORE
            tier = "STANDARD"
            vip_capped = True
            checklist.append(f"[VIP Gate] Capped at {STAGE3_VIP_CAPPED_SCORE:.1f}% (Missing {' & '.join(missing)})")
    elif final_score >= STAGE3_STANDARD_THRESHOLD:
        tier = "STANDARD"
    else:
        tier = "REJECTED"
        
    eval_trace = {
        "stage1_passed": True,
        "base_score": base_score,
        "vol_points": round(vol_points, 2),
        "level_points": round(level_points, 2),
        "vwap_points": round(vwap_points, 2),
        "ob_points": round(ob_points, 2),
        "trend_tailwinds": round(trend_tailwinds, 2),
        "catalyst_points": round(catalyst_points, 2),
        "friction": round(friction, 2),
        "net_score": round(net_score, 2),
        "final_score": final_score,
        "tier": tier,
        "vip_capped": vip_capped,
        "has_sweep_risk": has_sweep_risk,
        "macro_trend_aligned": macro_trend_aligned,
        "book_depth": round(book_depth, 2),
        "breakdown": {
            "volume_ratio": round(ratio, 2),
            "directional_aligned": directional_aligned,
            "key_level_rejection": key_level_rejection,
            "dist_to_key_level_pct": round(dist, 4),
            "vwap_aligned": vwap_aligned,
            "imbalance_ratio": round(imbalance_ratio, 2),
            "total_book_depth_usd": round(book_depth, 2),
            "ob_defensive_shield": ob_defensive_shield,
            "macro_trend_aligned": macro_trend_aligned,
            "btc_aligned": btc_aligned,
            "funding_rate_tailwind": funding_rate_tailwind,
            "oi_delta_15m": round(oi_delta, 2),
            "rsi_14": round(rsi_14, 2),
            "rsi_momentum_room": rsi_room,
            "rsi_divergence": rsi_div,
            "target_magnet": target_magnet,
            "blocking_ob_wall": blocking_ob_wall,
            "has_sweep_risk": has_sweep_risk,
            "friction_absorbed": friction_absorbed
        }
    }

    
    return {
        "status": tier,
        "tier": tier,
        "final_score": final_score,
        "net_score": round(net_score, 2),
        "rejection_reason": "" if tier != "REJECTED" else f"Score {final_score:.1f}% below {STAGE3_STANDARD_THRESHOLD:.1f}% threshold",
        "scoring_checklist": checklist,
        "eval_trace": eval_trace
    }


# ==============================================================================
# AI EVALUATOR SERVICE
# ==============================================================================

class AIEvaluatorService:
    """Evaluates trading signals with institutional confluence scoring and Gemini auditing."""

    def __init__(
        self, 
        api_key: str, 
        model_name: str = 'gemini-3.5-flash-lite', 
        timeout: float = 8.0,
        fallback_models: Optional[List[str]] = None
    ) -> None:
        self.api_key: str = api_key
        self.model_name: str = model_name
        self.timeout: float = timeout
        self.fallback_models: List[str] = fallback_models if fallback_models is not None else [
            'gemini-flash-lite-latest',
            'gemini-3.1-flash-lite'
        ]
        self.client = genai.Client(api_key=self.api_key)

    def evaluate_confluence_score(self, symbol: str, signal_data: Dict[str, Any]) -> Dict[str, Any]:
        """Bound instance helper for evaluate_confluence_score."""
        return evaluate_confluence_score(symbol, signal_data)

    async def evaluate_signal(
        self, 
        symbol: str, 
        signal_data: Dict[str, Any], 
        news_data: Optional[List[Dict[str, Any]]] = None, 
        regime_data: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """
        Executes the Two-Stage Parametric Confluence Engine and overlays qualitative
        LLM analysis (live news & institutional limit-order wall auditing).
        """
        news_data = news_data or []
        direction_raw = str(signal_data.get('direction', 'NEUTRAL')).upper()
        is_long = direction_raw in ['LONG', 'BULLISH', 'BUY']
        is_short = direction_raw in ['SHORT', 'BEARISH', 'SELL']
        direction = 'LONG' if is_long else 'SHORT' if is_short else 'NEUTRAL'

        # 1. Deterministic Two-Stage Parametric Confluence Engine
        score_result = self.evaluate_confluence_score(symbol, signal_data)
        
        # If Circuit Breaker triggered in Stage 1, fail immediately without LLM call
        if not score_result["eval_trace"].get("stage1_passed", True):
            output = {
                "trade_direction": direction,
                "overall_verdict": "REJECT",
                "strategy_analysis": f"Circuit breaker tripped in Stage 1: {score_result['rejection_reason']}",
                "final_confidence_rating": 0.0,
                "tier": "REJECTED",
                "scoring_checklist": score_result["scoring_checklist"],
                "eval_trace": score_result["eval_trace"],
                "is_degraded": False
            }
            return json.dumps(output)

        patterns = signal_data.get('patterns', [])
        magnets = signal_data.get('liquidity_magnets', [])
        magnet_desc = "; ".join([
            f"{m.get('volume_formatted', '')} {m.get('side', '')} @ ${m.get('price', 0)} ({m.get('action', 'No action specified')})" 
            for m in magnets
        ]) if magnets else "None detected"
        
        # Ask AI for Qualitative News and Wall Auditing
        prompt = f"""
You are an Institutional Risk Auditor.
Your job is to audit the qualitative aspects of this trade setup.

Asset: {symbol}
Current Price: {signal_data.get('close', 0.0)}
Technical Direction: {direction}
Detected Patterns: {", ".join(patterns)}
Microstructure Note: {signal_data.get('strategy_rationale', 'N/A')}
Order Book Liquidity Magnets: {magnet_desc}

Recent News:
"""
        for i, article in enumerate(news_data, 1):
            prompt += f"{i}. {article['title']}\n   {article['summary']}\n"
            
        prompt += """
Audit Requirements:
1. live_news_penalty: Apply a -10 or -20 penalty if the live news directly opposes the technical setup (e.g. bearish news for a LONG setup). Otherwise 0.
2. institutional_wall_penalty: Apply a -10 penalty ONLY if there is a massive institutional limit-order wall blocking the path to the Take Profit target. Based on the Microstructure Note, judge if this is the case. Otherwise 0.
3. overall_verdict: Return 'APPROVE' if penalties don't exceed -15, else 'REJECT'.

Provide JSON output adhering exactly to the schema.
"""

        models_to_try = [self.model_name] + [m for m in self.fallback_models if m != self.model_name]
        ai_data = None

        for current_model in models_to_try:
            try:
                response = await asyncio.wait_for(
                    self.client.aio.models.generate_content(
                        model=current_model,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            response_schema=SignalEvaluation,
                        ),
                    ),
                    timeout=self.timeout
                )
                raw_text = response.text
                if not raw_text:
                    logger.warning(f"Empty AI response text received from {current_model} for {symbol}.")
                    continue

                text = raw_text.strip()
                if text.startswith("```json"): text = text[7:]
                if text.endswith("```"): text = text[:-3]
                
                ai_data = json.loads(text.strip())
                ai_data['eval_model'] = current_model
                break
            except asyncio.TimeoutError:
                logger.warning(f"AI evaluation timed out for model {current_model}.")
            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    logger.warning(f"Gemini quota/rate limit exhausted for model {current_model} (429). Attempting fallback...")
                elif "503" in err_str or "UNAVAILABLE" in err_str:
                    logger.warning(f"Gemini model {current_model} temporarily unavailable (503). Attempting fallback...")
                else:
                    logger.warning(f"AI evaluation failed for model {current_model}: {e}")

        # If an AI response was successfully obtained:
        if ai_data is not None:
            # Apply Qualitative AI adjustments to parametric score
            live_news_penalty = int(ai_data.get('live_news_penalty', 0))
            wall_penalty = int(ai_data.get('institutional_wall_penalty', 0))
            
            adjusted_score = score_result["final_score"]
            checklist = list(score_result["scoring_checklist"])
            
            if live_news_penalty < 0:
                adjusted_score += live_news_penalty
                checklist.append(f"{live_news_penalty}% Live News Conflict")
            if wall_penalty < 0 and not score_result["eval_trace"]["breakdown"].get("blocking_ob_wall", False):
                adjusted_score += wall_penalty
                checklist.append(f"{wall_penalty}% Institutional Wall Blocking")
                
            final_score = round(max(0.0, min(100.0, adjusted_score)), 1)
            
            # Tier reassessment post-AI review
            if final_score >= STAGE3_VIP_THRESHOLD and score_result["tier"] == "VIP":
                tier = "VIP"
            elif final_score >= STAGE3_STANDARD_THRESHOLD:
                tier = "STANDARD"
            else:
                tier = "REJECTED"
                
            eval_trace = dict(score_result["eval_trace"])
            eval_trace["final_score"] = final_score
            eval_trace["tier"] = tier
            eval_trace["qualitative_penalties"] = {
                "live_news_penalty": live_news_penalty,
                "wall_penalty": wall_penalty
            }

            output = {
                "trade_direction": ai_data.get('trade_direction', direction),
                "overall_verdict": ai_data.get('overall_verdict', 'APPROVE'),
                "strategy_analysis": ai_data.get('strategy_analysis', ''),
                "final_confidence_rating": final_score,
                "tier": tier,
                "scoring_checklist": checklist,
                "eval_trace": eval_trace,
                "is_degraded": (signal_data.get('pattern_drift_status') == 'DEGRADED')
            }
            return json.dumps(output)

        # Fallback: All AI models failed (Quota exhausted, service unavailable, or offline)
        # Preserve quantitative parametric confluence score so valid setups aren't dropped!
        logger.info(f"Preserving quantitative parametric confluence for {symbol} (AI auditing offline/exhausted).")
        checklist = list(score_result["scoring_checklist"])
        checklist.append("⚠️ Parametric Preserved: AI audit bypassed (quota/offline)")
        
        output = {
            "trade_direction": direction,
            "overall_verdict": "APPROVE" if score_result["tier"] in ["VIP", "STANDARD"] else "REJECT",
            "strategy_analysis": (
                f"Quantitative Parametric Confluence score: {score_result['final_score']}%. "
                f"Tier: {score_result['tier']}. Evaluated via deterministic parametric confluence engine."
            ),
            "final_confidence_rating": score_result["final_score"],
            "tier": score_result["tier"],
            "scoring_checklist": checklist,
            "eval_trace": score_result["eval_trace"],
            "is_degraded": (signal_data.get('pattern_drift_status') == 'DEGRADED')
        }
        return json.dumps(output)

    async def generate_autopsy(
        self, 
        trade_summary: Dict[str, Any], 
        sl_candle: Dict[str, Any], 
        microstructure_at_exit: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Send stopped-out trade metrics to Gemini for post-mortem forensic analysis."""
        micro = microstructure_at_exit or {}
        prompt = f"""
You are an Institutional Lead Quantitative Risk Officer conducting a Post-Trade Autopsy on a stopped-out trade.

### Trade Setup (Original Entry Thesis)
- Symbol: {trade_summary.get('symbol')}
- Direction: {trade_summary.get('direction')}
- Entry Price: ${trade_summary.get('entry_price')}
- Initial Stop Loss: ${trade_summary.get('sl_price')}
- Pattern Traded: {trade_summary.get('pattern_name', 'Unknown')}
- Leverage: {trade_summary.get('leverage', 1.0)}x

### Exit Snapshot (The Exact Candle That Triggered the Stop Loss)
- Exit Price: ${sl_candle.get('close')}
- Breach Candle Range: Low: ${sl_candle.get('low')} – High: ${sl_candle.get('high')}
- Exit Candle Open: ${sl_candle.get('open')} | Volume: {sl_candle.get('volume')}
- Funding Rate at Exit: {micro.get('funding_rate', 0.0)}%
- Order Book Imbalance at Exit: {micro.get('imbalance_ratio', 1.0)}
- Realized Net Loss: {trade_summary.get('net_pnl_pct', 0.0):.2f}%

Conduct a forensic analysis to determine:
1. Did price wick through the stop loss and reverse (classic liquidity sweep), or break through with heavy continuation?
2. Was the stop loss placed within high-frequency market noise?
3. What is the single biggest lesson to protect capital on similar setups?

Provide JSON output adhering strictly to the schema.
"""
        models_to_try = [self.model_name] + [m for m in self.fallback_models if m != self.model_name]
        for current_model in models_to_try:
            try:
                response = await asyncio.wait_for(
                    self.client.aio.models.generate_content(
                        model=current_model,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            response_schema=AutopsyReport,
                        ),
                    ),
                    timeout=self.timeout
                )
                raw = response.text or ""
                text = raw.strip()
                if text.startswith("```json"): text = text[7:]
                if text.endswith("```"): text = text[:-3]
                res = json.loads(text.strip())
                if isinstance(res, dict) and "failure_category" in res:
                    return dict(res)
            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    logger.warning(f"Gemini quota exhausted for autopsy with {current_model}. Attempting fallback...")
                else:
                    logger.warning(f"AI autopsy failed with {current_model}: {e}")

        logger.info(f"Generating deterministic autopsy for trade {trade_summary.get('trade_id', 'unknown')}.")
        return {
            "failure_category": "SL_TOO_TIGHT",
            "summary": "Trade stopped out due to adverse market volatility. Stop loss boundary breached before target.",
            "key_culprit": "Price breached stop loss boundary before reaching profit target.",
            "actionable_lesson": "Consider widening ATR buffer multiplier in higher volatility regimes."
        }

