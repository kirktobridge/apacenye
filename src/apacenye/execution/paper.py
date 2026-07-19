"""Paper fill simulator — Stage 3 §6.1 semantics, implemented verbatim.

Plain-language summary: this is a pretend exchange that always treats us as
the impatient side (the "taker"). A buy fills only if the real market's
current selling price (the ask) is at or below our limit, and it fills AT
that ask — never at the friendlier midpoint. If the price isn't there yet,
the order rests and is re-checked on every snapshot until its TTL expires;
a resting order fills at OUR limit when the market crosses it (we never award
ourselves maker rebates or queue priority). Fill size is capped at a fraction
of the visible top-of-book depth.

*** PAPER P&L IS AN OPTIMISTIC BOUND *** (stated per the Stage 5 brief):
no queue competition, no market impact, no partial-fill adverse selection,
and the book we fill against may itself be stale. Paper results are NOT
evidence the strategy makes money live.

Idempotency: client_order_id == intent_id. Submitting the same intent twice
(retry after a timeout, rate-limit backoff, crash-replay) is the SAME order:
the duplicate returns no new fills and can never double the position.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Callable

from apacenye.config import RiskConfig
from apacenye.contract import Action, Fill, MarketSnapshot, OrderIntent, Side, utcnow
from apacenye.domain.fees import order_fee_dollars

log = logging.getLogger(__name__)

_BUY_ACTIONS = (Action.OPEN, Action.INCREASE)


class _Order:
    __slots__ = ("intent", "remaining", "expires_at", "fills")

    def __init__(self, intent: OrderIntent, size: int):
        self.intent = intent
        self.remaining = size
        self.expires_at = intent.ts + timedelta(seconds=intent.ttl_seconds)
        self.fills: list[Fill] = []


class PaperExecutionClient:
    def __init__(self, get_snapshot: Callable[[str], MarketSnapshot | None], risk: RiskConfig):
        self.get_snapshot = get_snapshot
        self.risk = risk
        self._orders: dict[str, _Order] = {}  # order_id → order (incl. done, for idempotency)
        self._resting: dict[str, _Order] = {}

    # ------------------------------------------------------------------ public

    def submit(self, intent: OrderIntent, final_size: int) -> list[Fill]:
        """Submit an approved order. Duplicate intent_id ⇒ same order, no new
        fills — the retry path can never double-submit."""
        if intent.intent_id in self._orders:
            log.info("duplicate submit for %s ignored (idempotent)", intent.intent_id)
            return []
        order = _Order(intent, final_size)
        self._orders[intent.intent_id] = order
        snap = self.get_snapshot(intent.market_ticker)
        fills = self._try_fill(order, snap, resting=False)
        if order.remaining > 0:
            self._resting[intent.intent_id] = order
        return fills

    def on_snapshot(self, snap: MarketSnapshot) -> list[Fill]:
        """Re-check resting orders against a fresh snapshot."""
        fills: list[Fill] = []
        for order_id in [oid for oid, o in self._resting.items()
                         if o.intent.market_ticker == snap.ticker]:
            order = self._resting[order_id]
            fills.extend(self._try_fill(order, snap, resting=True))
            if order.remaining == 0:
                del self._resting[order_id]
        return fills

    def cancel(self, intent_id: str) -> bool:
        return self._resting.pop(intent_id, None) is not None

    def cancel_all(self) -> list[str]:
        cancelled = list(self._resting)
        self._resting.clear()
        return cancelled

    def expire_stale(self, now: datetime | None = None) -> list[str]:
        now = now or utcnow()
        expired = [oid for oid, o in self._resting.items() if o.expires_at <= now]
        for oid in expired:
            del self._resting[oid]
        return expired

    def resting_count(self) -> int:
        return len(self._resting)

    def resting_orders(self) -> list[dict]:
        return [
            {"intent_id": o.intent.intent_id, "ticker": o.intent.market_ticker,
             "side": o.intent.side.value, "action": o.intent.action.value,
             "limit_price_dollars": o.intent.limit_price_dollars,
             "remaining": o.remaining, "expires_at": o.expires_at.isoformat()}
            for o in self._resting.values()
        ]

    # ----------------------------------------------------------------- filling

    def _try_fill(self, order: _Order, snap: MarketSnapshot | None, resting: bool) -> list[Fill]:
        """Fill logic, both first-touch and resting re-check.

        First touch: marketable ⇒ fill AT the opposing quote (the worse
        price). Resting re-check: the market crossed our limit ⇒ fill at OUR
        limit (never better — we model no price improvement).
        """
        if snap is None:
            return []
        intent = order.intent
        buying = intent.action in _BUY_ACTIONS

        if buying:
            opposing = snap.executable_buy_price_dollars(intent.side)
            depth = snap.executable_buy_depth(intent.side)
            marketable = opposing is not None and opposing <= intent.limit_price_dollars
        else:
            # selling YES hits the bid; selling NO hits the NO bid = 1 − ask
            if intent.side is Side.YES:
                opposing = snap.yes_bid_dollars
                depth = snap.yes_bid_depth
            else:
                opposing = None if snap.yes_ask_dollars is None else round(1.0 - snap.yes_ask_dollars, 4)
                depth = snap.yes_ask_depth
            marketable = opposing is not None and opposing >= intent.limit_price_dollars

        if not marketable or depth <= 0:
            return []

        price = intent.limit_price_dollars if resting else opposing
        max_now = max(1, int(depth * self.risk.max_depth_fraction))
        count = min(order.remaining, max_now)
        if count <= 0:
            return []
        fill = Fill(
            order_id=intent.intent_id, intent_id=intent.intent_id,
            strategy_id=intent.strategy_id, market_ticker=intent.market_ticker,
            side=intent.side, action=intent.action,
            price_dollars=price, count=count,
            fee_dollars=order_fee_dollars(count, price),
        )
        order.remaining -= count
        order.fills.append(fill)
        return [fill]
