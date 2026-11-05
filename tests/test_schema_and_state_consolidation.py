"""
Unit tests for database schema and application state consolidation.
"""

import pytest
import asyncio
import os
import json
from infrastructure.database.repository import SentinelRepository
from engine.journal_manager import JournalManager
from core.db import get_db_connection


def test_schema_unification_via_repository(tmp_path):
    """Verify that JournalManager delegates table creation to SentinelRepository."""
    async def run_test():
        db_file = str(tmp_path / "test_unified.db")
        journal = JournalManager(db_path=db_file)

        await journal.ensure_db_exists()

        async with get_db_connection(db_file) as conn:
            cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [r[0] for r in await cursor.fetchall()]

        assert 'trades' in tables
        assert 'evaluations' in tables
        assert 'trade_autopsies' in tables
        assert 'rejected_signals' in tables
        assert 'sentinel_kv_state' in tables

    asyncio.run(run_test())


def test_migrate_json_state_to_sqlite(tmp_path):
    """Verify that sentinel_state.json is safely migrated into sentinel_kv_state."""
    async def run_test():
        db_file = str(tmp_path / "test_migration.db")
        json_file = str(tmp_path / "sentinel_state.json")

        test_state = {
            "seen_guids": ["guid_1", "guid_2"],
            "active_signals": {"SOL_1h": {"symbol": "SOL/USDT", "direction": "LONG"}},
            "last_crossed_alerts": {"BTC/USDT": ["level_80000"]}
        }
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(test_state, f)

        repo = SentinelRepository(db_path=db_file)
        await repo.ensure_schema()
        migrated = await repo.migrate_json_state_to_db(json_path=json_file)
        assert migrated is True

        seen = await repo.get_state_value("seen_guids")
        assert seen == ["guid_1", "guid_2"]

        active = await repo.get_state_value("active_signals")
        assert "SOL_1h" in active
        assert active["SOL_1h"]["direction"] == "LONG"

    asyncio.run(run_test())
