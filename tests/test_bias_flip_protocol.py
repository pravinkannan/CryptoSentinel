"""Unit and integration tests for 3-Tier Graduated Bias-Flip Protocol and reversal handling."""
import asyncio
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock
import pandas as pd

from core.config import settings
from engine.journal_manager import JournalManager
from execution.paper_trader import PaperTradingService
from notifications.notifier import NotificationService


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


def test_graduated_bias_flip_protocol_in_main(isolated_state_manager, tmp_path):
    """
    Verify the 3-Tier Graduated Bias-Flip Protocol:
    1. Weak noise (< 80%): Ignored completely, active trade runs untouched.
    2. Threat Defensive Mode (80% - 87.9%): Does NOT flip/whipsaw into opposite trade,
       but ratchets active opposing trade's SL to Breakeven (+0.1% buffer).
    3. Confirmed Bias Flip (>= 88%): Force closes active opposing position and dispatches new direction.
    """
    from main import CryptoSentinelApp

    async def run_test():
        with patch("main.StateManager", return_value=isolated_state_manager):
            app = CryptoSentinelApp()
            app.state_manager = isolated_state_manager
            from infrastructure.database.repository import SentinelRepository
            db_file = str(tmp_path / "test_bias_flip.db")
            app.repository = SentinelRepository(db_path=db_file, state_manager=isolated_state_manager)
            await app.repository.ensure_schema()
            app.market_data = MagicMock()
            app.notifier = MagicMock()
            app.notifier.notify_signal = AsyncMock()
            app.notifier.notify_defensive_shift = AsyncMock()
            app.notifier.notify_dynamic_level = AsyncMock()
            app.paper_trader = MagicMock()
            app.paper_trader.db_path = db_file
            app.paper_trader.update_trades = AsyncMock()
            app.paper_trader.move_sl_to_be = AsyncMock()
            app.paper_trader.get_open_positions = AsyncMock(return_value=[
                {'id': 'TRADE_1', 'direction': 'SHORT', 'entry': 1300.0, 'stop_loss': 1330.0, 'confidence_score': 100.0}
            ])
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
            app.journal_manager.log_rejected_signal = AsyncMock()
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
                    "trade_id": "TRADE_1",
                    "direction": "SHORT",
                    "confidence": 100,
                    "timestamp": now.isoformat(),
                    "timeframe": tf,
                    "entry": 1300.0,
                    "stop_loss": 1330.0,
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

            # Case 1: Tier 2 Threat Defensive Mode (84% confidence opposing LONG)
            # Must NOT flip to LONG, but MUST ratchet active SHORT to Breakeven
            app.ai_evaluator.evaluate_signal = AsyncMock(return_value=json.dumps({
                'trade_direction': 'LONG',
                'final_confidence_rating': 84,
                'overall_verdict': 'CONFIRMED'
            }))

            with patch.object(settings, "timeframes", ["15m"]):
                await app._process_symbol(symbol, [], False, "Bullish")

                # Verify: Active signal remains SHORT, new trade is NOT dispatched
                assert f"{symbol}_{tf}" in isolated_state_manager.state["active_signals"]
                assert isolated_state_manager.state["active_signals"][f"{symbol}_{tf}"]["direction"] == "SHORT"
                app.notifier.notify_signal.assert_not_awaited()
                app.paper_trader.close_position.assert_not_awaited()

                # Verify: Active trade SL ratcheted to Breakeven (1300 * 0.999 = 1298.7)
                app.paper_trader.move_sl_to_be.assert_awaited_once_with('TRADE_1', 1298.7)
                app.notifier.notify_defensive_shift.assert_awaited_once()

                # Case 2: Tier 3 Confirmed Bias Flip (89% confidence opposing LONG)
                # Clears the >= 88% institutional threshold -> Flips bias, closes SHORT, dispatches LONG!
                app.ai_evaluator.evaluate_signal = AsyncMock(return_value=json.dumps({
                    'trade_direction': 'LONG',
                    'final_confidence_rating': 89,
                    'overall_verdict': 'CONFIRMED'
                }))

                await app._process_symbol(symbol, [], False, "Bullish")

                # Verify: Opposing SHORT position forcefully closed
                app.paper_trader.close_position.assert_awaited_once_with(
                    trade_id='TRADE_1',
                    exit_price=1290.0,
                    reason="INVALIDATED_BY_REVERSAL"
                )
                # Verify: Fresh LONG signal is accepted and dispatched!
                assert f"{symbol}_{tf}" in isolated_state_manager.state["active_signals"]
                assert isolated_state_manager.state["active_signals"][f"{symbol}_{tf}"]["direction"] == "LONG"
                assert isolated_state_manager.state["active_signals"][f"{symbol}_{tf}"]["confidence"] == 89
                app.notifier.notify_signal.assert_awaited_once()

    asyncio.run(run_test())
