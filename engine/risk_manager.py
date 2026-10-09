import logging
import math
import pandas as pd
from typing import Dict, List, Any
from core.config import settings

logger = logging.getLogger(__name__)

class RiskManager:
    """Service to handle SL, TP, leverage, and liquidation zones."""
    
    def __init__(self, atr_period: int = 14, sl_atr_multiplier: float = 1.5):
        self.atr_period = atr_period
        self.sl_atr_multiplier = sl_atr_multiplier

    def calculate_liquidation_zones(self, df: pd.DataFrame, window: int = 5) -> Dict[str, List[float]]:
        """Identify Swing Highs and Lows acting as Liquidation Zones."""
        if df is None or len(df) < window * 2 + 1:
            return {'short_liq_zones': [], 'long_liq_zones': []}
            
        short_zones, long_zones = [], []
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

    def compute_risk_parameters(self, df: pd.DataFrame, symbol: str, timeframe: str, direction: str, entry: float, regime: str) -> Dict[str, Any]:
        """
        Calculate stop loss, take profit targets, risk/reward ratios, and leverage.
        Returns None if risk parameters are rejected.
        """
        latest = df.iloc[-1]
        atr = latest.get(f'ATRr_{self.atr_period}', entry * 0.02)
        
        if direction not in ["Bullish", "Bearish"]:
            return None
        
        recent_low = df['low'].tail(15).min()
        recent_high = df['high'].tail(15).max()

        if direction == "Bullish":
            stop_loss = recent_low - (atr * self.sl_atr_multiplier)
            risk = entry - stop_loss if entry > stop_loss else entry * 0.01
            tp1 = entry + (risk * 1.5)
            tp2 = entry + (risk * 3.0)
            take_profit = tp1
        elif direction == "Bearish":
            stop_loss = recent_high + (atr * self.sl_atr_multiplier)
            risk = stop_loss - entry if stop_loss > entry else entry * 0.01
            tp1 = entry - (risk * 1.5)
            tp2 = entry - (risk * 3.0)
            take_profit = tp1
        else:
            stop_loss = entry * 0.95
            tp1 = entry * 1.05
            tp2 = entry * 1.10
            take_profit = tp1

        sl_pct = abs(entry - stop_loss) / entry * 100
        tp1_pct = abs(tp1 - entry) / entry * 100
        tp2_pct = abs(tp2 - entry) / entry * 100
        tp_pct = tp1_pct
        
        # Mandatory Risk Parameters & Regime Logic
        if regime == "HIGH_VOLATILITY":
            if sl_pct > settings.sl_pct_max_high_volatility or sl_pct < settings.sl_pct_min_high_volatility:
                logger.info(f"[REJECT: REGIME_SL_RANGE] sl_pct={sl_pct:.2f}% (Limit {settings.sl_pct_min_high_volatility}%-{settings.sl_pct_max_high_volatility}%) on {symbol} {timeframe}")
                return None
        else:
            if sl_pct > settings.sl_pct_max_normal or sl_pct < settings.sl_pct_min_normal:
                logger.info(f"[REJECT: SL_RANGE] sl_pct={sl_pct:.2f}% (Limit {settings.sl_pct_min_normal}%-{settings.sl_pct_max_normal}%) on {symbol} {timeframe}")
                return None
            
        if sl_pct == 0 or (tp_pct / sl_pct) < settings.min_acceptable_rr:
            rr_val = round(tp_pct/sl_pct, 2) if sl_pct > 0 else 0
            logger.info(f"[REJECT: LOW_RR] rr={rr_val} on {symbol} {timeframe}")
            return None
            
        # Timeframe limitations
        if timeframe in ['1h', '4h']:
            if timeframe == '4h':
                return None
            if timeframe == '1h' and sl_pct > 2.50:
                return None

        # Dynamic Leverage Calculation
        if regime == "HIGH_VOLATILITY":
            rec_lev_val = max(3, min(6, math.floor(15.0 / sl_pct)))
        else:
            max_lev = 150 if ("BTC" in symbol or "ETH" in symbol) else 100
            rec_lev_val = max(5, min(max_lev, math.floor(15.0 / sl_pct)))
            
        rr_ratio = round(tp_pct / sl_pct, 2) if sl_pct > 0 else 0.0
        roe_sl = round(sl_pct * rec_lev_val, 2)
        roe_tp = round(tp_pct * rec_lev_val, 2)
        
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
            'atr_value': round(atr, 4),
            'leverage_val': rec_lev_val,
            'recommended_leverage': f"{rec_lev_val}x"
        }
