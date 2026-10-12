"""
Shared pytest fixtures for the Crypto Sentinel test suite.
Enterprise-standard fixture definitions for isolated, reproducible testing.
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional
from unittest.mock import AsyncMock, MagicMock
from core.state_manager import StateManager


@pytest.fixture
def make_ohlcv_df():
    """Factory fixture to create synthetic OHLCV DataFrames with configurable parameters."""
    def _generator(
        n_bars: int = 100,
        start_price: float = 60000.0,
        volatility: float = 100.0,
        freq_mins: int = 5,
        start_time: Optional[datetime] = None
    ) -> pd.DataFrame:
        if start_time is None:
            start_time = datetime.now(timezone.utc) - timedelta(minutes=freq_mins * n_bars)
        
        timestamps = [start_time + timedelta(minutes=freq_mins * i) for i in range(n_bars)]
        np.random.seed(42)
        price_changes = np.random.normal(0, volatility, n_bars)
        closes = np.maximum(start_price + np.cumsum(price_changes), 1.0)
        opens = np.roll(closes, 1)
        opens[0] = start_price
        
        highs = np.maximum(opens, closes) + np.abs(np.random.normal(10, 20, n_bars))
        lows = np.minimum(opens, closes) - np.abs(np.random.normal(10, 20, n_bars))
        volumes = np.random.uniform(50.0, 500.0, n_bars)
        
        df = pd.DataFrame({
            'timestamp': timestamps,
            'open': opens,
            'high': highs,
            'low': lows,
            'close': closes,
            'volume': volumes
        })
        return df
    return _generator


@pytest.fixture
def isolated_state_manager(tmp_path):
    """Provides an isolated StateManager pointing to a temporary file in tmp_path."""
    state_file = str(tmp_path / "test_sentinel_state.json")
    return StateManager(filename=state_file)


@pytest.fixture
def mock_notifier():
    """Mock NotificationService for verifying dispatch and alert messages."""
    notifier = MagicMock()
    notifier.send_telegram = AsyncMock(return_value=True)
    notifier.notify_signal = AsyncMock(return_value=True)
    notifier.notify_dynamic_level = AsyncMock(return_value=True)
    notifier.notify_error = AsyncMock(return_value=True)
    return notifier
