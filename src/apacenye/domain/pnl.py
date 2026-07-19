"""P&L math — Stage 1 §1.5 settlement rules.

Plain-language summary: a position's worst case is exactly what was paid for
it (price + entry fee) because contracts are fully collateralized — there is
no leverage and no way to lose more. Settlement pays $1 per winning contract
and $0 per losing one, with no settlement fee. Exiting early pays the trading
fee a second time on the exit leg.

`price_dollars` is always the price of the side actually HELD (for a NO
position, the NO price = 1 − YES price). All functions are pure.

NOTE (binding, Stage 3 §6.1): any paper P&L computed from these functions is
an OPTIMISTIC BOUND — the fill simulator ignores queue competition, market
impact, and partial-fill adverse selection.
"""

from apacenye.domain.fees import order_fee_dollars


def entry_cost_dollars(count: int, price_dollars: float) -> float:
    """Total cash out the door to open: price × count + entry fee.

    This is also the position's maximum possible loss.
    """
    return round(count * price_dollars + order_fee_dollars(count, price_dollars), 10)


def settlement_pnl_dollars(count: int, price_dollars: float, won: bool) -> float:
    """Realized P&L when held to settlement (no settlement fee).

    Won:  receive $1 × count, minus what we paid (incl. entry fee).
    Lost: receive nothing — lose the entire entry cost.
    """
    payout = float(count) if won else 0.0
    return round(payout - entry_cost_dollars(count, price_dollars), 10)


def early_exit_pnl_dollars(
    count: int, entry_price_dollars: float, exit_price_dollars: float
) -> float:
    """Realized P&L when sold before settlement: price move × count minus the
    fee on BOTH legs. A flat exit therefore always loses money — the round
    trip is never free."""
    gross = (exit_price_dollars - entry_price_dollars) * count
    fees = order_fee_dollars(count, entry_price_dollars) + order_fee_dollars(
        count, exit_price_dollars
    )
    return round(gross - fees, 10)


def mark_to_market_dollars(count: int, mark_price_dollars: float) -> float:
    """INDICATIVE value of an open position at a mid-price mark.

    Marks are for risk triggers and dashboard display only (Stage 3 §6.1);
    qualification and fills always use executable prices, never mid.
    """
    return round(count * mark_price_dollars, 10)
