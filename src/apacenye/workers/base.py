"""Strategy worker lifecycle ABC — Stage 2 §4.2, mechanically bound.

Workers are self-contained asyncio tasks that PROPOSE trades and never place
orders: there is no execution client anywhere in a worker's reach — the only
money-adjacent output is an OrderIntent put onto the orchestrator's queue via
the WorkerContext. Workers evaluate ONLY on platform ticks (never sleep-loops,
never the wall clock) so replay backtesting stays honest, and hold no
authoritative position state — the orchestrator's ledger is truth.
"""

from __future__ import annotations

import abc
import logging

from apacenye.config import RiskConfig
from apacenye.contract import (
    CancelIntent,
    Evaluation,
    Heartbeat,
    LifecycleState,
    MarketSnapshot,
    OrderIntent,
    Tick,
    utcnow,
)

log = logging.getLogger(__name__)


class WorkerContext:
    """Everything the orchestrator promises a worker (Stage 2 §4.5).

    Constructed by the orchestrator; contains NO execution client, by design.
    """

    def __init__(
        self,
        emit,  # async callable(message) — intents/cancels/evaluations/heartbeats
        get_snapshot,  # callable(ticker) -> MarketSnapshot | None
        get_positions,  # callable(strategy_id) -> list[dict] (authoritative, ledger-backed)
        get_bankroll_dollars,  # callable() -> float
        risk: RiskConfig,
        list_event_brackets=None,  # callable(event_ticker) -> list[MarketInfo]
    ):
        self.emit = emit
        self.get_snapshot = get_snapshot
        self.get_positions = get_positions
        self.get_bankroll_dollars = get_bankroll_dollars
        self.risk = risk
        self.list_event_brackets = list_event_brackets or (lambda e: [])


class StrategyWorker(abc.ABC):
    """Subclasses implement `_initialize`, `_evaluate`, `_validate_config`."""

    def __init__(self, strategy_id: str, config: dict, ctx: WorkerContext):
        self.strategy_id = strategy_id
        self.config = dict(config)
        self.ctx = ctx
        self.state = LifecycleState.INIT
        self.last_evaluation_ts = None
        self.error_flags: list[str] = []
        self._outstanding_intents: dict[str, OrderIntent] = {}

    # ------------------------------------------------------------- lifecycle

    async def initialize(self) -> None:
        """INIT: load config, validate data access. May fail loudly; must not
        emit intents. Restart-safe: everything is rebuilt from the context."""
        self.state = LifecycleState.INIT
        await self._initialize()

    def start(self) -> None:
        self.state = LifecycleState.START

    def pause(self) -> None:
        """Stop emitting intents immediately; stay warm; keep consuming data."""
        self.state = LifecycleState.PAUSE

    async def stop(self) -> None:
        """Graceful shutdown: cancel anything outstanding, then stop."""
        for intent_id in list(self._outstanding_intents):
            await self.emit_cancel(intent_id, "worker stopping")
        self.state = LifecycleState.STOP

    async def update_config(self, new_config: dict) -> tuple[bool, str]:
        """Hot-apply tunables or reject with a reason (no restart needed)."""
        ok, reason = self._validate_config(new_config)
        if ok:
            self.config.update(new_config)
        return ok, reason

    # ------------------------------------------------------------------ ticks

    async def on_tick(self, tick: Tick) -> None:
        """Platform tick entry point. PAUSE consumes data but emits no
        intents; heartbeats flow in every state except STOP."""
        if self.state is LifecycleState.STOP:
            return
        if self.state is LifecycleState.START:
            try:
                await self._evaluate(tick)
                self.last_evaluation_ts = tick.now
                self.error_flags = []
            except Exception as exc:
                log.exception("[%s] evaluation failed", self.strategy_id)
                self.error_flags = [f"evaluate: {exc!r}"]
        await self.emit_heartbeat()

    # ------------------------------------------------------------- emissions

    async def emit_intent(self, intent: OrderIntent) -> None:
        if self.state is not LifecycleState.START:
            # PAUSE means what it says: proposals stop IMMEDIATELY.
            return
        self._outstanding_intents[intent.intent_id] = intent
        await self.ctx.emit(intent)

    async def emit_cancel(self, intent_id: str, reason: str) -> None:
        self._outstanding_intents.pop(intent_id, None)
        await self.ctx.emit(CancelIntent(
            intent_id=intent_id, strategy_id=self.strategy_id, reason=reason,
        ))

    async def emit_evaluation(self, ev: Evaluation) -> None:
        # Shadow forecasts flow in every state — they are observation, not risk.
        await self.ctx.emit(ev)

    async def emit_heartbeat(self) -> None:
        await self.ctx.emit(Heartbeat(
            strategy_id=self.strategy_id, state=self.state,
            last_evaluation_ts=self.last_evaluation_ts,
            data_ages_seconds=self.data_ages_seconds(),
            error_flags=self.error_flags,
        ))

    # ----------------------------------------------------------- subclass API

    @abc.abstractmethod
    async def _initialize(self) -> None: ...

    @abc.abstractmethod
    async def _evaluate(self, tick: Tick) -> None: ...

    @abc.abstractmethod
    def _validate_config(self, new_config: dict) -> tuple[bool, str]: ...

    def data_ages_seconds(self) -> dict[str, float]:
        """Override: per-input data ages for the heartbeat."""
        return {}
