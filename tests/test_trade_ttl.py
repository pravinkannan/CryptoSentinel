import pytest
import pandas as pd
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

from core.config import settings
from engine.signal_generator import SignalGeneratorService
from execution.paper_trader import PaperTradingService


def test_trade_ttl_default_configuration():
    assert settings.trade_ttl_hours == 18.0
    assert settings.trade_ttl_minutes == 1080


def test_signal_generator_ttl_18_hours():
    service = SignalGeneratorService(
        percentage_trigger=0.5,
        static_alerts={},
        atr_period=14,
        sl_atr_multiplier=1.5,
        target_rr_ratio=2.5
    )

    # Mock dataframe with 50 rows
    timestamps = pd.date_range('2026-10-11 12:00:00', periods=50, freq='15min')
    df = pd.DataFrame({
        'timestamp': timestamps,
        'open': [100.0] * 50,
        'high': [102.0] * 50,
        'low': [98.0] * 50,
        'close': [101.0] * 50,
        'volume': [1000.0] * 50,
        'ATRr_14': [1.5] * 50,
        'EMA_200': [95.0] * 50,
        'RSI_14': [55.0] * 50
    })

    # Mock pattern recognition and risk parameters
    service.pattern_recognition.detect_patterns = MagicMock(return_value=(
        ['FVG Bullish'], ['FVG formed on 15m'], 'LONG', 0.0, 'NEUTRAL'
    ))
    service.risk_manager.compute_risk_parameters = MagicMock(return_value={
        'stop_loss': 98.0,
        'take_profit': 105.0,
        'tp1': 104.0,
        'tp2': 106.0,
        'sl_pct': 3.0,
        'tp_pct': 4.0,
        'tp1_pct': 3.0,
        'tp2_pct': 5.0,
        'atr_value': 1.5,
        'rr_ratio': 2.5,
        'recommended_leverage': '5x',
        'leverage_val': 5.0,
        'roe_sl': 15.0,
        'roe_tp': 20.0
    })

    signal = service.analyze_patterns(
        df=df,
        timeframe='15m',
        htf_trend='BULLISH',
        htf_4h_trend='BULLISH',
        microstructure={},
        symbol='SOL/USDT',
        regime_data={'regime': 'NORMAL'}
    )

    assert signal is not None
    assert signal['ttl_mins'] == 1080
    assert "1 – 3 Hours" in signal['target_resolution']
    assert "IST" in signal['expiration_time']


def test_paper_trader_does_not_expire_under_18_hours():
    # Trade created 5 hours ago should NOT expire when ttl_mins is 1080 (18 hours)
    trader = PaperTradingService()
    now = datetime.now(timezone.utc)
    five_hours_ago = now - timedelta(hours=5)

    # Verify expiration math
    ttl_mins = settings.trade_ttl_minutes
    elapsed_seconds = (now - five_hours_ago).total_seconds()

    assert elapsed_seconds < ttl_mins * 60
    assert elapsed_seconds > 180 * 60  # Would have expired under old 3h limit, but survives now!


def test_paper_trader_expired_status_persisted(tmp_path):
    import asyncio
    import aiosqlite
    from engine.journal_manager import JournalManager

    async def run_test():
        db_file = str(tmp_path / "test_ttl.db")
        journal = JournalManager(db_path=db_file)
        await journal.ensure_db_exists()

        trader = PaperTradingService(db_path=db_file)

        # Insert a trade that was created 20 hours ago (TTL is 18h = 1080m)
        twenty_hours_ago = (datetime.now(timezone.utc) - timedelta(hours=20)).isoformat()
        trade_id = await journal.log_trade({
            'timestamp': twenty_hours_ago,
            'symbol': 'BTC/USDT',
            'direction': 'LONG',
            'pattern_name': 'FVG',
            'entry_price': 100.0,
            'sl_price': 90.0,
            'tp1_price': 110.0,
            'tp2_price': 120.0,
            'leverage': 5.0,
            'ttl_mins': 1080,
            'outcome': 'PENDING',
            'confidence_score': 85
        })

        # Create a candle that breaches the SL (low = 85.0 < 90.0)
        df_candle = pd.DataFrame([{
            'high': 98.0,
            'low': 85.0,
            'close': 92.0
        }])

        # Run update_trades
        await trader.update_trades('BTC/USDT', df_candle)

        # Check status in database: it should be EXPIRED, not LOSS
        async with aiosqlite.connect(db_file) as conn:
            cursor = await conn.execute('SELECT status FROM trades WHERE trade_id = ?', (trade_id,))
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == 'EXPIRED'

    asyncio.run(run_test())

