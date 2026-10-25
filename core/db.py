"""
Database connection utilities for SQLite with WAL mode optimization and robust concurrency protection.
"""

import os
import contextlib
import aiosqlite
from collections.abc import AsyncGenerator

@contextlib.asynccontextmanager
async def get_db_connection(db_path: str = 'data/sentinel.db') -> AsyncGenerator[aiosqlite.Connection, None]:
    """
    Provides a managed asynchronous SQLite connection with enterprise-grade WAL optimization.
    Configured with a 30-second busy timeout and WAL journaling to eliminate locked database hazards.
    """
    abs_path = os.path.abspath(db_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    
    async with aiosqlite.connect(abs_path, timeout=30.0) as conn:
        await conn.execute('PRAGMA journal_mode = WAL;')
        await conn.execute('PRAGMA synchronous = NORMAL;')
        await conn.execute('PRAGMA busy_timeout = 30000;')
        await conn.execute('PRAGMA cache_size = -64000;')
        yield conn
