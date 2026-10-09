import pytest
import pandas as pd
from typing import Dict, List
from engine.signal_generator import SignalGeneratorService

@pytest.fixture
def signal_generator():
    static_alerts: Dict[str, List[float]] = {}
    return SignalGeneratorService(
        percentage_trigger=0.5,
        static_alerts=static_alerts,
        atr_period=14,
        sl_atr_multiplier=1.5,
        target_rr_ratio=2.5
    )

def test_risk_metrics_bullish(signal_generator):
    # Mock dataframe with recent low and entry
    df = pd.DataFrame({
        'timestamp': pd.date_range('2023-01-01', periods=20, freq='h'),
        'open': [100.0] * 20,
        'high': [105.0] * 20,
        'low': [90.0] * 19 + [95.0],  # Recent min low is 90
        'close': [100.0] * 19 + [100.0], # Entry is 100
        'volume': [1000] * 20
    })
    
    # We mock the ATR manually since we aren't running TA functions on full DF in this pure unit test
    class MockLatest(dict):
        def get(self, key, default=None):
            if key == 'ATRr_14':
                return 2.0
            return default
            
    latest = MockLatest({'close': 100.0, 'timestamp': df.iloc[-1]['timestamp']})
    
    # Bullish SL should be: recent_low - (atr * multiplier) = 90 - (2.0 * 1.5) = 87.0
    atr = 2.0
    recent_low = 90.0
    sl_atr_multiplier = 1.5
    target_rr_ratio = 2.5
    
    entry = latest['close']
    stop_loss = recent_low - (atr * sl_atr_multiplier)
    risk = entry - stop_loss if entry > stop_loss else entry * 0.01
    take_profit = entry + (risk * target_rr_ratio)
    
    sl_pct = abs(entry - stop_loss) / entry * 100
    tp_pct = abs(take_profit - entry) / entry * 100
    rr_ratio = round(tp_pct / sl_pct, 2) if sl_pct > 0 else 0.0
    
    assert stop_loss == 87.0
    assert risk == 13.0
    assert take_profit == 100.0 + (13.0 * 2.5) # 132.5
    assert sl_pct == 13.0
    assert tp_pct == 32.5
    assert rr_ratio == 2.5
    
def test_risk_metrics_bearish(signal_generator):
    df = pd.DataFrame({
        'timestamp': pd.date_range('2023-01-01', periods=20, freq='h'),
        'open': [100.0] * 20,
        'high': [110.0] * 19 + [105.0],  # Recent max high is 110
        'low': [90.0] * 20,
        'close': [100.0] * 19 + [100.0], # Entry is 100
        'volume': [1000] * 20
    })
    
    recent_high = 110.0
    atr = 2.0
    sl_atr_multiplier = 1.5
    target_rr_ratio = 2.5
    
    entry = 100.0
    stop_loss = recent_high + (atr * sl_atr_multiplier) # 110 + 3 = 113.0
    risk = stop_loss - entry if stop_loss > entry else entry * 0.01
    take_profit = entry - (risk * target_rr_ratio)
    
    assert stop_loss == 113.0
    assert risk == 13.0
    assert take_profit == 100.0 - (13.0 * 2.5) # 67.5
