import pytest
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock
import ccxt.async_support as ccxt
from data.market_data_service import MarketDataService

def test_resilient_failover_sticky_switch():
    """Verify that when primary exchange fails, it switches to fallback and stays sticky."""
    async def run():
        service = MarketDataService(
            exchange_id='binance',
            fallback_exchanges=['bybit'],
            probe_cooldown_minutes=15
        )
        
        mock_binance = MagicMock()
        mock_binance.fetch_ohlcv = AsyncMock(side_effect=ccxt.RequestTimeout("Binance timed out"))
        
        sample_candles = [
            [1700000000000, 50000.0, 50100.0, 49900.0, 50050.0, 100.0],
            [1700003600000, 50050.0, 50200.0, 50000.0, 50150.0, 120.0],
        ]
        mock_bybit = MagicMock()
        mock_bybit.fetch_ohlcv = AsyncMock(return_value=sample_candles)
        
        service._exchanges['binance'] = mock_binance
        service._exchanges['bybit'] = mock_bybit
        
        assert service.active_exchange_id == 'binance'
        
        # 1. First call fails on Binance -> switches to Bybit -> returns Bybit candles
        df1 = await service.fetch_ohlcv('BTC/USDT', '1h')
        assert df1 is not None
        assert len(df1) == 2
        assert service.active_exchange_id == 'bybit'
        assert mock_binance.fetch_ohlcv.call_count >= 1
        assert mock_bybit.fetch_ohlcv.call_count == 1
        
        # 2. Second call must be sticky: directly call Bybit without calling Binance
        binance_calls_before = mock_binance.fetch_ohlcv.call_count
        df2 = await service.fetch_ohlcv('ETH/USDT', '1h')
        assert df2 is not None
        assert service.active_exchange_id == 'bybit'
        assert mock_binance.fetch_ohlcv.call_count == binance_calls_before
        assert mock_bybit.fetch_ohlcv.call_count == 2
        
        await service.close()

    asyncio.run(run())

def test_recovery_probe_reconnects_primary():
    """Verify that periodic probe restores primary exchange if it recovers."""
    async def run():
        service = MarketDataService(
            exchange_id='binance',
            fallback_exchanges=['bybit'],
            probe_cooldown_minutes=15
        )
        
        mock_binance = MagicMock()
        mock_binance.has = {'fetchTime': True}
        mock_binance.fetch_time = AsyncMock(return_value=1700000000000)
        mock_binance.fetch_ohlcv = AsyncMock(return_value=[
            [1700000000000, 50000.0, 50100.0, 49900.0, 50050.0, 100.0]
        ])
        
        mock_bybit = MagicMock()
        mock_bybit.fetch_ohlcv = AsyncMock(return_value=[
            [1700000000000, 50000.0, 50100.0, 49900.0, 50050.0, 100.0]
        ])
        
        service._exchanges['binance'] = mock_binance
        service._exchanges['bybit'] = mock_bybit
        service.active_exchange_id = 'bybit'
        
        # Simulate failed binance in the past (>15 minutes ago)
        service._last_failed_times['binance'] = time.time() - 1000.0
        
        # Next call should trigger probe, detect binance is healthy, and switch back to binance
        df = await service.fetch_ohlcv('BTC/USDT', '1h')
        assert df is not None
        assert service.active_exchange_id == 'binance'
        assert mock_binance.fetch_time.called
        
        await service.close()

    asyncio.run(run())

def test_bad_symbol_linear_contract_fallback():
    """Verify that ccxt.BadSymbol automatically falls back to linear perpetual contract."""
    async def run():
        service = MarketDataService(
            exchange_id='bybit',
            fallback_exchanges=[]
        )
        
        mock_bybit = MagicMock()
        
        async def mock_fetch(symbol, timeframe, limit=250):
            if symbol == 'ZEC/USDT':
                raise ccxt.BadSymbol("Symbol not found on spot")
            elif symbol == 'ZEC/USDT:USDT':
                return [[1700000000000, 1200.0, 1210.0, 1190.0, 1205.0, 50.0]]
            raise ValueError(f"Unexpected symbol {symbol}")
            
        mock_bybit.fetch_ohlcv = AsyncMock(side_effect=mock_fetch)
        service._exchanges['bybit'] = mock_bybit
        
        df = await service.fetch_ohlcv('ZEC/USDT', '1h')
        assert df is not None
        assert len(df) == 1
        assert df['close'].iloc[0] == 1205.0
        
        await service.close()

    asyncio.run(run())
