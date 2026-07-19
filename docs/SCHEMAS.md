# Doc schemas

Formats for the living documents that skills read and write. Any skill that
touches one of these files must conform to (and lightly validate) the schema
here — malformed entries get fixed in the same change that would extend them.
The Stage 1–5 handoffs under `docs/initial-bootstrap/` are frozen historical
records: no schema applies retroactively and they are never edited.

## BACKLOG.md

Sections in order: `## Now` (committed next work, keep ≤ 3 items), `## Next`,
`## Later / blocked`, `## Hardening-session only`. One entry per item:

```
- **<id> — <title>** (<type>; <size>) — <one-line value statement>.
  blocked-by: <OD-nn / "weeks of capture" / nothing>. [optional 1–2 note lines]
  [plan: docs/plans/<ID>-PLAN.md (...)  OR  plan-required (Fable) — ...]
```

- `id`: `B-<n>`, monotonically increasing, never reused.
- `type`: `strategy | data | platform | ops | research`
- `size`: `S | M | L` (≈ hours / a session / multiple sessions)
- Items depending on an unverified OD sit in **Later / blocked** with the OD
  named. Anything requiring live-order code sits in **Hardening-session only**
  — it cannot be scheduled in this bootstrap, only recorded.
- Done items are DELETED (the DEV_LOG entry is the record), not struck through.
- **Plan linkage (optional field, at most one form per entry).** An item that
  needs a written plan before implementation (triage's Bucket A: crosses 3+
  files/layers, new abstraction, non-obvious tradeoffs, silent-regression
  risk, or a design dependency) carries exactly one of:
  - `plan: docs/plans/<ID>-PLAN.md (<note>)` — the plan exists. The file MUST
    exist and its name MUST match the entry id (`B-7` → `B7-PLAN.md`).
  - `plan-required (Fable) — <what the plan must resolve first>` — flagged as
    needing a plan, not yet written.
  The two are mutually exclusive. Direct-implementation items (Bucket B) carry
  neither. `tests/test_backlog_schema.py` enforces the header format and this
  linkage rule — a `plan:` pointer to a missing or mis-named file fails CI.

## DEV_LOG.md

Append-only, chronological (newest last). One entry per substantive change —
"substantive" = new capability, behavior change, risk/config change, or
notable ops event. Not per-commit noise. Format:

```
## YYYY-MM-DD — <title> (<scope>)
<2–5 lines: what changed and why; the non-obvious decision if there was one.>
Tests: <n passed / notable additions>. Backlog: <ids closed/added, or —>.
ODs: <touched/resolved, or —>. Ratification: <what awaits the owner, or —>.
```

`scope`: `strategy | data | platform | ops | risk-config | docs`.
Entries are never edited after the fact; corrections get a new entry.

## docs/strategies/<id>.md (strategy one-pager)

Required sections, in order (template: `docs/strategies/w1.md`):
**Status/Mode line · Thesis** (why the market is wrong AND why that persists)
**· Data sources** (signal + settlement source, access terms) **· p_model**
(how it's computed) **· Entry/exit · Sizing · Worst case** (dollar numbers
from current caps) **· Open Decisions this strategy depends on · Upgrade
path.** A strategy may not be implemented before its one-pager exists
(add-strategy-worker step 1).
