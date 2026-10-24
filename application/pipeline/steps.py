"""
Discrete Pipeline Steps for Crypto Sentinel.
Implements IPipelineStep protocol:
- MarketDataIngestionStep: Ingestion, freshness validation, regime detection, trade updates.
- PatternRecognitionStep: Dynamic levels, microstructure, pattern recognition.
- ConfluenceEvaluationStep: Domain quantitative scoring, AI qualitative audit, opposing position checks.
- RiskAndPositionSizingStep: Tier recalibration, ATR buffers, take profit targets, funding rate filters.
- ExecutionDispatchStep: Order execution, state update, TradeOpenedEvent emission, Telegram notification.
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List

from application.pipeline.pipeline import IPipelineStep, SignalContext
from domain.confluence.engine import evaluate_confluence_score
from core.events import TradeOpenedEvent, SignalRejectedEvent
from core.config import settings

logger = logging.getLogger(__name__)


class MarketDataIngestionStep:
    """
    Step 1: Ingests market candles across timeframes, validates freshness via Heartbeat Watchdog,
    detects market regime, updates pending paper trades, and checks portfolio emergency guard.
    """

    def __init__(
        self,
        market_data: Any,
        regime_detector: Any,
        portfolio_manager: Any,
        paper_trader: Any,
        signal_generator: Any,
        state_manager: Any = None,
        journal_manager: Any = None,
        notifier: Any = None,
        ai_evaluator: Any = None,
    ) -> None:
        self.market_data = market_data
        self.regime_detector = regime_detector
        self.portfolio_manager = portfolio_manager
        self.paper_trader = paper_trader
        self.signal_generator = signal_generator
        self.state_manager = state_manager
        self.journal_manager = journal_manager
        self.notifier = notifier
        self.ai_evaluator = ai_evaluator

    async def process(self, context: SignalContext) -> Optional[SignalContext]:
        symbol = context.symbol
        now_utc = context.now_utc
        tf = context.timeframe

        logger.info(f"🔍 [{symbol} {tf}] Analyzing market structure & indicators...")

        # 1. Fetch 5m candles and verify freshness
        df_5m = await self.market_data.fetch_ohlcv(symbol, '5m', limit=500)
        if df_5m is None or df_5m.empty:
            logger.info(f"⏸️ [{symbol} {tf}] Result: No trade - 5m market data unavailable.")
            return None

        latest_timestamp = df_5m['timestamp'].iloc[-1]
        if hasattr(latest_timestamp, 'tzinfo') and latest_timestamp.tzinfo is None:
            naive_age = (datetime.now() - latest_timestamp).total_seconds()
            if 0 <= naive_age <= 330:
                age = naive_age
            else:
                latest_timestamp = latest_timestamp.replace(tzinfo=timezone.utc)
                age = (now_utc - latest_timestamp).total_seconds()
        elif not hasattr(latest_timestamp, 'tzinfo'):
            try:
                import pandas as pd
                dt = pd.to_datetime(latest_timestamp)
                if getattr(dt, 'tzinfo', None) is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                age = (now_utc - dt).total_seconds()
            except Exception:
                age = 0.0
        else:
            age = (now_utc - latest_timestamp).total_seconds()

        if age > 330:
            logger.warning(f"[HEARTBEAT ERROR] Stale market data for {symbol}. Candle age: {age:.0f}s. Pausing signal dispatch.")
            logger.info(f"⏸️ [{symbol} {tf}] Result: No trade - Stale market data (age: {age:.0f}s > 330s).")
            return None

        context.raw_candles['5m'] = df_5m
        context.current_price = float(df_5m['close'].iloc[-1])

        # 2. Macro trends from HTF candles
        df_htf = await self.market_data.fetch_ohlcv(symbol, '1h', limit=250)
        context.raw_candles['1h'] = df_htf
        context.htf_trend = self.signal_generator.get_htf_trend(df_htf) if df_htf is not None else "Unknown"

        df_4h = await self.market_data.fetch_ohlcv(symbol, '4h', limit=250)
        context.raw_candles['4h'] = df_4h
        context.htf_4h_trend = self.signal_generator.get_htf_trend(df_4h) if df_4h is not None else "Unknown"

        # 3. Market Regime detection
        regime_data = self.regime_detector.detect_regime(symbol, df_5m)
        context.regime_data = regime_data

        # 4. Emergency Guard Check
        if regime_data.get('regime') == "HIGH_VOLATILITY" and regime_data.get('phase') == "PHASE 1":
            if self.portfolio_manager:
                await self.portfolio_manager.check_emergency_guard(
                    symbol, regime_data.get('regime'), regime_data.get('phase'), context.current_price
                )

        # 5. Update pending paper trades for symbol (once per symbol cycle on primary timeframe)
        is_primary_tf = not getattr(settings, 'timeframes', None) or context.timeframe == settings.timeframes[0]
        if is_primary_tf and context.current_price > 0 and self.paper_trader:
            await self.paper_trader.update_trades(
                symbol, df_5m, self.journal_manager, self.notifier, self.ai_evaluator, self.market_data, self.state_manager
            )

        return context


class PatternRecognitionStep:
    """
    Step 2: Checks dynamic levels, fetches order book microstructure, and scans candlestick
    and indicator patterns via SignalGeneratorService.
    """

    def __init__(
        self,
        signal_generator: Any,
        market_data: Any,
        state_manager: Any = None,
        notifier: Any = None,
        journal_manager: Any = None,
    ) -> None:
        self.signal_generator = signal_generator
        self.market_data = market_data
        self.state_manager = state_manager
        self.notifier = notifier
        self.journal_manager = journal_manager

    async def process(self, context: SignalContext) -> Optional[SignalContext]:
        tf = context.timeframe
        symbol = context.symbol
        df = context.raw_candles.get(tf)
        if df is None or df.empty:
            df = await self.market_data.fetch_ohlcv(context.symbol, tf)
            if df is None or df.empty:
                logger.info(f"⏸️ [{symbol} {tf}] Result: No trade - Candle data unavailable for timeframe.")
                return None
            context.raw_candles[tf] = df

        current_price = float(df['close'].iloc[-1])
        context.current_price = current_price

        # Check Dynamic Levels on base timeframe (maintains situational awareness)
        if tf == settings.timeframes[0] and self.state_manager:
            crossed_levels = self.signal_generator.check_dynamic_levels(
                context.symbol, current_price, self.state_manager, df
            )
            for name, level in crossed_levels:
                logger.info(f"Dynamic level '{name}' ({level}) crossed for {context.symbol}")
                if getattr(settings, 'enable_dynamic_level_alerts', True) and self.notifier:
                    last_alert_times = self.state_manager.state.setdefault("dynamic_level_alert_timestamps", {})
                    alert_tag = f"{context.symbol}_{name}"
                    last_ts_str = last_alert_times.get(alert_tag)
                    now_utc = context.now_utc
                    if last_ts_str:
                        try:
                            last_ts = datetime.fromisoformat(last_ts_str)
                            if (now_utc - last_ts).total_seconds() < settings.dynamic_level_cooldown_minutes * 60:
                                continue
                        except Exception:
                            pass
                    last_alert_times[alert_tag] = now_utc.isoformat()
                    await self.notifier.notify_dynamic_level(context.symbol, name, level, current_price)

        if context.circuit_breaker_active:
            logger.info(f"⏸️ [{context.symbol} {tf}] Result: No trade - Circuit breaker active (Drawdown limit).")
            return None

        # Microstructure & order book
        microstructure = await self.market_data.get_market_microstructure(context.symbol)
        context.microstructure = microstructure

        # Pattern Recognition
        signal_data = self.signal_generator.analyze_patterns(
            df,
            tf,
            context.htf_trend,
            context.htf_4h_trend,
            microstructure,
            context.symbol,
            context.btc_trend,
            context.regime_data
        )

        if not signal_data:
            logger.info(f"⏸️ [{context.symbol} {tf}] Result: No trade - No setup pattern detected.")
            return None

        context.signal_data = signal_data
        pattern_name = signal_data['patterns'][0] if signal_data.get('patterns') else 'Unknown'
        logger.info(f"🎯 [{context.symbol} {tf}] Pattern detected: {pattern_name} ({signal_data.get('direction', 'NEUTRAL')}). Evaluating confluence...")
        if self.journal_manager:
            signal_data['pattern_drift_status'] = await self.journal_manager.check_pattern_drift(pattern_name)

        return context


class ConfluenceEvaluationStep:
    """
    Step 3: Evaluates quantitative confluence score via domain/confluence/engine.py.
    If rejected or circuit breaker tripped, emits SignalRejectedEvent and returns None.
    If passes, handles active opposing position threat modes (defensive ratcheting vs bias flip)
    and overlays qualitative AI narrative validation.
    """

    def __init__(
        self,
        event_bus: Any = None,
        ai_evaluator: Any = None,
        journal_manager: Any = None,
        paper_trader: Any = None,
        state_manager: Any = None,
        notifier: Any = None,
        repository: Any = None,
    ) -> None:
        self.event_bus = event_bus
        self.ai_evaluator = ai_evaluator
        self.journal_manager = journal_manager
        self.paper_trader = paper_trader
        self.state_manager = state_manager
        self.notifier = notifier
        self.repository = repository

    async def process(self, context: SignalContext) -> Optional[SignalContext]:
        if not context.signal_data:
            return None

        symbol = context.symbol
        tf = context.timeframe
        signal_data = context.signal_data
        direction_raw = str(signal_data.get('direction', 'NEUTRAL')).upper()
        is_long = direction_raw in ['LONG', 'BULLISH', 'BUY']
        is_short = direction_raw in ['SHORT', 'BEARISH', 'SELL']
        direction = 'LONG' if is_long else 'SHORT' if is_short else 'NEUTRAL'

        ai_data = None
        confluence_res = None

        if self.ai_evaluator and hasattr(self.ai_evaluator, 'evaluate_signal'):
            ai_resp_str = await self.ai_evaluator.evaluate_signal(
                symbol, signal_data, context.news_data, context.regime_data
            )
            if ai_resp_str:
                try:
                    ai_data = json.loads(ai_resp_str)
                    direction = ai_data.get('trade_direction', direction)
                    final_score = round(float(ai_data.get('final_confidence_rating', 0.0)), 1)
                    tier = ai_data.get('tier', 'STANDARD')
                    verdict = ai_data.get('overall_verdict', 'APPROVE').upper()
                    circuit_breaker = (
                        ai_data.get('eval_trace', {}).get('stage1_passed') is False or
                        ai_data.get('circuit_breaker_tripped', False)
                    )
                    if "REJECT" in verdict or "SKIP" in verdict or tier == "REJECTED" or circuit_breaker:
                        reason = ai_data.get('strategy_analysis', 'Rejected by AI / Confluence evaluation')
                        summary_reason = reason.split('. ')[0].strip() if reason else 'Score below threshold'
                        logger.info(f"⏸️ [{symbol} {tf}] Result: No trade - Signal rejected (Score: {final_score}%): {summary_reason}")
                        if self.event_bus:
                            await self.event_bus.publish(SignalRejectedEvent(
                                symbol=symbol,
                                direction=direction,
                                score=final_score,
                                tier=tier,
                                reason=reason,
                                signal_data=signal_data
                            ))
                        near_miss_thresh = float(getattr(settings, 'near_miss_min_score', 75.0)) if settings else 75.0
                        if final_score >= near_miss_thresh and self.journal_manager:
                            await self.journal_manager.log_rejected_signal({
                                'timestamp': signal_data.get('timestamp', context.now_utc.isoformat()),
                                'symbol': symbol,
                                'timeframe': tf,
                                'direction': direction,
                                'pattern_name': signal_data.get('patterns', ['Unknown'])[0] if signal_data.get('patterns') else 'Unknown',
                                'confidence_score': final_score,
                                'eval_trace': ai_data.get('eval_trace', {}),
                                'scoring_checklist': ai_data.get('scoring_checklist', []),
                                'rejection_reason': reason
                            })
                        return None
                except Exception as e:
                    logger.debug(f"Could not parse AI response JSON: {e}")

        if ai_data is None:
            # Deterministic Domain Confluence Math (Direct Domain Engine Call)
            confluence_res = evaluate_confluence_score(symbol, signal_data)
            context.confluence_result = confluence_res
            final_score = confluence_res["final_score"]
            tier = confluence_res["tier"]
            circuit_breaker = confluence_res.get("circuit_breaker_tripped", False)

            if circuit_breaker or tier == "REJECTED":
                reason = confluence_res.get("rejection_reason") or confluence_res.get("fatal_flaw") or "Score below regime threshold"
                logger.info(f"⏸️ [{symbol} {tf}] Result: No trade - Domain confluence rejected (Score: {final_score}%): {reason}")
                if self.event_bus:
                    await self.event_bus.publish(SignalRejectedEvent(
                        symbol=symbol,
                        direction=direction,
                        score=final_score,
                        tier=tier,
                        reason=reason,
                        signal_data=signal_data
                    ))
                near_miss_thresh = float(getattr(settings, 'near_miss_min_score', 75.0)) if settings else 75.0
                if final_score >= near_miss_thresh and self.journal_manager:
                    await self.journal_manager.log_rejected_signal({
                        'timestamp': signal_data.get('timestamp', context.now_utc.isoformat()),
                        'symbol': symbol,
                        'timeframe': tf,
                        'direction': direction,
                        'pattern_name': signal_data.get('patterns', ['Unknown'])[0] if signal_data.get('patterns') else 'Unknown',
                        'confidence_score': final_score,
                        'eval_trace': confluence_res.get('eval_trace', {}),
                        'scoring_checklist': confluence_res.get('scoring_checklist', []),
                        'rejection_reason': reason
                    })
                return None

            ai_data = {
                "trade_direction": direction,
                "overall_verdict": "APPROVE",
                "strategy_analysis": f"Approved by Domain Confluence Engine ({final_score}% - {tier})",
                "final_confidence_rating": final_score,
                "tier": tier,
                "scoring_checklist": confluence_res.get("scoring_checklist", []),
                "eval_trace": confluence_res.get("eval_trace", {}),
                "is_degraded": False
            }

        context.evaluated_signal = ai_data

        # 3. Active Opposing Positions & Bias Flip Handling
        now = context.now_utc
        active_signals = {}
        if self.state_manager and hasattr(self.state_manager, 'state'):
            active_signals = self.state_manager.state.setdefault("active_signals", {})

        TF_RANKS = {'5m': 1, '15m': 2, '1h': 3, '4h': 4}
        curr_rank = TF_RANKS.get(tf, 2)

        # Prune inactive or closed signals from active_signals state
        for k, sig in list(active_signals.items()):
            if not k.startswith(f"{symbol}_"):
                continue
            try:
                sig_time = datetime.fromisoformat(sig['timestamp'])
                sig_ttl = sig.get('ttl_mins', getattr(settings, 'trade_ttl_minutes', 180))
                if (now - sig_time).total_seconds() > sig_ttl * 60:
                    del active_signals[k]
                    continue
            except Exception:
                continue

            sig_dir = sig.get('direction', '')
            if self.paper_trader and hasattr(self.paper_trader, 'is_trade_active'):
                try:
                    is_active = await self.paper_trader.is_trade_active(
                        symbol=symbol,
                        trade_id=sig.get('trade_id'),
                        direction=sig_dir
                    )
                    if is_active is False:
                        del active_signals[k]
                        continue
                except Exception:
                    pass

        # Check opposing open positions in DB & state
        db_open_positions = []
        if self.paper_trader and hasattr(self.paper_trader, 'get_open_positions'):
            db_open_positions = await self.paper_trader.get_open_positions(symbol)
        opposing_db_positions = [p for p in db_open_positions if p.get('direction') != direction]
        opposing_state_signals = [
            (k, s) for k, s in active_signals.items()
            if k.startswith(f"{symbol}_") and s.get('direction') and s.get('direction') != direction
        ]
        has_active_opposing = bool(opposing_db_positions or opposing_state_signals)

        # Dual-Regime Dispatch Floor
        eval_trace = ai_data.get('eval_trace') if isinstance(ai_data, dict) else {}
        breakdown = eval_trace.get('breakdown') if isinstance(eval_trace, dict) else {}
        is_reversal_setup = bool(
            (isinstance(breakdown, dict) and breakdown.get('key_level_rejection') and breakdown.get('rsi_divergence')) or
            (signal_data.get('key_level_rejection') and signal_data.get('rsi_divergence'))
        )
        dispatch_floor = float(getattr(settings, 'min_dispatch_confidence_reversal', 80.0)) if is_reversal_setup else float(getattr(settings, 'min_dispatch_confidence', 86.0))

        pattern_name = signal_data['patterns'][0] if signal_data.get('patterns') else 'Unknown'
        opposing_to_invalidate = []

        if has_active_opposing:
            defensive_threat_min = float(getattr(settings, 'defensive_threat_min_score', 80.0))
            bias_flip_min = float(getattr(settings, 'bias_flip_min_score', 88.0))

            if final_score < defensive_threat_min:
                logger.info(f"⏸️ [{symbol} {tf}] Result: No trade - Counter-trend noise ({final_score}%) opposes active position.")
                return None
            elif final_score < bias_flip_min:
                logger.warning(f"⚠️ Threat Defensive Mode triggered: {symbol} {tf} {direction} ({final_score}%) opposing active position. Ratcheting SL to BE.")
                for pos in opposing_db_positions:
                    entry = pos.get('entry', 0.0)
                    current_sl = pos.get('stop_loss', 0.0)
                    pos_dir = pos.get('direction', '')
                    if pos_dir.upper() == 'LONG':
                        be_price = round(entry * 1.001, 4 if entry < 10 else 2)
                        should_ratchet = (current_sl < be_price)
                    else:
                        be_price = round(entry * 0.999, 4 if entry < 10 else 2)
                        should_ratchet = (current_sl > be_price)
                    if should_ratchet and self.paper_trader:
                        await self.paper_trader.move_sl_to_be(str(pos['id']), be_price)
                        if self.notifier and hasattr(self.notifier, 'notify_defensive_shift'):
                            await self.notifier.notify_defensive_shift(
                                symbol=symbol,
                                active_direction=pos_dir,
                                opposing_direction=direction,
                                opposing_confidence=final_score,
                                be_price=be_price,
                                opposing_pattern=pattern_name
                            )

                for opp_k, sig in opposing_state_signals:
                    sig_entry = sig.get('entry', 0.0)
                    sig_dir = sig.get('direction', '')
                    if sig_entry > 0:
                        sig_be = round(sig_entry * 1.001, 4 if sig_entry < 10 else 2) if sig_dir == 'LONG' else round(sig_entry * 0.999, 4 if sig_entry < 10 else 2)
                        sig['stop_loss'] = sig_be

                logger.info(f"⏸️ [{symbol} {tf}] Result: No trade - Ratcheted opposing SL to BE (Threat defensive mode).")
                return None
            else:
                # Confirmed Bias Flip (>= 88.0%)
                vip_thresh = float(getattr(settings, 'vip_confidence_threshold', 92.0))
                skip_flip = False
                for opp_k, sig in opposing_state_signals:
                    sig_tf = sig.get('timeframe', opp_k.split('_')[-1])
                    sig_rank = TF_RANKS.get(sig_tf, 2)
                    if curr_rank < sig_rank and final_score < vip_thresh:
                        logger.info(f"⏸️ [{symbol} {tf}] Result: No trade - Counter-trend setup ({final_score}%) opposes active HTF {sig_tf}.")
                        skip_flip = True
                        break
                    else:
                        opposing_to_invalidate.append((opp_k, sig_tf, sig.get('direction', '')))
                if skip_flip:
                    return None
        else:
            if final_score < dispatch_floor:
                logger.info(f"⏸️ [{symbol} {tf}] Result: No trade - Score {final_score:.1f}% below {dispatch_floor:.1f}% dispatch floor.")
                return None

        context.metadata['opposing_to_invalidate'] = opposing_to_invalidate
        context.metadata['opposing_db_positions'] = opposing_db_positions
        return context


class RiskAndPositionSizingStep:
    """
    Step 4: Calibrates position risk, dynamic leverage, ATR buffers, and take-profit targets (TP1 & TP2).
    """

    def __init__(self, risk_manager: Any, state_manager: Any = None) -> None:
        self.risk_manager = risk_manager
        self.state_manager = state_manager

    async def process(self, context: SignalContext) -> Optional[SignalContext]:
        if not context.signal_data or not context.evaluated_signal:
            return None

        symbol = context.symbol
        tf = context.timeframe
        signal_data = context.signal_data
        ai_data = context.evaluated_signal
        direction = ai_data.get('trade_direction', 'NEUTRAL')
        confidence = float(ai_data.get('final_confidence_rating', 0.0))

        # Same-direction deduplication check
        signal_key = f"{symbol}_{tf}"
        if self.state_manager and hasattr(self.state_manager, 'state'):
            active_signals = self.state_manager.state.get("active_signals", {})
            if signal_key in active_signals and active_signals[signal_key].get('direction') == direction:
                sig = active_signals[signal_key]
                try:
                    sig_time = datetime.fromisoformat(sig['timestamp'])
                    if context.now_utc - sig_time < timedelta(minutes=60):
                        if confidence <= sig.get('confidence', 0):
                            logger.info(f"⏸️ [{symbol} {tf}] Result: No trade - Better or equal signal ({sig.get('confidence')}%) active.")
                            return None
                        else:
                            override_msg = f"🚨 OVERRIDE: Better setup detected ({confidence}% vs previous {sig.get('confidence')}%)."
                            logger.info(override_msg)
                            if 'strategy_rationale' in signal_data:
                                signal_data['strategy_rationale'] = f"{override_msg}\n\n{signal_data['strategy_rationale']}"
                except Exception:
                    pass

        vip_thresh = float(getattr(settings, 'vip_confidence_threshold', 92.0)) if settings else 92.0
        is_vip = (confidence >= vip_thresh)

        signal_data['symbol'] = symbol
        signal_data['timeframe'] = tf

        # Recalibrate for tier
        sized_data = self.risk_manager.recalibrate_for_tier(
            signal_data,
            is_vip=is_vip,
            liquidity_magnets=signal_data.get('liquidity_magnets', [])
        )

        # Funding rate filter check
        fr = float(sized_data.get('funding_rate', 0.0))
        if direction == 'SHORT' and fr < -0.04 and not is_vip:
            logger.info(f"⏸️ [{symbol} {tf}] Result: No trade - Adverse funding rate: {fr}%.")
            return None
        if direction == 'LONG' and fr > 0.06 and not is_vip:
            logger.info(f"⏸️ [{symbol} {tf}] Result: No trade - Adverse funding rate: {fr}%.")
            return None

        context.sized_order = sized_data
        return context


class ExecutionDispatchStep:
    """
    Step 5: Passes payload to PaperTradingService/Repository, emits TradeOpenedEvent to EventBus,
    and dispatches notifications to configured channels.
    """

    def __init__(
        self,
        paper_trader: Any,
        journal_manager: Any,
        notifier: Any,
        event_bus: Any = None,
        repository: Any = None,
        state_manager: Any = None,
    ) -> None:
        self.paper_trader = paper_trader
        self.journal_manager = journal_manager
        self.notifier = notifier
        self.event_bus = event_bus
        self.repository = repository
        self.state_manager = state_manager

    async def process(self, context: SignalContext) -> Optional[SignalContext]:
        if not context.sized_order or not context.evaluated_signal:
            return None

        symbol = context.symbol
        tf = context.timeframe
        signal_data = context.sized_order
        ai_data = context.evaluated_signal
        direction = ai_data.get('trade_direction', 'NEUTRAL')
        confidence = float(ai_data.get('final_confidence_rating', 0.0))
        tier = ai_data.get('tier', 'STANDARD')
        pattern_name = signal_data.get('patterns', ['Unknown'])[0] if signal_data.get('patterns') else 'Unknown'
        vip_thresh = float(getattr(settings, 'vip_confidence_threshold', 92.0)) if settings else 92.0
        is_vip = (confidence >= vip_thresh)

        # Invalidate opposing active signals & open paper trades if bias flip
        opposing_to_inval = context.metadata.get('opposing_to_invalidate', [])
        opposing_db_positions = context.metadata.get('opposing_db_positions', [])

        if opposing_to_inval or opposing_db_positions:
            inval_labels = []
            if self.state_manager and hasattr(self.state_manager, 'state'):
                active_signals = self.state_manager.state.get("active_signals", {})
                for opp_k, opp_tf, opp_dir in opposing_to_inval:
                    if opp_k in active_signals:
                        del active_signals[opp_k]
                    inval_labels.append(f"{opp_tf} {opp_dir}")

            for pos in opposing_db_positions:
                logger.warning(f"Force-closing opposing paper trade {pos['id']} ({pos['direction']}) due to {symbol} bias flip to {direction}")
                if self.paper_trader:
                    await self.paper_trader.close_position(
                        trade_id=str(pos['id']),
                        exit_price=signal_data['entry'],
                        reason="INVALIDATED_BY_REVERSAL"
                    )
                if f"{pos['direction']}" not in " ".join(inval_labels):
                    inval_labels.append(f"DB {pos['direction']}")

            inval_summary = ", ".join(inval_labels) if inval_labels else f"opposing {symbol} positions"
            signal_data['reversal_notice'] = (
                f"⚠️ <b>PRIOR {inval_summary} SETUP INVALIDATED</b>: Fresh {tf} {direction} structure shift detected.\n"
                f"🛑 <b>ACTION REQUIRED</b>: Close prior opposing positions before entering this {direction} trade!"
            )

        signal_data['dispatch_channels'] = ["VIP"] if is_vip else ["STANDARD"]

        trade_dict = {
            'timestamp': signal_data.get('timestamp', context.now_utc.isoformat()),
            'symbol': symbol,
            'direction': direction,
            'regime': signal_data.get('regime', 'UNKNOWN'),
            'pattern_name': pattern_name,
            'entry_price': float(signal_data['entry']),
            'sl_price': float(signal_data['stop_loss']),
            'tp1_price': float(signal_data.get('tp1', signal_data.get('take_profit', signal_data['entry']))),
            'tp2_price': float(signal_data.get('tp2', signal_data.get('take_profit', signal_data['entry']))),
            'leverage': float(signal_data.get('leverage_val', 1.0)),
            'ttl_mins': int(signal_data.get('ttl_mins', getattr(settings, 'trade_ttl_minutes', 180))),
            'confidence_score': confidence,
            'scoring_checklist': ai_data.get('scoring_checklist', []),
            'setup_type': signal_data.get('setup_type', 'STANDARD_SWING'),
            'breakeven_price': float(signal_data.get('breakeven_price', 0.0))
        }

        if self.repository:
            trade_id = await self.repository.save_trade(trade_dict)
        elif self.journal_manager:
            trade_id = await self.journal_manager.log_trade(trade_dict)
        else:
            trade_id = f"{symbol}_{context.now_utc.strftime('%Y%m%d_%H%M%S')}"

        context.trade_id = trade_id
        signal_data['trade_id'] = trade_id

        signal_key = f"{symbol}_{tf}"
        sig_state = {
            'trade_id': trade_id,
            'direction': direction,
            'confidence': confidence,
            'timestamp': context.now_utc.isoformat(),
            'timeframe': tf,
            'ttl_mins': trade_dict['ttl_mins']
        }
        if self.repository:
            await self.repository.save_active_signal(signal_key, sig_state)
        elif self.state_manager and hasattr(self.state_manager, 'state'):
            self.state_manager.state.setdefault("active_signals", {})[signal_key] = sig_state

        # Emit TradeOpenedEvent to Event Bus (Phase 2 Decoupled Architecture)
        if self.event_bus:
            await self.event_bus.publish(TradeOpenedEvent(
                trade_id=str(trade_id),
                symbol=symbol,
                direction=direction,
                entry_price=float(signal_data['entry']),
                sl_price=float(signal_data['stop_loss']),
                tp1_price=float(trade_dict['tp1_price']),
                tp2_price=float(trade_dict['tp2_price']),
                leverage=float(trade_dict['leverage']),
                confidence_score=confidence,
                tier=tier,
                signal_payload=signal_data
            ))

        # Telegram / Discord Notification
        if self.notifier:
            current_drawdown = 0.0
            if self.paper_trader and hasattr(self.paper_trader, 'get_current_drawdown'):
                current_drawdown = await self.paper_trader.get_current_drawdown()
            ai_json_str = json.dumps(ai_data)
            await self.notifier.notify_signal(symbol, signal_data, ai_json_str, current_drawdown)

        logger.info(f"🚀 [{symbol} {tf}] Result: Trade Dispatched - {trade_id} {direction} (Score: {confidence}%, Tier: {tier})")
        return context
