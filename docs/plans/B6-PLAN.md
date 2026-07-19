# B-6 Plan — OD-12 Study: Late-Day Quote Persistence

Status: PLANNED (not started). Blocked-by: B-3 (METAR capture wiring) plus
weeks of overlapping capture. Ships via `dev-cycle` when picked up.
Author: Claude Fable 5, 2026-07-19. Backlog item: B-6 (research; M).

## Goal

Answer OD-12 with our own capture data so B-7 (W2 worker) is either unblocked
on evidence or shelved on evidence. Per Stage 2 §3.2/OD-12, this is TWO
questions, not one:

- **Q1 — Persistence**: do stale late-day quotes (prices contradicting the
  already-locked running max) persist at tradeable size, for long enough
  that we could actually have traded them?
- **Q2 — Settlement fidelity**: how often does the live METAR feed disagree
  with the official settlement result? (W2's `p_model = 1` determinism claim
  is only as good as this agreement rate.)

The core discipline: **all definitions and the decision rule are frozen in a
pre-registered memo and committed BEFORE any analysis runs.** A study that
defines "tradeable" after seeing the data will find whatever it wants to
find; this plan exists to make that impossible.

## What exists today (verified in code)

- Capture channels (`backtest/capture.py`): `book` (full `MarketSnapshot`
  dumps — top-of-book bid/ask + depth + ts), `trade`, `settlement`, `market`
  (catalog metadata incl. bracket bounds), `nws_forecast`. The `metar`
  channel exists but is **not yet wired into serve** — that is B-3, and this
  study has no data until B-3 ships and accumulates.
- Book capture resolution is the feed poll interval, **default 15 s**
  (`feed.py`) — persistence must be defined in units of consecutive polls,
  not milliseconds we never observed.
- Bracket bounds with strict-tail semantics come from the catalog and are
  captured on the `market` channel; the study must reuse that parsing, not
  re-derive ticker semantics.
- Fee math exists in `domain/fees.py`; the qualification rule is ratified
  (net edge ≥ 4 pts after fee + $0.01 slippage at executable prices). The
  study reuses both verbatim — "mispriced" means *our own rule would have
  fired*, not an ad-hoc threshold.
- Relevant caps for "tradeable size" (risk.yaml): ≤ 25% of visible
  top-of-book depth, ≤ 100 contracts/order, event exposure ≤ $50. At W2's
  typical 90–99¢ prices the depth cap is the binding one.

## Pre-registered definitions (proposed — owner ratifies before analysis)

### Determinism (Q1's trigger condition) — strictly determined only

Running max `M_t` = max METAR temperature for the settlement station over
the local day, using **only observations with capture-ts ≤ t** (no
lookahead; the ob's own age is recorded). Then, respecting the catalog's
bracket bounds and strict-tail semantics:

- A bracket with `hi < M_t` is **NO-determined** (the day's max already
  exceeds it).
- An upper tail (`≥ K`) with `M_t ≥ K` is **YES-determined**.
- Everything else — middle brackets not yet exceeded, lower tails — is
  **undetermined**, because temperature can still rise. The study does NOT
  model "additional warming" probabilities; that empirical curve is W2 build
  material, and using a guessed version here would let model error
  contaminate the market-behavior measurement. Strictly-determined cases are
  a clean lower bound on the opportunity.
- **Unit/rounding guard**: METAR reports °C (tenths); brackets are integer
  °F; the official report has its own rounding. Any `M_t` within 0.5°F of a
  bracket boundary is classified **undetermined** (conservative — boundary
  cases are exactly where settlement-source rounding bites).

### A qualifying episode (Q1's unit of count)

All of the following, simultaneously, evaluated per book snapshot:

1. Market is determined (above) and the determining METAR ob is ≤ 75 min
   old at snapshot time (W2's own staleness rule — if W2 couldn't have
   traded it, it doesn't count).
2. Snapshot time is inside W2's entry window (after 14:00 local,
   America/New_York, DST-aware) and before market close.
3. The determined side's **executable** price clears the ratified rule:
   `1 − price − fee(price) − 0.01 ≥ 0.04` (YES-determined: price = ask;
   NO-determined: NO-side equivalent from 1 − bid). Never mid.
4. **Tradeable size**: `min(0.25 × visible_depth, 100, floor($50 / price))`
   ≥ **10 contracts**.
5. **Persistence**: conditions 1–4 hold on ≥ **2 consecutive book polls**
   (≈ ≥ 15–30 s at default cadence) — one poll proves a snapshot existed,
   not that we could have acted on it.

Consecutive qualifying snapshots merge into one episode; episodes also
report duration, depth, and net edge distributions, and the calendar spread
of episode days (10 episodes on one freak day ≠ 10 days with one each).

### Q2 — Settlement mismatch rate

For each settled event day in the window: the bracket implied by the
end-of-day METAR daily max vs. the actually-settled bracket (from the
`settlement` channel / `markets.settled_side`). Report mismatch days /
total days, and list every mismatch with its temperature delta.

### Decision rule (proposed numbers — owner ratifies or amends at pick-up)

Over a window of ≥ 28 calendar days with adequate coverage (below):

- **BUILD (unblock B-7)**: ≥ 8 qualifying episodes spread over ≥ 5 distinct
  days, AND Q2 mismatch on ≤ 1 day. If mismatches exist, W2's design must
  cap `p_model < 1` accordingly — carried into B-7 as a design input.
- **SHELVE B-7**: ≤ 2 episodes over a well-covered window — the inattention
  edge does not survive contact at our size.
- **EXTEND-CAPTURE**: anything between, or coverage too thin to say —
  re-run after N more weeks; extension is not failure.

## Coverage precondition (do this FIRST, before any Q1/Q2 analysis)

The study is silently biased by capture gaps: the server only captures while
running, and late-day episodes need the feed alive from 14:00 local through
settlement. Step one is a **coverage report**: per day, whether `book` and
`metar` channels both span the entry window, and the poll-gap distribution.
Days with material gaps are excluded *by the pre-registered coverage rule*
(proposed: any gap > 10 min inside the window excludes the day), and the
excluded-day count is reported in the memo. If coverage is thin, the honest
output is EXTEND-CAPTURE — plus an `operate-paper` note that afternoon
uptime is now a data-quality requirement.

## Deliverables & code placement

1. **`docs/research/OD12-STUDY.md`** — the memo. Committed in two stages:
   first the frozen definitions + decision rule (pre-registration commit,
   before analysis code runs against real capture); then results + verdict
   appended. The pre-registration commit hash is cited in the results
   section.
2. **`src/apacenye/domain/determinism.py`** (new, tests-first): pure
   functions — `determined_side(bracket_lo, bracket_hi, running_max_f)`
   with the boundary guard, and episode-qualification math. Placed in
   `domain/` deliberately: **W2 will import this exact audited logic later**
   (its `p_model = 1` branch), so the study and the eventual strategy cannot
   disagree about what "determined" means.
3. **`research/od12_study.py`** — thin read-only driver: loads capture via
   `CaptureWriter.read_day`, joins channels, applies domain functions,
   emits the memo's tables. Pandas allowed; no writes outside stdout.
   (`research/` is the existing convention — `estimate_sigma_w1.py`, the
   OD-11 study, lives there.)
4. DEV_LOG entry (scope `research`): window, coverage, episode count, Q2
   rate, verdict. BACKLOG updated: B-6 deleted; B-7 unblocked, amended
   (p_model cap), or shelved.

## Out of scope

- Building any part of W2 beyond `domain/determinism.py` (B-7, gated on
  this verdict).
- Additional-warming ("near-determined") probability curves — W2 build
  input, not study input.
- Backtesting W2 P&L on the window (run-backtest territory, illustrative-
  only; this study measures market behavior, not strategy performance).
- Second-city generalization (B-8; this study is KNYC-only and says so).

## Implementation order

1. Pre-registration: write memo definitions + decision rule; owner ratifies
   the proposed thresholds (episode minimum, 10-contract size floor,
   coverage rule, Q2 tolerance); commit.
2. Tests for `domain/determinism.py` (boundary cases: `M_t` at/near bracket
   edges, tails vs middles, unit conversion rounding); implement to green.
3. Coverage report over the accumulated capture; if inadequate →
   EXTEND-CAPTURE verdict now, stop honestly.
4. Q1 episode scan + Q2 mismatch table; append results + verdict to memo.
5. DEV_LOG + BACKLOG updates per verdict.

## Risks / honesty rails

- **Adverse-selection blind spot**: a persistent cheap quote may be cheap
  because someone knows an ob we haven't seen (Stage 2 flags this exact
  failure mode). Persistence measures *availability*, not *safety* — the
  memo must say so, and W2's staleness rule remains the mitigation, not
  this study.
- **Optimism direction**: every approximation here (top-of-book only,
  paper-style executability, no queue competition) errs optimistic. A
  SHELVE verdict is therefore strong; a BUILD verdict is a lower-bound
  argument that still meets the qualification floor with room.
- Capture-derived findings inherit the provenance of the window: this is
  our own live capture (executability-honest), but a single station, single
  city, single season — the verdict is scoped to KNYC-now, and B-8 does not
  inherit it.
- No risk-config or contract changes anywhere in this item.
