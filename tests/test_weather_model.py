"""W1-v0 probability model tests — written BEFORE the implementation.

Ground truth: Stage 2 §3.1. T_max ~ Normal(μ = NWS forecast high, σ), bracket
probability via the normal CDF with a ±0.5°F continuity correction because
brackets are integer-degree.
"""

import pytest

from apacenye.domain.weather import bracket_probability


def test_stage2_worked_example():
    # μ=86, σ=3 → P(85 ≤ Tmax ≤ 89) = Φ(3.5/3) − Φ(−1.5/3) ≈ 0.570
    p = bracket_probability(lo=85, hi=89, mu=86.0, sigma=3.0)
    assert p == pytest.approx(0.5698, abs=0.001)


def test_open_ended_low_bracket():
    # "79 or below" → P(Tmax ≤ 79.5)
    p = bracket_probability(lo=None, hi=79, mu=86.0, sigma=3.0)
    assert p == pytest.approx(0.01513, abs=0.0005)


def test_open_ended_high_bracket():
    # "90 or above" → P(Tmax ≥ 89.5)
    p = bracket_probability(lo=90, hi=None, mu=86.0, sigma=3.0)
    assert p == pytest.approx(0.12167, abs=0.0005)


def test_complete_bracket_set_sums_to_one():
    # A mutually exclusive, exhaustive set must have probabilities summing to 1
    # (the same coherence property the S1 monitor checks on market prices).
    brackets = [(None, 79), (80, 84), (85, 89), (90, None)]
    total = sum(bracket_probability(lo, hi, mu=86.0, sigma=3.0) for lo, hi in brackets)
    assert total == pytest.approx(1.0, abs=1e-9)


def test_symmetric_bracket_centered_on_mu():
    p_below = bracket_probability(None, 85, mu=86.0, sigma=3.0)  # ≤ 85.5
    p_above = bracket_probability(87, None, mu=86.0, sigma=3.0)  # ≥ 86.5
    assert p_below == pytest.approx(p_above, abs=1e-9)


def test_sigma_must_be_positive():
    with pytest.raises(ValueError):
        bracket_probability(85, 89, mu=86.0, sigma=0.0)
