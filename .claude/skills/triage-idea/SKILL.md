---
name: triage-idea
description: Triage a new idea (strategy, feature, data source, ops task) into Apacenyë's BACKLOG.md — or reject it — using the project's exploitability test, OD dependencies, and paper-only constraints. Use whenever the user floats an idea, asks "should we build X", or wants the backlog groomed.
---

# Triage an Idea into the Backlog

The backlog is `BACKLOG.md` (schema: `docs/SCHEMAS.md`). Triage is cheap on
purpose: every idea gets a fast, honest verdict — **do-now, backlog, blocked,
hardening-only, or reject** — with the reason written down. Ideas never go
straight to code without passing through here (except trivial S-size fixes,
which may go directly to `dev-cycle`).

## Steps

1. **Classify**: `strategy | data | platform | ops | research`, and size
   `S | M | L`.
2. **Strategy ideas get the Stage 1 §2 exploitability test** — all four or
   reject: (a) a persistent gap exists; (b) public data lets US be on the
   right side of it; (c) the gap exceeds fees + spread + slippage; (d) there's
   a structural reason it persists. Then the recurrence check: does it resolve
   often enough to generate calibration feedback? One-shot markets fail
   triage regardless of edge story. A surviving strategy idea also needs a
   one-pager (`docs/strategies/<id>.md`, schema in SCHEMAS.md) before
   implementation — note that in the entry.
3. **Check OD dependencies.** If the idea depends on an unverified [verify]
   item or an unratified decision, it goes to **Later / blocked** with the OD
   named — never build on an unverified figure (precedents: W2/OD-12,
   E1/OD-13).
4. **Check the paper-only wall.** Anything requiring live order submission,
   real capital, or weakening a hard-disable goes to **Hardening-session
   only** (recorded, unschedulable — Always-Apply Rule 1) or is rejected
   outright if it would shortcut the wall. Anything loosening a risk number
   needs owner ratification flagged in the entry.
5. **Write the entry** per the schema (next free `B-<n>` id) into the right
   section; keep **Now ≤ 3 items**. If Now is full, adding means demoting
   something — say which.
6. **Rejections are answered, not filed**: tell the user why (which test
   failed), and don't add clutter to the backlog.

## Conventions

- Duplicates: extend the existing entry's note instead of a new id.
- Re-triage on new evidence (an OD resolving may unblock items — check
  **Later / blocked** whenever one resolves).
- Grooming pass = re-check every blocked item's blocker, delete stale ideas,
  re-rank Next. Note grooming in DEV_LOG only if it materially reshuffled.
