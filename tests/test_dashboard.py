"""Unit and integration tests for Streamlit Analytics Dashboard data loaders and read-only enforcement."""
import json
import os
import sqlite3
import pytest

from ui.dashboard import (
    get_readonly_connection,
    load_trades_data,
    load_rejected_signals_data,
    load_active_signals_state
)


def test_dashboard_readonly_enforcement(tmp_path):
    """Verify that get_readonly_connection strictly prevents writing to SQLite."""
    db_file = str(tmp_path / "readonly_test.db")
    with sqlite3.connect(db_file) as init_conn:
        init_conn.execute("CREATE TABLE test (val TEXT)")
        init_conn.execute("INSERT INTO test VALUES ('sample')")
        init_conn.commit()

    conn = get_readonly_connection(db_file)
    cur = conn.cursor()
    # Read should succeed
    rows = cur.execute("SELECT * FROM test").fetchall()
    assert len(rows) == 1

    # Write attempt must raise an OperationalError
    with pytest.raises(sqlite3.OperationalError):
        cur.execute("INSERT INTO test VALUES ('illegal_write')")
        conn.commit()
    conn.close()


def test_dashboard_data_loaders(tmp_path):
    """Test dashboard data parsing, regime classification, and fatal flaw categorization."""
    db_file = str(tmp_path / "dashboard_test.db")
    with sqlite3.connect(db_file) as conn:
        conn.execute("""
        CREATE TABLE trades (
            trade_id TEXT PRIMARY KEY,
            timestamp TEXT,
            symbol TEXT,
            direction TEXT,
            entry_price REAL,
            sl_price REAL,
            tp1_price REAL,
            tp2_price REAL,
            leverage REAL,
            tp1_hit INTEGER,
            tp2_hit INTEGER,
            trailing_sl INTEGER,
            status TEXT,
            pnl_pct REAL,
            net_pnl_pct REAL,
            gross_pnl_pct REAL,
            ttl_mins INTEGER,
            exit_price REAL
        )
        """)
        conn.execute("""
        CREATE TABLE evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id TEXT,
            signal_payload TEXT,
            pattern_name TEXT,
            confidence_score REAL,
            confluences_applied TEXT,
            regime_state TEXT,
            mae_pct REAL,
            mfe_pct REAL
        )
        """)
        conn.execute("""
        CREATE TABLE rejected_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            symbol TEXT,
            timeframe TEXT,
            direction TEXT,
            pattern_name TEXT,
            confidence_score REAL,
            base_score REAL,
            vol_points REAL,
            level_points REAL,
            vwap_points REAL,
            ob_points REAL,
            trend_tailwinds REAL,
            catalyst_points REAL,
            friction REAL,
            net_score REAL,
            scoring_checklist TEXT,
            eval_trace_json TEXT,
            rejection_reason TEXT
        )
        """)
        conn.execute("""
        CREATE TABLE sentinel_kv_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT
        )
        """)

        # Insert 1 Reversal Trade (80% floor) and 1 Continuation Trade (86% floor)
        conn.execute("""
        INSERT INTO trades (trade_id, timestamp, symbol, direction, entry_price, sl_price, tp1_price, tp2_price, leverage, status, net_pnl_pct)
        VALUES ('T1', '2026-10-25T12:00:00', 'BTC/USDT', 'LONG', 65000, 64000, 67000, 69000, 5, 'WIN_TP1', 3.5)
        """)
        conn.execute("""
        INSERT INTO evaluations (trade_id, pattern_name, confidence_score, regime_state)
        VALUES ('T1', 'Bullish Hammer / Rejection', 82.0, 'NORMAL')
        """)

        conn.execute("""
        INSERT INTO trades (trade_id, timestamp, symbol, direction, entry_price, sl_price, tp1_price, tp2_price, leverage, status, net_pnl_pct)
        VALUES ('T2', '2026-10-25T13:00:00', 'ETH/USDT', 'SHORT', 2500, 2550, 2400, 2350, 5, 'LOSS', -2.1)
        """)
        conn.execute("""
        INSERT INTO evaluations (trade_id, pattern_name, confidence_score, regime_state)
        VALUES ('T2', 'Bearish 20/50 EMA Crossover', 88.0, 'NORMAL')
        """)

        # Insert rejected signals with fatal flaw
        trace_json = json.dumps({
            "stage1_passed": False,
            "circuit_breaker_tripped": True,
            "fatal_flaw": "Phase 1 Gate Failed: 4H Macro Conflict"
        })
        conn.execute("""
        INSERT INTO rejected_signals (timestamp, symbol, timeframe, direction, pattern_name, confidence_score, eval_trace_json, rejection_reason)
        VALUES ('2026-10-25T14:00:00', 'SOL/USDT', '15m', 'LONG', 'Bullish Belthold', 72.5, ?, 'Macro opposition')
        """, (trace_json,))

        # Insert active signals in KV state
        active_state = {
            'BTC/USDT_15m': {
                'trade_id': 'T_ACTIVE_1',
                'direction': 'LONG',
                'confidence': 87.0,
                'timeframe': '15m',
                'ttl_mins': 180
            }
        }
        conn.execute("""
        INSERT INTO sentinel_kv_state (key, value, updated_at)
        VALUES ('active_signals', ?, '2026-10-25T14:30:00')
        """, (json.dumps(active_state),))

        conn.commit()

    # Load via dashboard functions
    df_trades = load_trades_data(db_file)
    assert len(df_trades) == 2

    # Check regime classification
    t1_row = df_trades[df_trades['trade_id'] == 'T1'].iloc[0]
    t2_row = df_trades[df_trades['trade_id'] == 'T2'].iloc[0]
    assert t1_row['regime_type'] == "Reversal (80% Floor)"
    assert t1_row['regime_floor'] == 80.0
    assert t1_row['is_win'] == True

    assert t2_row['regime_type'] == "Continuation (86% Floor)"
    assert t2_row['regime_floor'] == 86.0
    assert t2_row['is_win'] == False

    # Check rejected signals loader
    df_rejected = load_rejected_signals_data(db_file)
    assert len(df_rejected) == 1
    assert df_rejected.iloc[0]['fatal_flaw_clean'] == "Phase 1 Gate Failed: 4H Macro Conflict"
    assert df_rejected.iloc[0]['confidence_score'] == 72.5

    # Check active signals loader
    active_data = load_active_signals_state(db_file)
    assert 'BTC/USDT_15m' in active_data
    assert active_data['BTC/USDT_15m']['confidence'] == 87.0
