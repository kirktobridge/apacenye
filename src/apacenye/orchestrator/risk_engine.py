"""Risk engine — the gate pipeline G0–G10 (Stage 3 §3), the binding line.

Plain-language summary: every worker proposal passes through these gates in
order. Any gate may reject; the sizing gates compute the intent's maximum
allowed size as the MINIMUM across every cap's headroom (composition rule),
which is why the portfolio cap always dominates — nothing can be approved
that doesn't fit inside every limit simultaneously.

Reservation accounting: an approved intent RESERVES its cost against the
event/strategy/portfolio headrooms immediately, so two concurrent intents
cannot each pass the same headroom and jointly breach a cap. The reservation
is released when the intent fills (the ledger position replaces it), expires,
is cancelled, or is rejected by the venue.

Workers structurally cannot reach the execution client; this engine is the
only path from an OrderIntent to a (paper) order.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable

from apacenye.config import RiskConfig
from apacenye.contract import (
    Action,
    Disposition,
    DispositionStatus,
    LifecycleState,
    MarketSnapshot,
    OrderIntent,
    RunMode,
    utcnow,
)
from apacenye.orchestrator.kill import KillSwitch
from apacenye.orchestrator.ledger import Ledger

log = logging.getLogger(__name__)

_RISK_ADDING_ACTIONS = (Action.OPEN, Action.INCREASE)


class _Reservation:
    __slots__ = ("intent_id", "strategy_id", "event_ticker", "dollars", "expires_at")

    def __init__(self, intent_id: str, strategy_id: str, event_ticker: str,
                 dollars: float, expires_at: datetime):
        self.intent_id = intent_id
        self.strategy_id = strategy_id
        self.event_ticker = event_ticker
        self.dollars = dollars
        self.expires_at = expires_at


class RiskEngine:
    def __init__(
        self,
        risk: RiskConfig,
        ledger: Ledger,
        kill: KillSwitch,
        run_mode: RunMode,
        get_snapshot: Callable[[str], MarketSnapshot | None],
        get_strategy_state: Callable[[str], LifecycleState],
        pause_strategy: Callable[[str, str], None],
        staleness_window_s: Callable[[str], float],
    ):
        self.risk = risk
        self.ledger = ledger
        self.kill = kill
        self.run_mode = run_mode
        self.get_snapshot = get_snapshot
        self.get_strategy_state = get_strategy_state
        self.pause_strategy = pause_strategy
        self.staleness_window_s = staleness_window_s
        self._reservations: dict[str, _Reservation] = {}

    # ------------------------------------------------------------ reservations

    def _sweep_expired_reservations(self, now: datetime) -> None:
        for iid in [i for i, r in self._reservations.items() if r.expires_at <= now]:
            del self._reservations[iid]

    def _reserved_dollars(self, *, event: str | None = None, strategy: str | None = None) -> float:
        total = 0.0
        for r in self._reservations.values():
            if event is not None and r.event_ticker != event:
                continue
            if strategy is not None and r.strategy_id != strategy:
                continue
            total += r.dollars
        return total

    def release_reservation(self, intent_id: str) -> None:
        """Call on fill, expiry, cancel, or venue rejection."""
        self._reservations.pop(intent_id, None)

    # ------------------------------------------------------------------ gates

    def evaluate(self, intent: OrderIntent, human_initiated: bool = False) -> Disposition:
        """Run the full pipeline; always returns a Disposition (never raises
        for a merely-bad intent — a bad intent is a REJECTED disposition)."""
        now = utcnow()
        self._sweep_expired_reservations(now)

        def reject(gate: str, reason: str) -> Disposition:
            return Disposition(
                intent_id=intent.intent_id, strategy_id=intent.strategy_id,
                status=DispositionStatus.REJECTED,
                requested_size=intent.size_contracts, final_size=0,
                binding_gates=[gate], reason=reason,
            )

        # G0 — schema/validity. Pydantic already validated field shapes; here
        # we check what only runtime knows: the TTL.
        if intent.ts + timedelta(seconds=intent.ttl_seconds) <= now:
            return reject("G0", "intent TTL expired before evaluation")

        # G1 — kill switch (os.stat on the sentinel file, every single time).
        if self.kill.is_killed():
            if not (human_initiated and intent.action in (Action.REDUCE, Action.CLOSE)):
                return reject("G1", "kill switch active; only human-initiated reduce/close allowed")

        # G2 — run mode. LIVE can never reach execution (defense in depth —
        # boot already refuses LIVE, and live.py contains no submission code).
        dry_run = self.run_mode is RunMode.DRY_RUN
        if self.run_mode is RunMode.LIVE:
            return reject("G2", "LIVE is hard-disabled in this bootstrap")

        # G3 — strategy lifecycle (humans flattening bypass, per G1's spirit).
        if not human_initiated:
            state = self.get_strategy_state(intent.strategy_id)
            if state is not LifecycleState.START:
                return reject("G3", f"strategy is {state.value}, not START")

        # G4 — staleness: every *_ts entry in key_inputs inside the window.
        window = self.staleness_window_s(intent.strategy_id)
        for key, value in intent.key_inputs.items():
            if not key.endswith("_ts"):
                continue
            try:
                ts = datetime.fromisoformat(str(value))
            except ValueError:
                return reject("G4", f"unparseable timestamp in key_inputs[{key}]")
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age = (now - ts).total_seconds()
            if age > window:
                return reject("G4", f"key_inputs[{key}] is {age:.0f}s old (window {window:.0f}s)")

        # G5 — liquidity, from the ORCHESTRATOR'S OWN snapshot (never the
        # worker's claim). No snapshot ⇒ we cannot verify ⇒ reject.
        snap = self.get_snapshot(intent.market_ticker)
        if snap is None:
            return reject("G5", "no snapshot for ticker; liquidity unverifiable")
        depth = snap.executable_buy_depth(intent.side) if intent.action in _RISK_ADDING_ACTIONS \
            else snap.yes_bid_depth + snap.yes_ask_depth  # exits: total visible size
        depth_cap = int(depth * self.risk.max_depth_fraction)

        # G6 — absolute per-order cap (the unit-bug backstop).
        order_cap = self.risk.max_order_contracts

        binding: list[str] = []
        caps: dict[str, int] = {"G5": depth_cap, "G6": order_cap}

        # G7/G8/G9 — dollar headrooms, only for risk-ADDING actions. Cancels
        # and reduce/close reduce risk and bypass these (Stage 3 §3.1).
        if intent.action in _RISK_ADDING_ACTIONS:
            event = self.ledger.event_for_ticker(intent.market_ticker) or ""
            price = intent.limit_price_dollars
            event_headroom = (
                self.risk.max_event_exposure_dollars
                - self.ledger.event_exposure_dollars(event)
                - self._reserved_dollars(event=event)
            )
            strategy_headroom = (
                self.risk.max_strategy_exposure_dollars
                - self.ledger.strategy_exposure_dollars(intent.strategy_id)
                - self._reserved_dollars(strategy=intent.strategy_id)
            )
            portfolio_headroom = (
                self.risk.max_portfolio_exposure_dollars
                - self.ledger.portfolio_exposure_dollars()
                - self._reserved_dollars()
            )
            caps["G7"] = max(0, int(event_headroom / price))
            caps["G8"] = max(0, int(strategy_headroom / price))
            caps["G9"] = max(0, int(portfolio_headroom / price))

        # G10 — daily loss stops, marked at latest INDICATIVE mids. The
        # portfolio check runs FIRST: a portfolio breach must trip the kill
        # switch even when the emitting strategy's own stop also breached.
        port_day = self.ledger.day_pnl_dollars(None, self._current_marks(None))
        port_stop = -self.risk.bankroll_usd * self.risk.portfolio_daily_loss_pct / 100.0
        if port_day <= port_stop:
            # OD-17: portfolio daily loss trips the kill switch automatically.
            self.kill.trip("risk_engine", f"portfolio day P&L {port_day:.2f} ≤ stop {port_stop:.2f}")
            self.ledger.record_kill_event("kill", "risk_engine",
                                          f"portfolio daily loss {port_day:.2f}")
            return reject("G10", f"portfolio day P&L {port_day:.2f} ≤ stop {port_stop:.2f}; kill tripped")
        strat_day = self.ledger.day_pnl_dollars(intent.strategy_id,
                                                self._current_marks(intent.strategy_id))
        strat_stop = -self.risk.bankroll_usd * self.risk.strategy_daily_loss_pct / 100.0
        if strat_day <= strat_stop:
            self.pause_strategy(
                intent.strategy_id,
                f"daily loss {strat_day:.2f} breached stop {strat_stop:.2f}",
            )
            return reject("G10", f"strategy day P&L {strat_day:.2f} ≤ stop {strat_stop:.2f}; auto-PAUSEd")

        # Composition rule: effective size = min over all applicable caps.
        final_size = min(intent.size_contracts, *caps.values())
        if final_size < 1:
            worst = min(caps, key=lambda g: caps[g])
            return reject(worst, f"headroom allows {max(0, final_size)} contracts")
        binding = [g for g, cap in caps.items() if cap == final_size] \
            if final_size < intent.size_contracts else []

        status = (DispositionStatus.APPROVED if final_size == intent.size_contracts
                  else DispositionStatus.RESIZED)
        reason = "dry_run: gates passed; execution will be logged, not simulated" if dry_run \
            else ("approved" if status is DispositionStatus.APPROVED
                  else f"resized {intent.size_contracts}→{final_size} by {','.join(binding)}")

        # Reserve the approved cost against all headrooms until fill/expiry.
        if intent.action in _RISK_ADDING_ACTIONS:
            event = self.ledger.event_for_ticker(intent.market_ticker) or ""
            self._reservations[intent.intent_id] = _Reservation(
                intent.intent_id, intent.strategy_id, event,
                final_size * intent.limit_price_dollars,
                intent.ts + timedelta(seconds=intent.ttl_seconds),
            )

        return Disposition(
            intent_id=intent.intent_id, strategy_id=intent.strategy_id, status=status,
            requested_size=intent.size_contracts, final_size=final_size,
            binding_gates=binding, reason=reason,
        )

    def _current_marks(self, strategy_id: str | None) -> dict[str, float]:
        """Mid marks for the strategy's (or everyone's) open positions.
        Marks are indicative, for risk triggers only — never for fills."""
        marks: dict[str, float] = {}
        for pos in self.ledger.open_positions(strategy_id):
            snap = self.get_snapshot(pos["market_ticker"])
            if snap is not None and snap.mid_dollars is not None:
                marks[pos["market_ticker"]] = snap.mid_dollars
        return marks

    def risk_summary(self) -> dict:
        """Headroom view for GET /api/risk and the dashboard."""
        self._sweep_expired_reservations(utcnow())
        port_used = self.ledger.portfolio_exposure_dollars() + self._reserved_dollars()
        return {
            "bankroll_usd": self.risk.bankroll_usd,
            "equity_dollars": self.ledger.equity_dollars(),
            "portfolio_exposure_dollars": port_used,
            "portfolio_cap_dollars": self.risk.max_portfolio_exposure_dollars,
            "reservations": len(self._reservations),
            "realized_pnl_today_dollars": self.ledger.realized_pnl_today_dollars(),
            "strategy_daily_stop_dollars": -self.risk.bankroll_usd * self.risk.strategy_daily_loss_pct / 100.0,
            "portfolio_daily_stop_dollars": -self.risk.bankroll_usd * self.risk.portfolio_daily_loss_pct / 100.0,
            "killed": self.kill.is_killed(),
        }
