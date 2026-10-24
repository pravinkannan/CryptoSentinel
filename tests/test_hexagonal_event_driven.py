"""
Unit and integration tests for Hexagonal and Event-Driven Architecture Refactor.
Verifies:
1. Pure Domain Confluence Math (Zero network/LLM/JSON, all 6 quantitative rules preserved).
2. Async Event Bus & Subscriber Isolation (asyncio.gather isolation, frozen event immutability).
3. Pipeline Steps & Pipeline Execution (IPipelineStep protocol & SignalContext flow).
4. Repository Pattern & State Consolidation (ITradeRepository / SentinelRepository atomic eviction).
"""

import asyncio
import pytest
from dataclasses import FrozenInstanceError

from domain.confluence.engine import (
    evaluate_confluence_score,
    STAGE2_RSI_DIVERGENCE_POINTS,
    STAGE3_REVERSAL_THRESHOLD,
    STAGE3_STANDARD_THRESHOLD,
    STAGE2_FRICTION_EXHAUSTION_TRAP_PENALTY,
    STAGE2_TARGET_MAGNET_POINTS,
)
from core.events import TradeOpenedEvent, TradeClosedEvent, SignalRejectedEvent
from core.event_bus import EventBus
from application.pipeline.pipeline import SignalPipeline, SignalContext, IPipelineStep
from application.pipeline.steps import ConfluenceEvaluationStep
from infrastructure.database.repository import SentinelRepository


# ==============================================================================
# 1. PURE DOMAIN CONFLUENCE MATH TESTS
# ==============================================================================

def test_domain_engine_ghost_scoring():
    """Circuit breaker trip must calculate full score, set circuit_breaker_tripped=True, and force tier=REJECTED."""
    signal = {
        'direction': 'LONG',
        'rsi_14': 82.0,  # Trips RSI exhaustion circuit breaker
        'volume': 2000.0,
        'vol_sma_20': 1000.0,
        'key_level_rejection': True,
        'dist_to_key_level_pct': 0.05,
    }
    res = evaluate_confluence_score("ETH/USDT", signal)
    assert res['circuit_breaker_tripped'] is True
    assert res['tier'] == 'REJECTED'
    assert res['final_score'] > 0.0  # Ghost score preserved!
    assert "RSI Exhaustion" in res['fatal_flaw']


def test_domain_engine_reversal_bypass():
    """5m/15m counter-trend setups bypass 4H macro conflict with 1.5x volume + reversal confirmation."""
    signal = {
        'direction': 'LONG',
        'timeframe': '5m',
        'htf_4h_trend': 'Macro Down',
        'volume': 1600.0,
        'vol_sma_20': 1000.0,  # 1.6x volume (>= 1.5x)
        'key_level_rejection': True,
        'rsi_divergence': True,
        'patterns': ['Bullish Hammer'],
    }
    res = evaluate_confluence_score("BTC/USDT", signal)
    # Must NOT trip the 4H macro conflict circuit breaker
    assert res['eval_trace']['stage1_passed'] is True
    assert res['circuit_breaker_tripped'] is False


def test_domain_engine_dual_regime_floor():
    """Reversals dispatch floor is 80.0%, Continuations floor is 86.0%."""
    assert STAGE3_REVERSAL_THRESHOLD == 80.0
    assert STAGE3_STANDARD_THRESHOLD == 86.0


def test_domain_engine_state_aware_vwap():
    """Waive -3.0% VWAP penalty and award +2.0% target pull when reversing toward VWAP."""
    signal = {
        'direction': 'LONG',
        'close': 100.0,
        'vwap': 105.0,  # Price is below VWAP, pulling toward it
        'key_level_rejection': True,
        'rsi_divergence': True,  # Reversal setup
        'volume': 1000.0,
        'vol_sma_20': 1000.0,
    }
    res = evaluate_confluence_score("SOL/USDT", signal)
    checklist = res['scoring_checklist']
    # Check that VWAP penalty was waived and Target Pull was awarded
    assert not any("-3.0% Fighting Session VWAP" in c for c in checklist)
    assert any("Target Magnet Pull (Session VWAP Reversion)" in c for c in checklist)


def test_domain_engine_exhaustion_trap():
    """Continuation with Volume Surge + OB Imbalance + Extended RSI (>70) incurs -5.0% penalty."""
    signal = {
        'direction': 'LONG',
        'is_continuation': True,
        'volume': 2000.0,
        'vol_sma_20': 1000.0,
        'imbalance_ratio': 1.5,
        'rsi_14': 74.0,  # Extended RSI > 70
        'key_level_rejection': False,
    }
    res = evaluate_confluence_score("AVAX/USDT", signal)
    checklist = res['scoring_checklist']
    assert any("Exhaustion Absorption Trap" in c for c in checklist)
    assert res['eval_trace']['breakdown']['exhaustion_trap'] is True


def test_domain_engine_elevated_rsi_divergence_points():
    """STAGE2_RSI_DIVERGENCE_POINTS must be +5.0%."""
    assert STAGE2_RSI_DIVERGENCE_POINTS == 5.0


# ==============================================================================
# 2. EVENT BUS & SUBSCRIBER ISOLATION TESTS
# ==============================================================================

def test_event_immutability():
    """Events must be immutable frozen dataclasses."""
    evt = TradeClosedEvent(
        trade_id="T1",
        symbol="BTC/USDT",
        direction="LONG",
        exit_price=90000.0,
        pnl_pct=3.5,
        outcome="WIN_TP1",
        exit_reason="TP1_HIT"
    )
    with pytest.raises(FrozenInstanceError):
        evt.exit_price = 91000.0  # type: ignore


def test_event_bus_subscriber_isolation():
    """A crashing subscriber must not prevent other subscribers from receiving the event."""
    async def run_test():
        bus = EventBus()
        received = []

        async def faulty_handler(event: TradeClosedEvent):
            raise RuntimeError("Subscriber exploded!")

        async def healthy_handler(event: TradeClosedEvent):
            received.append(event.trade_id)

        bus.subscribe(TradeClosedEvent, faulty_handler)
        bus.subscribe(TradeClosedEvent, healthy_handler)

        evt = TradeClosedEvent(
            trade_id="T_ISOLATION",
            symbol="ETH/USDT",
            direction="SHORT",
            exit_price=3000.0,
            pnl_pct=-1.2,
            outcome="LOSS",
            exit_reason="SL_HIT"
        )

        # Must complete cleanly without raising the subscriber's RuntimeError
        await bus.publish(evt)
        assert "T_ISOLATION" in received

    asyncio.run(run_test())


# ==============================================================================
# 3. PIPELINE & COMPOSABLE STEP TESTS
# ==============================================================================

def test_signal_pipeline_early_exit():
    """If a step returns None, pipeline execution terminates cleanly."""
    async def run_test():
        bus = EventBus()
        rejections = []

        async def on_rejected(event: SignalRejectedEvent):
            rejections.append(event)

        bus.subscribe(SignalRejectedEvent, on_rejected)

        step = ConfluenceEvaluationStep(event_bus=bus)
        pipeline = SignalPipeline([step])

        # Context with a setup that fails confluence floor
        ctx = SignalContext(
            symbol="ADA/USDT",
            timeframe="5m",
            signal_data={
                'direction': 'LONG',
                'volume': 100.0,
                'vol_sma_20': 1000.0,  # 0.1x anemic volume -> trips circuit breaker
                'key_level_rejection': False,
                'rsi_14': 50.0
            }
        )

        result = await pipeline.execute(ctx)
        assert result is None  # Pipeline halted!
        assert len(rejections) == 1
        assert rejections[0].symbol == "ADA/USDT"
        assert rejections[0].tier == "REJECTED"

    asyncio.run(run_test())


# ==============================================================================
# 4. REPOSITORY ATOMIC EVICTION TESTS
# ==============================================================================

def test_repository_atomic_eviction(tmp_path):
    """SentinelRepository evicts closed trade from active signals upon TradeClosedEvent."""
    async def run_test():
        db_file = str(tmp_path / "test_sentinel.db")
        repo = SentinelRepository(db_path=db_file)
        await repo.ensure_schema()

        # Save active signal
        await repo.save_active_signal("BTC/USDT_5m", {
            'trade_id': 'TRADE_999',
            'direction': 'LONG',
            'confidence': 90.0
        })

        active = await repo.get_active_signals()
        assert "BTC/USDT_5m" in active

        # Trigger TradeClosedEvent via listener
        closed_event = TradeClosedEvent(
            trade_id="TRADE_999",
            symbol="BTC/USDT",
            direction="LONG",
            exit_price=95000.0,
            pnl_pct=5.0,
            outcome="WIN_TP2",
            exit_reason="TP2_HIT"
        )
        await repo.on_trade_closed(closed_event)

        # Active signal must be evicted atomically!
        active_post = await repo.get_active_signals()
        assert "BTC/USDT_5m" not in active_post

    asyncio.run(run_test())
