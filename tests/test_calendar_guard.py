"""Unit tests for CalendarGuard economic news synchronization and filtering."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from core.calendar_guard import CalendarGuard


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
