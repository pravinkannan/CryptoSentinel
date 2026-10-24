"""
Confluence & Deduction Telemetry Auditor
=========================================
Audits the health, activation rates, and statistical distribution of all 
institutional confluence scoring components and friction deductions.
Identifies 'dead confluences' (0% fire rate) and over-restrictive filters.
"""

import sqlite3
import json
import logging
from typing import Dict, List, Any, Optional
import pandas as pd

logger = logging.getLogger(__name__)

CONFLUENCE_SPECS = [
    ("Dynamic Volume Surge", "vol_points", 8.0),
    ("Key Level Precision", "level_points", 6.0),
    ("Session VWAP Acceptance", "vwap_points", 4.0),
    ("Macro Trend Alignment", "macro_trend", 3.0),
    ("BTC Leadership / Beta", "btc_trend", 2.5),
    ("Funding Rate Tailwind", "funding_tailwind", 1.0),
    ("Order Book Imbalance", "ob_imbalance", 1.5),
    ("Order Book Defensive Shield", "ob_shield", 3.0),
    ("RSI Divergence", "rsi_div", 5.0),
    ("Target Magnet Pull", "target_magnet", 2.0),
    ("Open Interest Expansion", "oi_expansion", 1.5),
    ("RSI Momentum Room", "rsi_room", 1.0),
    ("Volatility Squeeze", "squeeze", 1.0),
]

FRICTION_SPECS = [
    ("Blocking OB Wall", "blocking_wall", -3.0),
    ("Sweep Risk Friction", "sweep_risk", -3.0),
    ("Fighting BTC Macro", "btc_conflict", -3.5),
    ("Low Volume Drag", "vol_drag", -2.0),
    ("Fighting Session VWAP", "vwap_conflict", -3.0),
    ("Overcrowded Funding Trap", "funding_trap", -2.5),
    ("Phase 3 Exhaustion Trap", "phase3_trap", -2.0),
    ("Exhaustion Absorption Trap", "exhaustion_trap", -5.0),
    ("Smart Money Absorption", "absorbed", 0.5),
]


class ConfluenceAuditor:
    """Audits confluence and friction telemetry across historical or live evaluations."""

    def __init__(self, db_path: str = "data/sentinel.db"):
        self.db_path = db_path

    def audit_database(self, limit: int = 500, since_timestamp: Optional[str] = None) -> Dict[str, Any]:
        """Audit the most recent evaluations and rejected signals from SQLite database."""
        records: List[Dict[str, Any]] = []
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            # Read rejected signals
            if since_timestamp:
                cur.execute("""
                    SELECT eval_trace_json, confidence_score, symbol, timeframe, direction 
                    FROM rejected_signals 
                    WHERE timestamp >= ?
                    ORDER BY id DESC 
                    LIMIT ?
                """, (since_timestamp, limit))
            else:
                cur.execute("""
                    SELECT eval_trace_json, confidence_score, symbol, timeframe, direction 
                    FROM rejected_signals 
                    ORDER BY id DESC 
                    LIMIT ?
                """, (limit,))

            for row in cur.fetchall():
                try:
                    trace = json.loads(row['eval_trace_json'])
                    records.append(trace)
                except Exception:
                    pass

            # Read approved evaluations
            if since_timestamp:
                cur.execute("""
                    SELECT e.signal_payload, e.confidence_score, e.confluences_applied 
                    FROM evaluations e
                    JOIN trades t ON e.trade_id = t.trade_id
                    WHERE t.timestamp >= ?
                    ORDER BY e.id DESC 
                    LIMIT ?
                """, (since_timestamp, limit))
            else:
                cur.execute("""
                    SELECT signal_payload, confidence_score, confluences_applied 
                    FROM evaluations 
                    ORDER BY id DESC 
                    LIMIT ?
                """, (limit,))
            for row in cur.fetchall():
                try:
                    payload = json.loads(row['signal_payload']) if row['signal_payload'] else {}
                    if 'eval_trace' in payload:
                        records.append(payload['eval_trace'])
                    elif row['confluences_applied']:
                        parsed = self._parse_confluences_checklist(row['confluences_applied'], float(row['confidence_score'] or 0.0))
                        if parsed:
                            records.append(parsed)
                except Exception:
                    pass
            conn.close()
        except Exception as e:
            logger.warning(f"Could not read from database: {e}")

        return self.audit_traces(records)

    def _parse_confluences_checklist(self, checklist_raw: Any, confidence: float) -> Optional[Dict[str, Any]]:
        """Parse text checklist items from confluences_applied into an evaluation trace."""
        try:
            items = json.loads(checklist_raw) if isinstance(checklist_raw, str) else checklist_raw
            if not items or not isinstance(items, list):
                return None
            bd: Dict[str, Any] = {}
            vol_pts = 0.0
            lvl_pts = 0.0
            vwap_pts = 0.0
            cat_pts = 0.0
            
            for item in items:
                s = str(item)
                if "Dynamic Volume Surge" in s:
                    bd["volume_ratio"] = 1.5
                    vol_pts = 4.0
                if "Key Level Precision" in s:
                    bd["key_level_rejection"] = True
                    lvl_pts = 6.0
                if "Session VWAP Acceptance" in s:
                    bd["vwap_aligned"] = True
                    vwap_pts = 4.0
                if "Multi-Timeframe Macro Trend Alignment" in s:
                    bd["macro_trend_aligned"] = True
                if "BTC Macro" in s:
                    bd["btc_aligned"] = True
                if "Funding Rate Tailwind" in s:
                    bd["funding_rate_tailwind"] = True
                if "Order Book Imbalance" in s:
                    bd["imbalance_ratio"] = 1.5
                if "Order Book Defensive Shield" in s:
                    bd["ob_defensive_shield"] = True
                if "RSI Divergence" in s:
                    bd["rsi_divergence"] = True
                    cat_pts += 5.0
                if "Target Liquidity Magnet" in s:
                    bd["target_magnet"] = True
                    cat_pts += 2.0
                if "Open Interest Expansion" in s:
                    bd["oi_delta_15m"] = 2.0
                    cat_pts += 1.5
                if "RSI Momentum Trajectory Room" in s:
                    bd["rsi_momentum_room"] = True
                    cat_pts += 1.0
                if "Volatility Squeeze Expansion" in s:
                    bd["is_squeezed"] = True
                    cat_pts += 1.0

            return {
                "vol_points": vol_pts,
                "level_points": lvl_pts,
                "vwap_points": vwap_pts,
                "catalyst_points": cat_pts,
                "breakdown": bd
            }
        except Exception:
            return None

    def audit_traces(self, traces: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Analyze a list of evaluation trace dictionaries for confluence health."""
        total = len(traces)
        if total == 0:
            return {
                "total_audited": 0,
                "health_status": "NO_DATA",
                "confluences": {},
                "deductions": {},
                "dead_confluences": [],
                "warnings": ["No evaluation traces available to audit."]
            }

        # Extract breakdown stats
        confluence_stats: Dict[str, Dict[str, Any]] = {}
        dead_confluences: List[str] = []
        warnings: List[str] = []

        # Count active confluences
        for name, key, max_pts in CONFLUENCE_SPECS:
            fired_count = 0
            points_list = []
            for t in traces:
                bd = t.get('breakdown', {})
                pts = 0.0
                if key == 'vol_points':
                    pts = float(t.get('vol_points', 0.0))
                elif key == 'level_points':
                    pts = float(t.get('level_points', 0.0))
                elif key == 'vwap_points':
                    pts = float(t.get('vwap_points', 0.0))
                elif key == 'macro_trend':
                    pts = 3.0 if bd.get('macro_trend_aligned') else 0.0
                elif key == 'btc_trend':
                    pts = 2.5 if bd.get('btc_aligned') else 0.0
                elif key == 'funding_tailwind':
                    pts = 1.0 if bd.get('funding_rate_tailwind') else 0.0
                elif key == 'ob_imbalance':
                    imbalance = float(bd.get('imbalance_ratio', 1.0))
                    pts = max(0.0, min(1.5, (imbalance - 1.0) * 1.5)) if imbalance > 1.0 else 0.0
                elif key == 'ob_shield':
                    pts = 3.0 if bd.get('ob_defensive_shield') else 0.0
                elif key == 'rsi_div':
                    pts = 5.0 if bd.get('rsi_divergence') else 0.0
                elif key == 'target_magnet':
                    pts = 2.0 if bd.get('target_magnet') else 0.0
                elif key == 'oi_expansion':
                    oi_d = float(bd.get('oi_delta_15m', 0.0))
                    pts = 1.5 if oi_d > 1.5 else 0.0
                elif key == 'rsi_room':
                    pts = 1.0 if bd.get('rsi_momentum_room') else 0.0
                elif key == 'squeeze':
                    pts = 1.0 if t.get('catalyst_points', 0.0) >= 1.0 and bd.get('is_squeezed') else 0.0

                points_list.append(pts)
                if pts > 0.0:
                    fired_count += 1

            fire_rate = round((fired_count / total) * 100, 1)
            avg_pts = round(sum(points_list) / max(1, total), 2)
            max_achieved = round(max(points_list) if points_list else 0.0, 2)

            confluence_stats[name] = {
                "max_possible": max_pts,
                "fire_rate_pct": fire_rate,
                "avg_points": avg_pts,
                "max_achieved": max_achieved,
                "is_active": fire_rate > 0.0
            }

            if fire_rate == 0.0:
                dead_confluences.append(name)
                warnings.append(f"DEAD CONFLUENCE: '{name}' never fired (0.0% activation rate across {total} evaluations).")

        # Count deductions
        friction_stats: Dict[str, Dict[str, Any]] = {}
        for name, key, penalty in FRICTION_SPECS:
            fired_count = 0
            for t in traces:
                bd = t.get('breakdown', {})
                active = False
                if key == 'blocking_wall':
                    active = bool(bd.get('blocking_ob_wall'))
                elif key == 'sweep_risk':
                    active = bool(bd.get('has_sweep_risk'))
                elif key == 'btc_conflict':
                    active = not bd.get('btc_aligned') and not bd.get('is_btc', False)
                elif key == 'vol_drag':
                    active = float(bd.get('volume_ratio', 1.0)) < 0.80
                elif key == 'vwap_conflict':
                    active = not bool(bd.get('vwap_aligned'))
                elif key == 'funding_trap':
                    active = bool(bd.get('overcrowded_funding', False))
                elif key == 'phase3_trap':
                    active = bd.get('phase') in ['PHASE 3', 'PHASE_3']
                elif key == 'exhaustion_trap':
                    active = bool(bd.get('exhaustion_trap'))
                elif key == 'absorbed':
                    active = bool(bd.get('friction_absorbed'))

                if active:
                    fired_count += 1

            pen_rate = round((fired_count / total) * 100, 1)
            friction_stats[name] = {
                "penalty": penalty,
                "penalty_rate_pct": pen_rate,
                "count": fired_count
            }

            if pen_rate > 50.0 and key != 'vwap_conflict':
                warnings.append(f"OVER-RESTRICTION WARNING: Deduction '{name}' fired on {pen_rate}% of all evaluations.")

        health_status = "HEALTHY" if not dead_confluences else "DEFECTS_DETECTED"

        return {
            "total_audited": total,
            "health_status": health_status,
            "confluences": confluence_stats,
            "deductions": friction_stats,
            "dead_confluences": dead_confluences,
            "warnings": warnings
        }

    def generate_markdown_report(self, audit_result: Dict[str, Any]) -> str:
        """Format the audit result into a clean GitHub markdown report."""
        lines = [
            f"# Confluence & Deduction Telemetry Audit Report",
            f"**Total Evaluations Audited**: {audit_result['total_audited']} | **Health Status**: `{audit_result['health_status']}`",
            "",
            "## 1. Confluence Activation Distribution",
            "| Confluence Component | Max Pts | Fire Rate | Avg Points | Max Earned | Status |",
            "| :--- | :---: | :---: | :---: | :---: | :---: |"
        ]

        for name, data in audit_result.get('confluences', {}).items():
            status_str = "[ACTIVE]" if data['is_active'] else "**[DEAD]**"
            lines.append(
                f"| {name} | +{data['max_possible']:.1f}% | {data['fire_rate_pct']:.1f}% | +{data['avg_points']:.2f}% | +{data['max_achieved']:.1f}% | {status_str} |"
            )

        lines.extend([
            "",
            "## 2. Friction & Deduction Breakdown",
            "| Deduction Rule | Penalty | Frequency | Impact Count |",
            "| :--- | :---: | :---: | :---: |"
        ])

        for name, data in audit_result.get('deductions', {}).items():
            lines.append(
                f"| {name} | {data['penalty']:+.1f}% | {data['penalty_rate_pct']:.1f}% | {data['count']} |"
            )

        if audit_result.get('warnings'):
            lines.extend([
                "",
                "## 3. Telemetry Warnings & Diagnostic Action Items"
            ])
            for w in audit_result['warnings']:
                lines.append(f"- [WARNING] {w}")
        else:
            lines.extend([
                "",
                "## 3. Health Diagnosis",
                "[OK] All institutional confluence scoring channels are operational with zero dead features."
            ])

        return "\n".join(lines)
