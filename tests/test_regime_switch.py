import pytest
import pandas as pd
from datetime import datetime, timezone, timedelta

from engine.regime_detector import RegimeDetector
from core.calendar_guard import CalendarGuard

def test_regime_detector_normal():
    detector = RegimeDetector()

    # Create mock 5m df with ATR constant
    df = pd.DataFrame({'atr_14': [10.0] * 25, 'volume': [100.0] * 25})

    result = detector.detect_regime('BTC/USDT', df)
    assert result['regime'] == 'NORMAL'
    assert result['cooldown_active'] == False

def test_regime_detector_high_volatility():
    detector = RegimeDetector()

    # Create mock df where baseline is 10, but current is 30 (3x, which is >= 2.5x)
    data = [10.0] * 24 + [30.0]
    df = pd.DataFrame({'atr_14': data, 'volume': [100.0] * len(data)})

    result = detector.detect_regime('BTC/USDT', df)
    assert result['regime'] == 'HIGH_VOLATILITY'
    assert result['cooldown_active'] == True
    assert "IMPULSE SHOCK" in result['catalyst_info']

def test_regime_detector_cooldown_persistence():
    detector = RegimeDetector()

    data = [10.0] * 24 + [30.0]
    df = pd.DataFrame({'atr_14': data, 'volume': [100.0] * len(data)})

    # Trigger High Volatility
    result = detector.detect_regime('BTC/USDT', df)
    assert result['cooldown_active'] == True

    # Next candle ATR drops back to normal, but cooldown should still be active
    df2 = pd.DataFrame({'atr_14': [10.0] * 25, 'volume': [100.0] * 25})
    result2 = detector.detect_regime('BTC/USDT', df2)

    assert result2['regime'] == 'HIGH_VOLATILITY'
    assert result2['cooldown_active'] == True

def test_calendar_guard_blackout():
    guard = CalendarGuard()
    
    # Mock current time and add a high impact event happening in 10 minutes
    now = datetime.now(timezone.utc)
    event_time = now + timedelta(minutes=10)
    guard.high_impact_events.append(event_time)
    
    is_blackout, msg = guard.check_blackout_window()
    
    assert is_blackout == True
    assert "SENTINEL PAUSED" in msg

def test_calendar_guard_no_blackout():
    guard = CalendarGuard()
    
    # Event is 2 hours away (outside of 30m pre-buffer)
    now = datetime.now(timezone.utc)
    event_time = now + timedelta(hours=2)
    guard.high_impact_events.append(event_time)
    
    is_blackout, msg = guard.check_blackout_window()
    assert is_blackout == False
    assert msg == ""
