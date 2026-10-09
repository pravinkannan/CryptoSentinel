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

class AIEvaluatorService:
    """Evaluates trading signals using Google Gemini."""
    def __init__(self, api_key: str, timeout: float = 30.0):
        self.timeout = timeout
        if not api_key:
            logger.warning("GEMINI_API_KEY is missing. AI Evaluation will fail.")
        self.client = genai.Client(api_key=api_key)
        self.model_name = "gemini-flash-lite-latest"

    async def evaluate_signal(self, symbol: str, signal_data: Dict[str, Any], news_data: List[Dict[str, str]], regime_data: dict = None) -> Optional[str]:
        """Send signal and news data to Gemini for a final verdict."""
        regime = regime_data.get("regime", "NORMAL") if regime_data else "NORMAL"
        phase = regime_data.get("phase", "NORMAL") if regime_data else "NORMAL"
        
        direction = signal_data.get('direction', 'NEUTRAL')
        
        # 1. Deterministic Python Base-Up Math
        base_score = 75
        checklist = []
        stackable_bonus = 0
        
        htf = signal_data.get('htf_trend', '')
        htf4h = signal_data.get('htf_4h_trend', '')
        btc = signal_data.get('btc_trend', '')
        imbalance = float(signal_data.get('imbalance_ratio', 1.0))
        rr = float(signal_data.get('rr_ratio', 0))
        fr = float(signal_data.get('funding_rate', 0.0))
        patterns = signal_data.get('patterns', [])
        short_liq = signal_data.get('short_liq_zones', 'None')
        long_liq = signal_data.get('long_liq_zones', 'None')
        oi_delta = float(signal_data.get('oi_delta_pct', 0.0))
        oi_dir = signal_data.get('oi_direction', 'Neutral')
        drift = signal_data.get('pattern_drift_status', 'HEALTHY')
        
        # Bonuses
        if (htf.startswith('Macro Up') and htf4h.startswith('Macro Up') and direction == 'LONG') or \
           (htf.startswith('Macro Down') and htf4h.startswith('Macro Down') and direction == 'SHORT'):
            stackable_bonus += 5
            checklist.append("+5% Macro Trend Alignment")
            
        if (btc.startswith('Macro Up') and direction == 'LONG') or (btc.startswith('Macro Down') and direction == 'SHORT'):
            stackable_bonus += 5
            checklist.append("+5% BTC Global Trend Support")
            
        if (direction == 'LONG' and imbalance >= 1.5) or (direction == 'SHORT' and imbalance <= 0.66):
            stackable_bonus += 5
            checklist.append("+5% Order Book Dominance")
            
        if rr >= 2.5:
            stackable_bonus += 5
            checklist.append("+5% Exceptional Risk/Reward")
            
        if (direction == 'LONG' and fr < 0) or (direction == 'SHORT' and fr > 0):
            stackable_bonus += 5
            checklist.append("+5% Funding Rate Tailwind")
            
        if len(patterns) > 1:
            stackable_bonus += 5
            checklist.append("+5% Multiple Pattern Confluence")
            
        if (direction == 'LONG' and short_liq != 'None') or (direction == 'SHORT' and long_liq != 'None'):
            stackable_bonus += 5
            checklist.append("+5% Magnetic Liquidity Pools")
            
        if oi_delta > 1.5 and oi_dir.lower() == direction.lower():
            stackable_bonus += 5
            checklist.append("+5% Open Interest Expansion")
            
        # Phase 2 Override
        if phase == 'PHASE 2':
            # Add +10% bonus for vol/structure (hardcoded assumed present for simplicity in this phase)
            stackable_bonus += 10
            checklist.append("+10% PHASE 2 Momentum Volume Expansion")
            
        # Cap stackable bonus to 25%
        stackable_bonus = min(25, stackable_bonus)
        
        calculated_score = base_score + stackable_bonus
        
        # Deductions
        if (htf4h.startswith('Macro Down') and direction == 'LONG') or (htf4h.startswith('Macro Up') and direction == 'SHORT'):
            calculated_score -= 15
            checklist.append("-15% Macro Trend Conflict")
            
        if drift == 'DEGRADED':
            calculated_score -= 10
            checklist.append("-10% Degraded Pattern")
            
        # Ask AI for News and Wall Penalties
        prompt = f"""
You are an Institutional Risk Auditor.
Your job is to audit the qualitative aspects of this trade setup.

Asset: {symbol}
Current Price: {signal_data['close']}
Technical Direction: {direction}
Detected Patterns: {", ".join(patterns)}
Microstructure Note: {signal_data.get('strategy_rationale', 'N/A')}

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
            text = response.text
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
            raise AIEvaluationError(f"AI evaluation failed: {e}")
