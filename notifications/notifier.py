"""
NotificationService manages Telegram and Discord alerts.
"""

import logging
import json
import asyncio
import aiohttp
from typing import Dict, Any, Optional
from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.exceptions import TelegramNetworkError
from core.config import settings

logger = logging.getLogger(__name__)

class NotificationService:
    """Handles external notifications for signals and static levels."""
    
    def __init__(self, telegram_token: str, telegram_chat_id: str, discord_webhook: str = ""):
        self.bot_token = telegram_token
        self.chat_id = telegram_chat_id
        self.discord_webhook = discord_webhook
        
        self.bot: Optional[Bot] = None
        self.dp = Dispatcher()
        self.journal_manager: Any = None
        
        self._register_handlers()
        if self.bot_token:
            try:
                self.bot = Bot(token=self.bot_token)
            except Exception as e:
                logger.warning(f"Could not initialize Telegram bot: {e}")

    def _register_handlers(self) -> None:
        """Register Telegram command handlers."""
        @self.dp.message(Command("status"))
        async def status_command(message: Message):
            await message.reply("Sentinel is running actively monitoring markets.")
        
        @self.dp.message(Command("ping"))
        async def ping_command(message: Message):
            await message.reply("Pong! Sentinel is online.")

        @self.dp.message(Command("autopsy"))
        async def autopsy_command(message: Message):
            args = message.text.split() if message.text else []
            if len(args) < 2:
                await message.reply("⚠️ Usage: <code>/autopsy &lt;trade_id&gt;</code>\nExample: <code>/autopsy b53e4a35</code>", parse_mode="HTML")
                return
                
            trade_id = args[1].strip()
            if not self.journal_manager:
                await message.reply("❌ Journal manager not initialized.", parse_mode="HTML")
                return

            autopsy = await self.journal_manager.get_trade_autopsy(trade_id)
            if not autopsy:
                await message.reply(f"❌ No autopsy report found for trade <code>{trade_id}</code> (Trade may still be pending or was not a loss).", parse_mode="HTML")
                return

            msg = (
                f"🔬 <b>POST-TRADE AUTOPSY: {autopsy.get('symbol')}</b>\n"
                f"Trade ID: <code>{trade_id}</code> | Direction: {autopsy.get('direction')}\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"🏷 <b>Failure Mode</b>: <code>{autopsy.get('failure_category')}</code>\n"
                f"📉 <b>Loss Result</b>: {autopsy.get('net_pnl_pct', 0.0):.2f}%\n\n"
                f"📋 <b>Forensic Summary</b>:\n{autopsy.get('summary')}\n\n"
                f"🔍 <b>Key Culprit</b>:\n• {autopsy.get('key_culprit')}\n\n"
                f"💡 <b>Actionable Lesson</b>:\n• {autopsy.get('actionable_lesson')}"
            )
            await message.reply(msg, parse_mode="HTML")

        @self.dp.message(Command("nearmiss"))
        async def nearmiss_command(message: Message):
            if not self.journal_manager:
                await message.reply("❌ Journal manager not initialized.", parse_mode="HTML")
                return

            records = await self.journal_manager.get_recent_rejected_signals(limit=5)
            if not records:
                await message.reply("ℹ️ No near-miss rejected setups (score ≥ 75.0%) found in database yet.", parse_mode="HTML")
                return

            lines = ["🔍 <b>RECENT NEAR-MISS SETUPS (75% - 85.9%)</b>\n━━━━━━━━━━━━━━━━━━━━━"]
            for r in records:
                score = r.get('confidence_score', 0.0)
                needed = round(86.0 - score, 1)
                checklist = r.get('scoring_checklist', [])
                penalties = [c for c in checklist if '-' in c or 'Drag' in c or 'Wall' in c or 'Sweep' in c or 'Fighting' in c or 'Trap' in c]
                penalties_str = ", ".join(penalties) if penalties else "None"

                lines.append(
                    f"• <b>{r.get('symbol')} ({r.get('timeframe')})</b> | {r.get('direction')} [{r.get('pattern_name')}]\n"
                    f"  Score: <b>{score:.1f}%</b> (Missing <b>{needed:.1f}%</b> for Standard 86%)\n"
                    f"  Friction Deductions: <i>{penalties_str}</i>\n"
                    f"  Reason: {r.get('rejection_reason')}\n"
                )
            await message.reply("\n".join(lines), parse_mode="HTML")

    async def start_polling(self) -> None:
        """Start the aiogram bot polling loop with automatic reconnection on network drops."""
        if not self.bot:
            return
        logger.info("Starting Telegram Bot polling...")
        retry_delay = 5
        while True:
            try:
                await self.dp.start_polling(self.bot, handle_signals=False)
                break
            except asyncio.CancelledError:
                logger.info("Telegram Bot polling stopped.")
                break
            except (TelegramNetworkError, aiohttp.ClientError, OSError) as e:
                logger.warning(
                    f"Telegram network connection lost ({e}). Reconnecting in {retry_delay}s..."
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)
            except Exception as e:
                logger.error(
                    f"Unexpected error in Telegram polling: {e}. Retrying in {retry_delay}s...",
                    exc_info=True
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)

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

    async def notify_signal(self, symbol: str, signal_data: Dict[str, Any], ai_response_text: Any, current_drawdown: float = 0.0) -> None:
        """Format and broadcast an evaluated trading signal."""
        if isinstance(ai_response_text, dict):
            ai_data = ai_response_text
        else:
            try:
                ai_data = json.loads(ai_response_text)
            except Exception as e:
                logger.error(f"Failed to parse AI JSON response: {e}")
                ai_data = {}

        direction = signal_data.get('direction', 'Neutral')
        action_str = "LONG 🟢" if direction == "Bullish" else "SHORT 🔴" if direction == "Bearish" else "NEUTRAL ⚪"
        
        entry = signal_data.get('entry', 0.0)
        entry_min = round(entry * 0.9995, max(2, len(str(entry).split('.')[-1])))
        entry_max = round(entry * 1.0005, max(2, len(str(entry).split('.')[-1])))
        
        confidence = ai_data.get('final_confidence_rating', 0)
        news_summary = ai_data.get('strategy_analysis', 'N/A')
        
        channels = signal_data.get('dispatch_channels', [])
        if not channels:
            channels = ["VIP"] if confidence >= settings.vip_confidence_threshold else ["STANDARD"]
            
        regime = signal_data.get('regime', 'NORMAL')
        outcome = signal_data.get('outcome', '')
        
        if "SHADOW" in channels or regime == "SHADOW" or outcome == "SHADOW":
            logger.info(f"Silently ignoring broadcast for SHADOW trade: {symbol}")
            return
            
        setup_type = signal_data.get('setup_type', '')
        channel_tags = []
        if "VIP" in channels:
            if setup_type == "VIP_SNIPER":
                channel_tags.append("[👑 VIP SNIPER EXCLUSIVE]")
            else:
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
            
        sl_val = signal_data.get('stop_loss', 0.0)
        sl_pct = signal_data.get('sl_pct', 0.0)
        tp1 = signal_data.get('tp1', signal_data.get('take_profit', 0.0))
        tp2 = signal_data.get('tp2', signal_data.get('take_profit', 0.0))
        tp1_pct = signal_data.get('tp1_pct', signal_data.get('tp_pct', 0.0))
        tp2_pct = signal_data.get('tp2_pct', signal_data.get('tp_pct', 0.0))
        
        lev = signal_data.get('leverage_val')
        if lev is None:
            try:
                import re
                lev_match = re.search(r'(\d+(\.\d+)?)', str(signal_data.get('recommended_leverage', '1')))
                lev = float(lev_match.group(1)) if lev_match else 1.0
            except Exception:
                lev = 1.0
        else:
            lev = float(lev)

        close_ratio_pct = int(getattr(settings, 'tp1_close_ratio', 0.70) * 100)
        rem_ratio_pct = 100 - close_ratio_pct
        roe_sl = round(abs(float(signal_data.get('roe_sl') or (sl_pct * lev))), 2)
        roe_tp1 = round(abs(float(signal_data.get('roe_tp1') or signal_data.get('roe_tp') or (tp1_pct * lev))), 2)
        overall_tp2_roe = round(abs(float(signal_data.get('overall_tp2_roe') or (((close_ratio_pct / 100.0) * tp1_pct + ((rem_ratio_pct / 100.0) * tp2_pct)) * lev))), 2)

        if setup_type == "VIP_SNIPER":
            tier_badge = " | 👑 VIP SNIPER (100% ROE)"
        elif confidence >= settings.vip_confidence_threshold:
            tier_badge = " | 👑 VIP TIER"
        else:
            tier_badge = " | 🔹 STANDARD TIER"

        ttl_mins_val = signal_data.get('ttl_mins', 1080)
        ttl_display = f"{ttl_mins_val // 60}h" if ttl_mins_val % 60 == 0 else f"{ttl_mins_val} mins"
        
        reversal_str = ""
        reversal_notice = signal_data.get('reversal_notice', '')
        if reversal_notice:
            reversal_str = f"🔄 <b>BIAS FLIP / INVALIDATION NOTICE</b>\n{reversal_notice}\n━━━━━━━━━━━━━━━━━━━━━\n\n"

        be_price = signal_data.get('breakeven_price')
        be_str = f" | Auto-BE @ ${be_price}" if be_price else ""

        msg = (
            f"{reversal_str}"
            f"{tag_str}\n"
            f"{badge_str}"
            f"Action: <b>{action_str}</b> | Confidence: <b>{confidence}%</b>{tier_badge}\n"
            f"Recommended Leverage: <b>{signal_data.get('recommended_leverage', '1x')}</b>\n"
            f"Entry: <b>${entry_min} – ${entry_max}</b>\n"
            f"Stop Loss: <b>${sl_val} (-{sl_pct}% | ROE: -{roe_sl}%){be_str}</b>\n"
            f"⏳ Target Horizon: {signal_data.get('target_resolution', 'Intraday')} (TTL {ttl_display} | Expires ~{signal_data.get('expiration_time', 'N/A')})\n\n"
            f"🎯 Take Profit Targets:\n"
            f"• TP1: <b>${tp1} (+{tp1_pct}% | ROE: +{roe_tp1}%)</b> -> Closes {close_ratio_pct}% & Moves SL to Breakeven\n"
            f"• TP2: <b>${tp2} (+{tp2_pct}% | Total ROE: +{overall_tp2_roe}%)</b> -> Closes {rem_ratio_pct}% Runner"
        )

        # Detailed internal technical diagnostics appended only if verbose mode is enabled
        if settings.verbose_telegram_signals:
            catalyst_str = ""
            catalyst_info = signal_data.get('catalyst_info', '')
            if catalyst_info:
                catalyst_str = f"\n\n📰 Market Catalyst:\n• {catalyst_info}"

            magnet_str = ""
            magnets = signal_data.get('liquidity_magnets', [])
            if magnets:
                magnet_lines = []
                for m in magnets:
                    icon = "🟢" if m.get('side') == 'BID' else "🔴"
                    action_text = m.get('action')
                    if not action_text:
                        role = "Target Pull" if (direction == 'Bullish' and m.get('side') == 'ASK') or (direction == 'Bearish' and m.get('side') == 'BID') else "Liquidity Wall / Sweep Pool"
                        action_text = f"Monitor price reaction near ${m.get('price', 0.0)} ({role})."
                    magnet_lines.append(
                        f"• {icon} <b>{m.get('volume_formatted', '')} {m.get('side', '')} Pool</b> at <b>${m.get('price', 0.0)}</b> "
                        f"({m.get('distance_pct', 0.0):+.2f}% | {m.get('ratio', 1.0)}x depth)\n"
                        f"  ↳ <b>Next Action:</b> <i>{action_text}</i>"
                    )
                magnet_str = "\n\n🧲 <b>LIQUIDITY MAGNETS DETECTED & NEXT ACTIONS</b>:\n" + "\n".join(magnet_lines)

            checklist_str = ""
            checklist = ai_data.get('scoring_checklist', [])
            if checklist and isinstance(checklist, list):
                checklist_str = "\n\n✅ AI Scoring Checklist:\n" + "\n".join([f"• {item}" for item in checklist])

            msg += (
                f"\n\n📰 Market Context:\n• {news_summary}"
                f"{catalyst_str}"
                f"{magnet_str}"
                f"{checklist_str}"
            )

        await self.send_telegram(msg)

    async def notify_dynamic_level(self, symbol: str, level_name: str, level: float, price: float) -> None:
        """Notify when a dynamically calculated key price level is crossed."""
        msg = (
            f"⚠️ <b>{symbol} Level Crossed!</b> ⚠️\n\n"
            f"Price has crossed the watched level of <b>${level}</b> ({level_name}).\n"
            f"Current Price: ${price}"
        )
        await self.send_telegram(msg)
