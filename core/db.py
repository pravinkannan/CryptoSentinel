"""
Database connection utilities for SQLite with WAL mode optimization.
"""

import contextlib
import aiosqlite
from typing import AsyncIterator

@contextlib.asynccontextmanager
async def get_db_connection(db_path: str = 'data/sentinel.db') -> AsyncIterator[aiosqlite.Connection]:
    """Provides a managed asynchronous SQLite connection with WAL optimization."""
    async with aiosqlite.connect(db_path, timeout=20.0, isolation_level=None) as conn:
        await conn.execute('PRAGMA journal_mode = WAL;')
        await conn.execute('PRAGMA synchronous = NORMAL;')
        await conn.execute('PRAGMA busy_timeout = 5000;')
        yield conn
