from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

class RegimeData(BaseModel):
    regime: str = Field(default="NORMAL")
    phase: str = Field(default="PHASE 0")
    volatility_score: float = Field(default=0.0)
    cooldown_active: bool = Field(default=False)
    catalyst_info: str = Field(default="")

class SignalData(BaseModel):
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
    dispatch_channels: List[str] = Field(default_factory=list)
    trade_id: int = 0
    reversal_notice: str = ""
    liquidity_magnets: List[Dict[str, Any]] = Field(default_factory=list)
