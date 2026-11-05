"""
backtesting/calibration.py - Score Calibration & Quantitative Performance Metrics

Calculates empirical win probability per score band, Profit Factor, Sharpe Ratio,
Max Drawdown, Expected Value, and Calibration Error (ECE / Brier Score) for walk-forward
and out-of-sample validation.
"""

from dataclasses import dataclass, field
import math
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd


@dataclass
class ScoreBandMetrics:
    """Quantitative performance statistics for a specific confidence score band."""
    band_name: str
    min_score: float
    max_score: float
    sample_size: int = 0
    wins: int = 0
    losses: int = 0
    breakevens: int = 0
    win_rate_pct: float = 0.0
    loss_rate_pct: float = 0.0
    avg_score: float = 0.0
    gross_pnl_pct: float = 0.0
    net_pnl_pct: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    realized_rr: float = 0.0
    expected_value_pct: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    avg_mae_pct: float = 0.0
    avg_mfe_pct: float = 0.0
    calibrated_win_prob: float = 0.0  # Empirical probability (0.0 to 1.0)


@dataclass
class CalibrationSummary:
    """Overall summary report including calibration fidelity and aggregate metrics."""
    total_trades: int
    overall_win_rate_pct: float
    overall_net_pnl_pct: float
    overall_profit_factor: float
    overall_sharpe_ratio: float
    overall_max_drawdown_pct: float
    brier_score: float
    expected_calibration_error: float
    bands: List[ScoreBandMetrics] = field(default_factory=list)


DEFAULT_SCORE_BINS: List[Tuple[float, float, str]] = [
    (0.0, 65.0, "< 65%"),
    (65.0, 70.0, "65% - 70%"),
    (70.0, 75.0, "70% - 75%"),
    (75.0, 80.0, "75% - 80%"),
    (80.0, 85.0, "80% - 85%"),
    (85.0, 90.0, "85% - 90%"),
    (90.0, 100.0, ">= 90%"),
]


class ScoreCalibrator:
    """
    Analyzes simulated trades grouped by model confidence scores to empirically calibrate
    win probabilities and assess parameter edge.
    """

    def __init__(
        self,
        score_bins: Optional[List[Tuple[float, float, str]]] = None,
        annualization_factor: float = 252.0,
    ) -> None:
        self.score_bins = score_bins or DEFAULT_SCORE_BINS
        self.annualization_factor = annualization_factor

    def calibrate(self, trades: List[Any]) -> CalibrationSummary:
        """
        Groups trades into confidence bands and computes institutional statistics.
        Accepts SimulatedTrade objects or dictionaries with required trade attributes.
        """
        if not trades:
            return CalibrationSummary(
                total_trades=0,
                overall_win_rate_pct=0.0,
                overall_net_pnl_pct=0.0,
                overall_profit_factor=0.0,
                overall_sharpe_ratio=0.0,
                overall_max_drawdown_pct=0.0,
                brier_score=0.0,
                expected_calibration_error=0.0,
                bands=[],
            )

        # Convert to records if dataclasses
        trade_dicts = []
        for t in trades:
            if hasattr(t, "__dict__"):
                trade_dicts.append(t.__dict__)
            elif isinstance(t, dict):
                trade_dicts.append(t)
            else:
                trade_dicts.append({
                    "confidence_score": getattr(t, "confidence_score", 0.0),
                    "net_pnl_pct": getattr(t, "net_pnl_pct", 0.0),
                    "gross_pnl_pct": getattr(t, "gross_pnl_pct", 0.0),
                    "status": getattr(t, "status", "UNKNOWN"),
                    "mae_pct": getattr(t, "mae_pct", 0.0),
                    "mfe_pct": getattr(t, "mfe_pct", 0.0),
                })

        df = pd.DataFrame(trade_dicts)
        if "confidence_score" not in df.columns:
            df["confidence_score"] = 0.0
        if "net_pnl_pct" not in df.columns:
            df["net_pnl_pct"] = 0.0

        total_n = len(df)
        band_metrics_list: List[ScoreBandMetrics] = []

        ece_numerator = 0.0
        brier_sum = 0.0

        for min_s, max_s, label in self.score_bins:
            # Inclusive on lower, exclusive on upper except top bin
            if max_s >= 100.0:
                mask = (df["confidence_score"] >= min_s) & (df["confidence_score"] <= max_s)
            else:
                mask = (df["confidence_score"] >= min_s) & (df["confidence_score"] < max_s)

            sub_df = df[mask]
            sub_n = len(sub_df)

            if sub_n == 0:
                band_metrics_list.append(
                    ScoreBandMetrics(
                        band_name=label,
                        min_score=min_s,
                        max_score=max_s,
                        sample_size=0,
                    )
                )
                continue

            wins_df = sub_df[sub_df["net_pnl_pct"] > 0]
            losses_df = sub_df[sub_df["net_pnl_pct"] < 0]
            be_df = sub_df[sub_df["net_pnl_pct"] == 0]

            n_wins = len(wins_df)
            n_losses = len(losses_df)
            n_be = len(be_df)

            win_rate = (n_wins / sub_n) * 100.0
            loss_rate = (n_losses / sub_n) * 100.0
            avg_score = float(sub_df["confidence_score"].mean())

            sum_gains = float(wins_df["net_pnl_pct"].sum())
            sum_losses = abs(float(losses_df["net_pnl_pct"].sum()))
            gross_pnl = float(sub_df.get("gross_pnl_pct", sub_df["net_pnl_pct"]).sum())
            net_pnl = float(sub_df["net_pnl_pct"].sum())

            avg_win = float(wins_df["net_pnl_pct"].mean()) if n_wins > 0 else 0.0
            avg_loss = abs(float(losses_df["net_pnl_pct"].mean())) if n_losses > 0 else 0.0
            realized_rr = (avg_win / avg_loss) if avg_loss > 0 else 0.0

            # Expected Value: EV = (WR * AvgWin) - (LR * AvgLoss)
            ev = ((win_rate / 100.0) * avg_win) - ((loss_rate / 100.0) * avg_loss)

            profit_factor = (sum_gains / sum_losses) if sum_losses > 0 else (99.0 if sum_gains > 0 else 0.0)

            # Sharpe Ratio (per-trade normalized)
            returns = sub_df["net_pnl_pct"].values / 100.0
            ret_mean = float(np.mean(returns))
            ret_std = float(np.std(returns, ddof=1)) if len(returns) > 1 else 0.0
            sharpe = (ret_mean / ret_std * np.sqrt(self.annualization_factor)) if ret_std > 1e-9 else 0.0

            # Max Drawdown for this band
            cum_curve = np.cumprod(1.0 + returns)
            peak = np.maximum.accumulate(cum_curve)
            dd = (peak - cum_curve) / peak * 100.0
            max_dd = float(np.max(dd)) if len(dd) > 0 else 0.0

            avg_mae = float(sub_df["mae_pct"].mean()) if "mae_pct" in sub_df.columns else 0.0
            avg_mfe = float(sub_df["mfe_pct"].mean()) if "mfe_pct" in sub_df.columns else 0.0

            calibrated_prob = n_wins / sub_n

            # Calibration metrics
            mean_predicted_prob = avg_score / 100.0
            ece_numerator += sub_n * abs(calibrated_prob - mean_predicted_prob)

            # Brier contribution
            for _, row in sub_df.iterrows():
                actual_y = 1.0 if row["net_pnl_pct"] > 0 else 0.0
                pred_y = float(row["confidence_score"]) / 100.0
                brier_sum += (pred_y - actual_y) ** 2

            band_metrics_list.append(
                ScoreBandMetrics(
                    band_name=label,
                    min_score=min_s,
                    max_score=max_s,
                    sample_size=sub_n,
                    wins=n_wins,
                    losses=n_losses,
                    breakevens=n_be,
                    win_rate_pct=win_rate,
                    loss_rate_pct=loss_rate,
                    avg_score=avg_score,
                    gross_pnl_pct=gross_pnl,
                    net_pnl_pct=net_pnl,
                    avg_win_pct=avg_win,
                    avg_loss_pct=avg_loss,
                    realized_rr=realized_rr,
                    expected_value_pct=ev,
                    profit_factor=profit_factor,
                    sharpe_ratio=sharpe,
                    max_drawdown_pct=max_dd,
                    avg_mae_pct=avg_mae,
                    avg_mfe_pct=avg_mfe,
                    calibrated_win_prob=calibrated_prob,
                )
            )

        # Aggregate overall metrics
        overall_wins = df[df["net_pnl_pct"] > 0]
        overall_losses = df[df["net_pnl_pct"] < 0]
        overall_wr = (len(overall_wins) / total_n) * 100.0 if total_n > 0 else 0.0
        overall_gains = float(overall_wins["net_pnl_pct"].sum())
        overall_loss_sum = abs(float(overall_losses["net_pnl_pct"].sum()))
        overall_pf = (overall_gains / overall_loss_sum) if overall_loss_sum > 0 else (99.0 if overall_gains > 0 else 0.0)

        all_rets = df["net_pnl_pct"].values / 100.0
        all_mean = float(np.mean(all_rets))
        all_std = float(np.std(all_rets, ddof=1)) if len(all_rets) > 1 else 0.0
        overall_sharpe = (all_mean / all_std * np.sqrt(self.annualization_factor)) if all_std > 1e-9 else 0.0

        all_curve = np.cumprod(1.0 + all_rets)
        all_peak = np.maximum.accumulate(all_curve)
        all_dd = (all_peak - all_curve) / all_peak * 100.0
        overall_max_dd = float(np.max(all_dd)) if len(all_dd) > 0 else 0.0

        ece = (ece_numerator / total_n) if total_n > 0 else 0.0
        brier = (brier_sum / total_n) if total_n > 0 else 0.0

        return CalibrationSummary(
            total_trades=total_n,
            overall_win_rate_pct=overall_wr,
            overall_net_pnl_pct=float(df["net_pnl_pct"].sum()),
            overall_profit_factor=overall_pf,
            overall_sharpe_ratio=overall_sharpe,
            overall_max_drawdown_pct=overall_max_dd,
            brier_score=brier,
            expected_calibration_error=ece,
            bands=band_metrics_list,
        )


def split_walk_forward(
    df: pd.DataFrame,
    in_sample_pct: float = 0.70,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Chronologically splits historical data into In-Sample (calibration) and Out-of-Sample (validation).
    Preserves strict chronological ordering without shuffling or lookahead bias.
    """
    if df.empty:
        return df, df

    split_idx = int(len(df) * in_sample_pct)
    in_sample = df.iloc[:split_idx].copy().reset_index(drop=True)
    out_of_sample = df.iloc[split_idx:].copy().reset_index(drop=True)

    return in_sample, out_of_sample

