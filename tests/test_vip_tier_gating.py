"""Unit tests for VIP Tier confidence thresholds, badge styling, and channel routing."""
import asyncio
from unittest.mock import AsyncMock

from core.config import settings
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
