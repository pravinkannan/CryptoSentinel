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
1. **4H Macro Conflict**: Trade opposes the 4H macro trend. On scalping timeframes (`5m`, `15m`), counter-trend scalps are strictly prohibited unless they meet a higher institutional bar: confirmed `key_level_rejection == True` AND volume surge $\ge 2.5\times$ 20-period SMA.
2. **RSI Exhaustion**: RSI > 75 for Long, or RSI < 25 for Short.
3. **Anemic Volume Trap**: Volume < 0.55x 20-period SMA AND `key_level_rejection == False`.

#### Stage 2: Parametric Continuous Scoring
Starts with `base_score = 64.0%` and computes continuous probability points across three orthogonal tiers (+36.0% maximum total tailwinds):

##### Tier 1: Foundational Order Flow & Execution Zone (Max +18.0%)
1. **Dynamic Institutional Volume (0.0% to +8.0%)**:
   - `vol_points = min(8.0, max(0.0, round(((volume / vol_sma_20) - 1.0) * 5.333333333333334, 2)))`
   - Reaches maximum points at $2.5\times$ 20-period SMA.
   - *Directional Constraint*: Only awarded if the candle closed strongly in trade direction (`close > open` for Long, `close < open` for Short).
2. **Structural Level Precision (0.0% to +6.0%)**:
   - *Rejection Constraint*: Only awarded if confirmed `key_level_rejection == True`.
   - Continuous linear decay: `level_points = max(0.0, 6.0 * (1.0 - (dist / 0.30)))` if `dist <= 0.30%`, else `0.0`.
3. **Session VWAP Acceptance (0.0% to +4.0%)**:
   - `+4.0%` awarded when price is trading in direction of setup relative to Session VWAP (`close >= vwap` for Long, `close <= vwap` for Short).

##### Tier 2: Institutional Positioning & Macro Bias (Max +11.0%)
1. **Multi-Timeframe Trend Alignment (+3.0%)**:
   - `+3.0%` awarded when trade strictly aligns with both 1H and 4H macro trends.
2. **BTC Macro Beta Alignment (+2.5%)**:
   - `+2.5%` awarded to altcoin setups moving in sync with Bitcoin macro trend direction.
3. **Funding Rate Tailwind (+1.0%)**:
   - `+1.0%` awarded when perpetual funding rate is negative on Longs ($\le -0.005\%$) or positive on Shorts ($\ge +0.005\%$).
4. **Order Book Defensive Shield (+3.0%)**:
   - `+3.0%` awarded when a massive institutional limit bid/ask wall ($\ge \$100\text{k}$) protects entry.
5. **Order Book Depth & Imbalance (0.0% to +1.5%)**:
   - *Thin Book Filter*: Only calculated if `total_book_depth_usd > 100,000`.
   - `ob_points = min(1.5, max(0.0, round((imbalance_ratio - 1.0) * 1.5, 2)))`.

##### Tier 3: Momentum Catalysts & Exit Gravity (Max +7.0%)
1. **Structural RSI Divergence (+3.5%)**:
   - `+3.5%` awarded for confirmed Bullish/Bearish RSI Divergence on the signal timeframe.
2. **Target Liquidity Magnet Pull (+2.0%)**:
   - `+2.0%` awarded when an opposing liquidity pool acts as an institutional price magnet accelerating towards Take Profit.
3. **Open Interest Expansion (+1.5%)**:
   - `+1.5%` awarded when 15m Open Interest delta expands by $> +1.5\%$.
4. **Volatility Squeeze Expansion (+1.0%)**:
   - `+1.0%` awarded when Bollinger/Keltner squeeze releases with directional volume.
5. **RSI Momentum Room (+1.0%)**:
   - `+1.0%` awarded if RSI is in the prime momentum acceleration corridor ($40 \le \text{RSI} \le 60$) without divergence.
   *(Tier 3 subtotal is capped at +7.0% maximum).*

##### Microstructure Friction & Penalties (Capped at -10.0% max)
- **Hard Frictional Barriers**:
  - `-3.0%` Blocking Order Book Wall directly in path of TP.
  - `-3.0%` Opposing Liquidity Sweep Risk pool within 0.8% of entry.
  - `-3.5%` Altcoin fighting opposing BTC Macro Trend.
- **Soft Frictional Drag**:
  - `-3.0%` Fighting Session VWAP (e.g. longing below VWAP).
  - `-2.5%` Overcrowded Funding Squeeze Trap ($|\text{FR}| > 0.03\%$).
  - `-2.0%` Low Volume Drag ($< 0.80\times$ SMA).
  - `-2.0%` Phase 3 Momentum Exhaustion Trap.
- **Smart Money Absorption**: If institutional volume is strong (`vol_points >= 5.0`), raw friction penalties are halved ($\times 0.5$).
- **Deduction Floor**: Total friction is capped at `-10.0%` to prevent deductive cannibalization/double jeopardy.

`net_score = base_score + vol_points + level_points + vwap_points + ob_points + trend_tailwinds + catalyst_points + friction`  
`final_score = round(max(0.0, min(100.0, net_score)), 1)` (Theoretical ceiling: $64.0 + 36.0 = 100.0\%$)

#### Stage 3: The VIP Gate (Tier Classification)
- **VIP Dispatch (>= 92.0%)**: To protect the >80% win rate mandate, VIP qualification strictly enforces 5 concurrent institutional requirements:
  1. `vol_points > 0.0`: Strong institutional volume surge in the trade direction.
  2. `level_points > 0.0`: Verified structural key level rejection.
  3. `has_sweep_risk == False`: No opposing order book liquidity sweep pools threatening entry.
  4. `macro_trend_aligned == True`: Trade strictly aligns with both 1H and 4H macro trends.
  5. `total_book_depth_usd >= $250k`: Sufficient institutional book depth to prevent slippage sweeps.
  *If any mandate is missing, score is strictly capped at `91.9%` (`🔹 STANDARD TIER`).*
  
  **Dual-Engine VIP Model:**
  - **👑 VIP Sniper (`VIP_SNIPER`)**: High-leverage institutional wall setup (up to 60x on BTC, 40x on ETH) on 5m/15m leaning against resting order book shields ($\ge \$250\text{k}$) with tight $\sim 0.75\%$ SL designed for +80% to +100% ROE.
  - **👑 VIP Macro Swing (`VIP_SWING`)**: Calibrated leverage (20x–25x on BTC/ETH, 15x–20x on Altcoins) targeting 1H trend continuation.
- **Standard Dispatch (86.0% – 91.9%)**: Disciplined swing setups (`🔹 STANDARD TIER`) with asset-tiered leverage (20x–25x on BTC/ETH, 10x–12x on Altcoins), 70% profit-take at TP1, and Auto-Breakeven at 1.0R.
- **Reject (< 86.0%)**: Discarded as low probability. Eliminates 87% of system drawdown based on empirical SQLite trade journal analytics. All exact decimal deductions and metrics are recorded in `eval_trace` for auditability.


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
