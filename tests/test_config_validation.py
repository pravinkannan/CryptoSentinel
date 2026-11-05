"""
Unit tests for boot-time configuration validation and calendar guard returns.
"""

import pytest
from pydantic import ValidationError
from core.config import Settings, settings
from core.calendar_guard import CalendarGuard


def test_gemini_model_validation_success():
    """Verify valid gemini model names pass validation."""
    cfg = Settings(gemini_model_name="gemini-2.5-flash")
    assert cfg.gemini_model_name == "gemini-2.5-flash"


def test_gemini_model_validation_fails_on_empty():
    """Verify empty model name raises ValueError and fails loudly on startup."""
    with pytest.raises((ValidationError, ValueError)):
        Settings(gemini_model_name="")


def test_gemini_model_validation_fails_on_invalid_prefix():
    """Verify non-gemini model names raise ValueError and fail loudly."""
    with pytest.raises((ValidationError, ValueError)):
        Settings(gemini_model_name="claude-3-5-sonnet")


def test_static_alerts_default_empty():
    """Verify that static_alerts defaults to an empty dict to favor dynamic structural levels."""
    cfg = Settings()
    assert isinstance(cfg.static_alerts, dict)
    assert len(cfg.static_alerts) == 0


def test_calendar_guard_returns_standard_bool():
    """Verify that CalendarGuard.is_circuit_breaker_active returns a pure Python bool."""
    guard = CalendarGuard()
    active = guard.is_circuit_breaker_active()
    assert isinstance(active, bool)
    assert active in (True, False)

