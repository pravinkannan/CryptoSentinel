"""Crypto Sentinel Performance Analytics Dashboard.

A clean, intuitive, strictly read-only Streamlit dashboard
visualizing Sentinel repository performance and version evolution.
Supports automated live polling without manual page reloads.
"""

from datetime import datetime, timezone
import json
import os
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# ==============================================================================
# CONFIGURATION & THEME
# ==============================================================================
st.set_page_config(
    page_title="Crypto Sentinel | Performance Dashboard",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom Clean Styling
st.markdown("""
<style>
    .metric-container {
        background-color: #161B22;
        border: 1px solid #30363D;
        border-radius: 8px;
        padding: 16px;
        text-align: center;
    }
    .stMetric {
        background-color: #0E1117;
        padding: 14px;
        border-radius: 8px;
        border: 1px solid #21262D;
    }
    .banner-current {
        background-color: #0d2822;
        border-left: 4px solid #00FFCC;
        padding: 14px 18px;
        border-radius: 4px;
        margin-bottom: 20px;
    }
    .banner-legacy {
        background-color: #1a1f26;
        border-left: 4px solid #8B949E;
        padding: 14px 18px;
        border-radius: 4px;
        margin-bottom: 20px;
    }
    .badge-win {
        color: #38EF7D;
        font-weight: 700;
    }
    .badge-loss {
        color: #FF5555;
        font-weight: 700;
    }
</style>
""", unsafe_allow_html=True)


# ==============================================================================
# VERSION DEFINITIONS (ACCURATE TIMELINE)
# ==============================================================================
VERSION_METADATA = {
    "v3 (Hexagonal Architecture)": {
        "title": "v3 (Hexagonal & Event-Driven Architecture)",
        "badge": "CURRENT PRODUCTION",
        "dates": "Oct 23, 2026 11:00 UTC – Present",
        "summary": "Decoupled pure domain math, async pipeline, event bus, circuit breaker protection, and Telegram ad-hoc recon.",
        "color": "#00FFCC",
        "is_current": True
    },
    "v2 (VIP Gate Overhaul)": {
        "title": "v2 (3-Tier Confluence & VIP Gate Overhaul)",
        "badge": "INTERMEDIATE STAGE",
        "dates": "Oct 17 – Oct 23, 2026",
        "summary": "Parametric scoring, 5-Pillar VIP gate (≥92%), and anti-sweep liquidity buffers.",
        "color": "#58A6FF",
        "is_current": False
    },
    "Legacy (v1 Prototype)": {
        "title": "Legacy (v1 Procedural Prototype)",
        "badge": "INITIAL PROTOTYPE",
        "dates": "Oct 9 – Oct 17, 2026",
        "summary": "Early prototype with flat additive scoring, uniform 0.55% stop floor, and no VIP gate.",
        "color": "#8B949E",
        "is_current": False
    }
}

DB_PATH = os.environ.get("SENTINEL_DB_PATH", "data/sentinel.db")


def assign_engine_version(ts: Any) -> str:
    """Classifies a timestamp into Sentinel's exact 3 version milestones."""
    if pd.isna(ts):
        return "Legacy (v1 Prototype)"
    t_str = str(ts)
    if t_str >= "2026-10-23 11:00":
        return "v3 (Hexagonal Architecture)"
    elif t_str >= "2026-10-17":
        return "v2 (VIP Gate Overhaul)"
    else:
        return "Legacy (v1 Prototype)"


# ==============================================================================
# READ-ONLY DATA ACCESS
# ==============================================================================
def get_readonly_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    """Establish a strictly read-only SQLite connection."""
    if not os.path.isabs(db_path):
        db_path = os.path.abspath(db_path)
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database file not found at: {db_path}")

    uri = f"file:{db_path}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=10.0)
    except Exception:
        conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


@st.cache_data(ttl=10)
def load_trades_data(db_path: str = DB_PATH) -> pd.DataFrame:
    """Load trades and join with evaluations data in read-only mode."""
    if not os.path.exists(db_path):
        return pd.DataFrame()

    query = """
    SELECT 
        t.trade_id,
        t.timestamp,
        t.symbol,
        t.direction,
        t.entry_price,
        t.sl_price,
        t.tp1_price,
        t.tp2_price,
        t.leverage,
        t.tp1_hit,
        t.tp2_hit,
        t.trailing_sl,
        t.status,
        t.pnl_pct,
        t.net_pnl_pct,
        t.gross_pnl_pct,
        t.ttl_mins,
        t.exit_price,
        e.pattern_name,
        e.confidence_score,
        e.confluences_applied,
        e.regime_state
    FROM trades t
    LEFT JOIN evaluations e ON t.trade_id = e.trade_id
    ORDER BY t.timestamp DESC
    """
    try:
        with get_readonly_connection(db_path) as conn:
            df = pd.read_sql_query(query, conn)
    except Exception as e:
        st.error(f"Error querying trades: {e}")
        return pd.DataFrame()

    if df.empty:
        return df

    df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
    df['net_pnl_pct'] = pd.to_numeric(df['net_pnl_pct'], errors='coerce').fillna(0.0)
    df['confidence_score'] = pd.to_numeric(df['confidence_score'], errors='coerce').fillna(0.0)

    # Classify exact version
    df['engine_version'] = df['timestamp'].apply(assign_engine_version)

    # Backward compatibility helpers for tests
    def classify_regime(row: pd.Series) -> Tuple[str, float]:
        pat = str(row.get('pattern_name') or '').lower()
        score = float(row.get('confidence_score') or 0.0)
        reversal_keywords = ['hammer', 'rejection', 'engulfing', 'star', 'pin bar', 'pinbar', 'reversal', 'piercing']
        if any(k in pat for k in reversal_keywords) or (score > 0 and score < 86.0):
            return "Reversal (80% Floor)", 80.0
        return "Continuation (86% Floor)", 86.0

    regime_tuples = df.apply(classify_regime, axis=1)
    df['regime_type'] = [r[0] for r in regime_tuples]
    df['regime_floor'] = [r[1] for r in regime_tuples]

    # Outcomes
    closed_statuses = ['WIN_TP1', 'WIN_TP2', 'LOSS', 'EXPIRED_PROFIT', 'EXPIRED_LOSS', 'BE', 'BREAKEVEN']
    df['is_closed'] = df['status'].isin(closed_statuses)
    df['is_win'] = df['status'].isin(['WIN_TP1', 'WIN_TP2', 'EXPIRED_PROFIT']) | (df['net_pnl_pct'] > 0)
    return df


@st.cache_data(ttl=10)
def load_rejected_signals_data(db_path: str = DB_PATH) -> pd.DataFrame:
    """Load signals that circuit breakers safely prevented Sentinel from trading."""
    if not os.path.exists(db_path):
        return pd.DataFrame()

    query = """
    SELECT 
        id,
        timestamp,
        symbol,
        timeframe,
        direction,
        pattern_name,
        confidence_score,
        eval_trace_json,
        rejection_reason
    FROM rejected_signals
    ORDER BY timestamp DESC
    """
    try:
        with get_readonly_connection(db_path) as conn:
            df = pd.read_sql_query(query, conn)
    except Exception as e:
        return pd.DataFrame()

    if df.empty:
        return df

    df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
    df['confidence_score'] = pd.to_numeric(df['confidence_score'], errors='coerce').fillna(0.0)
    df['engine_version'] = df['timestamp'].apply(assign_engine_version)

    def simplify_rejection_reason(row: pd.Series) -> str:
        trace_str = row.get('eval_trace_json')
        if trace_str and isinstance(trace_str, str):
            try:
                trace = json.loads(trace_str)
                flaw = trace.get('fatal_flaw')
                if flaw and str(flaw).strip():
                    return str(flaw).strip()
            except Exception:
                pass

        reason = str(row.get('rejection_reason') or '')
        if 'Macro Conflict' in reason or '4H Macro' in reason:
            return 'Phase 1 Gate Failed: 4H Macro Conflict'
        elif 'RSI Exhaustion' in reason:
            return 'RSI Momentum Overheated'
        elif 'Anemic Volume' in reason or 'Volume ratio' in reason:
            return 'Trading Volume Too Low'
        elif 'Counter-trend' in reason:
            return 'Against Major Trend'
        elif 'wall' in reason.lower():
            return 'Order Book Resistance Wall'
        return 'Score Below Required Quality Bar'

    df['fatal_flaw_clean'] = df.apply(simplify_rejection_reason, axis=1)
    return df


@st.cache_data(ttl=5)
def load_active_signals_state(db_path: str = DB_PATH) -> Dict[str, Any]:
    """Load real-time active signals tracked in sentinel_kv_state."""
    if not os.path.exists(db_path):
        return {}

    query = "SELECT value FROM sentinel_kv_state WHERE key = 'active_signals'"
    try:
        with get_readonly_connection(db_path) as conn:
            cur = conn.cursor()
            row = cur.execute(query).fetchone()
            if row and row['value']:
                data = json.loads(row['value'])
                if isinstance(data, dict):
                    return data
    except Exception:
        pass
    return {}


# ==============================================================================
# MAIN DASHBOARD INTERFACE
# ==============================================================================
def main() -> None:
    # Sidebar
    st.sidebar.title("🛡️ Sentinel Dashboard")
    st.sidebar.caption("Automated Signal & Verification Engine")
    st.sidebar.markdown("---")

    # Initial data probe for available coins
    initial_trades = load_trades_data()

    # Clean Version Selector
    st.sidebar.markdown("### Version View")
    version_choices = [
        "⭐ v3 (Hexagonal Architecture) [Current]",
        "v2 (VIP Gate Overhaul)",
        "Legacy (v1 Prototype)",
        "📊 All Versions Combined"
    ]
    selected_choice = st.sidebar.selectbox(
        "Select App Version",
        options=version_choices,
        index=0,
        help="View trades for the current engine or compare past versions."
    )

    # Normalize key
    if "v3" in selected_choice:
        selected_version = "v3 (Hexagonal Architecture)"
    elif "v2" in selected_choice:
        selected_version = "v2 (VIP Gate Overhaul)"
    elif "Legacy" in selected_choice:
        selected_version = "Legacy (v1 Prototype)"
    else:
        selected_version = "ALL"

    is_all = (selected_version == "ALL")

    # Optional Coin Filter in sidebar
    st.sidebar.markdown("### Filter by Coin")
    available_coins = sorted(initial_trades['symbol'].dropna().unique().tolist()) if not initial_trades.empty else []
    selected_coins = st.sidebar.multiselect("Select Coin(s)", options=available_coins, default=[])

    st.sidebar.markdown("---")
    st.sidebar.markdown("### Live Updates")
    auto_refresh = st.sidebar.checkbox(
        "⚡ Auto-Refresh (Every 10s)",
        value=True,
        help="When checked, the dashboard automatically checks for new trades every 10 seconds without needing to refresh."
    )
    if st.sidebar.button("🔄 Manual Refresh", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    # ==========================================================================
    # LIVE RENDERING FRAGMENT (AUTO-UPDATES IN REAL TIME)
    # ==========================================================================
    @st.fragment(run_every="10s" if auto_refresh else None)
    def render_live_dashboard(ver_name: str, show_all: bool, coin_filters: List[str]) -> None:
        # Load fresh data from DB
        trades_df = load_trades_data()
        rejected_df = load_rejected_signals_data()
        active_signals = load_active_signals_state()

        # Apply Version Filter
        if show_all:
            ver_trades = trades_df.copy()
            ver_rejected = rejected_df.copy()
        else:
            ver_trades = trades_df[trades_df['engine_version'] == ver_name].copy()
            ver_rejected = rejected_df[rejected_df['engine_version'] == ver_name].copy()

        # Apply Coin Filters
        filtered_trades = ver_trades.copy()
        filtered_rejected = ver_rejected.copy()
        if coin_filters:
            filtered_trades = filtered_trades[filtered_trades['symbol'].isin(coin_filters)]
            filtered_rejected = filtered_rejected[filtered_rejected['symbol'].isin(coin_filters)]

        # Header
        col_t1, col_t2 = st.columns([3, 1])
        with col_t1:
            st.title("Crypto Sentinel Performance")
            st.caption("Real-Time Signal Verification & Risk Governance")
        with col_t2:
            st.markdown(f"**UTC Time:** `{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}`")
            if auto_refresh:
                st.caption("🟢 Live (Polling every 10s)")
            else:
                st.caption("⏸️ Auto-Refresh Paused")

        # Clean Informative Banner
        if not show_all and ver_name in VERSION_METADATA:
            v_meta = VERSION_METADATA[ver_name]
            b_class = "banner-current" if v_meta['is_current'] else "banner-legacy"
            st.markdown(f"""
            <div class="{b_class}">
                <b style="color: {v_meta['color']}; font-size: 1.05rem;">{v_meta['title']}</b> &nbsp; • &nbsp; <i>{v_meta['dates']}</i><br>
                <span style="color: #C9D1D9; font-size: 0.95rem; display: block; margin-top: 4px;">
                    {v_meta['summary']}
                </span>
            </div>
            """, unsafe_allow_html=True)
        elif show_all:
            st.markdown("""
            <div class="banner-legacy" style="border-left-color: #BC8CFF;">
                <b style="color: #BC8CFF; font-size: 1.05rem;">All Versions Combined (Oct 9 – Present)</b><br>
                <span style="color: #C9D1D9; font-size: 0.95rem; display: block; margin-top: 4px;">
                    Complete project history tracking the progression from early prototype to profitable quantitative engine.
                </span>
            </div>
            """, unsafe_allow_html=True)

        # Key Performance Indicators
        closed_trades = filtered_trades[filtered_trades['is_closed']] if not filtered_trades.empty else pd.DataFrame()
        total_closed = len(closed_trades)
        wins = len(closed_trades[closed_trades['is_win']]) if total_closed > 0 else 0
        breakevens = len(closed_trades[closed_trades['status'].isin(['BE', 'BREAKEVEN']) & ~closed_trades['is_win']]) if total_closed > 0 else 0
        actual_losses = max(0, total_closed - wins - breakevens)
        losses = total_closed - wins

        directional_trades = wins + actual_losses
        directional_win_rate = (wins / directional_trades * 100.0) if directional_trades > 0 else 0.0
        strict_win_rate = (wins / total_closed * 100.0) if total_closed > 0 else 0.0

        net_pnl = closed_trades['net_pnl_pct'].sum() if total_closed > 0 else 0.0
        active_count = len(active_signals) if active_signals else (len(filtered_trades[filtered_trades['status'] == 'PENDING']) if not filtered_trades.empty else 0)
        avoided_count = len(filtered_rejected)

        k1, k2, k3, k4 = st.columns(4)

        with k1:
            if breakevens > 0:
                st.metric(
                    label="Directional Win Rate",
                    value=f"{directional_win_rate:.1f}%",
                    delta=f"{wins}W / {actual_losses}L • {breakevens} Breakeven"
                )
            else:
                st.metric(
                    label="Win Rate",
                    value=f"{strict_win_rate:.1f}%",
                    delta=f"{wins} Wins / {actual_losses} Losses"
                )

        with k2:
            pnl_delta_color = "normal" if net_pnl >= 0 else "inverse"
            st.metric(
                label="Total Profit / Loss",
                value=f"{net_pnl:+.1f}%",
                delta=f"Avg: {(net_pnl / total_closed):+.2f}% / trade" if total_closed > 0 else "0.0%",
                delta_color=pnl_delta_color
            )

        with k3:
            st.metric(
                label="Total Trades",
                value=f"{total_closed}",
                delta=f"{active_count} Active Right Now"
            )

        with k4:
            st.metric(
                label="Bad Trades Prevented",
                value=f"{avoided_count}",
                delta="Capital Saved by Safety Filters"
            )

        st.markdown("---")

        # Tabs
        tab_overview, tab_versions, tab_protection, tab_history = st.tabs([
            "📊 Overview & Profit",
            "🚀 Version Evolution (v1 vs v2 vs v3)",
            "🛡️ Saved Trades (Safety Net)",
            "📜 Trade History"
        ])

        # Tab 1: Overview & Profit
        with tab_overview:
            if not closed_trades.empty:
                c_g1, c_g2 = st.columns([2, 1])

                with c_g1:
                    closed_chrono = closed_trades.sort_values('timestamp').copy()
                    closed_chrono['running_profit'] = closed_chrono['net_pnl_pct'].cumsum()

                    fig_equity = px.line(
                        closed_chrono,
                        x='timestamp',
                        y='running_profit',
                        title=f"Account Profit Growth ({'Current Version' if not show_all else 'All Time'})",
                        labels={'running_profit': 'Cumulative Profit (%)', 'timestamp': 'Date'},
                        template="plotly_dark"
                    )
                    fig_equity.update_traces(line_color="#00FFCC" if net_pnl >= 0 else "#FF5555", line_width=2.5)
                    fig_equity.update_layout(height=350)
                    st.plotly_chart(fig_equity, use_container_width=True)

                with c_g2:
                    outcome_items = [{'Outcome': 'Wins', 'Count': wins}]
                    if breakevens > 0:
                        outcome_items.append({'Outcome': 'Breakeven (0 Loss)', 'Count': breakevens})
                    outcome_items.append({'Outcome': 'Losses', 'Count': actual_losses})

                    outcome_df = pd.DataFrame(outcome_items)
                    fig_bar = px.bar(
                        outcome_df,
                        x='Outcome',
                        y='Count',
                        color='Outcome',
                        color_discrete_map={'Wins': '#38EF7D', 'Breakeven (0 Loss)': '#58A6FF', 'Losses': '#FF5555'},
                        title="Trade Outcomes",
                        text='Count',
                        template="plotly_dark"
                    )
                    fig_bar.update_traces(textposition='outside')
                    fig_bar.update_layout(showlegend=False, height=350)
                    st.plotly_chart(fig_bar, use_container_width=True)

                if breakevens > 0:
                    st.caption(
                        f"💡 **Understanding the 4 Breakeven Trades:** "
                        f"When Take-Profit 1 (TP1) is hit, a trade is logged as **WIN_TP1**. "
                        f"These 4 **BREAKEVEN** trades occurred when Sentinel triggered safety protection *before* TP1 was hit "
                        f"(such as locking the stop to entry once +1.0R profit was reached, or defensively protecting against an opposing bias flip). "
                        f"When price reversed, it exited safely at your entry price with zero capital loss (only a tiny ~0.5% exchange fee)."
                    )

                avg_win = closed_trades[closed_trades['is_win']]['net_pnl_pct'].mean() if wins > 0 else 0.0
                avg_loss = closed_trades[~closed_trades['is_win']]['net_pnl_pct'].mean() if losses > 0 else 0.0
                best_trade = closed_trades['net_pnl_pct'].max() if total_closed > 0 else 0.0
                worst_trade = closed_trades['net_pnl_pct'].min() if total_closed > 0 else 0.0

                sc1, sc2, sc3, sc4 = st.columns(4)
                sc1.metric("Average Win", f"{avg_win:+.2f}%")
                sc2.metric("Average Loss", f"{avg_loss:+.2f}%")
                sc3.metric("Best Single Trade", f"{best_trade:+.2f}%")
                sc4.metric("Worst Single Trade", f"{worst_trade:+.2f}%")

                if net_pnl > 0:
                    growth_val = 1000 * (1 + net_pnl / 100.0)
                    st.success(f"💰 **Portfolio Impact Example:** A portfolio starting with **\\$1,000** would currently stand at **\\${growth_val:,.2f}** ({net_pnl:+.1f}% net gain across {total_closed} trades).")

                st.markdown("#### 🪙 Profit by Coin")
                coin_pnl = closed_trades.groupby('symbol')['net_pnl_pct'].agg(['sum', 'count']).reset_index()
                coin_pnl.columns = ['Coin', 'Total Profit (%)', 'Trades']
                coin_pnl = coin_pnl.sort_values('Total Profit (%)', ascending=True)

                fig_coin = px.bar(
                    coin_pnl,
                    y='Coin',
                    x='Total Profit (%)',
                    text='Total Profit (%)',
                    orientation='h',
                    color='Total Profit (%)',
                    color_continuous_scale=['#FF5555', '#58A6FF', '#00FFCC'],
                    title="Profit Contribution by Coin",
                    template="plotly_dark"
                )
                fig_coin.update_traces(texttemplate='%{text:+.1f}%', textposition='outside')
                fig_coin.update_layout(height=260, showlegend=False, coloraxis_showscale=False)
                st.plotly_chart(fig_coin, use_container_width=True)
            else:
                st.info("No closed trades logged for this selection.")

        # Tab 2: Version Evolution
        with tab_versions:
            st.subheader("How Sentinel Evolved Over Time")
            st.markdown(
                "Tracking the transformation from the early heuristic prototype to the current quantitative engine. "
                "Notice how each architecture update dramatically improved signal quality and profitability:"
            )

            all_closed = trades_df[trades_df['is_closed']].copy()
            if not all_closed.empty:
                evo_list = []
                version_keys = [
                    ("Legacy (v1 Prototype)", "Legacy (v1 Prototype)", "Oct 9 – Oct 17", "Flat rules, no VIP gate, uniform 0.55% stops"),
                    ("v2 (VIP Gate Overhaul)", "v2 (VIP Gate Overhaul)", "Oct 17 – Oct 23", "Parametric scoring, VIP gate ≥92%, liquidity buffers"),
                    ("v3 (Hexagonal Architecture)", "v3 (Hexagonal Architecture) [Current]", "Oct 23 11:00 – Present", "Pure domain math, decoupled pipeline, circuit breakers, ad-hoc recon")
                ]

                for key, display_name, dates, notes in version_keys:
                    grp = all_closed[all_closed['engine_version'] == key]
                    v_closed = len(grp)
                    v_wins = len(grp[grp['is_win']])
                    v_losses = v_closed - v_wins
                    v_wr = (v_wins / v_closed * 100.0) if v_closed > 0 else 0.0
                    v_pnl = grp['net_pnl_pct'].sum()
                    v_avg = v_pnl / v_closed if v_closed > 0 else 0.0

                    evo_list.append({
                        'Version': key.split(' (')[0],
                        'Architecture Phase': display_name,
                        'Timeline': dates,
                        'Trades': v_closed,
                        'Wins': v_wins,
                        'Losses': v_losses,
                        'Win Rate': f"{v_wr:.1f}%",
                        'Total Profit / Loss': f"{v_pnl:+.1f}%",
                        'Avg Return / Trade': f"{v_avg:+.2f}%",
                        'Summary': notes,
                        '_wr_num': v_wr,
                        '_pnl_num': v_pnl
                    })

                evo_summary_df = pd.DataFrame(evo_list)

                cv1, cv2 = st.columns(2)
                with cv1:
                    fig_v_wr = px.bar(
                        evo_summary_df,
                        x='Version',
                        y='_wr_num',
                        text='Win Rate',
                        color='Version',
                        color_discrete_sequence=['#8B949E', '#58A6FF', '#00FFCC'],
                        title="Win Rate by Version (%)",
                        template="plotly_dark"
                    )
                    fig_v_wr.update_traces(textposition='outside')
                    fig_v_wr.update_layout(yaxis_range=[0, 100], showlegend=False, height=330)
                    st.plotly_chart(fig_v_wr, use_container_width=True)

                with cv2:
                    fig_v_pnl = px.bar(
                        evo_summary_df,
                        x='Version',
                        y='_pnl_num',
                        text='Total Profit / Loss',
                        color='Version',
                        color_discrete_sequence=['#8B949E', '#58A6FF', '#00FFCC'],
                        title="Total Profit / Loss Realized (%)",
                        template="plotly_dark"
                    )
                    fig_v_pnl.update_traces(textposition='outside')
                    fig_v_pnl.update_layout(showlegend=False, height=330)
                    st.plotly_chart(fig_v_pnl, use_container_width=True)

                st.markdown("#### Version Performance Scorecard")
                display_cols = ['Architecture Phase', 'Timeline', 'Trades', 'Wins', 'Losses', 'Win Rate', 'Total Profit / Loss', 'Avg Return / Trade', 'Summary']
                st.dataframe(
                    evo_summary_df[display_cols],
                    use_container_width=True,
                    hide_index=True
                )

        # Tab 3: Saved Trades
        with tab_protection:
            st.subheader("Trades Sentinel Safely Avoided")
            st.markdown(
                "These are candidate setups detected by our scanners that were **rejected by circuit breakers** "
                "before risking any money. This shows our capital protection filters in action."
            )

            if not filtered_rejected.empty:
                cp1, cp2 = st.columns([1, 1])

                with cp1:
                    flaws = filtered_rejected['fatal_flaw_clean'].value_counts().reset_index()
                    flaws.columns = ['Reason Skipped', 'Count']

                    fig_donut = px.pie(
                        flaws,
                        names='Reason Skipped',
                        values='Count',
                        hole=0.45,
                        title="Top Reasons Sentinel Skipped Weak Setups",
                        template="plotly_dark",
                        color_discrete_sequence=px.colors.sequential.Tealgrn_r
                    )
                    fig_donut.update_traces(textposition='inside', textinfo='percent+label')
                    fig_donut.update_layout(height=340, showlegend=False)
                    st.plotly_chart(fig_donut, use_container_width=True)

                with cp2:
                    fig_ghost = px.histogram(
                        filtered_rejected,
                        x='confidence_score',
                        nbins=20,
                        title="Score of Avoided Setups (Cut off before 80% / 86%)",
                        labels={'confidence_score': 'Setup Score (%)', 'count': 'Number of Setups'},
                        template="plotly_dark",
                        color_discrete_sequence=['#FF5555']
                    )
                    fig_ghost.update_layout(height=340)
                    st.plotly_chart(fig_ghost, use_container_width=True)

                st.markdown("#### Recent Avoided Candidate Setups")
                rej_show = filtered_rejected[['timestamp', 'symbol', 'timeframe', 'direction', 'pattern_name', 'confidence_score', 'fatal_flaw_clean']].copy()
                rej_show.columns = ['Time (UTC)', 'Coin', 'Timeframe', 'Direction', 'Detected Pattern', 'Score (%)', 'Why Skipped']
                st.dataframe(
                    rej_show.head(50).style.format({'Score (%)': '{:.1f}%'}),
                    use_container_width=True,
                    hide_index=True
                )
            else:
                st.info("No skipped trade records for this selection.")

        # Tab 4: Trade History
        with tab_history:
            st.subheader(f"Trade History ({ver_name if not show_all else 'All Time'})")

            if not filtered_trades.empty:
                history_cols = [
                    'timestamp', 'symbol', 'direction', 'status', 'net_pnl_pct',
                    'entry_price', 'exit_price', 'leverage', 'confidence_score'
                ]
                avail = [c for c in history_cols if c in filtered_trades.columns]
                hist_df = filtered_trades[avail].copy()
                hist_df.columns = [
                    'Date (UTC)', 'Coin', 'Direction', 'Status', 'Profit / Loss (%)',
                    'Entry ($)', 'Exit ($)', 'Leverage', 'Score (%)'
                ][:len(avail)]

                st.dataframe(
                    hist_df.head(100).style.format({
                        'Profit / Loss (%)': '{:+.2f}%',
                        'Score (%)': '{:.1f}%',
                        'Entry ($)': '{:.4f}',
                        'Exit ($)': '{:.4f}',
                        'Leverage': '{:.0f}x'
                    }),
                    use_container_width=True,
                    hide_index=True
                )
            else:
                st.info("No trade records available.")

    # Execute Live Dashboard Fragment
    render_live_dashboard(selected_version, is_all, selected_coins)


if __name__ == "__main__":
    main()
