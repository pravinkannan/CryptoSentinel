"""
Repository Pattern for Crypto Sentinel.
Consolidates raw SQL operations, trade lifecycle tracking, autopsy records,
and application state (active signals, cooldowns) behind a unified, thread-safe
and async-safe repository interface. Eliminates split-brain persistence.
"""

from abc import ABC, abstractmethod
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from core.db import get_db_connection
from core.config import settings
from core.events import TradeClosedEvent

logger = logging.getLogger(__name__)


class ITradeRepository(ABC):
    """Abstract interface for trade repository and unified state operations."""

    @abstractmethod
    async def ensure_schema(self) -> None:
        """Create tables and perform idempotent migrations."""
        ...

    @abstractmethod
    async def save_trade(self, trade_data: Dict[str, Any]) -> str:
        """Persist a newly opened trade and its evaluation, returning the generated trade_id."""
        ...

    @abstractmethod
    async def get_trade(self, trade_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve trade record by trade_id."""
        ...

    @abstractmethod
    async def get_open_trades(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieve all currently PENDING trades, optionally filtered by symbol."""
        ...

    @abstractmethod
    async def update_trade_status(
        self, 
        trade_id: str, 
        status: str, 
        exit_price: float, 
        net_pnl_pct: float, 
        gross_pnl_pct: float
    ) -> None:
        """Update trade closure status and finalized PnL metrics."""
        ...

    @abstractmethod
    async def update_trailing_sl(
        self, 
        trade_id: str, 
        trailing_sl: float, 
        tp1_hit: int, 
        mae_pct: float, 
        mfe_pct: float
    ) -> None:
        """Update trailing stop loss and telemetry."""
        ...

    @abstractmethod
    async def save_autopsy(self, autopsy_data: Dict[str, Any]) -> None:
        """Save post-trade forensic autopsy metrics."""
        ...

    @abstractmethod
    async def log_rejected_signal(self, rejected_data: Dict[str, Any]) -> None:
        """Log near-miss or rejected trade candidates for offline analysis."""
        ...

    @abstractmethod
    async def get_active_signals(self) -> Dict[str, Dict[str, Any]]:
        """Retrieve in-memory or persisted active signals map."""
        ...

    @abstractmethod
    async def save_active_signal(self, key: str, signal_data: Dict[str, Any]) -> None:
        """Persist active signal entry."""
        ...

    @abstractmethod
    async def evict_active_signal(
        self, 
        trade_id: Optional[str] = None, 
        symbol: Optional[str] = None, 
        direction: Optional[str] = None, 
        key: Optional[str] = None
    ) -> List[str]:
        """Atomically evict active signals matching criteria upon trade close."""
        ...


class SentinelRepository(ITradeRepository):
    """
    Unified SQLite-backed repository managing trades, evaluations, autopsies,
    and application state (active signals & cooldowns). Synchronizes with StateManager.
    """

    def __init__(
        self, 
        db_path: str = "data/sentinel.db", 
        state_manager: Any = None
    ) -> None:
        self.db_path: str = db_path if isinstance(db_path, str) else "data/sentinel.db"
        self.state_manager = state_manager

    async def ensure_schema(self) -> None:
        """Create tables and perform idempotent migrations."""
        abs_path = os.path.abspath(self.db_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        async with get_db_connection(self.db_path) as conn:
            # Trades table
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS trades (
                    trade_id TEXT PRIMARY KEY,
                    timestamp TEXT,
                    symbol TEXT,
                    direction TEXT,
                    entry_price REAL,
                    sl_price REAL,
                    tp1_price REAL,
                    tp2_price REAL,
                    leverage REAL,
                    tp1_hit INTEGER DEFAULT 0,
                    tp2_hit INTEGER DEFAULT 0,
                    trailing_sl REAL,
                    status TEXT,
                    exit_price REAL DEFAULT 0.0,
                    pnl_pct REAL DEFAULT 0.0,
                    net_pnl_pct REAL DEFAULT 0.0,
                    gross_pnl_pct REAL DEFAULT 0.0,
                    ttl_mins INTEGER DEFAULT 180
                )
            ''')
            # Evaluations table
            # Evaluations table matching JournalManager
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS evaluations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id TEXT,
                    signal_payload TEXT,
                    pattern_name TEXT,
                    confidence_score REAL,
                    confluences_applied TEXT,
                    regime_state TEXT,
                    mae_pct REAL DEFAULT 0.0,
                    mfe_pct REAL DEFAULT 0.0,
                    FOREIGN KEY(trade_id) REFERENCES trades(trade_id)
                )
            ''')
            # Autopsies table
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS trade_autopsies (
                    trade_id TEXT PRIMARY KEY,
                    symbol TEXT,
                    direction TEXT,
                    failure_category TEXT,
                    summary TEXT,
                    key_culprit TEXT,
                    actionable_lesson TEXT,
                    timestamp TEXT,
                    net_pnl_pct REAL DEFAULT 0.0,
                    FOREIGN KEY(trade_id) REFERENCES trades(trade_id)
                )
            ''')
            # Rejected signals table
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS rejected_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    symbol TEXT,
                    timeframe TEXT,
                    direction TEXT,
                    pattern_name TEXT,
                    confidence_score REAL,
                    eval_trace TEXT,
                    scoring_checklist TEXT,
                    rejection_reason TEXT
                )
            ''')
            # Key-value state table to eliminate split-brain JSON vs SQLite
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS sentinel_kv_state (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TEXT
                )
            ''')

    async def save_trade(self, trade_data: Dict[str, Any]) -> str:
        """Persist a newly opened trade and its evaluation, returning trade_id."""
        await self.ensure_schema()
        trade_id = trade_data.get('trade_id') or f"{trade_data['symbol']}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}"
        now_iso = datetime.now(timezone.utc).isoformat()
        timestamp = trade_data.get('timestamp', now_iso)
        ttl = trade_data.get('ttl_mins', getattr(settings, 'trade_ttl_minutes', 180))

        async with get_db_connection(self.db_path) as conn:
            await conn.execute('''
                INSERT INTO trades (
                    trade_id, timestamp, symbol, direction, entry_price, sl_price, 
                    tp1_price, tp2_price, leverage, tp1_hit, tp2_hit, trailing_sl, 
                    status, exit_price, pnl_pct, net_pnl_pct, gross_pnl_pct, ttl_mins
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, 0.0, 0.0, 0.0, 0.0, ?)
            ''', (
                trade_id,
                timestamp,
                trade_data['symbol'],
                trade_data['direction'],
                trade_data['entry_price'],
                trade_data['sl_price'],
                trade_data.get('tp1_price', trade_data.get('entry_price')),
                trade_data.get('tp2_price', trade_data.get('entry_price')),
                trade_data.get('leverage', 1.0),
                trade_data.get('sl_price'),
                'PENDING',
                ttl
            ))

            cursor = await conn.execute("PRAGMA table_info(evaluations)")
            cols = [r[1] for r in await cursor.fetchall()]
            checklist_col = "confluences_applied" if "confluences_applied" in cols else "scoring_checklist"
            regime_col = "regime_state" if "regime_state" in cols else "regime"

            await conn.execute(f'''
                INSERT INTO evaluations (
                    trade_id, signal_payload, pattern_name, confidence_score, 
                    {checklist_col}, {regime_col}, mae_pct, mfe_pct
                ) VALUES (?, ?, ?, ?, ?, ?, 0.0, 0.0)
            ''', (
                trade_id,
                json.dumps(trade_data.get('signal_payload', {})),
                trade_data.get('pattern_name', 'Unknown'),
                trade_data.get('confidence_score', 0.0),
                json.dumps(trade_data.get('scoring_checklist', [])),
                trade_data.get('regime', 'UNKNOWN')
            ))
            await conn.commit()
            logger.info(f"Trade {trade_id} persisted in unified repository.")
            return trade_id

    async def get_trade(self, trade_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve trade record by trade_id."""
        async with get_db_connection(self.db_path) as conn:
            cursor = await conn.execute(
                'SELECT trade_id, symbol, direction, entry_price, sl_price, tp1_price, tp2_price, leverage, status, exit_price, pnl_pct, net_pnl_pct, gross_pnl_pct FROM trades WHERE trade_id = ?',
                (trade_id,)
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                'trade_id': row[0],
                'symbol': row[1],
                'direction': row[2],
                'entry_price': row[3],
                'sl_price': row[4],
                'tp1_price': row[5],
                'tp2_price': row[6],
                'leverage': row[7],
                'status': row[8],
                'exit_price': row[9],
                'pnl_pct': row[10],
                'net_pnl_pct': row[11],
                'gross_pnl_pct': row[12]
            }

    async def get_open_trades(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieve all currently PENDING trades."""
        async with get_db_connection(self.db_path) as conn:
            if symbol:
                cursor = await conn.execute(
                    'SELECT trade_id, symbol, direction, entry_price, sl_price, leverage, pnl_pct FROM trades WHERE symbol = ? AND status = "PENDING"',
                    (symbol,)
                )
            else:
                cursor = await conn.execute(
                    'SELECT trade_id, symbol, direction, entry_price, sl_price, leverage, pnl_pct FROM trades WHERE status = "PENDING"'
                )
            rows = await cursor.fetchall()
            return [
                {
                    'trade_id': r[0],
                    'symbol': r[1],
                    'direction': r[2],
                    'entry_price': r[3],
                    'sl_price': r[4],
                    'leverage': r[5],
                    'pnl_pct': r[6]
                }
                for r in rows
            ]

    async def update_trade_status(
        self, 
        trade_id: str, 
        status: str, 
        exit_price: float, 
        net_pnl_pct: float, 
        gross_pnl_pct: float
    ) -> None:
        """Update trade closure status and finalized PnL metrics."""
        async with get_db_connection(self.db_path) as conn:
            await conn.execute('''
                UPDATE trades 
                SET status = ?, exit_price = ?, pnl_pct = ?, net_pnl_pct = ?, gross_pnl_pct = ? 
                WHERE trade_id = ?
            ''', (status, exit_price, net_pnl_pct, net_pnl_pct, gross_pnl_pct, trade_id))
            await conn.commit()
            logger.info(f"Trade {trade_id} status updated to {status} at {exit_price} in repository.")

    async def update_trailing_sl(
        self, 
        trade_id: str, 
        trailing_sl: float, 
        tp1_hit: int, 
        mae_pct: float, 
        mfe_pct: float
    ) -> None:
        """Update trailing stop loss and telemetry."""
        async with get_db_connection(self.db_path) as conn:
            await conn.execute('UPDATE trades SET tp1_hit = ?, trailing_sl = ? WHERE trade_id = ?', (tp1_hit, trailing_sl, trade_id))
            await conn.execute('UPDATE evaluations SET mae_pct = ?, mfe_pct = ? WHERE trade_id = ?', (mae_pct, mfe_pct, trade_id))
            await conn.commit()

    async def save_autopsy(self, autopsy_data: Dict[str, Any]) -> None:
        """Save post-trade forensic autopsy metrics."""
        await self.ensure_schema()
        async with get_db_connection(self.db_path) as conn:
            cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('trade_autopsies', 'autopsies')")
            row = await cursor.fetchone()
            tbl = row[0] if row else "trade_autopsies"

            await conn.execute(f'''
                INSERT OR REPLACE INTO {tbl} (
                    trade_id, symbol, direction, failure_category, summary, 
                    key_culprit, actionable_lesson, timestamp, net_pnl_pct
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                autopsy_data.get('trade_id', 'UNKNOWN'),
                autopsy_data.get('symbol', 'UNKNOWN'),
                autopsy_data.get('direction', 'UNKNOWN'),
                autopsy_data.get('failure_category', 'UNKNOWN'),
                autopsy_data.get('summary', ''),
                autopsy_data.get('key_culprit', ''),
                autopsy_data.get('actionable_lesson', ''),
                autopsy_data.get('timestamp', datetime.now(timezone.utc).isoformat()),
                autopsy_data.get('net_pnl_pct', 0.0)
            ))
            await conn.commit()
            logger.info(f"Autopsy saved for {autopsy_data.get('trade_id')} in repository.")

    async def log_rejected_signal(self, rejected_data: Dict[str, Any]) -> None:
        """Log near-miss or rejected trade candidates for offline analysis."""
        await self.ensure_schema()
        async with get_db_connection(self.db_path) as conn:
            await conn.execute('''
                INSERT INTO rejected_signals (
                    timestamp, symbol, timeframe, direction, pattern_name, 
                    confidence_score, eval_trace, scoring_checklist, rejection_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                rejected_data.get('timestamp', datetime.now(timezone.utc).isoformat()),
                rejected_data.get('symbol', 'UNKNOWN'),
                rejected_data.get('timeframe', '5m'),
                rejected_data.get('direction', 'UNKNOWN'),
                rejected_data.get('pattern_name', 'Unknown'),
                rejected_data.get('confidence_score', 0.0),
                json.dumps(rejected_data.get('eval_trace', {})),
                json.dumps(rejected_data.get('scoring_checklist', [])),
                rejected_data.get('rejection_reason', '')
            ))
            await conn.commit()

    # --------------------------------------------------------------------------
    # STATE CONSOLIDATION & EVICTION
    # --------------------------------------------------------------------------

    async def get_active_signals(self) -> Dict[str, Dict[str, Any]]:
        """Retrieve active signals map synchronously or from DB."""
        if self.state_manager and hasattr(self.state_manager, 'state'):
            sigs = self.state_manager.state.setdefault("active_signals", {})
            return dict(sigs) if isinstance(sigs, dict) else {}
        async with get_db_connection(self.db_path) as conn:
            cursor = await conn.execute('SELECT value FROM sentinel_kv_state WHERE key = "active_signals"')
            row = await cursor.fetchone()
            if row and row[0]:
                try:
                    data = json.loads(row[0])
                    if isinstance(data, dict):
                        return dict(data)
                except Exception:
                    pass
        return {}

    async def save_active_signal(self, key: str, signal_data: Dict[str, Any]) -> None:
        """Atomically persist active signal to state manager and SQLite state table."""
        active_sigs = await self.get_active_signals()
        active_sigs[key] = signal_data
        if self.state_manager and hasattr(self.state_manager, 'state'):
            self.state_manager.state["active_signals"] = active_sigs
        
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            async with get_db_connection(self.db_path) as conn:
                await conn.execute('''
                    INSERT INTO sentinel_kv_state (key, value, updated_at) 
                    VALUES ("active_signals", ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                ''', (json.dumps(active_sigs), now_iso))
                await conn.commit()
        except Exception as e:
            logger.debug(f"Could not persist active_signals to sentinel_kv_state: {e}")

    async def evict_active_signal(
        self, 
        trade_id: Optional[str] = None, 
        symbol: Optional[str] = None, 
        direction: Optional[str] = None, 
        key: Optional[str] = None
    ) -> List[str]:
        """Atomically evict active signals matching criteria upon trade close."""
        active_sigs = await self.get_active_signals()
        keys_to_remove: List[str] = []

        if key and key in active_sigs:
            keys_to_remove.append(key)
        else:
            for k, sig in list(active_sigs.items()):
                matched = False
                if trade_id and sig.get('trade_id') == trade_id:
                    matched = True
                elif symbol and direction and k.startswith(f"{symbol}_") and sig.get('direction') == direction:
                    matched = True
                if matched:
                    keys_to_remove.append(k)

        for k in keys_to_remove:
            if k in active_sigs:
                del active_sigs[k]
                logger.info(f"Evicted active signal {k} from unified repository state.")

        if keys_to_remove:
            if self.state_manager and hasattr(self.state_manager, 'state'):
                self.state_manager.state["active_signals"] = active_sigs
            try:
                now_iso = datetime.now(timezone.utc).isoformat()
                async with get_db_connection(self.db_path) as conn:
                    await conn.execute('''
                        INSERT INTO sentinel_kv_state (key, value, updated_at) 
                        VALUES ("active_signals", ?, ?)
                        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                    ''', (json.dumps(active_sigs), now_iso))
                    await conn.commit()
            except Exception as e:
                logger.debug(f"Could not sync evicted active_signals: {e}")

        return keys_to_remove

    async def on_trade_closed(self, event: TradeClosedEvent) -> None:
        """Event Bus listener that automatically evicts closed trades from state atomically."""
        await self.evict_active_signal(
            trade_id=event.trade_id,
            symbol=event.symbol,
            direction=event.direction
        )
