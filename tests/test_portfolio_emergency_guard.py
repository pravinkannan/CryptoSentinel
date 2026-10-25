"""Unit tests for PortfolioManager emergency guard protective mechanisms (breakeven ratchet & slippage close)."""
import asyncio
from unittest.mock import AsyncMock

from engine.portfolio_manager import PortfolioManager


def test_portfolio_manager_emergency_guard_breakeven():
    """Verify that high-volatility regime moves in-profit positions to Breakeven."""
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
    """Verify that when price has breached entry in high volatility, emergency slippage market close executes."""
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
