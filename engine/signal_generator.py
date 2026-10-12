"""
SignalGeneratorService orchestrates pattern recognition, risk management, and technical indicator computation.
"""

import logging
import pandas as pd
import pandas_ta as ta
import re
from typing import Dict, List, Optional, Any
from core.config import settings
from core.models import SignalData
from engine.pattern_recognition import PatternRecognitionService
from engine.risk_manager import RiskManager
from data.market_data_service import scan_liquidity_magnets, enrich_magnets_with_actions

logger = logging.getLogger(__name__)

class SignalGeneratorService:
    """Service to generate trading signals and technical analysis from DataFrames."""

    def __init__(self, percentage_trigger: float, static_alerts: Dict[str, List[float]],
                 atr_period: int = 14, sl_atr_multiplier: float = 1.5, target_rr_ratio: float = 2.5):
        self.percentage_trigger = percentage_trigger
        self.static_alerts = static_alerts
        self.atr_period = atr_period
        
        self.pattern_recognition = PatternRecognitionService(percentage_trigger=percentage_trigger)
        self.risk_manager = RiskManager(atr_period=atr_period, sl_atr_multiplier=sl_atr_multiplier)

    def get_dynamic_levels(self, current_price: float, symbol: str, df: Optional[pd.DataFrame] = None) -> Dict[str, float]:
        """Dynamically calculate psychological and historical support/resistance levels."""
        levels: Dict[str, float] = {}
        step: float = 1.0
        
        if current_price > 10000: step = 1000.0
        elif current_price > 1000: step = 100.0
        elif current_price > 100: step = 10.0
        elif current_price > 10: step = 1.0
        else: step = 0.1
            
        nearest_round = round(current_price / step) * step
        levels[f"Psychological {nearest_round}"] = nearest_round
        
        if df is not None and len(df) >= 288:
            recent_df = df.tail(288)
            levels["24h High"] = recent_df['high'].max()
            levels["24h Low"] = recent_df['low'].min()
        elif df is not None and len(df) > 0:
            levels["Recent High"] = df['high'].max()
            levels["Recent Low"] = df['low'].min()

        return levels

    def check_dynamic_levels(self, symbol: str, current_price: float, state_manager: Any, df: Optional[pd.DataFrame] = None) -> List[tuple[str, float]]:
        """Check if price crossed dynamic levels, using state_manager to avoid spam."""
        alerts = []
        dynamic_levels = self.get_dynamic_levels(current_price, symbol, df)
        
        for name, level in dynamic_levels.items():
            if level * 0.998 < current_price < level * 1.002:
                alert_key = f"{name}_{level}"
                if alert_key not in state_manager.state["last_crossed_alerts"].get(symbol, []):
                    alerts.append((name, level))
                    if symbol not in state_manager.state["last_crossed_alerts"]:
                        state_manager.state["last_crossed_alerts"][symbol] = []
                    state_manager.state["last_crossed_alerts"][symbol].append(alert_key)
            else:
                alert_key = f"{name}_{level}"
                if symbol in state_manager.state["last_crossed_alerts"] and alert_key in state_manager.state["last_crossed_alerts"][symbol]:
                    state_manager.state["last_crossed_alerts"][symbol].remove(alert_key)
        return alerts

    def get_htf_trend(self, df: pd.DataFrame) -> str:
        """Calculate high timeframe trend based on EMA 200."""
        if df is None or len(df) < 200:
            return "Unknown (Macro Trend)"
            
        df.ta.ema(length=200, append=True)
        latest = df.iloc[-1]
        ema200 = latest.get('EMA_200', 0)
        close = latest['close']
        
        if ema200 > 0:
            if close > ema200:
                return "Macro Uptrend (Bullish)"
            elif close < ema200:
                return "Macro Downtrend (Bearish)"
        return "Macro Consolidation (Neutral)"

    def _compute_indicators(self, df: pd.DataFrame):
        df.ta.ema(length=20, append=True)
        df.ta.ema(length=50, append=True)
        df.ta.rsi(length=14, append=True)
        df.ta.macd(fast=12, slow=26, signal=9, append=True)
        df.ta.atr(length=self.atr_period, append=True)
        df.ta.sma(close="volume", length=20, append=True, prefix="VOL")
        
        # Volatility & Squeeze
        df.ta.bbands(length=20, std=2, append=True)
        df.ta.kc(length=20, scalar=1.5, append=True)
        
        # VWAP (Needs datetime index for anchoring, fallback to simple if it fails)
        try:
            df.set_index('timestamp').ta.vwap(append=True)
            df['VWAP'] = df.set_index('timestamp').ta.vwap()
        except Exception:
            pass
        
        try:
            df.ta.cdl_pattern(name="all", append=True)
        except Exception:
            pass

    def analyze_patterns(self, df: pd.DataFrame, timeframe: str, htf_trend: str, htf_4h_trend: str, microstructure: Dict[str, Any], symbol: str, btc_trend: str = "Unknown", regime_data: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Run complete TA logic on a dataframe and return a structured signal payload."""
        if df is None or len(df) < 50:
            return None
            
        if regime_data and regime_data.get("cooldown_active"):
            logger.info(f"[REJECT: COOLDOWN] Skipping {symbol} on {timeframe} due to active 15m whipsaw buffer.")
            return None

        # 1. Compute Indicators
        self._compute_indicators(df)

        latest = df.iloc[-1]
        entry = latest['close']
        regime = regime_data.get("regime", "NORMAL") if regime_data else "NORMAL"
        phase = regime_data.get("phase", "NORMAL") if regime_data else "NORMAL"

        # 2. Pattern Recognition (Decoupled)
        signals, rationale, direction, oi_delta_pct, oi_direction = self.pattern_recognition.detect_patterns(df, regime_data)
        
        if phase == "PHASE 2":
            allowed_patterns = ["Marubozu", "EMA Break", "Session Breakout", "FVG", "Fair Value Gap"]
            filtered_signals = [s for s in signals if any(ap.lower() in s.lower() for ap in allowed_patterns)]
            if not filtered_signals:
                logger.info(f"[REJECT: PHASE 2 FILTER] {symbol} rejected counter-trend patterns in momentum continuation.")
                return None
            signals = filtered_signals

        # Missing microstructure context added to rationale
        fr = microstructure.get('funding_rate', 0.0)
        imbalance = microstructure.get('imbalance_ratio', 1.0)
        if fr > 0.03: rationale.append(f"Funding rate is extremely high ({fr:.4f}%), indicating crowded longs.")
        elif fr < -0.03: rationale.append(f"Funding rate is extremely negative ({fr:.4f}%), indicating crowded shorts.")
        if imbalance > 1.5: rationale.append(f"OB shows strong buy imbalance ({imbalance:.2f}).")
        elif imbalance < 0.66: rationale.append(f"OB shows strong sell imbalance ({imbalance:.2f}).")

        # Scan for anomalous Liquidity Magnets in the order book
        order_book = microstructure.get('order_book', {})
        liquidity_magnets = scan_liquidity_magnets(
            order_book, 
            entry, 
            depth_pct=settings.liquidity_magnet_depth_pct, 
            multiplier=settings.liquidity_magnet_multiplier
        )
        # 3. Risk Management & Exits (Decoupled)
        risk_params = self.risk_manager.compute_risk_parameters(df, symbol, timeframe, direction, entry, regime)
        if not risk_params:
            return None

        # Enrich magnets with concrete tactical actions
        liquidity_magnets = enrich_magnets_with_actions(
            liquidity_magnets,
            direction=direction,
            entry=entry,
            stop_loss=risk_params['stop_loss'],
            take_profit=risk_params['tp1']
        )
        for m in liquidity_magnets:
            rationale.append(f"Liquidity magnet: {m['volume_formatted']} {m['side']} @ ${m['price']} -> {m.get('action')}")
            
        liq_zones = self.risk_manager.calculate_liquidation_zones(df)

        # 4. TTL / Target Resolution Window
        tf_match = re.match(r'(\d+)([mhdw])', timeframe)
        ttl_mins = settings.trade_ttl_minutes
        target_resolution = f"4 - {int(settings.trade_ttl_hours)} Hours"
        if regime == "HIGH_VOLATILITY":
            target_resolution = f"2 - {int(settings.trade_ttl_hours)} Hours (Fast Momentum)"
            ttl_mins = settings.trade_ttl_minutes
        else:
            if tf_match:
                val = int(tf_match.group(1))
                unit = tf_match.group(2)
                if unit == 'h' and val >= 4:
                    ttl_mins = max(settings.trade_ttl_minutes, val * 60 * 12)
                    target_resolution = f"18 - {ttl_mins // 60} Hours"
                elif unit == 'd':
                    ttl_mins = max(settings.trade_ttl_minutes, val * 1440 * 5)
                    target_resolution = "Multi-Day Swing"
                else:
                    ttl_mins = settings.trade_ttl_minutes
                    target_resolution = f"4 - {int(settings.trade_ttl_hours)} Hours"

        expiration_time = (pd.to_datetime(latest['timestamp']) + pd.Timedelta(minutes=ttl_mins) + pd.Timedelta(hours=5, minutes=30)).strftime('%d %b %H:%M IST')

        # 5. Build and Validate Typed Signal Payload
        return SignalData(
            timestamp=latest['timestamp'].strftime('%Y-%m-%d %H:%M:%S UTC'),
            timeframe=timeframe,
            htf_trend=htf_trend,
            htf_4h_trend=htf_4h_trend,
            btc_trend=btc_trend,
            close=entry,
            patterns=signals,
            direction=direction,
            strategy_rationale=" ".join(rationale),
            entry=round(entry, 2),
            stop_loss=risk_params['stop_loss'],
            take_profit=risk_params['take_profit'],
            tp1=risk_params['tp1'],
            tp2=risk_params['tp2'],
            sl_pct=risk_params['sl_pct'],
            tp_pct=risk_params['tp_pct'],
            tp1_pct=risk_params['tp1_pct'],
            tp2_pct=risk_params['tp2_pct'],
            rr_ratio=risk_params['rr_ratio'],
            roe_sl=risk_params['roe_sl'],
            roe_tp=risk_params['roe_tp'],
            atr_value=risk_params['atr_value'],
            ttl_mins=ttl_mins,
            expiration_time=expiration_time,
            target_resolution=target_resolution,
            recommended_leverage=risk_params['recommended_leverage'],
            leverage_val=risk_params['leverage_val'],
            short_liq_zones=", ".join(map(str, liq_zones['short_liq_zones'])) or "None",
            long_liq_zones=", ".join(map(str, liq_zones['long_liq_zones'])) or "None",
            funding_rate=fr,
            imbalance_ratio=imbalance,
            regime=regime,
            catalyst_info=regime_data.get('catalyst_info', '') if regime_data else '',
            oi_delta_pct=round(oi_delta_pct, 2),
            oi_direction=oi_direction,
            liquidity_magnets=liquidity_magnets
        ).model_dump()
