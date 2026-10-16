"""
Entry point for Crypto Sentinel.
Wires up services using Dependency Injection and manages the main async event loop.
"""

import asyncio
import logging
import json
from datetime import datetime, timezone, timedelta

from core.config import settings
from core.state_manager import StateManager
from data.market_data_service import MarketDataService
from engine.signal_generator import SignalGeneratorService
from data.news_aggregator import NewsAggregatorService
from engine.ai_evaluator import AIEvaluatorService
from notifications.notifier import NotificationService
from execution.paper_trader import PaperTradingService
from engine.regime_detector import RegimeDetector
from core.calendar_guard import CalendarGuard
from engine.journal_manager import JournalManager
from engine.portfolio_manager import PortfolioManager

logger = logging.getLogger(__name__)

class CryptoSentinelApp:
    """Main Application Container."""
    def __init__(self):
        self.state_manager = StateManager()
        self.paper_trader = PaperTradingService()
        self.market_data = MarketDataService(exchange_id=settings.exchange)
        self.regime_detector = RegimeDetector()
        self.calendar_guard = CalendarGuard()
        self.journal_manager = JournalManager()
        self.portfolio_manager = PortfolioManager(self.paper_trader)
        self.signal_generator = SignalGeneratorService(
            percentage_trigger=settings.percentage_move_trigger,
            static_alerts=settings.static_alerts,
            atr_period=settings.atr_period,
            sl_atr_multiplier=settings.sl_atr_multiplier,
            target_rr_ratio=settings.target_rr_ratio
        )
        self.risk_manager = self.signal_generator.risk_manager
        self.news_aggregator = NewsAggregatorService(
            rss_feeds=settings.rss_feeds,
            state_manager=self.state_manager
        )
        self.ai_evaluator = AIEvaluatorService(
            api_key=settings.gemini_api_key,
            model_name=settings.gemini_model_name,
            timeout=settings.llm_timeout_seconds,
            fallback_models=settings.gemini_fallback_models
        )
        self.notifier = NotificationService(
            telegram_token=settings.telegram_bot_token,
            telegram_chat_id=settings.telegram_chat_id
        )
        self.notifier.journal_manager = self.journal_manager

    async def initialize(self) -> None:
        """Initialize asynchronous state and resources."""
        await self.state_manager.load_state()
        await self.paper_trader.init_db()
        await self.journal_manager.ensure_db_exists()
        await self.calendar_guard.sync_calendar()

    async def _process_symbol(self, symbol: str, news_data: list, circuit_breaker_active: bool, btc_trend: str) -> None:
        """Process a single trading symbol across timeframes."""
        try:
            # 1. Heartbeat Watchdog & Data Freshness (fetch up to 500 candles / ~41.6 hrs to cover downtime)
            df_5m = await self.market_data.fetch_ohlcv(symbol, '5m', limit=500)
            if df_5m is None or df_5m.empty:
                return
                
            latest_timestamp = df_5m['timestamp'].iloc[-1]
            now_utc = datetime.now(timezone.utc)
            if latest_timestamp.tzinfo is None:
                latest_timestamp = latest_timestamp.replace(tzinfo=timezone.utc)
            age = (now_utc - latest_timestamp).total_seconds()
            
            if age > 330:
                logger.error(f"[HEARTBEAT ERROR] Stale market data detected for {symbol}. Candle age: {age}s. Pausing signal dispatch.")
                return
                
            # 2. Macro Trend Check & Regime Detection
            df_htf = await self.market_data.fetch_ohlcv(symbol, '1h', limit=250)
            htf_trend = self.signal_generator.get_htf_trend(df_htf) if df_htf is not None else "Unknown"
            
            df_4h = await self.market_data.fetch_ohlcv(symbol, '4h', limit=250)
            htf_4h_trend = self.signal_generator.get_htf_trend(df_4h) if df_4h is not None else "Unknown"


            regime_data = self.regime_detector.detect_regime(symbol, df_5m)
            
            # Emergency Guard Check
            if regime_data.get('regime') == "HIGH_VOLATILITY" and regime_data.get('phase') == "PHASE 1":
                current_price = df_5m['close'].iloc[-1] if not df_5m.empty else 0.0
                await self.portfolio_manager.check_emergency_guard(symbol, regime_data.get('regime'), regime_data.get('phase'), current_price)
                
            current_price_5m = df_5m['close'].iloc[-1] if not df_5m.empty else 0.0
            if current_price_5m > 0:
                # Update Paper Trades once per symbol (optimization to prevent multi-timeframe redundant DB calls)
                await self.paper_trader.update_trades(
                    symbol, df_5m, self.journal_manager, self.notifier, self.ai_evaluator, self.market_data, self.state_manager
                )

            for tf in settings.timeframes:
                logger.info(f"Analyzing {symbol} on {tf}... Regime: {regime_data['regime']}")
                df = await self.market_data.fetch_ohlcv(symbol, tf)
                
                if df is not None and not df.empty:
                    current_price = float(df.iloc[-1]['close'])
                    

                    # Check Dynamic Levels once per symbol on base timeframe
                    if tf == settings.timeframes[0]:
                        crossed_levels = self.signal_generator.check_dynamic_levels(symbol, current_price, self.state_manager, df)
                        for name, level in crossed_levels:
                            logger.info(f"Dynamic level '{name}' ({level}) crossed for {symbol}")
                            if settings.enable_dynamic_level_alerts:
                                last_alert_times = self.state_manager.state.setdefault("dynamic_level_alert_timestamps", {})
                                alert_tag = f"{symbol}_{name}"
                                last_ts_str = last_alert_times.get(alert_tag)
                                if last_ts_str:
                                    try:
                                        last_ts = datetime.fromisoformat(last_ts_str)
                                        if (now_utc - last_ts).total_seconds() < settings.dynamic_level_cooldown_minutes * 60:
                                            logger.info(f"Dynamic level alert throttled by cooldown: {alert_tag}")
                                            continue
                                    except Exception:
                                        pass
                                last_alert_times[alert_tag] = now_utc.isoformat()
                                await self.notifier.notify_dynamic_level(symbol, name, level, current_price)
                        
                    if circuit_breaker_active:
                        continue

                    # Fetch Microstructure
                    microstructure = await self.market_data.get_market_microstructure(symbol)
                    
                    # Generate Signals
                    signal_data = self.signal_generator.analyze_patterns(df, tf, htf_trend, htf_4h_trend, microstructure, symbol, btc_trend, regime_data)
                    if signal_data:
                        logger.info(f"Signal detected for {symbol} on {tf}: {signal_data['patterns']}")
                        
                        pattern_name = signal_data['patterns'][0] if signal_data['patterns'] else 'Unknown'
                        signal_data['pattern_drift_status'] = await self.journal_manager.check_pattern_drift(pattern_name)
                        
                        # Evaluate via AI
                        ai_response = await self.ai_evaluator.evaluate_signal(symbol, signal_data, news_data, regime_data)
                        if ai_response:
                            try:
                                ai_data = json.loads(ai_response)
                                direction = ai_data.get('trade_direction', 'NEUTRAL')
                                confidence = round(float(ai_data.get('final_confidence_rating', 0.0)), 1)
                                verdict = ai_data.get('overall_verdict', 'SKIP').upper()
                                is_degraded = ai_data.get('is_degraded', False)
                                
                                status = "DISPATCH" if (direction in ['LONG', 'SHORT'] and confidence >= settings.min_dispatch_confidence and "SKIP" not in verdict and "REJECT" not in verdict) else "REJECT"
                                logger.info(f"Evaluated: {symbol} {tf} | Score: {confidence}% | Status: {status}")
                                
                                if status == "DISPATCH":
                                    is_vip = (confidence >= settings.vip_confidence_threshold)
                                    signal_data = self.risk_manager.recalibrate_for_tier(
                                        signal_data,
                                        is_vip=is_vip,
                                        liquidity_magnets=signal_data.get('liquidity_magnets', [])
                                    )
                                    # Funding Rate Filter Check
                                    fr = signal_data.get('funding_rate', 0.0)
                                    if direction == 'SHORT' and fr < -0.04 and not is_vip:
                                        logger.info(f"Trade filtered out due to funding rate: {fr}%")
                                        continue
                                    if direction == 'LONG' and fr > 0.06 and not is_vip:
                                        logger.info(f"Trade filtered out due to funding rate: {fr}%")
                                        continue

                                    # Multi-Timeframe Conflict & Bias Flip Resolution
                                    now = datetime.now(timezone.utc)
                                    signal_key = f"{symbol}_{tf}"
                                    active_signals = self.state_manager.state.setdefault("active_signals", {})
                                    TF_RANKS = {'5m': 1, '15m': 2, '1h': 3, '4h': 4}
                                    curr_rank = TF_RANKS.get(tf, 2)
                                    
                                    skip_dispatch = False
                                    opposing_to_invalidate = []
                                    
                                    for k, sig in list(active_signals.items()):
                                        if not k.startswith(f"{symbol}_"):
                                            continue
                                        try:
                                            sig_time = datetime.fromisoformat(sig['timestamp'])
                                            sig_ttl = sig.get('ttl_mins', settings.trade_ttl_minutes)
                                            if (now - sig_time).total_seconds() > sig_ttl * 60:
                                                del active_signals[k]
                                                continue
                                        except Exception:
                                            continue
                                            
                                        sig_dir = sig.get('direction', '')
                                        sig_tf = sig.get('timeframe', k.split('_')[-1])
                                        sig_rank = TF_RANKS.get(sig_tf, 2)
                                        sig_conf = sig.get('confidence', 0)
                                        
                                        # Verify if the trade backing this active signal has already closed (e.g. SL or TP hit)
                                        if hasattr(self.paper_trader, 'is_trade_active') and callable(getattr(self.paper_trader, 'is_trade_active')):
                                            try:
                                                is_active = await self.paper_trader.is_trade_active(
                                                    symbol=symbol,
                                                    trade_id=sig.get('trade_id'),
                                                    direction=sig_dir
                                                )
                                                if is_active is False:
                                                    logger.info(
                                                        f"Pruning inactive signal from state: {k} {sig_dir} ({sig_conf}%) "
                                                        f"because its trade is already closed in database."
                                                    )
                                                    del active_signals[k]
                                                    continue
                                            except Exception as e:
                                                logger.debug(f"Could not verify trade active status for {k}: {e}")
                                        
                                        # Check for conflicting direction across timeframes
                                        if sig_dir and sig_dir != direction:
                                            if curr_rank < sig_rank:
                                                # Lower timeframe opposes an active higher timeframe trade!
                                                if confidence < settings.vip_confidence_threshold:
                                                    logger.info(
                                                        f"Counter-trend setup filtered: {symbol} {tf} {direction} ({confidence}%) "
                                                        f"opposes active higher-timeframe {sig_tf} {sig_dir} ({sig_conf}%)."
                                                    )
                                                    skip_dispatch = True
                                                    break
                                                else:
                                                    signal_data['reversal_notice'] = (
                                                        f"⚡ <b>COUNTER-TREND SCALP</b>: Opposes active {sig_tf} {sig_dir}. "
                                                        f"Use strict stop loss and tighter profit targets."
                                                    )
                                            else:
                                                # Equal or higher timeframe reversal -> Check Confidence Hierarchy Gate
                                                if confidence < sig_conf:
                                                    logger.info(
                                                        f"Bias flip rejected: New {symbol} {tf} {direction} ({confidence}%) "
                                                        f"has lower confidence than active opposing {sig_tf} {sig_dir} ({sig_conf}%). "
                                                        f"Preserving active setup."
                                                    )
                                                    skip_dispatch = True
                                                    break
                                                else:
                                                    # Equal or higher confidence -> Confirmed Market Structure Shift!
                                                    opposing_to_invalidate.append((k, sig_tf, sig_dir))
                                        elif k == signal_key:
                                            # Same symbol, same timeframe, same direction
                                            sig_time = datetime.fromisoformat(sig['timestamp'])
                                            if now - sig_time < timedelta(minutes=60):
                                                if confidence <= sig.get('confidence', 0):
                                                    logger.info(f"Trade filtered out: A better or equal signal ({sig.get('confidence')}%) is already active for {signal_key}.")
                                                    skip_dispatch = True
                                                    break
                                                else:
                                                    override_msg = f"🚨 OVERRIDE: Better setup detected ({confidence}% vs previous {sig.get('confidence')}%). Use this instead of the previous signal."
                                                    logger.info(override_msg)
                                                    if 'strategy_rationale' in signal_data:
                                                        signal_data['strategy_rationale'] = f"{override_msg}\n\n{signal_data['strategy_rationale']}"

                                    if not skip_dispatch:
                                        # Cross-check open paper positions in DB (covers post-reboot scenarios)
                                        db_open_positions = await self.paper_trader.get_open_positions(symbol)
                                        for pos in db_open_positions:
                                            if pos['direction'] != direction:
                                                pos_conf = pos.get('confidence_score', 0)
                                                if confidence < pos_conf:
                                                    logger.info(
                                                        f"Bias flip rejected against open DB position: {symbol} {tf} {direction} ({confidence}%) "
                                                        f"opposes active DB trade {pos['id']} ({pos['direction']} with {pos_conf}%). "
                                                        f"Preserving active trade."
                                                    )
                                                    skip_dispatch = True
                                                    break

                                    if skip_dispatch:
                                        continue

                                    # Invalidate opposing active signals & open paper trades
                                    if opposing_to_invalidate:
                                        inval_labels = []
                                        for opp_k, opp_tf, opp_dir in opposing_to_invalidate:
                                            if opp_k in active_signals:
                                                del active_signals[opp_k]
                                            inval_labels.append(f"{opp_tf} {opp_dir}")
                                            
                                        # Forcefully close opposing paper positions at current entry price
                                        open_positions = await self.paper_trader.get_open_positions(symbol)
                                        for pos in open_positions:
                                            if pos['direction'] != direction:
                                                pos_conf = pos.get('confidence_score', 0)
                                                if confidence < pos_conf:
                                                    logger.info(
                                                        f"Preserving active paper trade {pos['id']} ({pos['direction']} with {pos_conf}%) "
                                                        f"against weaker new reversal ({confidence}%)."
                                                    )
                                                    continue
                                                logger.warning(
                                                    f"Force-closing opposing paper trade {pos['id']} ({pos['direction']}) "
                                                    f"due to {symbol} bias flip to {direction}"
                                                )
                                                await self.paper_trader.close_position(
                                                    trade_id=str(pos['id']),
                                                    exit_price=signal_data['entry'],
                                                    reason="INVALIDATED_BY_REVERSAL"
                                                )
                                                
                                        inval_summary = ", ".join(inval_labels)
                                        reversal_msg = (
                                            f"⚠️ <b>PRIOR {inval_summary} SETUP INVALIDATED</b>: Fresh {tf} {direction} structure shift detected.\n"
                                            f"🛑 <b>ACTION REQUIRED</b>: Close/exit prior opposing positions before entering this {direction} trade!"
                                        )
                                        signal_data['reversal_notice'] = reversal_msg
                                        logger.info(f"Bias Flip Alert generated for {symbol}: {inval_summary} -> {direction}")

                                    # Channel tagging based on VIP threshold
                                    if is_vip:
                                        signal_data['dispatch_channels'] = ["VIP"]
                                    else:
                                        signal_data['dispatch_channels'] = ["STANDARD"]

                                    # Log to Journal Manager (which now uses sentinel.db unified table)
                                    trade_id = await self.journal_manager.log_trade({
                                        'timestamp': signal_data['timestamp'],
                                        'symbol': symbol,
                                        'direction': direction,
                                        'regime': signal_data['regime'],
                                        'pattern_name': pattern_name,
                                        'entry_price': signal_data['entry'],
                                        'sl_price': signal_data['stop_loss'],
                                        'tp1_price': signal_data.get('tp1', signal_data['take_profit']),
                                        'tp2_price': signal_data.get('tp2', signal_data['take_profit']),
                                        'leverage': float(signal_data.get('leverage_val', 1.0)),
                                        'ttl_mins': signal_data.get('ttl_mins', settings.trade_ttl_minutes),
                                        'confidence_score': confidence,
                                        'scoring_checklist': ai_data.get('scoring_checklist', []),
                                        'setup_type': signal_data.get('setup_type', 'STANDARD_SWING'),
                                        'breakeven_price': signal_data.get('breakeven_price', 0.0)
                                    })
                                    signal_data['trade_id'] = trade_id

                                    # Update Active Signals State
                                    active_signals[signal_key] = {
                                        'trade_id': trade_id,
                                        'direction': direction,
                                        'confidence': confidence,
                                        'timestamp': now.isoformat(),
                                        'timeframe': tf,
                                        'ttl_mins': signal_data.get('ttl_mins', settings.trade_ttl_minutes)
                                    }
                                    current_drawdown = await self.paper_trader.get_current_drawdown()
                                    await self.notifier.notify_signal(symbol, signal_data, ai_response, current_drawdown)
                                else:
                                    if is_degraded:
                                        logger.info(f"Trade REJECTED but logging as SHADOW due to DEGRADED pattern: {symbol} {tf}")
                                        trade_id = await self.journal_manager.log_trade({
                                            'timestamp': signal_data['timestamp'],
                                            'symbol': symbol,
                                            'direction': direction,
                                            'regime': signal_data['regime'],
                                            'pattern_name': pattern_name,
                                            'entry_price': signal_data['entry'],
                                            'sl_price': signal_data['stop_loss'],
                                            'tp1_price': signal_data.get('tp1', signal_data['take_profit']),
                                            'tp2_price': signal_data.get('tp2', signal_data['take_profit']),
                                            'leverage': float(signal_data.get('leverage_val', 1.0)),
                                            'ttl_mins': signal_data.get('ttl_mins', settings.trade_ttl_minutes),
                                            'confidence_score': confidence,
                                            'scoring_checklist': ai_data.get('scoring_checklist', []),
                                            'outcome': 'SHADOW'
                                        })
                                    else:
                                        reason = ai_data.get('strategy_analysis', 'No reason provided')
                                        logger.info(f"Trade filtered out. Direction: {direction}, Confidence: {confidence}%. Verdict: {verdict}. AI Reason: {reason}")
                            except Exception as e:
                                logger.error(f"Error evaluating paper trade: {e}")
                            
                # Rate Limiting
                await asyncio.sleep(2)
        except Exception as e:
            logger.error(f"Error processing {symbol}: {e}", exc_info=True)

    async def run_loop(self) -> None:
        """Main event loop."""
        try:
            while True:
                logger.info("Running iteration...")
                
                # Check Calendar Guard
                is_blackout, blackout_msg = self.calendar_guard.check_blackout_window()
                if is_blackout:
                    if blackout_msg:
                        logger.warning(blackout_msg)
                        try:
                            await self.notifier.send_telegram(blackout_msg)
                        except Exception as e:
                            logger.error(f"Failed to send telegram: {e}")
                    # Sleep short and check again
                    await asyncio.sleep(60)
                    continue
                
                # Check Circuit Breaker
                circuit_breaker_active = False
                if settings.enable_circuit_breaker:
                    current_drawdown = await self.paper_trader.get_current_drawdown()
                    if current_drawdown > settings.max_drawdown_percent:
                        logger.warning(f"CIRCUIT BREAKER TRIGGERED: Drawdown {current_drawdown:.2f}% exceeds {settings.max_drawdown_percent}%. Halting new signals.")
                        circuit_breaker_active = True
                
                # Fetch News
                news_data = await self.news_aggregator.get_latest_news(limit=5)
                
                # Fetch Global BTC Trend
                btc_df = await self.market_data.fetch_ohlcv('BTC/USDT', '1h', limit=250)
                btc_trend = self.signal_generator.get_htf_trend(btc_df) if btc_df is not None else "Unknown"
                
                # Process all symbols
                for symbol in settings.symbols:
                    await self._process_symbol(symbol, news_data, circuit_breaker_active, btc_trend)
                
                # Save persistent state
                await self.state_manager.save_state(
                    self.news_aggregator.seen_guids, 
                    self.state_manager.state.get("last_crossed_alerts", {})
                )

                # Sleep
                sleep_seconds = settings.poll_interval_minutes * 60
                logger.info(f"Iteration complete. Sleeping for {settings.poll_interval_minutes} minutes.")
                await asyncio.sleep(sleep_seconds)
                
        except asyncio.CancelledError:
            logger.info("Watcher loop cancelled.")
        except Exception as e:
            logger.error(f"Unexpected error in run_loop: {e}", exc_info=True)
        finally:
            await self.market_data.close()
            if self.notifier.bot and self.notifier.bot.session:
                await self.notifier.bot.session.close()

async def _supervised_task(coro, task_name: str) -> None:
    """Supervises a background task to isolate failures from the main execution loop."""
    try:
        await coro
    except asyncio.CancelledError:
        logger.info(f"{task_name} cancelled.")
    except Exception as e:
        logger.error(f"Unhandled error in {task_name}: {e}", exc_info=True)

async def main() -> None:
    logger.info("Starting Advanced Crypto Sentinel (Refactored)...")
    app = CryptoSentinelApp()
    await app.initialize()
    
    tasks = [
        asyncio.create_task(_supervised_task(app.run_loop(), "Trading Watcher Loop")),
        asyncio.create_task(_supervised_task(app.calendar_guard.start_sync_task(), "Calendar Sync Task"))
    ]
    if app.notifier.bot:
        tasks.append(asyncio.create_task(_supervised_task(app.notifier.start_polling(), "Telegram Bot Polling")))
        
    try:
        await asyncio.gather(*tasks)
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Sentinel shutdown initiated by user.")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
    finally:
        logger.info("Disposing background tasks and releasing resources...")
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await app.market_data.close()
        if app.notifier.bot and app.notifier.bot.session:
            await app.notifier.bot.session.close()
        logger.info("Sentinel shutdown complete.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
