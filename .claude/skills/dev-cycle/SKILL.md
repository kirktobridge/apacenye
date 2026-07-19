---
name: dev-cycle
description: Implement, verify, commit to main, and journal one Apacenyë change — the standard ship loop for any backlog item or fix, including the extra steps for risk-relevant config changes. Use whenever implementing anything beyond a trivial typo fix.
---

# Dev Cycle: Implement → Verify → Commit → Journal

One change at a time, trunk-based (single maintainer, commit to `main`).
Specialized skills nest inside this one: a new strategy runs
`add-strategy-worker` as step 3, a new feed runs `onboard-data-source`, etc.
This skill owns the outer loop either way.

## Steps

1. **Scope.** Pick the `BACKLOG.md` item (or triage the request via
   `triage-idea` first). Confirm its `blocked-by` is actually clear. State
   what "done" means in one sentence before writing code.
2. **Tests first for money-touching logic** (CLAUDE.md standard): fee math,
   sizing, gates, ledger, fills, checkpoint — the test exists and fails
   before the implementation makes it pass. Non-money code: tests with, not
   necessarily before.
3. **Implement.** Contract changes (`src/apacenye/contract/`) are amendments
   — flag to the user, never silent (precedent: OD-15). New external data
   must flow through a capture channel or it's future backtest data thrown
   away.
4. **Verify — all three, not just the first:**
   - `uv run pytest` — full suite, green.
   - Exercise the changed surface for real: worker/gate changes → replay or a
     brief `serve` against live read-only data; service changes → hit the
     pages/endpoints; CLI changes → run the command.
   - Safety invariants if touched (they have tests, but confirm the behavior
     you saw): LIVE still refuses to boot; kill drill still halts; START
     still requires a valid ack.
5. **Risk-relevant change? Extra steps, same commit:**
   - Numbers in Always-Apply Rule 4 (caps, stops, λ, k, edge floor, bankroll)
     change **only with explicit owner ratification** — unattended sessions
     stop here and park the change in BACKLOG instead.
   - `config/risk.yaml` and CLAUDE.md rule 4 update **in the same commit**.
   - Note in the DEV_LOG entry that existing paper acks are now invalid
     (the config-hash gate enforces re-acknowledgment automatically).
6. **Sync the docs the change touched** (schemas: `docs/SCHEMAS.md`):
   CLAUDE.md layout/state if structure changed; the relevant SKILL.md if an
   interface/spelling a skill documents changed; the strategy one-pager if
   strategy behavior changed.
7. **Commit to `main`.** Before staging: `git status` shows no `.env`,
   `secrets/`, `data/`, `*.sqlite*`, `*.pem` (gitignore covers them — verify
   anyway). Message: one summary line, then what/why; end with the
   Co-Authored-By line if Claude-written.
8. **Journal.** Append a DEV_LOG.md entry per schema (substantive changes
   only — a batch of trivial fixes can share one entry). Delete the shipped
   item from BACKLOG.md in the same commit.

## Don'ts

- No drive-by refactors inside an unrelated change — backlog them.
- Never weaken a test to make it pass; a wrong test gets fixed with the
  reasoning in the commit message.
- Never commit with the suite red, even "temporarily".
