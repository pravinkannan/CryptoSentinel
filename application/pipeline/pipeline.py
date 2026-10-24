"""
Composable Pipeline Architecture for Crypto Sentinel.
Defines IPipelineStep protocol, SignalContext data carrier, and the SignalPipeline executor.
"""

from typing import Protocol, Optional, List, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime, timezone
import pandas as pd
import logging

logger = logging.getLogger(__name__)


@dataclass
class SignalContext:
    """Carries symbol state, market data, analysis, and execution payloads through the pipeline."""
    symbol: str
    timeframe: str = "5m"
    now_utc: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    raw_candles: Dict[str, pd.DataFrame] = field(default_factory=dict)
    current_price: float = 0.0
    htf_trend: str = "Unknown"
    htf_4h_trend: str = "Unknown"
    regime_data: Dict[str, Any] = field(default_factory=dict)
    microstructure: Dict[str, Any] = field(default_factory=dict)
    news_data: List[Dict[str, Any]] = field(default_factory=list)
    btc_trend: str = "Unknown"
    circuit_breaker_active: bool = False
    signal_data: Optional[Dict[str, Any]] = None
    confluence_result: Optional[Dict[str, Any]] = None
    evaluated_signal: Optional[Dict[str, Any]] = None
    sized_order: Optional[Dict[str, Any]] = None
    trade_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class IPipelineStep(Protocol):
    """Protocol for discrete, composable steps in the signal processing pipeline."""
    async def process(self, context: SignalContext) -> Optional[SignalContext]:
        """
        Process the given signal context.
        Returns the updated context to proceed to the next step, or None to halt the pipeline.
        """
        ...


class SignalPipeline:
    """Executes a sequential chain of IPipelineSteps."""

    def __init__(self, steps: Optional[List[IPipelineStep]] = None) -> None:
        self.steps: List[IPipelineStep] = steps or []

    def add_step(self, step: IPipelineStep) -> "SignalPipeline":
        """Add a step to the pipeline chain."""
        self.steps.append(step)
        return self

    async def execute(self, context: SignalContext) -> Optional[SignalContext]:
        """
        Sequentially executes each step on the SignalContext.
        If any step returns None, pipeline execution terminates cleanly for that signal.
        """
        current_ctx: Optional[SignalContext] = context
        for step in self.steps:
            if current_ctx is None:
                return None
            step_name = getattr(step, '__class__', type(step)).__name__
            try:
                current_ctx = await step.process(current_ctx)
                if current_ctx is None:
                    logger.debug(f"Pipeline stopped at step: {step_name} for symbol {context.symbol}")
                    return None
            except Exception as e:
                logger.error(f"Pipeline step {step_name} encountered error for {context.symbol}: {e}", exc_info=True)
                return None
        return current_ctx
