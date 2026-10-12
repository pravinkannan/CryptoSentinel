import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone

from core.config import settings
from data.market_data_service import scan_liquidity_magnets, enrich_magnets_with_actions
from engine.journal_manager import JournalManager
from execution.paper_trader import PaperTradingService
from notifications.notifier import NotificationService
from aiogram.types import Message


def test_min_dispatch_confidence_configuration():
    """Verify that min dispatch confidence is set to 80%."""
    assert settings.min_dispatch_confidence == 80
    assert settings.vip_confidence_threshold == 90


def test_scan_liquidity_magnets():
    """Verify that anomalous order book clusters are detected as liquidity magnets."""
    current_price = 82500.0
    
    # Normal surrounding bids (~$50k each)
    bids = [[82400.0, 0.6], [82300.0, 0.6], [82200.0, 0.6], [82100.0, 0.6]]
    # Massive institutional bid wall at $82,000 ($4.1M volume)
    bids.append([82000.0, 50.0])
    
    # Normal asks
    asks = [[82600.0, 0.5], [82700.0, 0.5], [82800.0, 0.5]]
    
    order_book = {'bids': bids, 'asks': asks}
    magnets = scan_liquidity_magnets(order_book, current_price, depth_pct=0.02, multiplier=3.0)
    
    assert len(magnets) >= 1
    top_magnet = magnets[0]
    assert top_magnet['side'] == 'BID'
    assert 81950.0 <= top_magnet['price'] <= 82050.0
    assert top_magnet['volume_usd'] >= 4_000_000
    assert top_magnet['ratio'] >= 3.0


def test_liquidity_magnet_telegram_rendering():
    """Verify that liquidity magnets are visually rendered in the Telegram alert."""
    async def run_test():
        notifier = NotificationService(telegram_token="dummy", telegram_chat_id="dummy")
        notifier.send_telegram = AsyncMock()

        signal = {
            'timeframe': '1h',
            'entry': 82500.0,
            'stop_loss': 82000.0,
            'take_profit': 83500.0,
            'sl_pct': 0.6,
            'tp_pct': 1.2,
            'recommended_leverage': '20x',
            'ttl_mins': 720,
            'expiration_time': '04:00 UTC',
            'liquidity_magnets': [
                {
                    'side': 'BID',
                    'price': 82000.0,
                    'volume_formatted': '$4.1M',
                    'distance_pct': -0.61,
                    'ratio': 6.5
                }
            ]
        }
        ai_data = {'trade_direction': 'Bullish', 'final_confidence_rating': 85, 'strategy_analysis': 'Liquidity pool detected'}

        await notifier.notify_signal('BTC/USDT', signal, ai_data, 0.0)
        msg = notifier.send_telegram.call_args[0][0]
        assert "🧲 <b>LIQUIDITY MAGNETS DETECTED" in msg
        assert "$4.1M BID Pool" in msg
        assert "$82000.0" in msg
        assert "Next Action:" in msg

    asyncio.run(run_test())


def test_enrich_magnets_with_actions():
    """Verify that magnets are enriched with precise, actionable trade instructions."""
    raw_magnets = [
        {
            'side': 'ASK',
            'price': 85000.0,
            'volume_formatted': '$12.0M',
            'distance_pct': 2.4,
            'ratio': 4.5
        },
        {
            'side': 'BID',
            'price': 82000.0,
            'volume_formatted': '$10.0M',
            'distance_pct': -1.2,
            'ratio': 3.8
        },
        {
            'side': 'BID',
            'price': 82700.0,
            'volume_formatted': '$8.0M',
            'distance_pct': -0.36,
            'ratio': 3.2
        }
    ]

    # Test Bullish / Long context
    enriched_long = enrich_magnets_with_actions(
        raw_magnets,
        direction="Bullish",
        entry=83000.0,
        stop_loss=81500.0,
        take_profit=85000.0
    )

    # Magnet 1 ($85,000 ASK) should be TARGET_MAGNET with Front-Run TP action
    m1 = next(m for m in enriched_long if m['price'] == 85000.0)
    assert m1['role'] == 'TARGET_MAGNET'
    assert "Front-Run TP" in m1['action']
    assert m1['action_price'] == 84830.0

    # Magnet 2 ($82,000 BID, -1.2%) should be DEFENSIVE_SHIELD
    m2 = next(m for m in enriched_long if m['price'] == 82000.0)
    assert m2['role'] == 'DEFENSIVE_SHIELD'
    assert "Defensive Shield" in m2['action']

    # Magnet 3 ($82,700 BID, -0.36%) should be SWEEP_RISK
    m3 = next(m for m in enriched_long if m['price'] == 82700.0)
    assert m3['role'] == 'SWEEP_RISK'
    assert "Anticipate Sweep" in m3['action']

    # Test Bearish / Short context
    enriched_short = enrich_magnets_with_actions(
        raw_magnets,
        direction="Bearish",
        entry=83000.0,
        stop_loss=84500.0,
        take_profit=82000.0
    )
    # Magnet 2 ($82,000 BID) should be TARGET_MAGNET for short
    s_m2 = next(m for m in enriched_short if m['price'] == 82000.0)
    assert s_m2['role'] == 'TARGET_MAGNET'
    assert "Front-Run TP" in s_m2['action']
    assert s_m2['action_price'] == 82164.0


def test_journal_autopsy_storage_and_retrieval(tmp_path):
    """Verify saving and retrieving post-trade autopsy in sentinel.db."""
    async def run_test():
        db_file = str(tmp_path / "test_sentinel.db")
        journal = JournalManager(db_path=db_file)
        await journal.ensure_db_exists()

        trade_id = "test-trade-1234"
        autopsy_data = {
            'trade_id': trade_id,
            'symbol': 'BTC/USDT',
            'direction': 'LONG',
            'failure_category': 'STOP_HUNT_SWEEP',
            'summary': 'Price wicked through stop loss at $82,400 to sweep liquidity before rallying.',
            'key_culprit': 'Aggressive market sell cascade absorbed by limit bids.',
            'actionable_lesson': 'Widen ATR stop loss buffer to 2.5x during high volume sessions.',
            'net_pnl_pct': -14.5
        }

        await journal.save_autopsy(autopsy_data)
        retrieved = await journal.get_trade_autopsy(trade_id)

        assert retrieved is not None
        assert retrieved['trade_id'] == trade_id
        assert retrieved['failure_category'] == 'STOP_HUNT_SWEEP'
        assert retrieved['net_pnl_pct'] == -14.5
        assert "liquidity before rallying" in retrieved['summary']

    asyncio.run(run_test())


def test_autopsy_telegram_command_handler(tmp_path):
    """Verify that the /autopsy telegram command returns the post-mortem report."""
    async def run_test():
        db_file = str(tmp_path / "test_sentinel.db")
        journal = JournalManager(db_path=db_file)
        await journal.ensure_db_exists()

        trade_id = "loss-5678"
        await journal.save_autopsy({
            'trade_id': trade_id,
            'symbol': 'ETH/USDT',
            'direction': 'SHORT',
            'failure_category': 'MACRO_TREND_BREAK',
            'summary': 'Bullish breakout broke through 1h resistance.',
            'key_culprit': 'Sudden spot volume influx.',
            'actionable_lesson': 'Do not short into ascending volume.',
            'net_pnl_pct': -12.0
        })

        notifier = NotificationService(telegram_token="dummy", telegram_chat_id="dummy")
        notifier.journal_manager = journal

        mock_message = MagicMock(spec=Message)
        mock_message.text = f"/autopsy {trade_id}"
        mock_message.reply = AsyncMock()

        # Invoke handler
        # Find the registered autopsy handler
        handlers = notifier.dp.message.handlers
        autopsy_handler = None
        for h in handlers:
            if "autopsy_command" in getattr(h.callback, '__name__', ''):
                autopsy_handler = h.callback
                break
                
        assert autopsy_handler is not None
        await autopsy_handler(mock_message)

        mock_message.reply.assert_called_once()
        reply_text = mock_message.reply.call_args[0][0]
        assert "POST-TRADE AUTOPSY: ETH/USDT" in reply_text
        assert "MACRO_TREND_BREAK" in reply_text
        assert "Sudden spot volume influx." in reply_text

    asyncio.run(run_test())
