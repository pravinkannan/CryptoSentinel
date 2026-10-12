"""
Enterprise unit tests for StateManager persistence and resilience.
Tests:
- Positive save and load roundtrip of seen news GUIDs and alert crosses
- Missing file recovery
- Corrupted / invalid JSON file error resilience
- Empty file handling
- Partial schema / missing keys backwards compatibility
- Async concurrency locking
"""

import os
import json
import asyncio
import pytest
from core.state_manager import StateManager


def test_state_manager_defaults():
    """Verify clean initial state on brand new StateManager instance."""
    sm = StateManager(filename="non_existent.json")
    assert sm.state["seen_guids"] == []
    assert sm.state["last_crossed_alerts"] == {}
    assert sm.state["active_signals"] == {}


def test_state_manager_save_and_load_roundtrip(tmp_path):
    """
    Positive case: StateManager accurately serializes to disk
    and re-deserializes in a separate instance without data loss.
    """
    async def run_test():
        state_file = str(tmp_path / "sentinel_state.json")
        sm_writer = StateManager(filename=state_file)

        seen_guids = {"news_guid_101", "news_guid_102", "news_guid_103"}
        last_alerts = {
            "BTC/USDT": ["24h High_65000.0", "Psychological 60000.0"],
            "ETH/USDT": ["Recent Low_3200.0"]
        }

        # Save state
        await sm_writer.save_state(seen_guids, last_alerts)

        # Confirm file exists and is valid JSON
        assert os.path.exists(state_file)
        with open(state_file, 'r') as f:
            disk_data = json.load(f)
        assert set(disk_data["seen_guids"]) == seen_guids
        assert disk_data["last_crossed_alerts"] == last_alerts

        # Load in a fresh StateManager instance
        sm_reader = StateManager(filename=state_file)
        await sm_reader.load_state()

        assert set(sm_reader.state["seen_guids"]) == seen_guids
        assert sm_reader.state["last_crossed_alerts"] == last_alerts

    asyncio.run(run_test())


def test_state_manager_missing_file_handled_gracefully(tmp_path):
    """
    Edge case: Loading state when file does not exist on disk
    should not raise FileNotFoundError and should preserve defaults.
    """
    async def run_test():
        non_existent_file = str(tmp_path / "does_not_exist.json")
        sm = StateManager(filename=non_existent_file)
        
        await sm.load_state()
        assert sm.state["seen_guids"] == []
        assert sm.state["last_crossed_alerts"] == {}
        assert sm.state["active_signals"] == {}

    asyncio.run(run_test())


def test_state_manager_corrupted_json_resilience(tmp_path):
    """
    Edge case: Corrupted JSON file (e.g. abrupt system crash or partial write)
    must log an error and not raise JSONDecodeError or crash the engine.
    """
    async def run_test():
        corrupt_file = tmp_path / "corrupt_state.json"
        corrupt_file.write_text("{ this is completely invalid json ###", encoding="utf-8")

        sm = StateManager(filename=str(corrupt_file))
        await sm.load_state()

        # Defaults maintained despite corrupt file on disk
        assert sm.state["seen_guids"] == []
        assert sm.state["last_crossed_alerts"] == {}

    asyncio.run(run_test())


def test_state_manager_empty_file_resilience(tmp_path):
    """
    Edge case: 0-byte empty file on disk must not crash json.load().
    """
    async def run_test():
        empty_file = tmp_path / "empty_state.json"
        empty_file.write_text("", encoding="utf-8")

        sm = StateManager(filename=str(empty_file))
        await sm.load_state()

        assert sm.state["seen_guids"] == []
        assert sm.state["last_crossed_alerts"] == {}

    asyncio.run(run_test())


def test_state_manager_partial_keys_compatibility(tmp_path):
    """
    Edge case: State file has only a subset of keys (backwards compatibility).
    """
    async def run_test():
        partial_file = tmp_path / "partial_state.json"
        partial_data = {"seen_guids": ["legacy_id_1"]}
        partial_file.write_text(json.dumps(partial_data), encoding="utf-8")

        sm = StateManager(filename=str(partial_file))
        await sm.load_state()

        assert sm.state["seen_guids"] == ["legacy_id_1"]
        # Missing keys must fall back to default dictionaries
        assert sm.state["last_crossed_alerts"] == {}
        assert sm.state["active_signals"] == {}

    asyncio.run(run_test())


def test_state_manager_concurrent_access_locking(tmp_path):
    """
    Concurrency test: Concurrent async saves and loads run safely under asyncio.Lock.
    """
    async def run_test():
        state_file = str(tmp_path / "concurrent_state.json")
        sm = StateManager(filename=state_file)

        async def worker(worker_id: int):
            for i in range(5):
                await sm.save_state({f"guid_{worker_id}_{i}"}, {f"SYM_{worker_id}": [f"ALERT_{i}"]})
                await sm.load_state()

        # Run 5 concurrent workers
        await asyncio.gather(*(worker(i) for i in range(5)))
        # Final state must be readable without corrupt json syntax
        assert os.path.exists(state_file)
        with open(state_file, 'r') as f:
            final_data = json.load(f)
        assert isinstance(final_data, dict)

    asyncio.run(run_test())
