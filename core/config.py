"""
Strictly typed configuration module using pydantic-settings.
"""

import logging
from typing import List, Dict
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    """
    Core settings loaded from environment variables and .env file.
    Validates types strictly on startup.
    """
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    gemini_api_key: str = Field(default="", description="Google Gemini API Key")
    telegram_bot_token: str = Field(default="", description="Telegram Bot Token")
    telegram_chat_id: str = Field(default="", description="Telegram Chat ID")
    cryptopanic_api_key: str = Field(default="", description="CryptoPanic API Key")
    discord_webhook_url: str = Field(default="", description="Discord Webhook URL")

    symbols: List[str] = Field(default=['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'PAXG/USDT', 'ZEC/USDT'])
    timeframes: List[str] = Field(default=['5m', '15m', '1h', '4h'])
    exchange: str = Field(default='binance')
    poll_interval_minutes: int = Field(default=5)
    
    percentage_move_trigger: float = Field(default=0.5)
    atr_period: int = Field(default=14, description="Period for ATR calculation")
    sl_atr_multiplier: float = Field(default=2.0, description="ATR multiplier for stop loss buffer")
    target_rr_ratio: float = Field(default=2.5, description="Target Risk:Reward ratio")
    static_alerts: Dict[str, List[float]] = Field(default={
        'BTC/USDT': [81000.0, 81500.0, 82000.0, 82500.0, 83000.0],
        'ETH/USDT': [2550.0, 2575.0, 2600.0, 2625.0, 2650.0],
        'SOL/USDT': [115.0, 118.0, 120.0, 122.0, 125.0],
        'PAXG/USDT': [4100.0, 4150.0, 4200.0, 4250.0],
        'ZEC/USDT': [1500.0, 1550.0, 1600.0, 1650.0]
    })
    
    rss_feeds: List[str] = Field(default=[
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "https://cointelegraph.com/rss",
        "https://decrypt.co/feed"
    ])
    
    enable_circuit_breaker: bool = Field(default=False, description="Enable automated drawdown circuit breaker")
    enable_dynamic_level_alerts: bool = Field(default=False, description="Enable notification alerts when price crosses dynamic support/resistance levels")
    dynamic_level_cooldown_minutes: int = Field(default=120, description="Minimum cooldown minutes before repeating an alert for the same dynamic level")
    verbose_telegram_signals: bool = Field(default=False, description="Include detailed technical market context, checklists, and order book walls in Telegram alerts")
    max_drawdown_percent: float = Field(default=5.0)
    llm_timeout_seconds: float = Field(default=30.0)

    # Magic Numbers / Thresholds Extracted
    emergency_guard_leverage_threshold: float = Field(default=10.0, description="Minimum leverage to trigger emergency guard")
    emergency_guard_pnl_threshold: float = Field(default=0.1, description="Minimum PnL pct to move SL to BE in emergency")
    
    tp1_rr_ratio: float = Field(default=1.5, description="Risk/Reward ratio for TP1")
    tp2_rr_ratio: float = Field(default=3.0, description="Risk/Reward ratio for TP2")
    
    sl_pct_max_high_volatility: float = Field(default=3.50, description="Max SL pct in high volatility")
    sl_pct_min_high_volatility: float = Field(default=1.0, description="Min SL pct in high volatility")
    
    sl_pct_max_normal: float = Field(default=2.50, description="Max SL pct in normal conditions")
    sl_pct_min_normal: float = Field(default=0.40, description="Min SL pct in normal conditions")
    
    min_acceptable_rr: float = Field(default=1.5, description="Minimum overall R:R acceptable to dispatch")
    
    pattern_drift_win_rate_threshold: float = Field(default=40.0, description="Win rate under which pattern is degraded")
    pattern_drift_evaluation_window: int = Field(default=20, description="Number of past trades to evaluate pattern drift")

    trade_ttl_hours: float = Field(default=18.0, description="Default trade expiration window / TTL in hours")
    trade_ttl_minutes: int = Field(default=1080, description="Default trade expiration window / TTL in minutes (18 hours = 1080m)")
    vip_confidence_threshold: int = Field(default=90, description="Minimum confidence score for VIP tier classification")
    min_dispatch_confidence: int = Field(default=80, description="Minimum confidence score required to dispatch a signal")
    liquidity_magnet_depth_pct: float = Field(default=0.02, description="Top depth pct of order book to scan for liquidity magnets")
    liquidity_magnet_multiplier: float = Field(default=3.0, description="Volume multiplier threshold for magnet cluster anomaly")

settings = Settings()

# Setup root logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
