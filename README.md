# Crypto Futures Sentinel 🛡️

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.13%2B-blue.svg)
![Tests](https://img.shields.io/badge/tests-165%20passed-success.svg)
![Status](https://img.shields.io/badge/status-active-success.svg)

Crypto Sentinel is an **enterprise-grade, volatility-aware algorithmic trading engine** built for crypto perpetual futures markets. Designed on a **Neuro-Symbolic Architecture**, it couples a high-speed quantitative execution engine with Google Gemini AI for qualitative macro and news auditing.

---

## 🚀 Key Features

- **Two-Stage Parametric Confluence Engine**:
  - **Stage 1 (Binary Circuit Breakers)**: Instantly purges 4H macro trend conflicts, RSI exhaustion traps ($>75$ Long / $<25$ Short), and anemic volume ($<0.55\times$ SMA) in memory in 0.001ms.
  - **Stage 2 (3-Tier Quantitative Scoring)**: Continuous non-linear scoring starting from a calibrated $64.0\%$ base score to a flawless $100.0\%$ theoretical ceiling:
    - *Tier 1 (Execution Zone - Max +18%)*: Dynamic volume curve ($0-8\%$), key-level proximity ($0-6\%$), and Session VWAP acceptance ($+4\%$).
    - *Tier 2 (Institutional Macro - Max +11%)*: Multi-timeframe trend ($+3\%$), BTC beta correlation ($+2.5\%$), funding tailwinds ($+1\%$), order book defensive shield ($+3\%$), and book depth imbalance ($+1.5\%$).
    - *Tier 3 (Momentum Catalysts - Max +7%)*: Structural RSI divergence ($+3.5\%$), target liquidity magnet pull ($+2\%$), and Open Interest expansion ($+1.5\%$).
    - *Microstructure Deductions*: Capped at $-10.0\%$ floor to prevent deductive cannibalization; halved by Smart Money Absorption when volume $\ge 5.0$ pts.
  - **Stage 3 (The 5-Pillar VIP Gate)**: Enforces 5 mandatory pillars (Volume surge, Key-level rejection, Zero sweep risk, Dual macro alignment, and Book depth $\ge \$250\text{k}$) for VIP qualification ($\ge 92.0\%$). Missing any pillar caps the score at $91.9\%$ (Standard Tier).
- **Dual-Engine VIP Model**:
  - **👑 VIP Sniper (`VIP_SNIPER`)**: High-leverage institutional wall setup (up to 60x on BTC, 40x on ETH) anchored behind $\ge \$100\text{k} - \$250\text{k}$ resting defensive shields on 5m/15m with tight $\sim 0.75\%$ SL.
  - **👑 VIP Macro Swing (`VIP_SWING`)**: Calibrated leverage ($20\text{x}-25\text{x}$) riding confirmed 1H/4H trend continuation.
- **Near-Miss Audit Logging & Telegram Commands**:
  - Logs candidate setups in the $75.0\% \le \text{score} < 86.0\%$ zone into a dedicated `rejected_signals` table in `sentinel.db` to analyze missing confluences and deductions.
  - Interactive Telegram bot commands: `/nearmiss` (view recent near-miss candidates) and `/autopsy <trade_id>` (post-loss forensic diagnoses).
- **Asset-Tiered Risk & Volatility Multipliers**:
  - Enforces minimum Stop Loss floors ($0.90\%$ Majors, $1.50\%$ Altcoins) to neutralize retail stop-hunt sweeps.
  - Anti-Sweep Liquidity Pool Anchoring pushes stops $0.20\%$ behind resting order book pools.
  - Dynamic Leverage Compression ($L = \lfloor \text{ROE\_Risk} / \text{SL}_{\%} \rfloor$) mathematically guarantees risk never exceeds $20\%$ ROE.
  - $1.0\text{R}$ Auto-Breakeven ratchet and $70\%$ profit-taking at TP1 ($1.5\text{R}$).
- **Automated Forensic Autopsies (`/autopsy`)**: Stopped-out paper trades trigger Gemini forensic post-mortems classifying root failure causes (`STOP_HUNT_SWEEP`, `MACRO_TREND_BREAK`, `SL_TOO_TIGHT`, `MOMENTUM_EXHAUSTION`).
- **Pattern Drift Governance**: Automatically identifies degraded candlestick patterns ($<40\%$ rolling win rate in SQLite) and downgrades them to paper SHADOW tracking.

---

## 🏛️ System Architecture

```text
[ Live Market Microstructure: L2 Order Book + OHLCV + Funding + RSS News ]
                                   │
                                   ▼
 [ STAGE 1: Hard Binary Circuit Breakers ]  ──▶ (90% noisy setups purged in memory)
                                   │ Passed
                                   ▼
 [ STAGE 2: 3-Tier Continuous Parametric Engine ]
 ├── Base Score: 64.0%
 ├── Tier 1: Order Flow & Execution Zone (Max +18.0%)
 ├── Tier 2: Institutional Positioning & Macro (Max +11.0%)
 ├── Tier 3: Momentum Catalysts & Exit Gravity (Max +7.0%)
 └── Microstructure Deductions (Floored at -10.0%, Halved on Volume)
                                   │
                                   ▼
 [ STAGE 3: The 5-Pillar VIP Gate & Gemini News Audit ]
 ├── Enforces 5 Non-Negotiable Institutional Pillars
 ├── Gemini Live News Headline Audit (-10% / -20% conflict penalty)
 └── Adverse Order Book Barrier Check (-10%)
                                   │
                ┌──────────────────┴──────────────────┐
                ▼                                     ▼
      👑 VIP TIER (>= 92.0%)              🔹 STANDARD TIER (86.0% - 91.9%)
  (Win Rate Target: > 80%)               (Win Rate Target: > 60%)
  Up to 60x Leverage (Sniper)            Asset-Tiered 20x-25x / 10x-12x
```

For detailed architecture specifications and interview narratives, see:
- [00 - System Architecture & Interview Master Document](docs/00_SYSTEM_ARCHITECTURE_AND_INTERVIEW_MASTER_DOC.md)
- [01 - System Architecture & Decoupling](docs/01_system_architecture.md)
- [02 - Volatility & Risk Management](docs/02_volatility_and_risk.md)
- [03 - AI Evaluator & Signal Generation](docs/03_ai_and_signals.md)
- [04 - Unified Database Schemas](docs/04_database_schemas.md)

---

## 🛠️ Setup Instructions

### 1. Requirements
Ensure you are using Python 3.10+ (tested on Python 3.13) and install dependencies:
```bash
pip install -r requirements.txt
```

### 2. Configuration
Create a `.env` file in the root directory:
```env
GEMINI_API_KEY=your_gemini_api_key_here
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
TELEGRAM_CHAT_ID=your_telegram_chat_id_here
```

### 3. Run the Sentinel
```bash
python main.py
```

### 4. Run Test Suite
```bash
pytest
```
*Current test suite: **165 passing unit & integration tests**.*

---

## 📂 Project Structure

```text
Crypto-Sentinel/
├── core/                   # State managers, Configs, Pydantic Models, DB connector
├── data/                   # Microstructure, Level-2 Order Book, News Webhooks
├── docs/                   # Complete architectural documentation & flowcharts
├── engine/                 # TA, Risk Math, Two-Stage AI Evaluator, JournalManager
├── execution/              # Async Paper Trader, Trailing SL, Breakeven Ratchet
├── notifications/          # Telegram Bot (Commands: /status, /autopsy, /nearmiss)
├── tests/                  # Pytest test suite (165 passing tests)
└── main.py                 # Async Dependency Injection container & event loop
```
