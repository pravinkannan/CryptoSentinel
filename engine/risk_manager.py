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
        
        # Asset-Tier Volatility Sizing:
        # Tier 1 (BTC/ETH): High liquidity, tighter ATR noise band, higher leverage ceiling
        # Tier 2 (SOL/ZEC/High-Beta): Wider spread/wick excursions, requires larger ATR buffer & lower leverage
        is_tier1 = any(coin in symbol.upper() for coin in ["BTC", "ETH"])
        if is_tier1:
            tier_min_sl_pct = 0.90
            tier_atr_mult = 1.2
            tier_max_lev = 25
        else:
            tier_min_sl_pct = 1.50
            tier_atr_mult = 1.6
            tier_max_lev = 12
        
        # Timeframe lookback & ROE risk budget
        if timeframe == '5m':
            lookback = 15
            atr_mult = tier_atr_mult
            roe_risk_budget = 20.0
        elif timeframe == '15m':
            lookback = 12
            atr_mult = tier_atr_mult
            roe_risk_budget = 18.0
        elif timeframe == '1h':
            lookback = 15
            atr_mult = tier_atr_mult
            roe_risk_budget = 16.0
        else:
            lookback = 15
            atr_mult = max(tier_atr_mult, self.sl_atr_multiplier)
            roe_risk_budget = 15.0

        recent_low = float(df['low'].tail(lookback).min())
        recent_high = float(df['high'].tail(lookback).max())

        min_risk_fallback = entry * (tier_min_sl_pct / 100.0)

        if direction == "Bullish":
            stop_loss = recent_low - (atr * atr_mult)
            risk = (entry - stop_loss) if entry > stop_loss else min_risk_fallback
        else:  # Bearish
            stop_loss = recent_high + (atr * atr_mult)
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

        tp1 = entry + (risk * settings.tp1_rr_ratio) if direction == "Bullish" else entry - (risk * settings.tp1_rr_ratio)
        tp2 = entry + (risk * settings.tp2_rr_ratio) if direction == "Bullish" else entry - (risk * settings.tp2_rr_ratio)
        take_profit = tp1

        tp1_pct = abs(tp1 - entry) / entry * 100.0
        tp2_pct = abs(tp2 - entry) / entry * 100.0
        tp_pct = tp1_pct
        
        # Mandatory Risk Parameters & Regime Logic
        if regime == "HIGH_VOLATILITY":
            if sl_pct > settings.sl_pct_max_high_volatility or sl_pct < settings.sl_pct_min_high_volatility:
                logger.info(f"[REJECT: REGIME_SL_RANGE] sl_pct={sl_pct:.2f}% (Limit {settings.sl_pct_min_high_volatility}%-{settings.sl_pct_max_high_volatility}%) on {symbol} {timeframe}")
                return None
        else:
            max_sl = 3.50 if (timeframe in ['1h', '4h'] or not is_tier1) else settings.sl_pct_max_normal
            if sl_pct > max_sl or sl_pct < settings.sl_pct_min_normal:
                logger.info(f"[REJECT: SL_RANGE] sl_pct={sl_pct:.2f}% (Limit {settings.sl_pct_min_normal}%-{max_sl}%) on {symbol} {timeframe}")
                return None
            
        rr_val = round(tp_pct / sl_pct, 2) if sl_pct > 0 else 0.0
        if sl_pct == 0 or rr_val < settings.min_acceptable_rr:
            logger.info(f"[REJECT: LOW_RR] rr={rr_val} on {symbol} {timeframe}")
            return None
            
        # Timeframe limitations
        if timeframe in ['1h', '4h']:
            if timeframe == '4h':
                return None
            if timeframe == '1h' and sl_pct > 3.50:
                return None

        # Timeframe & Asset-Tier Adaptive Leverage Calculation
        if regime == "HIGH_VOLATILITY":
            max_lev_regime = min(15, tier_max_lev)
            rec_lev_val = max(3, min(max_lev_regime, math.floor(roe_risk_budget / sl_pct)))
        else:
            rec_lev_val = max(3, min(tier_max_lev, math.floor(roe_risk_budget / sl_pct)))
            
        rr_ratio = round(tp_pct / sl_pct, 2) if sl_pct > 0 else 0.0
        roe_sl = round(sl_pct * rec_lev_val, 2)
        roe_tp = round(tp_pct * rec_lev_val, 2)
        roe_tp1 = round(tp1_pct * rec_lev_val, 2)
        roe_tp2 = round(tp2_pct * rec_lev_val, 2)
        close_ratio = getattr(settings, 'tp1_close_ratio', 0.70)
        overall_tp2_roe = round((close_ratio * tp1_pct + (1.0 - close_ratio) * tp2_pct) * rec_lev_val, 2)
        
        be_dist = risk * getattr(settings, 'breakeven_trigger_r_ratio', 1.0)
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
        is_vip: bool,
        liquidity_magnets: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """
        Recalibrates leverage, stop loss, and take profit targets based on evaluated tier:
        - VIP Tier: Differentiates between VIP_SNIPER (leaning against institutional wall on BTC/ETH)
          and VIP_SWING (macro trend expansion on 1h or altcoins).
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
        is_major = is_btc or is_eth
        
        magnets = liquidity_magnets or signal_data.get('liquidity_magnets', [])
        has_shield = any(m.get('role') == 'DEFENSIVE_SHIELD' for m in magnets) or bool(signal_data.get('ob_defensive_shield', False))
        
        # Determine if setup qualifies as a VIP Sniper
        # Requires: VIP tier, BTC/ETH, 5m or 15m timeframe, and an active defensive order book wall
        is_sniper = is_vip and is_major and (timeframe in ['5m', '15m']) and has_shield

        initial_sl = float(signal_data.get('stop_loss', entry * 0.98 if is_long else entry * 1.02))
        sl_pct = abs(entry - initial_sl) / entry * 100.0

        if is_sniper:
            setup_type = "VIP_SNIPER"
            target_max_lev = getattr(settings, 'vip_max_leverage_btc', 60.0) if is_btc else getattr(settings, 'vip_max_leverage_eth', 40.0)
            
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

            # Ensure minimum SL floor of 0.65% to avoid hyper-tight noise stopouts
            if sl_pct < 0.65:
                sl_pct = 0.65
                initial_sl = entry * (1.0 - 0.0065) if is_long else entry * (1.0 + 0.0065)

            rec_lev_val = max(10, min(int(target_max_lev), math.floor(45.0 / sl_pct)))
            
            # Target TP1 for Sniper: ~80% to 100% ROE
            target_tp1_pct = max(sl_pct * 1.5, round(90.0 / rec_lev_val, 2))
            tp1 = entry + (entry * target_tp1_pct / 100.0) if is_long else entry - (entry * target_tp1_pct / 100.0)
            target_tp2_pct = round(target_tp1_pct * 1.5, 2)
            tp2 = entry + (entry * target_tp2_pct / 100.0) if is_long else entry - (entry * target_tp2_pct / 100.0)
            
        elif is_vip:
            setup_type = "VIP_SWING"
            if is_major:
                max_lev = getattr(settings, 'std_max_leverage_btc_eth', 25.0)
                rec_lev_val = max(5, min(int(max_lev), math.floor(35.0 / max(0.8, sl_pct))))
            else:
                max_lev = getattr(settings, 'vip_max_leverage_alt', 20.0)
                rec_lev_val = max(5, min(int(max_lev), math.floor(30.0 / max(1.2, sl_pct))))
                
            tp1 = float(signal_data.get('tp1', entry * 1.025 if is_long else entry * 0.975))
            tp2 = float(signal_data.get('tp2', entry * 1.050 if is_long else entry * 0.950))
            target_tp1_pct = abs(tp1 - entry) / entry * 100.0
            target_tp2_pct = abs(tp2 - entry) / entry * 100.0
            
        else:
            if timeframe == '5m':
                setup_type = "STANDARD_SCALP"
                max_lev = getattr(settings, 'std_max_leverage_btc_eth', 25.0) if is_btc else (20.0 if is_eth else getattr(settings, 'std_max_leverage_alt', 15.0))
                budget = 22.0 if is_btc else (18.0 if is_eth else 15.0)
                rec_lev_val = max(5, min(int(max_lev), math.floor(budget / max(0.5, sl_pct))))
                # Scalp Take Profit Targets: reachable in 30-60 mins
                target_tp1_pct = round(max(0.60, min(1.10, sl_pct * 1.2)), 2)
                target_tp2_pct = round(target_tp1_pct * 1.6, 2)
                tp1 = entry * (1.0 + target_tp1_pct / 100.0) if is_long else entry * (1.0 - target_tp1_pct / 100.0)
                tp2 = entry * (1.0 + target_tp2_pct / 100.0) if is_long else entry * (1.0 - target_tp2_pct / 100.0)
            elif timeframe == '15m':
                setup_type = "STANDARD_SWING"  # Preserves test_recalibrate_for_tier_standard_swing assertion
                max_lev = 20.0 if is_btc else (18.0 if is_eth else getattr(settings, 'std_max_leverage_alt', 12.0))
                budget = 20.0 if is_btc else (18.0 if is_eth else 15.0)
                rec_lev_val = max(5, min(int(max_lev), math.floor(budget / max(0.7, sl_pct))))
                # Intraday Targets: reachable in 1-3 hours
                target_tp1_pct = round(max(1.10, min(1.60, sl_pct * 1.4)), 2)
                target_tp2_pct = round(target_tp1_pct * 1.7, 2)
                tp1 = entry * (1.0 + target_tp1_pct / 100.0) if is_long else entry * (1.0 - target_tp1_pct / 100.0)
                tp2 = entry * (1.0 + target_tp2_pct / 100.0) if is_long else entry * (1.0 - target_tp2_pct / 100.0)
            else:  # 1h, 4h
                setup_type = "STANDARD_SWING"
                max_lev = 12.0 if is_btc else (10.0 if is_eth else 8.0)
                budget = 15.0 if is_btc else (14.0 if is_eth else 12.0)
                rec_lev_val = max(4, min(int(max_lev), math.floor(budget / max(0.9, sl_pct))))
                # Swing Targets: reachable in 4-8 hours
                target_tp1_pct = round(max(1.80, min(2.80, sl_pct * 1.6)), 2)
                target_tp2_pct = round(target_tp1_pct * 1.8, 2)
                tp1 = entry * (1.0 + target_tp1_pct / 100.0) if is_long else entry * (1.0 - target_tp1_pct / 100.0)
                tp2 = entry * (1.0 + target_tp2_pct / 100.0) if is_long else entry * (1.0 - target_tp2_pct / 100.0)

        # Compute breakeven trigger price at 1.0R
        be_dist = abs(entry - initial_sl) * getattr(settings, 'breakeven_trigger_r_ratio', 1.0)
        breakeven_price = (entry + be_dist) if is_long else (entry - be_dist)

        close_ratio = getattr(settings, 'tp1_close_ratio', 0.70)
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


