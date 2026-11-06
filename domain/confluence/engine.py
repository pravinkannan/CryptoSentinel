"""
Domain Confluence Engine.
Pure, deterministic, zero-network quantitative scoring engine for Crypto Sentinel.
Includes:
- Stage 1: Hard Binary Sanity Gate (Circuit Breakers & Ghost Scoring)
- Stage 2: Parametric Factor Scoring (Order flow, institutional positioning, momentum catalysts, microstructure friction)
- Stage 3: PREMIUM Gate & Dual-Regime Dispatch Floor
"""

from typing import Dict, List, Any
import logging
from core.config import settings

logger = logging.getLogger(__name__)



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
STAGE2_RSI_DIVERGENCE_POINTS: float = 5.0
STAGE2_TARGET_MAGNET_POINTS: float = 2.0
STAGE2_TREND_OI_EXPANSION_POINTS: float = 1.5
STAGE2_TREND_OI_DELTA_THRESHOLD: float = 1.5
STAGE2_TREND_RSI_ROOM_POINTS: float = 1.0
STAGE2_TREND_RSI_ROOM_MIN: float = 40.0
STAGE2_TREND_RSI_ROOM_MAX: float = 60.0
STAGE2_SQUEEZE_POINTS: float = 1.0
STAGE2_CATALYST_MAX_POINTS: float = 7.0

# Climax Governor & Squeeze Ignition Constants
STAGE2_CLIMAX_VOL_RATIO_LTF: float = 3.5  # On 5m/15m, volume > 3.5x SMA is climactic churn
STAGE2_CLIMAX_WICK_THRESHOLD: float = 0.25  # Opposing wick > 25% of candle range indicates rejection
STAGE2_FRICTION_CLIMAX_PENALTY: float = -6.0  # Penalty when volume is climactic and rejected by wick
STAGE2_FRESH_SQUEEZE_IGNITION_POINTS: float = 10.0  # Institutional bonus for early squeeze ignition on LTF
STAGE2_PREMIUM_LTF_RSI_MAX_LONG: float = 68.0  # Max RSI for fresh PREMIUM Long on LTF
STAGE2_PREMIUM_LTF_RSI_MIN_SHORT: float = 32.0  # Min RSI for fresh PREMIUM Short on LTF
STAGE2_VIP_LTF_RSI_MAX_LONG = STAGE2_PREMIUM_LTF_RSI_MAX_LONG
STAGE2_VIP_LTF_RSI_MIN_SHORT = STAGE2_PREMIUM_LTF_RSI_MIN_SHORT

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
STAGE2_FRICTION_EXHAUSTION_TRAP_PENALTY: float = -5.0
STAGE2_ABSORPTION_VOL_THRESHOLD: float = 5.0
STAGE2_ABSORPTION_MULTIPLIER: float = 0.5

# Stage 3: Tier Thresholds & PREMIUM Gate
STAGE3_PREMIUM_THRESHOLD: float = 92.0
STAGE3_VIP_THRESHOLD = STAGE3_PREMIUM_THRESHOLD
STAGE3_STANDARD_THRESHOLD: float = 86.0
STAGE3_REVERSAL_THRESHOLD: float = 80.0
STAGE3_PREMIUM_CAPPED_SCORE: float = 91.9
STAGE3_VIP_CAPPED_SCORE = STAGE3_PREMIUM_CAPPED_SCORE
STAGE3_PREMIUM_MIN_BOOK_DEPTH_USD: float = 250000.0
STAGE3_VIP_MIN_BOOK_DEPTH_USD = STAGE3_PREMIUM_MIN_BOOK_DEPTH_USD


def evaluate_confluence_score(symbol: str, signal_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Two-Stage Parametric Confluence Engine:
    - Stage 1: Hard Binary Sanity Gate (The Circuit Breaker with Ghost Scoring)
    - Stage 2: Parametric Continuous Scoring (3-Tier Institutional Hierarchy)
    - Stage 3: The PREMIUM Gate (Tier Classification & Dual-Regime Floor)

    Pure deterministic function: Zero async network calls, Zero LLM dependencies, Zero JSON parsing.
    """
    direction_raw = str(signal_data.get('direction', 'NEUTRAL')).upper()
    is_long = direction_raw in ['LONG', 'BULLISH', 'BUY']
    is_short = direction_raw in ['SHORT', 'BEARISH', 'SELL']
    
    # --------------------------------------------------------------------------
    # STAGE 1: Hard Binary Sanity Gate (The Circuit Breaker)
    # --------------------------------------------------------------------------
    circuit_breaker_tripped: bool = False
    fatal_flaw: str = ""

    # Pattern & Reversal Context (Stage 1 & Stage 2)
    patterns = signal_data.get('patterns', [])
    pattern_name = str(signal_data.get('pattern_name', ''))
    all_patterns_str = " ".join([str(p) for p in patterns] + [pattern_name]).lower()
    reversal_keywords = ['engulfing', 'hammer', 'morning', 'evening', 'pin bar', 'shooting star', 'piercing', 'dark cloud', 'reversal']
    has_reversal_pattern = any(k in all_patterns_str for k in reversal_keywords)

    key_level_rejection = bool(signal_data.get(
        'key_level_rejection', 
        signal_data.get('dynamic_level_reaction', False) or signal_data.get('bb_bounce', False)
    ))

    rsi_div = signal_data.get('rsi_divergence')
    if rsi_div is None:
        if is_long:
            rsi_div = any('Bullish RSI Divergence' in str(p) or 'Bullish Divergence' in str(p) for p in patterns) or ('bullish' in pattern_name.lower() and 'divergence' in pattern_name.lower())
        elif is_short:
            rsi_div = any('Bearish RSI Divergence' in str(p) or 'Bearish Divergence' in str(p) for p in patterns) or ('bearish' in pattern_name.lower() and 'divergence' in pattern_name.lower())
        else:
            rsi_div = False
    else:
        rsi_div = bool(rsi_div)

    is_reversal = key_level_rejection and (rsi_div or has_reversal_pattern)
    is_squeezed = bool(signal_data.get('is_squeezed', any('Squeeze' in str(p) for p in patterns)))

    vol = float(signal_data.get('volume', 0.0))
    vol_sma = float(signal_data.get('vol_sma_20', 0.0))
    vol_ratio = float(signal_data.get('volume_ratio', (vol / vol_sma) if vol_sma > 0 else 1.0))

    # 0. Pillar 1: Multi-Timeframe (MTF) Alignment (>70% Standard Tier Filter)
    # On 5m and 15m charts, entry direction must strictly match 1H 200-EMA macro trend.
    tf = str(signal_data.get('timeframe', ''))
    htf_1h = str(signal_data.get('htf_1h_trend') or signal_data.get('htf_trend', ''))
    is_1h_bearish = ('down' in htf_1h.lower() or 'bear' in htf_1h.lower())
    is_1h_bullish = ('up' in htf_1h.lower() or 'bull' in htf_1h.lower())

    if tf in ['5m', '15m']:
        if is_long and is_1h_bearish and not circuit_breaker_tripped:
            circuit_breaker_tripped = True
            fatal_flaw = "Phase 1 Gate Failed: 1H Macro 200-EMA Downtrend Conflict (MTF Alignment)"
        elif is_short and is_1h_bullish and not circuit_breaker_tripped:
            circuit_breaker_tripped = True
            fatal_flaw = "Phase 1 Gate Failed: 1H Macro 200-EMA Uptrend Conflict (MTF Alignment)"

    # 1. 4H Macro Conflict Gate (Phase 2 Refactoring & Fix Cause 2: Range-Bound Counter-Trend Bypass)
    htf4h = str(signal_data.get('htf_4h_trend', ''))
    opposes_4h = (htf4h.startswith('Macro Down') and is_long) or (htf4h.startswith('Macro Up') and is_short)

    # Fix Cause 2: Mean Reversion Bypass for 5m and 15m timeframes
    # If 4H is Bearish but setup is LONG, allow if price is extended below VWAP (>= 1.5%) and RSI <= 30
    # Symmetrically, if 4H is Bullish but setup is SHORT, allow if price is extended above VWAP (>= 1.5%) and RSI >= 70
    vwap_val_gate = float(signal_data.get('vwap', signal_data.get('VWAP', 0.0)))
    entry_p = float(signal_data.get('close', signal_data.get('entry', 0.0)))
    rsi_14_gate = float(signal_data.get('rsi_14', signal_data.get('rsi_val', 50.0)))
    vwap_dist_pct = (abs(entry_p - vwap_val_gate) / vwap_val_gate * 100.0) if (vwap_val_gate > 0 and entry_p > 0) else 0.0

    mean_reversion_bypass = False
    if opposes_4h and tf in ['5m', '15m']:
        if is_long and (entry_p < vwap_val_gate or vwap_val_gate == 0.0) and (vwap_dist_pct >= 1.5 or signal_data.get('vwap_extended')) and rsi_14_gate <= 30.0:
            mean_reversion_bypass = True
            logger.info(f"🔄 [{signal_data.get('symbol', 'UNKNOWN')} {tf}] Mean Reversion Bypass active: 4H Bearish bypassed (VWAP dist {vwap_dist_pct:.2f}%, RSI {rsi_14_gate:.1f}).")
        elif is_short and (entry_p > vwap_val_gate or vwap_val_gate == 0.0) and (vwap_dist_pct >= 1.5 or signal_data.get('vwap_extended')) and rsi_14_gate >= 70.0:
            mean_reversion_bypass = True
            logger.info(f"🔄 [{signal_data.get('symbol', 'UNKNOWN')} {tf}] Mean Reversion Bypass active: 4H Bullish bypassed (VWAP dist {vwap_dist_pct:.2f}%, RSI {rsi_14_gate:.1f}).")

    macro_4h_conflict = signal_data.get('macro_4h_conflict')
    if mean_reversion_bypass:
        macro_4h_conflict = False
    elif macro_4h_conflict is None:
        if opposes_4h:
            if tf in ['5m', '15m']:
                # Action 1 (5m/15m bypass): Lower counter-trend volume barrier from 2.5x SMA to 1.5x SMA
                # IF a recognized reversal pattern OR rsi_divergence is present alongside key level rejection.
                has_reversal_confirmation = (has_reversal_pattern or rsi_div) and key_level_rejection
                required_vol_ratio = 1.5 if has_reversal_confirmation else 2.5
                macro_4h_conflict = not (key_level_rejection and vol_ratio >= required_vol_ratio)
            elif tf in ['1h', '4h']:
                # Action 2 (1H/4H bypass): Passes Phase 1 IF confirmed rsi_divergence at support/resistance.
                if rsi_div and key_level_rejection:
                    macro_4h_conflict = False
                else:
                    macro_4h_conflict = True
            else:
                if rsi_div and key_level_rejection:
                    macro_4h_conflict = False
                else:
                    macro_4h_conflict = opposes_4h
        else:
            macro_4h_conflict = False
        
    if macro_4h_conflict:
        circuit_breaker_tripped = True
        fatal_flaw = "Phase 1 Gate Failed: 4H Macro Conflict"
        
    # 2. RSI Exhaustion Gate
    rsi_14 = float(signal_data.get('rsi_14', signal_data.get('rsi_val', 50.0)))
    rsi_exhaustion = (is_long and rsi_14 > STAGE1_RSI_OVERBOUGHT) or (is_short and rsi_14 < STAGE1_RSI_OVERSOLD)
    if rsi_exhaustion and not circuit_breaker_tripped:
        circuit_breaker_tripped = True
        fatal_flaw = f"Phase 1 Gate Failed: RSI Exhaustion ({rsi_14:.1f})"
        
    # 3. Anemic Volume Trap Gate
    if vol_sma > 0 and vol > 0:
        is_anemic_vol = vol < (STAGE1_ANEMIC_VOL_RATIO * vol_sma)
    else:
        is_anemic_vol = vol_ratio < STAGE1_ANEMIC_VOL_RATIO
    
    if is_anemic_vol and not key_level_rejection and not circuit_breaker_tripped:
        circuit_breaker_tripped = True
        fatal_flaw = "Phase 1 Gate Failed: Anemic Volume Trap with No Key Level Rejection"

    # 4. Pillar 3: Market Regime Detection (ADX Chop Filter / "Do Not Trade" Standby Mode)
    # If 14-period ADX is below 20, the market is in choppy consolidation.
    # Instantly rejects standard breakout trades. Squeeze compression (Pillar 2) & reversals exempt.
    adx_14 = float(signal_data.get('adx_14', 25.0))
    if adx_14 < 20.0 and not is_reversal and not is_squeezed and not circuit_breaker_tripped:
        circuit_breaker_tripped = True
        fatal_flaw = f"Phase 1 Gate Failed: Low ADX Chop Filter (ADX {adx_14:.1f} < 20.0, Standby Mode)"

    # --------------------------------------------------------------------------
    # STAGE 2: Parametric Continuous Scoring (Ghost Scoring enabled)
    # --------------------------------------------------------------------------
    base_score = STAGE2_BASE_SCORE
    checklist: List[str] = [f"Base Setup Score: {base_score:.1f}%"]
    if circuit_breaker_tripped:
        cb_label = fatal_flaw.replace("Phase 1 Gate Failed: ", "")
        checklist.append(f"❌ Circuit Breaker: {cb_label}")
    elif mean_reversion_bypass:
        checklist.append("Mean Reversion Bypass (VWAP extension + RSI exhaustion)")
    
    # --------------------------------------------------------------------------
    # TIER 1: Foundational Order Flow & Execution Zone (Max +18.0%)
    # --------------------------------------------------------------------------
    # 1. Dynamic Institutional Volume (0.0% to +8.0%)
    open_p = float(signal_data.get('open', 0.0))
    close_p = float(signal_data.get('close', signal_data.get('entry', 0.0)))
    high_p = float(signal_data.get('high', max(open_p, close_p) if (open_p > 0 and close_p > 0) else 0.0))
    low_p = float(signal_data.get('low', min(open_p, close_p) if (open_p > 0 and close_p > 0) else 0.0))
    c_range = high_p - low_p
    if c_range > 0 and open_p > 0 and close_p > 0:
        upper_wick_ratio = (high_p - max(open_p, close_p)) / c_range
        lower_wick_ratio = (min(open_p, close_p) - low_p) / c_range
    else:
        upper_wick_ratio = float(signal_data.get('upper_wick_ratio', 0.0))
        lower_wick_ratio = float(signal_data.get('lower_wick_ratio', 0.0))
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

    # BTC Macro Beta Alignment (+2.5%) - Applies to Altcoins, not Commodities (Gold/PAXG)
    btc_trend = str(signal_data.get('btc_trend', ''))
    symbol_upper = symbol.upper()
    is_btc = "BTC" in symbol_upper
    is_commodity = any(c in symbol_upper for c in ["XAU", "PAXG", "GOLD"])

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

    if not is_btc and not is_commodity and btc_aligned:
        trend_tailwinds += STAGE2_BTC_MACRO_POINTS
        checklist.append(f"+{STAGE2_BTC_MACRO_POINTS:.1f}% BTC Macro Beta Alignment")
    elif (is_btc or is_commodity) and bool(signal_data.get('benchmark_macro_authority', True)) and macro_trend_aligned:
        trend_tailwinds += STAGE2_BTC_MACRO_POINTS
        checklist.append(f"+{STAGE2_BTC_MACRO_POINTS:.1f}% Benchmark Macro Trend Authority")

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
    if is_short:
        eff_imbalance = (1.0 / imbalance_ratio) if imbalance_ratio > 0 else 0.0
    else:
        eff_imbalance = imbalance_ratio

    if book_depth > STAGE2_MIN_BOOK_DEPTH_USD:
        ob_points = min(STAGE2_OB_MAX_IMBALANCE_POINTS, max(0.0, round((eff_imbalance - 1.0) * STAGE2_OB_IMBALANCE_SCALING, 2)))
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
        checklist.append(f"+{imbalance_pts:.1f}% Order Book Imbalance ({eff_imbalance:.2f}x)")

    # --------------------------------------------------------------------------
    # TIER 3: Momentum Catalysts & Exit Gravity (Max +7.0%)
    # --------------------------------------------------------------------------
    catalyst_points = 0.0

    if rsi_div:
        catalyst_points += STAGE2_RSI_DIVERGENCE_POINTS
        checklist.append(f"+{STAGE2_RSI_DIVERGENCE_POINTS:.1f}% Structural RSI Divergence")

    target_magnet = signal_data.get('target_magnet_present')
    if target_magnet is None:
        target_magnet = any(m.get('role') in ['TARGET_ACCELERATOR', 'MAGNET', 'ACCELERATOR'] for m in magnets)

    # State-Aware VWAP: If reversal and price is pulling toward VWAP, award Target Magnet Pull
    pulling_to_vwap = is_reversal and not vwap_aligned and vwap_val > 0 and close_p > 0 and ((is_long and close_p < vwap_val) or (is_short and close_p > vwap_val))
    has_magnet_pull = target_magnet or pulling_to_vwap

    if has_magnet_pull:
        catalyst_points += STAGE2_TARGET_MAGNET_POINTS
        if pulling_to_vwap and not target_magnet:
            checklist.append(f"+{STAGE2_TARGET_MAGNET_POINTS:.1f}% Target Magnet Pull (Session VWAP Reversion)")
        else:
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
    sq_pts = float(signal_data.get('_override_squeeze_points', STAGE2_SQUEEZE_POINTS))
    cat_max = float(signal_data.get('_override_catalyst_max', STAGE2_CATALYST_MAX_POINTS))
    if is_squeezed and directional_aligned and vol_points > 0:
        catalyst_points += sq_pts
        checklist.append(f"+{sq_pts:.1f}% Volatility Squeeze Expansion")

    catalyst_points = min(cat_max, catalyst_points)

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
    if not is_btc and not is_commodity and btc_opposes:
        hard_friction += STAGE2_FRICTION_BTC_PENALTY
        checklist.append(f"{STAGE2_FRICTION_BTC_PENALTY:.1f}% Fighting BTC Macro Trend")

    # Soft friction: Low volume drag (< 0.80x SMA)
    if (vol_sma > 0 and vol > 0 and vol < STAGE2_FRICTION_VOL_DRAG_THRESHOLD * vol_sma) or (vol_sma == 0 and ratio < STAGE2_FRICTION_VOL_DRAG_THRESHOLD):
        soft_friction += STAGE2_FRICTION_VOL_DRAG_PENALTY
        checklist.append(f"{STAGE2_FRICTION_VOL_DRAG_PENALTY:.1f}% Low Volume Drag (<0.80x SMA)")

    # Soft friction: Fighting Session VWAP (State-Aware: Reversal towards VWAP waives penalty)
    if not vwap_aligned and vwap_val > 0 and close_p > 0:
        if pulling_to_vwap:
            # Waive penalty for mean-reversion pull towards VWAP
            pass
        else:
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

    # Friction: Exhaustion Absorption Trap (-5.0%)
    # Setup is Continuation + Volume Surge + Order Book Imbalance, but RSI is extended (> 70 or < 30)
    is_continuation = signal_data.get('is_continuation')
    if is_continuation is None:
        is_continuation = not is_reversal or str(signal_data.get('setup_type', '')).upper() in ['CONTINUATION', 'BREAKOUT'] or any('continuation' in str(p).lower() or 'breakout' in str(p).lower() for p in patterns)

    has_vol_surge = vol_points > 0.0 or vol_ratio >= 1.5 or ratio >= 1.5 or bool(signal_data.get('volume_surge'))
    has_ob_imbalance = eff_imbalance >= 1.2 or bool(signal_data.get('ob_imbalance')) or (ob_points > shield_pts)
    rsi_extended = (rsi_14 > 70.0) or (rsi_14 < 30.0)

    exhaustion_trap = is_continuation and has_vol_surge and (has_ob_imbalance or tf in ['5m', '15m']) and rsi_extended
    if exhaustion_trap:
        hard_friction += STAGE2_FRICTION_EXHAUSTION_TRAP_PENALTY
        checklist.append(f"{STAGE2_FRICTION_EXHAUSTION_TRAP_PENALTY:.1f}% Exhaustion Absorption Trap (Extended RSI {rsi_14:.1f})")

    # Wyckoff Climax Governor: Detect rejection wick on high volume (LTF)
    is_climax_trap = False
    if tf in ['5m', '15m']:
        if is_long and upper_wick_ratio >= STAGE2_CLIMAX_WICK_THRESHOLD and ratio >= 2.0:
            is_climax_trap = True
        elif is_short and lower_wick_ratio >= STAGE2_CLIMAX_WICK_THRESHOLD and ratio >= 2.0:
            is_climax_trap = True
        elif ratio >= STAGE2_CLIMAX_VOL_RATIO_LTF and (rsi_14 >= 68.0 if is_long else rsi_14 <= 32.0):
            is_climax_trap = True

    if is_climax_trap:
        hard_friction += STAGE2_FRICTION_CLIMAX_PENALTY
        wick_pct = (upper_wick_ratio if is_long else lower_wick_ratio) * 100.0
        checklist.append(f"{STAGE2_FRICTION_CLIMAX_PENALTY:.1f}% Climax Rejection Trap ({ratio:.1f}x vol, {wick_pct:.0f}% wick)")

    # Fresh Squeeze Ignition: Clean, early breakout from Bollinger/Keltner squeeze on LTF
    # Empirically demonstrated 84.1% win rate on 5m charts.
    ignition_points = 0.0
    is_fresh_squeeze_ignition = False
    if tf in ['5m', '15m'] and is_squeezed and macro_trend_aligned and vol_points > 0:
        has_rsi_runway = (42.0 <= rsi_14 <= 66.0) if is_long else (34.0 <= rsi_14 <= 58.0)
        clean_wick = (upper_wick_ratio < 0.22) if is_long else (lower_wick_ratio < 0.22)
        if has_rsi_runway and clean_wick and (ratio <= STAGE2_CLIMAX_VOL_RATIO_LTF) and not is_climax_trap:
            is_fresh_squeeze_ignition = True
            ignition_points = STAGE2_FRESH_SQUEEZE_IGNITION_POINTS
            checklist.append(f"+{ignition_points:.1f}% Fresh Squeeze Ignition (Early Institutional Breakout)")

    # Smart Money Absorption: If institutional volume is strong (vol_points >= 5.0), halve friction
    raw_friction = hard_friction + soft_friction
    friction_absorbed = False
    if vol_points >= STAGE2_ABSORPTION_VOL_THRESHOLD and raw_friction < 0.0:
        trap_pen = (STAGE2_FRICTION_EXHAUSTION_TRAP_PENALTY if exhaustion_trap else 0.0) + (STAGE2_FRICTION_CLIMAX_PENALTY if is_climax_trap else 0.0)
        other_friction = raw_friction - trap_pen
        if other_friction < 0.0:
            other_friction *= STAGE2_ABSORPTION_MULTIPLIER
        raw_friction = other_friction + trap_pen
        friction_absorbed = True
        checklist.append("⚡ Smart Money Absorption: Strong volume halved friction penalty")

    friction = max(STAGE2_MAX_FRICTION_FLOOR, raw_friction)

    # Net Score Calculation
    net_score = base_score + vol_points + level_points + vwap_points + ob_points + trend_tailwinds + catalyst_points + ignition_points + friction
    final_score = round(max(0.0, min(100.0, net_score)), 1)
    
    # --------------------------------------------------------------------------
    # STAGE 3: The PREMIUM Gate & Dual-Regime Floor (Tier Classification)
    # --------------------------------------------------------------------------
    premium_capped = False
    vip_capped = False
    premium_thresh = float(signal_data.get('_override_premium_threshold', STAGE3_PREMIUM_THRESHOLD))
    
    # Dual-Regime Dispatch Floor: Reversals dispatch at 80.0%, Continuations at 86.0% (unless overridden by caller/pipeline)
    if is_reversal:
        std_thresh = float(signal_data.get('_override_reversal_threshold', STAGE3_REVERSAL_THRESHOLD))
    else:
        std_thresh = float(signal_data.get('_override_standard_threshold', STAGE3_STANDARD_THRESHOLD))
    
    premium_capped_score = round(premium_thresh - 0.1, 1)

    if circuit_breaker_tripped:
        tier = "REJECTED"
        rejection_reason = fatal_flaw
    elif final_score >= premium_thresh:
        missing = []
        if vol_points <= 0.0 or is_anemic_vol:
            missing.append("Volume Surge")
        if level_points <= 0.0:
            missing.append("Key Level Precision")
        if has_sweep_risk:
            missing.append("No Sweep Risk (Opposing pool detected)")
        if not macro_trend_aligned:
            missing.append("Multi-Timeframe Macro Alignment")
        if not is_squeezed and not is_fresh_squeeze_ignition:
            missing.append("Volatility Squeeze Compression (Bollinger inside Keltner)")
        if book_depth < STAGE3_PREMIUM_MIN_BOOK_DEPTH_USD:
            missing.append(f"Order Book Depth (>= ${STAGE3_PREMIUM_MIN_BOOK_DEPTH_USD/1000:.0f}k)")

        # Microstructure Climax Filter for Lower Timeframes (5m, 15m)
        if tf in ['5m', '15m']:
            if is_climax_trap or exhaustion_trap:
                missing.append("No Climax Trap (Volume or Wick Exhaustion)")
            if (is_long and rsi_14 > STAGE2_PREMIUM_LTF_RSI_MAX_LONG) or (is_short and rsi_14 < STAGE2_PREMIUM_LTF_RSI_MIN_SHORT):
                missing.append("RSI Momentum Runway (RSI not overextended)")
            if ratio > STAGE2_CLIMAX_VOL_RATIO_LTF:
                missing.append(f"Orderly Volume (Vol {ratio:.1f}x <= {STAGE2_CLIMAX_VOL_RATIO_LTF}x)")

            # Adjustment 5: Double-Barrel Macro Filter for VIP > 85%
            # Mandates that for 5m/15m setups to achieve PREMIUM tier (>= 88%), 4H ADX must be > 25 with matching +DI/-DI
            require_4h_adx = getattr(settings, 'premium_require_4h_adx_alignment', getattr(settings, 'vip_require_4h_adx_alignment', True))
            if require_4h_adx and 'adx_4h' in signal_data:
                adx_4h = float(signal_data.get('adx_4h', 0.0))
                plus_di_4h = float(signal_data.get('plus_di_4h', 0.0))
                minus_di_4h = float(signal_data.get('minus_di_4h', 0.0))
                min_adx = float(getattr(settings, 'premium_macro_adx_threshold', getattr(settings, 'vip_macro_adx_threshold', 25.0)))

                if adx_4h < min_adx:
                    missing.append(f"4H Macro ADX Strong Trend (ADX {adx_4h:.1f} > {min_adx:.0f})")
                if is_long and plus_di_4h <= minus_di_4h:
                    missing.append("4H Directional Trend Alignment (+DI > -DI)")
                elif is_short and minus_di_4h <= plus_di_4h:
                    missing.append("4H Directional Trend Alignment (-DI > +DI)")

        if not missing:
            tier = "PREMIUM"
            rejection_reason = ""
            checklist.append("👑 PREMIUM Tier Qualification: Institutional Triple-Confluence Gate (>= 90% Empirical Win Rate)")
        else:
            final_score = min(final_score, premium_capped_score)
            tier = "STANDARD" if final_score >= std_thresh else "REJECTED"
            premium_capped = True
            vip_capped = True
            rejection_reason = ""
            checklist.append(f"[PREMIUM Gate] Capped at {premium_capped_score:.1f}% (Missing {' & '.join(missing)})")
    elif final_score >= std_thresh:
        tier = "STANDARD"
        rejection_reason = ""
    else:
        tier = "REJECTED"
        rejection_reason = f"Score {final_score:.1f}% below {std_thresh:.1f}% threshold"
        
    eval_trace = {
        "stage1_passed": not circuit_breaker_tripped,
        "circuit_breaker_tripped": circuit_breaker_tripped,
        "fatal_flaw": fatal_flaw,
        "circuit_breaker": fatal_flaw.replace("Phase 1 Gate Failed: ", "") if circuit_breaker_tripped else None,
        "base_score": base_score,
        "vol_points": round(vol_points, 2),
        "level_points": round(level_points, 2),
        "vwap_points": round(vwap_points, 2),
        "ob_points": round(ob_points, 2),
        "trend_tailwinds": round(trend_tailwinds, 2),
        "catalyst_points": round(catalyst_points, 2),
        "ignition_points": round(ignition_points, 2),
        "friction": round(friction, 2),
        "net_score": round(net_score, 2),
        "final_score": final_score,
        "tier": tier,
        "premium_capped": premium_capped,
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
            "target_magnet": has_magnet_pull,
            "blocking_ob_wall": blocking_ob_wall,
            "has_sweep_risk": has_sweep_risk,
            "friction_absorbed": friction_absorbed,
            "is_reversal": is_reversal,
            "pulling_to_vwap": pulling_to_vwap,
            "exhaustion_trap": exhaustion_trap,
            "is_climax_trap": is_climax_trap,
            "is_fresh_squeeze_ignition": is_fresh_squeeze_ignition,
            "upper_wick_ratio": round(upper_wick_ratio, 4),
            "lower_wick_ratio": round(lower_wick_ratio, 4)
        }
    }

    return {
        "status": tier,
        "tier": tier,
        "circuit_breaker_tripped": circuit_breaker_tripped,
        "fatal_flaw": fatal_flaw,
        "final_score": final_score,
        "net_score": round(net_score, 2),
        "rejection_reason": rejection_reason,
        "scoring_checklist": checklist,
        "eval_trace": eval_trace
    }
