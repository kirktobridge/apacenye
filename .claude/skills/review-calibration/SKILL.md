---
name: review-calibration
description: Review shadow-forecast calibration for an Apacenyë strategy — Brier vs. the market benchmark, reliability, sample-size honesty — the ONLY sanctioned evidence path for changing λ, k, or σ (OD-9). Use for "is the model calibrated", "can we raise λ/k", "how is W1 actually doing", or periodic calibration check-ins.
---

# Review Shadow-Forecast Calibration

Every evaluation logs `p_model`, the market mid, and (after settlement) the
outcome — traded or not. That dataset, not P&L, is how the model is judged:
paper P&L is an optimistic bound and says almost nothing at small n; the
sizing hyperparameters (λ = 0.5, k = 0.25) and W1's σ move **only** on the
evidence this review produces, ratified by the owner (OD-9).

## The tool

`apacenye calibration --strategy W1 [--since D --until D] [--json]` computes
steps 2–6 below off ONE audited computation (`domain/calibration.py`, the same
Brier the replay harness uses) — run it instead of ad-hoc queries. It reports
only; it never recommends λ/k/σ (step 7 stays a human call). If markets settled
while `serve` was down they sit `open` and unscoreable; `--backfill-settlements`
marks the positionless ones from the read-only Kalshi API first.

## Steps

1. **Pull the data**: normally just run the tool above. Under the hood it is the
   `evaluations` table (or `GET /api/evaluations?strategy=`) joined to settled
   outcomes via `markets.settled_side`; only evaluations on markets that have
   SETTLED count. For replay windows the backtest CLI computes the same join.
2. **State the sample size first**, and refuse conclusions it can't carry:
   under ~100 settled samples, report "insufficient data" and stop —
   directionally interesting, evidentially nothing. Note that same-event
   brackets are correlated (one weather outcome drives all of them):
   effective n ≈ number of EVENTS, not rows. Count both.
3. **Brier, model vs. market**: mean (p − outcome)² for `model_probability`
   and for `market_implied_probability` over identical rows. The market mid
   is the benchmark to beat — a model that loses to the mid has no business
   proposing trades whatever its P&L says.
4. **Reliability curve**: bucket p_model (e.g. deciles), compare bucket mean
   vs. observed frequency with a count per bucket. Systematic over-confidence
   in the tails is the expected W1-v0 failure mode (crude Gaussian, guessed σ)
   — say where it deviates, not just that it deviates.
5. **Qualified-subset check**: same metrics restricted to evaluations that
   emitted intents. Selection should IMPROVE calibration edge; if the traded
   subset is worse than the untraded, the qualification rule is selecting
   adverse spots (classic adverse-selection signature) — flag loudly.
6. **Write the verdict down** (DEV_LOG entry, scope `strategy`): n (rows and
   events), both Briers, reliability summary, verdict — one of
   *insufficient-data / calibrated / miscalibrated-in-⟨direction⟩*.
7. **Parameter changes exit through governance, not this skill**: a
   recommendation (e.g. "raise λ to 0.7", "σ→2.4 from archive + shadow
   evidence") goes to the owner for ratification, then ships via `dev-cycle`
   (risk-relevant branch: λ and k invalidate paper acks; σ is strategy config
   and does not). Never tune and review in the same breath.

## Honesty rails

- Replay-derived calibration inherits the illustrative-only label of its
  capture window; say so wherever quoted.
- Never mix DRY_RUN/paper/replay samples in one metric without labeling.
- A good Brier with thin P&L is progress; good P&L on 12 samples is noise
  (run-backtest skill, step 4 — same rule here).
