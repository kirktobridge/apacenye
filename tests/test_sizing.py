"""Sizing tests — written BEFORE the implementation (Stage 5 test-first constraint).

Ground truth: Stage 2 §1.3–§1.4 (Kelly, shrinkage, quarter-Kelly) and §5 /
Stage 3 §3 (hard caps ALWAYS dominate Kelly). The caps here are the worker's
first-line self-check; the orchestrator's gate pipeline re-enforces them.
"""

import pytest

from apacenye.domain.sizing import (
    kelly_fraction,
    shrink_probability,
    proposed_stake_dollars,
    clamp_contracts_to_caps,
)


def test_kelly_textbook_case():
    # Stage 2 §1.3: cost 50¢, p 0.60 → b = 1 → f* = 0.60 − 0.40 = 20%
    assert kelly_fraction(p_win=0.60, cost_dollars=0.50) == pytest.approx(0.20)


def test_kelly_zero_when_no_edge():
    assert kelly_fraction(p_win=0.50, cost_dollars=0.50) == 0.0
    assert kelly_fraction(p_win=0.30, cost_dollars=0.50) == 0.0  # negative edge floors at 0


def test_shrinkage_lambda_half():
    # Checkpoint question K2: p_model 0.60, p_market 0.50, λ 0.5 → 0.55
    assert shrink_probability(0.60, 0.50, lam=0.5) == pytest.approx(0.55)


def test_stage2_worked_sizing_example():
    # Stage 2 §3.1: p_model 0.57, mid 0.47, ask 0.48, bankroll $1,000.
    # p_used = 0.52; b = 0.52/0.48; f* ≈ 7.7%; quarter-Kelly ≈ 1.9% ≈ $19 ≈ 40 contracts.
    p_used = shrink_probability(0.57, 0.47, lam=0.5)
    assert p_used == pytest.approx(0.52)
    f_star = kelly_fraction(p_used, cost_dollars=0.48)
    assert f_star == pytest.approx(0.0769, abs=0.0005)
    stake = proposed_stake_dollars(
        p_model=0.57, p_market=0.47, cost_dollars=0.48,
        bankroll_dollars=1000.0, lam=0.5, k=0.25,
    )
    assert stake == pytest.approx(19.23, abs=0.05)
    assert int(stake / 0.48) == 40


def test_caps_always_dominate_kelly():
    # A wildly confident model (p_used → 0.99 at a cheap price) produces a huge
    # Kelly stake; the caps must clamp it regardless. Per-event headroom $50
    # (5% × $1,000), 100-contract order cap, 25%-of-depth cap.
    stake = proposed_stake_dollars(
        p_model=0.99, p_market=0.99, cost_dollars=0.10,
        bankroll_dollars=1000.0, lam=0.5, k=0.25,
    )
    kelly_contracts = int(stake / 0.10)
    assert kelly_contracts > 100  # Kelly alone would breach the order cap

    final, caps_applied = clamp_contracts_to_caps(
        kelly_contracts,
        price_dollars=0.10,
        event_headroom_dollars=50.0,
        strategy_headroom_dollars=200.0,
        portfolio_headroom_dollars=500.0,
        top_of_book_depth=1000,
        max_depth_fraction=0.25,
        max_order_contracts=100,
    )
    # min(event $50/0.10 = 500 … order cap 100, depth 250) → the 100 cap binds
    assert final == 100
    assert "max_order_contracts" in caps_applied


def test_depth_cap_binds_when_book_is_thin():
    final, caps_applied = clamp_contracts_to_caps(
        400, price_dollars=0.50,
        event_headroom_dollars=1000.0, strategy_headroom_dollars=1000.0,
        portfolio_headroom_dollars=1000.0,
        top_of_book_depth=40, max_depth_fraction=0.25, max_order_contracts=100,
    )
    assert final == 10  # 25% of 40
    assert "max_depth_fraction" in caps_applied


def test_event_headroom_binds_and_composition_is_min_of_all():
    # Composition rule (Stage 3 §3.1): effective size = min over all caps.
    final, caps_applied = clamp_contracts_to_caps(
        90, price_dollars=0.50,
        event_headroom_dollars=10.0,   # → 20 contracts at 50¢
        strategy_headroom_dollars=100.0,
        portfolio_headroom_dollars=15.0,  # → 30 contracts; portfolio > event here
        top_of_book_depth=1000, max_depth_fraction=0.25, max_order_contracts=100,
    )
    assert final == 20
    assert "max_event_exposure" in caps_applied


def test_zero_headroom_means_zero_contracts():
    final, _ = clamp_contracts_to_caps(
        50, price_dollars=0.50,
        event_headroom_dollars=0.0, strategy_headroom_dollars=100.0,
        portfolio_headroom_dollars=100.0,
        top_of_book_depth=1000, max_depth_fraction=0.25, max_order_contracts=100,
    )
    assert final == 0
