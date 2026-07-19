# Dev log

Append-only journal of substantive changes; schema in `docs/SCHEMAS.md`.
Entries before 2026-07-19 are backfilled one-liners from the bootstrap
commits; full detail lives in `docs/initial-bootstrap/handoffs/`.

## 2026-07-18 — Stages 1–4: design bootstrap (docs)
Foundations, strategy design (W1-v0 picked), architecture (propose-then-approve,
G0–G10, kill switch, checkpoints), conventions + skills. No code.
Tests: —. Backlog: —. ODs: OD-1…OD-20 opened; 4/6/7 resolved. Ratification: OD-15…18 pending.

## 2026-07-19 — Stage 5: full platform implementation (platform)
All-Python monolith built and smoke-tested end-to-end against live read-only
Kalshi + NWS: domain math (tests-first), ledger, risk engine with reservation
accounting, paper simulator, double live hard-disable, checkpoint gates,
orchestrator, dashboard, replay harness, CLI. Verified live: OD-19 auth/rate
limits, KXHIGHNY strict-tail bracket semantics, KNYC grid OKX 34,45.
Tests: 98 passed. Backlog: —. ODs: OD-19 resolved, OD-3 (NYC) partial.
Ratification: OD-8/9/10/15/16/17/18 + D5-1…D5-14 pending (see implementation log §2).

## 2026-07-19 — Lifecycle skill suite + tracking docs (docs)
Added the four lifecycle skills (triage-idea, dev-cycle, operate-paper,
review-calibration) so the suite covers idea→backlog→ship→operate→calibrate;
seeded BACKLOG.md (B-1…B-12) and this DEV_LOG; doc schemas centralized in
docs/SCHEMAS.md; CLAUDE.md gained a Process section. Risk-config changes fold
into dev-cycle as a branch rather than a ninth skill (anti-bloat call).
Tests: 98 passed (docs-only change). Backlog: B-1…B-12 added. ODs: —.
Ratification: — (backlog item B-2 tracks the pending pass).
