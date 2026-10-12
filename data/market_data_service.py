"""
MarketDataService handles all ccxt API interactions.
Follows SRP by only fetching and formatting market data without business logic.
"""

import logging
import asyncio
from typing import Optional, Dict, Any, List
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
    async def _fetch_order_book_with_retry(self, symbol: str, limit: int = 100):
        return await self.exchange.fetch_order_book(symbol, limit=limit)

    async def get_market_microstructure(self, symbol: str) -> Dict[str, Any]:
        """Fetch funding rate, open interest, and order book imbalance."""
        future_symbol = f"{symbol}:{symbol.split('/')[1]}" if '/' in symbol and ':' not in symbol else symbol
        
        funding_rate, open_interest, imbalance_ratio = 0.0, 0.0, 1.0
        best_bid, best_ask = 0.0, 0.0
        ob: Dict[str, Any] = {}
        
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
                    ob = await self._fetch_order_book_with_retry(future_symbol, limit=100)
                except Exception: 
                    ob = await self._fetch_order_book_with_retry(symbol, limit=100)
                
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
            'best_ask': best_ask,
            'order_book': ob
        }


def scan_liquidity_magnets(
    order_book: Dict[str, Any], 
    current_price: float, 
    depth_pct: float = 0.02, 
    multiplier: float = 3.0
) -> List[Dict[str, Any]]:
    """
    Scans the top depth_pct of the order book for anomalous liquidity clusters
    where clustered nominal volume >= multiplier * average surrounding level volume.
    """
    if not order_book or current_price <= 0:
        return []

    bucket_size = max(0.01, current_price * 0.0005)

    def cluster_side(orders: List[Any], side_name: str) -> List[Dict[str, Any]]:
        clusters: Dict[float, float] = {}
        for entry in orders:
            if not entry or len(entry) < 2:
                continue
            price_level, qty = float(entry[0]), float(entry[1])
            pct_dist = abs(price_level - current_price) / current_price
            if pct_dist > depth_pct:
                continue
            
            bucket = round(price_level / bucket_size) * bucket_size
            clusters[bucket] = clusters.get(bucket, 0.0) + (price_level * qty)

        if not clusters:
            return []

        avg_vol = sum(clusters.values()) / max(1, len(clusters))
        detected = []
        for bucket_price, total_usd in clusters.items():
            if total_usd >= avg_vol * multiplier and total_usd >= 100_000:
                detected.append({
                    'side': side_name,
                    'price': round(bucket_price, 2),
                    'volume_usd': round(total_usd, 2),
                    'volume_formatted': f"${total_usd / 1_000_000:.1f}M" if total_usd >= 1_000_000 else f"${int(total_usd / 1000)}k",
                    'ratio': round(total_usd / avg_vol, 1),
                    'distance_pct': round(((bucket_price - current_price) / current_price) * 100, 2)
                })
        return detected

    bids_clusters = cluster_side(order_book.get('bids', []), 'BID')
    asks_clusters = cluster_side(order_book.get('asks', []), 'ASK')

    all_magnets = sorted(bids_clusters + asks_clusters, key=lambda x: x['volume_usd'], reverse=True)
    return all_magnets[:3]


def enrich_magnets_with_actions(
    magnets: List[Dict[str, Any]], 
    direction: str, 
    entry: float, 
    stop_loss: float = 0.0, 
    take_profit: float = 0.0
) -> List[Dict[str, Any]]:
    """
    Enriches detected order book liquidity magnets with concrete tactical next actions
    tailored to the trade direction and key risk/reward levels.
    """
    enriched: List[Dict[str, Any]] = []
    is_long = direction.upper() in ["LONG", "BULLISH"]
    is_short = direction.upper() in ["SHORT", "BEARISH"]

    for m in magnets:
        item = dict(m)
        side = item.get('side', '').upper()
        price = float(item.get('price', 0.0))
        dist_pct = float(item.get('distance_pct', 0.0))
        vol_str = item.get('volume_formatted', '')

        # Default fallback
        role = "LIQUIDITY_POOL"
        action = f"Monitor price reaction at ${price:,.2f} for liquidity absorption or breakout."
        action_price = price

        if is_long:
            if side == "ASK" and price > entry:
                # Ask pool overhead (Target pull vs Resistance)
                if take_profit > 0 and price >= min(entry * 1.01, take_profit * 0.98):
                    role = "TARGET_MAGNET"
                    action_price = round(price * 0.998, 2)
                    action = (
                        f"🎯 Front-Run TP: Institutional Ask Pool at ${price:,.2f} pulls price upward. "
                        f"Set limit take-profit at ${action_price:,.2f} (~0.2% below wall) to guarantee fill before liquidity exhaustion."
                    )
                else:
                    role = "BLOCKING_RESISTANCE"
                    action_price = round(price * 0.999, 2)
                    action = (
                        f"⚡ Resistance Friction: Large Ask Pool at ${price:,.2f} sits before main target. "
                        f"Scale out 30-50% partial profits if momentum stalls near ${action_price:,.2f}."
                    )
            elif side == "BID" and price < entry:
                # Bid pool below entry
                if abs(dist_pct) <= 0.8:
                    role = "SWEEP_RISK"
                    action_price = round(price * 1.001, 2)
                    action = (
                        f"⚠️ Anticipate Sweep: High risk of stop-hunt wick down to ${price:,.2f} before rally. "
                        f"Avoid market FOMO; ladder limit entry at ${action_price:,.2f} or await 5m wick rejection confirmation."
                    )
                else:
                    role = "DEFENSIVE_SHIELD"
                    action_price = round(price * 0.997, 2)
                    action = (
                        f"🛡 Defensive Shield: Anchor Stop Loss below ${action_price:,.2f}. "
                        f"The {vol_str} institutional bid cluster absorbs sell pressure and shields the position."
                    )
            elif side == "BID" and price >= entry:
                role = "MOMENTUM_SUPPORT"
                action = f"🚀 Momentum Lift: Aggressive bid cluster driving bid-ask spread upward. Ride momentum with trailing stop."

        elif is_short:
            if side == "BID" and price < entry:
                # Bid pool underneath (Target pull vs Support)
                if take_profit > 0 and price <= max(entry * 0.99, take_profit * 1.02):
                    role = "TARGET_MAGNET"
                    action_price = round(price * 1.002, 2)
                    action = (
                        f"🎯 Front-Run TP: Institutional Bid Pool at ${price:,.2f} pulls price downward. "
                        f"Set limit take-profit at ${action_price:,.2f} (~0.2% above wall) to guarantee fill before support bounce."
                    )
                else:
                    role = "BLOCKING_SUPPORT"
                    action_price = round(price * 1.001, 2)
                    action = (
                        f"⚡ Support Friction: Large Bid Pool at ${price:,.2f} sits before main target. "
                        f"Scale out 30-50% partial profits if buy volume holds near ${action_price:,.2f}."
                    )
            elif side == "ASK" and price > entry:
                # Ask pool above entry
                if abs(dist_pct) <= 0.8:
                    role = "SWEEP_RISK"
                    action_price = round(price * 0.999, 2)
                    action = (
                        f"⚠️ Anticipate Sweep: High risk of short-squeeze wick up to ${price:,.2f} before breakdown. "
                        f"Avoid market FOMO; ladder limit entry at ${action_price:,.2f} or await sweep exhaustion."
                    )
                else:
                    role = "DEFENSIVE_SHIELD"
                    action_price = round(price * 1.003, 2)
                    action = (
                        f"🛡 Defensive Shield: Anchor Stop Loss above ${action_price:,.2f}. "
                        f"The {vol_str} institutional ask cluster absorbs rally spikes and protects the short."
                    )
            elif side == "ASK" and price <= entry:
                role = "MOMENTUM_PRESSURE"
                action = f"🔻 Downward Pressure: Aggressive ask cluster pressing price down. Trail stop loss aggressively."

        item['role'] = role
        item['action'] = action
        item['action_price'] = action_price
        enriched.append(item)

    return enriched
