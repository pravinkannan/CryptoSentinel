"""Crypto Sentinel: Hexagonal & Event-Driven Algorithmic Trading Sentinel."""
import asyncio, logging
from typing import Optional
from core.config import settings; from core.state_manager import StateManager; from core.events import TradeClosedEvent
from core.event_bus import get_event_bus; from core.calendar_guard import CalendarGuard; from data.market_data_service import MarketDataService
from data.news_aggregator import NewsAggregatorService; from engine.signal_generator import SignalGeneratorService
from engine.ai_evaluator import AIEvaluatorService; from engine.regime_detector import RegimeDetector; from engine.journal_manager import JournalManager
from engine.portfolio_manager import PortfolioManager; from notifications.notifier import NotificationService; from execution.paper_trader import PaperTradingService
from infrastructure.database.repository import SentinelRepository; from application.pipeline.pipeline import SignalPipeline, SignalContext
from application.pipeline.steps import (MarketDataIngestionStep, PatternRecognitionStep, ConfluenceEvaluationStep, RiskAndPositionSizingStep, ExecutionDispatchStep)

logger = logging.getLogger(__name__)

class CryptoSentinelApp:
    """Bootstraps DI container, Event Bus, and composable SignalPipeline."""
    _pipeline: Optional[SignalPipeline] = None
    def __init__(self) -> None:
        self.event_bus, self.state_manager = get_event_bus(), StateManager()
        self.paper_trader = PaperTradingService(event_bus=self.event_bus)
        self.repository = SentinelRepository(state_manager=self.state_manager)
        self.market_data = MarketDataService(exchange_id=settings.exchange, fallback_exchanges=settings.fallback_exchanges, proxy_url=settings.proxy_url, probe_cooldown_minutes=settings.exchange_probe_cooldown_minutes)
        self.regime_detector, self.calendar_guard = RegimeDetector(), CalendarGuard()
        self.journal_manager, self.portfolio_manager = JournalManager(), PortfolioManager(self.paper_trader)
        self.signal_generator = SignalGeneratorService(percentage_trigger=settings.percentage_move_trigger, static_alerts=settings.static_alerts, atr_period=settings.atr_period, sl_atr_multiplier=settings.sl_atr_multiplier, target_rr_ratio=settings.target_rr_ratio)
        self.risk_manager, self.news_aggregator = self.signal_generator.risk_manager, NewsAggregatorService(rss_feeds=settings.rss_feeds, state_manager=self.state_manager)
        self.ai_evaluator = AIEvaluatorService(api_key=settings.gemini_api_key, model_name=settings.gemini_model_name, timeout=settings.llm_timeout_seconds, fallback_models=settings.gemini_fallback_models)
        self.notifier = NotificationService(telegram_token=settings.telegram_bot_token, telegram_chat_id=settings.telegram_chat_id); self.notifier.journal_manager = self.journal_manager
        self.event_bus.subscribe(TradeClosedEvent, self.repository.on_trade_closed); self.event_bus.subscribe(TradeClosedEvent, self._handle_trade_closed)

    def build_pipeline(self) -> SignalPipeline:
        rm = getattr(self, 'risk_manager', getattr(self.signal_generator, 'risk_manager', None))
        pt_db = getattr(self.paper_trader, 'db_path', None); self.repository.db_path = pt_db if isinstance(pt_db, str) else 'data/sentinel.db'; self.repository.state_manager = self.state_manager
        return SignalPipeline([
            MarketDataIngestionStep(self.market_data, self.regime_detector, self.portfolio_manager, self.paper_trader, self.signal_generator, self.state_manager, self.journal_manager, self.notifier, self.ai_evaluator),
            PatternRecognitionStep(self.signal_generator, self.market_data, self.state_manager, self.notifier, self.journal_manager),
            ConfluenceEvaluationStep(self.event_bus, self.ai_evaluator, self.journal_manager, self.paper_trader, self.state_manager, self.notifier, self.repository),
            RiskAndPositionSizingStep(rm, self.state_manager),
            ExecutionDispatchStep(self.paper_trader, self.journal_manager, self.notifier, self.event_bus, self.repository, self.state_manager)
        ])

    @property
    def pipeline(self) -> SignalPipeline: return self._pipeline or self.build_pipeline()
    @pipeline.setter
    def pipeline(self, val: Optional[SignalPipeline]) -> None: self._pipeline = val

    async def _handle_trade_closed(self, event: TradeClosedEvent) -> None:
        if event.outcome == 'LOSS' and event.exit_candle and self.ai_evaluator and self.journal_manager:
            try:
                s = {'trade_id': event.trade_id, 'symbol': event.symbol, 'direction': event.direction, 'entry_price': event.entry_price, 'sl_price': event.exit_price, 'leverage': event.leverage, 'net_pnl_pct': event.net_pnl_pct}
                rep = await self.ai_evaluator.generate_autopsy(s, event.exit_candle, (await self.market_data.get_market_microstructure(event.symbol)) if self.market_data else {})
                await self.repository.save_autopsy({**s, **rep})
            except Exception as e: logger.error(f"Autopsy worker failed for {event.trade_id}: {e}")

    async def initialize(self) -> None: await self.state_manager.load_state(); await self.repository.ensure_schema(); await self.calendar_guard.sync_calendar()

    async def _process_symbol(self, symbol: str, news_data: list, circuit_breaker_active: bool, btc_trend: str) -> None:
        for tf in settings.timeframes: await self.pipeline.execute(SignalContext(symbol=symbol, timeframe=tf, news_data=news_data, circuit_breaker_active=circuit_breaker_active, btc_trend=btc_trend))

    async def run_loop(self) -> None:
        while True:
            try:
                is_bo, msg = self.calendar_guard.check_blackout_window()
                if is_bo:
                    if msg and self.notifier:
                        try: await self.notifier.send_telegram(msg)
                        except Exception as e: logger.error(f"Blackout alert error: {e}")
                    await asyncio.sleep(60); continue
                cb = (await self.paper_trader.get_current_drawdown() > settings.max_drawdown_percent) if getattr(settings, 'enable_circuit_breaker', True) else False
                if cb: logger.warning("CIRCUIT BREAKER TRIGGERED: Drawdown exceeds threshold. Halting new signals.")
                logger.info(f"Starting market scan cycle for {len(settings.symbols)} symbols...")
                news = await self.news_aggregator.get_latest_news(limit=5)
                btc_df = await self.market_data.fetch_ohlcv('BTC/USDT', '1h', limit=250)
                btc_trend = self.signal_generator.get_htf_trend(btc_df) if btc_df is not None else "Unknown"
                for sym in settings.symbols: await self._process_symbol(sym, news, cb, btc_trend)
                await self.state_manager.save_state(self.news_aggregator.seen_guids, self.state_manager.state.get("last_crossed_alerts", {}))
                logger.info(f"Scan cycle complete. Sleeping for {settings.poll_interval_minutes} minutes...")
                await asyncio.sleep(settings.poll_interval_minutes * 60)
            except asyncio.CancelledError: break
            except Exception as e: logger.error(f"Watcher loop error: {e}", exc_info=True); await asyncio.sleep(5)

async def _supervised_task(coro, name: str) -> None:
    try: await coro
    except asyncio.CancelledError: pass
    except Exception as e: logger.error(f"Error in {name}: {e}", exc_info=True)

async def main() -> None:
    app = CryptoSentinelApp(); await app.initialize()
    tasks = [asyncio.create_task(_supervised_task(app.run_loop(), "Watcher")), asyncio.create_task(_supervised_task(app.calendar_guard.start_sync_task(), "Calendar"))]
    if app.notifier.bot: tasks.append(asyncio.create_task(_supervised_task(app.notifier.start_polling(), "Bot")))
    try: await asyncio.gather(*tasks)
    finally:
        for t in tasks: t.cancel()
        await app.market_data.close()
        if app.notifier.bot and app.notifier.bot.session: await app.notifier.bot.session.close()

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
