"""
scripts/health_check.py - Enterprise Health & Diagnostic Suite
Validates configuration, database integrity, state manager, exchange failover pool,
drawdown status, and macro calendar guard.
Run with: python scripts/health_check.py
"""

import sys
import os
import asyncio
import sqlite3
import time
from typing import Dict, Any, List

WORKSPACE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from core.config import settings
from core.state_manager import StateManager
from core.calendar_guard import CalendarGuard
from execution.paper_trader import PaperTradingService
from data.market_data_service import MarketDataService


def print_banner():
    print("=" * 80)
    print(" CRYPTO SENTINEL: ENTERPRISE SYSTEM HEALTH & READINESS AUDIT")
    print("=" * 80)


async def check_configuration() -> bool:
    print("\n[1/6] Auditing Configuration Settings...")
    try:
        print(f"  • Primary Exchange: {settings.exchange.upper()}")
        print(f"  • Failover Pool: {[e.upper() for e in settings.fallback_exchanges]}")
        print(f"  • Monitored Assets: {settings.symbols}")
        print(f"  • Champion Matrix: {settings.symbol_timeframes}")
        print(f"  • Drawdown Circuit Breaker: Enabled={settings.enable_circuit_breaker} (Threshold: {settings.max_drawdown_percent}%)")
        print(f"  • Dispatch Floors: Continuations={settings.min_dispatch_confidence}% | Reversals={settings.min_dispatch_confidence_reversal}%")
        print("  -> Configuration: [PASSED]")
        return True
    except Exception as e:
        print(f"  -> Configuration: [FAILED] {e}")
        return False


async def check_database() -> bool:
    print("\n[2/6] Auditing SQLite Database & Schema...")
    db_path = getattr(settings, 'db_path', 'data/sentinel.db')
    if not os.path.exists(db_path):
        print(f"  • Database path '{db_path}' does not exist yet (will auto-create on start).")
        return True

    try:
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute("PRAGMA journal_mode")
        mode = c.fetchone()[0]
        c.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in c.fetchall()]
        c.execute("SELECT count(*) FROM trades")
        trade_count = c.fetchone()[0]
        conn.close()

        print(f"  • Database File: {db_path} ({os.path.getsize(db_path):,} bytes)")
        print(f"  • Journal Mode: {mode.upper()}")
        print(f"  • Registered Tables: {tables}")
        print(f"  • Total Trade Records: {trade_count}")
        print("  -> Database Integrity: [PASSED]")
        return True
    except Exception as e:
        print(f"  -> Database Integrity: [FAILED] {e}")
        return False


async def check_state_manager() -> bool:
    print("\n[3/6] Auditing State Manager Persistence...")
    try:
        sm = StateManager()
        await sm.load_state()
        active = sm.state.get("active_signals", {})
        seen_count = len(sm.state.get("seen_guids", []))
        print(f"  • Active Signals in Memory: {len(active)} ({list(active.keys())})")
        print(f"  • Deduplicated News GUIDs: {seen_count}")
        print("  -> State Manager: [PASSED]")
        return True
    except Exception as e:
        print(f"  -> State Manager: [FAILED] {e}")
        return False


async def check_drawdown_and_risk() -> bool:
    print("\n[4/6] Auditing Portfolio Drawdown & Margin Safety...")
    try:
        pt = PaperTradingService()
        dd = await pt.get_current_drawdown()
        limit = settings.max_drawdown_percent
        is_safe = dd <= limit
        status_str = "[PASSED]" if is_safe else "[ALERT: BREACHED]"
        print(f"  • Current Portfolio Drawdown: {dd:.2f}% (Safety Limit: {limit:.2f}%)")
        print(f"  -> Circuit Breaker Status: {status_str}")
        return is_safe
    except Exception as e:
        print(f"  -> Portfolio Risk Audit: [FAILED] {e}")
        return False


async def check_calendar_guard() -> bool:
    print("\n[5/6] Auditing Macro CalendarGuard...")
    try:
        cg = CalendarGuard()
        is_bo, msg = cg.check_blackout_window()
        if is_bo:
            print(f"  • ⚠️ Macro Blackout Active: {msg}")
        else:
            print("  • Normal Macro Environment: No active FOMC/CPI blackout.")
        print("  -> CalendarGuard: [PASSED]")
        return True
    except Exception as e:
        print(f"  -> CalendarGuard: [FAILED] {e}")
        return False


async def check_exchange_connectivity() -> bool:
    print("\n[6/6] Auditing Market Data Exchange Pool...")
    try:
        md = MarketDataService(
            exchange_id=settings.exchange,
            fallback_exchanges=settings.fallback_exchanges,
            proxy_url=settings.proxy_url
        )
        print(f"  • Active Exchange: {md.active_exchange_id.upper()}")
        print(f"  • Exchange Pool: {[e.upper() for e in md.exchange_pool]}")
        await md.close()
        print("  -> Exchange Pool Configuration: [PASSED]")
        return True
    except Exception as e:
        print(f"  -> Exchange Pool Configuration: [FAILED] {e}")
        return False


async def main():
    print_banner()
    t0 = time.time()
    results = [
        await check_configuration(),
        await check_database(),
        await check_state_manager(),
        await check_drawdown_and_risk(),
        await check_calendar_guard(),
        await check_exchange_connectivity(),
    ]
    print("\n" + "=" * 80)
    passed = sum(1 for r in results if r)
    total = len(results)
    if passed == total:
        print(f" ALL {total}/{total} ENTERPRISE HEALTH AUDITS PASSED ({time.time() - t0:.2f}s) - SYSTEM READY FOR PRODUCTION")
    else:
        print(f" ⚠️ AUDIT WARNING: {passed}/{total} CHECKS PASSED. REVIEW ITEMS MARKED [FAILED] ABOVE.")
    print("=" * 80)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

