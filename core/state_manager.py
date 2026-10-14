"""
StateManager handles persistent tracking of processed alerts, news, and active signals.
Implements non-blocking, atomic file I/O to prevent state corruption during abrupt terminations.
"""

import json
import logging
import asyncio
import os
from typing import Set, Dict, List, Any, Optional, TypedDict

logger = logging.getLogger(__name__)


class SentinelState(TypedDict, total=False):
    seen_guids: List[str]
    last_crossed_alerts: Dict[str, List[str]]
    active_signals: Dict[str, Dict[str, Any]]
    dynamic_level_alert_timestamps: Dict[str, str]


class StateManager:
    """Manages reading and writing application state to disk with atomic write protection."""
    
    def __init__(self, filename: str = "sentinel_state.json") -> None:
        self.filename: str = filename
        self.state: Dict[str, Any] = {
            "seen_guids": [],
            "last_crossed_alerts": {},
            "active_signals": {},
            "dynamic_level_alert_timestamps": {}
        }
        self.lock = asyncio.Lock()

    def _sync_load(self) -> Dict[str, Any]:
        """Synchronously read JSON state from disk."""
        if not os.path.exists(self.filename):
            return {}
        with open(self.filename, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return dict(data) if isinstance(data, dict) else {}

    def _sync_atomic_save(self, data: Dict[str, Any]) -> None:
        """Write JSON data to a temporary file, then atomically replace the target."""
        tmp_filename = f"{self.filename}.tmp"
        dirname = os.path.dirname(self.filename)
        if dirname:
            os.makedirs(dirname, exist_ok=True)
            
        with open(tmp_filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)
            f.flush()
            os.fsync(f.fileno())
            
        os.replace(tmp_filename, self.filename)

    async def load_state(self) -> None:
        """Load state asynchronously from disk without blocking the event loop."""
        async with self.lock:
            try:
                loaded = await asyncio.to_thread(self._sync_load)
                if loaded:
                    self.state["seen_guids"] = loaded.get("seen_guids", [])
                    self.state["last_crossed_alerts"] = loaded.get("last_crossed_alerts", {})
                    self.state["active_signals"] = loaded.get("active_signals", {})
                    self.state["dynamic_level_alert_timestamps"] = loaded.get("dynamic_level_alert_timestamps", {})
                    logger.info("State loaded successfully.")
            except Exception as e:
                logger.error(f"Error loading state from {self.filename}: {e}", exc_info=True)

    async def save_state(
        self, 
        seen_guids: Set[str], 
        last_crossed_alerts: Dict[str, List[str]],
        active_signals: Optional[Dict[str, Dict[str, Any]]] = None
    ) -> None:
        """Save state asynchronously to disk via atomic write."""
        async with self.lock:
            try:
                self.state["seen_guids"] = list(seen_guids)
                self.state["last_crossed_alerts"] = last_crossed_alerts
                if active_signals is not None:
                    self.state["active_signals"] = active_signals
                    
                await asyncio.to_thread(self._sync_atomic_save, self.state)
                logger.debug("State saved successfully (atomic write).")
            except Exception as e:
                logger.error(f"Error saving state to {self.filename}: {e}", exc_info=True)
