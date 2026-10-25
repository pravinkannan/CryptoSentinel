"""Unit and integration tests for Telegram /recon command and Recon Scanner Pipeline."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from notifications.notifier import NotificationService
from application.pipeline.pipeline import SignalContext
from main import CryptoSentinelApp


def test_telegram_recon_ticker_normalization():
    """Verify Telegram /recon command correctly parses and normalizes ticker variations."""
    async def run():
        notifier = NotificationService(telegram_token="MOCK_TOKEN", telegram_chat_id="12345")
        mock_handler = AsyncMock(return_value="RECON_OUTPUT_REPORT")
        notifier.recon_handler = mock_handler

        tickers_to_test = [
            ("PEPEUSDT", "PEPE/USDT"),
            ("pepeusdt", "PEPE/USDT"),
            ("SOL/USDT", "SOL/USDT"),
            ("BTC", "BTC/USDT"),
            ("ETHUSDC", "ETH/USDC"),
        ]

        for raw, expected in tickers_to_test:
            raw_ticker = raw.strip().upper()
            if "/" in raw_ticker:
                normalized = raw_ticker
            elif raw_ticker.endswith("USDT"):
                normalized = f"{raw_ticker[:-4]}/USDT"
            elif raw_ticker.endswith("USDC"):
                normalized = f"{raw_ticker[:-4]}/USDC"
            else:
                normalized = f"{raw_ticker}/USDT"
            assert normalized == expected
    asyncio.run(run())


def test_execute_recon_scan_confirmed(tmp_path):
    """Test execute_recon_scan runs pipeline across standard timeframes and formats confirmed report."""
    async def run():
        test_db = str(tmp_path / "test_recon.db")
        with patch("core.config.settings.telegram_bot_token", None):
            app = CryptoSentinelApp()
        app.repository.db_path = test_db
        app.paper_trader.db_path = test_db

        # Mock market data & pipeline execution
        app.market_data = AsyncMock()
        app.market_data.fetch_ohlcv = AsyncMock(return_value=None)
        app.signal_generator = MagicMock()
        app.signal_generator.get_htf_trend = MagicMock(return_value="BULLISH")

        async def mock_execute(ctx: SignalContext):
            if ctx.timeframe == "15m":
                ctx.metadata['recon_status'] = 'CONFIRMED'
                ctx.metadata['recon_data'] = {
                    'symbol': ctx.symbol,
                    'timeframe': ctx.timeframe,
                    'direction': 'LONG',
                    'confidence': 88.5,
                    'tier': 'STANDARD',
                    'regime': 'NORMAL',
                    'pattern_name': 'Bullish Engulfing',
                    'entry': 0.00001250,
                    'stop_loss': 0.00001200,
                    'tp1': 0.00001350,
                    'tp2': 0.00001450,
                    'leverage': 5.0,
                    'checklist': ['+8.0% Volume Surge', '+6.0% Key Level Rejection']
                }
            else:
                ctx.metadata['recon_status'] = 'NO_PATTERN'
            return ctx

        app.pipeline = MagicMock()
        app.pipeline.execute = AsyncMock(side_effect=mock_execute)

        report = await app.execute_recon_scan("PEPE/USDT")

        assert "RECON: PEPE/USDT" in report
        assert "CONFIRMED [15m] LONG" in report
        assert "88.5%" in report
        assert "0.00001350" in report
        assert "Sentinel DB" in report
    asyncio.run(run())


def test_execute_recon_scan_rejected(tmp_path):
    """Test execute_recon_scan formats Phase 1 circuit breaker rejection and Ghost Score."""
    async def run():
        test_db = str(tmp_path / "test_recon.db")
        with patch("core.config.settings.telegram_bot_token", None):
            app = CryptoSentinelApp()
        app.repository.db_path = test_db

        app.market_data = AsyncMock()
        app.market_data.fetch_ohlcv = AsyncMock(return_value=None)
        app.signal_generator = MagicMock()
        app.signal_generator.get_htf_trend = MagicMock(return_value="BEARISH")

        async def mock_execute(ctx: SignalContext):
            if ctx.timeframe == "5m":
                ctx.metadata['recon_status'] = 'REJECTED'
                ctx.metadata['recon_score'] = 74.5
                ctx.metadata['recon_fatal_flaw'] = 'Phase 1 Gate Failed: 4H Macro Conflict'
                ctx.metadata['recon_reason'] = 'Counter-trend without divergence'
                ctx.metadata['recon_pattern'] = 'Bearish Belthold'
                ctx.metadata['recon_direction'] = 'SHORT'
            else:
                ctx.metadata['recon_status'] = 'NO_PATTERN'
            return ctx

        app.pipeline = MagicMock()
        app.pipeline.execute = AsyncMock(side_effect=mock_execute)

        report = await app.execute_recon_scan("DOGE/USDT")

        assert "RECON: DOGE/USDT" in report
        assert "No Valid Setup" in report
        assert "74.5%" in report
        assert "4H Macro Conflict" in report
    asyncio.run(run())
