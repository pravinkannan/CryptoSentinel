"""Unit and integration tests for JournalManager pattern drift and rejected signal persistence."""
import asyncio
from engine.journal_manager import JournalManager


def test_pattern_drift_small_sample_size_remains_healthy(tmp_path):
    """Verify that pattern drift maintains HEALTHY status when sample size is below minimum threshold."""
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
    """Verify that pattern drift switches to DEGRADED when win rate drops below 40% after >= 5 trades."""
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
