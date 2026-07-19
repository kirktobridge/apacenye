---
name: review-risk-pre-live
description: Review a strategy's risk profile before any live-capital discussion — the evidence-gathering workflow that leads up to and triggers the Stage 3 concept checkpoint (live gate). Use when the user asks whether a strategy is ready for real money, or wants a pre-live risk review.
---

# Review a Strategy's Risk Profile Pre-Live

This workflow **leads up to and triggers the Stage 3 §11 concept checkpoint — it is not a second, separate gate.** The checkpoint (`apacenye ack`, the five concepts, the append-only hash-chained log) is the *mechanism*; this skill is the homework that makes running it meaningful. In this bootstrap the flow **always terminates at the live hard-disable wall** (Stage 3 §6): the checkpoint runs, is recorded, and live enablement is refused with `outcome_note: "live refused: bootstrap hard-disable"`. A bootstrap-era live ack pre-authorizes nothing.

## Steps

1. **Check the blocking [verify] items first.** A live discussion is premature while any figure the strategy depends on is still an estimate: OD-1 (real fee schedule per series), OD-2 (real spreads/depth for its markets), OD-3 (listings), plus strategy-specific ODs (e.g., OD-12 for W2). List each with its verification status; any open blocker ends the review with "not ready — verify X" rather than proceeding on assumptions.
2. **Pull calibration evidence** — the shadow-forecast records (`GET /api/evaluations?strategy=<id>` or the `evaluations` table): sample count, Brier score vs. the market's Brier score (the market mid is the benchmark to beat), reliability curve. State plainly whether the sample size supports any conclusion.
3. **Review paper P&L under the honesty rules**: paper fills are an optimistic bound; **illustrative-only backtests are excluded from a live argument entirely** (Stage 3 §9). Only weeks of own-capture-based results even enter the discussion.
4. **Review the risk configuration as it would apply live** (`config/risk.yaml` + strategy config): recompute worst-case drawdown by hand — per-event cap dollars, per-strategy cap dollars, and "if every position went to $0" — and check the daily-loss stops and portfolio cap headroom. This is the same math the checkpoint's K3 question will ask; the review should produce the number before the gate asks for it.
5. **Review operational history**: disposition log (`GET /api/intents` — which gates bind and how often), staleness incidents, heartbeat gaps, worker restarts, S1 data-sanity alerts, kill events. A strategy whose intents are routinely resized by G5 (liquidity) is telling you its size assumptions are wrong.
6. **Review residual correlation exposure** (OD-20): same-event brackets are capped (G7), but cross-day/cross-city weather correlation is mitigated only by the portfolio cap in v0 — state this limitation in the review.
7. **Write the review down** (short doc or summary in the session): evidence for/against, open ODs, worst-case numbers, recommendation.
8. **Then trigger the checkpoint**: `apacenye enable-live --strategy <id>` — which requires completing the full live gate (all five concepts K1–K5, computed from the strategy's *current* config, typed acknowledgments, 3-failure abort), appends the attempt to `data/acks/acknowledgments.jsonl`, and in this bootstrap then prints the hard-disable refusal and records it. Verify log integrity any time with `apacenye ack --verify-log`.
9. **Never bypass the wall.** If the review concludes "ready," the correct output is a note that a future dedicated hardening session (with its own acceptance gate and a fresh acknowledgment) is the next step — not code changes to `execution/live.py`.

## Verified After Scaffolding (Stage 5, 2026-07-19)

- [x] CLI spellings all confirmed: `apacenye enable-live --strategy <id>`, `apacenye ack --strategy <id> --gate live`, `apacenye ack --verify-log`.
- [x] Ack log at `data/acks/acknowledgments.jsonl`; chain fields `seq` and `prev_sha256` (sha256 of the previous LINE; 64 zeros at genesis), written O_APPEND. `--verify-log` walks the chain.
- [x] Risk-relevant hash params as implemented (`checkpoint/ack.py::RISK_RELEVANT_FIELDS`): `bankroll_usd`, `max_event_exposure_pct`, `max_strategy_exposure_pct`, `max_portfolio_exposure_pct`, **`max_order_contracts`, `max_depth_fraction`** (both added beyond the Stage 3 §11.2 minimum — "all exposure caps" read broadly, conservative direction, D5-6), `kelly_multiplier`, `shrinkage_lambda`, `min_net_edge`. Daily-loss stops and staleness windows do NOT invalidate acks.
- [x] Endpoints exist as named: `/api/evaluations`, `/api/intents`, `/api/risk`, `/api/acks` (plus `/api/explanations/{intent_id}`).
- [x] Calibration metrics: the replay harness computes Brier (model vs. market benchmark) per run; live-shadow-forecast analysis is pandas over the `evaluations` table or `GET /api/evaluations` — there is no dedicated calibration endpoint yet.
- [x] `enable-live` verified end-to-end (smoke-tested): full K1–K5 gate → typed `ENABLE LIVE <id> CONFIG <hash>` line → `LiveDisabledError` printed → `outcome_note: "live refused: bootstrap hard-disable"` appended to the chained log.
- [x] Table names match the real DDL: `evaluations`, `dispositions` (and `realizations` holds realized P&L rows the daily stops read).
