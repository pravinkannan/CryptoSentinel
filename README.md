# Crypto Futures Sentinel

Crypto Sentinel is an **enterprise-grade, volatility-aware algorithmic trading engine**. It utilizes an asynchronous event loop and a highly decoupled Dependency Injection (DI) architecture.

It automates advanced technical analysis (FVG, Imbalances, ATR bounds), determines Volatility Regimes, and evaluates risk via Google Gemini.

## Features

- **Asynchronous Event Loop**: Fully non-blocking `aiosqlite` databases and async operations ensure zero bottlenecking in the main loop.
- **Dynamic 3-Phase Volatility Life-Cycle**: 
  - Automatically tracks 5m ATR for sudden expansions (Phase 1 Impulse Shock).
  - Triggers cooldown periods during extreme whipsaw events.
- **Automated Emergency Guards**: Protects highly leveraged traps by moving Stop-Losses to Breakeven instantly when an Impulse Shock triggers.
- **Advanced Technical Analysis**: Identifies candlestick patterns, Fair Value Gaps (FVG), order block imbalances, and multi-timeframe regime alignment.
- **Risk & Microstructure Architecture**: Computes exact Liquidation Zones, 2-Tier Take Profits, ATR-buffered Stop Losses, and computes dynamically safe leverage ratios.
- **AI Evaluator & Pattern Drift Tracking**: Uses LLM evaluations to verify trading context (news, microstructure) while rejecting signals from "degraded" patterns.

## Architecture

For a detailed breakdown of the system execution flow and state machines, please review the [Architecture Documentation](docs/architecture.md).

## Setup Instructions

1. **Install Requirements**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Configuration**:
   Create a `.env` file in the root directory:
   ```env
   GEMINI_API_KEY=your_gemini_api_key_here
   TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
   TELEGRAM_CHAT_ID=your_telegram_chat_id_here
   ```
   Modify execution parameters safely inside `core/config.py`. The `Settings` model utilizes Pydantic for rigid type enforcement and validation.

3. **Telegram Setup**:
   - Talk to [@BotFather](https://t.me/botfather) to create a new bot.
   - Run `https://api.telegram.org/bot<YourBOTToken>/getUpdates` to locate your `chat_id`.

4. **Run the Sentinel**:
   ```bash
   python main.py
   ```

## Project Structure

The engine is built on SOLID principles and Domain-Driven Design (DDD):

```text
Crypto-Sentinel/
├── core/                   # Global state managers, Configs, Pydantic Models, Exceptions
├── data/                   # Market Microstructure, OHLCV fetchers, News Webhooks
├── docs/                   # Internal documentation and Mermaid flowcharts
├── engine/                 # TA, Volatility Engine, Risk Math, AI Evaluation
├── execution/              # Async PaperTrading and SL/TP Trailing Logic
├── notifications/          # Discord/Telegram alerts routing
├── tests/                  # Pytest unit tests
├── data_storage/           # aiosqlite databases and JSON state storage
└── main.py                 # Async Injection Container and execution loop
```

## About Storage (`data_storage/`)

- `sentinel_state.json`: Maintains short-term memory (e.g., alert tracking). Delete it only for a hard factory reset.
- `trading_records.db`: Async SQLite database for paper trades.
- `sentinel_journal.db`: Async SQLite database for statistical performance over time (used for pattern drift degradation).
