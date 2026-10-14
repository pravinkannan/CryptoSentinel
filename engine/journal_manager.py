"""
JournalManager manages trade logging, evaluations, pattern drift detection, 
and post-trade autopsies in the unified sentinel.db.
"""

import os
import json
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, TypedDict
from core.config import settings
from core.db import get_db_connection


class TradeLogPayload(TypedDict, total=False):
    timestamp: str
    symbol: str
    direction: str
    entry_price: float
    sl_price: float
    tp1_price: float
    tp2_price: float
    leverage: float
    outcome: str
    pnl_pct: float
    ttl_mins: int
    signal_payload: Dict[str, Any]
    pattern_name: str
    confidence_score: float
    scoring_checklist: List[str]
    regime: str
    mae_pct: float
    mfe_pct: float


class AutopsyPayload(TypedDict, total=False):
    trade_id: str
    symbol: str
    direction: str
    failure_category: str
    summary: str
    key_culprit: str
    actionable_lesson: str
    timestamp: str
    net_pnl_pct: float


class JournalManager:
    """Manages trade lifecycles, historical performance metrics, and post-mortems."""
    
    def __init__(self, db_path: str = "data/sentinel.db") -> None:
        self.db_path: str = db_path

    async def ensure_db_exists(self) -> None:
        """Initializes tables and performs idempotent schema migrations."""
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        async with get_db_connection(self.db_path) as conn:
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
                    exit_price REAL,
                    pnl_pct REAL,
                    net_pnl_pct REAL DEFAULT 0.0,
                    gross_pnl_pct REAL DEFAULT 0.0
                )
            ''')
            try:
                await conn.execute('ALTER TABLE trades ADD COLUMN exit_price REAL DEFAULT 0.0')
            except Exception:
                pass
            try:
                await conn.execute('ALTER TABLE trades ADD COLUMN net_pnl_pct REAL DEFAULT 0.0')
                await conn.execute('ALTER TABLE trades ADD COLUMN gross_pnl_pct REAL DEFAULT 0.0')
            except Exception:
                pass
            try:
                await conn.execute(f'ALTER TABLE trades ADD COLUMN ttl_mins INTEGER DEFAULT {settings.trade_ttl_minutes}')
            except Exception:
                pass
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
            await conn.commit()

    async def log_trade(self, trade_data: Dict[str, Any]) -> str:
        """
        Logs a trade into the unified sentinel.db.
        Returns trade_id.
        """
        trade_id = str(uuid.uuid4())
        
        async with get_db_connection(self.db_path) as conn:
            # Insert into trades
            await conn.execute('''
                INSERT INTO trades (
                    trade_id, timestamp, symbol, direction, 
                    entry_price, sl_price, tp1_price, tp2_price, 
                    leverage, trailing_sl, status, pnl_pct, ttl_mins
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                trade_id,
                trade_data.get('timestamp', datetime.now(timezone.utc).isoformat()),
                trade_data.get('symbol'),
                trade_data.get('direction'),
                trade_data.get('entry_price'),
                trade_data.get('sl_price'),
                trade_data.get('tp1_price'),
                trade_data.get('tp2_price'),
                trade_data.get('leverage'),
                trade_data.get('sl_price'),  # initial trailing_sl is sl_price
                trade_data.get('outcome', 'PENDING'),
                trade_data.get('pnl_pct', 0.0),
                trade_data.get('ttl_mins', settings.trade_ttl_minutes)
            ))
            
            # Insert into evaluations
            await conn.execute('''
                INSERT INTO evaluations (
                    trade_id, signal_payload, pattern_name, 
                    confidence_score, confluences_applied, regime_state,
                    mae_pct, mfe_pct
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                trade_id,
                json.dumps(trade_data.get('signal_payload', {})),
                trade_data.get('pattern_name'),
                trade_data.get('confidence_score'),
                json.dumps(trade_data.get('scoring_checklist', [])),
                trade_data.get('regime'),
                trade_data.get('mae_pct', 0.0),
                trade_data.get('mfe_pct', 0.0)
            ))
            
            await conn.commit()
            return trade_id

    async def update_trade_outcome(
        self,
        trade_id: str,
        outcome: str,
        pnl_pct: float,
        mae_pct: float,
        mfe_pct: float,
        duration_minutes: float = 0.0
    ) -> None:
        """Updates an existing trade with its final outcome and metrics."""
        async with get_db_connection(self.db_path) as conn:
            await conn.execute('''
                UPDATE trades
                SET status = ?, pnl_pct = ?, net_pnl_pct = ?
                WHERE trade_id = ?
            ''', (outcome, pnl_pct, pnl_pct, trade_id))
            
            await conn.execute('''
                UPDATE evaluations
                SET mae_pct = ?, mfe_pct = ?
                WHERE trade_id = ?
            ''', (mae_pct, mfe_pct, trade_id))
            
            await conn.commit()

    async def get_pattern_win_rate(self, pattern_name: str, limit: int = 0) -> float:
        """
        Calculates the win rate for a specific pattern over the last N trades.
        Win is considered WIN_TP1 or WIN_TP2.
        """
        eval_limit = limit if limit > 0 else settings.pattern_drift_evaluation_window
        async with get_db_connection(self.db_path) as conn:
            cursor = await conn.execute('''
                SELECT t.status 
                FROM trades t
                JOIN evaluations e ON t.trade_id = e.trade_id
                WHERE e.pattern_name = ? AND t.status IN ('WIN_TP1', 'WIN_TP2', 'LOSS', 'BE', 'EXPIRED_PROFIT', 'EXPIRED_LOSS')
                ORDER BY t.timestamp DESC
                LIMIT ?
            ''', (pattern_name, eval_limit))
            results = list(await cursor.fetchall())

        if not results or len(results) < 5:
            return 1.0  # Default to 100% if no history or insufficient sample size

        total_trades = len(results)
        wins = sum(1 for r in results if r[0] in ('WIN_TP1', 'WIN_TP2', 'EXPIRED_PROFIT'))
        
        return wins / total_trades

    async def check_pattern_drift(self, pattern_name: str) -> str:
        """
        Pattern Drift Guard:
        Returns 'DEGRADED' if the pattern's rolling win rate < threshold.
        Returns 'HEALTHY' if >= threshold or if not enough data to confidently degrade.
        """
        win_rate = await self.get_pattern_win_rate(pattern_name, limit=settings.pattern_drift_evaluation_window)
        threshold = settings.pattern_drift_win_rate_threshold / 100.0
        
        if win_rate < threshold:
            return 'DEGRADED'
        
        return 'HEALTHY'

    async def get_all_pattern_drifts(self) -> Dict[str, str]:
        """Returns a dictionary of pattern_name: drift_status for all tracked patterns."""
        async with get_db_connection(self.db_path) as conn:
            cursor = await conn.execute('''
                SELECT DISTINCT pattern_name FROM evaluations
            ''')
            patterns = [row[0] for row in await cursor.fetchall()]

        drifts: Dict[str, str] = {}
        for pattern in patterns:
            drifts[pattern] = await self.check_pattern_drift(pattern)
            
        return drifts

    async def save_autopsy(self, autopsy_data: Dict[str, Any]) -> None:
        """Saves a post-mortem autopsy for a failed trade into sentinel.db."""
        async with get_db_connection(self.db_path) as conn:
            await conn.execute('''
                INSERT OR REPLACE INTO trade_autopsies (
                    trade_id, symbol, direction, failure_category,
                    summary, key_culprit, actionable_lesson, timestamp, net_pnl_pct
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                str(autopsy_data.get('trade_id', '')),
                str(autopsy_data.get('symbol', '')),
                str(autopsy_data.get('direction', '')),
                str(autopsy_data.get('failure_category', 'UNKNOWN')),
                str(autopsy_data.get('summary', '')),
                str(autopsy_data.get('key_culprit', '')),
                str(autopsy_data.get('actionable_lesson', '')),
                str(autopsy_data.get('timestamp', datetime.now(timezone.utc).isoformat())),
                float(autopsy_data.get('net_pnl_pct', 0.0))
            ))
            await conn.commit()

    async def get_trade_autopsy(self, trade_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves autopsy post-mortem data for a given trade_id."""
        async with get_db_connection(self.db_path) as conn:
            cursor = await conn.execute('''
                SELECT trade_id, symbol, direction, failure_category,
                       summary, key_culprit, actionable_lesson, timestamp, net_pnl_pct
                FROM trade_autopsies
                WHERE trade_id = ?
            ''', (trade_id,))
            row = await cursor.fetchone()
            if row:
                return {
                    'trade_id': row[0],
                    'symbol': row[1],
                    'direction': row[2],
                    'failure_category': row[3],
                    'summary': row[4],
                    'key_culprit': row[5],
                    'actionable_lesson': row[6],
                    'timestamp': row[7],
                    'net_pnl_pct': row[8]
                }
            return None
