import pandas as pd
from datetime import datetime, timezone, timedelta
import logging
from core.models import RegimeData

logger = logging.getLogger(__name__)

class RegimeDetector:
    def __init__(self):
        # symbol -> { "phase": "NORMAL", "start_time": datetime, "base_atr": float }
        self.state = {} 

    def detect_regime(self, symbol: str, df_5m: pd.DataFrame) -> dict:
        if symbol not in self.state:
            self.state[symbol] = {"phase": "NORMAL", "start_time": None, "base_atr": 0.0}

        if df_5m is None or len(df_5m) < 20:
            return RegimeData(regime="NORMAL", phase="NORMAL").model_dump()

        if 'atr_14' not in df_5m.columns:
            return RegimeData(regime="NORMAL", phase="NORMAL").model_dump()

        current_time = datetime.now(timezone.utc)
        state = self.state[symbol]
        
        baseline_atr = df_5m['atr_14'].rolling(window=20).mean().iloc[-1]
        current_atr = df_5m['atr_14'].iloc[-1]
        
        # Calculate volume metrics
        vol_ma = df_5m['volume'].rolling(window=20).mean().iloc[-1]
        current_vol = df_5m['volume'].iloc[-1]
        
        is_cooldown_active = False
        catalyst_info = ""

        # Check for FAST EXIT (Hysteresis / Consolidation)
        if state["phase"] != "NORMAL":
            last_3 = df_5m.tail(3)
            # Check tight range < 0.4%
            ranges = (last_3['high'] - last_3['low']) / last_3['low'] * 100
            tight_range = all(r < 0.4 for r in ranges)
            vol_drop = all(v < vol_ma for v in last_3['volume'])
            
            if tight_range and vol_drop:
                logger.info(f"[{symbol}] Fast Exit Triggered: Tight consolidation detected. Reverting to NORMAL.")
                self.state[symbol] = {"phase": "NORMAL", "start_time": None, "base_atr": 0.0}
                state = self.state[symbol]

        # Check triggers based on current state
        if state["phase"] == "NORMAL":
            if baseline_atr > 0 and current_atr >= (baseline_atr * 2.5):
                logger.warning(f"[{symbol}] PHASE 1: IMPULSE SHOCK triggered (ATR expanded >= 2.5x).")
                self.state[symbol] = {
                    "phase": "PHASE 1", 
                    "start_time": current_time, 
                    "base_atr": baseline_atr
                }
                state = self.state[symbol]
                
        elif state["phase"] == "PHASE 1":
            elapsed = (current_time - state["start_time"]).total_seconds() / 60.0
            if elapsed >= 10:
                logger.info(f"[{symbol}] Moving to PHASE 2: MOMENTUM CONTINUATION.")
                state["phase"] = "PHASE 2"
            
        elif state["phase"] == "PHASE 2":
            elapsed = (current_time - state["start_time"]).total_seconds() / 60.0
            
            # Check Phase 3 trigger
            wick_pct = 0.0
            candle_range = df_5m['high'].iloc[-1] - df_5m['low'].iloc[-1]
            if candle_range > 0:
                close = df_5m['close'].iloc[-1]
                open_price = df_5m['open'].iloc[-1]
                upper_wick = df_5m['high'].iloc[-1] - max(open_price, close)
                lower_wick = min(open_price, close) - df_5m['low'].iloc[-1]
                wick_pct = max(upper_wick, lower_wick) / candle_range
            
            if elapsed >= 30:
                if current_vol > (2.5 * vol_ma) and wick_pct >= 0.50:
                    logger.info(f"[{symbol}] Moving to PHASE 3: EXHAUSTION & REVERSAL.")
                    state["phase"] = "PHASE 3"
            
        elif state["phase"] == "PHASE 3":
            elapsed = (current_time - state["start_time"]).total_seconds() / 60.0
            if elapsed >= 60:
                logger.info(f"[{symbol}] Volatility lifecycle complete. Reverting to NORMAL.")
                state["phase"] = "NORMAL"

        # Apply state effects
        regime = "NORMAL"
        if state["phase"] == "PHASE 1":
            regime = "HIGH_VOLATILITY"
            is_cooldown_active = True
            catalyst_info = "PHASE 1: IMPULSE SHOCK - Hard blocking new entries."
        elif state["phase"] == "PHASE 2":
            regime = "HIGH_VOLATILITY"
            catalyst_info = "PHASE 2: MOMENTUM CONTINUATION - Trend only."
        elif state["phase"] == "PHASE 3":
            regime = "HIGH_VOLATILITY"
            catalyst_info = "PHASE 3: EXHAUSTION & REVERSAL - Reversals enabled."

        return RegimeData(
            regime=regime,
            cooldown_active=is_cooldown_active,
            catalyst_info=catalyst_info,
            phase=state["phase"]
        ).model_dump()
