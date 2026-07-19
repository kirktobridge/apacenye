"""Gate-pipeline tests (G0–G10) — written before the engine.

Because only one worker exists in this bootstrap, the aggregate-exposure,
correlation, and daily-loss gates are exercised with SYNTHETIC multi-position
scenarios (Stage 5 cross-review hardening requirement) — they must not go
live-untested just because W1 alone can't trigger them.
"""

from datetime import datetime, timedelta, timezone

import pytest

from apacenye.config import RiskConfig
from apacenye.contract import (
    Action,
    DispositionStatus,
    Fill,
    LifecycleState,
    MarketSnapshot,
    OrderIntent,
    QuoteSeen,
    RunMode,
    Side,
    SizingTrace,
    utcnow,
)
from apacenye.orchestrator.kill import KillSwitch
from apacenye.orchestrator.ledger import Ledger
from apacenye.orchestrator.risk_engine import RiskEngine


EVENT_NY = "HIGHNY-26JUL18"
TICKER_B85 = "HIGHNY-26JUL18-B85"
TICKER_B90 = "HIGHNY-26JUL18-B90"
EVENT_CHI = "HIGHCHI-26JUL18"
TICKER_CHI = "HIGHCHI-26JUL18-B80"


def make_intent(**kw) -> OrderIntent:
    now = utcnow()
    defaults = dict(
        strategy_id="W1",
        market_ticker=TICKER_B85,
        side=Side.YES,
        action=Action.OPEN,
        limit_price_dollars=0.50,
        size_contracts=40,
        ttl_seconds=600,
        model_probability=0.57,
        market_implied_probability=0.47,
        net_edge=0.06,
        confidence=0.6,
        key_inputs={"nws_forecast_high": 86, "sigma": 3.0, "forecast_ts": now.isoformat()},
        sizing=SizingTrace(p_used=0.52, kelly_f=0.077, k=0.25, lam=0.5,
                           bankroll_seen_dollars=1000.0),
        rationale="test intent",
        quote_seen=QuoteSeen(bid_dollars=0.46, ask_dollars=0.48, bid_depth=200,
                             ask_depth=300, ts=now),
    )
    defaults.update(kw)
    return OrderIntent(**defaults)


class Env:
    """Minimal orchestrator environment around the engine for tests."""

    def __init__(self, tmp_path, risk: RiskConfig | None = None, run_mode=RunMode.PAPER):
        self.ledger = Ledger(tmp_path / "t.sqlite", initial_bankroll_dollars=1000.0)
        for t, e in [(TICKER_B85, EVENT_NY), (TICKER_B90, EVENT_NY), (TICKER_CHI, EVENT_CHI)]:
            self.ledger.upsert_market(t, e)
        self.kill = KillSwitch(tmp_path / "KILL")
        self.snapshots: dict[str, MarketSnapshot] = {}
        self.states: dict[str, LifecycleState] = {"W1": LifecycleState.START,
                                                  "W2": LifecycleState.START}
        self.paused: list[str] = []
        self.set_snapshot(TICKER_B85)
        self.set_snapshot(TICKER_B90)
        self.set_snapshot(TICKER_CHI)
        self.engine = RiskEngine(
            risk=risk or RiskConfig(),
            ledger=self.ledger,
            kill=self.kill,
            run_mode=run_mode,
            get_snapshot=self.snapshots.get,
            get_strategy_state=lambda s: self.states.get(s, LifecycleState.STOP),
            pause_strategy=self._pause,
            staleness_window_s=lambda s: 12 * 3600.0,
        )

    def _pause(self, strategy_id: str, reason: str) -> None:
        self.paused.append(strategy_id)
        self.states[strategy_id] = LifecycleState.PAUSE

    def set_snapshot(self, ticker, bid=0.46, ask=0.48, bid_depth=200, ask_depth=300):
        self.snapshots[ticker] = MarketSnapshot(
            ticker=ticker, yes_bid_dollars=bid, yes_ask_dollars=ask,
            yes_bid_depth=bid_depth, yes_ask_depth=ask_depth,
        )

    def seed_position(self, ticker, strategy="W1", count=40, price=0.48, fee=0.68,
                      side=Side.YES, intent="seed"):
        self.ledger.record_fill(Fill(
            order_id=intent, intent_id=intent, strategy_id=strategy,
            market_ticker=ticker, side=side, action=Action.OPEN,
            price_dollars=price, count=count, fee_dollars=fee,
        ))


@pytest.fixture()
def env(tmp_path):
    e = Env(tmp_path)
    yield e
    e.ledger.close()


def test_clean_intent_is_approved(env):
    d = env.engine.evaluate(make_intent())
    assert d.status is DispositionStatus.APPROVED
    assert d.final_size == 40


def test_g0_expired_ttl_rejected(env):
    stale = make_intent(ts=utcnow() - timedelta(seconds=700))
    d = env.engine.evaluate(stale)
    assert d.status is DispositionStatus.REJECTED
    assert "G0" in d.binding_gates


def test_g1_kill_switch_rejects_worker_intents(env):
    env.kill.trip("test", "drill")
    d = env.engine.evaluate(make_intent())
    assert d.status is DispositionStatus.REJECTED
    assert "G1" in d.binding_gates


def test_g1_human_reduce_allowed_during_kill(env):
    # Stage 3 §5: a human may still flatten during a kill.
    env.seed_position(TICKER_B85)
    env.kill.trip("test", "drill")
    d = env.engine.evaluate(
        make_intent(action=Action.REDUCE, size_contracts=10), human_initiated=True
    )
    assert d.status is not DispositionStatus.REJECTED


def test_g3_paused_strategy_rejected(env):
    env.states["W1"] = LifecycleState.PAUSE
    d = env.engine.evaluate(make_intent())
    assert d.status is DispositionStatus.REJECTED
    assert "G3" in d.binding_gates


def test_g4_stale_key_inputs_rejected(env):
    old = (utcnow() - timedelta(hours=13)).isoformat()
    d = env.engine.evaluate(make_intent(key_inputs={"nws_forecast_high": 86,
                                                    "forecast_ts": old}))
    assert d.status is DispositionStatus.REJECTED
    assert "G4" in d.binding_gates


def test_g5_liquidity_resize_to_quarter_of_depth(env):
    env.set_snapshot(TICKER_B85, ask_depth=100)  # 25% → 25 contracts
    d = env.engine.evaluate(make_intent(size_contracts=40))
    assert d.status is DispositionStatus.RESIZED
    assert d.final_size == 25
    assert "G5" in d.binding_gates


def test_g5_missing_snapshot_rejects(env):
    del env.snapshots[TICKER_B85]
    d = env.engine.evaluate(make_intent())
    assert d.status is DispositionStatus.REJECTED


def test_g6_order_cap_binds(env):
    # price 0.02 keeps dollar caps loose so the 100-contract backstop binds
    env.set_snapshot(TICKER_B85, bid=0.01, ask=0.02, ask_depth=100000)
    d = env.engine.evaluate(make_intent(size_contracts=2000, limit_price_dollars=0.02))
    assert d.status is DispositionStatus.RESIZED
    assert d.final_size == 100
    assert "G6" in d.binding_gates


def test_g7_event_headroom_aggregates_same_event_brackets(env):
    # SYNTHETIC multi-position: 40 @ 0.48 in B85 and 30 @ 0.10 in B90, same
    # event → exposure 19.20 + 3.00 = 22.20 against the $50 cap (5% × 1000).
    env.seed_position(TICKER_B85, count=40, price=0.48, intent="s1")
    env.seed_position(TICKER_B90, count=30, price=0.10, fee=0.19, intent="s2")
    # headroom 27.80 → at limit 0.50 → 55 contracts max
    d = env.engine.evaluate(make_intent(size_contracts=80, market_ticker=TICKER_B90,
                                        limit_price_dollars=0.50))
    assert d.status is DispositionStatus.RESIZED
    assert d.final_size == 55
    assert "G7" in d.binding_gates


def test_g7_counts_positions_from_other_strategies(env):
    # OD-7: event exposure is portfolio-wide, not per-strategy.
    env.seed_position(TICKER_B85, strategy="W2", count=90, price=0.50, fee=1.58, intent="s1")
    d = env.engine.evaluate(make_intent(size_contracts=40))  # headroom $5 → 10 @ 0.50
    assert d.final_size == 10
    assert "G7" in d.binding_gates


def test_reservations_prevent_concurrent_breach(env):
    # Two intents approved back-to-back with no fills in between must not
    # jointly breach the event cap (Stage 3 §3.1 reservation accounting).
    env.set_snapshot(TICKER_B85, ask_depth=1000)  # keep G5 out of the way
    d1 = env.engine.evaluate(make_intent(size_contracts=80, limit_price_dollars=0.50))
    assert d1.final_size == 80  # $40 reserved of the $50 event cap
    d2 = env.engine.evaluate(make_intent(size_contracts=80, limit_price_dollars=0.50))
    assert d2.final_size == 20  # only $10 headroom left
    assert "G7" in d2.binding_gates


def test_reservation_released_on_cancel(env):
    env.set_snapshot(TICKER_B85, ask_depth=1000)
    d1 = env.engine.evaluate(make_intent(size_contracts=80, limit_price_dollars=0.50))
    env.engine.release_reservation(d1.intent_id)
    d2 = env.engine.evaluate(make_intent(size_contracts=80, limit_price_dollars=0.50))
    assert d2.final_size == 80


def test_g8_strategy_cap_across_events(env):
    # SYNTHETIC: W1 holds $150 in NY and $40 in CHI → $190 of the $200 cap.
    # Snapshots are pinned near the entry prices so unrealized marks stay
    # flat and G10 doesn't fire before the gate under test.
    env.seed_position(TICKER_B85, count=100, price=0.75, fee=1.32, intent="s1")
    env.seed_position(TICKER_B90, count=100, price=0.75, fee=1.32, intent="s2")
    env.seed_position(TICKER_CHI, count=80, price=0.50, fee=0.70, intent="s3")
    env.set_snapshot(TICKER_B85, bid=0.74, ask=0.78)
    env.set_snapshot(TICKER_B90, bid=0.74, ask=0.78)
    env.set_snapshot(TICKER_CHI, bid=0.49, ask=0.53)
    d = env.engine.evaluate(make_intent(market_ticker=TICKER_CHI, size_contracts=40,
                                        limit_price_dollars=0.50))
    # strategy headroom $10 → 20 contracts; event CHI headroom also $10 → 20.
    assert d.final_size == 20
    assert "G8" in d.binding_gates


def test_g9_portfolio_cap_always_dominates(tmp_path):
    env = Env(tmp_path)
    try:
        # Portfolio exposure $485 of the $500 cap, spread across 5 events so
        # neither the event cap (G7) nor W1's strategy cap (G8) binds first.
        for i in range(5):
            t, e = f"T{i}", f"E{i}"
            env.ledger.upsert_market(t, e)
            # pin marks near entry so unrealized P&L stays ~flat for G10
            env.set_snapshot(t, bid=0.96, ask=0.98)
            env.states[f"S{i}"] = LifecycleState.START
            env.seed_position(t, strategy=f"S{i}", count=100, price=0.97, fee=0.0, intent=f"s{i}")
        # synthetic exposure seed: 5 × $97 = $485 of the $500 portfolio cap
        d = env.engine.evaluate(make_intent(size_contracts=40, limit_price_dollars=0.50))
        # portfolio headroom $15 → 30 contracts; event headroom $50 → 100.
        assert d.final_size == 30
        assert "G9" in d.binding_gates
    finally:
        env.ledger.close()


def test_g10_strategy_daily_loss_auto_pauses(tmp_path):
    env = Env(tmp_path)
    try:
        # SYNTHETIC: seed a realized loss of $25 (> 2% of $1,000) today.
        env.seed_position(TICKER_B85, count=50, price=0.50, fee=0.88, intent="s1")
        env.ledger.settle_market(TICKER_B85, settled_side=Side.NO)  # −$25.88
        # intent on a still-open bracket (B85 itself is now settled → G0)
        d = env.engine.evaluate(make_intent(market_ticker=TICKER_B90))
        assert d.status is DispositionStatus.REJECTED
        assert "G10" in d.binding_gates
        assert "W1" in env.paused  # auto-PAUSE side effect; human must un-pause
    finally:
        env.ledger.close()


def test_g10_portfolio_daily_loss_trips_kill(tmp_path):
    env = Env(tmp_path)
    try:
        # SYNTHETIC: two strategies jointly lose > 5% of bankroll today.
        env.seed_position(TICKER_B85, strategy="W1", count=60, price=0.50, fee=1.05, intent="s1")
        env.seed_position(TICKER_CHI, strategy="W2", count=60, price=0.50, fee=1.05, intent="s2")
        env.ledger.settle_market(TICKER_B85, settled_side=Side.NO)
        env.ledger.settle_market(TICKER_CHI, settled_side=Side.NO)  # ≈ −$62
        d = env.engine.evaluate(make_intent(market_ticker=TICKER_B90))
        assert d.status is DispositionStatus.REJECTED
        assert env.kill.is_killed()  # OD-17: kill switch trips automatically
    finally:
        env.ledger.close()


def test_dry_run_annotates_but_still_gates(tmp_path):
    env = Env(tmp_path, run_mode=RunMode.DRY_RUN)
    try:
        d = env.engine.evaluate(make_intent())
        assert d.status is DispositionStatus.APPROVED
        assert "dry_run" in d.reason
    finally:
        env.ledger.close()


def test_live_mode_rejected_in_engine_as_defense_in_depth(tmp_path):
    # Boot already refuses LIVE; if it were ever reached, G2 rejects too.
    env = Env(tmp_path, run_mode=RunMode.LIVE)
    try:
        d = env.engine.evaluate(make_intent())
        assert d.status is DispositionStatus.REJECTED
        assert "G2" in d.binding_gates
    finally:
        env.ledger.close()
