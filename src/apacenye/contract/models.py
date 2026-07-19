"""THE interface module — Stage 2 §4 contract, mechanically bound per Stage 3 §0.

Both workers and the orchestrator import exactly this module and nothing else
of each other. Changing any field here is a CONTRACT AMENDMENT: it must be
flagged to the user explicitly (precedent: OD-15 `quote_seen`, adopted
provisionally by Stage 3 and included below).

Unit convention (CLAUDE.md): every money field name says its unit —
`*_dollars` or `*_cents` — never a bare `price`. All timestamps are UTC
ISO-8601 (`datetime` with tzinfo=UTC).
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


class Side(str, enum.Enum):
    YES = "yes"
    NO = "no"


class Action(str, enum.Enum):
    OPEN = "open"
    INCREASE = "increase"
    REDUCE = "reduce"
    CLOSE = "close"


class RunMode(str, enum.Enum):
    DRY_RUN = "DRY_RUN"
    PAPER = "PAPER"
    LIVE = "LIVE"  # refuses to boot in this bootstrap (Stage 3 §6)


class LifecycleState(str, enum.Enum):
    INIT = "INIT"
    START = "START"
    PAUSE = "PAUSE"
    STOP = "STOP"


class DispositionStatus(str, enum.Enum):
    APPROVED = "APPROVED"
    RESIZED = "RESIZED"
    REJECTED = "REJECTED"


class MarketSnapshot(BaseModel):
    """Latest top-of-book view of one market, orchestrator-owned.

    Workers PULL this from the snapshot cache on each tick; its `ts` is how
    staleness stays visible. Depths are contract counts at best bid/ask.
    """

    ticker: str
    event_ticker: str = ""
    yes_bid_dollars: float | None = None
    yes_ask_dollars: float | None = None
    yes_bid_depth: int = 0
    yes_ask_depth: int = 0
    last_trade_dollars: float | None = None
    ts: datetime = Field(default_factory=utcnow)

    @property
    def mid_dollars(self) -> float | None:
        """Indicative midpoint — used for marks and p_market, NEVER for fills."""
        if self.yes_bid_dollars is None or self.yes_ask_dollars is None:
            return None
        return (self.yes_bid_dollars + self.yes_ask_dollars) / 2.0

    def executable_buy_price_dollars(self, side: Side) -> float | None:
        """Price to buy `side` right now: YES pays the ask; NO pays 1 − bid
        (the NO ask is the mirror of the YES bid, Stage 1 §1.3)."""
        if side is Side.YES:
            return self.yes_ask_dollars
        return None if self.yes_bid_dollars is None else round(1.0 - self.yes_bid_dollars, 4)

    def executable_buy_depth(self, side: Side) -> int:
        return self.yes_ask_depth if side is Side.YES else self.yes_bid_depth


class QuoteSeen(BaseModel):
    """OD-15 (additive amendment, provisionally adopted by Stage 3 §8):
    the exact quote the worker saw when deciding, for explanation fidelity."""

    bid_dollars: float | None
    ask_dollars: float | None
    bid_depth: int
    ask_depth: int
    ts: datetime


class SizingTrace(BaseModel):
    """Full sizing trace (Stage 2 §4.4a `sizing` dict, given typed form)."""

    p_used: float
    kelly_f: float
    k: float
    lam: float = Field(alias="lambda", serialization_alias="lambda")
    bankroll_seen_dollars: float
    caps_applied: list[str] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class OrderIntent(BaseModel):
    """A worker's PROPOSAL to trade. Never an order.

    The orchestrator may approve, resize DOWN, defer, or reject it; workers
    must remain correct if every intent is rejected or expires unfilled.
    """

    intent_id: str = Field(default_factory=new_id)
    strategy_id: str
    ts: datetime = Field(default_factory=utcnow)
    market_ticker: str
    side: Side
    action: Action
    limit_price_dollars: float  # worst acceptable executable price; never market orders
    size_contracts: int  # a request, not a command
    ttl_seconds: int  # intent expires unexecuted after this; no immortal intents
    # -- the five mandatory explanation fields (Stage 2 §4.4a) --
    model_probability: float
    market_implied_probability: float  # the mid the worker saw when deciding
    net_edge: float  # after fee + slippage allowance, at the executable price
    confidence: float = Field(ge=0.0, le=1.0)
    key_inputs: dict  # decisive inputs WITH their source timestamps
    # -- traces --
    sizing: SizingTrace
    rationale: str  # one plain-English sentence a human can read in a log
    quote_seen: QuoteSeen  # OD-15

    @field_validator("limit_price_dollars")
    @classmethod
    def _price_in_range(cls, v: float) -> float:
        if not 0.01 <= v <= 0.99:
            raise ValueError(f"limit price must be $0.01–$0.99, got {v}")
        return v

    @field_validator("size_contracts", "ttl_seconds")
    @classmethod
    def _positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("must be positive")
        return v


class CancelIntent(BaseModel):
    """Withdraw a previously emitted intent (edge flipped or inputs stale)."""

    cancel_id: str = Field(default_factory=new_id)
    intent_id: str
    strategy_id: str
    ts: datetime = Field(default_factory=utcnow)
    reason: str


class Heartbeat(BaseModel):
    """Periodic worker health. Silence beyond the timeout ⇒ orchestrator
    pauses the strategy (Stage 3 §2 supervision rules)."""

    strategy_id: str
    ts: datetime = Field(default_factory=utcnow)
    state: LifecycleState
    last_evaluation_ts: datetime | None = None
    data_ages_seconds: dict[str, float] = Field(default_factory=dict)
    error_flags: list[str] = Field(default_factory=list)


class Evaluation(BaseModel):
    """Shadow forecast: logged on EVERY evaluation, traded or not.

    This is the calibration dataset (Brier score, reliability curves) that
    later justifies changing λ or k — never drop it (Stage 2 §4.4d).
    """

    evaluation_id: str = Field(default_factory=new_id)
    strategy_id: str
    ts: datetime = Field(default_factory=utcnow)
    market_ticker: str
    event_ticker: str = ""
    model_probability: float
    market_implied_probability: float | None  # mid; None if no two-sided book
    executable_price_dollars: float | None
    net_edge: float | None
    qualified: bool
    intent_id: str | None = None  # set when this evaluation emitted an intent
    note: str = ""


class Disposition(BaseModel):
    """The orchestrator's ruling on one intent, with the gates that bound."""

    intent_id: str
    strategy_id: str
    status: DispositionStatus
    requested_size: int
    final_size: int
    binding_gates: list[str] = Field(default_factory=list)
    reason: str = ""
    ts: datetime = Field(default_factory=utcnow)


class Fill(BaseModel):
    """One execution against an approved order (paper simulator in this
    bootstrap). `order_id == intent_id` — idempotency key end-to-end."""

    fill_id: str = Field(default_factory=new_id)
    order_id: str
    intent_id: str
    strategy_id: str
    market_ticker: str
    side: Side
    action: Action
    price_dollars: float
    count: int
    fee_dollars: float
    ts: datetime = Field(default_factory=utcnow)


class Tick(BaseModel):
    """A platform-issued evaluation tick. Workers never own wall-clock
    scheduling; in replay the harness emits identical Ticks with a virtual
    `now` — workers cannot tell the difference, which is the point."""

    strategy_id: str
    now: datetime
    kind: str = "eval"


class ExplanationRecord(BaseModel):
    """One per intent, assembled by the ORCHESTRATOR (Stage 3 §8): the
    worker's fields verbatim plus what only the orchestrator knows."""

    intent_id: str
    strategy_id: str
    market_ticker: str
    side: Side
    action: Action
    model_probability: float
    market_implied_probability: float
    net_edge: float
    confidence: float
    key_inputs: dict
    sizing: SizingTrace
    rationale: str
    quote_seen: QuoteSeen
    disposition: Disposition | None = None
    risk_context: dict = Field(default_factory=dict)  # headrooms at decision time
    execution: dict | None = None  # {fills, avg_price_dollars, fees_paid_dollars} | dry_run
    outcome: dict | None = None  # appended at settlement: {settled_side, realized_pnl_dollars, model_vs_outcome}
