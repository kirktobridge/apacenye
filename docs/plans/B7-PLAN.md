# B-7 Plan — W2 Late-Day Determinism Worker

Status: PLANNED (not started). **Hard-gated on B-6's OD-12 verdict being
BUILD** — if B-6 returned SHELVE or EXTEND-CAPTURE, this plan must not be
picked up. Ships via `dev-cycle` with the `add-strategy-worker` skill
providing the build checklist; this plan settles the design decisions the
skill deliberately leaves open. Author: Claude Fable 5, 2026-07-19.
Backlog item: B-7 (strategy; M).

## Goal

Implement W2 (Stage 2 §3.2): by mid-afternoon the running max already bounds
the day's high, yet brackets it has foreclosed can still trade at
non-degenerate prices out of inattention. W2 proposes trades on the
**already-determined side** when the market still offers it below fair value
net of the ratified qualification rule.

## Scope decision: W2-v0 is strictly-determined-only

Stage 2's full design includes "near-determined" pricing from an empirical
additional-warming distribution. **v0 deliberately excludes it.** The B-6
evidence covers exactly the strictly-determined case (that was the study's
own pre-registered scope); building the warming-curve model now would put
unvalidated model risk on top of freshly validated market behavior. So:

- v0 trades only markets where `domain/determinism.py` (built and tested in
  B-6, imported verbatim — worker and study can never disagree) returns a
  determined side: NO on brackets with `hi < M_t`, YES on upper tails with
  `M_t ≥ K`, nothing else, with the ±0.5°F boundary guard.
- Undetermined brackets get **shadow evaluations only** (p_model from the
  determinism module's honest "no opinion" is not a probability — see
  Evaluations below), never intents.
- W2-v1 (warming curves, archive ETL) is a separate future backlog item
  with its own evidence bar; the worker's config/interface should not need
  to change for it (the "swap the p_model producer" property W1-v1 proves).

## Design decisions this plan resolves

### D1 — p_model for a determined side is capped below 1.0

The determinism claim is only as good as METAR-vs-settlement agreement (B-6
Q2). v0 sets:

```
p_model = 1 − max(observed_mismatch_rate, 3 / n_study_days)
```

The `3/n` term is the rule-of-three upper bound: even zero observed
mismatches in a ~30-day study cannot support certainty beyond ~1 − 3/30 ≈
0.90…0.99 depending on window length. Both inputs come from the B-6 memo
and are **frozen into `config/strategies/w2.yaml` with a comment citing the
memo**; they are strategy config (like W1's σ), updated only via new
evidence through `review-calibration`/study updates. Never a literal 1.0 —
a p of 1.0 would make Kelly want everything and turns any settlement quirk
into a guaranteed-loss position.

### D2 — Running max must be restart-safe: adapter grows a history fetch

`MetarAdapter.fetch_latest` returns one observation; a worker restarted at
4pm cannot reconstruct the day's running max from "latest" alone, and a
worker that trusts an in-memory max violates the restart-safety rule. Add
`fetch_day_observations(local_day)` to the adapter (NWS observations
endpoint with a start/end window), used by `_initialize` to rebuild `M_t`
from scratch; per-tick updates then fold in `fetch_latest`. No reliance on
our own capture files at runtime (capture is for replay, not a live
dependency).

### D3 — Bad-observation guard: QC filtering, not spike heuristics

A single erroneous METAR spike would create false determinism and a
worst-case entry (buying "certainty" that is wrong). The NWS observations
API carries `qualityControl` flags on temperature; the adapter must surface
them and the worker accepts only QC-passed values into the running max.
No hand-rolled spike filters (that's inventing a QC system); if QC flags
prove unusable in practice, that goes back to the owner as an open decision
rather than a silent heuristic.

### D4 — Entry window and staleness

- Entry window: config `entry_window_start_local` (default `"14:00"`),
  timezone `America/New_York` via `zoneinfo` (DST-correct by construction).
  Outside the window `_evaluate` returns after a heartbeat — no intents, no
  shadow evaluations (nothing is being estimated yet).
- Staleness: `staleness_s: 4500` (75 min, Stage 2's rule) — the single
  per-strategy window G4 checks against every `*_ts` in `key_inputs`. The
  observation ts AND the quote ts both ride in `key_inputs`, so G4 enforces
  both against 4500 s; quotes are far fresher, so this is safe. Stale ob ⇒
  no new intents and cancel outstanding (same pattern as W1's stale
  branch).
- Cadence: `cadence_s: 300`. METAR is ~hourly but quotes move faster, and
  the opportunity is a decaying race after each new ob; 5 min is cheap
  against the read-only API budget and matches the B-6 episode-persistence
  resolution.

### D5 — Coexistence with W1 on the same event is contention by design

W1 and W2 both trade KXHIGHNY brackets; OD-7 makes all brackets of one
settlement event ONE exposure **across strategies** (enforced by the
orchestrator's event-cap gate against the ledger). Expect W2 intents to be
resized down or rejected when W1 already holds the event — that is the risk
engine working, not a bug. The worker still does its own first-line event
budget check from its *own* positions (W1's pattern), knowing the
orchestrator's ledger view wins. The one-pager must state this expected
interaction so a quiet W2 isn't misread as broken.

### D6 — Evaluations and calibration honesty for a near-certainty strategy

- Determined markets: shadow evaluation every tick in-window with the
  capped p_model — these settle almost always "correctly" and will make W2's
  raw Brier look spuriously excellent; the one-pager notes that W2's
  calibration review (B-4 tooling) should focus on the *mismatch* tail and
  the qualified-subset adverse-selection check, not the headline Brier.
- Exit policy: hold to settlement (hours away by construction; no exit
  fee). The running max is monotone, so a determined verdict cannot flip;
  the only reversal risk is the settlement-source mismatch already priced
  into D1's cap. No early-exit logic in v0.

## What this build does NOT need

- **No contract changes**: OrderIntent/Evaluation fields cover everything
  (ob value + ts and running max ride in `key_inputs`; `quote_seen` per
  OD-15). If implementation discovers otherwise, stop and flag — contract
  amendments are user-ratified.
- **No new risk.yaml values**: shared floor/λ/k/caps inherited unchanged.
  At p_used ≈ 0.95+, Kelly fractions are huge and the hard caps do the real
  sizing work — by design (Stage 2 §3.2); tests must pin this.
- No changes to W1, the risk engine, or the scheduler beyond one
  `register_worker` line in `cli.py::_serve`.

## Files touched

| File | Change |
|---|---|
| `docs/strategies/w2.md` | New one-pager — written FIRST (skill step 1) |
| `config/strategies/w2.yaml` | New: station, series/event resolution, window, staleness 4500, cadence 300, p_model cap params citing B-6 memo |
| `src/apacenye/dataadapters/metar.py` | Add `fetch_day_observations` + QC flag surfacing (D2, D3) |
| `src/apacenye/workers/w2_determinism.py` | New worker, subclassing `StrategyWorker`, importing `domain/determinism.py` |
| `src/apacenye/cli.py` | Construct + `register_worker` alongside W1 |
| `tests/` | New: worker unit tests + adapter parsing tests |

## Implementation order (per `add-strategy-worker`, tests-first)

1. Confirm the gate: B-6 memo verdict is BUILD; lift `observed_mismatch_rate`
   and `n_study_days` from it. If the memo carries design amendments (e.g.,
   a mismatch case), fold them in here before coding.
2. One-pager `docs/strategies/w2.md`: thesis, D1–D6 decisions, the OD-12
   evidence citation, expected W1 contention, calibration-review caveat.
3. Adapter tests + implementation for `fetch_day_observations` and QC
   filtering (recorded API fixtures; no live calls in tests).
4. Worker tests: determinism-to-intent mapping at boundary values, p_model
   cap arithmetic, window logic across a DST transition date, staleness
   refusal + cancel-outstanding, restart rebuild of `M_t` (INIT-after-crash),
   sizing at p ≈ 0.97 (asserting caps, not Kelly, set the size), PAUSE
   stops emissions immediately.
5. Worker to green; wire registration; `uv run pytest` clean.
6. Soak: `RUN_MODE=DRY_RUN` through at least 3 afternoon sessions —
   dispositions and ExplanationRecords reviewed for sanity (are proposed
   sizes cap-bound? do windows open/close on time?).
7. Owner gate: `apacenye ack --strategy W2 --gate paper` (owner's own ack,
   never scripted), then PAPER.
8. `dev-cycle` close-out: DEV_LOG entry, delete B-7 from BACKLOG.md.

## Risks / notes

- **The strategy's failure mode is data error, not model error**: a wrong
  ob or a settlement-source mismatch turns "free money" into a locked loss
  held to settlement. D1's cap and D3's QC guard are the mitigations; both
  must be tested at their edges.
- Adverse-selection caveat from B-6 carries over: a quote persisting at
  size may persist because someone knows the ob we haven't ingested. The
  75-min staleness rule is the mitigation; the DRY_RUN soak should
  specifically eyeball cases where our fill-side quote moved right after a
  new ob.
- Paper fills remain an optimistic bound (no queue competition) — W2's
  apparent paper edge will overstate live edge more than W1's, because the
  whole strategy lives in the race window; say so in the one-pager.
- New strategy ⇒ new risk-relevant config hash ⇒ paper ack required before
  START (checkpoint K-gates); this is the owner's step, not the agent's.
