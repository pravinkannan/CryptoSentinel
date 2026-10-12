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

- **VIP Dispatch (>= 90%)**: Institutional-grade conviction (`👑 VIP TIER`). Dispatched to premium channels.
- **Standard Dispatch (80% - 89%)**: High-probability setup (`🔹 STANDARD TIER`). Traded via PaperTrader and logged.
- **Reject (< 80%)**: The trade is discarded as low probability.

### Directional Invalidation & Bias Flip Protocol
When market structure changes while an active setup is open (e.g. 1h Bullish Engulfing follows an earlier 1h Bearish setup):
- **Opposing Equal/Higher Timeframe Signal**: Triggers a **BIAS FLIP / INVALIDATION ALERT**. The previous trade thesis is immediately invalidated, opposing paper trades are closed forcefully at market, and an alert is broadcast instructing traders to close/cancel opposing positions.
- **Opposing Lower Timeframe Signal**: Minor pullbacks (e.g. 5m/15m) against an active higher timeframe trade (1h/4h) are filtered out unless they possess VIP conviction (>= 90%), preventing traders from getting chopped up by noise.

### Liquidity Magnet Heatmap Detection & Tactical Next Actions
Sentinel scans the top 2% depth of the perpetual order book, clustering volume into dynamic price bins:
- When a clustered price level contains $\ge 3\times$ the local baseline volume and $\ge \$100\text{k}$, it is tagged as a **Liquidity Magnet**.
- Aligned magnets pull price toward Take Profit targets and grant a `+5% Order Book Liquidity Magnet` AI score boost.
- **Actionable Order Guidance**: Instead of merely displaying the wall, Sentinel computes specific, actionable order instructions for the trader:
  1. **🎯 Front-Run Take Profit (Target Magnet)**: When an opposing wall lies in the target path, Sentinel prescribes setting TP ~0.2% before the wall to guarantee execution before liquidity exhaustion.
  2. **🛡 Defensive Shield (Support/Resistance)**: When an institutional cluster defends the trade direction, Sentinel prescribes anchoring Stop Loss just behind the wall to let institutional limit orders absorb adverse pressure.
  3. **⚠️ Anticipate Sweep (Near-Entry Pool)**: When a large pool sits right below/above entry, Sentinel warns of a high-probability stop-run wick and instructs traders to ladder limit entry orders or await 5m wick rejection confirmation.
  4. **⚡ Scale Out at Barrier (Friction Wall)**: When an adverse cluster sits between entry and target, Sentinel advises taking 30–50% partial profits if order flow stalls at the cluster.
- Visual alerts and next actions are rendered directly in Telegram alerts.

### Post-Trade Autopsies (/autopsy)
When a trade hits its Stop Loss (`LOSS` state in Paper Trading):
- Sentinel captures the exact candle anatomy (5m OHLCV), order book imbalance, and funding rate at the moment of exit.
- Google Gemini conducts a forensic diagnosis determining whether the loss was a stop-hunt liquidity sweep, trend failure, or noise breach.
- Forensic reports are stored in `sentinel.db` (`trade_autopsies` table) and retrievable via Telegram: `/autopsy <trade_id>`.

## Pattern Drift (Strategy Decay)

Markets evolve, and patterns that worked last month may stop working today. The `JournalManager` continuously calculates the rolling win-rate of the last 20 trades for every distinct pattern.

- If a pattern drops below a `40%` win-rate, it is tagged as `DEGRADED`.
- `DEGRADED` patterns are not hard-blocked, but they face a strict 10% penalty in the AI Evaluator, meaning they will only pass if macroeconomic news and macro-trends are perfectly aligned.
