"""
Unit tests for Telegram Access Control Allow-list in NotificationService.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from aiogram.types import Message
from notifications.notifier import NotificationService


def test_telegram_access_control_authorized_user():
    """Verify that authorized chat IDs can execute commands."""
    async def run_test():
        notifier = NotificationService(
            telegram_token="dummy_token",
            telegram_chat_id="12345",
            authorized_chat_ids=["99999", "88888"]
        )
        notifier.journal_manager = MagicMock()
        notifier.journal_manager.get_trade_autopsy = AsyncMock(return_value={
            'symbol': 'BTC/USDT',
            'direction': 'LONG',
            'failure_category': 'STOP_HUNT',
            'net_pnl_pct': -2.5,
            'summary': 'Stop swept before bounce.',
            'key_culprit': 'Tight buffer.',
            'actionable_lesson': 'Add anti-sweep buffer.'
        })

        # Message from authorized chat_id 12345
        mock_msg = MagicMock(spec=Message)
        mock_msg.chat = MagicMock()
        mock_msg.chat.id = 12345
        mock_msg.text = "/autopsy test_trade_1"
        mock_msg.reply = AsyncMock()

        handlers = notifier.dp.message.handlers
        autopsy_handler = next(h.callback for h in handlers if "autopsy_command" in getattr(h.callback, '__name__', ''))

        await autopsy_handler(mock_msg)
        mock_msg.reply.assert_called_once()
        reply = mock_msg.reply.call_args[0][0]
        assert "POST-TRADE AUTOPSY: BTC/USDT" in reply
        assert "Access Denied" not in reply

    asyncio.run(run_test())


def test_telegram_access_control_unauthorized_user_blocked():
    """Verify that unauthorized chat IDs are strictly rejected with an Access Denied message."""
    async def run_test():
        notifier = NotificationService(
            telegram_token="dummy_token",
            telegram_chat_id="12345",
            authorized_chat_ids=["99999"]
        )
        notifier.journal_manager = MagicMock()
        notifier.journal_manager.get_trade_autopsy = AsyncMock()

        # Message from unauthorized attacker 66666
        mock_msg = MagicMock(spec=Message)
        mock_msg.chat = MagicMock()
        mock_msg.chat.id = 66666
        mock_msg.text = "/autopsy test_trade_1"
        mock_msg.reply = AsyncMock()

        handlers = notifier.dp.message.handlers
        autopsy_handler = next(h.callback for h in handlers if "autopsy_command" in getattr(h.callback, '__name__', ''))

        await autopsy_handler(mock_msg)
        mock_msg.reply.assert_called_once()
        reply = mock_msg.reply.call_args[0][0]
        assert "Access Denied" in reply
        notifier.journal_manager.get_trade_autopsy.assert_not_called()

    asyncio.run(run_test())


def test_telegram_access_control_recon_blocked_for_unauthorized():
    """Verify /recon command is blocked for unauthorized callers before executing scans."""
    async def run_test():
        notifier = NotificationService(
            telegram_token="dummy_token",
            telegram_chat_id="12345"
        )
        mock_recon = AsyncMock(return_value="RECON_OUTPUT")
        notifier.recon_handler = mock_recon

        mock_msg = MagicMock(spec=Message)
        mock_msg.chat = MagicMock()
        mock_msg.chat.id = 9999999  # Unauthorized
        mock_msg.text = "/recon SOL/USDT"
        mock_msg.reply = AsyncMock()

        handlers = notifier.dp.message.handlers
        recon_handler = next(h.callback for h in handlers if "recon_command" in getattr(h.callback, '__name__', ''))

        await recon_handler(mock_msg)
        mock_msg.reply.assert_called_once()
        reply = mock_msg.reply.call_args[0][0]
        assert "Access Denied" in reply
        mock_recon.assert_not_called()

    asyncio.run(run_test())
