"""
scripts/test_champions.py - Targeted Champions Benchmark
Tests the 9 approved champions across BTC, ETH, SOL, XAU.
"""
import sys
import os
import json
import time

WORKSPACE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from backtesting.data_ingestion import load_ohlcv_csv
from backtesting.execution_simulator import BacktestExecutionSimulator
from backtesting.calibration import ScoreCalibrator

CHAMPIONS = [
    ("BTC/USDT", "5m"),
    ("BTC/USDT", "15m"),
    ("ETH/USDT", "5m"),
    ("ETH/USDT", "15m"),
    ("SOL/USDT", "5m"),
    ("SOL/USDT", "15m"),
    ("XAU/USDT", "15m"),
    ("XAU/USDT", "1h"),
    ("XAU/USDT", "4h"),
]

CANDLE_LIMITS = {
    "5m": 1200,
    "15m": 1200,
    "1h": 1200,
    "4h": 500,
}

def run_champions():
    print("=" * 105)
    print("TESTING APPROVED CHAMPIONS BENCHMARK")
    print("=" * 105)

    results = []
    t_start = time.time()

    for sym, tf in CHAMPIONS:
        clean_sym = sym.replace("/", "_")
        csv_path = f"data/historical/{clean_sym}_{tf}.csv"
        if not os.path.exists(csv_path):
            continue

        limit = CANDLE_LIMITS.get(tf, 1200)
        df_raw = load_ohlcv_csv(csv_path, symbol=sym, timeframe=tf)
        df_primary = df_raw.tail(limit).copy().reset_index(drop=True) if limit and len(df_raw) > limit else df_raw

        intra_path = f"data/historical/{clean_sym}_5m.csv"
        df_intra = None
        if tf in ["15m", "1h"] and os.path.exists(intra_path):
            df_intra_raw = load_ohlcv_csv(intra_path, symbol=sym, timeframe="5m")
            start_t = df_primary["timestamp"].iloc[0]
            end_t = df_primary["timestamp"].iloc[-1]
            df_intra = df_intra_raw[(df_intra_raw["timestamp"] >= start_t) & (df_intra_raw["timestamp"] <= end_t)].copy().reset_index(drop=True)

        t0 = time.time()
        sim = BacktestExecutionSimulator(
            symbol=sym,
            primary_timeframe=tf,
            intraday_timeframe="5m" if df_intra is not None else tf,
            min_score_threshold=65.0,
            taker_fee_rate=0.0005,
            slippage_rate=0.0003,
            enable_circuit_breaker_filter=True,
        )

        trades = sim.run_simulation(df_primary=df_primary, df_intraday=df_intra, warmup_candles=50)
        dur = round(time.time() - t0, 1)

        calibrator = ScoreCalibrator()
        summary = calibrator.calibrate(trades)

        real_trades = [t for t in trades if t.status != "MARK_TO_MARKET"]
        vip_trades = [t for t in real_trades if t.tier == "VIP" or t.confidence_score >= 88.0]

        vip_wr = (sum(1 for t in vip_trades if t.net_pnl_pct > 0) / len(vip_trades) * 100) if vip_trades else 0.0
        vip_pnl = sum(t.net_pnl_pct for t in vip_trades)

        global_wr = summary.overall_win_rate_pct
        global_pnl = summary.overall_net_pnl_pct
        pf = summary.overall_profit_factor

        results.append({
            "symbol": sym,
            "timeframe": tf,
            "trades": len(real_trades),
            "global_wr": round(global_wr, 1),
            "global_pnl": round(global_pnl, 2),
            "pf": round(pf, 2),
            "vip_trades": len(vip_trades),
            "vip_wr": round(vip_wr, 1),
            "vip_pnl": round(vip_pnl, 2),
        })

        print(f"[*] {sym:<10} {tf:<4} | Trades: {len(real_trades):<3} | WR: {global_wr:>5.1f}% | Net PnL: {global_pnl:>+8.2f}% | PF: {pf:>4.2f} | VIP WR: {vip_wr:>5.1f}% ({dur}s)")

    print("-" * 105)
    tot_pnl = sum(r['global_pnl'] for r in results)
    avg_wr = sum(r['global_wr'] for r in results) / len(results) if results else 0
    print(f"Total Approved Champions Net PnL: {tot_pnl:+.2f}% | Avg Win Rate: {avg_wr:.1f}% | Time: {round(time.time() - t_start, 1)}s")
    print("=" * 105)
    return results

if __name__ == "__main__":
    run_champions()

