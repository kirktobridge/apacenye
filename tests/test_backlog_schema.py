"""Structural enforcement of the BACKLOG.md schema (docs/SCHEMAS.md).

The backlog is a living doc that skills read and write; this test is the
machine half of "skills validate what they touch". It parses every entry and
fails on malformed headers or, most importantly, a **dangling plan pointer** —
a `plan:` line whose target file was deleted, renamed, or mis-numbered. That
is the regression the plan-linkage convention exists to prevent: a backlog
that claims a plan exists when it does not.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKLOG = REPO_ROOT / "BACKLOG.md"

VALID_TYPES = {"strategy", "data", "platform", "ops", "research"}
VALID_SIZES = {"S", "M", "L"}

# Entry start: `- **B-4 — <title>...` (title/metadata may wrap to later lines).
HEADER_RE = re.compile(r"^- \*\*(?P<id>B-\d+) — ")
# `(platform; M)` — may sit on the header line or a continuation line (B-12).
TYPESIZE_RE = re.compile(r"\((?P<type>[a-z]+); (?P<size>[SML])\)")
# `  plan: docs/plans/B4-PLAN.md (written 2026-07-19).`
PLAN_RE = re.compile(r"^\s+plan:\s+(?P<path>docs/plans/\S+?\.md)\b")
# `  plan-required (Fable) — not yet written; ...`
PLAN_REQUIRED_RE = re.compile(r"^\s+plan-required\b")


def _parse_entries() -> list[dict]:
    """Split BACKLOG.md into entries: each header line plus its continuation
    lines (indented, up to the next header or blank line)."""
    lines = BACKLOG.read_text().splitlines()
    entries: list[dict] = []
    current: dict | None = None
    for line in lines:
        header = HEADER_RE.match(line)
        if header:
            current = {"id": header["id"], "header_line": line, "body": []}
            entries.append(current)
        elif current is not None and line.startswith("  "):
            current["body"].append(line)
        elif not line.strip():
            current = None  # blank line ends an entry's continuation block
    # type/size may live on the header or any continuation line (e.g. B-12).
    for e in entries:
        blob = "\n".join([e["header_line"], *e["body"]])
        m = TYPESIZE_RE.search(blob)
        e["type"] = m["type"] if m else None
        e["size"] = m["size"] if m else None
    return entries


ENTRIES = _parse_entries()


def test_backlog_parses_at_least_the_known_entries() -> None:
    # Guards the parser itself: if a formatting change silently breaks header
    # matching, this catches it instead of the file appearing empty-and-valid.
    assert len(ENTRIES) >= 10, f"parsed only {len(ENTRIES)} entries — parser or format broke"


@pytest.mark.parametrize("entry", ENTRIES, ids=lambda e: e["id"])
def test_entry_header_is_well_formed(entry: dict) -> None:
    assert entry["type"] in VALID_TYPES, f"{entry['id']}: bad type {entry['type']!r}"
    assert entry["size"] in VALID_SIZES, f"{entry['id']}: bad size {entry['size']!r}"


def test_ids_are_unique() -> None:
    ids = [e["id"] for e in ENTRIES]
    dupes = {i for i in ids if ids.count(i) > 1}
    assert not dupes, f"duplicate backlog ids: {sorted(dupes)}"


@pytest.mark.parametrize("entry", ENTRIES, ids=lambda e: e["id"])
def test_plan_linkage(entry: dict) -> None:
    """The core rule this test exists for. Per entry:
    - `plan:` and `plan-required` are mutually exclusive (at most one).
    - a `plan:` pointer must resolve to an existing file whose name matches
      the entry id (B-7 → B7-PLAN.md) — no dangling or mis-numbered pointers.
    """
    plan_lines = [ln for ln in entry["body"] if PLAN_RE.match(ln)]
    required_lines = [ln for ln in entry["body"] if PLAN_REQUIRED_RE.match(ln)]

    assert not (plan_lines and required_lines), (
        f"{entry['id']}: has both a plan: pointer and a plan-required marker "
        "— they are mutually exclusive"
    )
    assert len(plan_lines) <= 1, f"{entry['id']}: more than one plan: line"

    for ln in plan_lines:
        rel = PLAN_RE.match(ln)["path"]
        target = REPO_ROOT / rel
        assert target.exists(), f"{entry['id']}: plan points to missing file {rel}"
        expected = f"{entry['id'].replace('-', '')}-PLAN.md"  # B-7 -> B7-PLAN.md
        assert target.name == expected, (
            f"{entry['id']}: plan file is {target.name}, expected {expected} "
            "(plan filename must match the entry id)"
        )


def test_no_orphan_plan_files() -> None:
    """Every docs/plans/B*-PLAN.md must be referenced by its backlog entry —
    a plan whose backlog item shipped/was deleted is an orphan to clean up."""
    plans_dir = REPO_ROOT / "docs" / "plans"
    if not plans_dir.exists():
        return
    referenced = {
        PLAN_RE.match(ln)["path"].rsplit("/", 1)[-1]
        for e in ENTRIES
        for ln in e["body"]
        if PLAN_RE.match(ln)
    }
    for plan_file in plans_dir.glob("B*-PLAN.md"):
        assert plan_file.name in referenced, (
            f"orphan plan {plan_file.name}: no backlog entry references it "
            "(delete it, or its item was closed without cleanup)"
        )
