"""Ledger tests — written before the ledger (Stage 5 test-first constraint).

The SQLite ledger is the single source of truth for positions, cash, and
exposure. These tests exercise the money-critical paths: fills → positions,
exposure aggregation (all brackets of one event = ONE exposure, OD-7),
settlement realization, and daily P&L.
"""

import pytest

from apacenye.contract import Action, Fill, Side, utcnow
from apacenye.orchestrator.ledger import Ledger


@pytest.fixture()
def ledger(tmp_path):
    led = Ledger(tmp_path / "test.sqlite", initial_bankroll_dollars=1000.0)
    led.upsert_market("HIGHNY-26JUL18-B85", "HIGHNY-26JUL18", bracket_lo=85, bracket_hi=89)
    led.upsert_market("HIGHNY-26JUL18-B90", "HIGHNY-26JUL18", bracket_lo=90, bracket_hi=None)
    led.upsert_market("HIGHCHI-26JUL18-B80", "HIGHCHI-26JUL18", bracket_lo=80, bracket_hi=84)
    yield led
    led.close()


def _fill(ticker="HIGHNY-26JUL18-B85", strategy="W1", count=40, price=0.48,
          fee=0.68, side=Side.YES, action=Action.OPEN, intent="i1"):
    return Fill(
        order_id=intent, intent_id=intent, strategy_id=strategy,
        market_ticker=ticker, side=side, action=action,
        price_dollars=price, count=count, fee_dollars=fee,
    )


def test_fill_creates_position_and_debits_cash(ledger):
    ledger.record_fill(_fill())
    positions = ledger.open_positions()
    assert len(positions) == 1
    pos = positions[0]
    assert pos["count"] == 40
    assert pos["cost_basis_dollars"] == pytest.approx(19.20)
    assert pos["fees_paid_dollars"] == pytest.approx(0.68)
    # equity = bankroll + realized pnl; nothing realized yet
    assert ledger.equity_dollars() == pytest.approx(1000.0)


def test_same_event_brackets_are_one_exposure(ledger):
    # OD-7: two brackets of the same settlement event aggregate into one
    # exposure figure, even across strategies.
    ledger.record_fill(_fill(ticker="HIGHNY-26JUL18-B85", strategy="W1", intent="i1"))
    ledger.record_fill(_fill(ticker="HIGHNY-26JUL18-B90", strategy="W2",
                             count=10, price=0.10, fee=0.07, intent="i2"))
    exp = ledger.event_exposure_dollars("HIGHNY-26JUL18")
    assert exp == pytest.approx(40 * 0.48 + 10 * 0.10)
    # a different event is separate
    assert ledger.event_exposure_dollars("HIGHCHI-26JUL18") == 0.0


def test_strategy_and_portfolio_exposure(ledger):
    ledger.record_fill(_fill(strategy="W1", intent="i1"))
    ledger.record_fill(_fill(ticker="HIGHCHI-26JUL18-B80", strategy="W1",
                             count=20, price=0.30, fee=0.42, intent="i2"))
    ledger.record_fill(_fill(ticker="HIGHNY-26JUL18-B90", strategy="W2",
                             count=10, price=0.10, fee=0.07, intent="i3"))
    assert ledger.strategy_exposure_dollars("W1") == pytest.approx(19.20 + 6.00)
    assert ledger.portfolio_exposure_dollars() == pytest.approx(19.20 + 6.00 + 1.00)


def test_settlement_win_realizes_pnl(ledger):
    ledger.record_fill(_fill())  # 40 @ 0.48, fee 0.68 → cost 19.88
    ledger.settle_market("HIGHNY-26JUL18-B85", settled_side=Side.YES)
    assert ledger.open_positions() == []
    # payout 40 × $1 = 40; realized = 40 − 19.20 − 0.68 = +20.12
    assert ledger.realized_pnl_today_dollars("W1") == pytest.approx(20.12)
    assert ledger.equity_dollars() == pytest.approx(1020.12)


def test_settlement_loss_realizes_full_cost(ledger):
    ledger.record_fill(_fill())
    ledger.settle_market("HIGHNY-26JUL18-B85", settled_side=Side.NO)
    assert ledger.realized_pnl_today_dollars("W1") == pytest.approx(-19.88)
    assert ledger.equity_dollars() == pytest.approx(980.12)


def test_no_side_position_wins_when_market_settles_no(ledger):
    # Buying NO at 52¢ (mirror of YES bid 48¢) wins if the bracket misses.
    ledger.record_fill(_fill(side=Side.NO, price=0.52, fee=0.70))
    ledger.settle_market("HIGHNY-26JUL18-B85", settled_side=Side.NO)
    # payout 40 − cost 20.80 − fee 0.70 = +18.50
    assert ledger.realized_pnl_today_dollars("W1") == pytest.approx(18.50)


def test_reduce_realizes_proportional_pnl(ledger):
    ledger.record_fill(_fill(count=40, price=0.48, fee=0.68))
    # sell 20 at 0.60; exit fee 0.34
    ledger.record_fill(_fill(count=20, price=0.60, fee=0.34,
                             action=Action.REDUCE, intent="i2"))
    pos = ledger.open_positions()[0]
    assert pos["count"] == 20
    assert pos["cost_basis_dollars"] == pytest.approx(9.60)
    # realized: (0.60 − 0.48) × 20 − exit fee 0.34 − entry-fee share 0.34 = 1.72
    assert ledger.realized_pnl_today_dollars("W1") == pytest.approx(2.40 - 0.34 - 0.34)


def test_duplicate_fill_id_is_ignored(ledger):
    f = _fill()
    ledger.record_fill(f)
    ledger.record_fill(f)  # replay of the same fill must not double-count
    assert ledger.open_positions()[0]["count"] == 40


def test_daily_pnl_includes_unrealized_marks(ledger):
    ledger.record_fill(_fill())  # cost basis 19.20, fees 0.68
    # marked at mid 0.55: unrealized = 40×0.55 − 19.20 − 0.68 = +2.12
    day = ledger.day_pnl_dollars("W1", marks={"HIGHNY-26JUL18-B85": 0.55})
    assert day == pytest.approx(40 * 0.55 - 19.88)
