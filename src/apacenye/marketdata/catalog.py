"""Market catalog — orchestrator-owned ticker→event truth (Stage 3 §3.1 G7).

The mapping "which tickers are brackets of the same settlement event" is what
lets G7 treat all brackets of one event as ONE exposure (OD-7). It is built
from Kalshi's own market metadata (each market carries an `event_ticker`) —
NEVER from worker claims. The S1 bracket-coherence monitor discovers complete
bracket sets from this catalog too.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class MarketInfo:
    ticker: str
    event_ticker: str
    title: str = ""
    bracket_lo: float | None = None
    bracket_hi: float | None = None
    status: str = "open"


@dataclass
class MarketCatalog:
    _markets: dict[str, MarketInfo] = field(default_factory=dict)

    def add(self, info: MarketInfo) -> None:
        self._markets[info.ticker] = info

    def get(self, ticker: str) -> MarketInfo | None:
        return self._markets.get(ticker)

    def event_for(self, ticker: str) -> str | None:
        info = self._markets.get(ticker)
        return info.event_ticker if info else None

    def brackets_of_event(self, event_ticker: str) -> list[MarketInfo]:
        return [m for m in self._markets.values() if m.event_ticker == event_ticker]

    def events(self) -> set[str]:
        return {m.event_ticker for m in self._markets.values()}

    def tickers(self) -> list[str]:
        return list(self._markets)

    def add_from_kalshi_market(self, market: dict) -> MarketInfo:
        """Build an entry from one Kalshi GetMarkets market object.

        Bracket bounds come from `floor_strike`/`cap_strike` when present
        (temperature markets), falling back to parsing the subtitle. Bounds
        are informational for W1's bracket math; exposure aggregation needs
        only `event_ticker`, which Kalshi supplies directly.
        """
        lo = market.get("floor_strike")
        hi = market.get("cap_strike")
        if lo is None and hi is None:
            lo, hi = _parse_bracket(market.get("subtitle") or market.get("yes_sub_title") or "")
        info = MarketInfo(
            ticker=market["ticker"],
            event_ticker=market.get("event_ticker", ""),
            title=market.get("title", ""),
            bracket_lo=lo,
            bracket_hi=hi,
            status=market.get("status", "open"),
        )
        self.add(info)
        return info


_RANGE = re.compile(r"(-?\d+(?:\.\d+)?)°?\s*(?:to|-|–)\s*(-?\d+(?:\.\d+)?)")
_ABOVE = re.compile(r"(-?\d+(?:\.\d+)?)°?\s*(?:or above|or higher|\+)")
_BELOW = re.compile(r"(-?\d+(?:\.\d+)?)°?\s*(?:or below|or lower)")


def _parse_bracket(text: str) -> tuple[float | None, float | None]:
    if m := _RANGE.search(text):
        return float(m.group(1)), float(m.group(2))
    if m := _ABOVE.search(text):
        return float(m.group(1)), None
    if m := _BELOW.search(text):
        return None, float(m.group(1))
    return None, None
