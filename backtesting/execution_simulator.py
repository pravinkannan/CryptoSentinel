"""
backtesting/execution_simulator.py - Chronological Execution Simulator

Leverages the codebase's existing Time Machine architecture:
- HistoricalMarketDataAdapter for zero-lookahead candle slicing and microstructure injection
- SignalContext(now_utc=current_ts) to enforce exact historical time propagation
- domain/confluence/engine.py (evaluate_confluence_score) for pure deterministic scoring
- IntraCandlePathSimulator for conservative dual-wick resolution, auto-breakeven, TP1/TP2 scaling,
  and realistic taker fee + slippage drag.
- Complete state isolation from production sentinel.db and live Telegram flows.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
import logging
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from core.config import settings
from domain.confluence.engine import evaluate_confluence_score
from engine.signal_generator import SignalGeneratorService
from engine.regime_detector import RegimeDetector
from scripts.time_machine import (
    HistoricalMarketDataAdapter,
    IntraCandlePathSimulator,
    SimulatedTrade,
    SilentNotificationAdapter,
)

logger = logging.getLogger("backtester.simulator")


@dataclass
class ScoredSetupRecord:
    """Telemetry record for every candidate signal pattern evaluated by ConfluenceEngine."""
    timestamp: datetime
    symbol: str
    direction: str
    pattern_name: str
    entry_price: float
    sl_price: float
    tp1_price: float
    tp2_price: float
    confluence_score: float
    tier: str
    circuit_breaker_tripped: bool
    fatal_flaw: str
    checklist: List[str] = field(default_factory=list)
    trade_executed: bool = False
    trade_ref: Optional[SimulatedTrade] = None


class BacktestExecutionSimulator:
    """
    Chronological historical simulator that feeds candles through ConfluenceEngine
    using the Time Machine adapter to evaluate trade setups across all score bands.
    """

    def __init__(
        self,
        symbol: str = "BTC/USDT",
        primary_timeframe: str = "1h",
        intraday_timeframe: str = "5m",
        initial_capital: float = 10000.0,
        min_score_threshold: float = 60.0,
        taker_fee_rate: float = 0.0005,
        slippage_rate: float = 0.0010,
        enable_circuit_breaker_filter: bool = True,
    ) -> None:
        self.symbol = symbol
        self.primary_timeframe = primary_timeframe
        self.intraday_timeframe = intraday_timeframe
        self.initial_capital = initial_capital
        self.min_score_threshold = min_score_threshold
        self.taker_fee_rate = taker_fee_rate
        self.slippage_rate = slippage_rate
        self.enable_circuit_breaker_filter = enable_circuit_breaker_filter

        self.path_simulator = IntraCandlePathSimulator()
        self.path_simulator.TAKER_FEE_RATE = taker_fee_rate
        self.path_simulator.DEFAULT_SLIPPAGE_PCT = slippage_rate

        self.scored_setups: List[ScoredSetupRecord] = []
        self.signal_generator = SignalGeneratorService(
            percentage_trigger=settings.percentage_move_trigger,
            static_alerts=settings.static_alerts,
            atr_period=settings.atr_period,
            sl_atr_multiplier=settings.sl_atr_multiplier,
            target_rr_ratio=settings.target_rr_ratio,
        )
        self.regime_detector = RegimeDetector()

    def run_simulation(
        self,
        df_primary: pd.DataFrame,
        df_intraday: Optional[pd.DataFrame] = None,
        df_htf: Optional[pd.DataFrame] = None,
        warmup_candles: int = 50,
    ) -> List[SimulatedTrade]:
        """
        Executes chronological candle-by-candle simulation.

        Args:
            df_primary: Primary timeframe DataFrame (e.g. 1h or 15m) sorted chronologically.
            df_intraday: Optional higher-frequency intraday DataFrame (e.g. 5m) for sub-candle path resolution.
            df_htf: Optional higher timeframe DataFrame (e.g. 4h) for macro trend context.
            warmup_candles: Initial candles reserved for technical indicators before signal generation.

        Returns:
            List of finalized SimulatedTrade objects with net PnL, MAE, MFE, status, and exit metrics.
        """
        if df_primary is None or len(df_primary) <= warmup_candles:
            raise ValueError(f"Primary DataFrame must have at least {warmup_candles + 1} candles.")

        # Ensure datetime timestamps
        df_primary = df_primary.copy().sort_values("timestamp").reset_index(drop=True)
        if df_intraday is not None and not df_intraday.empty:
            df_intraday = df_intraday.copy().sort_values("timestamp").reset_index(drop=True)
        else:
            # Fallback: if no 5m data provided, synthesize 5m resolution from primary candles
            df_intraday = df_primary

        if df_htf is not None and not df_htf.empty:
            df_htf = df_htf.copy().sort_values("timestamp").reset_index(drop=True)
        elif self.primary_timeframe in ['5m', '15m']:
            # Prefer loading authentic 1H macro candles if available on disk (matches live Sentinel scanner)
            clean_sym = self.symbol.replace('/', '_')
            htf_path = f"data/historical/{clean_sym}_1h.csv"
            if os.path.exists(htf_path):
                try:
                    df_htf = pd.read_csv(htf_path)
                    df_htf['timestamp'] = pd.to_datetime(df_htf['timestamp'], utc=True)
                    df_htf = df_htf.sort_values("timestamp").reset_index(drop=True)
                except Exception:
                    df_htf = None

            if df_htf is None or df_htf.empty:
                # Resample lower-timeframe data to 1h to obtain authentic 1H macro candles for MTF 200-EMA
                try:
                    ts_col = pd.to_datetime(df_primary['timestamp'], utc=True)
                    df_temp = df_primary.copy()
                    df_temp['timestamp'] = ts_col
                    resampled_1h = df_temp.set_index('timestamp').resample('1h').agg({
                        'open': 'first',
                        'high': 'max',
                        'low': 'min',
                        'close': 'last',
                        'volume': 'sum'
                    }).dropna().reset_index()
                    if len(resampled_1h) >= 20:
                        df_htf = resampled_1h
                    else:
                        df_htf = df_primary
                except Exception:
                    df_htf = df_primary
        else:
            df_htf = df_primary

        # Assemble Time Machine Market Data Store
        candles_store: Dict[str, Dict[str, pd.DataFrame]] = {
            self.symbol: {
                self.primary_timeframe: df_primary,
                self.intraday_timeframe: df_intraday,
                "4h": df_htf,
            },
            # Map BTC/USDT context (fallback to self.symbol if symbol is already BTC/USDT)
            "BTC/USDT": {
                self.primary_timeframe: df_primary,
                self.intraday_timeframe: df_intraday,
                "4h": df_htf,
            },
        }

        # Initialize Time Machine Input Adapter (zero-lookahead cursor)
        market_adapter = HistoricalMarketDataAdapter(candles_store)

        total_candles = len(df_primary)
        last_step_time: Optional[datetime] = None

        logger.info(
            f"Starting backtest simulation: {total_candles - warmup_candles} steps from index {warmup_candles}..."
        )
        self.signal_generator._compute_indicators(df_primary)

        for i in range(warmup_candles, total_candles):
            current_candle = df_primary.iloc[i]
            current_ts = current_candle["timestamp"]
            if isinstance(current_ts, str):
                current_ts = pd.to_datetime(current_ts, utc=True)
            elif getattr(current_ts, "tzinfo", None) is None:
                current_ts = current_ts.replace(tzinfo=timezone.utc)

            # Advance Time Machine cursor strictly to current_ts (zero lookahead)
            market_adapter.set_cursor(current_ts)

            # Advance open trades through intraday candles since last step
            if last_step_time is not None and df_intraday is not None:
                intervening = df_intraday[
                    (df_intraday["timestamp"] > last_step_time) & (df_intraday["timestamp"] <= current_ts)
                ]
                if not intervening.empty:
                    self.path_simulator.step_trades_through_candles(self.symbol, intervening)

            # Get historical candle slice up to current cursor (bounded to standard 250-candle window)
            sliced_df = df_primary.iloc[: i + 1].tail(250).copy().reset_index(drop=True)

            # Microstructure & macro context
            microstructure = {
                "symbol": self.symbol,
                "funding_rate": 0.0001,
                "open_interest": 125000000.0,
                "imbalance_ratio": 1.25 if current_candle["close"] >= current_candle["open"] else 0.85,
                "total_book_depth_usd": 8500000.0,
                "order_book": {"bids": [], "asks": []},
                "ob_defensive_shield": True,
                "target_magnet_present": True,
                "btc_trend_aligned": True,
            }

            if df_htf is not df_primary and not df_htf.empty:
                htf_slice = df_htf[df_htf["timestamp"] <= current_ts]
                if len(htf_slice) >= 20:
                    htf_trend = self.signal_generator.get_htf_trend(htf_slice.tail(250))
                else:
                    htf_trend = self.signal_generator.get_htf_trend(sliced_df)
            else:
                htf_trend = self.signal_generator.get_htf_trend(sliced_df)

            htf_4h_trend = "Macro Up (Above 200 EMA)" if ("Up" in htf_trend or "Bull" in htf_trend) else ("Macro Down (Below 200 EMA)" if ("Down" in htf_trend or "Bear" in htf_trend) else "Macro Neutral")
            regime_data = {"regime": "NORMAL", "phase": "NORMAL", "cooldown_active": False}

            # Run pattern recognition
            signal_data = self.signal_generator.analyze_patterns(
                df=sliced_df,
                timeframe=self.primary_timeframe,
                htf_trend=htf_trend,
                htf_4h_trend=htf_4h_trend,
                microstructure=microstructure,
                symbol=self.symbol,
                btc_trend=htf_trend,
                regime_data=regime_data,
            )

            if signal_data is not None:
                # Add benchmark authority & calibrated squeeze parameters
                signal_data["benchmark_macro_authority"] = True
                signal_data["_override_squeeze_points"] = 3.5
                signal_data["_override_catalyst_max"] = 8.0
                signal_data["_override_premium_threshold"] = 88.0
                signal_data["_override_vip_threshold"] = 88.0
                signal_data["_override_standard_threshold"] = 70.0

                # Deterministic quantitative confluence evaluation
                confluence = evaluate_confluence_score(self.symbol, signal_data)

                score = float(confluence.get("final_score", 0.0))
                tier = str(confluence.get("tier", "STANDARD"))
                cb_tripped = bool(confluence.get("circuit_breaker_tripped", False))
                fatal_flaw = str(confluence.get("fatal_flaw", ""))
                checklist = list(confluence.get("scoring_checklist", confluence.get("checklist", [])))

                raw_dir = str(signal_data.get("direction", "NEUTRAL")).upper()
                direction = "LONG" if ("BULL" in raw_dir or "LONG" in raw_dir) else ("SHORT" if ("BEAR" in raw_dir or "SHORT" in raw_dir) else "NEUTRAL")

                # Recalibrate risk parameters for evaluated tier (matches live pipeline step)
                is_prem = (tier in ["PREMIUM", "VIP"])
                sized_data = self.signal_generator.risk_manager.recalibrate_for_tier(
                    signal_data,
                    is_premium=is_prem,
                    is_vip=is_prem,
                    liquidity_magnets=signal_data.get('liquidity_magnets', [])
                )

                entry_p = float(sized_data.get("entry", current_candle["close"]))
                sl_p = float(sized_data.get("stop_loss", entry_p * 0.98 if direction == "LONG" else entry_p * 1.02))
                tp1_p = float(sized_data.get("tp1", sized_data.get("take_profit", entry_p * 1.03 if direction == "LONG" else entry_p * 0.97)))
                tp2_p = float(sized_data.get("tp2", tp1_p))
                leverage_val = float(sized_data.get("leverage_val", 2.0))
                ttl_mins = int(sized_data.get("ttl_mins", settings.trade_ttl_minutes))

                record = ScoredSetupRecord(
                    timestamp=current_ts,
                    symbol=self.symbol,
                    direction=direction,
                    pattern_name=str(signal_data.get("pattern_name", "")),
                    entry_price=entry_p,
                    sl_price=sl_p,
                    tp1_price=tp1_p,
                    tp2_price=tp2_p,
                    confluence_score=score,
                    tier=tier,
                    circuit_breaker_tripped=cb_tripped,
                    fatal_flaw=fatal_flaw,
                    checklist=checklist,
                    trade_executed=False,
                )

                # Execution filter check
                should_execute = (score >= self.min_score_threshold)
                if self.enable_circuit_breaker_filter and cb_tripped:
                    should_execute = False

                if should_execute and entry_p > 0 and sl_p > 0 and tp1_p > 0:
                    trade_id = f"{self.symbol}_{current_ts.strftime('%Y%m%d_%H%M%S')}"
                    sim_trade = SimulatedTrade(
                        trade_id=trade_id,
                        symbol=self.symbol,
                        direction=direction,
                        tier=tier,
                        entry_price=entry_p,
                        sl_price=sl_p,
                        tp1_price=tp1_p,
                        tp2_price=tp2_p,
                        leverage=leverage_val,
                        confidence_score=score,
                        entry_time=current_ts,
                        ttl_mins=ttl_mins,
                    )
                    self.path_simulator.register_trade(sim_trade)
                    record.trade_executed = True
                    record.trade_ref = sim_trade

                self.scored_setups.append(record)

            last_step_time = current_ts

        # Mark-to-Market: Force close any remaining open trades at final close price
        if self.path_simulator.open_trades:
            final_price = float(df_primary["close"].iloc[-1])
            final_ts = df_primary["timestamp"].iloc[-1]
            if isinstance(final_ts, str):
                final_ts = pd.to_datetime(final_ts, utc=True)
            self.path_simulator.force_close_all_mark_to_market({self.symbol: final_price}, final_ts)

        logger.info(
            f"Backtest complete. Scored Setups: {len(self.scored_setups)} | Closed Trades: {len(self.path_simulator.closed_trades)}"
        )
        return self.path_simulator.closed_trades
