"""Fee math tests — written BEFORE the implementation (Stage 5 test-first constraint).

Ground truth: Stage 1 §1.5. fee = 0.07 × C × P × (1−P), rounded UP to the
next cent per executed order. Qualification math uses the raw per-contract
fee (no rounding); order accounting uses the rounded-up order fee.
"""

import pytest

from apacenye.domain.fees import (
    order_fee_dollars,
    per_contract_fee_dollars,
    net_edge,
    breakeven_probability,
)


def test_worked_example_stage1_100_at_50c():
    # 0.07 × 100 × 0.5 × 0.5 = 1.75 exactly (checkpoint question K4's answer)
    assert order_fee_dollars(100, 0.50) == pytest.approx(1.75)


def test_rounding_up_per_order():
    # 1 contract at 50¢: raw fee 0.0175 → rounds UP to 2 cents
    assert order_fee_dollars(1, 0.50) == pytest.approx(0.02)
    # 100 contracts at 95¢: raw 0.07×100×0.95×0.05 = 0.3325 → 0.34
    assert order_fee_dollars(100, 0.95) == pytest.approx(0.34)
    # 100 contracts at 57¢: raw 0.07×100×0.57×0.43 = 1.7157 → 1.72 (Stage 1 worked trade)
    assert order_fee_dollars(100, 0.57) == pytest.approx(1.72)


def test_fee_symmetric_in_price():
    assert order_fee_dollars(100, 0.30) == order_fee_dollars(100, 0.70)


def test_fee_maximal_at_50c():
    fees = [per_contract_fee_dollars(p / 100) for p in range(1, 100)]
    assert max(fees) == pytest.approx(per_contract_fee_dollars(0.50))


def test_per_contract_fee_unrounded():
    # Qualification uses the raw formula: 0.07 × 0.48 × 0.52 = 0.017472
    assert per_contract_fee_dollars(0.48) == pytest.approx(0.017472)


def test_net_edge_worked_example_stage2():
    # Stage 2 §3.1: p_model 0.57, ask 0.48 → net edge ≈ 0.0625, qualifies (≥ 0.04)
    edge = net_edge(p_model=0.57, executable_price_dollars=0.48)
    assert edge == pytest.approx(0.57 - 0.48 - 0.017472 - 0.01)
    assert edge >= 0.04


def test_net_edge_fails_qualification_when_gap_too_small():
    # 3 points of gross edge cannot survive fee + slippage + 4-pt floor
    assert net_edge(p_model=0.51, executable_price_dollars=0.48) < 0.04


def test_breakeven_probability():
    # Stage 1 §2 worked qualification: at a 48¢ ask the model must clear
    # 0.48 + 0.017472 + 0.01 + 0.04 = 0.547472 to qualify
    be = breakeven_probability(0.48, slippage_dollars=0.01, min_edge=0.04)
    assert be == pytest.approx(0.547472)
