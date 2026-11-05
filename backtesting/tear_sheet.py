"""
backtesting/tear_sheet.py - Institutional Console Tear Sheet & Reliability Diagram

Generates an institutional-grade performance and calibration report for the terminal,
including ASCII reliability diagrams and score-band calibration matrices.
"""

from typing import Any, Dict, List, Optional
from backtesting.calibration import CalibrationSummary, ScoreBandMetrics


class ConsoleTearSheet:
    """
    Renders institutional performance reports and reliability diagrams directly to the console.
    """

    def __init__(self, summary: CalibrationSummary, metadata: Optional[Dict[str, Any]] = None) -> None:
        self.summary = summary
        self.metadata = metadata or {}

    def render(self) -> str:
        """Constructs and returns the full tear sheet string."""
        lines: List[str] = []
        width = 100
        sep_double = "=" * width
        sep_single = "-" * width

        # Header
        lines.append(sep_double)
        lines.append("                  CRYPTO SENTINEL: QUANTITATIVE CALIBRATION TEAR SHEET")
        lines.append(sep_double)

        symbol = self.metadata.get("symbol", "N/A")
        timeframe = self.metadata.get("timeframe", "N/A")
        period = self.metadata.get("period", "Historical Dataset")
        fee = self.metadata.get("taker_fee_pct", 0.05)
        slip = self.metadata.get("slippage_pct", 0.03)

        lines.append(f" Asset: {symbol:<15} Timeframe: {timeframe:<10} Period: {period}")
        lines.append(f" Fee Drag: {fee:.2f}% taker/side | Slippage: {slip:.2f}% | Sample Size (N): {self.summary.total_trades}")
        lines.append(sep_single)

        # Global Portfolio Summary
        lines.append(" [1] GLOBAL SYSTEM PERFORMANCE")
        lines.append(f"   * Cumulative Net Return:   {self.summary.overall_net_pnl_pct:+8.2f}%")
        lines.append(f"   * Global Win Rate:         {self.summary.overall_win_rate_pct:8.2f}%")
        lines.append(f"   * Overall Profit Factor:   {self.summary.overall_profit_factor:8.2f}")
        lines.append(f"   * Annualized Sharpe Ratio: {self.summary.overall_sharpe_ratio:8.2f}")
        lines.append(f"   * Peak-to-Trough Drawdown: {self.summary.overall_max_drawdown_pct:8.2f}%")
        lines.append(f"   * Expected Calibration Err:{self.summary.expected_calibration_error * 100:8.2f}%")
        lines.append(f"   * Brier Calibration Score: {self.summary.brier_score:8.4f} (lower is better, 0.0 = perfect)")
        lines.append(sep_single)

        # Score Band Calibration Matrix
        lines.append(" [2] EMPIRICAL SCORE-BAND CALIBRATION MATRIX")
        header_row = (
            f" {'Score Band':<13} | {'N':>4} | {'Nominal':>7} | {'Calib WR':>8} | "
            f"{'W / L':>7} | {'Avg Win':>8} | {'Avg Loss':>8} | {'R:R':>5} | {'EV %':>7} | {'PF':>5} | {'Sharpe':>6}"
        )
        lines.append(header_row)
        lines.append("-" * len(header_row))

        for b in self.summary.bands:
            nom_score_str = f"{b.avg_score:.1f}%" if b.sample_size > 0 else "-"
            calib_wr_str = f"{b.win_rate_pct:.1f}%" if b.sample_size > 0 else "-"
            wl_str = f"{b.wins}/{b.losses}" if b.sample_size > 0 else "-/-"
            avg_w_str = f"{b.avg_win_pct:+.2f}%" if b.wins > 0 else "-"
            avg_l_str = f"-{b.avg_loss_pct:.2f}%" if b.losses > 0 else "-"
            rr_str = f"{b.realized_rr:.2f}" if b.sample_size > 0 else "-"
            ev_str = f"{b.expected_value_pct:+.2f}%" if b.sample_size > 0 else "-"
            pf_str = f"{b.profit_factor:.2f}" if b.sample_size > 0 else "-"
            sharpe_str = f"{b.sharpe_ratio:.2f}" if b.sample_size > 0 else "-"

            row = (
                f" {b.band_name:<13} | {b.sample_size:>4} | {nom_score_str:>7} | {calib_wr_str:>8} | "
                f"{wl_str:>7} | {avg_w_str:>8} | {avg_l_str:>8} | {rr_str:>5} | {ev_str:>7} | {pf_str:>5} | {sharpe_str:>6}"
            )
            lines.append(row)

        lines.append(sep_single)

        # Visual Reliability Diagram (ASCII Calibration Curve)
        lines.append(" [3] RELIABILITY DIAGRAM (Nominal Model Score vs Empirical Win Rate)")
        lines.append("     Band           Nominal  Empirical  Reliability Bar [■ = Win Rate, | = Nominal]")
        for b in self.summary.bands:
            if b.sample_size == 0:
                continue
            nom_val = b.avg_score
            emp_val = b.win_rate_pct
            bar_len = 30
            emp_blocks = int(round((emp_val / 100.0) * bar_len))
            nom_pos = min(bar_len - 1, max(0, int(round((nom_val / 100.0) * bar_len))))

            bar_chars = [" "] * bar_len
            for idx in range(min(emp_blocks, bar_len)):
                bar_chars[idx] = "■"
            if nom_pos < bar_len:
                if bar_chars[nom_pos] == "■":
                    bar_chars[nom_pos] = "█"
                else:
                    bar_chars[nom_pos] = "|"

            bar_str = "".join(bar_chars)
            lines.append(f"     {b.band_name:<14}  {nom_val:5.1f}%   {emp_val:5.1f}%    [{bar_str}]")

        lines.append(sep_single)

        # Quantitative Diagnostics & Recommendations
        lines.append(" [4] QUANTITATIVE DIAGNOSTICS & RECOMMENDATIONS")
        # Identify bands with positive EV
        profitable_bands = [b for b in self.summary.bands if b.sample_size >= 3 and b.expected_value_pct > 0 and b.profit_factor > 1.0]
        if profitable_bands:
            recommended_floor = profitable_bands[0].min_score
            lines.append(f"   * Recommended Production Dispatch Floor: Score >= {recommended_floor:.1f}%")
            lines.append(f"     (First score band achieving positive Expected Value and Profit Factor > 1.0)")
        else:
            lines.append("   * Warning: No score band met institutional criteria (EV > 0, PF > 1.0, N >= 3).")

        # Monotonicity check
        valid_bands = [b for b in self.summary.bands if b.sample_size >= 3]
        if len(valid_bands) >= 2:
            is_monotonic = all(valid_bands[i].win_rate_pct <= valid_bands[i+1].win_rate_pct for i in range(len(valid_bands)-1))
            if is_monotonic:
                lines.append("   * Edge Consistency: MONOTONIC (Higher confidence scores strictly yield higher win rates).")
            else:
                lines.append("   * Edge Consistency: NON-MONOTONIC (Calibration curvature detected; tune weightings in engine.py).")

        lines.append(sep_double)
        return "\n".join(lines)

    def print(self) -> None:
        """Prints the formatted tear sheet directly to stdout."""
        print("\n" + self.render() + "\n")

