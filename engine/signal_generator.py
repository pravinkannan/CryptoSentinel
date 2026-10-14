"""
SignalGeneratorService orchestrates pattern recognition, risk management, 
and technical indicator computation for institutional trade signal generation.
"""

import logging
from datetime import datetime, timezone
import pandas as pd
import pandas_ta as ta
from typing import Dict, List, Optional, Any, Tuple
from core.config import settings
from core.models import SignalData
from engine.pattern_recognition import PatternRecognitionService
from engine.risk_manager import RiskManager
from data.market_data_service import scan_liquidity_magnets, enrich_magnets_with_actions

logger = logging.getLogger(__name__)


class SignalGeneratorService:
    """Service to generate trading signals and technical analysis from DataFrames."""

    def __init__(
        self, 
        percentage_trigger: float, 
        static_alerts: Dict[str, List[float]],
        atr_period: int = 14, 
        sl_atr_multiplier: float = 1.5, 
        target_rr_ratio: float = 2.5
    ) -> None:
        self.percentage_trigger: float = percentage_trigger
        self.static_alerts: Dict[str, List[float]] = static_alerts
        self.atr_period: int = atr_period
        
        self.pattern_recognition = PatternRecognitionService(percentage_trigger=percentage_trigger)
        self.risk_manager = RiskManager(
            atr_period=atr_period, 
            sl_atr_multiplier=sl_atr_multiplier,
            target_rr_ratio=target_rr_ratio
        )

    def get_dynamic_levels(self, current_price: float, symbol: str, df: Optional[pd.DataFrame] = None) -> Dict[str, float]:
        """Dynamically calculate psychological and historical support/resistance levels."""
        levels: Dict[str, float] = {}
        if current_price <= 0:
            return levels
            
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
            levels["24h High"] = float(recent_df['high'].max())
            levels["24h Low"] = float(recent_df['low'].min())
        elif df is not None and len(df) > 0:
            levels["Recent High"] = float(df['high'].max())
            levels["Recent Low"] = float(df['low'].min())

        return levels

    def check_dynamic_levels(self, symbol: str, current_price: float, state_manager: Any, df: Optional[pd.DataFrame] = None) -> List[Tuple[str, float]]:
        """Check if price crossed dynamic levels, using state_manager to avoid spam."""
        alerts: List[Tuple[str, float]] = []
        if current_price <= 0:
            return alerts
            
        dynamic_levels = self.get_dynamic_levels(current_price, symbol, df)
        
        if symbol not in state_manager.state["last_crossed_alerts"]:
            state_manager.state["last_crossed_alerts"][symbol] = []
        
        active_alert_keys = set()
        for name, level in dynamic_levels.items():
            if level * 0.998 < current_price < level * 1.002:
                alert_key = f"{name}_{level}"
                active_alert_keys.add(alert_key)
                if alert_key not in state_manager.state["last_crossed_alerts"][symbol]:
                    alerts.append((name, level))
                    state_manager.state["last_crossed_alerts"][symbol].append(alert_key)

        # Prune alert keys that are no longer in the tolerance band so they can re-trigger on subsequent crosses
        existing_alerts = list(state_manager.state["last_crossed_alerts"][symbol])
        for key in existing_alerts:
            if key not in active_alert_keys:
                state_manager.state["last_crossed_alerts"][symbol].remove(key)

        return alerts

    def get_htf_trend(self, df: Optional[pd.DataFrame]) -> str:
        """Calculate high timeframe trend based on EMA 200 (with EMA 50 fallback)."""
        if df is None or len(df) < 50:
            return "Unknown (Macro Trend)"
            
        latest = df.iloc[-1]
        close = float(latest['close'])
        
        if len(df) >= 200:
            df.ta.ema(length=200, append=True)
            ema_val = float(df.iloc[-1].get('EMA_200', 0))
        else:
            df.ta.ema(length=50, append=True)
            ema_val = float(df.iloc[-1].get('EMA_50', 0))
        
        if ema_val > 0:
            if close > ema_val:
                return "Macro Uptrend (Bullish)"
            elif close < ema_val:
                return "Macro Downtrend (Bearish)"
        return "Macro Consolidation (Neutral)"

    def _compute_indicators(self, df: pd.DataFrame) -> None:
        """Compute TA indicators on the dataframe in place."""
        df.ta.ema(length=20, append=True)
        df.ta.ema(length=50, append=True)
        df.ta.rsi(length=14, append=True)
        df.ta.macd(fast=12, slow=26, signal=9, append=True)
        df.ta.atr(length=self.atr_period, append=True)
        df.ta.sma(close="volume", length=20, append=True, prefix="VOL")
        
        # Volatility & Squeeze
        df.ta.bbands(length=20, std=2, append=True)
        df.ta.kc(length=20, scalar=1.5, append=True)
        
        # VWAP (anchored on timestamp index)
        try:
            if 'timestamp' in df.columns:
                df['VWAP'] = df.set_index('timestamp').ta.vwap()
        except Exception:
            pass
        
        try:
            df.ta.cdl_pattern(name="all", append=True)
        except Exception:
            pass

    def analyze_patterns(
        self, 
        df: pd.DataFrame, 
        timeframe: str, 
        htf_trend: str, 
        htf_4h_trend: str, 
        microstructure: Dict[str, Any], 
        symbol: str, 
        btc_trend: str = "Unknown", 
        regime_data: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """Run complete quantitative TA logic and return a validated SignalData dictionary."""
        if df is None or len(df) < 50:
            return None
            
        if regime_data and regime_data.get("cooldown_active"):
            logger.info(f"[REJECT: COOLDOWN] Skipping {symbol} on {timeframe} due to active 15m whipsaw buffer.")
            return None

        # 1. Compute Indicators
        self._compute_indicators(df)

        latest = df.iloc[-1]
        entry = float(latest['close'])
        if entry <= 0:
            return None
            
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

        # Microstructure context added to rationale
        fr = float(microstructure.get('funding_rate', 0.0))
        imbalance = float(microstructure.get('imbalance_ratio', 1.0))
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

        # 4. Timeframe-Aware Target Horizon & Internal TTL
        ttl_mins = settings.trade_ttl_minutes
        if timeframe == '5m':
            target_resolution = "30 – 60 Mins (Intraday Scalp)"
            expected_horizon_mins = 60
        elif timeframe == '15m':
            target_resolution = "1 – 3 Hours (Intraday)"
            expected_horizon_mins = 180
        elif timeframe == '1h':
            target_resolution = "4 – 8 Hours (Swing)"
            expected_horizon_mins = 480
        elif timeframe == '4h':
            target_resolution = "12 – 24 Hours (Macro Swing)"
            expected_horizon_mins = 1440
        else:
            target_resolution = f"4 – {int(settings.trade_ttl_hours)} Hours"
            expected_horizon_mins = settings.trade_ttl_minutes

        if regime == "HIGH_VOLATILITY":
            target_resolution += " (Fast Momentum)"

        expiration_time = (pd.to_datetime(latest['timestamp']) + pd.Timedelta(minutes=expected_horizon_mins) + pd.Timedelta(hours=5, minutes=30)).strftime('%d %b %H:%M IST')

        # 5. Extract Advanced Quantitative & Structural Metrics
        rsi_val = float(latest.get('RSI_14', 50.0))
        vol = float(latest.get('volume', 0.0))
        vol_sma = float(latest.get('VOL_SMA_20', 0.0))
        prev_candle = df.iloc[-2] if len(df) >= 2 else latest
        prev_vol = float(prev_candle.get('volume', 0.0))

        # Pace adjustment for forming candles to prevent premature anemic volume rejections
        TF_SECONDS = {'5m': 300, '15m': 900, '1h': 3600, '4h': 14400}
        tf_sec = TF_SECONDS.get(timeframe, 900)
        try:
            c_time = pd.to_datetime(latest['timestamp'])
            if c_time.tzinfo is None:
                c_time = c_time.tz_localize(timezone.utc)
            elapsed_sec = max(30.0, (datetime.now(timezone.utc) - c_time).total_seconds())
            pace_factor = max(1.0, min(6.0, tf_sec / elapsed_sec))
            projected_vol = vol * pace_factor
        except Exception:
            projected_vol = vol

        effective_vol = max(vol, projected_vol, prev_vol)
        volume_ratio = round(effective_vol / vol_sma, 2) if vol_sma > 0 else 1.0
        volume_usd = round(effective_vol * entry, 2)
        candle_open = float(latest.get('open', entry))

        bbl = float(latest.get('BBL_20_2.0', 0.0))
        bbu = float(latest.get('BBU_20_2.0', 0.0))
        prev_low = float(df.iloc[-2]['low']) if len(df) >= 2 else entry
        prev_high = float(df.iloc[-2]['high']) if len(df) >= 2 else entry
        bb_bounce = False
        if direction == "Bullish" and bbl > 0 and prev_low <= bbl and entry > bbl:
            bb_bounce = True
        elif direction == "Bearish" and bbu > 0 and prev_high >= bbu and entry < bbu:
            bb_bounce = True

        levels = self.get_dynamic_levels(entry, symbol, df)
        if entry > 0 and levels:
            min_dist_pct = min(abs(entry - lvl) / entry for lvl in levels.values())
            dynamic_level_reaction = any(abs(entry - lvl) / entry <= 0.003 for lvl in levels.values())
        else:
            min_dist_pct = 1.0
            dynamic_level_reaction = False

        is_rejection_pattern = any(
            any(pat in s.lower() for pat in ["hammer", "rejection", "engulfing", "piercing", "marubozu", "belthold", "fvg", "crossover", "star", "cloud"])
            for s in signals
        )
        key_level_rejection = dynamic_level_reaction or bb_bounce or is_rejection_pattern
        dist_to_key_level_pct = round(min_dist_pct, 4) if dynamic_level_reaction else (0.0 if (bb_bounce or is_rejection_pattern) else round(min_dist_pct, 4))

        # Order book depth & microstructure flags
        bids = order_book.get('bids', [])
        asks = order_book.get('asks', [])
        total_book_depth_usd = round(sum(b[0] * b[1] for b in bids) + sum(a[0] * a[1] for a in asks), 2) if (bids or asks) else 250000.0

        is_long = direction in ["Bullish", "LONG", "BUY"]
        is_short = direction in ["Bearish", "SHORT", "SELL"]

        # 4H macro conflict is enforced strictly for higher timeframes (1h, 4h).
        # Scalping timeframes (5m, 15m) are permitted to trade mean-reversions and intraday counter-trend bounces.
        if timeframe in ['5m', '15m']:
            macro_4h_conflict = False
        else:
            macro_4h_conflict = (htf_4h_trend.startswith('Macro Down') and is_long) or (htf_4h_trend.startswith('Macro Up') and is_short)

        macro_trend_aligned = (
            (htf_trend.startswith('Macro Up') and htf_4h_trend.startswith('Macro Up') and is_long) or
            (htf_trend.startswith('Macro Down') and htf_4h_trend.startswith('Macro Down') and is_short)
        )
        ob_defensive_shield = any(m.get('role') == 'DEFENSIVE_SHIELD' for m in liquidity_magnets)
        blocking_ob_wall = any(m.get('role') in ['BLOCKING_RESISTANCE', 'BLOCKING_SUPPORT'] for m in liquidity_magnets)

        # 6. Build and Validate Typed Signal Payload
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
            roe_tp1=risk_params.get('roe_tp1', risk_params['roe_tp']),
            roe_tp2=risk_params.get('roe_tp2', 0.0),
            overall_tp2_roe=risk_params.get('overall_tp2_roe', 0.0),
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
            liquidity_magnets=liquidity_magnets,
            rsi_val=rsi_val,
            volume_ratio=volume_ratio,
            volume_usd=volume_usd,
            dynamic_level_reaction=dynamic_level_reaction,
            bb_bounce=bb_bounce,
            open=candle_open,
            vol_sma_20=vol_sma,
            volume=effective_vol,
            key_level_rejection=key_level_rejection,
            dist_to_key_level_pct=dist_to_key_level_pct,
            total_book_depth_usd=total_book_depth_usd,
            ob_defensive_shield=ob_defensive_shield,
            macro_trend_aligned=macro_trend_aligned,
            macro_4h_conflict=macro_4h_conflict,
            blocking_ob_wall=blocking_ob_wall
        ).model_dump()
