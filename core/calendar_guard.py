"""
CalendarGuard monitors high-impact economic calendar events (USD)
and enforces blackout windows to prevent trading through volatile macro releases.
Dispatches advance notifications with scheduled dates/times (UTC & IST)
and macro directional impact scenarios (Crypto & Gold) so users can protect open trades.
"""

import logging
import aiohttp
import asyncio
from typing import List, Optional, Tuple, Dict, Any, Union, Set
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


class CalendarGuard:
    """Monitors scheduled economic releases, alerts users in advance with directional bias,
    and enforces volatility blackout windows to protect capital."""
    
    def __init__(self) -> None:
        self.high_impact_events: List[Union[datetime, Dict[str, Any]]] = []
        self.pre_buffer_mins: int = 30
        self.post_buffer_mins: int = 15
        self.last_pause_notification: Optional[str] = None
        self.last_sync_time: Optional[datetime] = None
        self.alerted_event_keys: Set[str] = set()
        self.notifier: Optional[Any] = None
        self.state_manager: Optional[Any] = None

    def set_notifier(self, notifier: Any) -> None:
        """Injects NotificationService for dispatching macro calendar alerts."""
        self.notifier = notifier

    def set_state_manager(self, state_manager: Any) -> None:
        """Injects StateManager for persisting alerted macro release keys across restarts."""
        self.state_manager = state_manager
        if hasattr(state_manager, 'state') and isinstance(state_manager.state, dict):
            saved = state_manager.state.get("alerted_macro_events", [])
            if saved:
                self.alerted_event_keys.update(str(x) for x in saved)

    async def sync_calendar(self) -> None:
        """Fetches the economic calendar, filters for high-impact USD events,
        and dispatches advance notification if upcoming events are found."""
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.forexfactory.com/"
        }
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as response:
                    if response.status == 200:
                        try:
                            data = await response.json()
                        except Exception as e:
                            logger.warning(f"Failed to parse JSON from Faireconomy. Falling back to cached schedule. Error: {e}")
                            return
                            
                        self.high_impact_events.clear()
                        for event in data:
                            impact = event.get("impact", "")
                            currency = event.get("country", "")  # Note: nfs uses country field for USD/EUR etc.
                            if impact == "High" and currency == "USD":
                                date_str = event.get("date")
                                try:
                                    # Parse date format like "2026-11-04T18:30:00-05:00"
                                    dt = datetime.fromisoformat(date_str).astimezone(timezone.utc)
                                    self.high_impact_events.append({
                                        "time": dt,
                                        "title": event.get("title", "High-Impact USD Macro Event"),
                                        "country": currency,
                                        "forecast": event.get("forecast", ""),
                                        "previous": event.get("previous", "")
                                    })
                                except Exception as e:
                                    logger.error(f"Error parsing date {date_str}: {e}")
                        self.last_sync_time = datetime.now(timezone.utc)
                        logger.info(f"Successfully synced economic calendar. Found {len(self.high_impact_events)} high-impact USD events.")
                        await self.check_and_notify_upcoming_events()
                    elif response.status == 429:
                        logger.warning("Faireconomy economic calendar API rate limit (HTTP 429). Retrying on next scheduled cycle.")
                    else:
                        logger.error(f"Failed to fetch economic calendar: HTTP {response.status}")
        except Exception as e:
            logger.error(f"Error syncing economic calendar: {e}")

    async def start_sync_task(self) -> None:
        """Runs periodically to sync calendar."""
        while True:
            if not self.last_sync_time:
                await self.sync_calendar()
                if not self.last_sync_time:
                    # Initial sync failed (e.g. rate limit), wait 30 minutes before retrying
                    await asyncio.sleep(1800)
                    continue

            now = datetime.now(timezone.utc)
            next_sync = now.replace(hour=0, minute=1, second=0, microsecond=0)
            if now >= next_sync:
                next_sync += timedelta(days=1)
            
            wait_seconds = (next_sync - now).total_seconds()
            await asyncio.sleep(wait_seconds)
            await self.sync_calendar()

    def get_upcoming_high_impact_events(self, within_hours: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Returns all scheduled high-impact events occurring in the future or currently within blackout.
        Sorted chronologically.
        """
        now = datetime.now(timezone.utc)
        threshold = now - timedelta(minutes=self.post_buffer_mins)
        upcoming: List[Dict[str, Any]] = []
        for item in self.high_impact_events:
            if isinstance(item, dict):
                ev_time: datetime = item["time"]
                ev_dict = dict(item)
            else:
                ev_time = item
                ev_dict = {
                    "time": ev_time,
                    "title": "High-Impact USD Macro Event",
                    "country": "USD",
                    "forecast": "",
                    "previous": ""
                }
            if ev_time >= threshold:
                if within_hours is not None:
                    if ev_time <= now + timedelta(hours=within_hours):
                        upcoming.append(ev_dict)
                else:
                    upcoming.append(ev_dict)
        upcoming.sort(key=lambda x: x["time"])
        return upcoming

    def get_macro_directional_bias(
        self, 
        title: str, 
        country: str = "USD", 
        forecast: str = "", 
        previous: str = ""
    ) -> Dict[str, str]:
        """
        Calculates macroeconomic directional scenarios for Crypto (BTC/ETH/SOL) and Gold (XAU).
        Differentiates labor, unemployment, inflation, central bank, and growth releases.
        """
        t = title.lower()

        # 1. Unemployment Rate / Jobless Claims (inverse: higher unemployment = cooling = dovish = bullish crypto/gold)
        if "unemployment" in t or "jobless claims" in t:
            return {
                "category": "Unemployment & Labor Slack",
                "lower_actual_scenario": "🟢 BULLISH for Crypto & Gold: Higher/rising unemployment confirms economic cooling, increasing probability of Fed rate cuts (Dovish / USD sells off).",
                "higher_actual_scenario": "🔴 BEARISH for Crypto & Gold: Lower unemployment confirms resilient labor market, allowing Fed to maintain restrictive rates (Hawkish / USD rallies).",
                "mechanism": "Unemployment rate directly drives Fed monetary policy pivot expectations."
            }

        # 2. Employment Growth / Payrolls / Wage Growth (pro-cyclical: higher jobs/wages = hawkish = bearish crypto/gold)
        if any(k in t for k in ["non-farm", "nfp", "employment change", "payrolls", "hourly earnings", "adp", "job openings", "jolts"]):
            return {
                "category": "Employment & Wage Growth",
                "lower_actual_scenario": "🟢 BULLISH for Crypto & Gold: Weaker job creation & slowing wage growth signal cooling economy, sparking expectations for Fed rate cuts and USD easing.",
                "higher_actual_scenario": "🔴 BEARISH for Crypto & Gold: Robust job gains & wage inflation fuel persistent rate fears, pushing USD and Treasury yields higher.",
                "mechanism": "Strong jobs & wage growth delay rate cut easing; weak labor market pulls rate cuts forward."
            }

        # 3. Inflation / Price Indices (CPI, PCE, PPI)
        if any(k in t for k in ["cpi", "consumer price", "pce", "personal consumption", "ppi", "producer price", "inflation"]):
            return {
                "category": "Inflation & Cost of Living",
                "lower_actual_scenario": "🟢 BULLISH for Crypto & Gold: Cooling disinflation confirms Fed policy targets, triggering risk-on liquidity surge into Bitcoin and Gold.",
                "higher_actual_scenario": "🔴 BEARISH for Crypto & Gold: Hot inflation prevents Fed rate cuts, driving capital into high-yield cash and strengthening USD.",
                "mechanism": "Lower inflation unlocks monetary easing; higher inflation sustains restrictive policy."
            }

        # 4. Central Bank & Interest Rates (FOMC, Fed Funds, Powell speeches)
        if any(k in t for k in ["fomc", "federal funds", "interest rate", "fed chair", "powell", "monetary policy", "rate decision"]):
            return {
                "category": "Central Bank & Monetary Policy",
                "lower_actual_scenario": "🟢 BULLISH for Crypto & Gold: Dovish guidance, rate cuts, or balance sheet expansion injects liquidity into hard assets.",
                "higher_actual_scenario": "🔴 BEARISH for Crypto & Gold: Hawkish stance, rate hikes, or pushback against rate cuts tightens global dollar liquidity.",
                "mechanism": "Fed interest rate policy directly sets global liquidity conditions and asset valuation multiples."
            }

        # 5. Economic Growth & Activity (GDP, Retail Sales, ISM, PMI)
        if any(k in t for k in ["gdp", "gross domestic product", "retail sales", "ism", "pmi"]):
            return {
                "category": "Economic Activity & Growth",
                "lower_actual_scenario": "🟢 BULLISH for Crypto & Gold: Contraction or slowdown triggers flight to safe-haven assets (Gold) and alternative monetary networks (Bitcoin).",
                "higher_actual_scenario": "🔴 BEARISH / Choppy for Crypto & Gold: Resilient consumer spending and factory expansion diminish urgency for Fed monetary easing.",
                "mechanism": "Economic contraction forces central banks to stimulate liquidity."
            }

        # 6. Default / Geopolitical / Sovereign
        return {
            "category": "Macro & Sovereign Event",
            "lower_actual_scenario": "🟢 BULLISH Scenario: Dovish interpretation / USD weakening enables risk-on expansion.",
            "higher_actual_scenario": "🔴 BEARISH Scenario: Hawkish surprise / USD surge triggers immediate order-book sweeps.",
            "mechanism": "Major headline risk triggers binary order-book volatility and liquidity sweeps."
        }

    def _generate_cluster_directional_bias(self, ev_list: List[Dict[str, Any]]) -> List[str]:
        """Synthesizes combined directional guidance for events occurring at the same timestamp."""
        titles = [e.get("title", "") for e in ev_list]
        titles_lower = " ".join(titles).lower()

        # Check for unified US Jobs Report cluster (NFP + Unemployment Rate + Hourly Earnings)
        has_nfp = any(k in titles_lower for k in ["non-farm", "nfp", "employment change", "payroll"])
        has_unemployment = "unemployment" in titles_lower
        has_earnings = any(k in titles_lower for k in ["hourly earnings", "wage"])

        if has_nfp and has_unemployment:
            return [
                "🟢 <b>BULLISH for Crypto (BTC/ETH/SOL) & Gold (XAU) IF:</b>\n"
                "• NFP & Wages come in <b>LOWER than forecast</b>, or Unemployment Rate rises <b>HIGHER than forecast</b>.\n"
                "  <i>➔ Macro Engine: Confirms cooling labor market, drops US Treasury yields, weakens USD, and accelerates Fed rate cut timeline.</i>",
                "🔴 <b>BEARISH for Crypto (BTC/ETH/SOL) & Gold (XAU) IF:</b>\n"
                "• NFP & Wages come in <b>HIGHER than forecast</b>, or Unemployment Rate drops <b>LOWER than forecast</b>.\n"
                "  <i>➔ Macro Engine: Signals overheating labor demand, spikes Treasury yields, rallies USD, and forces Fed to keep rates restrictive.</i>"
            ]

        # Check for Inflation cluster
        if any(k in titles_lower for k in ["cpi", "consumer price", "pce", "inflation"]):
            return [
                "🟢 <b>BULLISH for Crypto (BTC/ETH/SOL) & Gold (XAU) IF:</b>\n"
                "• Inflation prints <b>LOWER than forecast (Cool/Disinflation)</b>.\n"
                "  <i>➔ Macro Engine: Fed has green light to cut rates, USD drops, liquidity flows into Bitcoin & Gold.</i>",
                "🔴 <b>BEARISH for Crypto (BTC/ETH/SOL) & Gold (XAU) IF:</b>\n"
                "• Inflation prints <b>HIGHER than forecast (Hot/Sticky)</b>.\n"
                "  <i>➔ Macro Engine: Higher-for-longer rate pricing, bond yields climb, USD rallies, triggering risk-off selling.</i>"
            ]

        # Check for Central Bank cluster
        if any(k in titles_lower for k in ["fomc", "federal funds", "interest rate", "powell"]):
            return [
                "🟢 <b>BULLISH for Crypto & Gold IF:</b>\n"
                "• Dovish forward guidance, rate cuts, or signals of balance sheet liquidity support.\n"
                "  <i>➔ Macro Engine: Expanding fiat money supply and lower real yields pump BTC and Gold.</i>",
                "🔴 <b>BEARISH for Crypto & Gold IF:</b>\n"
                "• Hawkish tone, rate hike warning, or pushback against anticipated rate cuts.\n"
                "  <i>➔ Macro Engine: Monetary tightening contracts dollar liquidity.</i>"
            ]

        # Generic per-indicator synthesis
        blocks = []
        for e in ev_list:
            bias = self.get_macro_directional_bias(e.get("title", ""), e.get("country", "USD"))
            blocks.append(
                f"• <b>{e.get('title')}:</b>\n"
                f"  {bias['lower_actual_scenario']}\n"
                f"  {bias['higher_actual_scenario']}"
            )
        return blocks

    def format_upcoming_events_alert(
        self, 
        events: List[Dict[str, Any]], 
        is_on_demand: bool = False
    ) -> str:
        """
        Formats a comprehensive Telegram notification alert with exact dates,
        UTC and IST times, consensus forecasts, macro directional scenarios,
        and actionable guidance to manage or stop existing trades.
        """
        if not events:
            return "📅 <b>Economic Calendar</b>: No upcoming high-impact USD events scheduled."

        now = datetime.now(timezone.utc)
        ist_offset = timedelta(hours=5, minutes=30)
        
        header_title = (
            "📅 <b>UPCOMING HIGH-IMPACT ECONOMIC CALENDAR</b>" 
            if is_on_demand 
            else "🚨 <b>HIGH-IMPACT MACRO ALERT: Upcoming USD Releases Detected</b>"
        )
        
        # Group events by release time (rounded to minute)
        grouped: Dict[datetime, List[Dict[str, Any]]] = {}
        for ev in events:
            t: datetime = ev["time"]
            key_time = t.replace(second=0, microsecond=0)
            grouped.setdefault(key_time, []).append(ev)

        lines = [
            header_title,
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"Sentinel detected <b>{len(events)} upcoming high-impact event{'s' if len(events) > 1 else ''}</b>. "
            "High order-book volatility and liquidity stop-sweeps are expected across Crypto & Gold.\n"
        ]

        for release_time, ev_list in grouped.items():
            ist_time = release_time + ist_offset
            date_str = release_time.strftime("%A, %d %b %Y")
            utc_str = release_time.strftime("%H:%M UTC")
            ist_str = ist_time.strftime("%H:%M IST")
            
            # Countdown
            diff = release_time - now
            total_sec = int(diff.total_seconds())
            if total_sec > 0:
                hours, rem = divmod(total_sec, 3600)
                mins = rem // 60
                days, hours = divmod(hours, 24)
                if days > 0:
                    countdown = f"in {days}d {hours}h {mins}m"
                elif hours > 0:
                    countdown = f"in {hours}h {mins}m"
                else:
                    countdown = f"in {mins}m"
            else:
                countdown = "Releasing NOW / In Blackout"

            lines.append(f"📅 <b>{date_str}</b>")
            lines.append(f"⏰ <b>Scheduled Time:</b> <code>{utc_str}</code> | <b><code>{ist_str}</code></b> <i>({countdown})</i>\n")
            
            lines.append("📊 <b>Scheduled Releases:</b>")
            for e in ev_list:
                f_val = e.get("forecast")
                p_val = e.get("previous")
                f_str = f" | Forecast: <code>{f_val}</code>" if f_val else ""
                p_str = f" | Prev: <code>{p_val}</code>" if p_val else ""
                lines.append(f"• <b>{e.get('title', 'USD Macro Event')}</b> ({e.get('country', 'USD')}){f_str}{p_str}")

            lines.append("\n🧭 <b>Macro Directional Impact (Crypto & Gold):</b>")
            bias_blocks = self._generate_cluster_directional_bias(ev_list)
            lines.extend(bias_blocks)
            lines.append("")

        lines.extend([
            "🛡️ <b>Actionable Protection for Active Trades:</b>",
            "1. <b>Stop-Loss to Breakeven</b>: Protect open winning trades by shifting SL to Entry/BE.",
            "2. <b>Lock in Partial Profits (TP1)</b>: De-risk ahead of binary macro outcomes.",
            "3. <b>Trim High-Leverage Scalps</b>: Spreads and slippage widen 5-10x during Tier-1 releases.",
            f"4. <b>Sentinel Execution Blackout</b>: Algorithmic entries pause {self.pre_buffer_mins}m before and resume {self.post_buffer_mins}m after."
        ])

        return "\n".join(lines)

    async def check_and_notify_upcoming_events(self) -> Optional[str]:
        """
        Scans synced high-impact events for new upcoming releases.
        If any upcoming high-impact events have not yet been alerted to users,
        formats a rich directional notification and dispatches via notifier.
        """
        upcoming = self.get_upcoming_high_impact_events()
        if not upcoming:
            return None

        # Filter events that haven't been alerted yet
        unalerted = []
        for e in upcoming:
            t: datetime = e["time"]
            key = f"{e.get('title', '')}_{t.strftime('%Y%m%d_%H%M')}"
            if key not in self.alerted_event_keys:
                unalerted.append(e)
                self.alerted_event_keys.add(key)

        if not unalerted:
            return None

        # Persist alerted keys if state_manager is configured
        if self.state_manager and hasattr(self.state_manager, 'state') and isinstance(self.state_manager.state, dict):
            self.state_manager.state["alerted_macro_events"] = list(self.alerted_event_keys)

        msg = self.format_upcoming_events_alert(unalerted, is_on_demand=False)
        if self.notifier:
            try:
                await self.notifier.send_telegram(msg)
                logger.info(f"Dispatched upcoming economic alert for {len(unalerted)} events to users.")
            except Exception as ex:
                logger.error(f"Failed to send upcoming economic alert: {ex}")

        return msg

    def check_blackout_window(self) -> Tuple[bool, str]:
        """
        Checks if current time is within a macro event blackout window.
        Returns (is_blackout, notification_message).
        Enriches notification message with directional bias and active trade safety steps.
        """
        current_time = datetime.now(timezone.utc)
        active_events: List[Dict[str, Any]] = []
        
        for item in self.high_impact_events:
            if isinstance(item, dict):
                event_time: datetime = item["time"]
                title: str = item.get("title", "High-Impact USD Event")
                country: str = item.get("country", "USD")
                forecast: str = item.get("forecast", "")
                previous: str = item.get("previous", "")
            else:
                event_time = item
                title = "High-Impact USD Macro Release"
                country = "USD"
                forecast = ""
                previous = ""

            blackout_start = event_time - timedelta(minutes=self.pre_buffer_mins)
            blackout_end = event_time + timedelta(minutes=self.post_buffer_mins)
            
            if blackout_start <= current_time <= blackout_end:
                active_events.append({
                    "event_time": event_time,
                    "blackout_end": blackout_end,
                    "title": title,
                    "country": country,
                    "forecast": forecast,
                    "previous": previous
                })
                
        if not active_events:
            return False, ""
            
        latest_end = max(e["blackout_end"] for e in active_events)
        earliest_time = min(e["event_time"] for e in active_events)
        remaining_mins = max(1, int((latest_end - current_time).total_seconds() / 60))
        
        # Build unique notification key to prevent spamming while still inside the same event window
        event_titles = ", ".join(dict.fromkeys(e["title"] for e in active_events))
        notification_key = f"{earliest_time.isoformat()}_{event_titles}"
        
        notify_str = ""
        if self.last_pause_notification != notification_key:
            utc_str = earliest_time.strftime("%H:%M UTC")
            ist_time = earliest_time + timedelta(hours=5, minutes=30)
            ist_str = ist_time.strftime("%H:%M IST")
            resume_utc = latest_end.strftime("%H:%M UTC")
            resume_ist = (latest_end + timedelta(hours=5, minutes=30)).strftime("%H:%M IST")
            
            forecast_parts = []
            for e in active_events:
                f_val = e.get("forecast")
                p_val = e.get("previous")
                if f_val or p_val:
                    forecast_parts.append(f"{e['title']} [Forecast: {f_val or 'N/A'} | Prev: {p_val or 'N/A'}]")
            forecast_line = f"\n📊 <b>Consensus:</b> {'; '.join(forecast_parts)}" if forecast_parts else ""

            directional_blocks = self._generate_cluster_directional_bias(active_events)
            directional_section = "\n".join(directional_blocks)

            notify_str = (
                f"⚠️ <b>SENTINEL PAUSED: High-Impact Macro Release Scheduled</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"📅 <b>Release:</b> {event_titles} ({active_events[0].get('country', 'USD')})\n"
                f"⏰ <b>Scheduled Time:</b> {utc_str} ({ist_str}){forecast_line}\n"
                f"⏳ <b>Blackout Window:</b> Resuming in <b>{remaining_mins} mins</b> (~{resume_ist} / {resume_utc})\n\n"
                f"🧭 <b>Expected Market Direction (Crypto & Gold):</b>\n{directional_section}\n\n"
                f"🛡️ <b>Actionable Trade Protection:</b>\n"
                f"• Move Stop-Loss to Breakeven on active winning trades.\n"
                f"• Close or trim speculative high-leverage scalps ahead of volatility.\n"
                f"• Sentinel halts new algorithmic signals ({self.pre_buffer_mins}m pre / {self.post_buffer_mins}m post) to prevent stop-hunts."
            )
            self.last_pause_notification = notification_key
            
        return True, notify_str

    def is_circuit_breaker_active(self) -> bool:
        """Helper checking if calendar blackout is currently active."""
        is_blackout, _ = self.check_blackout_window()
        return bool(is_blackout)
