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


class RiskManager:
    """Service to compute risk parameters, stops, targets, and leverage."""
    
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
        regime: str
    ) -> Optional[Dict[str, Any]]:
        """
        Calculate stop loss, take profit targets, risk/reward ratios, and leverage.
        Returns None if risk parameters or zero-entry inputs fail validation.
        """
        if df is None or df.empty or entry <= 0.0 or direction not in ["Bullish", "Bearish"]:
            return None
            
        latest = df.iloc[-1]
        atr = float(latest.get(f'ATRr_{self.atr_period}', entry * 0.02))
        
        # Timeframe-adaptive risk configuration for fast resolution on scalps
        if timeframe == '5m':
            lookback = 5
            atr_mult = 0.5
            roe_risk_budget = 20.0
        elif timeframe == '15m':
            lookback = 8
            atr_mult = 0.75
            roe_risk_budget = 18.0
        elif timeframe == '1h':
            lookback = 12
            atr_mult = 1.0
            roe_risk_budget = 16.0
        else:
            lookback = 15
            atr_mult = self.sl_atr_multiplier
            roe_risk_budget = 15.0

        recent_low = float(df['low'].tail(lookback).min())
        recent_high = float(df['high'].tail(lookback).max())

        if direction == "Bullish":
            stop_loss = recent_low - (atr * atr_mult)
            risk = (entry - stop_loss) if entry > stop_loss else (entry * 0.005)
            tp1 = entry + (risk * settings.tp1_rr_ratio)
            tp2 = entry + (risk * settings.tp2_rr_ratio)
            take_profit = tp1
        else:  # Bearish
            stop_loss = recent_high + (atr * atr_mult)
            risk = (stop_loss - entry) if stop_loss > entry else (entry * 0.005)
            tp1 = entry - (risk * settings.tp1_rr_ratio)
            tp2 = entry - (risk * settings.tp2_rr_ratio)
            take_profit = tp1

        sl_pct = abs(entry - stop_loss) / entry * 100.0

        # Anti-Noise-Wick Floor: Ensure SL has at least 0.55% buffer on scalps to avoid exchange noise sweeps
        min_sl_pct = 0.55 if timeframe in ['5m', '15m'] else 0.85
        if sl_pct < min_sl_pct:
            risk = entry * (min_sl_pct / 100.0)
            if direction == "Bullish":
                stop_loss = entry - risk
                tp1 = entry + (risk * settings.tp1_rr_ratio)
                tp2 = entry + (risk * settings.tp2_rr_ratio)
                take_profit = tp1
            else:  # Bearish
                stop_loss = entry + risk
                tp1 = entry - (risk * settings.tp1_rr_ratio)
                tp2 = entry - (risk * settings.tp2_rr_ratio)
                take_profit = tp1
            sl_pct = min_sl_pct

        tp1_pct = abs(tp1 - entry) / entry * 100.0
        tp2_pct = abs(tp2 - entry) / entry * 100.0
        tp_pct = tp1_pct
        
        # Mandatory Risk Parameters & Regime Logic
        if regime == "HIGH_VOLATILITY":
            if sl_pct > settings.sl_pct_max_high_volatility or sl_pct < settings.sl_pct_min_high_volatility:
                logger.info(f"[REJECT: REGIME_SL_RANGE] sl_pct={sl_pct:.2f}% (Limit {settings.sl_pct_min_high_volatility}%-{settings.sl_pct_max_high_volatility}%) on {symbol} {timeframe}")
                return None
        else:
            max_sl = 3.50 if timeframe == '1h' else settings.sl_pct_max_normal
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

        # Timeframe-Adaptive Leverage Calculation (Capped at 33x for scalps)
        if regime == "HIGH_VOLATILITY":
            rec_lev_val = max(5, min(15, math.floor(roe_risk_budget / sl_pct)))
        else:
            max_lev = 33 if timeframe in ['5m', '15m'] else (35 if ("BTC" in symbol or "ETH" in symbol) else 25)
            rec_lev_val = max(5, min(max_lev, math.floor(roe_risk_budget / sl_pct)))
            
        rr_ratio = round(tp_pct / sl_pct, 2) if sl_pct > 0 else 0.0
        roe_sl = round(sl_pct * rec_lev_val, 2)
        roe_tp = round(tp_pct * rec_lev_val, 2)
        roe_tp1 = round(tp1_pct * rec_lev_val, 2)
        roe_tp2 = round(tp2_pct * rec_lev_val, 2)
        overall_tp2_roe = round((0.5 * tp1_pct + 0.5 * tp2_pct) * rec_lev_val, 2)
        
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
            'recommended_leverage': f"{rec_lev_val}x"
        }
