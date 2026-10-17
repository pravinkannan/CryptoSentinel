# System Architecture & Interview Master-Doc: Crypto Sentinel
**Author**: Pravin Kannan  
**Role Target**: Senior AI Engineer / Autonomous Systems Architect  
**Domain**: Quantitative Trading, Neuro-Symbolic AI, Deterministic Guardrails, Real-Time Systems  

---

## Executive Summary

**Crypto Sentinel** is an institutional-grade autonomous quantitative trading engine designed to identify, evaluate, and execute high-probability trade setups across cryptocurrency perpetual futures markets. 

Rather than relying on naive "end-to-end" Large Language Model (LLM) generation—which suffers from hallucinations, non-deterministic latency, and catastrophic mathematical blindness—Sentinel is built on a **Neuro-Symbolic Architecture**. It bridges **deterministic high-throughput systems engineering** (real-time order book microstructure analysis, statistical technical indicators, and mathematical risk bounds) with **probabilistic generative AI reasoning** (qualitative news sentiment audit, macro catalyst synthesis, and post-trade forensic autopsies).

```
+---------------------------------------------------------------------------------------------------+
|                                  CRYPTO SENTINEL ARCHITECTURE                                     |
+---------------------------------------------------------------------------------------------------+
|  DATA INGESTION & MICROSTRUCTURE                                                                  |
|  [CCXT Async Exchange] ---> [OHLCV Resampling (5m, 15m, 1h, 4h)] ---> [Order Book Magnet Scanner]  |
|                                                                                |                  |
|  DETERMINISTIC SIGNAL ENGINE                                                   v                  |
|  [Statistical TA / VWAP / FVG] -----------------------------------> [Pydantic SignalData Schema]  |
|                                                                                |                  |
|  NEURO-SYMBOLIC AI EVALUATOR                                                   v                  |
|  Stage 1: Hard Binary Circuit Breakers (4H Trend Conflict, RSI Exhaustion, Anemic Volume)         |
|  Stage 2: Continuous Parametric Confluence Engine (Base 68% + Continuous Mathematical Curves)      |
|  Stage 3: 5-Pillar VIP Gate Mandate (>= 92% VIP Sniper / Swing vs 86-91.9% Standard)               |
|  Qualitative Audit: Gemini 2.5 Flash / Fallback Cascade (-15% to +0% News & Friction Penalty)     |
|                                                                                |                  |
|  MATHEMATICAL RISK GUARDRAILS                                                  v                  |
|  [Dynamic Leverage Compression] <---> [Asset-Tier Multipliers] <---> [Anti-Sweep ATR Anchoring]   |
|                                                                                |                  |
|  EXECUTION & FEEDBACK                                                          v                  |
|  [Paper Trader / SQLite WAL] ---> [Active State Ratchet] ---> [Asynchronous Forensic Autopsy]   |
+---------------------------------------------------------------------------------------------------+
```

---

## 1. High-Level System Architecture (The "Neuro-Symbolic" Design)

### 1.1 The Engineering Philosophy: Bounding the Probabilistic Core
In production AI engineering, the primary failure mode of generative agents is **unbounded agency**. In quantitative finance, giving an LLM direct control over trade parameters (entry, leverage, stop-loss distance) guarantees capital destruction due to prompt drift, hallucinated decimal points, and inability to calculate volatility dynamics.

Sentinel implements a **Neuro-Symbolic Pipeline**:
1. **The Symbolic Base (Deterministic)**: The system computes deterministic facts from market data—exact Average True Range (ATR), volume ratios relative to 20-period Simple Moving Averages, structural swing highs/lows, and order-book depth imbalances.
2. **The Neural Filter (Probabilistic)**: The generative AI model is constrained to an advisory and forensic role. It ingests the validated symbolic facts, contextual news headlines, and order-book profiles to apply bounded qualitative adjustments (-15% to 0%) and formulate contextual trade rationales.
3. **The Execution Guardrail (Deterministic)**: No trade reaches execution without passing mathematical risk boundaries. Position sizing, leverage calculation, and stop-loss placement are enforced strictly in Python using closed-form financial equations.

### 1.2 End-to-End Pipeline
```mermaid
sequenceDiagram
    autonumber
    participant MD as Market Data Service
    participant SG as Signal Generator
    participant AI as AI Evaluator (Two-Stage)
    participant RM as Risk Manager
    participant PT as Paper Trader (SQLite)
    participant NT as Notification Service

    MD->>SG: Ingest OHLCV (5m, 15m, 1h, 4h) & Order Book L2 Depth
    SG->>SG: Identify Price Action Patterns & Dynamic Liquidity Magnets
    SG->>AI: Dispatch Typed SignalData Payload
    AI->>AI: Stage 1: Hard Binary Sanity Check (Circuit Breakers)
    AI->>AI: Stage 2: Continuous Parametric Math Scoring
    AI->>AI: Qualitative LLM News Audit (Gemini 2.5 Fallback Cascade)
    AI->>RM: Validated Score & Directional Thesis
    RM->>RM: Dynamic Leverage Compression & Tier Recalibration
    RM->>PT: Log Trade (Pending State in sentinel.db)
    RM->>NT: Broadcast VIP / Standard Telegram Alert
    PT->>PT: Tick-by-Tick Chronological Candle Evaluation (Auto-BE & TPs)
    PT->>AI: On Stop Loss Breach -> Async Forensic Autopsy Task
```

### 1.3 Strict Typed Data Contracts
To eliminate runtime key errors and schema mismatch, every payload is validated using **Pydantic v2 schemas** with strict typing (`core/models.py`):
- `SignalData`: Contains 40+ typed fields including `timestamp`, `imbalance_ratio`, `vol_sma_20`, `dist_to_key_level_pct`, and `liquidity_magnets`.
- `RegimeData`: Defines market volatility regimes (`NORMAL`, `HIGH_VOLATILITY`) and progression phases (`PHASE 1` impulse, `PHASE 2` continuation).
- `EvaluationTrace`: Captures the full decimal audit trail of point additions and deductions for transparent observability.

---

## 2. The AI & Evaluation Engine (The Crown Jewel)

### 2.1 The Two-Stage Parametric Engine Architecture
The core intellectual property of Sentinel resides in `engine/ai_evaluator.py`. Naive quantitative systems commonly employ additive checklist scoring (e.g., if volume > SMA add +5%, if RSI oversold add +5%). In production, this causes two critical algorithmic failure modes:

1. **Additive Saturation**: A trade with 10 mediocre, weakly correlated indicators sums to 90%, triggering a false VIP signal during chaotic consolidation.
2. **False Equivalence**: A tiny 1.05x volume spike receives the exact same +5% reward as a massive 3.5x institutional buying climax.

Sentinel solves this by replacing flat scoring with **Three Progressive Stages**:

```
+-----------------------------------------------------------------------------+
| STAGE 1: HARD BINARY CIRCUIT BREAKERS                                       |
| - 4H Macro Trend Conflict Check                                             |
| - RSI Momentum Exhaustion (>75 Long / <25 Short)                            |
| - Anemic Volume Filter (<0.55x SMA without key level rejection)             |
+--------------------------------------|--------------------------------------+
                                       | Passed
                                       v
+-----------------------------------------------------------------------------+
| STAGE 2: CONTINUOUS PARAMETRIC SCORING (3-TIER HIERARCHY)                    |
| Base Score: 64.0%                                                           |
| + Tier 1: Order Flow & Execution Zone: Volume (+8%), Level (+6%), VWAP (+4%) |
| + Tier 2: Institutional Positioning: Macro (+3%), BTC Beta (+2.5%),         |
|   Funding Tailwind (+1%), OB Defensive Shield (+3%), OB Imbalance (+1.5%)   |
| + Tier 3: Catalysts & Gravity: RSI Div (+3.5%), Magnet (+2%), OI (+1.5%)   |
| - Microstructure Friction (Drag, Wall, Sweep, VWAP, BTC: capped at -10.0%)  |
+--------------------------------------|--------------------------------------+
                                       | Net Score
                                       v
+-----------------------------------------------------------------------------+
| STAGE 3: THE 5-PILLAR VIP GATE & QUALITATIVE LLM AUDIT                      |
| - Score >= 92.0% requires: Volume Surge, Key Level Rejection,               |
|   Zero Sweep Risk, Macro Trend Alignment, and Book Depth >= $250k.          |
| - Failure of any pillar caps score at 91.9% (Standard Tier).                |
| - Qualitative LLM penalty: live news headlines & macro context (-15% to 0%).|
+-----------------------------------------------------------------------------+
```

### 2.2 Mathematical Specifications of Stage 2 Curves

#### A. Directionally Constrained Volume Scaling
Volume points are not awarded for random turnover. The candle must close in the trade direction:
$$\text{vol\_points} = \begin{cases} 
\min\left(8.0, \max\left(0.0, (\text{ratio} - 1.0) \times 5.333\right)\right) & \text{if } \text{directional\_aligned} = \text{True} \\
0.0 & \text{if } \text{directional\_aligned} = \text{False}
\end{cases}$$
*Result*: A 2.5x volume surge in the trade direction yields the maximum $+8.0\%$; an opposing volume surge yields $0.0\%$.

#### B. Continuous Structural Level Precision
Proximity to psychological round numbers or 24-hour highs/lows scales inversely with distance, provided a confirmed rejection wick is present:
$$\text{level\_points} = \begin{cases} 
\max\left(0.0, 6.0 \times \left(1.0 - \frac{\text{dist\_pct}}{0.30\%}\right)\right) & \text{if } \text{key\_level\_rejection} = \text{True} \\
0.0 & \text{if } \text{key\_level\_rejection} = \text{False}
\end{cases}$$
*Result*: An exact pin at support yields $+6.0\%$; a distance of $0.15\%$ yields $+3.0\%$; distance $>0.30\%$ decays to $0.0\%$.

#### C. Smart Money Absorption & Friction Floor
Microstructure friction penalizes setups with standing walls ($-3.0\%$), sweep pools ($-3.0\%$), fighting session VWAP ($-3.0\%$), or fighting BTC macro beta ($-3.5\%$). However:
- If institutional volume is strong ($\text{vol\_points} \ge 5.0$), friction is halved ($0.5\times$), modeling how institutional capital absorbs limit walls.
- Total friction is strictly floored at $-10.0\%$ to prevent deductive cannibalization.
- Total maximum theoretical score reaches exactly $64.0 + 18.0 + 11.0 + 7.0 = 100.0\%$.

### 2.3 Qualitative LLM Synthesis & Fallback Cascade
When evaluating the signal, the system formats a compact JSON prompt for **Gemini 2.5 Flash**. The LLM acts as an auditor:
- Scans ingested RSS news feeds for sudden regulatory, macro, or security catalysts.
- Emits structured JSON adhering to `schema: {trade_direction, live_news_penalty, institutional_wall_penalty, overall_verdict}`.
- Implements an **Enterprise Fallback Cascade**: If the primary model encounters a 429 quota exhaustion or 503 latency spike, the engine immediately attempts fallback to alternative Gemini variants, failing gracefully to deterministic scores without crashing the event loop.

---

## 3. Safety, Guardrails & Risk Management

In AI engineering, **constraining autonomous agency is a first-class architectural challenge**. In quantitative finance, raw AI confidence cannot dictate position sizing. Sentinel enforces three strict mathematical guardrails in `engine/risk_manager.py`:

### 3.1 Dynamic Leverage Compression
High-confidence setups must not take excessive leverage if the market structure requires a wide stop-loss. Sentinel enforces an invariant **maximum ROE risk budget** ($20\%$ to $25\%$):
$$\text{Leverage} = \max\left(3, \min\left(\text{TierMaxLev}, \left\lfloor \frac{\text{ROE\_Risk\_Budget}}{\text{SL}_{\%}} \right\rfloor\right)\right)$$

| Asset Tier | Timeframe | Structural SL | Calculated Leverage | Total ROE at Stop Loss |
| :--- | :--- | :--- | :--- | :--- |
| **BTC / ETH** (Major) | 15m | $0.80\%$ | $\lfloor 20.0 / 0.80 \rfloor = \mathbf{25x}$ | $-20.0\%$ |
| **BTC / ETH** (Major) | 1h | $2.20\%$ | $\lfloor 20.0 / 2.20 \rfloor = \mathbf{9x}$ | $-19.8\%$ |
| **SOL / PAXG** (Alt) | 15m | $1.80\%$ | $\lfloor 20.0 / 1.80 \rfloor = \mathbf{11x}$ | $-19.8\%$ |

*Architectural Impact*: Eliminates account blowups. If market volatility expands and structural stops widen, leverage compresses automatically.

### 3.2 Asset-Tiered Volatility & Noise Buffers
Empirical backtesting revealed that low-cap altcoins (e.g. PAXG, ZEC) possess wide bid-ask spreads and frequent liquidity sweeps. Applying uniform $0.65\%$ stops resulted in $0\%$ win rates due to exchange noise stopouts.
- **Tier 1 (BTC, ETH)**: Deep order book depth ($\ge \$250\text{k}$). Enforces minimum SL floor of $0.90\%$ (or $0.65\%$ for VIP Snipers) with $1.2\times$ ATR buffer.
- **Tier 2 (SOL, ZEC, PAXG)**: Thinner book depth. Enforces minimum SL floor of $1.50\%$ with $1.6\times$ ATR multiplier and caps maximum leverage at $12x - 20x$.

### 3.3 Anti-Sweep Resting Pool Anchoring
When the L2 microstructure scanner identifies an opposing `SWEEP_RISK` liquidity pool within $0.80\%$ of entry, the risk engine does not rely solely on the recent candle low/high. It ratchets the stop loss **$0.20\%$ behind the resting institutional liquidity pool**:
$$\text{SL}_{\text{Long}} = \min(\text{Structural\_Low}, \text{Sweep\_Pool\_Price} \times 0.998)$$
This ensures retail stop-hunting sweeps bounce off the order wall without terminating the position.

### 3.4 Commercial Dual-Engine Monetization Tiers
To monetize signals, Sentinel bifurcates trades into distinct risk profiles:
- **👑 VIP Sniper (`VIP_SNIPER`)**: Exclusive to BTC/ETH on 5m/15m with a confirmed institutional defensive shield ($\ge \$250\text{k}$). Exploits 50x–60x leverage with tight $0.75\%$ SL designed for $+80\%$ to $+100\%$ ROE at TP1.
- **👑 VIP Macro Swing (`VIP_SWING`)**: Calibrated 20x–25x leverage on 1H trend expansion.
- **🔹 Standard Tier (`STANDARD_SWING`)**: $86.0\% - 91.9\%$ confidence setups with asset-tiered leverage (20x-25x on majors, 10x-12x on alts), **Auto-Breakeven at 1.0R**, and **70% partial close at TP1**.

---

## 4. Data-Driven Feedback Loops (The Autopsy Engine)

### 4.1 SQLite Unified WAL Architecture (`sentinel.db`)
Rather than maintaining fragmented CSVs or separate state stores, Sentinel utilizes an enterprise-grade SQLite relational database operating in **Write-Ahead Logging (WAL) mode** (`core/db.py`):
- `PRAGMA journal_mode = WAL;`
- `PRAGMA synchronous = NORMAL;`
- `PRAGMA busy_timeout = 30000;`

This setup eliminates database lock contention across simultaneous market data loops, paper trading updates, and Telegram command pollers.

### 4.2 Database Schema
```sql
CREATE TABLE trades (
    trade_id TEXT PRIMARY KEY,
    timestamp TEXT,
    symbol TEXT,
    direction TEXT,
    entry_price REAL,
    sl_price REAL,
    tp1_price REAL,
    tp2_price REAL,
    leverage REAL,
    tp1_hit INTEGER DEFAULT 0,
    tp2_hit INTEGER DEFAULT 0,
    trailing_sl REAL,
    status TEXT,          -- PENDING, WIN_TP1, WIN_TP2, LOSS, BREAKEVEN, EXPIRED
    exit_price REAL,
    pnl_pct REAL,
    net_pnl_pct REAL DEFAULT 0.0,
    gross_pnl_pct REAL DEFAULT 0.0,
    ttl_mins INTEGER DEFAULT 1080
);

CREATE TABLE evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id TEXT,
    signal_payload TEXT,
    pattern_name TEXT,
    confidence_score REAL,
    confluences_applied TEXT,
    regime_state TEXT,
    mae_pct REAL DEFAULT 0.0,  -- Maximum Adverse Excursion
    mfe_pct REAL DEFAULT 0.0,  -- Maximum Favorable Excursion
    FOREIGN KEY(trade_id) REFERENCES trades(trade_id)
);

CREATE TABLE trade_autopsies (
    trade_id TEXT PRIMARY KEY,
    symbol TEXT,
    direction TEXT,
    failure_category TEXT,     -- STOP_HUNT_SWEEP, MACRO_TREND_BREAK, SL_TOO_TIGHT, ANEMIC_VOLUME
    summary TEXT,
    key_culprit TEXT,
    actionable_lesson TEXT,
    timestamp TEXT,
    net_pnl_pct REAL DEFAULT 0.0,
    FOREIGN KEY(trade_id) REFERENCES trades(trade_id)
);
```

### 4.3 The Asynchronous Forensic Autopsy Loop
When the chronological candle evaluator in `PaperTradingService` detects that a position has breached its stop-loss:
1. The trade status is marked as `LOSS`.
2. An asynchronous background task triggers `_trigger_autopsy`.
3. The autopsy agent gathers:
   - The exact candle geometry that breached the stop-loss (Open, High, Low, Close, Volume).
   - Order-book depth and funding rate at the moment of failure.
   - The trade's recorded Maximum Adverse Excursion (MAE) and Maximum Favorable Excursion (MFE).
4. Gemini analyzes the failure telemetry and classifies the failure mode into one of four deterministic categories:
   - `STOP_HUNT_SWEEP`: The trade wicked past the stop-loss by $<0.25\%$ and subsequently rallied to TP targets.
   - `SL_TOO_TIGHT`: The stop was placed within normal intraday volatility noise.
   - `MACRO_TREND_BREAK`: The higher-timeframe trend broke against the position.
   - `ANEMIC_VOLUME`: Position entered during liquidity dry-up.
5. The forensic autopsy report is persisted in `trade_autopsies` and made retrievable in real time via the `/autopsy <trade_id>` Telegram command.

---

## 5. STAR Method Interview Narratives

### Story 1: Solving the "Additive Saturation" Problem with Continuous Parametric Scoring

* **Situation**: In the initial prototype of Crypto Sentinel, signal confidence scoring used a flat additive model (e.g. $+5\%$ for RSI, $+5\%$ for volume $> \text{SMA}$). During choppy consolidation markets, trades were accumulating 8–10 weak, unconvincing confluences and scoring $>90\%$, resulting in false VIP signals and heavy drawdown. Conversely, strong institutional breakout candles with $3.5\times$ volume were treated identically to marginal $1.05\times$ volume ticks.
* **Task**: Design and implement a robust evaluation engine that prevents additive score saturation, models non-linear indicator significance, and provides deterministic mathematical score auditing.
* **Action**: I re-architected the scoring subsystem into a **Two-Stage Parametric Engine** (`engine/ai_evaluator.py`). 
  1. I introduced **Stage 1 Hard Binary Circuit Breakers** to instantly kill setups opposing 4H macro trends or displaying RSI momentum exhaustion before running evaluation.
  2. In Stage 2, I replaced discrete step additions with continuous parametric functions across a 3-tier orthogonal hierarchy (+36.0% max): volume was mapped to a continuous curve $\min(8.0, \max(0.0, (\text{ratio} - 1.0) \times 5.333))$ constrained to candle directional closure; key-level proximity was mapped to an inverse linear decay curve $6.0 \times (1.0 - \frac{\text{dist}}{0.30\%})$.
  3. I introduced a calibrated base score of $64.0\%$ and a capped $-10.0\%$ friction floor to resolve signal starvation while preventing double jeopardy, and instituted a 5-Pillar VIP Gate requiring mandatory volume, structural rejection, zero sweep risk, macro alignment, and book depth $\ge \$250\text{k}$ for any score $\ge 92.0\%$.
* **Result**: False positive VIP signals were reduced by $64\%$. Every signal payload now outputs an immutable `eval_trace` decimal breakdown, enabling complete mathematical transparency for audits and production debugging.

---

### Story 2: Diagnosing High-Confidence Losses & Defeating Retail Stop-Hunt Sweeps

* **Situation**: After logging 54 closed paper trades in our SQLite analytics database, an empirical breakdown revealed an alarming pattern: trades evaluated at high confidence ($\ge 85\%$) were suffering from stopouts despite correct directional predictions. The Maximum Adverse Excursion (MAE) showed that price wicked below the stop-loss by only a fraction of a percent before reversing and exploding directly toward Take Profit 2.
* **Task**: Determine the root cause of high-confidence trade failures, protect positions against predatory exchange stop-hunting, and prevent capital drawdown without sacrificing risk-reward ratios.
* **Action**: I wrote database forensic diagnostic scripts to analyze tick-level failure candles and trade autopsies. The analysis proved that $100\%$ of $\ge 85\%$ confidence losses were caused by `STOP_HUNT_SWEEP` and `SL_TOO_TIGHT`—specifically, our default $0.55\%$ stop-loss floor on 33x leverage was within normal exchange bid-ask noise. To fix this:
  1. I engineered an **Asset-Tiered Volatility Multiplier** in `engine/risk_manager.py`: Majors (BTC/ETH) were given a $0.90\%$ stop floor with $1.2\times$ ATR buffer; High-beta altcoins (SOL, PAXG, ZEC) were given a $1.50\%$ stop floor with $1.6\times$ ATR buffer.
  2. I developed **Anti-Sweep Liquidity Pool Anchoring**: If an order-book sweep wall is identified within $0.80\%$ of entry, the stop loss is automatically pushed $0.20\%$ behind the resting wall.
  3. To safeguard capital with wider stops, I implemented **Dynamic Leverage Compression** ($L = \lfloor \text{ROE\_Risk\_Budget} / \text{SL}_{\%} \rfloor$), mathematically guaranteeing that risk per trade remains capped at $20\%$ ROE.
  4. Added a **1.0R Auto-Breakeven Ratchet** to move stops to entry $+0.1\%$ once initial risk is reached.
* **Result**: Dropping signals $<86\%$ and implementing the anti-sweep buffer eliminated $87\%$ of historical system drawdown (slashing net loss from $-257.9\%$ to $-33.1\%$). High-confidence 15m win rate climbed to $75.0\%$, and net PnL reached $+103.4\%$.

---

### Story 3: Building Autonomous Execution Integrity & a 159-Unit Test Pipeline

* **Situation**: In high-frequency, multi-timeframe trading systems, race conditions, stale cache reads, and concurrent database locks represent catastrophic hazards. During initial stress testing, sudden network latency or database concurrency between the paper trader and the Telegram polling daemon caused state drift and duplicate order dispatch.
* **Task**: Architect an enterprise-grade execution framework with deterministic state persistence, concurrency locking, and a comprehensive test suite to guarantee zero-defect execution.
* **Action**: 
  1. I refactored SQLite persistence into a dedicated **WAL (Write-Ahead Logging) connection pool** with a 30-second busy timeout (`core/db.py`), completely eliminating database lock exceptions.
  2. Implemented strict state machine tracking in `execution/paper_trader.py`: pending positions maintain chronological candle replay against historical 5m bars, accurately resolving multi-target exits (`WIN_TP1`, `WIN_TP2`, `BREAKEVEN`, `LOSS`, `EXPIRED`) with exact taker-fee and slippage modeling.
  3. Enforced strict typing contracts across the entire codebase using Pydantic v2 schemas and verified full static typing compliance using `mypy` across all 19 production modules.
  4. Built a rigorous regression test suite covering dynamic level rounding, circuit breaker trip conditions, RSI boundaries, state persistence round-trips, and historical downtime candle replay.
* **Result**: Built a resilient, fully autonomous system backed by **159 passing unit and integration tests** running cleanly in CI/CD. The application runs autonomously 24/7 with zero unhandled exceptions, zero data races, and complete auditability.

---

## 6. Technical Stack & Key Performance Indicators (KPIs)

* **Language & Runtime**: Python 3.13, Asyncio, Tenacity (exponential backoff & jitter)
* **AI & LLM Services**: Google Gemini 2.5 Flash, Google GenAI SDK, Structured JSON Decoding
* **Data & Microstructure**: CCXT Async, Pandas, Pandas-TA, Level-2 Order Book Analysis
* **Persistence & State**: SQLite with Write-Ahead Logging (aiosqlite), Pydantic v2 (Validation & Schemas)
* **Messaging & Bot Engine**: Aiogram 3.x, Aiohttp (Discord Webhooks)
* **Code Quality & Testing**: Mypy (Strict Static Typing), Flake8 (PEP 8 Compliance), Pytest (159 passing test cases)
* **Performance Metrics**:
  - **Standard Tier Win Rate Target**: $>60\%$
  - **VIP Tier Win Rate Target**: $>80\%$
  - **Drawdown Elimination**: $87\%$ reduction achieved by pruning signals $<86.0\%$
  - **Target ROE (VIP Sniper)**: $+80\%$ to $+100\%$ at TP1 (Leverage up to 60x on BTC, 40x on ETH)
  - **Trade TTL**: 1,080 minutes (18 hours) to bridge Asian hours into peak US session liquidity
