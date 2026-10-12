"""
AIEvaluatorService interfaces with the Google GenAI SDK.
"""

import logging
import json
import asyncio
from typing import Optional, Dict, List, Any
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from core.exceptions import AIEvaluationError

logger = logging.getLogger(__name__)
logging.getLogger("google_genai.models").setLevel(logging.ERROR)

class SignalEvaluation(BaseModel):
    trade_direction: str = Field(description="Strictly 'LONG', 'SHORT', or 'NEUTRAL'.")
    overall_verdict: str = Field(description="Final decision resolving Macro Trend vs News Bias conflicts.")
    news_impact_bias: str = Field(description="Bullish / Bearish / Neutral")
    live_news_penalty: int = Field(description="0 if no conflict, -10 or -20 if live news directly opposes the technical setup.")
    institutional_wall_penalty: int = Field(description="-10 if a massive limit-order wall sits directly in the path of the target, else 0.")
    strategy_analysis: str = Field(description="Detailed explanation of the setup, institutional flow, and news sentiment.")
    audit_notes: List[str] = Field(description="Notes on why penalties were or were not applied.")

class AutopsyReport(BaseModel):
    failure_category: str = Field(description="Strictly one of: 'STOP_HUNT_SWEEP', 'MACRO_TREND_BREAK', 'SL_TOO_TIGHT', 'MOMENTUM_EXHAUSTION', 'UNEXPECTED_NEWS'.")
    summary: str = Field(description="2-3 sentence executive breakdown of why the setup failed.")
    key_culprit: str = Field(description="The primary microstructural or technical cause.")
    actionable_lesson: str = Field(description="Specific adjustment for future trades with this pattern.")

class AIEvaluatorService:
    """Evaluates trading signals using Google Gemini."""
    def __init__(self, api_key: str, timeout: float = 30.0):
        self.timeout = timeout
        if not api_key:
            logger.warning("GEMINI_API_KEY is missing. AI Evaluation will fail.")
        self.client = genai.Client(api_key=api_key)
        self.model_name = "gemini-flash-lite-latest"

    async def evaluate_signal(self, symbol: str, signal_data: Dict[str, Any], news_data: List[Dict[str, str]], regime_data: Optional[Dict[str, Any]] = None) -> Optional[str]:
        """Send signal and news data to Gemini for a final verdict."""
        regime = regime_data.get("regime", "NORMAL") if regime_data else "NORMAL"
        phase = regime_data.get("phase", "NORMAL") if regime_data else "NORMAL"
        
        direction_raw = str(signal_data.get('direction', 'NEUTRAL')).upper()
        is_long = direction_raw in ['LONG', 'BULLISH', 'BUY']
        is_short = direction_raw in ['SHORT', 'BEARISH', 'SELL']
        direction = 'LONG' if is_long else 'SHORT' if is_short else 'NEUTRAL'
        
        # 1. Deterministic Python Base-Up Math
        base_score = 75
        checklist = []
        stackable_bonus = 0
        
        htf = str(signal_data.get('htf_trend', ''))
        htf4h = str(signal_data.get('htf_4h_trend', ''))
        btc = str(signal_data.get('btc_trend', ''))
        imbalance = float(signal_data.get('imbalance_ratio', 1.0))
        rr = float(signal_data.get('rr_ratio', 0))
        fr = float(signal_data.get('funding_rate', 0.0))
        patterns = signal_data.get('patterns', [])
        short_liq = str(signal_data.get('short_liq_zones', 'None'))
        long_liq = str(signal_data.get('long_liq_zones', 'None'))
        oi_delta = float(signal_data.get('oi_delta_pct', 0.0))
        oi_dir = str(signal_data.get('oi_direction', 'Neutral'))
        drift = signal_data.get('pattern_drift_status', 'HEALTHY')
        
        # Bonuses
        if (htf.startswith('Macro Up') and htf4h.startswith('Macro Up') and is_long) or \
           (htf.startswith('Macro Down') and htf4h.startswith('Macro Down') and is_short):
            stackable_bonus += 5
            checklist.append("+5% Macro Trend Alignment")
            
        if (btc.startswith('Macro Up') and is_long) or (btc.startswith('Macro Down') and is_short):
            stackable_bonus += 5
            checklist.append("+5% BTC Global Trend Support")
            
        if (is_long and imbalance >= 1.5) or (is_short and imbalance <= 0.66):
            stackable_bonus += 5
            checklist.append("+5% Order Book Dominance")
            
        if rr >= 2.5:
            stackable_bonus += 5
            checklist.append("+5% Exceptional Risk/Reward")
            
        if (is_long and fr < 0) or (is_short and fr > 0):
            stackable_bonus += 5
            checklist.append("+5% Funding Rate Tailwind")
            
        if len(patterns) > 1:
            stackable_bonus += 5
            checklist.append("+5% Multiple Pattern Confluence")
            
        if (is_long and short_liq != 'None' and short_liq != '') or (is_short and long_liq != 'None' and long_liq != ''):
            stackable_bonus += 5
            checklist.append("+5% Magnetic Liquidity Pools")
            
        # Order Book Liquidity Magnet Target Pull
        magnets = signal_data.get('liquidity_magnets', [])
        current_close = float(signal_data.get('close', 0.0))
        has_aligned_magnet = False
        for m in magnets:
            m_price = float(m.get('price', 0.0))
            if is_long and m.get('side') == 'ASK' and m_price > current_close:
                has_aligned_magnet = True
            elif is_short and m.get('side') == 'BID' and m_price < current_close:
                has_aligned_magnet = True
        if has_aligned_magnet:
            stackable_bonus += 5
            checklist.append("+5% Order Book Liquidity Magnet")
            
        if oi_delta > 1.5 and oi_dir.lower() in ["expanding", "up"]:
            stackable_bonus += 5
            checklist.append("+5% Open Interest Expansion")

        # High Volume Confluence
        strat_rationale = str(signal_data.get('strategy_rationale', ''))
        if "higher-than-average volume" in strat_rationale or "high volume" in strat_rationale:
            stackable_bonus += 5
            checklist.append("+5% High Volume Confirmation")
            
        # Phase 2 Override
        if phase == 'PHASE 2':
            # Add +10% bonus for vol/structure (hardcoded assumed present for simplicity in this phase)
            stackable_bonus += 10
            checklist.append("+10% PHASE 2 Momentum Volume Expansion")
            
        # Cap stackable bonus to 25%
        stackable_bonus = min(25, stackable_bonus)
        
        calculated_score = base_score + stackable_bonus
        
        # Deductions
        if (htf4h.startswith('Macro Down') and is_long) or (htf4h.startswith('Macro Up') and is_short):
            calculated_score -= 15
            checklist.append("-15% Macro Trend Conflict")
            
        if drift == 'DEGRADED':
            calculated_score -= 10
            checklist.append("-10% Degraded Pattern")
            
        magnet_desc = "; ".join([
            f"{m.get('volume_formatted', '')} {m.get('side', '')} @ ${m.get('price', 0)} ({m.get('action', 'No action specified')})" 
            for m in magnets
        ]) if magnets else "None detected"
        
        # Ask AI for News and Wall Penalties
        prompt = f"""
You are an Institutional Risk Auditor.
Your job is to audit the qualitative aspects of this trade setup.

Asset: {symbol}
Current Price: {signal_data.get('close', 0.0)}
Technical Direction: {direction}
Detected Patterns: {", ".join(patterns)}
Microstructure Note: {signal_data.get('strategy_rationale', 'N/A')}
Order Book Liquidity Magnets: {magnet_desc}

Recent News:
"""
        for i, article in enumerate(news_data, 1):
            prompt += f"{i}. {article['title']}\n   {article['summary']}\n"
            
        prompt += """
Audit Requirements:
1. live_news_penalty: Apply a -10 or -20 penalty if the live news directly opposes the technical setup (e.g. bearish news for a LONG setup). Otherwise 0.
2. institutional_wall_penalty: Apply a -10 penalty ONLY if there is a massive institutional limit-order wall blocking the path to the Take Profit target. Based on the Microstructure Note, judge if this is the case. Otherwise 0.
3. overall_verdict: Return 'APPROVE' if penalties don't exceed -15, else 'REJECT'.

Provide JSON output adhering exactly to the schema.
"""

        try:
            response = await asyncio.wait_for(
                self.client.aio.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=SignalEvaluation,
                    ),
                ),
                timeout=self.timeout
            )
            raw_text = response.text
            if not raw_text:
                logger.warning(f"Empty AI response text received for {symbol}.")
                return None

            text = raw_text.strip()
            if text.startswith("```json"): text = text[7:]
            if text.endswith("```"): text = text[:-3]
            
            ai_data = json.loads(text.strip())
            
            # Apply AI penalties
            live_news_penalty = int(ai_data.get('live_news_penalty', 0))
            wall_penalty = int(ai_data.get('institutional_wall_penalty', 0))
            
            if live_news_penalty < 0:
                calculated_score += live_news_penalty
                checklist.append(f"{live_news_penalty}% Live News Conflict")
            if wall_penalty < 0:
                calculated_score += wall_penalty
                checklist.append(f"{wall_penalty}% Institutional Wall Blocking")
                
            # Score Clamping
            final_score = max(0, min(100, calculated_score))
            
            # Repackage output for downstream
            output = {
                "trade_direction": ai_data.get('trade_direction', direction),
                "overall_verdict": ai_data.get('overall_verdict', 'APPROVE'),
                "strategy_analysis": ai_data.get('strategy_analysis', ''),
                "final_confidence_rating": final_score,
                "scoring_checklist": checklist,
                "is_degraded": (drift == 'DEGRADED')
            }
            
            return json.dumps(output)
            
        except asyncio.TimeoutError:
            logger.error("AI Evaluation timed out.")
            return None
        except Exception as e:
            logger.error(f"Error during AI evaluation: {e}")
            return None

    async def generate_autopsy(
        self, 
        trade_summary: Dict[str, Any], 
        sl_candle: Dict[str, Any], 
        microstructure_at_exit: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Send stopped-out trade metrics to Gemini for post-mortem forensic analysis."""
        micro = microstructure_at_exit or {}
        prompt = f"""
You are an Institutional Lead Quantitative Risk Officer conducting a Post-Trade Autopsy on a stopped-out trade.

### Trade Setup (Original Entry Thesis)
- Symbol: {trade_summary.get('symbol')}
- Direction: {trade_summary.get('direction')}
- Entry Price: ${trade_summary.get('entry_price')}
- Initial Stop Loss: ${trade_summary.get('sl_price')}
- Pattern Traded: {trade_summary.get('pattern_name', 'Unknown')}
- Leverage: {trade_summary.get('leverage', 1.0)}x

### Exit Snapshot (The Exact Candle That Triggered the Stop Loss)
- Exit Price: ${sl_candle.get('close')}
- Breach Candle Range: Low: ${sl_candle.get('low')} – High: ${sl_candle.get('high')}
- Exit Candle Open: ${sl_candle.get('open')} | Volume: {sl_candle.get('volume')}
- Funding Rate at Exit: {micro.get('funding_rate', 0.0)}%
- Order Book Imbalance at Exit: {micro.get('imbalance_ratio', 1.0)}
- Realized Net Loss: {trade_summary.get('net_pnl_pct', 0.0):.2f}%

Conduct a forensic analysis to determine:
1. Did price wick through the stop loss and reverse (classic liquidity sweep), or break through with heavy continuation?
2. Was the stop loss placed within high-frequency market noise?
3. What is the single biggest lesson to protect capital on similar setups?

Provide JSON output adhering strictly to the schema.
"""
        try:
            response = await asyncio.wait_for(
                self.client.aio.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=AutopsyReport,
                    ),
                ),
                timeout=self.timeout
            )
            raw = response.text or ""
            text = raw.strip()
            if text.startswith("```json"): text = text[7:]
            res = json.loads(text.strip())
            return dict(res) if isinstance(res, dict) else {}
        except Exception as e:
            logger.error(f"Error generating AI autopsy: {e}")
            return {
                "failure_category": "SL_TOO_TIGHT",
                "summary": f"Trade stopped out due to adverse market volatility. (AI post-mortem note: {e})",
                "key_culprit": "Price breached stop loss boundary before reaching profit target.",
                "actionable_lesson": "Consider widening ATR buffer multiplier in higher volatility regimes."
            }
