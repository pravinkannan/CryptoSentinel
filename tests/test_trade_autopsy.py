"""Unit and integration tests for post-trade autopsy recording and Telegram /autopsy command."""
import asyncio
from unittest.mock import AsyncMock, MagicMock
from aiogram.types import Message

from engine.journal_manager import JournalManager
from notifications.notifier import NotificationService


def test_journal_autopsy_storage_and_retrieval(tmp_path):
    """Verify saving and retrieving post-trade autopsy in sentinel.db."""
    async def run_test():
        db_file = str(tmp_path / "test_sentinel.db")
        journal = JournalManager(db_path=db_file)
        await journal.ensure_db_exists()

        trade_id = "test-trade-1234"
        autopsy_data = {
            'trade_id': trade_id,
            'symbol': 'BTC/USDT',
            'direction': 'LONG',
            'failure_category': 'STOP_HUNT_SWEEP',
            'summary': 'Price wicked through stop loss at $82,400 to sweep liquidity before rallying.',
            'key_culprit': 'Aggressive market sell cascade absorbed by limit bids.',
            'actionable_lesson': 'Widen ATR stop loss buffer to 2.5x during high volume sessions.',
            'net_pnl_pct': -14.5
        }

        await journal.save_autopsy(autopsy_data)
        retrieved = await journal.get_trade_autopsy(trade_id)

        assert retrieved is not None
        assert retrieved['trade_id'] == trade_id
        assert retrieved['failure_category'] == 'STOP_HUNT_SWEEP'
        assert retrieved['net_pnl_pct'] == -14.5
        assert "liquidity before rallying" in retrieved['summary']

    asyncio.run(run_test())


def test_autopsy_telegram_command_handler(tmp_path):
    """Verify that the /autopsy telegram command returns the post-mortem report."""
    async def run_test():
        db_file = str(tmp_path / "test_sentinel.db")
        journal = JournalManager(db_path=db_file)
        await journal.ensure_db_exists()

        trade_id = "loss-5678"
        await journal.save_autopsy({
            'trade_id': trade_id,
            'symbol': 'ETH/USDT',
            'direction': 'SHORT',
            'failure_category': 'MACRO_TREND_BREAK',
            'summary': 'Bullish breakout broke through 1h resistance.',
            'key_culprit': 'Sudden spot volume influx.',
            'actionable_lesson': 'Do not short into ascending volume.',
            'net_pnl_pct': -12.0
        })

        notifier = NotificationService(telegram_token="dummy", telegram_chat_id="dummy")
        notifier.journal_manager = journal

        mock_message = MagicMock(spec=Message)
        mock_message.text = f"/autopsy {trade_id}"
        mock_message.reply = AsyncMock()

        # Find the registered autopsy handler
        handlers = notifier.dp.message.handlers
        autopsy_handler = None
        for h in handlers:
            if "autopsy_command" in getattr(h.callback, '__name__', ''):
                autopsy_handler = h.callback
                break

        assert autopsy_handler is not None
        await autopsy_handler(mock_message)

        mock_message.reply.assert_called_once()
        reply_text = mock_message.reply.call_args[0][0]
        assert "POST-TRADE AUTOPSY: ETH/USDT" in reply_text
        assert "MACRO_TREND_BREAK" in reply_text
        assert "Sudden spot volume influx." in reply_text

    asyncio.run(run_test())
