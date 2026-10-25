"""
Enterprise unit tests for Dynamic Support/Resistance Levels and Watched Price Alerts.
Tests:
- Dynamic psychological level calculation across multiple asset price tiers
- 24h High / Low calculation (>= 288 candles) vs Recent High / Low (< 288 candles)
- Alert trigger window (+/- 0.2% band)
- Alert deduplication / anti-spam mechanism
- Level reset upon price departure and re-trigger on subsequent crosses
"""

import pytest
import pandas as pd
from engine.signal_generator import SignalGeneratorService
from core.state_manager import StateManager


@pytest.fixture
def signal_generator():
    """Returns a standard SignalGeneratorService instance."""
    return SignalGeneratorService(
        percentage_trigger=2.0,
        static_alerts={},
        atr_period=14,
        sl_atr_multiplier=1.5,
        target_rr_ratio=2.0
    )


# =========================================================================
# 1. Psychological Level Rounding Tests
# =========================================================================

@pytest.mark.parametrize("price,expected_key,expected_level", [
    (65432.0, "Psychological 65000.0", 65000.0),   # Tier > 10,000 (step = 1000)
    (65800.0, "Psychological 66000.0", 66000.0),
    (3456.0, "Psychological 3500.0", 3500.0),      # Tier > 1,000 (step = 100)
    (3410.0, "Psychological 3400.0", 3400.0),
    (154.2, "Psychological 150.0", 150.0),         # Tier > 100 (step = 10)
    (157.8, "Psychological 160.0", 160.0),
    (18.7, "Psychological 19.0", 19.0),            # Tier > 10 (step = 1)
    (18.2, "Psychological 18.0", 18.0),
    (0.164, "Psychological 0.2", 0.2),             # Tier <= 10 (step = 0.1)
    (0.83, "Psychological 0.8", 0.8)
])
def test_psychological_level_rounding_tiers(signal_generator, price, expected_key, expected_level):
    """
    Positive case: Verifies that rounding steps correctly scale according to asset price tiers.
    """
    levels = signal_generator.get_dynamic_levels(price, "TEST/USDT")
    assert expected_key in levels
    assert levels[expected_key] == pytest.approx(expected_level, 0.001)


# =========================================================================
# 2. Historical High / Low S/R Tests
# =========================================================================

def test_24h_high_low_with_sufficient_bars(signal_generator):
    """
    Positive case: When DataFrame has >= 288 candles (24h of 5m data),
    calculate '24h High' and '24h Low' from tail(288).
    """
    n = 300
    highs = [100.0] * n
    lows = [90.0] * n
    # Set an extreme high beyond the 288-window (should be excluded)
    highs[5] = 999.0
    # Set the actual 24h high inside the last 288 bars
    highs[-10] = 120.0
    lows[-20] = 85.0

    df = pd.DataFrame({'high': highs, 'low': lows})
    levels = signal_generator.get_dynamic_levels(100.0, "BTC/USDT", df=df)

    assert "24h High" in levels
    assert "24h Low" in levels
    assert levels["24h High"] == 120.0  # Excludes the older 999.0 bar
    assert levels["24h Low"] == 85.0


def test_recent_high_low_fallback_with_few_bars(signal_generator):
    """
    Negative/Fallback case: When DataFrame has < 288 candles,
    calculate 'Recent High' and 'Recent Low' from available data.
    """
    n = 50
    highs = [100.0] * n
    lows = [90.0] * n
    highs[10] = 108.0
    lows[15] = 88.0

    df = pd.DataFrame({'high': highs, 'low': lows})
    levels = signal_generator.get_dynamic_levels(95.0, "BTC/USDT", df=df)

    assert "Recent High" in levels
    assert "Recent Low" in levels
    assert "24h High" not in levels
    assert levels["Recent High"] == 108.0
    assert levels["Recent Low"] == 88.0


def test_dynamic_levels_none_dataframe_edge_case(signal_generator):
    """
    Edge case: When df is None, get_dynamic_levels succeeds safely
    with only psychological levels.
    """
    levels = signal_generator.get_dynamic_levels(64000.0, "BTC/USDT", df=None)
    assert len(levels) == 1
    assert "Psychological 64000.0" in levels


# =========================================================================
# 3. Cross Alerts & Anti-Spam Deduplication Tests
# =========================================================================

def test_check_dynamic_levels_cross_and_deduplication(signal_generator):
    """
    Test alert trigger, deduplication, price exit, and re-trigger lifecycle:
    1. First cross -> Alert emitted and key recorded.
    2. Repeated check while hovering -> Alert suppressed (anti-spam).
    3. Price moves away -> Key removed from active state.
    4. Price returns -> Alert emitted again.
    """
    state_mgr = StateManager(filename="test_mem.json")
    symbol = "BTC/USDT"
    target_level = 65000.0

    # 1. Price enters tolerance band (65000 * 0.999 = 64935.0, inside [64870, 65130])
    price_touch = 64950.0
    alerts = signal_generator.check_dynamic_levels(symbol, price_touch, state_mgr)
    
    assert len(alerts) >= 1
    assert any(name == "Psychological 65000.0" and lvl == target_level for name, lvl in alerts)
    assert "Psychological 65000.0_65000.0" in state_mgr.state["last_crossed_alerts"][symbol]

    # 2. Price hovers in the same band on next cycle (e.g. 64980.0) -> Must NOT alert again
    alerts_hover = signal_generator.check_dynamic_levels(symbol, 64980.0, state_mgr)
    assert not any(lvl == target_level for _, lvl in alerts_hover)

    # 3. Price breaks away outside tolerance window (e.g. 66000.0 > 65000 * 1.002)
    # The alert key for 65000.0 must be cleared
    signal_generator.check_dynamic_levels(symbol, 66000.0, state_mgr)
    assert "Psychological 65000.0_65000.0" not in state_mgr.state["last_crossed_alerts"].get(symbol, [])

    # 4. Price returns back to 65000.0 band -> Must trigger a new alert
    alerts_retest = signal_generator.check_dynamic_levels(symbol, 65020.0, state_mgr)
    assert any(lvl == target_level for _, lvl in alerts_retest)


def test_dynamic_level_alerts_disabled_by_default(isolated_state_manager):
    """
    Verify that dynamic level notification alerts are disabled by default
    so they do not spam notifications or disturb trade signals.
    """
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    from datetime import datetime, timezone, timedelta
    from main import CryptoSentinelApp
    from core.config import settings

    assert settings.enable_dynamic_level_alerts is False

    async def run_test():
        with patch("main.StateManager", return_value=isolated_state_manager):
            app = CryptoSentinelApp()
            app.state_manager = isolated_state_manager
            app.repository = MagicMock()
            app.repository.save_trade = AsyncMock(return_value="TEST_TRADE_ID")
            app.repository.ensure_schema = AsyncMock()
            app.repository.evict_active_signal = AsyncMock(return_value=[])
            app.repository.save_active_signal = AsyncMock()
            app.market_data = MagicMock()
            app.notifier = MagicMock()
            app.notifier.notify_dynamic_level = AsyncMock()
            app.paper_trader = MagicMock()
            app.paper_trader.update_trades = AsyncMock()
            app.signal_generator = MagicMock()
            app.signal_generator.check_dynamic_levels = MagicMock(return_value=[("Psychological 110.0", 110.0)])
            app.signal_generator.analyze_patterns = MagicMock(return_value=None)
            app.regime_detector = MagicMock()
            app.regime_detector.detect_regime = MagicMock(return_value={"regime": "NORMAL", "phase": "CONSOLIDATION"})

            now = datetime.now(timezone.utc)
            df = pd.DataFrame({
                'timestamp': [now - timedelta(seconds=60)],
                'open': [110.1], 'high': [110.3], 'low': [109.9], 'close': [110.2],
                'volume': [100.0]
            })
            app.market_data.fetch_ohlcv = AsyncMock(return_value=df)

            with patch.object(settings, "enable_dynamic_level_alerts", False):
                await app._process_symbol("SOL/USDT", [], False, "Bullish")
                # When disabled, notify_dynamic_level must NOT be called
                app.notifier.notify_dynamic_level.assert_not_awaited()

            with patch.object(settings, "enable_dynamic_level_alerts", True):
                await app._process_symbol("SOL/USDT", [], False, "Bullish")
                # When enabled, notify_dynamic_level is called
                app.notifier.notify_dynamic_level.assert_awaited_once()

    asyncio.run(run_test())


def test_dynamic_level_alerts_cooldown_throttling(isolated_state_manager):
    """
    Verify that when dynamic level alerts are enabled, subsequent crosses of the same
    level within the cooldown window are throttled and not sent to Telegram.
    """
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    from datetime import datetime, timezone, timedelta
    from main import CryptoSentinelApp
    from core.config import settings

    async def run_test():
        with patch("main.StateManager", return_value=isolated_state_manager):
            app = CryptoSentinelApp()
            app.state_manager = isolated_state_manager
            app.repository = MagicMock()
            app.repository.save_trade = AsyncMock(return_value="TEST_TRADE_ID")
            app.repository.ensure_schema = AsyncMock()
            app.repository.evict_active_signal = AsyncMock(return_value=[])
            app.repository.save_active_signal = AsyncMock()
            app.market_data = MagicMock()
            app.notifier = MagicMock()
            app.notifier.notify_dynamic_level = AsyncMock()
            app.paper_trader = MagicMock()
            app.paper_trader.update_trades = AsyncMock()
            app.signal_generator = MagicMock()
            app.signal_generator.check_dynamic_levels = MagicMock(return_value=[("Psychological 110.0", 110.0)])
            app.signal_generator.analyze_patterns = MagicMock(return_value=None)
            app.regime_detector = MagicMock()
            app.regime_detector.detect_regime = MagicMock(return_value={"regime": "NORMAL", "phase": "CONSOLIDATION"})

            now = datetime.now(timezone.utc)
            df = pd.DataFrame({
                'timestamp': [now - timedelta(seconds=60)],
                'open': [110.1], 'high': [110.3], 'low': [109.9], 'close': [110.2],
                'volume': [100.0]
            })
            app.market_data.fetch_ohlcv = AsyncMock(return_value=df)

            with patch.object(settings, "enable_dynamic_level_alerts", True):
                # 1. First alert sent
                await app._process_symbol("SOL/USDT", [], False, "Bullish")
                assert app.notifier.notify_dynamic_level.await_count == 1

                # 2. Repeated trigger 5 minutes later -> Throttled by cooldown (120m)
                await app._process_symbol("SOL/USDT", [], False, "Bullish")
                assert app.notifier.notify_dynamic_level.await_count == 1  # Still 1!

    asyncio.run(run_test())
