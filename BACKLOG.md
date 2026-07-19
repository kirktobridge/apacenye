# Backlog

Schema: `docs/SCHEMAS.md`. Triage in via the `triage-idea` skill; work items
out via the `dev-cycle` skill (which deletes them here and records them in
`DEV_LOG.md`).

## Now

_(nothing committed as next work)_

## Next

- **B-4 — Calibration report tooling** (platform; M) — reliability curve +
  Brier summary from the evaluations table (feeds review-calibration skill).
  blocked-by: enough shadow-forecast samples to be worth plotting (~weeks).
  plan: docs/plans/B4-PLAN.md (written 2026-07-19).
- **B-5 — Ledger/capture backup routine** (ops; S) — periodic copy of
  data/apacenye.sqlite + capture/ out of the working tree.
  blocked-by: nothing.

## Later / blocked

- **B-6 — OD-12 study: late-day quote persistence** (research; M) — do stale
  late-day quotes persist at tradeable size? W2's precondition, answerable
  from our own capture. blocked-by: weeks of capture (metar channel now
  recording since B-3 shipped 2026-07-19).
  plan: docs/plans/B6-PLAN.md (written 2026-07-19) — pre-registration commit
  is step 1, do it before analysis.
- **B-7 — W2 late-day determinism worker** (strategy; M) — blocked-by: B-6/OD-12.
  plan: docs/plans/B7-PLAN.md (written 2026-07-19) — CONDITIONAL, valid only
  if B-6's verdict is BUILD; do not pick up on SHELVE/EXTEND.
- **B-8 — Second city onboarding** (data; M) — blocked-by: OD-2 measurement
  from capture + OD-10 ratification of which city.
  plan-required (Fable) — not yet written; needs capture liquidity evidence
  + OD-10 city ratification before a plan is meaningful.
- **B-9 — W1-v1 ensemble p_model** (strategy; L) — same worker, better
  distribution. blocked-by: calibration evidence that the Gaussian is the
  binding limitation (σ is now evidence-based, OD-11 resolved 2026-07-19).
  plan-required (Fable) — not yet written; write once B-4 reports have real
  samples showing the Gaussian is the limitation.
- **B-10 — E1 FOMC cross-venue worker** (strategy; M) — blocked-by: OD-13
  (fed-funds-futures data path/licensing).
  plan-required (Fable) — not yet written; OD-13 desk research (free/licensed
  futures data path) is the gating unknown a plan must resolve first.
- **B-11 — Dashboard signals view: htmx WebSocket extension** (platform; S) —
  replace 3 s polling on the signals feed; still zero hand-written JS.
  blocked-by: nothing (low value until evaluations flow daily).
- **B-14 — Daily event auto-rollover in serve** (platform; M) — on settlement
  or local day boundary, resolve the next open event in each strategy's
  series, subscribe its brackets, and hand workers the new event_ticker
  without a restart; today's boot-time-only resolution makes multi-day runs
  track a settled market. blocked-by: nothing.
  Note: crosses cli/feed/worker (event_ticker hot-update path) — plan before
  implementing. Interim mitigation exists via B-15's nightly restart timer.
  plan-required (Fable) — not yet written.
- **B-15 — Deploy to always-on host: owner's Raspberry Pi 3B+** (ops; M) —
  run serve continuously under systemd so the capture and shadow-forecast
  clocks stop depending on the owner's desktop being awake.
  blocked-by: nothing (nightly restart timer stands in for B-14 until it ships).
  Note: verify first — 64-bit OS on the 3B+, uv-managed Python 3.12 on
  aarch64, 1 GB RAM headroom under numpy/scipy/pandas. Promotes B-5 backups
  to required and off-box. Secrets stay in .env on-device (Rule 3); dashboard
  and kill CLI reached over SSH only — no ports exposed.
- **B-13 — Intraday σ curve for W1** (strategy; M) — σ that shrinks morning→
  afternoon (lead-time-varying), replacing the single conservative 3.2°F
  same-day scalar (B-1). Needs the true 0–18h signal error, which the IEM MOS
  archive can't supply (too sparse intraday); source is our own shadow forecasts
  (OD-9 calibration) or an NDFD gridpoint archive ETL. blocked-by: weeks of
  shadow forecasts, OR an NDFD archive path. Requires weather.py + worker
  signature change (σ by hours-to-settlement).
  plan-required (Fable) — not yet written; a plan must choose shadow-forecast
  vs NDFD-ETL source and fix the σ(hours-to-settlement) interface. B-4 output
  is an input.

## Hardening-session only

- **B-12 — Live order client, tested against the demo environment first**
  (platform; L) — order submission/cancel, venue error taxonomy, reconciliation,
  write-side rate limits; demo (`KALSHI_ENV=demo`, mock funds) validates
  mechanics, never economics. MUST NOT be scheduled in this bootstrap
  (Always-Apply Rule 1); recorded here so the hardening session inherits it.
