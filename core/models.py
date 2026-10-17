"""
Enterprise Domain Models and Typed Schemas for Crypto Sentinel.
"""

from typing import List, Optional, Dict, Any, Union
from pydantic import BaseModel, Field, ConfigDict


class RegimeData(BaseModel):
    """Market regime classification snapshot."""
    model_config = ConfigDict(extra='ignore')
    
    regime: str = Field(default="NORMAL", description="Current market volatility regime: NORMAL or HIGH_VOLATILITY")
    phase: str = Field(default="PHASE 0", description="Trend phase classification")
    volatility_score: float = Field(default=0.0, description="Normalized volatility score")
    cooldown_active: bool = Field(default=False, description="Whether whipsaw cooldown buffer is active")
    catalyst_info: str = Field(default="", description="Information regarding prevailing macro catalyst")


class LiquidityMagnet(BaseModel):
    """Institutional Order Book Liquidity Pool."""
    model_config = ConfigDict(extra='ignore')
    
    side: str = Field(description="Order side: BID or ASK")
    price: float = Field(description="Price level of liquidity cluster")
    volume: float = Field(description="Total token volume at cluster")
    volume_formatted: str = Field(description="Human readable volume (e.g. 1.2M)")
    multiple: float = Field(description="Multiple of average baseline depth")
    action: Optional[str] = Field(default=None, description="Tactical action recommendation")
    role: Optional[str] = Field(default=None, description="Structural role: DEFENSIVE_SHIELD, BLOCKING_RESISTANCE, etc.")


class EvalBreakdown(BaseModel):
    """Parametric factor breakdown for Stage 2 confluence evaluation."""
    model_config = ConfigDict(extra='ignore')
    
    volume_ratio: float = 1.0
    directional_aligned: bool = True
    key_level_rejection: bool = False
    dist_to_key_level_pct: float = 0.0
    vwap_aligned: bool = False
    imbalance_ratio: float = 1.0
    total_book_depth_usd: float = 0.0
    ob_defensive_shield: bool = False
    macro_trend_aligned: bool = False
    btc_aligned: bool = False
    funding_rate_tailwind: bool = False
    oi_delta_15m: float = 0.0
    rsi_14: float = 50.0
    rsi_momentum_room: bool = False
    rsi_divergence: bool = False
    target_magnet: bool = False
    blocking_ob_wall: bool = False
    has_sweep_risk: bool = False
    friction_absorbed: bool = False


class EvalTrace(BaseModel):
    """Forensic audit trace of the Two-Stage Parametric Confluence Engine."""
    model_config = ConfigDict(extra='ignore')
    
    stage1_passed: bool = True
    circuit_breaker: Optional[str] = None
    base_score: float = 64.0
    vol_points: float = 0.0
    level_points: float = 0.0
    vwap_points: float = 0.0
    ob_points: float = 0.0
    trend_tailwinds: float = 0.0
    catalyst_points: float = 0.0
    friction: float = 0.0
    net_score: float = 64.0
    final_score: float = 64.0
    tier: str = "REJECTED"
    vip_capped: bool = False
    has_sweep_risk: bool = False
    macro_trend_aligned: bool = False
    book_depth: float = 0.0
    breakdown: Optional[Union[EvalBreakdown, Dict[str, Any]]] = None
    qualitative_penalties: Optional[Dict[str, int]] = None


class SignalData(BaseModel):
    """Institutional Trade Signal Data Payload."""
    model_config = ConfigDict(extra='ignore')
    
    timestamp: str
    timeframe: str
    htf_trend: str
    htf_4h_trend: str = "Unknown"
    btc_trend: str
    close: float
    patterns: List[str]
    direction: str
    strategy_rationale: str
    entry: float
    stop_loss: float
    take_profit: float
    tp1: float
    tp2: float
    sl_pct: float
    tp_pct: float
    tp1_pct: float
    tp2_pct: float
    rr_ratio: float
    roe_sl: float
    roe_tp: float
    roe_tp1: float = 0.0
    roe_tp2: float = 0.0
    overall_tp2_roe: float = 0.0
    atr_value: float
    target_resolution: str
    ttl_mins: int
    expiration_time: str
    recommended_leverage: str
    leverage_val: float
    short_liq_zones: str
    long_liq_zones: str
    funding_rate: float
    imbalance_ratio: float
    catalyst_info: str = ""
    oi_delta_pct: float = 0.0
    oi_direction: str = "Neutral"
    pattern_drift_status: str = "HEALTHY"
    regime: str = "NORMAL"
    phase: str = "NORMAL"
    dispatch_channels: List[str] = Field(default_factory=list)
    trade_id: int = 0
    reversal_notice: str = ""
    liquidity_magnets: List[Dict[str, Any]] = Field(default_factory=list)
    rsi_val: float = 50.0
    volume_ratio: float = 1.0
    volume_usd: float = 0.0
    dynamic_level_reaction: bool = False
    bb_bounce: bool = False
    open: float = 0.0
    vol_sma_20: float = 0.0
    volume: float = 0.0
    key_level_rejection: bool = False
    dist_to_key_level_pct: float = 0.0
    total_book_depth_usd: float = 150000.0
    ob_defensive_shield: bool = False
    macro_trend_aligned: bool = False
    macro_4h_conflict: bool = False
    blocking_ob_wall: bool = False
    has_sweep_risk: bool = False
    vwap: float = 0.0
    vwap_aligned: bool = False
    btc_trend_aligned: bool = False
    rsi_divergence: bool = False
    target_magnet_present: bool = False
    is_squeezed: bool = False
    setup_type: str = "STANDARD_SWING"
    breakeven_price: float = 0.0
    eval_trace: Dict[str, Any] = Field(default_factory=dict)

