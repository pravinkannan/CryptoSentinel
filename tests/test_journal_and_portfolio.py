import asyncio
import pytest
import aiosqlite
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone, timedelta

from core.config import settings
from core.calendar_guard import CalendarGuard
from engine.journal_manager import JournalManager
from engine.portfolio_manager import PortfolioManager
from execution.paper_trader import PaperTradingService


def test_pattern_drift_small_sample_size_remains_healthy(tmp_path):
    async def run_test():
        db_file = str(tmp_path / "test_sentinel.db")
        journal = JournalManager(db_path=db_file)
        await journal.ensure_db_exists()

        # Log only 2 trades (both LOSS)
        for _ in range(2):
            await journal.log_trade({
                'symbol': 'BTC/USDT',
                'direction': 'LONG',
                'pattern_name': 'Hammer',
                'entry_price': 100.0,
                'sl_price': 95.0,
                'tp1_price': 105.0,
                'tp2_price': 110.0,
                'leverage': 5.0,
                'outcome': 'LOSS',
                'confidence_score': 80
            })

        # With only 2 trades (< 5 minimum threshold), drift status must remain HEALTHY
        drift_status = await journal.check_pattern_drift('Hammer')
        assert drift_status == 'HEALTHY'

    asyncio.run(run_test())


def test_pattern_drift_degrades_after_sufficient_losses(tmp_path):
    async def run_test():
        db_file = str(tmp_path / "test_sentinel.db")
        journal = JournalManager(db_path=db_file)
        await journal.ensure_db_exists()

        # Log 6 trades: 1 WIN, 5 LOSS (Win rate ~ 16.7% < 40%)
        outcomes = ['WIN_TP1', 'LOSS', 'LOSS', 'LOSS', 'LOSS', 'LOSS']
        for outcome in outcomes:
            await journal.log_trade({
                'symbol': 'BTC/USDT',
                'direction': 'LONG',
                'pattern_name': 'Bearish Engulfing',
                'entry_price': 100.0,
                'sl_price': 95.0,
                'tp1_price': 105.0,
                'tp2_price': 110.0,
                'leverage': 5.0,
                'outcome': outcome,
                'confidence_score': 80
            })

        drift_status = await journal.check_pattern_drift('Bearish Engulfing')
        assert drift_status == 'DEGRADED'

    asyncio.run(run_test())


def test_portfolio_manager_emergency_guard_breakeven():
    async def run_test():
        mock_paper_trader = AsyncMock()
        mock_paper_trader.get_open_positions.return_value = [
            {
                'id': 'trade-123',
                'symbol': 'BTC/USDT',
                'direction': 'LONG',
                'entry': 100.0,
                'stop_loss': 95.0,
                'leverage': '15x',
                'pnl_pct': 1.5
            }
        ]

        portfolio_manager = PortfolioManager(mock_paper_trader)
        await portfolio_manager.check_emergency_guard(
            symbol='BTC/USDT',
            regime='HIGH_VOLATILITY',
            phase='PHASE 1',
            current_price=101.5
        )

        # Should move Stop Loss to Breakeven (100.0)
        mock_paper_trader.move_sl_to_be.assert_awaited_once_with('trade-123', 100.0)
        mock_paper_trader.close_position.assert_not_awaited()

    asyncio.run(run_test())


def test_portfolio_manager_emergency_guard_slippage_close():
    async def run_test():
        mock_paper_trader = AsyncMock()
        mock_paper_trader.get_open_positions.return_value = [
            {
                'id': 'trade-456',
                'symbol': 'BTC/USDT',
                'direction': 'LONG',
                'entry': 100.0,
                'stop_loss': 95.0,
                'leverage': '20x',
                'pnl_pct': 0.5
            }
        ]

        portfolio_manager = PortfolioManager(mock_paper_trader)
        # Market price has already crashed below entry price
        await portfolio_manager.check_emergency_guard(
            symbol='BTC/USDT',
            regime='HIGH_VOLATILITY',
            phase='PHASE 1',
            current_price=99.0
        )

        # Should execute emergency slippage market close
        mock_paper_trader.close_position.assert_awaited_once_with('trade-456', 99.0, 'EMERGENCY_SLIPPAGE_CLOSE')
        mock_paper_trader.move_sl_to_be.assert_not_awaited()

    asyncio.run(run_test())


def test_calendar_guard_sync_parses_events():
    async def run_test():
        guard = CalendarGuard()
        mock_data = [
            {
                "title": "Non-Farm Payrolls",
                "country": "USD",
                "date": "2026-10-15T08:30:00-04:00",
                "impact": "High"
            },
            {
                "title": "Low Impact Event",
                "country": "USD",
                "date": "2026-10-15T10:00:00-04:00",
                "impact": "Low"
            },
            {
                "title": "EUR Rate Decision",
                "country": "EUR",
                "date": "2026-10-15T12:00:00-04:00",
                "impact": "High"
            }
        ]

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=mock_data)

        # Context manager for session.get(...)
        mock_get_ctx = MagicMock()
        mock_get_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_get_ctx.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.get.return_value = mock_get_ctx

        # Context manager for aiohttp.ClientSession(...)
        mock_session_ctx = MagicMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

        with patch('aiohttp.ClientSession', return_value=mock_session_ctx):
            await guard.sync_calendar()

        assert len(guard.high_impact_events) == 1
        assert guard.last_sync_time is not None

    asyncio.run(run_test())


def test_rejected_signals_logging_and_retrieval(tmp_path):
    """Verify that near-miss rejected signals are logged into rejected_signals table and queried accurately."""
    async def run_test():
        db_file = str(tmp_path / "test_sentinel.db")
        journal = JournalManager(db_path=db_file)
        await journal.ensure_db_exists()

        # Log a near-miss candidate setup (Score: 78.5%)
        row_id = await journal.log_rejected_signal({
            'symbol': 'ETH/USDT',
            'timeframe': '15m',
            'direction': 'LONG',
            'pattern_name': 'Bullish Belthold',
            'confidence_score': 78.5,
            'eval_trace': {
                'base_score': 64.0,
                'vol_points': 5.0,
                'level_points': 3.0,
                'vwap_points': 0.0,
                'ob_points': 3.0,
                'trend_tailwinds': 6.5,
                'catalyst_points': 0.0,
                'friction': -3.0,
                'net_score': 78.5
            },
            'scoring_checklist': [
                '+5.0% Dynamic Volume Surge',
                '+3.0% Key Level Precision',
                '-3.0% Fighting Session VWAP'
            ],
            'rejection_reason': 'Score 78.5% below 86.0% threshold'
        })
        assert row_id > 0

        # Retrieve recent near-miss signals
        records = await journal.get_recent_rejected_signals(limit=5)
        assert len(records) == 1
        rec = records[0]
        assert rec['symbol'] == 'ETH/USDT'
        assert rec['timeframe'] == '15m'
        assert rec['direction'] == 'LONG'
        assert rec['confidence_score'] == 78.5
        assert rec['friction'] == -3.0
        assert rec['vol_points'] == 5.0
        assert any("Fighting Session VWAP" in c for c in rec['scoring_checklist'])
        assert rec['eval_trace']['base_score'] == 64.0

    asyncio.run(run_test())
