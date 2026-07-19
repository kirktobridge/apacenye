---
name: add-strategy-worker
description: Add a new strategy worker to Apacenyë conforming to the Stage 2 worker contract — lifecycle, OrderIntents, shadow forecasts, sizing, risk self-checks, and the paper acknowledgment gate. Use when implementing any new trading strategy (W2, E1, E2, A1, or a new archetype).
---

# Add a Strategy Worker

Workers are self-contained, swappable asyncio tasks. They **propose trades; they never place orders**. Read `docs/initial-bootstrap/handoffs/stage2-strategies.md` §3–§5 (contract + risk defaults) and `stage3-architecture.md` §0–§3 (mechanical bindings, gate pipeline) before starting.

## Steps

1. **Write the strategy one-pager first** (in `docs/strategies/<id>.md`): thesis (why the market is wrong and why that persists), data sources with access terms, how `p_model` is computed, entry/exit rules, staleness rule, and which Open Decisions ([verify] items) it depends on. If a blocking OD is unverified, stop and flag — do not build on an unverified figure.
2. **Read the contract module** `src/apacenye/contract/` and the existing reference worker `src/apacenye/workers/w1_forecast.py`. The contract module is the interface; do not modify it — a needed field change is a contract amendment and must be flagged to the user (precedent: OD-15 `quote_seen`).
3. **Create the strategy config** `config/strategies/<id>.yaml`: evaluation cadence, staleness window(s), strategy-specific parameters, and the shared sizing tunables (edge floor 0.04, λ=0.5, k=0.25 — inherit defaults; do not loosen).
4. **Build or reuse a data adapter** in `src/apacenye/dataadapters/` (see the `onboard-data-source` skill for a genuinely new external source). Every fetched datum must carry its source timestamp.
5. **Implement the worker** subclassing the lifecycle ABC in `src/apacenye/workers/base.py`:
   - All five lifecycle states: `INIT` (validate data access, load calibration state, no intents), `START`, `PAUSE` (stop emitting immediately, stay warm), `STOP` (cancel outstanding, persist), `UPDATE_CONFIG` (hot-apply or reject with reason).
   - Restart-safe: rebuild all views from orchestrator-supplied positions + refetched data. Hold no authoritative position state.
   - Evaluate only on platform ticks — never `sleep`-loop or read the wall clock for scheduling (this is what makes replay backtesting honest).
6. **Emit correct outputs**:
   - `OrderIntent` with *every* mandatory field, including the five explanation fields (`model_probability`, `market_implied_probability`, `net_edge`, `confidence`, `key_inputs` with timestamps), the full `sizing` trace, a one-sentence plain-English `rationale`, a `ttl_seconds`, and a `limit_price` (never market orders).
   - A **shadow evaluation record on every evaluation**, traded or not — this is the calibration dataset; never skip it.
   - `CancelIntent` when the edge flips or inputs go stale; periodic heartbeats.
7. **Self-enforce risk limits** as the first line (qualification rule, per-event/per-strategy caps, liquidity fraction, staleness) knowing the orchestrator re-checks everything and its ledger view wins. Treat every intent as possibly rejected or unfilled — correctness must not depend on fills.
8. **Handle same-event correlation**: all brackets of one settlement event are one exposure. Sum your own proposed cost across the event's brackets before proposing more.
9. **Register the worker** with the orchestrator's worker registry and the `TickScheduler` cadence (see how `w1_forecast.py` is wired in).
10. **Tests first, then the worker**: unit-test `p_model` math against hand-computed cases, the qualification rule at boundary values, sizing (shrinkage → Kelly → caps ordering), staleness refusal, and lifecycle transitions (especially PAUSE stops emissions immediately and INIT-after-crash rebuilds cleanly).
11. **Gate before running**: `apacenye ack --strategy <id> --gate paper` (the orchestrator refuses to START without a PASSED ack for the current risk-relevant config hash). Then run in `RUN_MODE=DRY_RUN` first; only move to `PAPER` after dispositions and explanation records look sane.

## Verified After Scaffolding (Stage 5, 2026-07-19)

All assumptions checked against the real code; corrections noted:

- [x] Lifecycle ABC: `src/apacenye/workers/base.py`, class `StrategyWorker`. Method spellings: `await initialize()` (INIT), `start()`, `pause()`, `await stop()`, `await update_config(dict) -> (ok, reason)`. States are the `LifecycleState` enum (`INIT/START/PAUSE/STOP`); **UPDATE_CONFIG is a method, not a state**. Subclasses implement `_initialize`, `_evaluate(tick)`, `_validate_config`, and optionally `data_ages_seconds()`.
- [x] Contract models: `src/apacenye/contract/models.py` (re-exported from `apacenye.contract`). **OD-15 `quote_seen` IS on OrderIntent** (typed `QuoteSeen`; still awaiting formal user ratification). Money fields carry units: `limit_price_dollars`, not `limit_price`. Sizing trace field is `sizing: SizingTrace` with `lam` aliased to `"lambda"` in JSON.
- [x] Strategy configs: `config/strategies/<id>.yaml`, loaded by `apacenye.config.load_strategy_config(id)` as a plain dict; validation is the worker's own `_initialize` (fails loudly on missing keys). No per-worker pydantic schema — keep required-key checks in `_initialize`.
- [x] Registration is **explicit in `cli.py::_serve`**: construct the worker with `orch.make_context()`, then `orch.register_worker(worker, cadence_s=...)` — that call also declares the TickScheduler cadence. No registry module, no discovery.
- [x] Emission APIs on the base class: `await self.emit_intent(intent)` (blocked outside START), `await self.emit_evaluation(ev)`, `await self.emit_cancel(intent_id, reason)`; heartbeats are emitted automatically by `on_tick` in every state except STOP.
- [x] CLI spelling confirmed: `apacenye ack --strategy <id> --gate paper`. DRY_RUN confirmed: full gate pipeline runs, approved orders are logged with `execution: {dry_run: true}`, zero fills/positions.
- [x] Reference worker is `src/apacenye/workers/w1_forecast.py`.
- [x] One-pagers live in `docs/strategies/` (`w1.md` is the template).
- Staleness config key the G4 gate reads: `staleness_s` in the strategy YAML (single window per strategy; every `*_ts` entry in `key_inputs` is checked against it).
