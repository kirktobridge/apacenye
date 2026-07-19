# B-4 Plan — Calibration Report Tooling

Status: PLANNED (not started). Ships via the `dev-cycle` skill when picked up.
Author: Claude Fable 5, 2026-07-19. Backlog item: B-4 (platform; M).

## Goal

A repeatable, owner-readable calibration report for a strategy's shadow
forecasts — reliability table + Brier-vs-market summary from the
`evaluations` table — implementing steps 1–5 of the `review-calibration`
skill as tooling, so the skill's evidence loop (the ONLY sanctioned path for
changing λ/k/σ, OD-9) runs off one audited computation instead of ad-hoc
queries each time.

## What exists today (verified in code)

- `evaluations` table logs every shadow forecast (`ledger.py` DDL ~L134);
  `markets` carries `status`, `settled_side`, `settled_ts` (~L37) — the
  outcome join the skill describes already has a home.
- Settlement is detected by the market-data feed while `serve` runs
  (`feed.py` → `orchestrator.on_settlement` → `ledger.settle_market`).
  Consequence: a market that settles while the server is down stays
  `status='open'` forever — a data-completeness hole the report must surface.
- `backtest/replay.py` L202–210 already computes model-vs-market Brier
  inline. Two Brier implementations must not drift; replay will reuse the new
  module.
- Replay runs in a `tempfile.TemporaryDirectory` ledger (`cli.py`
  `cmd_backtest`), so `data/apacenye.sqlite` contains **paper-mode samples
  only** — no replay/paper mixing risk in the live DB. The report still
  labels its provenance explicitly (honesty rail).
- `GET /api/evaluations` exists but returns raw rows only; no outcome join,
  no metrics.

## Decisions this plan resolves

### D1 — Surface: CLI subcommand, no dashboard page

`apacenye calibration --strategy W1 [--since YYYY-MM-DD] [--until YYYY-MM-DD]
[--json]`. Plain-text/markdown report to stdout; `--json` for machine use.

Rationale: the consumer is the owner running `review-calibration` at a
terminal; a dashboard page adds service-layer surface with near-zero value
until evaluations flow for weeks, and B-11 covers dashboard evolution
separately. No plotting dependency (matplotlib is not in the stack): the
reliability "curve" is a decile **table** — bucket, n, mean p_model, observed
frequency, gap — which is what the skill actually needs to quote in DEV_LOG.

### D2 — Placement: pure math in `domain/`, SQL in ledger, wiring in CLI

- `src/apacenye/domain/calibration.py` (new): pure, tests-first functions on
  plain sequences — Brier score, reliability binning, qualified/traded subset
  split, sample-size accounting. No I/O, no SQL, plain-language docstrings
  (owner reviews personally).
- `src/apacenye/orchestrator/ledger.py`: new read methods (SQL lives only
  here): `settled_evaluations(strategy_id, since, until)` — evaluations
  JOIN markets ON `market_ticker` WHERE `status='settled'`, returning rows
  with `outcome` (1.0 if `settled_side='yes'` else 0.0) — and
  `evaluation_coverage(strategy_id)` — counts of total / settled / unsettled
  / NULL-mid rows and distinct event tickers, for the honesty header.
- `src/apacenye/cli.py`: subcommand formatting the report. Read-only against
  the DB (safe alongside a running server; WAL).
- `src/apacenye/backtest/replay.py`: replace inline Brier lines with calls
  into `domain/calibration.py`. Behavior-identical refactor, guarded by a
  test pinning current output on a fixed dataset.

### D3 — Methodology (locked to the `review-calibration` skill, steps 2–5)

Report sections, in order:

1. **Provenance header**: DB path, strategy, date window, "PAPER shadow
   forecasts" label, and coverage counts — total evaluations, settled
   (scoreable), unsettled, dropped-for-NULL-mid. Nothing is silently dropped;
   every exclusion prints a count.
2. **Sample size first**: scoreable rows n AND distinct settlement events
   (same-event brackets are correlated; effective n ≈ events). Under **100
   settled rows** the verdict is hard-stamped `INSUFFICIENT-DATA`; metrics
   still print, labeled "directionally interesting, evidentially nothing."
3. **Brier, model vs market**: mean (p − outcome)² for `model_probability`
   and `market_implied_probability` over the **identical** row set (rows with
   NULL mid excluded from both, count reported). Market mid is the benchmark
   to beat.
4. **Reliability table**: p_model deciles — count, mean p_model, observed
   frequency, gap — with a one-line note on where deviation concentrates
   (tail over-confidence is W1-v0's expected failure mode).
5. **Qualified-subset check**: sections 3–4 recomputed for `qualified=1` rows
   and for `intent_id IS NOT NULL` rows. If the selected subset scores
   *worse* than the unselected, print an explicit **adverse-selection
   warning**.
6. **Verdict line**: `insufficient-data | calibrated |
   miscalibrated-in-<direction>`, mechanically derived thresholds stated in
   the output itself so the owner can audit the rule, not just the answer.

The tool computes and reports; it never recommends parameter values. λ/k/σ
recommendations remain a human step inside `review-calibration` → owner
ratification → `dev-cycle` (OD-9).

### D4 — Settlement-completeness backfill: mark-only, positionless markets

New CLI flag `apacenye calibration --backfill-settlements`: for evaluated
markets still `open` well past their settlement horizon, query the read-only
Kalshi client for the settled result and mark them via the ledger —
**only for markets with no open positions** (pure calibration bookkeeping,
no P&L side effects). Markets with open positions that appear settled
venue-side are *listed for attention* (an `operate-paper` matter — position
realization stays on the server's `on_settlement` path, which also cancels
resting orders). Default is no network access: without the flag the report
just prints the unsettled count.

Requires a small ledger write method (`mark_market_settled`) that is a no-op
if already settled — it must NOT reuse `settle_market` (which realizes
positions).

### D5 — Precondition to verify before trusting the join (step 0 of impl)

Confirm in `w1_forecast.py` that `Evaluation.model_probability` and
`market_implied_probability` are consistently **P(YES) of the ticker**
regardless of which side any resulting intent trades. If any code path logs
a side-relative probability, fix that first (it silently corrupts every
downstream metric). Record the finding in the DEV_LOG entry either way.

## Out of scope

- Dashboard/HTML rendering (later, possibly with B-11).
- Plotting images or new dependencies.
- Any λ/k/σ recommendation logic or auto-tuning (OD-9 forbids).
- σ-by-lead-time analysis (B-13's territory; this report is a B-13 input).
- Replay-window calibration reports (replay already prints its own Brier via
  the shared function; full replay reports inherit run-backtest's
  illustrative-only rails and can come later).

## Implementation order (tests-first)

1. Verify D5 (probability side convention); fix if violated.
2. `tests/test_calibration.py`: Brier (known hand-computed cases), decile
   binning incl. empty/sparse buckets, subset splits, insufficient-data
   gate at exactly n=99/100, event-vs-row counting.
3. `domain/calibration.py` to green.
4. Ledger read methods + tests against a seeded temp DB (settled/unsettled/
   NULL-mid mixtures).
5. Refactor `replay.py` onto the shared Brier; pin-output test first.
6. CLI subcommand + `--json`; golden-file test of the text report.
7. `--backfill-settlements` (D4) + tests (no-op on settled, refuses markets
   with open positions).
8. `dev-cycle` close-out: DEV_LOG entry, delete B-4 from BACKLOG.md.

## Risks / notes

- **No contract change**: nothing in `src/apacenye/contract/` is touched.
- **Not risk-relevant config**: no risk.yaml change, no ack invalidation.
- Report code is money-adjacent evidence tooling: same review standard as
  money-touching code — boring, explicit, docstrings in plain language.
- Small-n decile tables will be mostly empty for weeks; that is correct
  output, not a bug — the tool exists so the evidence loop is ready when the
  data arrives (backlog blocked-by acknowledges this).
