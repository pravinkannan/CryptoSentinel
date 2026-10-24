"""
MarketDataService handles all ccxt API interactions.
Follows SRP by only fetching and formatting market data without business logic.
Includes sticky multi-exchange failover and periodic recovery probing.
"""

import asyncio
import logging
import time
from typing import Optional, Dict, Any, List
import ccxt.async_support as ccxt
import pandas as pd
from core.exceptions import ExchangeAPIError
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

class MarketDataService:
    """Service to fetch OHLCV, funding rates, open interest, and order book data with sticky multi-exchange failover."""

    def __init__(
        self,
        exchange_id: str,
        fallback_exchanges: Optional[List[str]] = None,
        proxy_url: Optional[str] = None,
        probe_cooldown_minutes: int = 15,
        timeout_ms: int = 8000
    ):
        self.primary_exchange_id = exchange_id.lower()
        fallbacks = [f.lower() for f in (fallback_exchanges or ['bybit'])]
        raw_pool = [self.primary_exchange_id] + [f for f in fallbacks if f != self.primary_exchange_id]
        self.exchange_pool = list(dict.fromkeys(raw_pool))
        self.active_exchange_id = self.primary_exchange_id
        self.probe_cooldown_seconds = probe_cooldown_minutes * 60.0
        self.timeout_ms = timeout_ms
        self._last_failed_times: Dict[str, float] = {}

        self._exchanges: Dict[str, Any] = {}
        for ex_id in self.exchange_pool:
            try:
                exchange_class = getattr(ccxt, ex_id)
                config: Dict[str, Any] = {
                    'enableRateLimit': True,
                    'aiohttp_trust_env': True,
                    'timeout': self.timeout_ms,
                }
                if proxy_url:
                    config['aiohttp_proxy'] = proxy_url
                self._exchanges[ex_id] = exchange_class(config)
            except AttributeError:
                logger.warning(f"Exchange {ex_id} is not supported by ccxt, skipping from pool.")

        if not self._exchanges:
            raise ExchangeAPIError(f"None of the configured exchanges {self.exchange_pool} are supported by ccxt.")

        if self.primary_exchange_id not in self._exchanges:
            self.active_exchange_id = list(self._exchanges.keys())[0]

    @property
    def exchange(self):
        """Returns the currently active CCXT exchange instance."""
        return self._exchanges[self.active_exchange_id]

    async def close(self) -> None:
        """Close the async HTTP sessions of all initialized exchanges."""
        for ex_id, ex in self._exchanges.items():
            try:
                await ex.close()
            except Exception as e:
                logger.debug(f"Error closing exchange {ex_id}: {e}")

    async def _switch_to_next_exchange(self, reason: Exception) -> bool:
        """
        Marks current exchange as failed and switches active exchange to the next available healthy exchange.
        Maintains sticky state so subsequent queries continue with the new exchange without retrying the failed one.
        """
        failed_id = self.active_exchange_id
        self._last_failed_times[failed_id] = time.time()

        available_exchanges = [eid for eid in self.exchange_pool if eid in self._exchanges]
        if len(available_exchanges) <= 1:
            logger.error(f"[EXCHANGE FAILOVER] No alternative fallback exchange available for '{failed_id}'.")
            return False

        idx = available_exchanges.index(failed_id)
        next_ex_id = available_exchanges[(idx + 1) % len(available_exchanges)]

        logger.warning(
            f"[EXCHANGE FAILOVER] Active exchange '{failed_id}' failed ({type(reason).__name__}: {reason}). "
            f"Switching active exchange to '{next_ex_id}' and continuing execution with it."
        )
        self.active_exchange_id = next_ex_id
        return True

    async def _maybe_probe_primary(self) -> None:
        """
        Periodically probes higher-priority exchanges if they previously failed and we are currently on a fallback.
        Runs 'here and there' (every probe_cooldown_minutes) with a fast 3.5s timeout.
        If the primary exchange succeeds, smoothly switches back; otherwise stays on current fallback.
        """
        if self.active_exchange_id == self.primary_exchange_id:
            return

        last_failed = self._last_failed_times.get(self.primary_exchange_id, 0.0)
        now = time.time()
        if (now - last_failed) < self.probe_cooldown_seconds:
            return

        # Advance cooldown timestamp so consecutive candles don't spam probes
        self._last_failed_times[self.primary_exchange_id] = now
        primary_ex = self._exchanges.get(self.primary_exchange_id)
        if not primary_ex:
            return

        logger.info(f"[EXCHANGE PROBE] Attempting periodic health check on '{self.primary_exchange_id}'...")
        try:
            probe_coro = primary_ex.fetch_time() if primary_ex.has.get('fetchTime') else primary_ex.fetch_status()
            await asyncio.wait_for(probe_coro, timeout=3.5)
            logger.info(
                f"[EXCHANGE RECOVERY] Primary exchange '{self.primary_exchange_id}' is responsive! "
                f"Switching active exchange from '{self.active_exchange_id}' back to '{self.primary_exchange_id}'."
            )
            self.active_exchange_id = self.primary_exchange_id
        except Exception as e:
            logger.info(
                f"[EXCHANGE PROBE] Periodic health check for '{self.primary_exchange_id}' unsuccessful ({type(e).__name__}). "
                f"Continuing with '{self.active_exchange_id}'."
            )

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=0.5, min=1, max=3), reraise=True)
    async def _fetch_ohlcv_with_retry(self, symbol: str, timeframe: str, limit: int):
        curr_ex = self.exchange
        try:
            return await curr_ex.fetch_ohlcv(symbol, timeframe, limit=limit)
        except ccxt.BadSymbol:
            if '/' in symbol and ':' not in symbol:
                future_symbol = f"{symbol}:{symbol.split('/')[1]}"
                return await curr_ex.fetch_ohlcv(future_symbol, timeframe, limit=limit)
            raise

    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 250) -> Optional[pd.DataFrame]:
        """Fetch OHLCV data and convert to pandas DataFrame with sticky multi-exchange failover."""
        await self._maybe_probe_primary()
        for attempt in range(len(self.exchange_pool)):
            try:
                ohlcv = await self._fetch_ohlcv_with_retry(symbol, timeframe, limit)
                df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                return df
            except Exception as e:
                is_net_err = isinstance(e, (ccxt.NetworkError, ccxt.RequestTimeout)) or "timeout" in str(e).lower() or "connect" in str(e).lower()
                if is_net_err and len(self.exchange_pool) > 1 and attempt < len(self.exchange_pool) - 1:
                    switched = await self._switch_to_next_exchange(e)
                    if switched:
                        continue
                logger.error(f"Error fetching OHLCV for {symbol} on {timeframe} via {self.active_exchange_id}: {e}")
                return None
        return None

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=0.5, min=1, max=3))
    async def _fetch_funding_rate_with_retry(self, future_symbol: str):
        return await self.exchange.fetch_funding_rate(future_symbol)

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=0.5, min=1, max=3))
    async def _fetch_open_interest_with_retry(self, future_symbol: str):
        return await self.exchange.fetch_open_interest(future_symbol)

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=0.5, min=1, max=3))
    async def _fetch_order_book_with_retry(self, symbol: str, limit: int = 500):
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
                val = oi.get('openInterestValue') or oi.get('openInterestAmount') or oi.get('baseVolume') or (oi.get('info', {}) or {}).get('openInterest') or 0.0
                open_interest = float(val)
        except Exception as e:
            logger.debug(f"Open interest fetch failed for {symbol}: {e}")
            
        try:
            if self.exchange.has.get('fetchOrderBook'):
                try: 
                    ob = await self._fetch_order_book_with_retry(future_symbol, limit=500)
                except Exception: 
                    ob = await self._fetch_order_book_with_retry(symbol, limit=500)
                
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

    orders_sample = (order_book.get('bids', []) + order_book.get('asks', []))
    if orders_sample:
        max_dist = max(abs(float(o[0]) - current_price) for o in orders_sample if len(o) >= 2)
        if max_dist > 0:
            bucket_size = max(0.01, min(current_price * 0.0005, max_dist / 15.0))
        else:
            bucket_size = max(0.01, current_price * 0.0005)
    else:
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
        min_cluster_usd = 100_000.0 if current_price >= 10_000.0 else 25_000.0
        for bucket_price, total_usd in clusters.items():
            if total_usd >= avg_vol * multiplier and total_usd >= min_cluster_usd:
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
                action = (
                    "🚀 Momentum Lift: Aggressive bid cluster driving bid-ask spread upward. "
                    "Ride momentum with trailing stop."
                )

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
                action = (
                    "🔻 Downward Pressure: Aggressive ask cluster pressing price down. "
                    "Trail stop loss aggressively."
                )

        item['role'] = role
        item['action'] = action
        item['action_price'] = action_price
        enriched.append(item)

    return enriched
