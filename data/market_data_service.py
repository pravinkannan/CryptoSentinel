"""
MarketDataService handles all ccxt API interactions.
Follows SRP by only fetching and formatting market data without business logic.
"""

import logging
import asyncio
from typing import Optional, Dict, Any
import ccxt.async_support as ccxt
import pandas as pd
from core.exceptions import ExchangeAPIError
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

class MarketDataService:
    """Service to fetch OHLCV, funding rates, open interest, and order book data."""

    def __init__(self, exchange_id: str):
        try:
            exchange_class = getattr(ccxt, exchange_id.lower())
            self.exchange = exchange_class({'enableRateLimit': True})
        except AttributeError:
            raise ExchangeAPIError(f"Exchange {exchange_id} is not supported by ccxt.")

    async def close(self) -> None:
        """Close the async HTTP session of the exchange."""
        await self.exchange.close()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def _fetch_ohlcv_with_retry(self, symbol: str, timeframe: str, limit: int):
        return await self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)

    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> Optional[pd.DataFrame]:
        """Fetch OHLCV data and convert to pandas DataFrame."""
        try:
            ohlcv = await self._fetch_ohlcv_with_retry(symbol, timeframe, limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            return df
        except Exception as e:
            logger.error(f"Error fetching OHLCV for {symbol} on {timeframe}: {e}")
            return None

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def _fetch_funding_rate_with_retry(self, future_symbol: str):
        return await self.exchange.fetch_funding_rate(future_symbol)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def _fetch_open_interest_with_retry(self, future_symbol: str):
        return await self.exchange.fetch_open_interest(future_symbol)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def _fetch_order_book_with_retry(self, symbol: str, limit: int = 20):
        return await self.exchange.fetch_order_book(symbol, limit=limit)

    async def get_market_microstructure(self, symbol: str) -> Dict[str, float]:
        """Fetch funding rate, open interest, and order book imbalance."""
        future_symbol = f"{symbol}:{symbol.split('/')[1]}" if '/' in symbol and ':' not in symbol else symbol
        
        funding_rate, open_interest, imbalance_ratio = 0.0, 0.0, 1.0
        best_bid, best_ask = 0.0, 0.0
        
        try:
            if self.exchange.has.get('fetchFundingRate'):
                fr = await self._fetch_funding_rate_with_retry(future_symbol)
                funding_rate = float(fr.get('fundingRate', 0.0)) * 100
        except Exception as e:
            logger.debug(f"Funding rate fetch failed for {symbol}: {e}")
            
        try:
            if self.exchange.has.get('fetchOpenInterest'):
                oi = await self._fetch_open_interest_with_retry(future_symbol)
                open_interest = float(oi.get('openInterestValue', oi.get('baseVolume', 0.0)))
        except Exception as e:
            logger.debug(f"Open interest fetch failed for {symbol}: {e}")
            
        try:
            if self.exchange.has.get('fetchOrderBook'):
                try: 
                    ob = await self._fetch_order_book_with_retry(future_symbol)
                except Exception: 
                    ob = await self._fetch_order_book_with_retry(symbol)
                
                bids_vol = sum(float(b[1]) for b in ob.get('bids', []))
                asks_vol = sum(float(a[1]) for a in ob.get('asks', []))
                
                if ob.get('bids'): best_bid = float(ob['bids'][0][0])
                if ob.get('asks'): best_ask = float(ob['asks'][0][0])
                
                if asks_vol > 0: 
                    imbalance_ratio = bids_vol / asks_vol
                else: 
                    imbalance_ratio = 999.0 if bids_vol > 0 else 1.0
        except Exception as e:
            logger.debug(f"Order book fetch failed for {symbol}: {e}")
            
        return {
            'funding_rate': funding_rate,
            'open_interest': open_interest,
            'imbalance_ratio': imbalance_ratio,
            'best_bid': best_bid,
            'best_ask': best_ask
        }
