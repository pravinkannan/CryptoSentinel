# 03 - AI Integration & Signal Generation

Sentinel merges quantitative mathematical analysis with qualitative Artificial Intelligence reasoning via Google Gemini.

## Signal Generation Workflow

The `SignalGeneratorService` evaluates market data and builds a highly structured `SignalData` payload. It operates in stages:

1. **Indicator Computation**: `pandas-ta` calculates moving averages, oscillators (RSI, MACD), volatility bands (Bollinger, Keltner), and VWAP.
2. **Pattern Recognition**: The `PatternRecognitionService` scans for exact structural formations (e.g., Fair Value Gaps, Bullish/Bearish Engulfing, Order Book Imbalances).
3. **Risk Enforcement**: The `RiskManager` computes Stop Loss, Take Profit 1/2, and dynamic leverage parameters.
4. **Context Injection**: Microstructure data (Funding Rates, Open Interest delta) is injected.
5. **Payload Serialization**: The finalized data is passed into a strict Pydantic model and dumped to JSON.

## The AI Evaluator Prompt

The `AIEvaluatorService` wraps the payload into a heavily engineered prompt sent to Google Gemini. Gemini acts as an institutional desk manager reviewing a junior trader's setup.

### Scoring Structure
The AI starts at a base score of 100% and deducts points for conflicts:
- **News Conflict (-10% to -20%)**: If the technical setup is Bullish but live news indicates a sudden bearish catalyst (e.g., regulatory crackdown).
- **Macro Trend Conflict (-15%)**: If the trade directly opposes the 4h/1h high-timeframe trend.
- **Pattern Drift Penalty (-10%)**: If the pattern's recent historic win-rate is `< 40%`, it is flagged as `DEGRADED`.
- **Microstructure Warning (-10%)**: If the funding rate is heavily skewed indicating crowded consensus.

### Final Verdict Routing
The AI returns a strict JSON containing `trade_direction`, `final_confidence_rating`, and an `overall_verdict`.

- **VIP Dispatch (>= 85%)**: High conviction. Dispatched to premium channels.
- **Standard Dispatch (75% - 84%)**: Valid setup. Traded via PaperTrader and logged.
- **Reject (< 75%)**: The trade is discarded. 

## Pattern Drift (Strategy Decay)

Markets evolve, and patterns that worked last month may stop working today. The `JournalManager` continuously calculates the rolling win-rate of the last 20 trades for every distinct pattern.

- If a pattern drops below a `40%` win-rate, it is tagged as `DEGRADED`.
- `DEGRADED` patterns are not hard-blocked, but they face a strict 10% penalty in the AI Evaluator, meaning they will only pass if macroeconomic news and macro-trends are perfectly aligned.
