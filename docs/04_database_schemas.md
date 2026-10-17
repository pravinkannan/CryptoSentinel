# 04 - Database Schemas

Crypto Sentinel relies on asynchronous SQLite (`aiosqlite`) with Write-Ahead Logging (WAL) to prevent file I/O from blocking the asyncio event loop. All state, live paper trades, evaluations, autopsies, and near-miss audits are stored in the unified database at `data/sentinel.db`.

---

## 1. `trades` Table

Tracks live paper executions, multi-tier take-profits, trailing stops, and realized PnL.

| Column Name | Type | Description |
| :--- | :--- | :--- |
| `trade_id` | TEXT | Primary Key (UUID string). |
| `timestamp` | TEXT | ISO-format UTC execution time. |
| `symbol` | TEXT | Asset pair ticker (e.g., `BTC/USDT`, `ETH/USDT`). |
| `direction` | TEXT | `LONG` or `SHORT`. |
| `entry_price` | REAL | Entry fill price. |
| `sl_price` | REAL | Active Stop Loss (ratchets to entry +0.1% upon reaching 1.0R). |
| `tp1_price` | REAL | Take Profit 1 Target (70% position closed at 1.5R). |
| `tp2_price` | REAL | Take Profit 2 Target (remaining 30% position runner). |
| `leverage` | REAL | Asset-tiered dynamically compressed leverage. |
| `tp1_hit` | INTEGER | Boolean (0 or 1). 1 indicates TP1 locked in. |
| `tp2_hit` | INTEGER | Boolean (0 or 1). 1 indicates full runner executed. |
| `trailing_sl` | REAL | Active dynamic trailing stop price. |
| `status` | TEXT | `OPEN`, `SHADOW`, `WIN_TP1`, `WIN_TP2`, `LOSS`, `BE` (Breakeven), `EXPIRED`. |
| `exit_price` | REAL | Final closing market price. |
| `pnl_pct` | REAL | Margin percentage PnL. |
| `net_pnl_pct` | REAL | Net percentage PnL after maker/taker fee deduction. |
| `gross_pnl_pct`| REAL | Gross percentage PnL before fee deduction. |
| `ttl_mins` | INTEGER | Time-To-Live expiration window (default: 1,080 minutes / 18 hours). |

---

## 2. `evaluations` Table

Stores quantitative snapshots and maximum excursion metrics for every trade.

| Column Name | Type | Description |
| :--- | :--- | :--- |
| `id` | INTEGER | Primary Key (Auto-increment). |
| `trade_id` | TEXT | Foreign Key referencing `trades(trade_id)`. |
| `signal_payload` | TEXT | Serialized JSON containing technical indicators and order book depth. |
| `pattern_name` | TEXT | Candlestick or structural pattern name (e.g. `Bullish Engulfing`). |
| `confidence_score` | REAL | Final continuous confidence rating ($0.0\% - 100.0\%$). |
| `confluences_applied` | TEXT | JSON list of all positive confluences and tailwinds. |
| `regime_state` | TEXT | Volatility Regime at entry (`NORMAL`, `HIGH_VOLATILITY`, `COMPRESSION`). |
| `mae_pct` | REAL | **Maximum Adverse Excursion** (deepest drawdown experienced). |
| `mfe_pct` | REAL | **Maximum Favorable Excursion** (highest profit reached). |

---

## 3. `trade_autopsies` Table

Stores Google Gemini forensic diagnoses on stopped-out trades, retrievable via Telegram (`/autopsy <trade_id>`).

| Column Name | Type | Description |
| :--- | :--- | :--- |
| `trade_id` | TEXT | Primary Key referencing `trades(trade_id)`. |
| `symbol` | TEXT | Asset pair ticker. |
| `direction` | TEXT | `LONG` or `SHORT`. |
| `failure_category` | TEXT | Strictly: `STOP_HUNT_SWEEP`, `MACRO_TREND_BREAK`, `SL_TOO_TIGHT`, `MOMENTUM_EXHAUSTION`, `UNEXPECTED_NEWS`. |
| `summary` | TEXT | 2-3 sentence executive summary of the trade failure. |
| `key_culprit` | TEXT | The primary technical or microstructure cause of failure. |
| `actionable_lesson` | TEXT | Specific quantitative rule adjustment for future setups. |
| `timestamp` | TEXT | ISO-format UTC failure time. |
| `net_pnl_pct` | REAL | Realized loss percentage. |

---

## 4. `rejected_signals` Table

Stores near-miss candidate setups ($75.0\% \le \text{confidence} < 86.0\%$) to audit why trades missed the dispatch threshold. Retrievable via Telegram (`/nearmiss`).

| Column Name | Type | Description |
| :--- | :--- | :--- |
| `id` | INTEGER | Primary Key (Auto-increment). |
| `timestamp` | TEXT | ISO-format UTC evaluation time. |
| `symbol` | TEXT | Asset pair ticker. |
| `timeframe` | TEXT | Timeframe (`5m`, `15m`, `1h`, `4h`). |
| `direction` | TEXT | `LONG` or `SHORT`. |
| `pattern_name` | TEXT | Candlestick or structural pattern detected. |
| `confidence_score` | REAL | Final post-AI score (e.g. `77.2%`). |
| `base_score` | REAL | Calibrated foundation score (`64.0%`). |
| `vol_points` | REAL | Dynamic institutional volume points ($0.0\% - 8.0\%$). |
| `level_points` | REAL | Structural key level proximity points ($0.0\% - 6.0\%$). |
| `vwap_points` | REAL | Session VWAP acceptance points ($0.0\% - 4.0\%$). |
| `ob_points` | REAL | Order book defensive shield & imbalance points ($0.0\% - 4.5\%$). |
| `trend_tailwinds` | REAL | Multi-timeframe trend & BTC beta points ($0.0\% - 6.5\%$). |
| `catalyst_points` | REAL | RSI divergence & magnet points ($0.0\% - 7.0\%$). |
| `friction` | REAL | Total microstructure deductions applied (floored at $-10.0\%$). |
| `net_score` | REAL | Mathematical sum before boundary rounding. |
| `scoring_checklist` | TEXT | JSON list of all positive checklist items and negative deductions. |
| `eval_trace_json` | TEXT | Complete mathematical `eval_trace` JSON payload. |
| `rejection_reason` | TEXT | Explanation of why the setup was filtered out. |
