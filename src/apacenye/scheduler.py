"""TickScheduler — platform-owned evaluation ticks (Stage 3 §2).

Workers never own wall-clock scheduling; they receive Tick objects from this
scheduler. The design constraint that matters: `tick_due()` is a pure
function of a supplied `now`, so the backtest replay harness can drive the
SAME scheduler with a virtual clock and workers cannot tell replay from live.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Awaitable, Callable

from apacenye.contract import Tick, utcnow

log = logging.getLogger(__name__)

TickCallback = Callable[[Tick], Awaitable[None]]


class _Subscription:
    __slots__ = ("strategy_id", "cadence_s", "callback", "next_due")

    def __init__(self, strategy_id: str, cadence_s: float, callback: TickCallback):
        self.strategy_id = strategy_id
        self.cadence_s = cadence_s
        self.callback = callback
        self.next_due: datetime | None = None  # fire immediately on first check


class TickScheduler:
    def __init__(self) -> None:
        self._subs: dict[str, _Subscription] = {}
        self._running = False

    def register(self, strategy_id: str, cadence_s: float, callback: TickCallback) -> None:
        self._subs[strategy_id] = _Subscription(strategy_id, cadence_s, callback)

    def unregister(self, strategy_id: str) -> None:
        self._subs.pop(strategy_id, None)

    async def fire_due(self, now: datetime) -> list[Tick]:
        """Emit ticks for every subscription due at `now`. This is the single
        emission path for BOTH live serving and backtest replay."""
        fired: list[Tick] = []
        for sub in list(self._subs.values()):
            if sub.next_due is None or now >= sub.next_due:
                sub.next_due = now + timedelta(seconds=sub.cadence_s)
                tick = Tick(strategy_id=sub.strategy_id, now=now)
                fired.append(tick)
                try:
                    await sub.callback(tick)
                except Exception:
                    log.exception("tick callback failed for %s", sub.strategy_id)
        return fired

    async def run(self, poll_interval_s: float = 1.0) -> None:
        """Live mode: check the wall clock every `poll_interval_s`."""
        self._running = True
        while self._running:
            await self.fire_due(utcnow())
            await asyncio.sleep(poll_interval_s)

    def stop(self) -> None:
        self._running = False
