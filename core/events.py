"""
Immutable domain and application events for the Sentinel Event-Driven Architecture.
All events are strictly defined as frozen dataclasses.
"""

from dataclasses import dataclass, field
from typing import Dict, Any, Optional
import time


@dataclass(frozen=True)
class TradeOpenedEvent:
    """Emitted when a new simulated or live trade position is opened."""
    trade_id: str
    symbol: str
    direction: str
    entry_price: float
    sl_price: float
    tp1_price: float
    tp2_price: float
    leverage: float
    confidence_score: float = 0.0
    tier: str = ""
    timestamp: float = field(default_factory=time.time)
    signal_payload: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TradeClosedEvent:
    """
    Emitted when an active trade position is closed (SL, TP1/TP2, Breakeven, Expired, Market Close).
    Contains all required accounting metrics and forensics.
    """
    trade_id: str
    symbol: str
    direction: str
    exit_price: float
    pnl_pct: float
    outcome: str
    exit_reason: str
    entry_price: float = 0.0
    leverage: float = 1.0
    gross_pnl_pct: float = 0.0
    net_pnl_pct: float = 0.0
    mae_pct: float = 0.0
    mfe_pct: float = 0.0
    exit_candle: Optional[Dict[str, Any]] = None
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class SignalRejectedEvent:
    """Emitted when a signal fails Stage 1 circuit breakers or Stage 2/3 confidence floors."""
    symbol: str
    direction: str
    score: float
    tier: str
    reason: str
    timestamp: float = field(default_factory=time.time)
    signal_data: Dict[str, Any] = field(default_factory=dict)
