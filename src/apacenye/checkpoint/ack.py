"""Concept checkpoints — Stage 3 §11, implemented exactly.

Plain-language summary: before a strategy may START in paper, the owner must
pass an interactive CLI quiz (`apacenye ack`) proving they understand what
the system is about to do with (paper) money: implied vs. true probability,
that sizing runs on an estimate, this strategy's worst-case dollar loss,
round-trip costs, and that paper results are an optimistic bound. Every
attempt — pass, fail, abort — is appended to a hash-chained JSONL log that
makes retroactive edits detectable. The orchestrator refuses to START a
strategy without a PASSED paper ack for the CURRENT risk-relevant config.

The live gate exists and is fully exercisable, but in this bootstrap it
always terminates at the hard-disable wall (execution/live.py) and records
`outcome_note: "live refused: bootstrap hard-disable"`.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from apacenye.config import RiskConfig
from apacenye.domain.fees import order_fee_dollars
from apacenye.domain.sizing import shrink_probability

GENESIS_HASH = "0" * 64

# Stage 3 §11.2: changing any of these invalidates existing acks and forces
# re-acknowledgment. Other tunables (staleness windows, cadences) do not.
RISK_RELEVANT_FIELDS = (
    "bankroll_usd",
    "max_event_exposure_pct",
    "max_strategy_exposure_pct",
    "max_portfolio_exposure_pct",
    "max_order_contracts",
    "max_depth_fraction",
    "kelly_multiplier",
    "shrinkage_lambda",
    "min_net_edge",
)


def risk_relevant_config_hash(risk: RiskConfig) -> str:
    snapshot = {f: getattr(risk, f) for f in RISK_RELEVANT_FIELDS}
    digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
    return f"sha256:{digest}"


def risk_relevant_snapshot(risk: RiskConfig) -> dict:
    return {f: getattr(risk, f) for f in RISK_RELEVANT_FIELDS}


@dataclass
class Concept:
    id: str
    recap: str
    question: str
    check_answer: Callable[[str], bool]
    explanation: str
    required_ack: str  # typed sentence, matched case-insensitively, whitespace-normalized


def _norm(s: str) -> str:
    return " ".join(s.strip().lower().split())


def _num_ok(given: str, expected: float, tol_frac: float = 0.01) -> bool:
    """Numeric answers accept ±1% tolerance; $ and % signs are forgiven."""
    try:
        val = float(given.strip().lstrip("$").rstrip("%").replace(",", ""))
    except ValueError:
        return False
    return abs(val - expected) <= abs(expected) * tol_frac + 1e-9


def build_concepts(strategy_id: str, risk: RiskConfig) -> dict[str, Concept]:
    """The five concepts, with values COMPUTED from the current config —
    never hardcoded examples (Stage 3 §11.3)."""
    lam = risk.shrinkage_lambda
    p_used = shrink_probability(0.60, 0.50, lam)
    event_cap = risk.max_event_exposure_dollars
    strat_cap = risk.max_strategy_exposure_dollars
    strat_pct = risk.max_strategy_exposure_pct
    fee_100_50 = order_fee_dollars(100, 0.50)

    return {
        "K1": Concept(
            id="K1",
            recap=("A Kalshi price is the market's collective probability estimate: "
                   "a YES contract at 62¢ means the crowd thinks the event is ~62% "
                   "likely. That estimate can be wrong — which is the only reason "
                   "trading it can make money."),
            question=("A YES contract trades at 62¢. Is 62% (a) the true probability, "
                      "or (b) the market's aggregate estimate?  [a/b]"),
            check_answer=lambda s: _norm(s) == "b",
            explanation=("62¢ is what the market currently believes, not ground truth. "
                         "Our whole strategy is betting our estimate is better calibrated."),
            required_ack=("I understand a market price is the market's estimate of "
                          "probability, not the true probability."),
        ),
        "K2": Concept(
            id="K2",
            recap=(f"Sizing never trusts the model outright: with shrinkage λ={lam}, "
                   f"the probability used is λ·p_model + (1−λ)·p_market."),
            question=(f"With p_model=0.60, p_market=0.50, λ={lam} — what probability "
                      "does sizing actually use?"),
            check_answer=lambda s: _num_ok(s, p_used),
            explanation=f"λ·0.60 + (1−λ)·0.50 = {p_used:.4g}. The model is an estimate; "
                        "we hedge its miscalibration by shrinking toward the market.",
            required_ack=("I understand position sizing runs on an estimated probability, "
                          "and over-trusting that estimate can turn a real edge into a loss."),
        ),
        "K3": Concept(
            id="K3",
            recap=(f"Positions are fully collateralized: the worst case of any position "
                   f"is what was paid for it. Current config: per-event cap "
                   f"${event_cap:.2f}, per-strategy cap ${strat_cap:.2f}."),
            question=(f"Per-event cap ${event_cap:.2f}, per-strategy cap ${strat_cap:.2f} "
                      f"(from current config). If every {strategy_id} position went to $0, "
                      "what is the maximum dollar loss?"),
            check_answer=lambda s: _num_ok(s, strat_cap),
            explanation=(f"The per-strategy cap bounds the total: ${strat_cap:.2f} "
                         f"({strat_pct:g}% of bankroll)."),
            required_ack=(f"I understand {strategy_id} can lose up to ${strat_cap:.2f} "
                          f"({strat_pct:g}% of bankroll) and I accept that worst case."),
        ),
        "K4": Concept(
            id="K4",
            recap=("Every executed order pays a fee of 0.07 × C × P × (1−P), rounded up "
                   "per order — worst exactly at 50¢ — plus the spread when crossing it."),
            question=("Fee formula 0.07 × C × P × (1−P): what is the fee for 100 "
                      "contracts at 50¢? (dollars)"),
            check_answer=lambda s: _num_ok(s, fee_100_50),
            explanation=f"0.07 × 100 × 0.5 × 0.5 = ${fee_100_50:.2f}.",
            required_ack=("I understand every round trip costs fees plus spread, and the "
                          "model's edge must exceed those costs before a trade is worth taking."),
        ),
        "K5": Concept(
            id="K5",
            recap=("The paper simulator fills at executable quotes with no queue "
                   "competition, no market impact, and no adverse selection on partial "
                   "fills — every one of those omissions flatters results. Paper P&L is "
                   "an OPTIMISTIC BOUND. Confirm you understand.  [yes/no]"),
            question="Is paper P&L an optimistic bound rather than evidence of live profitability? [yes]",
            check_answer=lambda s: _norm(s) in ("yes", "y"),
            explanation="Paper fills flatter reality; treat every paper number as a ceiling.",
            required_ack=("I understand paper results are an optimistic bound and are not "
                          "evidence the strategy makes money live."),
        ),
    }


PAPER_GATE_CONCEPTS = ("K1", "K2", "K4", "K5")
LIVE_GATE_CONCEPTS = ("K1", "K2", "K3", "K4", "K5")
MAX_ATTEMPTS_PER_CONCEPT = 3


class AckLog:
    """Append-only, hash-chained JSONL log (Stage 3 §11.4). Never rewritten."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _read_lines(self) -> list[str]:
        if not self.path.exists():
            return []
        return [ln for ln in self.path.read_text().splitlines() if ln.strip()]

    def read_all(self) -> list[dict]:
        return [json.loads(ln) for ln in self._read_lines()]

    def append(self, record: dict) -> dict:
        """Chain and append: seq = n+1, prev_sha256 = sha256 of the previous
        LINE (or 64 zeros for the first). Opened O_APPEND so a concurrent
        writer cannot truncate."""
        lines = self._read_lines()
        record = dict(record)
        record["seq"] = len(lines) + 1
        record["prev_sha256"] = (
            hashlib.sha256(lines[-1].encode()).hexdigest() if lines else GENESIS_HASH
        )
        line = json.dumps(record, sort_keys=True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, (line + "\n").encode())
        finally:
            os.close(fd)
        return record

    def verify(self) -> tuple[bool, str]:
        """Walk the chain; any retroactive edit breaks a prev_sha256 link."""
        lines = self._read_lines()
        prev = GENESIS_HASH
        for i, line in enumerate(lines, start=1):
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                return False, f"line {i}: not valid JSON"
            if rec.get("seq") != i:
                return False, f"line {i}: seq is {rec.get('seq')}, expected {i}"
            if rec.get("prev_sha256") != prev:
                return False, f"line {i}: hash chain broken (log edited?)"
            prev = hashlib.sha256(line.encode()).hexdigest()
        return True, f"ok: {len(lines)} records, chain intact"

    def latest_result(self, strategy_id: str, gate: str, config_hash: str) -> str | None:
        """Most recent result for (strategy, gate, config-hash), or None."""
        result = None
        for rec in self.read_all():
            if (rec.get("strategy_id") == strategy_id and rec.get("gate") == gate
                    and rec.get("config_hash") == config_hash):
                result = rec.get("result")
        return result

    def has_valid_paper_ack(self, strategy_id: str, config_hash: str) -> bool:
        """The orchestrator's START gate: a PASSED paper ack for the CURRENT
        risk-relevant config hash. A config change invalidates by hash mismatch."""
        return self.latest_result(strategy_id, "paper", config_hash) == "PASSED"


def run_gate(
    strategy_id: str,
    gate: str,  # "paper" | "live"
    risk: RiskConfig,
    log: AckLog,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
) -> dict:
    """Run the interactive checkpoint; append the attempt (PASSED or ABORTED)
    to the log; return the appended record. 3 failures on any concept aborts."""
    assert gate in ("paper", "live")
    concepts = build_concepts(strategy_id, risk)
    ids = PAPER_GATE_CONCEPTS if gate == "paper" else LIVE_GATE_CONCEPTS
    config_hash = risk_relevant_config_hash(risk)
    taken: list[dict] = []
    result = "PASSED"
    outcome_note = None

    print_fn(f"\n=== Concept checkpoint — strategy {strategy_id}, gate {gate} ===")
    print_fn(f"Risk-relevant config hash: {config_hash}\n")

    for cid in ids:
        c = concepts[cid]
        print_fn(f"--- {c.id} ---\n{c.recap}\n")
        attempts = 0
        correct = False
        answer_given = ""
        while attempts < MAX_ATTEMPTS_PER_CONCEPT and not correct:
            answer_given = input_fn(f"{c.question}\n> ")
            attempts += 1
            correct = c.check_answer(answer_given)
            if not correct:
                print_fn(f"Not quite. {c.explanation}")
        if not correct:
            result = "ABORTED"
            outcome_note = f"aborted: 3 failures on {c.id}"
            taken.append({"id": c.id, "question": c.question, "answer_given": answer_given,
                          "correct": False, "attempts": attempts, "typed_ack": None})
            break
        # required typed sentence, matched case-insensitively/whitespace-normalized
        while True:
            typed = input_fn(f'Type exactly: "{c.required_ack}"\n> ')
            if _norm(typed) == _norm(c.required_ack):
                break
            print_fn("The typed acknowledgment must match exactly (case-insensitive).")
        taken.append({"id": c.id, "question": c.question, "answer_given": answer_given,
                      "correct": True, "attempts": attempts, "typed_ack": c.required_ack})

    if gate == "live" and result == "PASSED":
        final_line = f"ENABLE LIVE {strategy_id} CONFIG {config_hash}"
        print_fn(f"\nFinal step — type the request line exactly:\n  {final_line}")
        typed = input_fn("> ")
        if _norm(typed) != _norm(final_line):
            result = "ABORTED"
            outcome_note = "aborted: final request line mismatch"
        else:
            # The wall (Stage 3 §6): the checkpoint completes and is recorded,
            # and live enablement is then REFUSED. Always, in this bootstrap.
            outcome_note = "live refused: bootstrap hard-disable"
            print_fn("\n*** LIVE ENABLEMENT REFUSED ***")
            print_fn("Live trading is hard-disabled in this bootstrap. This checkpoint "
                     "is recorded, but pre-authorizes nothing; a future dedicated "
                     "hardening session with its own acceptance gate is required.")

    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "gate": gate,
        "strategy_id": strategy_id,
        "config_hash": config_hash,
        "config_snapshot": risk_relevant_snapshot(risk),
        "concepts": taken,
        "result": result,
        "outcome_note": outcome_note,
    }
    appended = log.append(record)
    print_fn(f"\nResult: {result}" + (f" — {outcome_note}" if outcome_note else ""))
    return appended
