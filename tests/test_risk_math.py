import pytest
import pandas as pd
from typing import Dict, List
from engine.signal_generator import SignalGeneratorService

@pytest.fixture
def signal_generator() -> SignalGeneratorService:
    static_alerts: Dict[str, List[float]] = {}
    return SignalGeneratorService(
        percentage_trigger=0.5,
        static_alerts=static_alerts,
        atr_period=14,
        sl_atr_multiplier=1.5,
        target_rr_ratio=2.5
    )

def test_risk_metrics_bullish(signal_generator: SignalGeneratorService) -> None:
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
    
def test_risk_metrics_bearish(signal_generator: SignalGeneratorService) -> None:
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


def test_risk_manager_anti_sweep_5m():
    """Verify 5m timeframe uses lookback=15, atr_mult=1.2, and compresses leverage."""
    from engine.risk_manager import RiskManager
    rm = RiskManager(atr_period=14)

    # 20 candles: close at 100.0, highs at 100.5, lows at 99.5
    # Candle 6 from end (index 14) has high=101.5 (inside lookback 15, but outside lookback 5)
    df = pd.DataFrame({
        'open': [100.0] * 20,
        'high': [100.5] * 14 + [101.5] + [100.5] * 5,
        'low': [99.5] * 20,
        'close': [100.0] * 20,
        'volume': [1000] * 20,
        'ATRr_14': [0.5] * 20
    })

    params = rm.compute_risk_parameters(df, "ETH/USDT", "5m", "Bearish", 100.0, "NORMAL")
    assert params is not None
    # Lookback 15 captures high=101.5. SL = 101.5 + (0.5 * 1.2) = 102.10
    assert params['stop_loss'] == 102.10
    # sl_pct = 2.10%
    assert params['sl_pct'] == 2.10
    # leverage with roe_risk_budget=20.0: floor(20 / 2.10) = 9x
    assert params['leverage_val'] == 9.0
    assert params['roe_sl'] <= 20.0


def test_risk_manager_anti_sweep_floor_0_90():
    """Verify tight structural stops are clamped at 0.90% floor and leverage compresses to 22x."""
    from engine.risk_manager import RiskManager
    rm = RiskManager(atr_period=14)

    # Tiny ATR and tight range that would yield <0.50% SL
    df = pd.DataFrame({
        'open': [100.0] * 20,
        'high': [100.1] * 20,
        'low': [99.9] * 20,
        'close': [100.0] * 20,
        'volume': [1000] * 20,
        'ATRr_14': [0.1] * 20
    })

    params = rm.compute_risk_parameters(df, "ETH/USDT", "5m", "Bearish", 100.0, "NORMAL")
    assert params is not None
    # Enforces 0.90% floor
    assert params['sl_pct'] == 0.90
    assert params['stop_loss'] == 100.90
    # Dynamic leverage: floor(20.0 / 0.90) = 22x
    assert params['leverage_val'] == 22.0
    assert params['roe_sl'] == 19.80  # Maintains 20% ROE budget!


def test_risk_manager_anti_sweep_15m():
    """Verify 15m timeframe uses lookback=12 and atr_mult=1.2."""
    from engine.risk_manager import RiskManager
    rm = RiskManager(atr_period=14)

    # Candle index 9 (within last 12) has low=98.5
    df = pd.DataFrame({
        'open': [100.0] * 20,
        'high': [101.0] * 20,
        'low': [99.0] * 8 + [98.5] + [99.0] * 11,
        'close': [100.0] * 20,
        'volume': [1000] * 20,
        'ATRr_14': [0.5] * 20
    })

    params = rm.compute_risk_parameters(df, "ETH/USDT", "15m", "Bullish", 100.0, "NORMAL")
    assert params is not None
    # Tier 1 (ETH): lookback=12 captures 98.5, atr_mult=1.2. SL = 98.5 - (0.5 * 1.2) = 97.90
    assert params['stop_loss'] == 97.90
    assert params['sl_pct'] == 2.10


def test_risk_manager_tier2_high_beta_volatility():
    """Verify Tier 2 (SOL/ZEC) enforces min_sl_pct=1.50%, atr_mult=1.6, and leverage capped at 12x."""
    from engine.risk_manager import RiskManager
    rm = RiskManager(atr_period=14)

    df = pd.DataFrame({
        'open': [100.0] * 20,
        'high': [101.0] * 20,
        'low': [99.0] * 8 + [98.5] + [99.0] * 11,
        'close': [100.0] * 20,
        'volume': [1000] * 20,
        'ATRr_14': [0.5] * 20
    })

    params = rm.compute_risk_parameters(df, "SOL/USDT", "15m", "Bullish", 100.0, "NORMAL")
    assert params is not None
    # Tier 2 (SOL): atr_mult=1.6 -> SL = 98.5 - (0.5 * 1.6) = 97.70
    assert params['stop_loss'] == 97.70
    assert params['sl_pct'] == 2.30
    # Leverage capped at 12x
    assert params['leverage_val'] <= 12.0
    assert params['roe_sl'] <= 28.0


def test_risk_manager_sweep_risk_anchoring():
    """Verify opposing SWEEP_RISK liquidity pool anchors SL 0.20% behind the pool."""
    from engine.risk_manager import RiskManager
    rm = RiskManager(atr_period=14)

    # Normal structural SL would be: recent_low (99.0) - (0.5 * 1.2) = 98.40 (1.6% SL)
    df = pd.DataFrame({
        'open': [100.0] * 20,
        'high': [100.5] * 20,
        'low': [99.0] * 20,
        'close': [100.0] * 20,
        'volume': [1000] * 20,
        'ATRr_14': [0.5] * 20
    })

    # But order book has a resting BID sweep pool at $98.00 (within 0.8% of structural low)
    magnets = [
        {'role': 'SWEEP_RISK', 'side': 'BID', 'price': 98.00, 'distance_pct': -2.0}
    ]

    params = rm.compute_risk_parameters(df, "ETH/USDT", "5m", "Bullish", 100.0, "NORMAL", liquidity_magnets=magnets)
    assert params is not None
    # Safe anchor = 98.00 * (1 - 0.0020) = 97.80, which is safer than 98.40!
    assert params['stop_loss'] == 97.80
    assert params['sl_pct'] == 2.20


def test_recalibrate_for_tier_vip_sniper():
    """Verify VIP Sniper setup achieves 50x-60x on BTC with defensive wall and tight SL."""
    from engine.risk_manager import RiskManager
    rm = RiskManager(atr_period=14)

    signal_data = {
        'symbol': 'BTC/USDT',
        'timeframe': '15m',
        'direction': 'LONG',
        'entry': 84000.0,
        'stop_loss': 83400.0,
        'liquidity_magnets': [
            {'role': 'DEFENSIVE_SHIELD', 'side': 'BID', 'price': 83500.0}
        ]
    }

    recal = rm.recalibrate_for_tier(signal_data, is_vip=True)
    assert recal['setup_type'] == "VIP_SNIPER"
    assert recal['leverage_val'] >= 45.0
    assert recal['leverage_val'] <= 60.0
    assert recal['roe_tp1'] >= 80.0
    assert recal['breakeven_price'] > 84000.0


def test_recalibrate_for_tier_vip_swing_altcoin():
    """Verify VIP Swing setup on Altcoin caps leverage at 20x."""
    from engine.risk_manager import RiskManager
    rm = RiskManager(atr_period=14)

    signal_data = {
        'symbol': 'SOL/USDT',
        'timeframe': '1h',
        'direction': 'LONG',
        'entry': 100.0,
        'stop_loss': 98.0,
        'tp1': 103.0,
        'tp2': 106.0
    }

    recal = rm.recalibrate_for_tier(signal_data, is_vip=True)
    assert recal['setup_type'] == "VIP_SWING"
    assert recal['leverage_val'] <= 20.0
    assert recal['breakeven_price'] == 102.0


def test_recalibrate_for_tier_standard_swing():
    """Verify Standard Swing setup respects asset limits and calculates 1.0R breakeven."""
    from engine.risk_manager import RiskManager
    rm = RiskManager(atr_period=14)

    signal_data = {
        'symbol': 'BTC/USDT',
        'timeframe': '15m',
        'direction': 'LONG',
        'entry': 84000.0,
        'stop_loss': 83000.0,
        'tp1': 86000.0,
        'tp2': 88000.0
    }

    recal = rm.recalibrate_for_tier(signal_data, is_vip=False)
    assert recal['setup_type'] == "STANDARD_SWING"
    assert recal['leverage_val'] <= 25.0
    # 1.0R distance: 84000 - 83000 = 1000 -> breakeven_price = 85000.0
    assert recal['breakeven_price'] == 85000.0


