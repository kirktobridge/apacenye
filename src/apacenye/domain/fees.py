"""Fee and edge math — Stage 1 §1.5, user-ratified qualification rule (Stage 2 §2).

Plain-language summary: Kalshi charges a fee on every executed order of
0.07 × contracts × price × (1 − price), in dollars, rounded UP to the next
cent per order. The fee is largest at 50¢ (maximum uncertainty) and shrinks
toward 1¢/99¢. Holding to settlement costs no additional fee.

Two forms exist on purpose:
- `order_fee_dollars` — the rounded, per-order fee actually charged. Used by
  the fill simulator and the ledger.
- `per_contract_fee_dollars` — the raw, unrounded per-contract fee. Used in
  the qualification rule so a trade's edge is judged on the marginal cost,
  exactly as Stage 2 §2 wrote it.

All prices are in DOLLARS (0.01–0.99), never cents — the unit is in the name.
"""

import math

# Kalshi general taker fee rate. OD-1: verified against docs.kalshi.com on
# 2026-07-18 for the general schedule; some series (index/sports) may differ —
# re-verify per series before trading a new category.
FEE_RATE = 0.07

# Stage 2 §2 (user-ratified): slippage allowance per contract per leg, on top
# of paying the full quoted spread.
SLIPPAGE_ALLOWANCE_DOLLARS = 0.01

# Stage 2 §2 (user-ratified OD-4): minimum net edge for a trade to qualify.
MIN_NET_EDGE = 0.04


def per_contract_fee_dollars(price_dollars: float, rate: float = FEE_RATE) -> float:
    """Raw (unrounded) fee for ONE contract executed at `price_dollars`."""
    if not 0.0 < price_dollars < 1.0:
        raise ValueError(f"price must be in (0, 1) dollars, got {price_dollars}")
    return rate * price_dollars * (1.0 - price_dollars)


def order_fee_dollars(contracts: int, price_dollars: float, rate: float = FEE_RATE) -> float:
    """Fee in dollars for one executed order, rounded UP to the next cent.

    This is the amount the ledger debits: e.g. 100 contracts at 57¢ →
    raw 1.7157 → charged $1.72.
    """
    if contracts <= 0:
        raise ValueError(f"contracts must be positive, got {contracts}")
    raw = contracts * per_contract_fee_dollars(price_dollars, rate)
    # round HALF-CENT-SAFE: ceil to whole cents, guarding float error
    return math.ceil(round(raw * 100, 6)) / 100.0


def net_edge(
    p_model: float,
    executable_price_dollars: float,
    slippage_dollars: float = SLIPPAGE_ALLOWANCE_DOLLARS,
    rate: float = FEE_RATE,
) -> float:
    """Edge left after all entry costs, per contract, in probability points.

    net_edge = p_model − executable_price − fee(executable_price) − slippage.
    `executable_price_dollars` is the price we would actually pay right now
    (the ask when buying) — never the midpoint. A trade qualifies iff this
    is ≥ MIN_NET_EDGE (0.04, user-ratified).
    """
    return (
        p_model
        - executable_price_dollars
        - per_contract_fee_dollars(executable_price_dollars, rate)
        - slippage_dollars
    )


def breakeven_probability(
    executable_price_dollars: float,
    slippage_dollars: float = SLIPPAGE_ALLOWANCE_DOLLARS,
    min_edge: float = 0.0,
    rate: float = FEE_RATE,
) -> float:
    """Minimum model probability at which a buy at this price clears costs
    plus `min_edge`. With min_edge=0.04 this is the qualification threshold."""
    return (
        executable_price_dollars
        + per_contract_fee_dollars(executable_price_dollars, rate)
        + slippage_dollars
        + min_edge
    )
