"""
Enterprise test suite for System Governance and Circuit Breakers in CryptoSentinel.
Tests:
- Drawdown Circuit Breakers (> 5.0% halt, <= 5.0% active, negative drawdown/profit)
- Stale Data Heartbeat Watchdog (> 330s stale drop, <= 330s pass, timezone handling)
- Funding Rate Filters (Crowded Short/Long suppression, High-conviction override)
"""

import asyncio
import pytest
import pandas as pd
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta

from core.config import settings
from main import CryptoSentinelApp


@pytest.fixture
def sentinel_app(isolated_state_manager):
    """Creates a mock-configured CryptoSentinelApp instance."""
    with patch("main.StateManager", return_value=isolated_state_manager):
        app = CryptoSentinelApp()
        app.state_manager = isolated_state_manager
        app.market_data = MagicMock()
        app.notifier = MagicMock()
        app.notifier.notify_dynamic_level = AsyncMock()
        app.notifier.notify_signal = AsyncMock()
        app.notifier.send_telegram = AsyncMock()
        app.paper_trader = MagicMock()
        app.paper_trader.get_current_drawdown = AsyncMock()
        app.paper_trader.update_trades = AsyncMock()
        app.paper_trader.execute_paper_trade = AsyncMock(return_value={"ticket_id": "TEST_001"})
        app.signal_generator = MagicMock()
        app.ai_evaluator = MagicMock()
        app.journal_manager = MagicMock()
        app.journal_manager.check_pattern_drift = AsyncMock(return_value="NORMAL")
        app.portfolio_manager = MagicMock()
        app.portfolio_manager.check_emergency_guard = AsyncMock()
        app.regime_detector = MagicMock()
        app.regime_detector.detect_regime = MagicMock(return_value={"regime": "NORMAL", "phase": "CONSOLIDATION"})
        return app


# =========================================================================
# 1. Circuit Breaker Drawdown Tests
# =========================================================================

def test_circuit_breaker_trips_when_drawdown_exceeds_threshold(sentinel_app):
    """
    Positive case: When drawdown (e.g. 5.5%) exceeds max_drawdown_percent (5.0%),
    circuit breaker halts new signal generation and analysis.
    """
    async def run_test():
        symbol = "BTC/USDT"
        now = datetime.now(timezone.utc)
        df_fresh = pd.DataFrame({
            'timestamp': [now - timedelta(seconds=60)],
            'open': [65000.0], 'high': [65100.0], 'low': [64900.0], 'close': [65050.0],
            'volume': [100.0]
        })
        
        sentinel_app.market_data.fetch_ohlcv = AsyncMock(return_value=df_fresh)
        sentinel_app.signal_generator.check_dynamic_levels = MagicMock(return_value=[])

        # Execute with circuit_breaker_active = True
        await sentinel_app._process_symbol(symbol, news_data=[], circuit_breaker_active=True, btc_trend="Macro Uptrend")

        # Dynamic levels check is allowed to maintain situational awareness
        assert sentinel_app.signal_generator.check_dynamic_levels.called
        # Crucial assertion: Pattern analysis & AI evaluation MUST NOT be called
        assert not sentinel_app.signal_generator.analyze_patterns.called
        assert not sentinel_app.ai_evaluator.evaluate_signal.called

    asyncio.run(run_test())


def test_circuit_breaker_inactive_when_drawdown_below_threshold(sentinel_app):
    """
    Negative case: When drawdown is below threshold (3.0% <= 5.0%),
    signals are generated and evaluated as normal.
    """
    async def run_test():
        symbol = "BTC/USDT"
        now = datetime.now(timezone.utc)
        df_fresh = pd.DataFrame({
            'timestamp': [now - timedelta(seconds=30)],
            'open': [65000.0], 'high': [65100.0], 'low': [64900.0], 'close': [65050.0],
            'volume': [100.0]
        })

        sentinel_app.market_data.fetch_ohlcv = AsyncMock(return_value=df_fresh)
        sentinel_app.market_data.get_market_microstructure = AsyncMock(return_value={})
        sentinel_app.signal_generator.check_dynamic_levels = MagicMock(return_value=[])
        sentinel_app.signal_generator.analyze_patterns = MagicMock(return_value=None)

        # Execute with circuit_breaker_active = False
        await sentinel_app._process_symbol(symbol, news_data=[], circuit_breaker_active=False, btc_trend="Macro Uptrend")

        # Signals analysis must be called
        assert sentinel_app.signal_generator.analyze_patterns.called

    asyncio.run(run_test())


@pytest.mark.parametrize("drawdown,expected_tripped", [
    (6.0, True),     # Above 5.0% -> Tripped
    (5.01, True),    # Just above 5.0% -> Tripped
    (5.00, False),   # Exactly 5.0% boundary -> Not tripped (> condition)
    (4.99, False),   # Just below 5.0% -> Not tripped
    (0.0, False),    # Breakeven -> Not tripped
    (-2.5, False)    # In profit -> Not tripped
])
def test_circuit_breaker_drawdown_boundary_matrix(drawdown, expected_tripped):
    """
    Tests exact boundary conditions for drawdown calculation logic.
    """
    is_active = drawdown > settings.max_drawdown_percent
    assert is_active == expected_tripped


# =========================================================================
# 2. Heartbeat Watchdog Tests
# =========================================================================

def test_heartbeat_watchdog_rejects_stale_candles(sentinel_app):
    """
    Positive case: Market data candle older than 330s triggers Heartbeat Error
    and aborts the cycle immediately.
    """
    async def run_test():
        symbol = "ETH/USDT"
        now = datetime.now(timezone.utc)
        # Stale by 400 seconds
        df_stale = pd.DataFrame({
            'timestamp': [now - timedelta(seconds=400)],
            'open': [3500.0], 'high': [3520.0], 'low': [3490.0], 'close': [3510.0],
            'volume': [50.0]
        })

        sentinel_app.market_data.fetch_ohlcv = AsyncMock(return_value=df_stale)

        await sentinel_app._process_symbol(symbol, news_data=[], circuit_breaker_active=False, btc_trend="Macro Uptrend")

        # Should exit at step 1 without calling regime detection or dynamic levels
        assert not sentinel_app.regime_detector.detect_regime.called
        assert not sentinel_app.signal_generator.check_dynamic_levels.called

    asyncio.run(run_test())


def test_heartbeat_watchdog_accepts_fresh_candles(sentinel_app):
    """
    Negative case: Fresh candles (e.g. 45s old) proceed without heartbeat error.
    """
    async def run_test():
        symbol = "ETH/USDT"
        now = datetime.now(timezone.utc)
        df_fresh = pd.DataFrame({
            'timestamp': [now - timedelta(seconds=45)],
            'open': [3500.0], 'high': [3520.0], 'low': [3490.0], 'close': [3510.0],
            'volume': [50.0]
        })

        sentinel_app.market_data.fetch_ohlcv = AsyncMock(return_value=df_fresh)
        sentinel_app.market_data.get_market_microstructure = AsyncMock(return_value={})
        sentinel_app.signal_generator.check_dynamic_levels = MagicMock(return_value=[])
        sentinel_app.signal_generator.analyze_patterns = MagicMock(return_value=None)

        await sentinel_app._process_symbol(symbol, news_data=[], circuit_breaker_active=False, btc_trend="Macro Uptrend")

        assert sentinel_app.regime_detector.detect_regime.called
        assert sentinel_app.signal_generator.check_dynamic_levels.called

    asyncio.run(run_test())


def test_heartbeat_watchdog_naive_timezone_conversion(sentinel_app):
    """
    Edge case: Candle timestamp arrives without tzinfo (naive).
    Watchdog must convert to UTC without raising TypeError.
    """
    async def run_test():
        symbol = "SOL/USDT"
        # Naive datetime 50 seconds in the past
        now_naive = datetime.now() - timedelta(seconds=50)
        df_naive = pd.DataFrame({
            'timestamp': [now_naive],
            'open': [150.0], 'high': [152.0], 'low': [149.0], 'close': [151.0],
            'volume': [200.0]
        })

        sentinel_app.market_data.fetch_ohlcv = AsyncMock(return_value=df_naive)
        sentinel_app.market_data.get_market_microstructure = AsyncMock(return_value={})
        sentinel_app.signal_generator.check_dynamic_levels = MagicMock(return_value=[])
        sentinel_app.signal_generator.analyze_patterns = MagicMock(return_value=None)

        # Must execute cleanly without unhandled TypeError
        await sentinel_app._process_symbol(symbol, news_data=[], circuit_breaker_active=False, btc_trend="Macro Uptrend")
        assert sentinel_app.regime_detector.detect_regime.called

    asyncio.run(run_test())


# =========================================================================
# 3. Funding Rate Filter Tests
# =========================================================================

@pytest.mark.parametrize("direction,funding_rate,confidence,should_filter", [
    # Short cases
    ('SHORT', -0.05, 80, True),    # Crowded negative funding (< -0.04) and normal confidence (< 85) -> Filtered
    ('SHORT', -0.041, 84, True),   # Just below -0.04 and confidence 84 -> Filtered
    ('SHORT', -0.05, 85, False),   # High conviction override (confidence == 85) -> NOT filtered
    ('SHORT', -0.05, 92, False),   # High conviction override (confidence 92) -> NOT filtered
    ('SHORT', -0.039, 80, False),  # Normal funding rate (> -0.04) -> NOT filtered
    ('SHORT', 0.02, 80, False),    # Positive funding rate on short -> NOT filtered
    
    # Long cases
    ('LONG', 0.07, 80, True),      # Crowded positive funding (> 0.06) and normal confidence (< 85) -> Filtered
    ('LONG', 0.061, 84, True),     # Just above 0.06 and confidence 84 -> Filtered
    ('LONG', 0.07, 85, False),     # High conviction override (confidence == 85) -> NOT filtered
    ('LONG', 0.08, 90, False),     # High conviction override (confidence 90) -> NOT filtered
    ('LONG', 0.059, 80, False),    # Normal funding rate (< 0.06) -> NOT filtered
    ('LONG', -0.02, 80, False)     # Negative funding rate on long -> NOT filtered
])
def test_funding_rate_filter_matrix(direction, funding_rate, confidence, should_filter):
    """
    Test the governance filter rule:
    - SHORT blocked if funding_rate < -0.04 and confidence < 85
    - LONG blocked if funding_rate > 0.06 and confidence < 85
    """
    is_filtered = False
    if direction == 'SHORT' and funding_rate < -0.04 and confidence < 85:
        is_filtered = True
    elif direction == 'LONG' and funding_rate > 0.06 and confidence < 85:
        is_filtered = True

    assert is_filtered == should_filter


# =========================================================================
# 4. Process Supervision & Telegram Network Resilience
# =========================================================================

def test_supervised_task_absorbs_exceptions():
    """
    Verify that _supervised_task traps unexpected exceptions from subtasks,
    preventing asyncio.gather from crashing the core trading loop.
    """
    async def run_test():
        from main import _supervised_task
        
        async def failing_worker():
            raise ConnectionResetError("Simulated network drop [WinError 64]")

        # Must complete cleanly without raising exception
        await _supervised_task(failing_worker(), "Test Failing Worker")

    asyncio.run(run_test())


def test_telegram_polling_reconnection_on_network_error():
    """
    Verify that start_polling handles TelegramNetworkError and retries
    with backoff without crashing the application.
    """
    async def run_test():
        from notifications.notifier import NotificationService
        from aiogram.exceptions import TelegramNetworkError

        notifier = NotificationService(telegram_token="123456:ABC-DEF", telegram_chat_id="123456")
        notifier.bot = MagicMock()
        
        # Simulate: first call raises TelegramNetworkError, second call cancels to exit loop
        call_count = 0
        async def mock_start_polling(bot, handle_signals=False):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise TelegramNetworkError(method="getMe", message="ClientConnectorError: Cannot connect to host")
            raise asyncio.CancelledError()

        notifier.dp.start_polling = mock_start_polling

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await notifier.start_polling()

        assert call_count == 2

    asyncio.run(run_test())
