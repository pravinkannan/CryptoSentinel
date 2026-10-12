"""
NotificationService manages Telegram and Discord alerts.
"""

import logging
import json
import aiohttp
from typing import Dict, Any, Optional
from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import Command

logger = logging.getLogger(__name__)

class NotificationService:
    """Handles external notifications for signals and static levels."""
    
    def __init__(self, telegram_token: str, telegram_chat_id: str, discord_webhook: str = ""):
        self.bot_token = telegram_token
        self.chat_id = telegram_chat_id
        self.discord_webhook = discord_webhook
        
        self.bot: Optional[Bot] = None
        self.dp = Dispatcher()
        
        if self.bot_token:
            self.bot = Bot(token=self.bot_token)
            self._register_handlers()

    def _register_handlers(self) -> None:
        """Register Telegram command handlers."""
        @self.dp.message(Command("status"))
        async def status_command(message: Message):
            await message.reply("Sentinel is running actively monitoring markets.")
        
        @self.dp.message(Command("ping"))
        async def ping_command(message: Message):
            await message.reply("Pong! Sentinel is online.")

    async def start_polling(self) -> None:
        """Start the aiogram bot polling loop."""
        if self.bot:
            logger.info("Starting Telegram Bot polling...")
            await self.dp.start_polling(self.bot)

    async def send_telegram(self, text: str, parse_mode: str = 'HTML') -> None:
        """Send a message to the configured Telegram chat."""
        if not self.bot or not self.chat_id:
            return
        try:
            await self.bot.send_message(chat_id=self.chat_id, text=text, parse_mode=parse_mode, disable_web_page_preview=True)
        except Exception as e:
            logger.error(f"Error sending Telegram message: {e}")

    async def send_discord(self, text: str) -> None:
        """Send a message via Discord Webhook."""
        if not self.discord_webhook:
            return
        clean_text = text.replace('<b>', '**').replace('</b>', '**')
        payload = {"content": clean_text}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.discord_webhook, json=payload) as response:
                    if response.status not in (200, 204):
                        logger.error(f"Failed to send Discord message: {response.status}")
        except Exception as e:
            logger.error(f"Error sending Discord message: {e}")

    async def notify_signal(self, symbol: str, signal_data: Dict[str, Any], ai_response_text: str, current_drawdown: float = 0.0) -> None:
        """Format and broadcast an evaluated trading signal."""
        try:
            ai_data = json.loads(ai_response_text)
        except Exception as e:
            logger.error(f"Failed to parse AI JSON response: {e}")
            ai_data = {}

        patterns_str = ", ".join(signal_data['patterns'])
        
        direction = signal_data.get('direction', 'Neutral')
        action_str = "LONG 🟢" if direction == "Bullish" else "SHORT 🔴" if direction == "Bearish" else "NEUTRAL ⚪"
        invalidation_dir = "below" if direction == "Bullish" else "above"
        
        entry = signal_data.get('entry', 0.0)
        entry_min = round(entry * 0.9995, max(2, len(str(entry).split('.')[-1])))
        entry_max = round(entry * 1.0005, max(2, len(str(entry).split('.')[-1])))
        
        confidence = ai_data.get('final_confidence_rating', 0)
        news_summary = ai_data.get('strategy_analysis', 'N/A')
        
        channels = signal_data.get('dispatch_channels', ["STANDARD"])
        regime = signal_data.get('regime', 'NORMAL')
        outcome = signal_data.get('outcome', '')
        
        if "SHADOW" in channels or regime == "SHADOW" or outcome == "SHADOW":
            logger.info(f"Silently ignoring broadcast for SHADOW trade: {symbol}")
            return
            
        channel_tags = []
        if "VIP" in channels:
            channel_tags.append("[👑 VIP EXCLUSIVE]")
        if "STANDARD_SPOTLIGHT" in channels:
            channel_tags.append("[⭐️ DAILY VIP SPOTLIGHT TRADE]")
        elif "STANDARD" in channels:
            channel_tags.append("[📈 STANDARD FEED]")
            
        tag_str = " ".join(channel_tags)
        regime = signal_data.get('regime', 'NORMAL')
        phase = signal_data.get('phase', '')
        phase_str = f" / {phase}" if phase else ""
        
        badge_str = ""
        if regime == "HIGH_VOLATILITY":
            badge_str = (
                f"⚡ SENTINEL MOMENTUM ALERT: {symbol} (Perp {signal_data.get('timeframe', '1h')})\n"
                f"🚨 REGIME: HIGH VOLATILITY{phase_str}\n━━━━━━━━━━━━━━━━━━━━━\n"
            )
        else:
            badge_str = (
                f"🚨 SENTINEL ALERT: {symbol} (Perp {signal_data.get('timeframe', '1h')})\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
            )
            
        catalyst_str = ""
        catalyst_info = signal_data.get('catalyst_info', '')
        if catalyst_info:
            catalyst_str = f"\n\n📰 Market Catalyst:\n• {catalyst_info}"
        
        tp1 = signal_data.get('tp1', signal_data.get('take_profit', 0.0))
        tp2 = signal_data.get('tp2', signal_data.get('take_profit', 0.0))
        tp1_pct = signal_data.get('tp1_pct', signal_data.get('tp_pct', 0.0))
        tp2_pct = signal_data.get('tp2_pct', signal_data.get('tp_pct', 0.0))
        
        rr_tp1 = round(tp1_pct / signal_data.get('sl_pct', 1.0), 1) if signal_data.get('sl_pct') else 0.0
        rr_tp2 = round(tp2_pct / signal_data.get('sl_pct', 1.0), 1) if signal_data.get('sl_pct') else 0.0

        tier_badge = " | 👑 VIP TIER" if confidence >= 85 else ""
        ttl_mins_val = signal_data.get('ttl_mins', 1080)
        ttl_display = f"{ttl_mins_val // 60}h" if ttl_mins_val % 60 == 0 else f"{ttl_mins_val} mins"
        msg = (
            f"{tag_str}\n"
            f"{badge_str}"
            f"Action: {action_str} | Confidence: {confidence}%{tier_badge}\n"
            f"Recommended Leverage: {signal_data.get('recommended_leverage', '1x')}\n"
            f"Entry: ${entry_min} – ${entry_max}\n"
            f"Stop Loss: ${signal_data.get('stop_loss', 0.0)} (Risk: <b>-{signal_data.get('sl_pct', 0.0)}%</b> | ROE: -{signal_data.get('roe_sl', 0.0)}%)\n"
            f"⏳ Setup Valid For: {ttl_display} ({ttl_mins_val} mins) (Expires ~{signal_data.get('expiration_time', 'N/A')})\n\n"
            f"🎯 Targets (Two-Tier Execution):\n"
            f"• TP1: ${tp1} (Profit: +{tp1_pct}% | ROE: +{signal_data.get('roe_tp', 0.0)}% | 1:{rr_tp1} R:R) -> Closes 50% & Moves SL to Breakeven\n"
            f"• TP2: ${tp2} (Profit: +{tp2_pct}% | 1:{rr_tp2} R:R) -> Closes 50% Runner\n\n"
            f"📰 Market Context:\n"
            f"• {news_summary}"
            f"{catalyst_str}"
        )
        
        checklist = ai_data.get('scoring_checklist', [])
        if checklist and isinstance(checklist, list):
            checklist_str = "\n\n✅ AI Scoring Checklist:\n" + "\n".join([f"• {item}" for item in checklist])
            msg += checklist_str

        await self.send_telegram(msg)

    async def notify_dynamic_level(self, symbol: str, level_name: str, level: float, price: float) -> None:
        """Notify when a dynamically calculated key price level is crossed."""
        msg = (
            f"⚠️ <b>{symbol} Level Crossed!</b> ⚠️\n\n"
            f"Price has crossed the watched level of <b>${level}</b> ({level_name}).\n"
            f"Current Price: ${price}"
        )
        await self.send_telegram(msg)
