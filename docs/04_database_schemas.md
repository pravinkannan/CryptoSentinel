# 04 - Database Schemas

Crypto Sentinel relies on asynchronous SQLite (`aiosqlite`) to prevent file I/O from blocking the asyncio event loop. The system maintains two primary databases inside the `data_storage/` directory.

## 1. Paper Trader Database (`trading_records.db`)

This database simulates exchange execution, trailing stops, and capital drawdown.

### `trades` Table

| Column Name | Type | Description |
|-------------|------|-------------|
| `id` | INTEGER | Primary Key (Auto-increment). |
| `symbol` | TEXT | The ticker (e.g., BTC/USDT). |
| `direction` | TEXT | LONG or SHORT. |
| `entry` | REAL | The exact entry price. |
| `stop_loss` | REAL | Current Stop Loss. This updates dynamically if TP1 is hit. |
| `take_profit`| REAL | Ultimate Take Profit target. |
| `tp1` | REAL | Take Profit 1 Target. |
| `tp2` | REAL | Take Profit 2 Target. |
| `tp1_hit` | INTEGER| Boolean (0 or 1). If 1, SL is moved to Breakeven. |
| `leverage` | INTEGER| The dynamic leverage used for this simulation. |
| `timestamp` | TEXT | ISO Format UTC execution time. |
| `status` | TEXT | PENDING, WIN_TP2, LOSS, BE (Breakeven). |
| `pnl` | REAL | The calculated percentage PNL relative to margin. |
| `exit_price` | REAL | Price at which the trade closed. |
| `mae` | REAL | **Maximum Adverse Excursion**. Worst drawdown experienced during the trade. |
| `mfe` | REAL | **Maximum Favorable Excursion**. Best profit experienced during the trade. |
| `journal_id` | INTEGER| Foreign key linking to `sentinel_journal.db`. |

---

## 2. Journal Manager Database (`sentinel_journal.db`)

This database logs statistical analysis required for determining Pattern Drift (Strategy Decay) and evaluating AI performance.

### `trade_journal` Table

| Column Name | Type | Description |
|-------------|------|-------------|
| `id` | INTEGER | Primary Key. |
| `timestamp` | TEXT | ISO Format UTC. |
| `symbol` | TEXT | The ticker. |
| `direction` | TEXT | LONG or SHORT. |
| `regime` | TEXT | Volatility Regime at entry (e.g., HIGH_VOLATILITY). |
| `phase` | TEXT | Life-cycle phase at entry (e.g., PHASE 2). |
| `pattern_name`| TEXT | Specific structural pattern detected (e.g., FVG, Bullish_Engulfing). |
| `entry_price` | REAL | Snapshot of entry. |
| `sl_price` | REAL | Snapshot of original SL. |
| `leverage` | REAL | The leverage utilized. |
| `confidence_score`| REAL | The exact 0-100 score given by the Gemini AI. |
| `outcome` | TEXT | Final outcome (WIN_TP1, WIN_TP2, LOSS, BE). |
| `pnl_pct` | REAL | Actual Realized PNL. |
| `mae_pct` | REAL | Max Adverse Excursion percentage. |
| `mfe_pct` | REAL | Max Favorable Excursion percentage. |
| `duration_minutes`| REAL | Time taken for the trade to resolve. |

### Note on MAE & MFE
- **MFE (Max Favorable Excursion)**: If MFE routinely hits 20% but trades close at 5%, the Take-Profits are too wide.
- **MAE (Max Adverse Excursion)**: If MAE routinely hits 15% on winning trades, the initial Stop Loss is too tight or entry criteria needs refinement to prevent drawdown.
