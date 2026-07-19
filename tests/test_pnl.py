"""P&L math tests — written BEFORE the implementation.

Ground truth: Stage 1 §1.5 worked trade and the settlement rules ($1 / $0,
no settlement fee; exits before settlement pay the fee again).
"""

import pytest

from apacenye.domain.fees import order_fee_dollars
from apacenye.domain.pnl import (
    entry_cost_dollars,
    settlement_pnl_dollars,
    early_exit_pnl_dollars,
)


def test_stage1_worked_trade_win():
    # Buy 100 YES at 57¢: pay $57.00 + $1.72 fee = $58.72. Settles YES → +$41.28.
    cost = entry_cost_dollars(count=100, price_dollars=0.57)
    assert cost == pytest.approx(58.72)
    pnl = settlement_pnl_dollars(count=100, price_dollars=0.57, won=True)
    assert pnl == pytest.approx(41.28)


def test_stage1_worked_trade_loss():
    # Settles NO → the full entry cost (incl. fee) is the realized loss.
    pnl = settlement_pnl_dollars(count=100, price_dollars=0.57, won=False)
    assert pnl == pytest.approx(-58.72)


def test_early_exit_charges_fee_on_both_legs():
    # Buy 100 at 60¢, sell at 70¢ before settlement.
    entry_fee = order_fee_dollars(100, 0.60)   # ceil(1.68) = 1.68
    exit_fee = order_fee_dollars(100, 0.70)    # ceil(1.47) = 1.47
    pnl = early_exit_pnl_dollars(count=100, entry_price_dollars=0.60,
                                 exit_price_dollars=0.70)
    assert pnl == pytest.approx((0.70 - 0.60) * 100 - entry_fee - exit_fee)


def test_early_exit_can_lose_more_than_price_move_due_to_fees():
    # Flat exit (60¢ → 60¢) still loses both fees: the round trip is never free.
    pnl = early_exit_pnl_dollars(100, 0.60, 0.60)
    assert pnl == pytest.approx(-2 * order_fee_dollars(100, 0.60))
    assert pnl < 0


def test_worst_case_loss_equals_entry_cost():
    # Fully collateralized (Stage 1 §1.1): the maximum possible loss on any
    # position is exactly what was paid for it, fee included.
    for count, price in [(1, 0.01), (100, 0.50), (37, 0.93)]:
        assert settlement_pnl_dollars(count, price, won=False) == pytest.approx(
            -entry_cost_dollars(count, price)
        )
