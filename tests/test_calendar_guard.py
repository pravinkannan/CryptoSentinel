"""Unit tests for CalendarGuard economic news synchronization, directional bias, and alerting."""
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from core.calendar_guard import CalendarGuard
from notifications.notifier import NotificationService


def test_calendar_guard_sync_parses_events():
    """Verify that CalendarGuard sync parses high-impact economic calendar events."""
    async def run_test():
        guard = CalendarGuard()
        mock_data = [
            {
                "title": "Non-Farm Payrolls",
                "country": "USD",
                "date": "2026-10-15T08:30:00-04:00",
                "impact": "High"
            },
            {
                "title": "Low Impact Event",
                "country": "USD",
                "date": "2026-10-15T10:00:00-04:00",
                "impact": "Low"
            },
            {
                "title": "EUR Rate Decision",
                "country": "EUR",
                "date": "2026-10-15T12:00:00-04:00",
                "impact": "High"
            }
        ]

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=mock_data)

        # Context manager for session.get(...)
        mock_get_ctx = MagicMock()
        mock_get_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_get_ctx.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.get.return_value = mock_get_ctx

        # Context manager for aiohttp.ClientSession(...)
        mock_session_ctx = MagicMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

        with patch('aiohttp.ClientSession', return_value=mock_session_ctx):
            await guard.sync_calendar()

        assert len(guard.high_impact_events) == 1
        assert guard.last_sync_time is not None

    asyncio.run(run_test())


def test_calendar_guard_sync_dispatches_upcoming_alert():
    """Verify that CalendarGuard sends detailed Telegram alerts for upcoming high-impact events."""
    async def run_test():
        guard = CalendarGuard()
        mock_notifier = MagicMock()
        mock_notifier.send_telegram = AsyncMock()
        guard.set_notifier(mock_notifier)

        # Event scheduled 4 hours in the future
        future_time = datetime.now(timezone.utc) + timedelta(hours=4)
        future_iso = future_time.isoformat()

        mock_data = [
            {
                "title": "Non-Farm Employment Change",
                "country": "USD",
                "date": future_iso,
                "impact": "High",
                "forecast": "65K",
                "previous": "29K"
            },
            {
                "title": "Unemployment Rate",
                "country": "USD",
                "date": future_iso,
                "impact": "High",
                "forecast": "4.2%",
                "previous": "4.2%"
            }
        ]

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=mock_data)

        mock_get_ctx = MagicMock()
        mock_get_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_get_ctx.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.get.return_value = mock_get_ctx

        mock_session_ctx = MagicMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

        with patch('aiohttp.ClientSession', return_value=mock_session_ctx):
            await guard.sync_calendar()

        assert mock_notifier.send_telegram.called
        call_msg = mock_notifier.send_telegram.call_args[0][0]

        # Verify exact required content
        assert "HIGH-IMPACT MACRO ALERT" in call_msg
        assert "Non-Farm Employment Change" in call_msg
        assert "Unemployment Rate" in call_msg
        assert "Forecast: <code>65K</code>" in call_msg
        assert "UTC" in call_msg
        assert "IST" in call_msg
        assert "BULLISH for Crypto" in call_msg
        assert "BEARISH for Crypto" in call_msg
        assert "Stop-Loss to Breakeven" in call_msg
        assert "Sentinel Execution Blackout" in call_msg

    asyncio.run(run_test())


def test_calendar_guard_alert_deduplication():
    """Verify that multiple sync runs do not dispatch duplicate alerts for already alerted events."""
    async def run_test():
        guard = CalendarGuard()
        mock_notifier = MagicMock()
        mock_notifier.send_telegram = AsyncMock()
        guard.set_notifier(mock_notifier)

        future_time = datetime.now(timezone.utc) + timedelta(hours=6)
        future_iso = future_time.isoformat()

        mock_data = [
            {
                "title": "CPI m/m",
                "country": "USD",
                "date": future_iso,
                "impact": "High",
                "forecast": "0.3%",
                "previous": "0.2%"
            }
        ]

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=mock_data)

        mock_get_ctx = MagicMock()
        mock_get_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_get_ctx.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.get.return_value = mock_get_ctx

        mock_session_ctx = MagicMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

        with patch('aiohttp.ClientSession', return_value=mock_session_ctx):
            # First sync cycle
            await guard.sync_calendar()
            assert mock_notifier.send_telegram.call_count == 1

            # Second sync cycle (same data)
            await guard.sync_calendar()
            assert mock_notifier.send_telegram.call_count == 1  # Deduplicated!

    asyncio.run(run_test())


def test_macro_directional_bias_scenarios():
    """Verify directional bias matrix accuracy across major macroeconomic categories."""
    guard = CalendarGuard()

    # 1. Unemployment Rate (Inverse: higher unemployment is dovish / bullish crypto & gold)
    unemp_bias = guard.get_macro_directional_bias("Unemployment Rate")
    assert "BULLISH" in unemp_bias["lower_actual_scenario"]
    assert "BEARISH" in unemp_bias["higher_actual_scenario"]

    # 2. Non-Farm Payrolls (Pro-cyclical: higher jobs is hawkish / bearish crypto & gold)
    nfp_bias = guard.get_macro_directional_bias("Non-Farm Employment Change")
    assert "BEARISH" in nfp_bias["higher_actual_scenario"]
    assert "BULLISH" in nfp_bias["lower_actual_scenario"]

    # 3. Inflation / CPI
    cpi_bias = guard.get_macro_directional_bias("CPI m/m")
    assert "BEARISH" in cpi_bias["higher_actual_scenario"]
    assert "BULLISH" in cpi_bias["lower_actual_scenario"]

    # 4. Central Bank / FOMC
    fomc_bias = guard.get_macro_directional_bias("FOMC Statement")
    assert "BEARISH" in fomc_bias["higher_actual_scenario"]
    assert "BULLISH" in fomc_bias["lower_actual_scenario"]


def test_calendar_command_telegram():
    """Verify /calendar Telegram command formats schedule and directional analysis for users."""
    async def run_test():
        notifier = NotificationService(telegram_token="dummy:token", telegram_chat_id="12345")
        guard = CalendarGuard()
        notifier.calendar_guard = guard

        future_time = datetime.now(timezone.utc) + timedelta(hours=10)
        guard.high_impact_events = [
            {
                "time": future_time,
                "title": "Non-Farm Employment Change",
                "country": "USD",
                "forecast": "75K",
                "previous": "30K"
            }
        ]

        mock_msg = MagicMock()
        mock_msg.chat.id = 12345
        mock_msg.text = "/calendar"
        mock_msg.reply = AsyncMock()

        # Find registered /calendar handler
        calendar_handler = None
        for handler in notifier.dp.message.handlers:
            if "calendar_command" in getattr(handler.callback, '__name__', ''):
                calendar_handler = handler.callback
                break

        assert calendar_handler is not None
        await calendar_handler(mock_msg)

        assert mock_msg.reply.called
        reply_text = mock_msg.reply.call_args[0][0]
        assert "UPCOMING HIGH-IMPACT ECONOMIC CALENDAR" in reply_text
        assert "Non-Farm Employment Change" in reply_text
        assert "UTC" in reply_text
        assert "IST" in reply_text
        assert "Macro Directional" in reply_text
        assert "Actionable Protection for Active Trades" in reply_text

    asyncio.run(run_test())


def test_blackout_window_enrichment():
    """Verify that active blackout window alerts include directional guidance and trade management advice."""
    guard = CalendarGuard()
    now = datetime.now(timezone.utc)
    event_time = now + timedelta(minutes=10)
    guard.high_impact_events.append({
        "time": event_time,
        "title": "CPI m/m",
        "country": "USD",
        "forecast": "0.3%",
        "previous": "0.2%"
    })

    is_blackout, msg = guard.check_blackout_window()
    assert is_blackout is True
    assert "SENTINEL PAUSED" in msg
    assert "CPI m/m" in msg
    assert "Expected Market Direction (Crypto & Gold)" in msg
    assert "Actionable Trade Protection" in msg
    assert "Move Stop-Loss to Breakeven" in msg
