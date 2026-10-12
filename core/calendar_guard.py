import logging
import aiohttp
import asyncio
from typing import List, Optional, Tuple
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

class CalendarGuard:
    def __init__(self) -> None:
        self.high_impact_events: List[datetime] = []
        self.pre_buffer_mins: int = 30
        self.post_buffer_mins: int = 15
        self.last_pause_notification: Optional[datetime] = None
        self.last_sync_time: Optional[datetime] = None

    async def sync_calendar(self) -> None:
        """Fetches the economic calendar and filters for high-impact USD events."""
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
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
                                    self.high_impact_events.append(dt)
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

    def check_blackout_window(self) -> tuple[bool, str]:
        """
        Checks if current time is within a macro event blackout window.
        Returns (is_blackout, notification_message).
        """
        current_time = datetime.now(timezone.utc)
        
        for event in self.high_impact_events:
            blackout_start = event - timedelta(minutes=self.pre_buffer_mins)
            blackout_end = event + timedelta(minutes=self.post_buffer_mins)
            
            if blackout_start <= current_time <= blackout_end:
                remaining_mins = int((blackout_end - current_time).total_seconds() / 60)
                
                notify_str = ""
                if self.last_pause_notification != event:
                    notify_str = f"⚠️ SENTINEL PAUSED: High-Impact Macro Release Scheduled. Resuming in {remaining_mins} mins."
                    self.last_pause_notification = event
                    
                return True, notify_str
                
        return False, ""
