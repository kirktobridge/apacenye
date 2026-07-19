"""Market data service — read-only Kalshi polling into the snapshot cache.

Responsibilities (Stage 3 §2 topology):
- poll order books for subscribed tickers → SnapshotCache.update() (which
  notifies the paper simulator's resting-order re-check and the WS feed)
- write every book to the capture channel (replay data from day one)
- detect settlements and notify the orchestrator
- run the S1 bracket-coherence monitor per event

Live-ness is polling, not websockets, in v0: W1's tempo is minutes, and a
poll loop is the simplest thing the owner can review. The Kalshi WS API can
replace this later without touching workers (they only see snapshots).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from apacenye.backtest.capture import CaptureWriter
from apacenye.contract import Side
from apacenye.execution.kalshi import KalshiApiError, KalshiClient
from apacenye.marketdata.catalog import MarketCatalog
from apacenye.marketdata.monitors import BracketCoherenceMonitor
from apacenye.marketdata.snapshots import SnapshotCache

log = logging.getLogger(__name__)


class MarketDataService:
    def __init__(
        self,
        client: KalshiClient,
        cache: SnapshotCache,
        catalog: MarketCatalog,
        capture: CaptureWriter | None = None,
        on_settlement: Callable[[str, Side], Awaitable[None]] | None = None,
        on_alert: Callable[[dict], None] | None = None,
        poll_interval_s: float = 15.0,
    ):
        self.client = client
        self.cache = cache
        self.catalog = catalog
        self.capture = capture
        self.on_settlement = on_settlement
        self.poll_interval_s = poll_interval_s
        self.monitor = BracketCoherenceMonitor(
            catalog, cache.get, on_alert or (lambda a: None)
        )
        self._running = False
        self._settled: set[str] = set()

    async def load_event_markets(self, event_ticker: str) -> list[str]:
        """Populate the catalog from Kalshi's own metadata for one event."""
        data = await self.client.get_markets(event_ticker=event_ticker)
        tickers = []
        for m in data.get("markets", []):
            info = self.catalog.add_from_kalshi_market(m)
            tickers.append(info.ticker)
            if self.capture:
                self.capture.write("market", {
                    "ticker": info.ticker, "event_ticker": info.event_ticker,
                    "bracket_lo": info.bracket_lo, "bracket_hi": info.bracket_hi,
                    "title": info.title, "status": info.status,
                }, ticker=info.ticker)
        return tickers

    async def poll_once(self) -> None:
        for ticker in self.catalog.tickers():
            info = self.catalog.get(ticker)
            if info is None or ticker in self._settled:
                continue
            try:
                snap = await self.client.get_snapshot(ticker, info.event_ticker)
            except KalshiApiError as exc:
                log.warning("book poll failed for %s: %s", ticker, exc)
                continue
            self.cache.update(snap)
            if self.capture:
                self.capture.write("book", snap.model_dump(mode="json"), ticker=ticker)
        # settlement check + S1, per event
        for event in self.catalog.events():
            self.monitor.check_event(event)
        await self._check_settlements()

    async def _check_settlements(self) -> None:
        for ticker in self.catalog.tickers():
            if ticker in self._settled:
                continue
            info = self.catalog.get(ticker)
            if info is None or info.status == "settled":
                continue
            try:
                market = (await self.client.get_market(ticker)).get("market", {})
            except KalshiApiError:
                continue
            if market.get("status") in ("settled", "finalized"):
                result = market.get("result", "")
                if result not in ("yes", "no"):
                    continue
                side = Side.YES if result == "yes" else Side.NO
                self._settled.add(ticker)
                info.status = "settled"
                if self.capture:
                    self.capture.write("settlement", {"result": result}, ticker=ticker)
                if self.on_settlement:
                    await self.on_settlement(ticker, side)

    async def run(self) -> None:
        self._running = True
        while self._running:
            try:
                await self.poll_once()
            except Exception:
                log.exception("market data poll cycle failed; continuing")
            await asyncio.sleep(self.poll_interval_s)

    def stop(self) -> None:
        self._running = False
