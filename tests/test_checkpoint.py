"""Concept-checkpoint tests: hash chain integrity, gate flow, the live wall."""

import json

import pytest

from apacenye.config import RiskConfig
from apacenye.checkpoint.ack import (
    AckLog,
    GENESIS_HASH,
    risk_relevant_config_hash,
    run_gate,
)


class ScriptedIO:
    """Feeds canned answers to run_gate; auto-types required acks correctly."""

    def __init__(self, answers: list[str]):
        self.answers = list(answers)
        self.printed: list[str] = []

    def input_fn(self, prompt: str) -> str:
        if prompt.startswith("Type exactly:"):
            # echo back the required sentence between the quotes
            return prompt.split('"')[1]
        if prompt.startswith(">") or "\n>" in prompt or prompt == "> ":
            if prompt == "> ":  # live final request line
                return self.final_line
        return self.answers.pop(0) if self.answers else ""

    def print_fn(self, s: str) -> None:
        self.printed.append(s)
        for line in s.splitlines():
            if line.strip().startswith("ENABLE LIVE"):
                self.final_line = line.strip()


def test_hash_chain_appends_and_verifies(tmp_path):
    log = AckLog(tmp_path / "acks.jsonl")
    r1 = log.append({"gate": "paper", "strategy_id": "W1", "result": "PASSED"})
    r2 = log.append({"gate": "paper", "strategy_id": "W1", "result": "PASSED"})
    assert r1["seq"] == 1 and r1["prev_sha256"] == GENESIS_HASH
    assert r2["seq"] == 2 and r2["prev_sha256"] != GENESIS_HASH
    ok, msg = log.verify()
    assert ok, msg


def test_tampering_is_detected(tmp_path):
    path = tmp_path / "acks.jsonl"
    log = AckLog(path)
    log.append({"gate": "paper", "strategy_id": "W1", "result": "ABORTED"})
    log.append({"gate": "paper", "strategy_id": "W1", "result": "PASSED"})
    # retroactively edit line 1 (turn the ABORTED into PASSED)
    lines = path.read_text().splitlines()
    rec = json.loads(lines[0])
    rec["result"] = "PASSED"
    lines[0] = json.dumps(rec, sort_keys=True)
    path.write_text("\n".join(lines) + "\n")
    ok, msg = log.verify()
    assert not ok
    assert "chain broken" in msg


def test_config_hash_changes_with_risk_relevant_params_only():
    base = RiskConfig()
    assert risk_relevant_config_hash(base) == risk_relevant_config_hash(RiskConfig())
    # risk-relevant change → new hash (invalidates acks)
    assert risk_relevant_config_hash(RiskConfig(bankroll_usd=2000)) != risk_relevant_config_hash(base)
    assert risk_relevant_config_hash(RiskConfig(kelly_multiplier=0.5)) != risk_relevant_config_hash(base)
    # non-risk-relevant change (heartbeat timeout) → same hash
    assert risk_relevant_config_hash(RiskConfig(heartbeat_timeout_s=999)) == risk_relevant_config_hash(base)


def test_paper_gate_passes_with_correct_answers(tmp_path):
    io = ScriptedIO(answers=["b", "0.55", "1.75", "yes"])  # K1, K2, K4, K5
    log = AckLog(tmp_path / "acks.jsonl")
    risk = RiskConfig()
    rec = run_gate("W1", "paper", risk, log, io.input_fn, io.print_fn)
    assert rec["result"] == "PASSED"
    assert [c["id"] for c in rec["concepts"]] == ["K1", "K2", "K4", "K5"]
    assert log.has_valid_paper_ack("W1", risk_relevant_config_hash(risk))


def test_ack_invalid_after_risk_config_change(tmp_path):
    io = ScriptedIO(answers=["b", "0.55", "1.75", "yes"])
    log = AckLog(tmp_path / "acks.jsonl")
    run_gate("W1", "paper", RiskConfig(), log, io.input_fn, io.print_fn)
    changed = RiskConfig(max_event_exposure_pct=10)
    assert not log.has_valid_paper_ack("W1", risk_relevant_config_hash(changed))


def test_three_failures_abort_and_are_logged(tmp_path):
    io = ScriptedIO(answers=["a", "a", "a"])  # K1 wrong three times
    log = AckLog(tmp_path / "acks.jsonl")
    risk = RiskConfig()
    rec = run_gate("W1", "paper", risk, log, io.input_fn, io.print_fn)
    assert rec["result"] == "ABORTED"
    assert "K1" in rec["outcome_note"]
    assert not log.has_valid_paper_ack("W1", risk_relevant_config_hash(risk))
    ok, _ = log.verify()  # aborted attempts are chained too
    assert ok


def test_numeric_answers_accept_one_percent_tolerance(tmp_path):
    io = ScriptedIO(answers=["b", "0.5501", "$1.76", "yes"])
    log = AckLog(tmp_path / "acks.jsonl")
    rec = run_gate("W1", "paper", RiskConfig(), log, io.input_fn, io.print_fn)
    assert rec["result"] == "PASSED"


def test_live_gate_terminates_at_the_wall(tmp_path):
    # All five concepts correct + the final typed request line → the flow
    # still REFUSES and records the refusal (the bootstrap wall).
    io = ScriptedIO(answers=["b", "0.55", "200", "1.75", "yes"])
    log = AckLog(tmp_path / "acks.jsonl")
    risk = RiskConfig()
    rec = run_gate("W1", "live", risk, log, io.input_fn, io.print_fn)
    assert rec["result"] == "PASSED"  # the CHECKPOINT passed…
    assert rec["outcome_note"] == "live refused: bootstrap hard-disable"  # …and live did not
    assert [c["id"] for c in rec["concepts"]] == ["K1", "K2", "K3", "K4", "K5"]
    # a live ack never doubles as a paper ack
    assert not log.has_valid_paper_ack("W1", risk_relevant_config_hash(risk))
