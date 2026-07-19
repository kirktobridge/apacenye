"""W1 worker + lifecycle tests: shadow forecasts on every evaluation,
qualification behavior, PAUSE semantics, staleness cancels."""

from datetime import datetime, timedelta, timezone

import pytest

from apacenye.config import RiskConfig
from apacenye.contract import (
    CancelIntent, Evaluation, Heartbeat, LifecycleState, MarketSnapshot,
    OrderIntent, Tick, utcnow,
)
from apacenye.dataadapters.nws import ForecastHigh
from apacenye.marketdata.catalog import MarketCatalog, MarketInfo
from apacenye.workers.base import WorkerContext
from apacenye.workers.w1_forecast import W1ForecastWorker

EVENT = "KXHIGHNY-26JUL18"


class FakeAdapter:
    def __init__(self, high_f=86.0, source_age_s=600):
        self.high_f = high_f
        self.source_age_s = source_age_s
        self.calls = 0

    async def fetch_forecast_high(self):
        self.calls += 1
        now = datetime.now(timezone.utc)
        return ForecastHigh(
            station="KNYC", high_f=self.high_f,
            source_ts=now - timedelta(seconds=self.source_age_s),
            fetched_ts=now, period_name="Today",
        )


def build_env(bid=0.46, ask=0.48):
    catalog = MarketCatalog()
    catalog.add(MarketInfo(f"{EVENT}-B85", EVENT, bracket_lo=85, bracket_hi=89))
    snaps = {
        f"{EVENT}-B85": MarketSnapshot(
            ticker=f"{EVENT}-B85", event_ticker=EVENT,
            yes_bid_dollars=bid, yes_ask_dollars=ask,
            yes_bid_depth=500, yes_ask_depth=500,
        )
    }
    emitted = []

    async def emit(msg):
        emitted.append(msg)

    ctx = WorkerContext(
        emit=emit,
        get_snapshot=snaps.get,
        get_positions=lambda s: [],
        get_bankroll_dollars=lambda: 1000.0,
        risk=RiskConfig(),
        list_event_brackets=catalog.brackets_of_event,
    )
    config = {"station": "KNYC", "grid_office": "OKX", "grid_x": 33, "grid_y": 37,
              "event_ticker": EVENT, "sigma_f": 3.0}
    return ctx, config, emitted, snaps


async def make_started_worker(adapter=None, **env_kw):
    ctx, config, emitted, snaps = build_env(**env_kw)
    worker = W1ForecastWorker("W1", config, ctx, adapter=adapter or FakeAdapter())
    await worker.initialize()
    worker.start()
    return worker, emitted, snaps


async def test_init_requires_config_keys():
    ctx, config, _, _ = build_env()
    del config["station"]
    worker = W1ForecastWorker("W1", config, ctx, adapter=FakeAdapter())
    with pytest.raises(ValueError, match="station"):
        await worker.initialize()


async def test_qualified_bracket_emits_intent_and_shadow():
    # μ=86 σ=3 → p(85–89) ≈ 0.57; ask 0.48 → net edge ≈ 0.0625 ≥ 0.04
    worker, emitted, _ = await make_started_worker()
    await worker.on_tick(Tick(strategy_id="W1", now=utcnow()))
    intents = [m for m in emitted if isinstance(m, OrderIntent)]
    evals = [m for m in emitted if isinstance(m, Evaluation)]
    beats = [m for m in emitted if isinstance(m, Heartbeat)]
    assert len(intents) == 1
    assert len(evals) == 1 and evals[0].qualified
    assert len(beats) == 1
    intent = intents[0]
    assert intent.side.value == "yes"
    assert intent.limit_price_dollars == pytest.approx(0.48)
    # Stage 2's worked example says ≈40 with p rounded to 0.57; exact
    # p=0.5698 gives 39 — same math, unrounded input.
    assert intent.size_contracts == 39
    assert "forecast_ts" in intent.key_inputs
    assert intent.sizing.p_used == pytest.approx(0.52, abs=0.005)


async def test_unqualified_bracket_still_logs_shadow_forecast():
    # market fairly priced at the model's own probability → no edge, no intent
    worker, emitted, _ = await make_started_worker(bid=0.56, ask=0.58)
    await worker.on_tick(Tick(strategy_id="W1", now=utcnow()))
    assert [m for m in emitted if isinstance(m, OrderIntent)] == []
    evals = [m for m in emitted if isinstance(m, Evaluation)]
    assert len(evals) == 1 and not evals[0].qualified  # the calibration record survives


async def test_overpriced_bracket_proposes_no_side():
    # market at 80¢ for a ~57% bracket → buy NO at 1 − bid = 0.21, p_no ≈ 0.43
    worker, emitted, _ = await make_started_worker(bid=0.79, ask=0.81)
    await worker.on_tick(Tick(strategy_id="W1", now=utcnow()))
    intents = [m for m in emitted if isinstance(m, OrderIntent)]
    assert len(intents) == 1
    assert intents[0].side.value == "no"
    assert intents[0].limit_price_dollars == pytest.approx(0.21)


async def test_pause_stops_intents_immediately_but_heartbeats_continue():
    worker, emitted, _ = await make_started_worker()
    worker.pause()
    await worker.on_tick(Tick(strategy_id="W1", now=utcnow()))
    assert worker.state is LifecycleState.PAUSE
    assert [m for m in emitted if isinstance(m, OrderIntent)] == []
    assert len([m for m in emitted if isinstance(m, Heartbeat)]) == 1


async def test_stale_forecast_blocks_intents_and_cancels_outstanding():
    worker, emitted, _ = await make_started_worker()
    await worker.on_tick(Tick(strategy_id="W1", now=utcnow()))
    assert len([m for m in emitted if isinstance(m, OrderIntent)]) == 1
    # forecast goes stale (13h old); refresh returns equally stale data
    worker.adapter = FakeAdapter(source_age_s=13 * 3600)
    worker.forecast = None
    await worker.on_tick(Tick(strategy_id="W1", now=utcnow()))
    assert len([m for m in emitted if isinstance(m, OrderIntent)]) == 1  # no new ones
    cancels = [m for m in emitted if isinstance(m, CancelIntent)]
    assert len(cancels) == 1  # outstanding intent withdrawn
    evals = [m for m in emitted if isinstance(m, Evaluation)]
    assert "stale" in evals[-1].note


async def test_event_cap_self_enforced_across_brackets():
    # Give the worker existing positions worth $45 in this event → only $5
    # of budget; at ask 0.48 that is 10 contracts max.
    ctx, config, emitted, snaps = build_env()
    ctx.get_positions = lambda s: [{"event_ticker": EVENT, "cost_basis_dollars": 45.0}]
    worker = W1ForecastWorker("W1", config, ctx, adapter=FakeAdapter())
    await worker.initialize()
    worker.start()
    await worker.on_tick(Tick(strategy_id="W1", now=utcnow()))
    intents = [m for m in emitted if isinstance(m, OrderIntent)]
    assert len(intents) == 1
    assert intents[0].size_contracts == 10
    assert "max_event_exposure" in intents[0].sizing.caps_applied


async def test_update_config_rejects_station_change():
    worker, _, _ = await make_started_worker()
    ok, reason = await worker.update_config({"station": "KMIA"})
    assert not ok and "restart" in reason
    ok, _ = await worker.update_config({"sigma_f": 2.5})
    assert ok and worker.config["sigma_f"] == 2.5
