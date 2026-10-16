import asyncio
import json
import pytest
import pandas as pd
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from core.config import settings
from core.state_manager import StateManager
from engine.journal_manager import JournalManager
from execution.paper_trader import PaperTradingService
from main import CryptoSentinelApp


def test_paper_trader_evicts_active_signal_on_stop_loss(tmp_path):
    """Verify that when a trade hits Stop Loss, it is immediately evicted from active_signals."""
    async def run_test():
        db_file = str(tmp_path / "test_lifecycle.db")
        journal = JournalManager(db_path=db_file)
        await journal.ensure_db_exists()
        trader = PaperTradingService(db_path=db_file)

        now = datetime.now(timezone.utc)
        trade_id = await journal.log_trade({
            'timestamp': now.isoformat(),
            'symbol': 'BTC/USDT',
            'direction': 'LONG',
            'pattern_name': 'Bullish Belthold',
            'entry_price': 83800.0,
            'sl_price': 83400.0,
            'tp1_price': 84500.0,
            'tp2_price': 85200.0,
            'leverage': 33.0,
            'ttl_mins': 1080,
            'confidence_score': 100
        })

        sm = StateManager(filename=str(tmp_path / "state.json"))
        sm.state["active_signals"] = {
            "BTC/USDT_15m": {
                "trade_id": trade_id,
                "direction": "LONG",
                "confidence": 100,
                "timestamp": now.isoformat(),
                "timeframe": "15m",
                "ttl_mins": 1080
            }
        }

        # Verify initial active state
        assert "BTC/USDT_15m" in sm.state["active_signals"]

        # Candle breaches SL (low = 83300 < 83400)
        df_candle = pd.DataFrame([{
            'timestamp': now + timedelta(minutes=5),
            'open': 83700.0,
            'high': 83750.0,
            'low': 83300.0,
            'close': 83350.0,
            'volume': 1500.0
        }])

        await trader.update_trades('BTC/USDT', df_candle, state_manager=sm)

        # Signal must be evicted from active_signals immediately!
        assert "BTC/USDT_15m" not in sm.state["active_signals"]

    asyncio.run(run_test())


def test_paper_trader_is_trade_active_detection(tmp_path):
    """Verify is_trade_active accurately distinguishes between PENDING and closed/stopped-out trades."""
    async def run_test():
        db_file = str(tmp_path / "test_is_active.db")
        journal = JournalManager(db_path=db_file)
        await journal.ensure_db_exists()
        trader = PaperTradingService(db_path=db_file)

        now = datetime.now(timezone.utc)
        trade_id = await journal.log_trade({
            'timestamp': now.isoformat(),
            'symbol': 'ETH/USDT',
            'direction': 'SHORT',
            'pattern_name': 'Bearish Engulfing',
            'entry_price': 2500.0,
            'sl_price': 2550.0,
            'tp1_price': 2450.0,
            'tp2_price': 2400.0,
            'leverage': 10.0,
            'ttl_mins': 1080,
            'confidence_score': 95
        })

        # 1. Trade is currently PENDING
        assert await trader.is_trade_active('ETH/USDT', trade_id=trade_id) is True
        assert await trader.is_trade_active('ETH/USDT', direction='SHORT') is True

        # Nonexistent symbol returns None
        assert await trader.is_trade_active('SOL/USDT', direction='LONG') is None

        # 2. Trigger SL breach (high = 2560 >= 2550)
        df_candle = pd.DataFrame([{
            'timestamp': now + timedelta(minutes=5),
            'open': 2520.0,
            'high': 2560.0,
            'low': 2510.0,
            'close': 2555.0,
            'volume': 500.0
        }])
        await trader.update_trades('ETH/USDT', df_candle)

        # 3. Trade is now LOSS -> is_trade_active must return False
        assert await trader.is_trade_active('ETH/USDT', trade_id=trade_id) is False
        assert await trader.is_trade_active('ETH/USDT', direction='SHORT') is False

    asyncio.run(run_test())


def test_main_prunes_closed_signal_and_dispatches_reversal(tmp_path):
    """
    Verify that when an opposing signal in active_signals was stopped out (LOSS),
    main._process_symbol prunes it and does NOT reject the incoming reversal signal.
    """
    async def run_test():
        db_file = str(tmp_path / "test_main_prune.db")
        journal = JournalManager(db_path=db_file)
        await journal.ensure_db_exists()
        trader = PaperTradingService(db_path=db_file)

        sm = StateManager(filename=str(tmp_path / "state.json"))
        symbol = "BTC/USDT"
        tf = "1h"
        now = datetime.now(timezone.utc)

        # 1. Log a 15m LONG trade and mark it as LOSS (stopped out)
        trade_id = await journal.log_trade({
            'timestamp': (now - timedelta(hours=2)).isoformat(),
            'symbol': symbol,
            'direction': 'LONG',
            'pattern_name': 'Bullish Belthold',
            'entry_price': 83800.0,
            'sl_price': 83400.0,
            'tp1_price': 84500.0,
            'tp2_price': 85200.0,
            'leverage': 33.0,
            'ttl_mins': 1080,
            'confidence_score': 100,
            'outcome': 'LOSS'
        })

        # 2. State still has the ghost 15m LONG (100% confidence)
        sm.state["active_signals"] = {
            f"{symbol}_15m": {
                "trade_id": trade_id,
                "direction": "LONG",
                "confidence": 100,
                "timestamp": (now - timedelta(hours=2)).isoformat(),
                "timeframe": "15m",
                "ttl_mins": 1080
            }
        }

        # 3. Setup app
        app = CryptoSentinelApp()
        app.state_manager = sm
        app.paper_trader = trader
        app.journal_manager = journal
        app.market_data = MagicMock()
        app.notifier = MagicMock()
        app.notifier.notify_signal = AsyncMock()
        app.ai_evaluator = MagicMock()
        app.portfolio_manager = MagicMock()
        app.portfolio_manager.check_emergency_guard = AsyncMock()
        app.regime_detector = MagicMock()
        app.regime_detector.detect_regime = MagicMock(return_value={"regime": "NORMAL", "phase": "CONSOLIDATION"})

        # Feed fresh candle
        df_fresh = pd.DataFrame({
            'timestamp': [now],
            'open': [83500.0], 'high': [83600.0], 'low': [83200.0], 'close': [83300.0],
            'volume': [2000.0]
        })
        app.market_data.fetch_ohlcv = AsyncMock(return_value=df_fresh)
        app.market_data.get_market_microstructure = AsyncMock(return_value={'funding_rate': 0.0, 'open_interest': 1000.0, 'imbalance_ratio': 1.0})

        # Signal generator emits 1h SHORT pattern
        app.signal_generator = MagicMock()
        app.signal_generator.get_htf_trend = MagicMock(return_value="Bearish")
        app.signal_generator.check_dynamic_levels = MagicMock(return_value=[])
        app.signal_generator.analyze_patterns = MagicMock(return_value={
            'patterns': ['Bearish Engulfing'],
            'entry': 83300.0,
            'stop_loss': 83700.0,
            'take_profit': 82500.0,
            'sl_pct': 0.5,
            'tp_pct': 1.0,
            'recommended_leverage': '20x',
            'ttl_mins': 1080,
            'funding_rate': 0.0,
            'timestamp': now.isoformat(),
            'regime': 'NORMAL'
        })

        # AI evaluates 88% SHORT (which is < 100%, but the 100% LONG was stopped out!)
        app.ai_evaluator.evaluate_signal = AsyncMock(return_value=json.dumps({
            'trade_direction': 'SHORT',
            'final_confidence_rating': 88,
            'overall_verdict': 'CONFIRMED'
        }))

        with patch.object(settings, "timeframes", [tf]):
            await app._process_symbol(symbol, [], False, "Bearish")

        # Result:
        # Ghost 15m LONG must be pruned
        assert f"{symbol}_15m" not in sm.state["active_signals"]
        # Fresh 1h SHORT (88%) must be accepted and dispatched!
        assert f"{symbol}_{tf}" in sm.state["active_signals"]
        assert sm.state["active_signals"][f"{symbol}_{tf}"]["direction"] == "SHORT"
        assert sm.state["active_signals"][f"{symbol}_{tf}"]["confidence"] == 88
        app.notifier.notify_signal.assert_awaited_once()

    asyncio.run(run_test())
