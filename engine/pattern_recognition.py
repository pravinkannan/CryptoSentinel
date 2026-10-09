import logging
import pandas as pd
from typing import List, Dict, Any, Tuple

logger = logging.getLogger(__name__)

class PatternRecognitionService:
    """Service dedicated to technical pattern and volume detection."""
    
    def __init__(self, percentage_trigger: float):
        self.percentage_trigger = percentage_trigger

    def detect_patterns(self, df: pd.DataFrame, regime_data: dict = None) -> Tuple[List[str], List[str], str, float, str]:
        """
        Detect candlestick patterns, momentum signals, and divergences.
        Returns: (signals, rationale, direction, oi_delta_pct, oi_direction)
        """
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        
        signals: List[str] = []
        rationale: List[str] = []

        vol_is_high = latest['volume'] > latest.get('VOL_SMA_20', 0)
        
        # CDL Signals
        cdl_columns = [col for col in df.columns if col.startswith("CDL_")]
        if cdl_columns:
            for col in cdl_columns:
                val = latest.get(col, 0)
                if val != 0:
                    pattern_name = col.replace("CDL_", "").replace("_", " ").title()
                    dir_str = "Bullish" if val > 0 else "Bearish"
                    
                    # STRICT FILTERING
                    strong_patterns = [
                        "Engulfing", "Hammer", "Morning", "Evening", 
                        "Piercing", "Dark Cloud", "Marubozu", 
                        "Three White", "Three Black", "Abandoned", "Belt"
                    ]
                    if not any(strong in pattern_name for strong in strong_patterns):
                        continue
                        
                    if "ENGULFING" in col:
                        signals.append(f"{dir_str} Engulfing")
                        if vol_is_high:
                            rationale.append(f"A {dir_str} Engulfing formed with high volume, confirming momentum.")
                    elif "HAMMER" in col:
                        signals.append(f"{dir_str} Hammer / Rejection")
                        rationale.append("A Hammer indicates strong price rejection.")
                    else:
                        signals.append(f"{dir_str} {pattern_name}")
                        rationale.append(f"A {dir_str} {pattern_name} was detected.")
        else:
            if prev['close'] < prev['open'] and latest['close'] > latest['open'] and latest['close'] > prev['open'] and latest['open'] < prev['close']:
                signals.append("Bullish Engulfing (Fallback)")
            elif prev['close'] > prev['open'] and latest['close'] < latest['open'] and latest['close'] < prev['open'] and latest['open'] > prev['close']:
                signals.append("Bearish Engulfing (Fallback)")

        # EMA Crossover
        ema20, ema50 = latest.get('EMA_20', 0), latest.get('EMA_50', 0)
        prev_ema20, prev_ema50 = prev.get('EMA_20', 0), prev.get('EMA_50', 0)
        if prev_ema20 and prev_ema50 and ema20 and ema50:
            if prev_ema20 < prev_ema50 and ema20 > ema50:
                signals.append("Bullish 20/50 EMA Crossover")
            elif prev_ema20 > prev_ema50 and ema20 < ema50:
                signals.append("Bearish 20/50 EMA Crossover")

        # Percentage Move
        pct_move = abs(latest['close'] - latest['open']) / latest['open'] * 100
        if pct_move >= self.percentage_trigger:
            dir_str = "Up" if latest['close'] > latest['open'] else "Down"
            signals.append(f"Massive Move {dir_str} ({pct_move:.2f}%)")

        direction = "Neutral"
        if any(kw in s for s in signals for kw in ["Bullish", "Hammer", "Oversold", "Up"]): direction = "Bullish"
        if any(kw in s for s in signals for kw in ["Bearish", "Overbought", "Down"]): direction = "Bearish"

        # FVG Tap & Go Setup
        if len(df) >= 4 and regime_data and regime_data.get("regime") == "HIGH_VOLATILITY" and regime_data.get("phase") == "PHASE 2":
            c1, c2, c3 = df.iloc[-4], df.iloc[-3], df.iloc[-2]
            c4 = df.iloc[-1]
            if c1['high'] < c3['low']:
                fvg_top, fvg_bot = c3['low'], c1['high']
                if c4['low'] <= fvg_top and c4['close'] > fvg_top:
                    signals.append("Bullish FVG Tap & Go (Momentum Continuation)")
                    rationale.append("Price tapped into Bullish FVG imbalance and closed back out, confirming continuation.")
                    direction = "Bullish"
            elif c1['low'] > c3['high']:
                fvg_top, fvg_bot = c1['low'], c3['high']
                if c4['high'] >= fvg_bot and c4['close'] < fvg_bot:
                    signals.append("Bearish FVG Tap & Go (Momentum Continuation)")
                    rationale.append("Price tapped into Bearish FVG imbalance and closed back out, confirming continuation.")
                    direction = "Bearish"

        # Open Interest Delta Calculation
        oi_delta_pct = 0.0
        oi_direction = "Neutral"
        if 'open_interest' in df.columns and len(df) > 3:
            past_oi = df.iloc[-4]['open_interest']
            current_oi = latest['open_interest']
            if past_oi > 0:
                oi_delta_pct = ((current_oi - past_oi) / past_oi) * 100
                if oi_delta_pct > 0:
                    oi_direction = "Expanding"
                elif oi_delta_pct < 0:
                    oi_direction = "Declining"

        # RSI Divergence Detection
        if len(df) > 15:
            recent_low_idx = df['low'].iloc[-15:-1].idxmin()
            recent_high_idx = df['high'].iloc[-15:-1].idxmax()
            
            if latest['low'] <= df.loc[recent_low_idx, 'low'] and latest.get('RSI_14', 0) > df.loc[recent_low_idx].get('RSI_14', 0):
                signals.append("Bullish RSI Divergence")
            if latest['high'] >= df.loc[recent_high_idx, 'high'] and latest.get('RSI_14', 100) < df.loc[recent_high_idx].get('RSI_14', 100):
                signals.append("Bearish RSI Divergence")

        # Basic Indicators
        if latest.get('RSI_14', 50) < 30: signals.append("Oversold (RSI < 30)")
        elif latest.get('RSI_14', 50) > 70: signals.append("Overbought (RSI > 70)")

        # Volatility Squeeze Check
        bbl = latest.get('BBL_20_2.0', 0)
        bbu = latest.get('BBU_20_2.0', 0)
        kcl = latest.get('KCLe_20_1.5', latest.get('KCLs_20_1.5', 0))
        kcu = latest.get('KCUe_20_1.5', latest.get('KCUs_20_1.5', 0))
        is_squeezed = (bbl > kcl) and (bbu < kcu) if kcl and kcu else False
        if is_squeezed:
            rationale.append("Market is currently in a Volatility Squeeze (BB inside Keltner), preparing for expansion.")

        # VWAP Rejection Check
        vwap_val = latest.get('VWAP', 0)
        if vwap_val > 0:
            dist = abs(latest['close'] - vwap_val) / vwap_val * 100
            if dist < 0.5:
                rationale.append("Price is rejecting/testing the Session VWAP.")

        vol_text = "supported by higher-than-average volume" if vol_is_high else "on average/low volume"
        if rationale:
            rationale.append(f"This entire move is {vol_text}.")

        return signals, rationale, direction, oi_delta_pct, oi_direction
