"""
RiskManager enforces institutional stop loss, take profit, dynamic leverage,
and liquidation zone calculations.
"""

import logging
import math
import pandas as pd
from typing import Dict, List, Any, Optional
from core.config import settings

logger = logging.getLogger(__name__)


# Anti-Noise / Anti-Sweep SL Floor (0.90% / 0.0090)
MIN_STOP_LOSS_PCT: float = 0.90
MIN_STOP_LOSS_RATIO: float = 0.0090


class RiskManager:
    """Service to compute risk parameters, stops, targets, and leverage."""
    
    MIN_STOP_LOSS_PCT: float = MIN_STOP_LOSS_PCT
    MIN_STOP_LOSS_RATIO: float = MIN_STOP_LOSS_RATIO

    def __init__(
        self, 
        atr_period: int = 14, 
        sl_atr_multiplier: float = 1.5,
        target_rr_ratio: float = 2.5
    ) -> None:
        self.atr_period: int = atr_period
        self.sl_atr_multiplier: float = sl_atr_multiplier
        self.target_rr_ratio: float = target_rr_ratio

    def calculate_liquidation_zones(self, df: pd.DataFrame, window: int = 5) -> Dict[str, List[float]]:
        """Identify Swing Highs and Lows acting as Liquidation Zones."""
        if df is None or len(df) < window * 2 + 1:
            return {'short_liq_zones': [], 'long_liq_zones': []}
            
        short_zones: List[float] = []
        long_zones: List[float] = []
        for i in range(window, len(df) - window):
            is_swing_high, is_swing_low = True, True
            current_high, current_low = df.iloc[i]['high'], df.iloc[i]['low']
            for j in range(i - window, i + window + 1):
                if i != j:
                    if df.iloc[j]['high'] >= current_high: is_swing_high = False
                    if df.iloc[j]['low'] <= current_low: is_swing_low = False
            if is_swing_high: short_zones.append(round(current_high * 1.002, 2))
            if is_swing_low: long_zones.append(round(current_low * 0.998, 2))
            
        return {
            'short_liq_zones': sorted(list(set(short_zones)))[-3:],
            'long_liq_zones': sorted(list(set(long_zones)))[:3]
        }

    def compute_risk_parameters(
        self, 
        df: pd.DataFrame, 
        symbol: str, 
        timeframe: str, 
        direction: str, 
        entry: float, 
        regime: str,
        liquidity_magnets: Optional[List[Dict[str, Any]]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Calculate stop loss, take profit targets, risk/reward ratios, and leverage.
        Enforces asset-tier volatility multipliers and anchors stops safely behind
        opposing SWEEP_RISK liquidity pools when present.
        Returns None if risk parameters or zero-entry inputs fail validation.
        """
        if df is None or df.empty or entry <= 0.0 or direction not in ["Bullish", "Bearish"]:
            return None
            
        latest = df.iloc[-1]
        atr = float(latest.get(f'ATRr_{self.atr_period}', entry * 0.02))
        
        # Asset-Tier Volatility Sizing (Armor Patch Fix 3):
        # BTC: Max 20x | ETH: Max 15x | Altcoins: Max 10x
        is_btc = "BTC" in symbol.upper()
        is_eth = "ETH" in symbol.upper()
        is_tier1 = is_btc or is_eth
        is_commodity = any(c in symbol.upper() for c in ["XAU", "PAXG", "GOLD"])
        if is_btc:
            tier_min_sl_pct = 0.80
            tier_atr_mult = 1.2
            tier_max_lev = 20
        elif is_eth:
            tier_min_sl_pct = 0.90
            tier_atr_mult = 1.2
            tier_max_lev = 15
        elif is_commodity:
            tier_min_sl_pct = 0.50
            tier_atr_mult = 1.0
            tier_max_lev = 20
        else:
            tier_min_sl_pct = 1.50
            tier_atr_mult = 1.6
            tier_max_lev = 10
        
        # Timeframe lookback & ROE risk budget
        base_budget = float(getattr(settings, 'roe_risk_budget', 12.0))
        if timeframe == '5m':
            lookback = 15
            atr_mult = tier_atr_mult
            roe_risk_budget = max(14.0, base_budget)  # Floor at 14.0 to guarantee 15x lev on 0.90% SL
        elif timeframe == '15m':
            lookback = 12
            atr_mult = tier_atr_mult
            roe_risk_budget = min(14.0, base_budget)
        elif timeframe == '1h':
            lookback = 15
            atr_mult = tier_atr_mult
            roe_risk_budget = min(12.0, base_budget)
        else:
            lookback = 15
            atr_mult = max(tier_atr_mult, self.sl_atr_multiplier)
            roe_risk_budget = min(10.0, base_budget)

        recent_low = float(df['low'].tail(lookback).min())
        recent_high = float(df['high'].tail(lookback).max())

        min_risk_fallback = entry * (tier_min_sl_pct / 100.0)

        # Pillar 4: Dynamic ATR Stop-Loss (Capital Protection)
        # Anchors SL to entry +/- (ATR * sl_atr_multiplier) based on prevailing volatility,
        # eliminating the double-compounded recent_low - (atr * mult) bug.
        atr_mult_eff = getattr(self, 'sl_atr_multiplier', 1.5)
        if direction == "Bullish":
            stop_loss = entry - (atr * atr_mult_eff)
            risk = (entry - stop_loss) if entry > stop_loss else min_risk_fallback
        else:  # Bearish
            stop_loss = entry + (atr * atr_mult_eff)
            risk = (stop_loss - entry) if stop_loss > entry else min_risk_fallback

        # Connect SWEEP_RISK: Anchor Stop Loss 0.20% behind opposing sweep magnet pool
        if liquidity_magnets:
            if direction == "Bullish":
                # Look for opposing BID sweep pools below entry
                sweep_pools = [
                    float(m['price']) for m in liquidity_magnets 
                    if m.get('role') == 'SWEEP_RISK' or (
                        str(m.get('side', '')).upper() == 'BID' and 
                        float(m.get('price', entry)) < entry and 
                        (entry - float(m['price'])) / entry <= 0.008
                    )
                ]
                if sweep_pools:
                    # Anchor SL safely 0.20% below the lowest resting sweep pool
                    safe_anchor = min(sweep_pools) * 0.998
                    if safe_anchor < stop_loss:
                        stop_loss = safe_anchor
            else:  # Bearish
                # Look for opposing ASK sweep pools above entry
                sweep_pools = [
                    float(m['price']) for m in liquidity_magnets 
                    if m.get('role') == 'SWEEP_RISK' or (
                        str(m.get('side', '')).upper() == 'ASK' and 
                        float(m.get('price', 0.0)) > entry and 
                        (float(m['price']) - entry) / entry <= 0.008
                    )
                ]
                if sweep_pools:
                    # Anchor SL safely 0.20% above the highest resting sweep pool
                    safe_anchor = max(sweep_pools) * 1.002
                    if safe_anchor > stop_loss:
                        stop_loss = safe_anchor

        sl_pct = abs(entry - stop_loss) / entry * 100.0

        # Anti-Noise-Wick Floor: Ensure SL has at least asset-tier buffer to absorb exchange noise
        if sl_pct < tier_min_sl_pct:
            risk = entry * (tier_min_sl_pct / 100.0)
            if direction == "Bullish":
                stop_loss = entry - risk
            else:
                stop_loss = entry + risk
            sl_pct = tier_min_sl_pct
        else:
            risk = abs(entry - stop_loss)

        # Timeframe-Tuned Asymmetric Target Architecture
        if timeframe == '5m':
            tp1_mult = 0.60 if is_commodity else 0.80
            tp2_mult = 1.80
        elif timeframe == '15m':
            tp1_mult = 0.80 if is_commodity else 1.00
            tp2_mult = 2.20
        elif timeframe == '1h':
            tp1_mult = 1.20 if is_commodity else 1.50
            tp2_mult = 3.00
        else:  # 4h
            tp1_mult = 1.50 if is_commodity else 1.80
            tp2_mult = 3.50

        tp1 = entry + (risk * tp1_mult) if direction == "Bullish" else entry - (risk * tp1_mult)
        tp2 = entry + (risk * tp2_mult) if direction == "Bullish" else entry - (risk * tp2_mult)
        take_profit = tp1

        tp1_pct = abs(tp1 - entry) / entry * 100.0
        tp2_pct = abs(tp2 - entry) / entry * 100.0
        tp_pct = tp1_pct
        
        # Adjustment 2: Pre-Execution Friction-to-Target Floor (Spread & Tax Filter)
        # Target_Distance_Percentage / (Spread_Percentage + Taker_Fees) must be >= 4.0
        tax_pct = ((getattr(settings, 'default_taker_fee', 0.0005) * 2) + getattr(settings, 'default_scalp_slippage_pct', 0.0010)) * 100.0
        friction_ratio = (tp1_pct / tax_pct) if tax_pct > 0 else 99.9
        min_friction = float(getattr(settings, 'min_friction_to_target_ratio', 4.0))
        if friction_ratio < min_friction:
            logger.info(f"[REJECT: FRICTION_TAX] Target {tp1_pct:.2f}% / Tax {tax_pct:.2f}% = {friction_ratio:.1f} < {min_friction:.1f} on {symbol} {timeframe}")
            return None

        # Mandatory Risk Parameters & Regime Logic
        if regime == "HIGH_VOLATILITY":
            if sl_pct > settings.sl_pct_max_high_volatility or sl_pct < settings.sl_pct_min_high_volatility:
                logger.info(f"[REJECT: REGIME_SL_RANGE] sl_pct={sl_pct:.2f}% (Limit {settings.sl_pct_min_high_volatility}%-{settings.sl_pct_max_high_volatility}%) on {symbol} {timeframe}")
                return None
        else:
            is_commodity = any(c in symbol for c in ["XAU", "PAXG", "GOLD"])
            if timeframe == '4h':
                max_sl = 7.50
            elif timeframe == '1h':
                max_sl = 4.50
            elif not is_tier1:
                max_sl = 3.50
            else:
                max_sl = settings.sl_pct_max_normal
            min_sl = settings.sl_pct_min_normal
            if sl_pct > max_sl or sl_pct < min_sl:
                logger.info(f"[REJECT: SL_RANGE] sl_pct={sl_pct:.2f}% (Limit {min_sl}%-{max_sl}%) on {symbol} {timeframe}")
                return None
            
        close_ratio = getattr(settings, 'tp1_close_ratio', 0.50)
        overall_tp_pct = (close_ratio * tp1_pct) + ((1.0 - close_ratio) * tp2_pct)
        rr_val = round(overall_tp_pct / sl_pct, 2) if sl_pct > 0 else 0.0
        if sl_pct == 0 or rr_val < settings.min_acceptable_rr:
            logger.info(f"[REJECT: LOW_RR] rr={rr_val} on {symbol} {timeframe}")
            return None
            
        # Timeframe limitations
        if timeframe == '4h' and sl_pct > 7.50:
            return None
        if timeframe == '1h' and sl_pct > 4.50:
            return None

        # Timeframe & Asset-Tier Adaptive Leverage Calculation
        if timeframe == '4h':
            rec_lev_val = max(1, min(3 if not is_tier1 else 5, math.floor(roe_risk_budget / max(0.1, sl_pct))))
        elif regime == "HIGH_VOLATILITY":
            max_lev_regime = min(15, tier_max_lev)
            rec_lev_val = max(3, min(max_lev_regime, math.floor(roe_risk_budget / sl_pct)))
        else:
            rec_lev_val = max(3, min(tier_max_lev, math.floor(roe_risk_budget / sl_pct)))
            
        rr_ratio = round(tp_pct / sl_pct, 2) if sl_pct > 0 else 0.0
        roe_sl = round(sl_pct * rec_lev_val, 2)
        roe_tp = round(tp_pct * rec_lev_val, 2)
        roe_tp1 = round(tp1_pct * rec_lev_val, 2)
        roe_tp2 = round(tp2_pct * rec_lev_val, 2)
        close_ratio = getattr(settings, 'tp1_close_ratio', 0.50)
        overall_tp2_roe = round((close_ratio * tp1_pct + (1.0 - close_ratio) * tp2_pct) * rec_lev_val, 2)
        
        be_mult = getattr(settings, 'be_trigger_rr_ratio_commodity', 0.30) if is_commodity else getattr(settings, 'be_trigger_rr_ratio', 1.0)
        be_dist = risk * be_mult
        breakeven_price = (entry + be_dist) if direction == "Bullish" else (entry - be_dist)

        return {
            'stop_loss': round(stop_loss, 2),
            'take_profit': round(take_profit, 2),
            'tp1': round(tp1, 2),
            'tp2': round(tp2, 2),
            'sl_pct': round(sl_pct, 2),
            'tp_pct': round(tp_pct, 2),
            'tp1_pct': round(tp1_pct, 2),
            'tp2_pct': round(tp2_pct, 2),
            'rr_ratio': rr_ratio,
            'roe_sl': roe_sl,
            'roe_tp': roe_tp,
            'roe_tp1': roe_tp1,
            'roe_tp2': roe_tp2,
            'overall_tp2_roe': overall_tp2_roe,
            'atr_value': round(atr, 4),
            'leverage_val': rec_lev_val,
            'recommended_leverage': f"{rec_lev_val}x",
            'setup_type': 'STANDARD_SWING',
            'breakeven_price': round(breakeven_price, 2)
        }

    def recalibrate_for_tier(
        self,
        signal_data: Dict[str, Any],
        is_premium: bool = False, is_vip: bool = False,
        liquidity_magnets: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """
        Recalibrates leverage, stop loss, and take profit targets based on evaluated tier:
        - VIP Tier: Differentiates between PREMIUM_SNIPER (leaning against institutional wall on BTC/ETH)
          and PREMIUM_SWING (macro trend expansion on 1h or altcoins).
        - Standard Tier: Applies disciplined swing leverage (20x-25x on BTC/ETH, 10x-12x on Altcoins)
          with 70% profit-take at TP1 and auto-breakeven at 1.0R.
        """
        symbol = str(signal_data.get('symbol', '')).upper()
        timeframe = str(signal_data.get('timeframe', '15m'))
        direction = str(signal_data.get('direction', 'LONG')).upper()
        is_long = direction in ["BULLISH", "LONG", "BUY"]
        entry = float(signal_data.get('entry', signal_data.get('close', 0.0)))
        if entry <= 0:
            return signal_data

        is_btc = "BTC" in symbol
        is_eth = "ETH" in symbol
        is_commodity = any(c in symbol for c in ["XAU", "PAXG", "GOLD"])
        is_major = is_btc or is_eth
        
        magnets = liquidity_magnets or signal_data.get('liquidity_magnets', [])
        has_shield = any(m.get('role') == 'DEFENSIVE_SHIELD' for m in magnets) or bool(signal_data.get('ob_defensive_shield', False))
        
        # Determine if setup qualifies as a VIP Sniper
        # Requires: VIP tier, BTC/ETH, 5m or 15m timeframe, and an active defensive order book wall
        is_prem = is_premium or is_vip
        is_sniper = is_prem and is_major and (timeframe in ['5m', '15m']) and has_shield

        initial_sl = float(signal_data.get('stop_loss', entry * 0.98 if is_long else entry * 1.02))
        sl_pct = abs(entry - initial_sl) / entry * 100.0

        if is_sniper:
            setup_type = "PREMIUM_SNIPER"
            target_max_lev = getattr(settings, 'premium_max_leverage_btc', getattr(settings, 'vip_max_leverage_btc', 60.0)) if is_btc else getattr(settings, 'premium_max_leverage_eth', getattr(settings, 'vip_max_leverage_eth', 40.0))
            
            # Find defensive wall price to anchor SL safely behind it
            shield_pools = [
                float(m['price']) for m in magnets
                if m.get('role') == 'DEFENSIVE_SHIELD' and (
                    (is_long and float(m.get('price', entry)) < entry and (entry - float(m['price'])) / entry <= 0.015) or
                    (not is_long and float(m.get('price', 0.0)) > entry and (float(m['price']) - entry) / entry <= 0.015)
                )
            ]
            if shield_pools:
                # Anchor 0.15% behind the wall
                if is_long:
                    wall_sl = min(shield_pools) * 0.9985
                    if wall_sl < entry:
                        initial_sl = wall_sl
                else:
                    wall_sl = max(shield_pools) * 1.0015
                    if wall_sl > entry:
                        initial_sl = wall_sl
                sl_pct = abs(entry - initial_sl) / entry * 100.0

            if timeframe == '5m':
                # Institutional Anti-Noise Cushion for 5m Sniper (prevents 2-bps noise wickouts)
                prem_5m_min_sl = 1.05 if is_btc else 1.30
                if sl_pct < prem_5m_min_sl:
                    sl_pct = prem_5m_min_sl
                    initial_sl = entry * (1.0 - prem_5m_min_sl / 100.0) if is_long else entry * (1.0 + prem_5m_min_sl / 100.0)
            # Ensure minimum SL floor of 0.65% to avoid hyper-tight noise stopouts
            elif sl_pct < 0.65:
                sl_pct = 0.65
                initial_sl = entry * (1.0 - 0.0065) if is_long else entry * (1.0 + 0.0065)

            rec_lev_val = max(10, min(int(target_max_lev), math.floor(45.0 / sl_pct)))
            
            # Target TP1 for Sniper: ~80% to 100% ROE
            if timeframe == '5m':
                target_tp1_pct = round(max(0.70, min(1.05, sl_pct * 0.80)), 2)
            else:
                target_tp1_pct = max(sl_pct * 1.5, round(90.0 / rec_lev_val, 2))
            tp1 = entry + (entry * target_tp1_pct / 100.0) if is_long else entry - (entry * target_tp1_pct / 100.0)
            target_tp2_pct = round(target_tp1_pct * 1.5, 2)
            tp2 = entry + (entry * target_tp2_pct / 100.0) if is_long else entry - (entry * target_tp2_pct / 100.0)
            
        elif (is_premium or is_vip):
            setup_type = "PREMIUM_SWING"
            if timeframe == '5m':
                # Institutional Anti-Noise Cushion for 5m Premium (prevents 2-bps exchange noise wickouts)
                prem_5m_min_sl = 0.65 if is_commodity else (0.95 if is_btc else (1.05 if is_eth else 1.65))
                if sl_pct < prem_5m_min_sl:
                    sl_pct = prem_5m_min_sl
                    initial_sl = entry * (1.0 - prem_5m_min_sl / 100.0) if is_long else entry * (1.0 + prem_5m_min_sl / 100.0)

            if is_major:
                max_lev = getattr(settings, 'std_max_leverage_btc_eth', 25.0)
                rec_lev_val = max(5, min(int(max_lev), math.floor(35.0 / max(0.8, sl_pct))))
            elif is_commodity:
                max_lev = 25.0
                rec_lev_val = max(5, min(int(max_lev), math.floor(25.0 / max(0.50, sl_pct))))
            else:
                max_lev = getattr(settings, 'premium_max_leverage_alt', getattr(settings, 'vip_max_leverage_alt', 20.0))
                rec_lev_val = max(5, min(int(max_lev), math.floor(30.0 / max(1.2, sl_pct))))
                
            if timeframe == '5m':
                if is_commodity:
                    target_tp1_pct = round(max(0.35, min(0.65, sl_pct * 1.2)), 2)
                    target_tp2_pct = round(target_tp1_pct * 1.6, 2)
                else:
                    target_tp1_pct = round(max(0.70, min(1.20, sl_pct * 1.3)), 2)
                    target_tp2_pct = round(target_tp1_pct * 1.6, 2)
                tp1 = entry * (1.0 + target_tp1_pct / 100.0) if is_long else entry * (1.0 - target_tp1_pct / 100.0)
                tp2 = entry * (1.0 + target_tp2_pct / 100.0) if is_long else entry * (1.0 - target_tp2_pct / 100.0)
            elif timeframe == '15m':
                if is_commodity:
                    target_tp1_pct = round(max(0.50, min(0.95, sl_pct * 1.3)), 2)
                    target_tp2_pct = round(target_tp1_pct * 1.7, 2)
                else:
                    target_tp1_pct = round(max(1.20, min(1.80, sl_pct * 1.4)), 2)
                    target_tp2_pct = round(target_tp1_pct * 1.7, 2)
                tp1 = entry * (1.0 + target_tp1_pct / 100.0) if is_long else entry * (1.0 - target_tp1_pct / 100.0)
                tp2 = entry * (1.0 + target_tp2_pct / 100.0) if is_long else entry * (1.0 - target_tp2_pct / 100.0)
            else:  # 1h, 4h
                if signal_data.get('tp1'):
                    tp1 = float(signal_data['tp1'])
                    tp2 = float(signal_data.get('tp2', entry * 1.050 if is_long else entry * 0.950))
                    target_tp1_pct = abs(tp1 - entry) / entry * 100.0
                    target_tp2_pct = abs(tp2 - entry) / entry * 100.0
                else:
                    if is_commodity:
                        target_tp1_pct = round(max(0.90, min(1.80, sl_pct * 1.5)), 2)
                        target_tp2_pct = round(target_tp1_pct * 1.8, 2)
                    else:
                        target_tp1_pct = round(max(1.80, min(3.00, sl_pct * 1.6)), 2)
                        target_tp2_pct = round(target_tp1_pct * 1.8, 2)
                    tp1 = entry * (1.0 + target_tp1_pct / 100.0) if is_long else entry * (1.0 - target_tp1_pct / 100.0)
                    tp2 = entry * (1.0 + target_tp2_pct / 100.0) if is_long else entry * (1.0 - target_tp2_pct / 100.0)
            
        else:
            if timeframe == '5m':
                setup_type = "STANDARD_SCALP"
                max_lev = getattr(settings, 'std_max_leverage_btc_eth', 25.0) if is_btc else (20.0 if is_eth else getattr(settings, 'std_max_leverage_alt', 15.0))
                budget = 22.0 if is_btc else (18.0 if is_eth else 15.0)
                rec_lev_val = max(5, min(int(max_lev), math.floor(budget / max(0.50 if is_commodity else 0.5, sl_pct))))
                # Scalp Take Profit Targets: reachable in 30-60 mins
                if is_commodity:
                    target_tp1_pct = round(max(0.30, min(0.60, sl_pct * 1.2)), 2)
                    target_tp2_pct = round(target_tp1_pct * 1.6, 2)
                else:
                    target_tp1_pct = round(max(0.60, min(1.10, sl_pct * 1.2)), 2)
                    target_tp2_pct = round(target_tp1_pct * 1.6, 2)
                tp1 = entry * (1.0 + target_tp1_pct / 100.0) if is_long else entry * (1.0 - target_tp1_pct / 100.0)
                tp2 = entry * (1.0 + target_tp2_pct / 100.0) if is_long else entry * (1.0 - target_tp2_pct / 100.0)
            elif timeframe == '15m':
                setup_type = "STANDARD_SWING"  # Preserves test_recalibrate_for_tier_standard_swing assertion
                max_lev = 20.0 if is_btc else (18.0 if is_eth else getattr(settings, 'std_max_leverage_alt', 12.0))
                budget = 20.0 if is_btc else (18.0 if is_eth else 15.0)
                rec_lev_val = max(5, min(int(max_lev), math.floor(budget / max(0.50 if is_commodity else 0.7, sl_pct))))
                # Intraday Targets: reachable in 1-3 hours
                if is_commodity:
                    target_tp1_pct = round(max(0.45, min(0.85, sl_pct * 1.3)), 2)
                    target_tp2_pct = round(target_tp1_pct * 1.7, 2)
                else:
                    target_tp1_pct = round(max(1.10, min(1.60, sl_pct * 1.4)), 2)
                    target_tp2_pct = round(target_tp1_pct * 1.7, 2)
                tp1 = entry * (1.0 + target_tp1_pct / 100.0) if is_long else entry * (1.0 - target_tp1_pct / 100.0)
                tp2 = entry * (1.0 + target_tp2_pct / 100.0) if is_long else entry * (1.0 - target_tp2_pct / 100.0)
            else:  # 1h, 4h
                setup_type = "STANDARD_SWING"
                max_lev = 12.0 if is_btc else (10.0 if is_eth else 8.0)
                budget = 15.0 if is_btc else (14.0 if is_eth else 12.0)
                rec_lev_val = max(4, min(int(max_lev), math.floor(budget / max(0.5 if is_commodity else 0.9, sl_pct))))
                # Swing Targets: reachable in 4-8 hours
                if is_commodity:
                    target_tp1_pct = round(max(0.80, min(1.50, sl_pct * 1.5)), 2)
                    target_tp2_pct = round(target_tp1_pct * 1.8, 2)
                else:
                    target_tp1_pct = round(max(1.80, min(2.80, sl_pct * 1.6)), 2)
                    target_tp2_pct = round(target_tp1_pct * 1.8, 2)
                tp1 = entry * (1.0 + target_tp1_pct / 100.0) if is_long else entry * (1.0 - target_tp1_pct / 100.0)
                tp2 = entry * (1.0 + target_tp2_pct / 100.0) if is_long else entry * (1.0 - target_tp2_pct / 100.0)

        # Compute breakeven trigger price at 1.0R
        be_dist = abs(entry - initial_sl) * getattr(settings, 'breakeven_trigger_r_ratio', 1.0)
        breakeven_price = (entry + be_dist) if is_long else (entry - be_dist)

        close_ratio = getattr(settings, 'tp1_close_ratio', 0.50)
        roe_sl = round(sl_pct * rec_lev_val, 1)
        roe_tp1 = round(target_tp1_pct * rec_lev_val, 1)
        overall_tp2_roe = round((close_ratio * target_tp1_pct + (1.0 - close_ratio) * target_tp2_pct) * rec_lev_val, 1)

        signal_data['setup_type'] = setup_type
        signal_data['leverage_val'] = rec_lev_val
        signal_data['recommended_leverage'] = f"{rec_lev_val}x"
        signal_data['stop_loss'] = round(initial_sl, 2)
        signal_data['tp1'] = round(tp1, 2)
        signal_data['tp2'] = round(tp2, 2)
        signal_data['take_profit'] = round(tp1, 2)
        signal_data['sl_pct'] = round(sl_pct, 2)
        signal_data['tp_pct'] = round(target_tp1_pct, 2)
        signal_data['tp1_pct'] = round(target_tp1_pct, 2)
        signal_data['tp2_pct'] = round(target_tp2_pct, 2)
        signal_data['roe_sl'] = roe_sl
        signal_data['roe_tp1'] = roe_tp1
        signal_data['overall_tp2_roe'] = overall_tp2_roe
        signal_data['breakeven_price'] = round(breakeven_price, 2)

        return signal_data


