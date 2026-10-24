"""
Infrastructure LLM Evaluator for Gemini.
Handles Gemini API calls, structured schemas, fallback cascades, prompt templating,
and qualitative narrative validation (news and institutional wall auditing).
"""

import logging
import json
import asyncio
from typing import Optional, Dict, List, Any
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from domain.confluence.engine import (
    evaluate_confluence_score,
    STAGE3_VIP_THRESHOLD,
    STAGE3_STANDARD_THRESHOLD,
)

logger = logging.getLogger(__name__)
logging.getLogger("google_genai.models").setLevel(logging.ERROR)


# ==============================================================================
# PYDANTIC RESPONSE SCHEMAS
# ==============================================================================

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


# ==============================================================================
# GEMINI EVALUATOR SERVICE
# ==============================================================================

class GeminiEvaluator:
    """Evaluates trading signals with institutional confluence scoring and Gemini auditing."""

    def __init__(
        self, 
        api_key: str, 
        model_name: str = 'gemini-3.5-flash-lite', 
        timeout: float = 8.0,
        fallback_models: Optional[List[str]] = None
    ) -> None:
        self.api_key: str = api_key
        self.model_name: str = model_name
        self.timeout: float = timeout
        self.fallback_models: List[str] = fallback_models if fallback_models is not None else [
            'gemini-flash-lite-latest',
            'gemini-3.1-flash-lite'
        ]
        self.client = genai.Client(api_key=self.api_key)

    def evaluate_confluence_score(self, symbol: str, signal_data: Dict[str, Any]) -> Dict[str, Any]:
        """Bound instance helper for evaluate_confluence_score from domain engine."""
        return evaluate_confluence_score(symbol, signal_data)

    async def evaluate_signal(
        self, 
        symbol: str, 
        signal_data: Dict[str, Any], 
        news_data: Optional[List[Dict[str, Any]]] = None, 
        regime_data: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """
        Executes the Two-Stage Parametric Confluence Engine and overlays qualitative
        LLM analysis (live news & institutional limit-order wall auditing).
        """
        news_data = news_data or []
        direction_raw = str(signal_data.get('direction', 'NEUTRAL')).upper()
        is_long = direction_raw in ['LONG', 'BULLISH', 'BUY']
        is_short = direction_raw in ['SHORT', 'BEARISH', 'SELL']
        direction = 'LONG' if is_long else 'SHORT' if is_short else 'NEUTRAL'

        # 1. Deterministic Two-Stage Parametric Confluence Engine
        score_result = self.evaluate_confluence_score(symbol, signal_data)
        
        # If Circuit Breaker triggered in Stage 1, fail immediately without LLM call (Ghost Scoring preserves actual score)
        if not score_result["eval_trace"].get("stage1_passed", True) or score_result.get("circuit_breaker_tripped", False):
            output = {
                "trade_direction": direction,
                "overall_verdict": "REJECT",
                "strategy_analysis": f"Circuit breaker tripped in Stage 1: {score_result['rejection_reason']}",
                "final_confidence_rating": score_result["final_score"],
                "tier": "REJECTED",
                "scoring_checklist": score_result["scoring_checklist"],
                "eval_trace": score_result["eval_trace"],
                "is_degraded": False
            }
            return json.dumps(output)

        patterns = signal_data.get('patterns', [])
        magnets = signal_data.get('liquidity_magnets', [])
        magnet_desc = "; ".join([
            f"{m.get('volume_formatted', '')} {m.get('side', '')} @ ${m.get('price', 0)} ({m.get('action', 'No action specified')})" 
            for m in magnets
        ]) if magnets else "None detected"
        
        # Ask AI for Qualitative News and Wall Auditing
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

        models_to_try = [self.model_name] + [m for m in self.fallback_models if m != self.model_name]
        ai_data = None

        for current_model in models_to_try:
            try:
                response = await asyncio.wait_for(
                    self.client.aio.models.generate_content(
                        model=current_model,
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
                    logger.warning(f"Empty AI response text received from {current_model} for {symbol}.")
                    continue

                text = raw_text.strip()
                if text.startswith("```json"): text = text[7:]
                if text.endswith("```"): text = text[:-3]
                
                ai_data = json.loads(text.strip())
                ai_data['eval_model'] = current_model
                break
            except asyncio.TimeoutError:
                logger.warning(f"AI evaluation timed out for model {current_model}.")
            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    logger.warning(f"Gemini quota/rate limit exhausted for model {current_model} (429). Attempting fallback...")
                elif "503" in err_str or "UNAVAILABLE" in err_str:
                    logger.warning(f"Gemini model {current_model} temporarily unavailable (503). Attempting fallback...")
                else:
                    logger.warning(f"AI evaluation failed for model {current_model}: {e}")

        # If an AI response was successfully obtained:
        if ai_data is not None:
            # Apply Qualitative AI adjustments to parametric score
            live_news_penalty = int(ai_data.get('live_news_penalty', 0))
            wall_penalty = int(ai_data.get('institutional_wall_penalty', 0))
            
            adjusted_score = score_result["final_score"]
            checklist = list(score_result["scoring_checklist"])
            
            if live_news_penalty < 0:
                adjusted_score += live_news_penalty
                checklist.append(f"{live_news_penalty}% Live News Conflict")
            if wall_penalty < 0 and not score_result["eval_trace"]["breakdown"].get("blocking_ob_wall", False):
                adjusted_score += wall_penalty
                checklist.append(f"{wall_penalty}% Institutional Wall Blocking")
                
            final_score = round(max(0.0, min(100.0, adjusted_score)), 1)
            
            # Tier reassessment post-AI review
            if final_score >= STAGE3_VIP_THRESHOLD and score_result["tier"] == "VIP":
                tier = "VIP"
            elif final_score >= STAGE3_STANDARD_THRESHOLD:
                tier = "STANDARD"
            else:
                tier = "REJECTED"
                
            eval_trace = dict(score_result["eval_trace"])
            eval_trace["final_score"] = final_score
            eval_trace["tier"] = tier
            eval_trace["qualitative_penalties"] = {
                "live_news_penalty": live_news_penalty,
                "wall_penalty": wall_penalty
            }

            output = {
                "trade_direction": ai_data.get('trade_direction', direction),
                "overall_verdict": ai_data.get('overall_verdict', 'APPROVE'),
                "strategy_analysis": ai_data.get('strategy_analysis', ''),
                "final_confidence_rating": final_score,
                "tier": tier,
                "scoring_checklist": checklist,
                "eval_trace": eval_trace,
                "is_degraded": (signal_data.get('pattern_drift_status') == 'DEGRADED')
            }
            return json.dumps(output)

        # Fallback: All AI models failed (Quota exhausted, service unavailable, or offline)
        # Preserve quantitative parametric confluence score so valid setups aren't dropped!
        logger.info(f"Preserving quantitative parametric confluence for {symbol} (AI auditing offline/exhausted).")
        checklist = list(score_result["scoring_checklist"])
        checklist.append("⚠️ Parametric Preserved: AI audit bypassed (quota/offline)")
        
        output = {
            "trade_direction": direction,
            "overall_verdict": "APPROVE" if score_result["tier"] in ["VIP", "STANDARD"] else "REJECT",
            "strategy_analysis": (
                f"Quantitative Parametric Confluence score: {score_result['final_score']}%. "
                f"Tier: {score_result['tier']}. Evaluated via deterministic parametric confluence engine."
            ),
            "final_confidence_rating": score_result["final_score"],
            "tier": score_result["tier"],
            "scoring_checklist": checklist,
            "eval_trace": score_result["eval_trace"],
            "is_degraded": (signal_data.get('pattern_drift_status') == 'DEGRADED')
        }
        return json.dumps(output)

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
        models_to_try = [self.model_name] + [m for m in self.fallback_models if m != self.model_name]
        for current_model in models_to_try:
            try:
                response = await asyncio.wait_for(
                    self.client.aio.models.generate_content(
                        model=current_model,
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
                if text.endswith("```"): text = text[:-3]
                res = json.loads(text.strip())
                if isinstance(res, dict) and "failure_category" in res:
                    return dict(res)
            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    logger.warning(f"Gemini quota exhausted for autopsy with {current_model}. Attempting fallback...")
                else:
                    logger.warning(f"AI autopsy failed with {current_model}: {e}")

        logger.info(f"Generating deterministic autopsy for trade {trade_summary.get('trade_id', 'unknown')}.")
        return {
            "failure_category": "SL_TOO_TIGHT",
            "summary": "Trade stopped out due to adverse market volatility. Stop loss boundary breached before target.",
            "key_culprit": "Price breached stop loss boundary before reaching profit target.",
            "actionable_lesson": "Consider widening ATR buffer multiplier in higher volatility regimes."
        }


# Alias for backward-compatibility
AIEvaluatorService = GeminiEvaluator
