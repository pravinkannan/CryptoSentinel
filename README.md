# Crypto Futures Sentinel 🛡️

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)
![Status](https://img.shields.io/badge/status-active-success.svg)

Crypto Sentinel is an **enterprise-grade, volatility-aware algorithmic trading engine**. Designed for modern crypto futures markets, it utilizes an asynchronous event loop and a highly decoupled Dependency Injection (DI) architecture for maximum performance.

By automating advanced technical analysis (FVG, Imbalances, ATR bounds), identifying structural Volatility Regimes, and delegating complex risk evaluations to an AI evaluator, Sentinel provides robust, hands-free market monitoring.

---

## 🚀 Key Features

- **Asynchronous Event Loop**: Powered by `asyncio` and `aiosqlite` to ensure zero bottlenecking in the main execution loop.
- **Dynamic 3-Phase Volatility Life-Cycle**: 
  - Automatically tracks 5m ATR for sudden expansions (Phase 1 Impulse Shock).
  - Triggers cooldown periods during extreme whipsaw events to protect capital.
- **Automated Emergency Guards**: Protects highly leveraged traps by moving Stop-Losses to Breakeven instantly when an Impulse Shock triggers.
- **Advanced Technical Analysis**: Detects candlestick patterns, Fair Value Gaps (FVG), order block imbalances, and multi-timeframe regime alignment.
- **Risk & Microstructure Architecture**: Computes exact Liquidation Zones, 2-Tier Take Profits, ATR-buffered Stop Losses, and computes dynamically safe leverage ratios based on real-time open interest.
- **AI Evaluator & Pattern Drift Tracking**: Uses LLM evaluations to verify trading context (news, microstructure) and actively rejects signals from historically "degraded" patterns.

---

## 🏛️ Architecture

For a detailed breakdown of the system execution flow, state machines, and microservice decoupling, please review our comprehensive [Architecture Documentation](docs/01_system_architecture.md) or explore the [Full Documentation Index](docs/index.md).

---

## 🛠️ Setup Instructions

### 1. Install Requirements
Ensure you are using Python 3.9+ and install the required dependencies:
```bash
pip install -r requirements.txt
```

### 2. Configuration
Create a `.env` file in the root directory with your API credentials:
```env
GEMINI_API_KEY=your_gemini_api_key_here
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
TELEGRAM_CHAT_ID=your_telegram_chat_id_here
```
> **Note:** Modify execution parameters safely inside `core/config.py`. The `Settings` model utilizes Pydantic for rigid type enforcement and validation.

### 3. Telegram Setup (Optional but Recommended)
To receive live signal dispatches and risk warnings:
- Talk to [@BotFather](https://t.me/botfather) on Telegram to create a new bot.
- Run `https://api.telegram.org/bot<YourBOTToken>/getUpdates` in your browser to locate your `chat_id`.

### 4. Run the Sentinel
```bash
python main.py
```

---

## 📂 Project Structure

The engine is built on SOLID principles and Domain-Driven Design (DDD):

```text
Crypto-Sentinel/
├── core/                   # Global state managers, Configs, Pydantic Models
├── data/                   # Microstructure, OHLCV fetchers, News Webhooks
├── docs/                   # Internal documentation and Mermaid flowcharts
├── engine/                 # TA, Volatility Engine, Risk Math, AI Evaluation
├── execution/              # Async PaperTrading and SL/TP Trailing Logic
├── notifications/          # Telegram alerts routing
├── tests/                  # Pytest unit testing suite
├── data/                   # Unified SQLite databases (sentinel.db)
└── main.py                 # Async Injection Container and execution loop
```
