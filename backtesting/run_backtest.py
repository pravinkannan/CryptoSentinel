#!/usr/bin/env python3
"""
backtesting/run_backtest.py - Standalone CLI Entrypoint for Systematic Historical Backtesting

Usage:
    python backtesting/run_backtest.py --csv path/to/candles.csv --symbol BTC/USDT --timeframe 1h
    python backtesting/run_backtest.py --demo  # Generates realistic synthetic data and runs backtest
"""

import argparse
from datetime import datetime, timezone, timedelta
import json
import logging
import os
import sys
from typing import Optional

# Enable UTF-8 encoding on Windows console
if sys.platform.startswith("win"):
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass

# Add workspace root to sys.path
WORKSPACE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

import numpy as np
import pandas as pd

from backtesting.data_ingestion import load_ohlcv_csv
from backtesting.execution_simulator import BacktestExecutionSimulator
from backtesting.calibration import ScoreCalibrator, split_walk_forward
from backtesting.tear_sheet import ConsoleTearSheet

logger = logging.getLogger("backtest_cli")


def generate_synthetic_ohlcv(
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    days: int = 30,
    base_price: float = 65000.0,
    output_path: str = "data/historical/synthetic_btc_1h.csv",
) -> str:
    """
    Generates a realistic historical OHLCV dataset with regime shifts, pullbacks,
    and cyclical volatility for zero-setup out-of-sample testing.
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    n_candles = days * 24

    start_time = datetime.now(timezone.utc) - timedelta(days=days)
    timestamps = [start_time + timedelta(hours=i) for i in range(n_candles)]

    np.random.seed(42)
    # Regime-switching geometric random walk with realistic crypto volatility
    regime = np.random.choice([-1, 0, 1], size=n_candles, p=[0.35, 0.30, 0.35])
    drift = regime * 0.0002
    vol = np.random.uniform(0.0010, 0.0022, size=n_candles)
    returns = np.random.normal(drift, vol)

    prices = base_price * np.exp(np.cumsum(returns))

    opens, highs, lows, closes, volumes = [], [], [], [], []
    for i in range(n_candles):
        p_close = float(prices[i])
        p_open = float(prices[i - 1]) if i > 0 else base_price
        c_high = max(p_open, p_close) * (1.0 + abs(np.random.normal(0, 0.0008)))
        c_low = min(p_open, p_close) * (1.0 - abs(np.random.normal(0, 0.0008)))
        c_vol = float(np.random.gamma(shape=3.0, scale=350.0) * (p_close / 1000.0))

        opens.append(round(p_open, 2))
        highs.append(round(c_high, 2))
        lows.append(round(c_low, 2))
        closes.append(round(p_close, 2))
        volumes.append(round(c_vol, 2))

    df = pd.DataFrame({
        "timestamp": [ts.isoformat() for ts in timestamps],
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    })

    df.to_csv(output_path, index=False)
    print(f"[*] Generated synthetic OHLCV dataset ({n_candles} candles) saved to: {output_path}")
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="CryptoSentinel Historical Backtesting & Calibration Engine",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--csv", type=str, default="", help="Path to historical OHLCV CSV file.")
    parser.add_argument("--symbol", type=str, default="BTC/USDT", help="Symbol identifier.")
    parser.add_argument("--days", type=int, default=20, help="Number of days to simulate in synthetic demo.")
    parser.add_argument("--timeframe", type=str, default="1h", help="Candle timeframe.")
    parser.add_argument("--intraday-csv", type=str, default="", help="Optional 5m intraday CSV for intra-candle resolution.")
    parser.add_argument("--initial-capital", type=float, default=10000.0, help="Initial capital in USD.")
    parser.add_argument("--out-of-sample-pct", type=float, default=0.30, help="Fraction of data reserved for out-of-sample testing.")
    parser.add_argument("--min-score", type=float, default=60.0, help="Minimum confluence score to simulate execution.")
    parser.add_argument("--taker-fee", type=float, default=0.0005, help="Taker fee per side (0.0005 = 0.05%).")
    parser.add_argument("--slippage", type=float, default=0.0003, help="Slippage percentage per trade (0.0003 = 0.03%).")
    parser.add_argument("--circuit-breaker-filter", action="store_true", help="Filter out trades when circuit breaker trips.")
    parser.add_argument("--export-trades", type=str, default="", help="Optional path to export simulated trades CSV.")
    parser.add_argument("--output-json", type=str, default="", help="Optional path to export calibration summary JSON.")
    parser.add_argument("--demo", action="store_true", help="Run with automatically generated synthetic dataset.")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose debug logging.")
    return parser.parse_args()


def run_backtest_cli() -> None:
    args = parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    csv_path = args.csv
    if not csv_path or args.demo:
        if not csv_path:
            csv_path = "data/historical/synthetic_btc_1h.csv"
        if not os.path.exists(csv_path) or args.demo:
            csv_path = generate_synthetic_ohlcv(symbol=args.symbol, timeframe=args.timeframe, days=args.days, output_path=csv_path)

    # 1. Ingestion
    print(f"\n[BACKTEST] Ingesting historical OHLCV data from {csv_path}...")
    full_df = load_ohlcv_csv(csv_path, symbol=args.symbol, timeframe=args.timeframe)

    intraday_df = None
    if args.intraday_csv and os.path.exists(args.intraday_csv):
        print(f"[BACKTEST] Ingesting high-resolution intraday candles from {args.intraday_csv}...")
        intraday_df = load_ohlcv_csv(args.intraday_csv, symbol=args.symbol, timeframe="5m")

    # 2. Walk-Forward Split
    in_sample_pct = 1.0 - args.out_of_sample_pct
    df_is, df_oos = split_walk_forward(full_df, in_sample_pct=in_sample_pct)

    print(f"[BACKTEST] Dataset Split: {len(df_is)} In-Sample candles | {len(df_oos)} Out-of-Sample candles.")

    # 3. In-Sample Calibration Run
    print("\n[BACKTEST] Running Phase 1: In-Sample Calibration...")
    sim_is = BacktestExecutionSimulator(
        symbol=args.symbol,
        primary_timeframe=args.timeframe,
        initial_capital=args.initial_capital,
        min_score_threshold=args.min_score,
        taker_fee_rate=args.taker_fee,
        slippage_rate=args.slippage,
        enable_circuit_breaker_filter=args.circuit_breaker_filter,
    )
    is_trades = sim_is.run_simulation(df_primary=df_is, df_intraday=intraday_df)

    calibrator = ScoreCalibrator()
    is_summary = calibrator.calibrate(is_trades)

    meta_is = {
        "symbol": args.symbol,
        "timeframe": args.timeframe,
        "period": f"{df_is['timestamp'].iloc[0].strftime('%Y-%m-%d')} to {df_is['timestamp'].iloc[-1].strftime('%Y-%m-%d')} (IN-SAMPLE)",
        "taker_fee_pct": args.taker_fee * 100,
        "slippage_pct": args.slippage * 100,
    }
    sheet_is = ConsoleTearSheet(is_summary, metadata=meta_is)
    print("\n" + "=" * 100)
    print("                    PHASE 1: IN-SAMPLE CALIBRATION TEAR SHEET")
    print("=" * 100)
    sheet_is.print()

    # 4. Out-of-Sample Validation Run
    if len(df_oos) > 60:
        print("\n[BACKTEST] Running Phase 2: Out-of-Sample Validation...")
        sim_oos = BacktestExecutionSimulator(
            symbol=args.symbol,
            primary_timeframe=args.timeframe,
            initial_capital=args.initial_capital,
            min_score_threshold=args.min_score,
            taker_fee_rate=args.taker_fee,
            slippage_rate=args.slippage,
            enable_circuit_breaker_filter=args.circuit_breaker_filter,
        )
        # Prepend warm-up candles from end of IS to OOS so indicator calculations don't drop the initial OOS period
        warmup_df = pd.concat([df_is.tail(50), df_oos]).reset_index(drop=True)
        oos_trades = sim_oos.run_simulation(df_primary=warmup_df, df_intraday=intraday_df, warmup_candles=50)

        oos_summary = calibrator.calibrate(oos_trades)
        meta_oos = {
            "symbol": args.symbol,
            "timeframe": args.timeframe,
            "period": f"{df_oos['timestamp'].iloc[0].strftime('%Y-%m-%d')} to {df_oos['timestamp'].iloc[-1].strftime('%Y-%m-%d')} (OUT-OF-SAMPLE)",
            "taker_fee_pct": args.taker_fee * 100,
            "slippage_pct": args.slippage * 100,
        }
        sheet_oos = ConsoleTearSheet(oos_summary, metadata=meta_oos)
        print("\n" + "=" * 100)
        print("                  PHASE 2: OUT-OF-SAMPLE VALIDATION TEAR SHEET")
        print("=" * 100)
        sheet_oos.print()

        all_trades = is_trades + oos_trades
        active_summary = oos_summary
    else:
        all_trades = is_trades
        active_summary = is_summary

    # Optional trade export
    if args.export_trades and all_trades:
        os.makedirs(os.path.dirname(os.path.abspath(args.export_trades)), exist_ok=True)
        records = [t.__dict__ for t in all_trades]
        trades_df = pd.DataFrame(records)
        trades_df.to_csv(args.export_trades, index=False)
        print(f"[EXPORT] Saved {len(all_trades)} trades to {args.export_trades}")

    # Optional JSON output
    if args.output_json:
        os.makedirs(os.path.dirname(os.path.abspath(args.output_json)), exist_ok=True)
        summary_dict = {
            "total_trades": active_summary.total_trades,
            "overall_win_rate_pct": active_summary.overall_win_rate_pct,
            "overall_net_pnl_pct": active_summary.overall_net_pnl_pct,
            "overall_profit_factor": active_summary.overall_profit_factor,
            "overall_sharpe_ratio": active_summary.overall_sharpe_ratio,
            "overall_max_drawdown_pct": active_summary.overall_max_drawdown_pct,
            "brier_score": active_summary.brier_score,
            "expected_calibration_error": active_summary.expected_calibration_error,
            "bands": [b.__dict__ for b in active_summary.bands],
        }
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(summary_dict, f, indent=2)
        print(f"[EXPORT] Saved calibration JSON summary to {args.output_json}")


if __name__ == "__main__":
    run_backtest_cli()
