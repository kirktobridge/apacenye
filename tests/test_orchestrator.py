"""Orchestrator end-to-end tests: the full order path with real components
(only the market data feed and NWS adapter are faked). Covers the wiring the
unit tests can't: intent → gates → paper fill → ledger position →
explanation; the ack-gated START; the kill watcher; heartbeat supervision.
"""

import asyncio
from datetime import timedelta

import pytest

from apacenye.checkpoint.ack import AckLog, risk_relevant_config_hash
from apacenye.config import AppSettings, RiskConfig
from apacenye.contract import (
    Action, DispositionStatus, LifecycleState, MarketSnapshot, RunMode, Side,
    Tick, utcnow,
)
from apacenye.dataadapters.nws import ForecastHigh
from apacenye.marketdata.catalog import MarketCatalog, MarketInfo
from apacenye.orchestrator.kill import KillSwitch
from apacenye.orchestrator.ledger import Ledger
from apacenye.orchestrator.orchestrator import Orchestrator
from apacenye.scheduler import TickScheduler
from apacenye.marketdata.snapshots import SnapshotCache
from apacenye.workers.w1_forecast import W1ForecastWorker

from tests.test_w1_worker import FakeAdapter  # reuse the canned NWS adapter

EVENT = "KXHIGHNY-26JUL18"
TICKER = f"{EVENT}-B85"


def write_paper_ack(log_path, risk: RiskConfig, strategy_id="W1") -> None:
    AckLog(log_path).append({
        "gate": "paper", "strategy_id": strategy_id,
        "config_hash": risk_relevant_config_hash(risk), "result": "PASSED",
    })


def build(tmp_path, run_mode=RunMode.PAPER, with_ack=True):
    settings = AppSettings(run_mode=run_mode, data_dir=tmp_path / "data")
    risk = RiskConfig()
    ledger = Ledger(settings.db_path, risk.bankroll_usd)
    catalog = MarketCatalog()
    catalog.add(MarketInfo(TICKER, EVENT, bracket_lo=85, bracket_hi=89))
    ledger.upsert_market(TICKER, EVENT, 85, 89)
    cache = SnapshotCache()
    orch = Orchestrator(settings, risk, ledger, KillSwitch(settings.kill_sentinel_path),
                        cache, catalog, TickScheduler())
    if with_ack:
        write_paper_ack(settings.ack_log_path, risk)
    worker = W1ForecastWorker("W1", {
        "station": "KNYC", "grid_office": "OKX", "grid_x": 33, "grid_y": 37,
        "event_ticker": EVENT, "sigma_f": 3.0,
    }, orch.make_context(), adapter=FakeAdapter())
    orch.register_worker(worker, cadence_s=600)
    cache.update(MarketSnapshot(ticker=TICKER, event_ticker=EVENT,
                                yes_bid_dollars=0.46, yes_ask_dollars=0.48,
                                yes_bid_depth=500, yes_ask_depth=500))
    return orch, worker, ledger, cache


async def drain(orch):
    while not orch.queue.empty():
        await orch.dispatch(await orch.queue.get())


async def test_full_paper_order_path(tmp_path):
    orch, worker, ledger, cache = build(tmp_path)
    await worker.initialize()
    ok, reason = orch.start_strategy("W1")
    assert ok, reason
    await worker.on_tick(Tick(strategy_id="W1", now=utcnow()))
    await drain(orch)
    # intent flowed through gates and filled at the ask in the simulator
    positions = ledger.open_positions("W1")
    assert len(positions) == 1
    assert positions[0]["count"] == 39
    intents = ledger.recent_intents()
    assert intents[0]["status"] == "APPROVED"
    # explanation persisted with execution appended
    exp = ledger.get_explanation(intents[0]["intent_id"])
    assert exp is not None
    assert exp["execution"]["avg_price_dollars"] == pytest.approx(0.48)
    assert exp["disposition"]["status"] == "APPROVED"
    # shadow evaluation recorded too
    assert len(ledger.recent_evaluations("W1")) == 1


async def test_start_refused_without_paper_ack(tmp_path):
    orch, worker, *_ = build(tmp_path, with_ack=False)
    await worker.initialize()
    ok, reason = orch.start_strategy("W1")
    assert not ok
    assert "acknowledgment" in reason
    assert worker.state is LifecycleState.INIT


async def test_start_refused_when_ack_is_for_stale_config(tmp_path):
    orch, worker, *_ = build(tmp_path, with_ack=False)
    # ack exists, but for a different (older) risk config
    write_paper_ack(orch.settings.ack_log_path, RiskConfig(bankroll_usd=2000))
    await worker.initialize()
    ok, reason = orch.start_strategy("W1")
    assert not ok and "acknowledgment" in reason


async def test_start_refused_while_killed(tmp_path):
    orch, worker, *_ = build(tmp_path)
    await worker.initialize()
    orch.kill.trip("test", "drill")
    ok, reason = orch.start_strategy("W1")
    assert not ok and "kill" in reason.lower()


async def test_dry_run_logs_but_never_fills(tmp_path):
    orch, worker, ledger, cache = build(tmp_path, run_mode=RunMode.DRY_RUN)
    await worker.initialize()
    assert orch.start_strategy("W1")[0]
    await worker.on_tick(Tick(strategy_id="W1", now=utcnow()))
    await drain(orch)
    assert ledger.open_positions() == []  # zero state mutation
    intents = ledger.recent_intents()
    assert intents[0]["status"] == "APPROVED"  # gates ran for real
    exp = ledger.get_explanation(intents[0]["intent_id"])
    assert exp["execution"] == {"dry_run": True}


async def test_kill_watcher_pauses_all_and_cancels_resting(tmp_path):
    orch, worker, ledger, cache = build(tmp_path)
    await worker.initialize()
    assert orch.start_strategy("W1")[0]
    # park a resting order: the worker decides at ask 0.48, then the book
    # moves up before submission, so the limit-0.48 order can't fill and rests
    await worker.on_tick(Tick(strategy_id="W1", now=utcnow()))
    cache.update(MarketSnapshot(ticker=TICKER, event_ticker=EVENT,
                                yes_bid_dollars=0.48, yes_ask_dollars=0.51,
                                yes_bid_depth=500, yes_ask_depth=500))
    await drain(orch)
    assert orch.paper.resting_count() == 1
    # trip the kill and run one watcher cycle
    orch.kill.trip("cli", "drill")
    orch._running = True
    task = asyncio.create_task(orch.kill_watcher(poll_s=0.01))
    await asyncio.sleep(0.05)
    orch._running = False
    task.cancel()
    assert worker.state is LifecycleState.PAUSE
    assert orch.paper.resting_count() == 0
    # un-kill does NOT auto-resume; START re-checks the gates
    orch.kill.clear()
    assert worker.state is LifecycleState.PAUSE


async def test_heartbeat_supervisor_pauses_silent_worker(tmp_path):
    orch, worker, ledger, cache = build(tmp_path)
    await worker.initialize()
    assert orch.start_strategy("W1")[0]
    orch._last_heartbeat["W1"] = utcnow() - timedelta(seconds=300)  # silent 5 min
    orch._running = True
    task = asyncio.create_task(orch.heartbeat_supervisor(poll_s=0.01))
    await asyncio.sleep(0.05)
    orch._running = False
    task.cancel()
    assert worker.state is LifecycleState.PAUSE


async def test_settlement_realizes_and_cancels_resting(tmp_path):
    orch, worker, ledger, cache = build(tmp_path)
    await worker.initialize()
    assert orch.start_strategy("W1")[0]
    await worker.on_tick(Tick(strategy_id="W1", now=utcnow()))
    await drain(orch)
    await orch.on_settlement(TICKER, Side.YES)
    assert ledger.open_positions() == []
    assert ledger.realized_pnl_today_dollars("W1") > 0  # 39 @ .48 won


async def test_worker_cannot_reach_execution(tmp_path):
    # Structural guarantee: nothing in the WorkerContext references the
    # execution client or the Kalshi client.
    orch, worker, *_ = build(tmp_path)
    ctx_attrs = vars(worker.ctx)
    for value in ctx_attrs.values():
        assert not isinstance(value, type(orch.paper))
    assert not hasattr(worker, "paper") and not hasattr(worker.ctx, "paper")
