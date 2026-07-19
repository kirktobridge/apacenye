"""Latest-quote cache — Stage 3 §2: workers PULL the latest snapshot on each
tick (no per-tick fan-out queues, no backlog); staleness is visible via each
snapshot's timestamp. Listeners (paper simulator resting-order re-check,
capture writer, WS feed) are notified on every update."""

from __future__ import annotations

import logging
from typing import Callable

from apacenye.contract import MarketSnapshot

log = logging.getLogger(__name__)


class SnapshotCache:
    def __init__(self) -> None:
        self._snaps: dict[str, MarketSnapshot] = {}
        self._listeners: list[Callable[[MarketSnapshot], None]] = []

    def add_listener(self, fn: Callable[[MarketSnapshot], None]) -> None:
        self._listeners.append(fn)

    def update(self, snap: MarketSnapshot) -> None:
        self._snaps[snap.ticker] = snap
        for fn in self._listeners:
            try:
                fn(snap)
            except Exception:
                log.exception("snapshot listener failed for %s", snap.ticker)

    def get(self, ticker: str) -> MarketSnapshot | None:
        return self._snaps.get(ticker)

    def all(self) -> dict[str, MarketSnapshot]:
        return dict(self._snaps)
