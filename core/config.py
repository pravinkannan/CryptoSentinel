"""
Strictly typed configuration module using pydantic-settings.
"""

import logging
from typing import List, Dict, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator

class Settings(BaseSettings):
    """
    Core settings loaded from environment variables and .env file.
    Validates types strictly on startup.
    """
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    gemini_api_key: str = Field(default="", description="Google Gemini API Key")
    gemini_model_name: str = Field(default="gemini-3.5-flash-lite", description="Google Gemini primary model identifier")
    gemini_fallback_models: List[str] = Field(
        default=["gemini-flash-lite-latest", "gemini-3.1-flash-lite"],
        description="Google Gemini fallback models in case primary model quota is exhausted or rate-limited"
    )
    telegram_bot_token: str = Field(default="", description="Telegram Bot Token")
    telegram_chat_id: str = Field(default="", description="Telegram Chat ID")
    telegram_authorized_chat_ids: List[str] = Field(
        default_factory=list,
        description="Strict allow-list of authorized Telegram chat IDs permitted to execute commands"
    )
    cryptopanic_api_key: str = Field(default="", description="CryptoPanic API Key")
    discord_webhook_url: str = Field(default="", description="Discord Webhook URL")

    @field_validator('gemini_model_name')
    @classmethod
    def validate_gemini_model(cls, v: str) -> str:
        """Validate Gemini model name on startup; fail loudly if missing or invalid."""
        if not v or not isinstance(v, str) or not v.strip():
            raise ValueError("gemini_model_name cannot be empty. Must specify a valid Gemini model ID.")
        cleaned = v.strip()
        if not (cleaned.startswith("gemini-") or cleaned.startswith("models/gemini-")):
            raise ValueError(f"Invalid gemini_model_name '{cleaned}'. Model ID must begin with 'gemini-'.")
        return cleaned

    symbols: List[str] = Field(default=['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'XAU/USDT'])
    timeframes: List[str] = Field(default=['5m', '15m', '1h', '4h'])
    symbol_timeframes: Dict[str, List[str]] = Field(
        default={
            'BTC/USDT': ['5m', '15m'],
            'ETH/USDT': ['5m', '15m'],
            'SOL/USDT': ['5m', '15m'],
            'XAU/USDT': ['15m', '1h', '4h'],
        },
        description="Empirically calibrated approved champion timeframes per symbol based on walk-forward backtesting"
    )
    blacklisted_symbols: List[str] = Field(
        default=[],
        description="Symbols explicitly blacklisted from automated scanning due to negative expectancy"
    )
    exchange: str = Field(default='bybit')
    fallback_exchanges: List[str] = Field(default=['binance'], description="Ordered fallback exchanges if primary exchange fails")
    exchange_probe_cooldown_minutes: int = Field(default=60, description="Minutes to wait before probing a previously failed exchange")
    proxy_url: Optional[str] = Field(default=None, description="Optional HTTP/SOCKS proxy URL for CCXT and network requests")
    db_path: str = Field(default="data/sentinel.db", description="Path to unified SQLite sentinel database")
    state_filename: str = Field(default="sentinel_state.json", description="Path to state manager persistence file")
    poll_interval_minutes: int = Field(default=5)
    
    percentage_move_trigger: float = Field(default=0.5)
    atr_period: int = Field(default=14, description="Period for ATR calculation")
    sl_atr_multiplier: float = Field(default=2.0, description="ATR multiplier for stop loss buffer")
    target_rr_ratio: float = Field(default=2.5, description="Target Risk:Reward ratio")
    static_alerts: Dict[str, List[float]] = Field(
        default_factory=dict,
        description="Static price alert levels (empty by default; dynamic thresholds derived from market structure are used)"
    )
    
    rss_feeds: List[str] = Field(default=[
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "https://cointelegraph.com/rss",
        "https://decrypt.co/feed"
    ])
    
    enable_circuit_breaker: bool = Field(default=True, description="Enable automated drawdown circuit breaker")
    enable_dynamic_level_alerts: bool = Field(default=False, description="Enable notification alerts when price crosses dynamic support/resistance levels")
    dynamic_level_cooldown_minutes: int = Field(default=120, description="Minimum cooldown minutes before repeating an alert for the same dynamic level")
    verbose_telegram_signals: bool = Field(default=False, description="Include detailed technical market context, checklists, and order book walls in Telegram alerts")
    max_drawdown_percent: float = Field(default=5.0)
    risk_per_trade_percent: float = Field(default=2.0, description="Risk percentage of current account equity per trade for position sizing")
    position_size_percent: float = Field(default=10.0, description="Default capital allocation fraction per trade if SL distance is undefined")
    max_same_direction_positions: int = Field(default=5, description="Max concurrent active positions across portfolio in the same direction (0 = unlimited). Defaults to 5 to allow full multi-asset coverage.")
    allow_multi_timeframe_positions_per_symbol: bool = Field(default=False, description="Allow concurrent positions on different timeframes for the same symbol in harmonic trend alignment (defaults to False for strict 1-trade-per-symbol lock)")
    max_scalp_spread_pct_alt: float = Field(default=0.12, description="Maximum bid-ask spread % for altcoins on 5m/15m scalps (12 bps)")
    max_candle_staleness_seconds: int = Field(default=390, description="Maximum candle age in seconds before triggering stale data guard (6.5 mins)")
    adverse_funding_rate_short: float = Field(default=-0.06, description="Adverse funding rate threshold to filter SHORT setups (-0.06%)")
    adverse_funding_rate_long: float = Field(default=0.08, description="Adverse funding rate threshold to filter LONG setups (+0.08%)")
    llm_timeout_seconds: float = Field(default=30.0)

    # Magic Numbers / Thresholds Extracted
    emergency_guard_leverage_threshold: float = Field(default=10.0, description="Minimum leverage to trigger emergency guard")
    emergency_guard_pnl_threshold: float = Field(default=0.1, description="Minimum PnL pct to move SL to BE in emergency")
    
    tp1_rr_ratio: float = Field(default=1.0, description="Risk/Reward ratio for TP1 (Rebalanced to 1.5R)")
    tp2_rr_ratio: float = Field(default=3.0, description="Risk/Reward ratio for TP2")
    be_trigger_rr_ratio: float = Field(default=0.5, description="Risk/Reward ratio to trigger moving Stop Loss to Breakeven (+1.0R delayed for crypto)")
    be_trigger_rr_ratio_commodity: float = Field(default=0.30, description="Risk/Reward ratio to trigger moving Stop Loss to Breakeven for commodities (Gold/PAXG +0.30R)")
    roe_risk_budget: float = Field(default=12.0, description="Target max ROE risk budget per trade for leverage sizing")
    standard_confidence_threshold: float = Field(default=78.0, description="Minimum confidence score for Standard tier dispatch (Quality Filter)")
    min_friction_to_target_ratio: float = Field(default=2.0, description="Pre-execution spread & tax filter: Target Distance % / Execution Tax % must be >= 4.0")
    momentum_decay_bars: int = Field(default=999, description="Max bars allowed in trade before evaluating Smart Stagnation Guard (30-40 mins on 5m)")
    stagnation_r_threshold: float = Field(default=0.15, description="Unrealized R:R threshold for stagnation filter escape velocity at bar 8")
    vip_macro_adx_threshold: float = Field(default=25.0, description="Minimum 4H ADX trend strength for 5m/15m setups to qualify for VIP tier (> 25.0)")
    vip_require_4h_adx_alignment: bool = Field(default=True, description="Enforce 4H ADX > 25 and +DI/-DI directional alignment for VIP tier on 5m/15m")
    
    sl_pct_max_high_volatility: float = Field(default=3.50, description="Max SL pct in high volatility")
    sl_pct_min_high_volatility: float = Field(default=1.0, description="Min SL pct in high volatility")
    
    sl_pct_max_normal: float = Field(default=2.50, description="Max SL pct in normal conditions")
    sl_pct_min_normal: float = Field(default=0.40, description="Min SL pct in normal conditions")
    
    min_acceptable_rr: float = Field(default=1.0, description="Minimum overall R:R acceptable to dispatch")
    
    pattern_drift_win_rate_threshold: float = Field(default=40.0, description="Win rate under which pattern is degraded")
    pattern_drift_evaluation_window: int = Field(default=20, description="Number of past trades to evaluate pattern drift")

    trade_ttl_hours: float = Field(default=18.0, description="Default trade expiration window / TTL in hours")
    trade_ttl_minutes: int = Field(default=1080, description="Default trade expiration window / TTL in minutes (18 hours = 1080m)")
    trade_ttl_hours_1h: float = Field(default=72.0, description="Trade expiration window / TTL in hours for 1h timeframe (3 days = 72 hours)")
    trade_ttl_hours_4h: float = Field(default=168.0, description="Trade expiration window / TTL in hours for 4h timeframe (7 days = 168 hours)")
    
    # Execution Friction & Live Spread Protection
    max_scalp_spread_pct: float = Field(default=0.08, description="Maximum bid-ask spread % to permit 5m/15m scalp execution (8 bps)")
    default_scalp_slippage_pct: float = Field(default=0.0010, description="Conservative slippage estimate (10 bps = 0.10%) for 5m scalps")
    vip_confidence_threshold: float = Field(default=89.0, description="Minimum confidence score for VIP tier classification")
    min_dispatch_confidence: float = Field(default=85.5, description="Minimum confidence score required to dispatch a signal")
    min_dispatch_confidence_reversal: float = Field(default=75.0, description="Minimum confidence score for high-confluence structural reversals (Key Level + RSI Div)")
    near_miss_min_score: float = Field(default=75.0, description="Minimum confidence score to record near-miss rejected signals in db")
    liquidity_magnet_depth_pct: float = Field(default=0.02, description="Top depth pct of order book to scan for liquidity magnets")
    liquidity_magnet_multiplier: float = Field(default=3.0, description="Volume multiplier threshold for magnet cluster anomaly")

    # Graduated Bias-Flip & Threat Defensive Protocol
    defensive_threat_min_score: float = Field(default=80.0, description="Minimum score (80.0% - 87.9%) to trigger Threat Defensive Mode (ratchet active opposing trade SL to BE)")
    bias_flip_min_score: float = Field(default=88.0, description="Minimum score to execute a full bias flip against active opposing trades")
    bias_flip_min_volume_ratio: float = Field(default=2.0, description="Minimum volume surge ratio required for full bias flip")

    # Tiered Leverage & Execution Settings
    vip_max_leverage_btc: float = Field(default=60.0, description="Max leverage for VIP BTC setups")
    vip_max_leverage_eth: float = Field(default=40.0, description="Max leverage for VIP ETH setups")
    vip_max_leverage_alt: float = Field(default=20.0, description="Max leverage for VIP Altcoin setups")
    std_max_leverage_btc_eth: float = Field(default=25.0, description="Max leverage for Standard BTC/ETH setups")
    std_max_leverage_alt: float = Field(default=12.0, description="Max leverage for Standard Altcoin setups")
    breakeven_trigger_r_ratio: float = Field(default=1.0, description="R:R gain ratio to trigger auto-breakeven")
    tp1_close_ratio: float = Field(default=0.50, description="Position fraction to close at TP1 (50%)")

settings = Settings()
