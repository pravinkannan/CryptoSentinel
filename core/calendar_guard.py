"""
CalendarGuard monitors high-impact economic calendar events (USD)
and enforces blackout windows to prevent trading through volatile macro releases.
"""

import logging
import aiohttp
import asyncio
from typing import List, Optional, Tuple, Dict, Any, Union
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


class CalendarGuard:
    """Monitors scheduled economic releases and enforces volatility blackout windows."""
    
    def __init__(self) -> None:
        self.high_impact_events: List[Union[datetime, Dict[str, Any]]] = []
        self.pre_buffer_mins: int = 30
        self.post_buffer_mins: int = 15
        self.last_pause_notification: Optional[str] = None
        self.last_sync_time: Optional[datetime] = None

    async def sync_calendar(self) -> None:
        """Fetches the economic calendar and filters for high-impact USD events."""
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as response:
                    if response.status == 200:
                        try:
                            data = await response.json()
                        except Exception as e:
                            logger.warning(f"Failed to parse JSON from Faireconomy (Rate Limit likely). Falling back to cached schedule. Error: {e}")
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
                    else:
                        logger.error(f"Failed to fetch economic calendar: HTTP {response.status}")
        except Exception as e:
            logger.error(f"Error syncing economic calendar: {e}")

    async def start_sync_task(self) -> None:
        """Runs daily at 00:01 UTC to sync calendar."""
        while True:
            now = datetime.now(timezone.utc)
            next_sync = now.replace(hour=0, minute=1, second=0, microsecond=0)
            if now >= next_sync:
                next_sync += timedelta(days=1)
            
            wait_seconds = (next_sync - now).total_seconds()
            
            if not self.last_sync_time:
                await self.sync_calendar()
                
            await asyncio.sleep(wait_seconds)
            await self.sync_calendar()

    def check_blackout_window(self) -> Tuple[bool, str]:
        """
        Checks if current time is within a macro event blackout window.
        Returns (is_blackout, notification_message).
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

            notify_str = (
                f"⚠️ <b>SENTINEL PAUSED: High-Impact Macro Release Scheduled</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"📅 <b>Release:</b> {event_titles} ({country})\n"
                f"⏰ <b>Scheduled Time:</b> {utc_str} ({ist_str}){forecast_line}\n"
                f"⏳ <b>Blackout Window:</b> Resuming in <b>{remaining_mins} mins</b> (~{resume_ist} / {resume_utc})\n\n"
                f"🛡️ <b>Reason for Pause:</b>\n"
                f"Tier-1 macro releases trigger extreme order book volatility, sudden spread expansion, and institutional liquidity stop-hunts. "
                f"Sentinel pauses trade entries {self.pre_buffer_mins}m before and {self.post_buffer_mins}m after high-impact events to safeguard capital."
            )
            self.last_pause_notification = notification_key
            
        return True, notify_str
