# Backlog

Schema: `docs/SCHEMAS.md`. Triage in via the `triage-idea` skill; work items
out via the `dev-cycle` skill (which deletes them here and records them in
`DEV_LOG.md`).

## Now

_(nothing committed as next work)_

## Next

- **B-3 — Wire METAR capture into serve** (data; S) — record KNYC observations
  from day one so the OD-12 study has data; adapter exists, just uncaptured.
  blocked-by: nothing.
- **B-4 — Calibration report tooling** (platform; M) — reliability curve +
  Brier summary from the evaluations table (feeds review-calibration skill).
  blocked-by: enough shadow-forecast samples to be worth plotting (~weeks).
- **B-5 — Ledger/capture backup routine** (ops; S) — periodic copy of
  data/apacenye.sqlite + capture/ out of the working tree.
  blocked-by: nothing.

## Later / blocked

- **B-6 — OD-12 study: late-day quote persistence** (research; M) — do stale
  late-day quotes persist at tradeable size? W2's precondition, answerable
  from our own capture. blocked-by: weeks of capture incl. B-3.
- **B-7 — W2 late-day determinism worker** (strategy; M) — blocked-by: B-6/OD-12.
- **B-8 — Second city onboarding** (data; M) — blocked-by: OD-2 measurement
  from capture + OD-10 ratification of which city.
- **B-9 — W1-v1 ensemble p_model** (strategy; L) — same worker, better
  distribution. blocked-by: B-1 first (fix σ before replacing the model), plus
  calibration evidence that the Gaussian is the binding limitation.
- **B-10 — E1 FOMC cross-venue worker** (strategy; M) — blocked-by: OD-13
  (fed-funds-futures data path/licensing).
- **B-11 — Dashboard signals view: htmx WebSocket extension** (platform; S) —
  replace 3 s polling on the signals feed; still zero hand-written JS.
  blocked-by: nothing (low value until evaluations flow daily).
- **B-13 — Intraday σ curve for W1** (strategy; M) — σ that shrinks morning→
  afternoon (lead-time-varying), replacing the single conservative 3.2°F
  same-day scalar (B-1). Needs the true 0–18h signal error, which the IEM MOS
  archive can't supply (too sparse intraday); source is our own shadow forecasts
  (OD-9 calibration) or an NDFD gridpoint archive ETL. blocked-by: weeks of
  shadow forecasts, OR an NDFD archive path. Requires weather.py + worker
  signature change (σ by hours-to-settlement).

## Hardening-session only

- **B-12 — Live order client, tested against the demo environment first**
  (platform; L) — order submission/cancel, venue error taxonomy, reconciliation,
  write-side rate limits; demo (`KALSHI_ENV=demo`, mock funds) validates
  mechanics, never economics. MUST NOT be scheduled in this bootstrap
  (Always-Apply Rule 1); recorded here so the hardening session inherits it.
