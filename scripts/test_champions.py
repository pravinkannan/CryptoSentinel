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
        premium_trades = [t for t in real_trades if t.tier in ["PREMIUM", "VIP"]]
        standard_trades = [t for t in real_trades if t.tier not in ["PREMIUM", "VIP"]]

        premium_wr = (sum(1 for t in premium_trades if t.net_pnl_pct > 0) / len(premium_trades) * 100) if premium_trades else 0.0
        premium_pnl = sum(t.net_pnl_pct for t in premium_trades)

        standard_wr = (sum(1 for t in standard_trades if t.net_pnl_pct > 0) / len(standard_trades) * 100) if standard_trades else 0.0
        standard_pnl = sum(t.net_pnl_pct for t in standard_trades)

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
            "standard_trades": len(standard_trades),
            "standard_wr": round(standard_wr, 1),
            "standard_pnl": round(standard_pnl, 2),
            "premium_trades": len(premium_trades),
            "premium_wr": round(premium_wr, 1),
            "premium_pnl": round(premium_pnl, 2),
        })

        print(f"[*] {sym:<10} {tf:<4} | All: {len(real_trades):<3} ({global_wr:>5.1f}%, {global_pnl:>+8.2f}%) | STD: {len(standard_trades):<3} ({standard_wr:>5.1f}%, {standard_pnl:>+8.2f}%) | PREM: {len(premium_trades):<3} ({premium_wr:>5.1f}%, {premium_pnl:>+8.2f}%) ({dur}s)")

    print("-" * 105)
    tot_pnl = sum(r['global_pnl'] for r in results)
    avg_wr = sum(r['global_wr'] for r in results) / len(results) if results else 0
    tot_prem_n = sum(r['premium_trades'] for r in results)
    tot_prem_w = sum(round(r['premium_wr'] * r['premium_trades'] / 100) for r in results)
    tot_prem_wr = (tot_prem_w / tot_prem_n * 100) if tot_prem_n else 0
    tot_prem_pnl = sum(r['premium_pnl'] for r in results)

    tot_std_n = sum(r['standard_trades'] for r in results)
    tot_std_w = sum(round(r['standard_wr'] * r['standard_trades'] / 100) for r in results)
    tot_std_wr = (tot_std_w / tot_std_n * 100) if tot_std_n else 0
    tot_std_pnl = sum(r['standard_pnl'] for r in results)

    print(f"TOTAL APPROVED CHAMPIONS BENCHMARK:")
    print(f"  GLOBAL:   {len(results)} pairs | Trades: {tot_prem_n + tot_std_n} | Win Rate: {avg_wr:.1f}% | Net PnL: {tot_pnl:+.2f}%")
    print(f"  STANDARD: Trades: {tot_std_n} | Win Rate: {tot_std_wr:.1f}% | Net PnL: {tot_std_pnl:+.2f}%")
    print(f"  PREMIUM:  Trades: {tot_prem_n} | Win Rate: {tot_prem_wr:.1f}% | Net PnL: {tot_prem_pnl:+.2f}%")
    print(f"Total Elapsed Time: {round(time.time() - t_start, 1)}s")
    print("=" * 105)
    return results

if __name__ == "__main__":
    run_champions()

