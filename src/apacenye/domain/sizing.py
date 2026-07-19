"""Position sizing — Stage 2 §1.3–§1.4 (user-ratified λ=0.5, k=0.25; OD-9).

Plain-language summary: the model's probability is an ESTIMATE with its own
error bars, so we never bet the mathematically "optimal" (Kelly) amount:

1. Shrink the model's probability halfway toward the market's (λ = 0.5) —
   the market price is itself a competent estimator, and averaging hedges
   our model being miscalibrated.
2. Bet a quarter of the Kelly-optimal fraction (k = 0.25) — over-betting a
   real edge destroys wealth faster than under-betting forfeits it.
3. Clamp with hard caps that bind no matter what the model says. Kelly
   proposes; caps dispose. λ and k change only on shadow-forecast
   calibration evidence, never on vibes (OD-9).

All prices in DOLLARS. All functions are pure so they are trivially testable.
"""

DEFAULT_LAMBDA = 0.5  # shrinkage toward market (OD-9; do not loosen without ratification)
DEFAULT_KELLY_MULTIPLIER = 0.25  # quarter-Kelly (OD-9)


def kelly_fraction(p_win: float, cost_dollars: float) -> float:
    """Growth-optimal fraction of bankroll for a binary contract.

    A contract costing `cost_dollars` pays $1 if it wins: net odds
    b = (1 − cost) / cost, and Kelly f* = (b·p − (1−p)) / b.
    Returns 0 when the edge is zero or negative (never bet a negative edge).
    """
    if not 0.0 < cost_dollars < 1.0:
        raise ValueError(f"cost must be in (0, 1) dollars, got {cost_dollars}")
    if not 0.0 <= p_win <= 1.0:
        raise ValueError(f"p_win must be a probability, got {p_win}")
    b = (1.0 - cost_dollars) / cost_dollars
    return max(0.0, (b * p_win - (1.0 - p_win)) / b)


def shrink_probability(p_model: float, p_market: float, lam: float = DEFAULT_LAMBDA) -> float:
    """Blend our estimate with the market's: λ·p_model + (1−λ)·p_market."""
    if not 0.0 <= lam <= 1.0:
        raise ValueError(f"lambda must be in [0, 1], got {lam}")
    return lam * p_model + (1.0 - lam) * p_market


def proposed_stake_dollars(
    p_model: float,
    p_market: float,
    cost_dollars: float,
    bankroll_dollars: float,
    lam: float = DEFAULT_LAMBDA,
    k: float = DEFAULT_KELLY_MULTIPLIER,
) -> float:
    """Dollar stake BEFORE caps: k × f*(shrunk p) × bankroll.

    This is only a proposal — `clamp_contracts_to_caps` (worker side) and the
    orchestrator's gate pipeline (the binding side) apply the hard limits.
    """
    p_used = shrink_probability(p_model, p_market, lam)
    return k * kelly_fraction(p_used, cost_dollars) * bankroll_dollars


def clamp_contracts_to_caps(
    proposed_contracts: int,
    price_dollars: float,
    event_headroom_dollars: float,
    strategy_headroom_dollars: float,
    portfolio_headroom_dollars: float,
    top_of_book_depth: int,
    max_depth_fraction: float,
    max_order_contracts: int,
) -> tuple[int, list[str]]:
    """Apply every hard cap; the final size is the MINIMUM across all of them
    (Stage 3 §3.1 composition rule — the portfolio cap therefore always
    dominates). Returns (final_contracts, names_of_caps_that_bound).

    The 100-contract `max_order_contracts` default is the unit-bug backstop
    (cents-vs-dollars or size-vs-price swaps) — never remove it.
    """
    if proposed_contracts < 0:
        raise ValueError("proposed_contracts must be >= 0")
    limits = {
        "max_event_exposure": int(event_headroom_dollars / price_dollars),
        "max_strategy_exposure": int(strategy_headroom_dollars / price_dollars),
        "max_portfolio_exposure": int(portfolio_headroom_dollars / price_dollars),
        "max_depth_fraction": int(top_of_book_depth * max_depth_fraction),
        "max_order_contracts": max_order_contracts,
    }
    final = max(0, min(proposed_contracts, *limits.values()))
    if final < proposed_contracts:
        # a cap "bound" if it equals the clamped size
        caps_applied = [name for name, lim in limits.items() if lim == final]
    else:
        caps_applied = []
    return final, caps_applied
