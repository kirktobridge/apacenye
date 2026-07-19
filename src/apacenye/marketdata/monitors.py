"""S1 bracket-coherence monitor — Stage 2 §3.6, shipped as a MONITOR only.

Plain-language summary: the YES prices of a complete, mutually exclusive
bracket set should sum to about $1 (exactly one bracket settles at $1). If
the asks sum to well under $1, buying every bracket would lock a riskless
profit; if the bids sum to well over $1, selling every bracket would. Both
are rare — and in practice a "violation" is more often a BAD DATA FEED than
free money, which is exactly why this runs as a data-sanity alarm and emits
NO intents in this bootstrap.
"""

from __future__ import annotations

import logging
from typing import Callable

from apacenye.contract import MarketSnapshot, utcnow
from apacenye.domain.fees import per_contract_fee_dollars
from apacenye.marketdata.catalog import MarketCatalog

log = logging.getLogger(__name__)


class BracketCoherenceMonitor:
    def __init__(self, catalog: MarketCatalog,
                 get_snapshot: Callable[[str], MarketSnapshot | None],
                 on_alert: Callable[[dict], None]):
        self.catalog = catalog
        self.get_snapshot = get_snapshot
        self.on_alert = on_alert

    def check_event(self, event_ticker: str) -> dict | None:
        """Alert if sum(asks) < $1 − fees or sum(bids) > $1 + fees across a
        COMPLETE set (every bracket must have a two-sided quote — a partial
        set proves nothing)."""
        brackets = self.catalog.brackets_of_event(event_ticker)
        if len(brackets) < 2:
            return None
        asks, bids, fees = [], [], 0.0
        for b in brackets:
            snap = self.get_snapshot(b.ticker)
            if snap is None or snap.yes_ask_dollars is None or snap.yes_bid_dollars is None:
                return None  # incomplete data — no conclusion
            asks.append(snap.yes_ask_dollars)
            bids.append(snap.yes_bid_dollars)
            fees += per_contract_fee_dollars(snap.yes_ask_dollars)
        alert = None
        if sum(asks) < 1.0 - fees:
            alert = {"kind": "bracket_coherence", "event_ticker": event_ticker,
                     "direction": "asks_undersell_dollar", "sum_asks": round(sum(asks), 4),
                     "fees": round(fees, 4), "ts": utcnow().isoformat(),
                     "note": "probable bad feed first, opportunity second"}
        elif sum(bids) > 1.0 + fees:
            alert = {"kind": "bracket_coherence", "event_ticker": event_ticker,
                     "direction": "bids_oversell_dollar", "sum_bids": round(sum(bids), 4),
                     "fees": round(fees, 4), "ts": utcnow().isoformat(),
                     "note": "probable bad feed first, opportunity second"}
        if alert:
            log.warning("S1 alert: %s", alert)
            self.on_alert(alert)
        return alert

    def check_all(self) -> list[dict]:
        return [a for e in self.catalog.events() if (a := self.check_event(e))]
