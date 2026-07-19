"""Paper fill simulator tests — written before the simulator.

Ground truth: Stage 3 §6.1 verbatim semantics — fills at the current best
OPPOSING quote or worse, never mid, never maker; depth-capped; resting
orders fill only when the opposing quote crosses our limit, at OUR limit;
idempotent submission via client_order_id == intent_id.
"""

from datetime import timedelta

import pytest

from apacenye.config import RiskConfig
from apacenye.contract import (
    Action, MarketSnapshot, OrderIntent, QuoteSeen, Side, SizingTrace, utcnow,
)
from apacenye.execution.paper import PaperExecutionClient
from apacenye.execution.live import LiveDisabledError, make_live_client


def make_intent(**kw) -> OrderIntent:
    now = utcnow()
    defaults = dict(
        strategy_id="W1", market_ticker="T1", side=Side.YES, action=Action.OPEN,
        limit_price_dollars=0.50, size_contracts=40, ttl_seconds=600,
        model_probability=0.57, market_implied_probability=0.47, net_edge=0.06,
        confidence=0.6, key_inputs={"forecast_ts": now.isoformat()},
        sizing=SizingTrace(p_used=0.52, kelly_f=0.077, k=0.25, lam=0.5,
                           bankroll_seen_dollars=1000.0),
        rationale="test",
        quote_seen=QuoteSeen(bid_dollars=0.46, ask_dollars=0.48, bid_depth=200,
                             ask_depth=300, ts=now),
    )
    defaults.update(kw)
    return OrderIntent(**defaults)


def snap(bid=0.46, ask=0.48, bid_depth=200, ask_depth=300, ticker="T1"):
    return MarketSnapshot(ticker=ticker, yes_bid_dollars=bid, yes_ask_dollars=ask,
                          yes_bid_depth=bid_depth, yes_ask_depth=ask_depth)


@pytest.fixture()
def sim():
    snaps = {"T1": snap()}
    client = PaperExecutionClient(get_snapshot=snaps.get, risk=RiskConfig())
    client._snaps = snaps  # test hook
    return client


def test_marketable_buy_fills_at_ask_never_mid(sim):
    fills = sim.submit(make_intent(), final_size=40)
    assert len(fills) == 1
    assert fills[0].price_dollars == pytest.approx(0.48)  # the ask, not mid 0.47
    assert fills[0].count == 40
    assert fills[0].fee_dollars == pytest.approx(0.70)  # ceil(0.07×40×0.48×0.52 = 0.699)


def test_buy_above_limit_rests(sim):
    sim._snaps["T1"] = snap(ask=0.55)  # ask above our 0.50 limit
    fills = sim.submit(make_intent(), final_size=40)
    assert fills == []
    assert sim.resting_count() == 1


def test_resting_order_fills_at_our_limit_when_crossed(sim):
    sim._snaps["T1"] = snap(ask=0.55)
    sim.submit(make_intent(), final_size=40)
    # ask drops through our limit to 0.44 → we fill at OUR limit 0.50 (taker
    # model: no price improvement, no queue priority awarded to ourselves)
    fills = sim.on_snapshot(snap(ask=0.44, ask_depth=1000))
    assert len(fills) == 1
    assert fills[0].price_dollars == pytest.approx(0.50)
    assert sim.resting_count() == 0


def test_fill_depth_capped_and_remainder_rests(sim):
    sim._snaps["T1"] = snap(ask_depth=100)  # 25% × 100 = 25 fill now
    fills = sim.submit(make_intent(), final_size=40)
    assert fills[0].count == 25
    assert sim.resting_count() == 1  # 15 contracts rest


def test_duplicate_submit_is_idempotent(sim):
    intent = make_intent()
    first = sim.submit(intent, final_size=40)
    retry = sim.submit(intent, final_size=40)  # e.g. rate-limit retry path
    assert len(first) == 1
    assert retry == []  # duplicate client_order_id: same order, no new fills


def test_expired_resting_order_is_dropped(sim):
    sim._snaps["T1"] = snap(ask=0.55)
    intent = make_intent(ttl_seconds=60)
    sim.submit(intent, final_size=40)
    expired = sim.expire_stale(now=utcnow() + timedelta(seconds=61))
    assert expired == [intent.intent_id]
    assert sim.resting_count() == 0


def test_cancel_removes_resting_order(sim):
    sim._snaps["T1"] = snap(ask=0.55)
    intent = make_intent()
    sim.submit(intent, final_size=40)
    assert sim.cancel(intent.intent_id) is True
    assert sim.resting_count() == 0


def test_buy_no_uses_mirror_of_yes_bid(sim):
    # NO ask = 1 − YES bid = 0.54; a NO buy limit 0.55 is marketable at 0.54.
    fills = sim.submit(make_intent(side=Side.NO, limit_price_dollars=0.55), final_size=10)
    assert len(fills) == 1
    assert fills[0].price_dollars == pytest.approx(0.54)


def test_sell_fills_at_bid(sim):
    # reduce = sell YES: executable at the bid (0.46), limit is the floor
    fills = sim.submit(make_intent(action=Action.REDUCE, limit_price_dollars=0.45),
                       final_size=10)
    assert len(fills) == 1
    assert fills[0].price_dollars == pytest.approx(0.46)


def test_no_snapshot_means_order_rests(sim):
    fills = sim.submit(make_intent(market_ticker="UNKNOWN"), final_size=10)
    assert fills == []
    assert sim.resting_count() == 1


def test_live_client_is_unreachable():
    # ALWAYS-APPLY RULE 1: no live order-submission code exists to enable.
    with pytest.raises(LiveDisabledError):
        make_live_client()
