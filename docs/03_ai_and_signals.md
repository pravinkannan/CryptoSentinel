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

### Scoring Structure: Two-Stage Parametric Engine

Sentinel replaces flat additive scoring with a continuous **Two-Stage Parametric Confluence Engine**:

#### Stage 1: Hard Binary Sanity Gate (The Circuit Breaker)
Immediately returns `REJECTED` (score: `0.0%`) without executing an LLM call if:
1. **4H Macro Conflict**: Trade opposes the 4H macro trend.
2. **RSI Exhaustion**: RSI > 75 for Long, or RSI < 25 for Short.
3. **Anemic Volume Trap**: Volume < 0.55x 20-period SMA AND `key_level_rejection == False`.

#### Stage 2: Parametric Continuous Scoring
Starts with `base_score = 68.0%` and computes continuous probability points:
1. **Dynamic Institutional Volume (0.0% to +12.0%)**:
   - `vol_points = min(12.0, max(0.0, ((volume / vol_sma_20) - 1.0) * 8.0))`
   - *Directional Fix*: Only awarded if candle closed strongly in trade direction (`close > open` for Long, `close < open` for Short).
2. **Structural Level Precision (0.0% to +10.0%)**:
   - *Rejection Fix*: Only awarded if `key_level_rejection == True`.
   - `level_points = max(0.0, 10.0 * (1.0 - (dist / 0.30)))` if `dist <= 0.30` else `0.0`.
3. **Order Book Depth (+0.0% to +8.0%)**:
   - *Thin Book Fix*: Only calculates imbalance points if `total_book_depth_usd > 100,000`.
   - `ob_points = min(5.0, max(0.0, (imbalance_ratio - 1.0) * 3.5))`.
   - Adds `+3.0%` if `ob_defensive_shield == True`.
4. **Trend & Derivatives (+0.0% to +10.0%)**:
   - `+4.0%` for `macro_trend_aligned == True`.
   - `+2.0%` for `funding_rate_tailwind == True`.
   - `+2.0%` for `oi_delta_15m > 1.5`.
   - `+2.0%` for RSI Momentum Room (RSI between 40-60).
5. **Microstructure Friction Absorption (0.0% to -6.0%)**:
   - `-3.0%` if volume < 0.80x SMA.
   - `-3.0%` if `blocking_ob_wall == True`.
   - *Smart Money Absorption*: If `vol_points > 6.0`, friction penalties are halved (* 0.5).

`net_score = base_score + vol_points + level_points + ob_points + trend_tailwinds + friction`  
`final_score = round(max(0.0, min(100.0, net_score)), 1)`

#### Stage 3: The VIP Gate (Tier Classification)
- **VIP Dispatch (>= 90.0%)**: Requires `vol_points > 0` AND `level_points > 0` (The 3-Pillar Mandate). If missing either pillar, score is strictly capped at `89.9%` (`🔹 STANDARD TIER`).
- **Standard Dispatch (80.0% - 89.9%)**: High-probability setup (`🔹 STANDARD TIER`). Traded via PaperTrader and logged.
- **Reject (< 80.0%)**: Discarded as low probability. All exact decimal deductions and metrics are recorded in `eval_trace` for auditability.

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
