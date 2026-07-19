"""The orchestrator — owns the queues, the risk engine, the ledger, and the
ONLY reference to an execution client (Stage 3 §2–§3).

Plain-language summary: workers put proposals and telemetry onto one queue;
this module consumes it. Every OrderIntent runs the G0–G10 gate pipeline and
its disposition is recorded, pushed to the dashboard, and — only if approved,
and only in PAPER mode — handed to the internal fill simulator. Workers are
constructed with a WorkerContext that contains no execution client, so there
is no code path from any worker to an order API: that is a structural
guarantee, not a convention.

Lifecycle supervision: heartbeat silence pauses a strategy; the kill sentinel
pauses everything and cancels resting paper orders; a strategy cannot START
without a PASSED paper acknowledgment for the current risk-relevant config.
"""

from __future__ import annotations

import asyncio
import logging

from apacenye.checkpoint.ack import AckLog, risk_relevant_config_hash
from apacenye.config import AppSettings, RiskConfig
from apacenye.contract import (
    CancelIntent,
    Disposition,
    DispositionStatus,
    Evaluation,
    ExplanationRecord,
    Fill,
    Heartbeat,
    LifecycleState,
    MarketSnapshot,
    OrderIntent,
    RunMode,
    Side,
    utcnow,
)
from apacenye.execution.paper import PaperExecutionClient
from apacenye.marketdata.catalog import MarketCatalog
from apacenye.marketdata.snapshots import SnapshotCache
from apacenye.orchestrator.kill import KillSwitch
from apacenye.orchestrator.ledger import Ledger
from apacenye.orchestrator.risk_engine import RiskEngine
from apacenye.scheduler import TickScheduler
from apacenye.workers.base import StrategyWorker, WorkerContext

log = logging.getLogger(__name__)


class Orchestrator:
    def __init__(
        self,
        settings: AppSettings,
        risk: RiskConfig,
        ledger: Ledger,
        kill: KillSwitch,
        cache: SnapshotCache,
        catalog: MarketCatalog,
        scheduler: TickScheduler,
        ws_hub=None,  # optional WsHub; orchestrator works headless (tests, replay)
        now_fn=utcnow,  # replay injects virtual time for the time-based gates
    ):
        self.settings = settings
        self.risk = risk
        self.ledger = ledger
        self.kill = kill
        self.cache = cache
        self.catalog = catalog
        self.scheduler = scheduler
        self.ws = ws_hub
        self.run_mode = settings.run_mode
        self.queue: asyncio.Queue = asyncio.Queue()
        self.workers: dict[str, StrategyWorker] = {}
        self.ack_log = AckLog(settings.ack_log_path)
        self._last_heartbeat: dict[str, object] = {}
        self._explanations: dict[str, ExplanationRecord] = {}
        self._kill_handled = False
        self._running = False

        # Execution factory (Stage 3 §6): PAPER → internal simulator;
        # DRY_RUN → no client (approved orders are logged, never executed);
        # LIVE is unreachable — AppSettings refused to boot, and even if this
        # object were constructed with LIVE somehow, G2 rejects every intent
        # and there is no live client to construct (live.py raises).
        self.paper: PaperExecutionClient | None = (
            PaperExecutionClient(cache.get, risk) if self.run_mode is RunMode.PAPER else None
        )

        self.risk_engine = RiskEngine(
            risk=risk,
            ledger=ledger,
            kill=kill,
            run_mode=self.run_mode,
            get_snapshot=cache.get,
            get_strategy_state=self._strategy_state,
            pause_strategy=self.pause_strategy,
            staleness_window_s=self._staleness_window_s,
            now_fn=now_fn,
        )
        # resting paper orders re-check on every snapshot update
        cache.add_listener(self._on_snapshot)

    # ------------------------------------------------------------- WS helper

    def _broadcast(self, channel: str, payload: dict) -> None:
        if self.ws is not None:
            self.ws.broadcast(channel, payload)

    # ----------------------------------------------------- worker management

    def make_context(self) -> WorkerContext:
        """The full set of promises workers get — and nothing more."""
        return WorkerContext(
            emit=self.queue.put,
            get_snapshot=self.cache.get,
            get_positions=self.ledger.open_positions,
            get_bankroll_dollars=self.ledger.equity_dollars,
            risk=self.risk,
            list_event_brackets=self.catalog.brackets_of_event,
        )

    def register_worker(self, worker: StrategyWorker, cadence_s: float) -> None:
        self.workers[worker.strategy_id] = worker
        self.scheduler.register(worker.strategy_id, cadence_s, worker.on_tick)

    def _strategy_state(self, strategy_id: str) -> LifecycleState:
        worker = self.workers.get(strategy_id)
        return worker.state if worker else LifecycleState.STOP

    def _staleness_window_s(self, strategy_id: str) -> float:
        worker = self.workers.get(strategy_id)
        if worker is None:
            return 0.0  # unknown strategy: everything is stale
        return float(worker.config.get("staleness_s", 12 * 3600))

    # ------------------------------------------------------------- lifecycle

    def start_strategy(self, strategy_id: str) -> tuple[bool, str]:
        """START is gated (Stage 3 §11.2): kill switch clear AND a PASSED
        paper acknowledgment for the CURRENT risk-relevant config hash."""
        worker = self.workers.get(strategy_id)
        if worker is None:
            return False, f"unknown strategy {strategy_id}"
        if self.kill.is_killed():
            return False, "kill switch is active; unkill first (CLI only)"
        config_hash = risk_relevant_config_hash(self.risk)
        if not self.ack_log.has_valid_paper_ack(strategy_id, config_hash):
            return False, (
                f"no PASSED paper acknowledgment for {strategy_id} at current "
                f"risk config ({config_hash[:18]}…). Run: apacenye ack "
                f"--strategy {strategy_id} --gate paper"
            )
        worker.start()
        self._last_heartbeat[strategy_id] = utcnow()
        self._broadcast("alerts", {"kind": "lifecycle", "strategy_id": strategy_id,
                                   "state": "START"})
        return True, "started"

    def pause_strategy(self, strategy_id: str, reason: str) -> None:
        worker = self.workers.get(strategy_id)
        if worker is not None and worker.state is LifecycleState.START:
            worker.pause()
            log.warning("paused %s: %s", strategy_id, reason)
            self._broadcast("alerts", {"kind": "lifecycle", "strategy_id": strategy_id,
                                       "state": "PAUSE", "reason": reason})

    async def stop_strategy(self, strategy_id: str) -> None:
        worker = self.workers.get(strategy_id)
        if worker is not None:
            await worker.stop()
            self.scheduler.unregister(strategy_id)

    # ------------------------------------------------------------ main loop

    async def run(self) -> None:
        """Consume worker messages until stopped. Runs alongside the
        scheduler, market data service, and supervisor tasks."""
        self._running = True
        while self._running:
            msg = await self.queue.get()
            try:
                await self.dispatch(msg)
            except Exception:
                log.exception("message dispatch failed: %r", msg)

    def stop(self) -> None:
        self._running = False

    async def dispatch(self, msg) -> None:
        if isinstance(msg, OrderIntent):
            self._handle_intent(msg)
        elif isinstance(msg, CancelIntent):
            self._handle_cancel(msg)
        elif isinstance(msg, Evaluation):
            self.ledger.record_evaluation(msg)
            self._broadcast("signals", {"kind": "evaluation",
                                        **msg.model_dump(mode="json")})
        elif isinstance(msg, Heartbeat):
            self.ledger.record_heartbeat(msg)
            self._last_heartbeat[msg.strategy_id] = msg.ts
            self._broadcast("heartbeats", msg.model_dump(mode="json"))
        else:
            log.error("unknown message type on worker queue: %r", type(msg))

    # ----------------------------------------------------------- order path

    def _handle_intent(self, intent: OrderIntent, human_initiated: bool = False) -> Disposition:
        """The full order path (Stage 3 §3): record → gates → disposition →
        (paper) execution → explanation. Synchronous on purpose — the ledger
        is SQLite and the simulator is in-process, and a single serialized
        path is the easiest to review."""
        self.ledger.record_intent(intent)
        disposition = self.risk_engine.evaluate(intent, human_initiated=human_initiated)
        self.ledger.record_disposition(disposition)
        self._broadcast("intents", {
            "intent": intent.model_dump(mode="json"),
            "disposition": disposition.model_dump(mode="json"),
        })

        explanation = ExplanationRecord(
            intent_id=intent.intent_id, strategy_id=intent.strategy_id,
            market_ticker=intent.market_ticker, side=intent.side, action=intent.action,
            model_probability=intent.model_probability,
            market_implied_probability=intent.market_implied_probability,
            net_edge=intent.net_edge, confidence=intent.confidence,
            key_inputs=intent.key_inputs, sizing=intent.sizing,
            rationale=intent.rationale, quote_seen=intent.quote_seen,
            disposition=disposition,
            risk_context=self.risk_engine.risk_summary(),
        )
        self._explanations[intent.intent_id] = explanation

        worker = self.workers.get(intent.strategy_id)
        if disposition.status is DispositionStatus.REJECTED:
            if worker is not None:
                worker._outstanding_intents.pop(intent.intent_id, None)
        else:
            self.ledger.record_order(intent, disposition.final_size)
            if self.run_mode is RunMode.DRY_RUN or self.paper is None:
                # DRY_RUN: full pipeline, then stop — log, no fills, no state.
                explanation.execution = {"dry_run": True}
                self.ledger.update_order_status(intent.intent_id, "dry_run")
                log.info("DRY_RUN: would submit %s ×%d %s %s @ %.2f",
                         intent.market_ticker, disposition.final_size,
                         intent.side.value, intent.action.value,
                         intent.limit_price_dollars)
            else:
                fills = self.paper.submit(intent, disposition.final_size)
                for fill in fills:
                    self._apply_fill(fill)

        self.ledger.record_explanation(explanation)
        self._broadcast("signals", {
            "kind": "explanation", "intent_id": intent.intent_id,
            "strategy_id": intent.strategy_id, "market_ticker": intent.market_ticker,
            "model_probability": intent.model_probability,
            "market_implied_probability": intent.market_implied_probability,
            "net_edge": intent.net_edge, "confidence": intent.confidence,
            "rationale": intent.rationale,
            "disposition": disposition.status.value,
            "final_size": disposition.final_size,
        })
        return disposition

    def submit_human_intent(self, intent: OrderIntent) -> Disposition:
        """Dashboard/CLI-originated reduce/close — the only path that may
        pass G1 during a kill. Still runs every other gate."""
        return self._handle_intent(intent, human_initiated=True)

    def _handle_cancel(self, cancel: CancelIntent) -> None:
        self.ledger.record_cancel(cancel)
        if self.paper is not None:
            self.paper.cancel(cancel.intent_id)
        self.risk_engine.release_reservation(cancel.intent_id)
        self.ledger.update_order_status(cancel.intent_id, "cancelled")
        self._broadcast("intents", {"cancel": cancel.model_dump(mode="json")})

    def _apply_fill(self, fill: Fill) -> None:
        self.ledger.record_fill(fill)
        # Release the reservation once the order is DONE (fully filled).
        # A partially filled order keeps its reservation: the filled part is
        # then counted twice (position + reservation) until terminal state —
        # double-counting in the conservative direction, never under.
        if self.paper is None or self.paper.order_remaining(fill.order_id) == 0:
            self.risk_engine.release_reservation(fill.order_id)
            self.ledger.update_order_status(fill.order_id, "filled")
        exp = self._explanations.get(fill.intent_id)
        if exp is not None:
            fills_list = (exp.execution or {}).get("fills", [])
            fills_list.append(fill.model_dump(mode="json"))
            paid = sum(f["price_dollars"] * f["count"] for f in fills_list)
            count = sum(f["count"] for f in fills_list)
            exp.execution = {
                "fills": fills_list,
                "avg_price_dollars": round(paid / count, 4) if count else None,
                "fees_paid_dollars": round(sum(f["fee_dollars"] for f in fills_list), 2),
            }
            self.ledger.record_explanation(exp)
        worker = self.workers.get(fill.strategy_id)
        if worker is not None:
            worker._outstanding_intents.pop(fill.intent_id, None)
        self._broadcast("fills", fill.model_dump(mode="json"))
        self._broadcast("positions", {"positions": self.ledger.open_positions()})

    def _on_snapshot(self, snap: MarketSnapshot) -> None:
        """SnapshotCache listener: re-check resting paper orders."""
        if self.paper is None:
            return
        for fill in self.paper.on_snapshot(snap):
            self._apply_fill(fill)

    # ----------------------------------------------------------- settlement

    async def on_settlement(self, ticker: str, side: Side) -> None:
        # settlement outcome → realize P&L, append to explanations later work;
        # cancel any resting paper orders on the settled market first
        if self.paper is not None:
            for order in self.paper.resting_orders():
                if order["ticker"] == ticker:
                    self.paper.cancel(order["intent_id"])
                    self.risk_engine.release_reservation(order["intent_id"])
        self.ledger.settle_market(ticker, side)
        self._broadcast("positions", {"positions": self.ledger.open_positions()})
        self._broadcast("alerts", {"kind": "settlement", "ticker": ticker,
                                   "result": side.value})

    # ----------------------------------------------------- supervisor tasks

    async def kill_watcher(self, poll_s: float = 2.0) -> None:
        """Poll the sentinel (Stage 3 §5): on kill — pause all workers and
        cancel resting orders. Un-kill does NOT auto-resume: recovering is a
        deliberate two-step, per strategy, via the ack-gated start."""
        while self._running:
            killed = self.kill.is_killed()
            if killed and not self._kill_handled:
                self._kill_handled = True
                log.error("KILL detected: %s", self.kill.read_state())
                for sid in self.workers:
                    self.pause_strategy(sid, "kill switch")
                if self.paper is not None:
                    for intent_id in self.paper.cancel_all():
                        self.risk_engine.release_reservation(intent_id)
                        self.ledger.update_order_status(intent_id, "cancelled")
                self._broadcast("alerts", {"kind": "kill",
                                           "state": self.kill.read_state()})
            elif not killed and self._kill_handled:
                self._kill_handled = False  # workers remain PAUSEd on purpose
                self._broadcast("alerts", {"kind": "unkill"})
            await asyncio.sleep(poll_s)

    async def heartbeat_supervisor(self, poll_s: float = 10.0) -> None:
        """Heartbeat silence beyond the timeout ⇒ pause that strategy."""
        while self._running:
            now = utcnow()
            for sid, worker in self.workers.items():
                if worker.state is not LifecycleState.START:
                    continue
                last = self._last_heartbeat.get(sid)
                age = (now - last).total_seconds() if last else None
                if age is not None and age > self.risk.heartbeat_timeout_s:
                    self.pause_strategy(sid, f"heartbeat silent for {age:.0f}s")
            await asyncio.sleep(poll_s)

    async def expiry_sweeper(self, poll_s: float = 5.0) -> None:
        """Expire resting paper orders past their TTL; free reservations."""
        while self._running:
            if self.paper is not None:
                for intent_id in self.paper.expire_stale():
                    self.risk_engine.release_reservation(intent_id)
                    self.ledger.update_order_status(intent_id, "expired")
                    self._broadcast("intents", {"expired": intent_id})
            await asyncio.sleep(poll_s)
