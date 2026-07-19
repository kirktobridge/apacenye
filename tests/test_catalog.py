"""Catalog bracket-mapping tests — edges verified against live Kalshi
KXHIGHNY rules text on 2026-07-19 (see docstring in catalog.py). An
off-by-one here misprices every tail bracket, so it gets its own tests."""

from apacenye.domain.weather import bracket_probability
from apacenye.marketdata.catalog import MarketCatalog


def _mk(ticker, event, floor=None, cap=None, subtitle=""):
    return {"ticker": ticker, "event_ticker": event, "floor_strike": floor,
            "cap_strike": cap, "subtitle": subtitle, "title": "", "status": "open"}


def test_inclusive_range_bracket():
    cat = MarketCatalog()
    info = cat.add_from_kalshi_market(_mk("E-B86.5", "E", floor=86, cap=87))
    assert (info.bracket_lo, info.bracket_hi) == (86.0, 87.0)


def test_tail_above_is_strictly_greater():
    # T87: "greater than 87°" settles YES at 88 or above → inclusive lo = 88
    cat = MarketCatalog()
    info = cat.add_from_kalshi_market(_mk("E-T87", "E", floor=87))
    assert (info.bracket_lo, info.bracket_hi) == (88.0, None)


def test_tail_below_is_strictly_less():
    # T80: "less than 80°" settles YES at 79 or below → inclusive hi = 79
    cat = MarketCatalog()
    info = cat.add_from_kalshi_market(_mk("E-T80", "E", cap=80))
    assert (info.bracket_lo, info.bracket_hi) == (None, 79.0)


def test_full_event_probabilities_sum_to_one():
    # The verified KXHIGHNY-26JUL19 structure: <80, pairs 80–81 … 86–87, >87.
    cat = MarketCatalog()
    cat.add_from_kalshi_market(_mk("E-T80", "E", cap=80))
    for lo in range(80, 87, 2):
        cat.add_from_kalshi_market(_mk(f"E-B{lo}.5", "E", floor=lo, cap=lo + 1))
    cat.add_from_kalshi_market(_mk("E-T87", "E", floor=87))
    total = sum(
        bracket_probability(m.bracket_lo, m.bracket_hi, mu=84.0, sigma=3.0)
        for m in cat.brackets_of_event("E")
    )
    # a complete, non-overlapping partition of the integer outcomes sums to 1
    assert abs(total - 1.0) < 1e-9
