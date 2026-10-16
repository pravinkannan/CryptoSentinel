import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone, timedelta

from core.config import settings
from engine.journal_manager import JournalManager
from execution.paper_trader import PaperTradingService
from notifications.notifier import NotificationService


def test_vip_confidence_threshold_configuration():
    """Verify that settings default VIP confidence threshold is 92.0."""
    assert settings.vip_confidence_threshold == 92.0


def test_notification_tier_badges_and_channels():
    """Verify that >= 92.0 is VIP TIER and < 92.0 is STANDARD TIER."""
    async def run_test():
        notifier = NotificationService(telegram_token="dummy", telegram_chat_id="dummy")
        notifier.send_telegram = AsyncMock()

        base_signal = {
            'timeframe': '1h',
            'entry': 83000.0,
            'stop_loss': 82500.0,
            'take_profit': 84000.0,
            'sl_pct': 0.6,
            'tp_pct': 1.2,
            'recommended_leverage': '20x',
            'ttl_mins': 720,
            'expiration_time': '04:00 UTC'
        }

        # Case 1: 92% Confidence -> VIP
        signal_vip = dict(base_signal)
        ai_vip = {'trade_direction': 'Bullish', 'final_confidence_rating': 92, 'strategy_analysis': 'Test'}
        await notifier.notify_signal('BTC/USDT', signal_vip, ai_vip, 0.0)
        sent_vip = notifier.send_telegram.call_args[0][0]
        assert "👑 VIP TIER" in sent_vip
        assert "[👑 VIP EXCLUSIVE]" in sent_vip

        # Case 1b: VIP Sniper setup
        signal_sniper = dict(base_signal, setup_type="VIP_SNIPER")
        ai_sniper = {'trade_direction': 'Bullish', 'final_confidence_rating': 95, 'strategy_analysis': 'Sniper'}
        await notifier.notify_signal('BTC/USDT', signal_sniper, ai_sniper, 0.0)
        sent_sniper = notifier.send_telegram.call_args[0][0]
        assert "👑 VIP SNIPER (100% ROE)" in sent_sniper
        assert "[👑 VIP SNIPER EXCLUSIVE]" in sent_sniper

        # Case 2: 88% Confidence -> STANDARD
        signal_std = dict(base_signal)
        ai_std = {'trade_direction': 'Bullish', 'final_confidence_rating': 88, 'strategy_analysis': 'Test'}
        await notifier.notify_signal('BTC/USDT', signal_std, ai_std, 0.0)
        sent_std = notifier.send_telegram.call_args[0][0]
        assert "🔹 STANDARD TIER" in sent_std
        assert "[📈 STANDARD FEED]" in sent_std

        # Case 3: 86% Confidence -> STANDARD
        signal_std2 = dict(base_signal)
        ai_std2 = {'trade_direction': 'Bearish', 'final_confidence_rating': 86, 'strategy_analysis': 'Test'}
        await notifier.notify_signal('BTC/USDT', signal_std2, ai_std2, 0.0)
        sent_std2 = notifier.send_telegram.call_args[0][0]
        assert "🔹 STANDARD TIER" in sent_std2
        assert "[📈 STANDARD FEED]" in sent_std2

    asyncio.run(run_test())


def test_notification_reversal_notice_rendering():
    """Verify that reversal_notice is rendered prominently in the alert."""
    async def run_test():
        notifier = NotificationService(telegram_token="dummy", telegram_chat_id="dummy")
        notifier.send_telegram = AsyncMock()

        signal = {
            'timeframe': '1h',
            'entry': 83000.0,
            'stop_loss': 82500.0,
            'take_profit': 84000.0,
            'sl_pct': 0.6,
            'tp_pct': 1.2,
            'recommended_leverage': '20x',
            'ttl_mins': 720,
            'expiration_time': '04:00 UTC',
            'reversal_notice': '⚠️ PRIOR 1h SHORT SETUP INVALIDATED: Fresh 1h LONG structure shift detected.'
        }
        ai_data = {'trade_direction': 'Bullish', 'final_confidence_rating': 85, 'strategy_analysis': 'Reversal confirmed'}

        await notifier.notify_signal('BTC/USDT', signal, ai_data, 0.0)
        msg = notifier.send_telegram.call_args[0][0]
        assert "🔄 <b>BIAS FLIP / INVALIDATION NOTICE</b>" in msg
        assert "PRIOR 1h SHORT SETUP INVALIDATED" in msg
        assert "Fresh 1h LONG structure shift detected" in msg

    asyncio.run(run_test())


def test_paper_trader_invalidation_by_reversal(tmp_path):
    """Verify that paper trading forcefully closes opposing positions on reversal."""
    async def run_test():
        db_file = str(tmp_path / "test_sentinel.db")
        journal = JournalManager(db_path=db_file)
        await journal.ensure_db_exists()
        paper_trader = PaperTradingService(db_path=db_file)

        # 1. Log a SHORT trade that is pending
        trade_id = await journal.log_trade({
            'symbol': 'BTC/USDT',
            'direction': 'SHORT',
            'pattern_name': 'Bearish Engulfing',
            'entry_price': 83000.0,
            'sl_price': 83500.0,
            'tp1_price': 82500.0,
            'tp2_price': 82000.0,
            'leverage': 20.0,
            'confidence_score': 90
        })

        open_positions = await paper_trader.get_open_positions('BTC/USDT')
        assert len(open_positions) == 1
        assert open_positions[0]['direction'] == 'SHORT'
        assert open_positions[0]['confidence_score'] == 90.0

        # 2. Force close on reversal
        await paper_trader.close_position(
            trade_id=trade_id,
            exit_price=83050.0,
            reason="INVALIDATED_BY_REVERSAL"
        )

        # 3. Position should now be closed
        open_after = await paper_trader.get_open_positions('BTC/USDT')
        assert len(open_after) == 0

    asyncio.run(run_test())


def test_bias_flip_confidence_hierarchy_in_main(isolated_state_manager, tmp_path):
    """
    Verify that an incoming opposing signal cannot flip an active higher-confidence signal.
    Specifically: Active 100% SHORT cannot be flipped by a 90% LONG on equal timeframe.
    """
    import json
    import pandas as pd
    from main import CryptoSentinelApp

    async def run_test():
        with patch("main.StateManager", return_value=isolated_state_manager):
            app = CryptoSentinelApp()
            app.state_manager = isolated_state_manager
            app.market_data = MagicMock()
            app.notifier = MagicMock()
            app.notifier.notify_signal = AsyncMock()
            app.notifier.notify_dynamic_level = AsyncMock()
            app.paper_trader = MagicMock()
            app.paper_trader.update_trades = AsyncMock()
            app.paper_trader.get_open_positions = AsyncMock(return_value=[])
            app.paper_trader.close_position = AsyncMock()
            app.paper_trader.get_current_drawdown = AsyncMock(return_value=0.0)
            app.signal_generator = MagicMock()
            app.signal_generator.get_htf_trend = MagicMock(return_value="Bullish")
            app.signal_generator.check_dynamic_levels = MagicMock(return_value=[])
            app.ai_evaluator = MagicMock()
            app.journal_manager = MagicMock()
            app.journal_manager.check_pattern_drift = AsyncMock(return_value="NORMAL")
            app.journal_manager.log_trade = AsyncMock(return_value="TRADE_123")
            app.journal_manager.log_evaluation = AsyncMock()
            app.portfolio_manager = MagicMock()
            app.portfolio_manager.check_emergency_guard = AsyncMock()
            app.regime_detector = MagicMock()
            app.regime_detector.detect_regime = MagicMock(return_value={"regime": "NORMAL", "phase": "CONSOLIDATION"})

            now = datetime.now(timezone.utc)
            symbol = "ZEC/USDT"
            tf = "15m"

            # Set an active 100% SHORT signal
            isolated_state_manager.state["active_signals"] = {
                f"{symbol}_{tf}": {
                    "direction": "SHORT",
                    "confidence": 100,
                    "timestamp": now.isoformat(),
                    "timeframe": tf,
                    "ttl_mins": 1080
                }
            }

            # Prepare fresh 5m and 15m OHLCV
            df_fresh = pd.DataFrame({
                'timestamp': [now - timedelta(seconds=60)],
                'open': [1280.0], 'high': [1295.0], 'low': [1275.0], 'close': [1290.0],
                'volume': [100.0]
            })
            app.market_data.fetch_ohlcv = AsyncMock(return_value=df_fresh)
            app.market_data.get_market_microstructure = AsyncMock(return_value={'funding_rate': 0.0, 'open_interest': 1000.0, 'imbalance_ratio': 1.0})

            # Signal generator emits a LONG pattern
            app.signal_generator.analyze_patterns = MagicMock(return_value={
                'patterns': ['Bullish Engulfing'],
                'entry': 1290.0,
                'stop_loss': 1270.0,
                'take_profit': 1330.0,
                'sl_pct': 1.5,
                'tp_pct': 3.0,
                'recommended_leverage': '10x',
                'ttl_mins': 1080,
                'funding_rate': 0.0,
                'timestamp': now.isoformat(),
                'regime': 'NORMAL'
            })

            # AI evaluates opposing LONG with 90% confidence (< 100%)
            app.ai_evaluator.evaluate_signal = AsyncMock(return_value=json.dumps({
                'trade_direction': 'LONG',
                'final_confidence_rating': 90,
                'overall_verdict': 'CONFIRMED'
            }))

            with patch.object(settings, "timeframes", ["15m"]):
                # Process symbol with 90% opposing signal
                await app._process_symbol(symbol, [], False, "Bullish")

                # Result: 90% LONG must NOT invalidate the 100% SHORT
                assert f"{symbol}_{tf}" in isolated_state_manager.state["active_signals"]
                assert isolated_state_manager.state["active_signals"][f"{symbol}_{tf}"]["direction"] == "SHORT"
                assert isolated_state_manager.state["active_signals"][f"{symbol}_{tf}"]["confidence"] == 100
                app.notifier.notify_signal.assert_not_awaited()
                app.paper_trader.close_position.assert_not_awaited()

                # Now test when AI evaluates opposing LONG with 100% confidence (>= 100%)
                app.ai_evaluator.evaluate_signal = AsyncMock(return_value=json.dumps({
                    'trade_direction': 'LONG',
                    'final_confidence_rating': 100,
                    'overall_verdict': 'CONFIRMED'
                }))

                await app._process_symbol(symbol, [], False, "Bullish")

                # Result: 100% LONG IS accepted and flips the bias!
                assert f"{symbol}_{tf}" in isolated_state_manager.state["active_signals"]
                assert isolated_state_manager.state["active_signals"][f"{symbol}_{tf}"]["direction"] == "LONG"
                assert isolated_state_manager.state["active_signals"][f"{symbol}_{tf}"]["confidence"] == 100
                app.notifier.notify_signal.assert_awaited_once()

    asyncio.run(run_test())
