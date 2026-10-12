"""
StateManager handles persistent tracking of processed alerts and news.
"""

import json
import logging
import asyncio
import os
from typing import Set, Dict, List, Any

logger = logging.getLogger(__name__)

class StateManager:
    """Manages reading and writing application state to a local JSON file."""
    def __init__(self, filename: str = "sentinel_state.json") -> None:
        self.filename = filename
        self.state: Dict[str, Any] = {
            "seen_guids": [],
            "last_crossed_alerts": {},
            "active_signals": {}
        }
        self.lock = asyncio.Lock()

    async def load_state(self) -> None:
        """Load state asynchronously from disk."""
        async with self.lock:
            try:
                if os.path.exists(self.filename):
                    with open(self.filename, 'r') as f:
                        loaded = json.load(f)
                        self.state["seen_guids"] = loaded.get("seen_guids", [])
                        self.state["last_crossed_alerts"] = loaded.get("last_crossed_alerts", {})
                        self.state["active_signals"] = loaded.get("active_signals", {})
                    logger.info("State loaded successfully.")
            except Exception as e:
                logger.error(f"Error loading state: {e}")

    async def save_state(self, seen_guids: Set[str], last_crossed_alerts: Dict[str, List[str]]) -> None:
        """Save state asynchronously to disk."""
        async with self.lock:
            try:
                self.state["seen_guids"] = list(seen_guids)
                self.state["last_crossed_alerts"] = last_crossed_alerts
                with open(self.filename, 'w') as f:
                    json.dump(self.state, f, indent=4)
                logger.debug("State saved successfully.")
            except Exception as e:
                logger.error(f"Error saving state: {e}")
