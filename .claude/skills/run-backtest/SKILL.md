---
name: run-backtest
description: Run a replay backtest of an Apacenyë strategy against recorded market data, with the mandatory honesty rules about data provenance and what results do and don't mean. Use whenever evaluating a strategy's historical or simulated performance.
---

# Run a Backtest

The harness is **replay, not simulation-from-scratch** (`stage3-architecture.md` §9): recorded snapshots and data-adapter records are replayed through the *same* `TickScheduler` interface, unmodified worker code, the same G0–G10 gate pipeline, and the paper fill simulator, on a virtual clock. Workers cannot tell replay from live.

## Steps

1. **Pick the data source and say so in the results.** In order of honesty:
   - **Our own capture** (`data/capture/YYYY-MM-DD/<channel>.jsonl.gz`) — the *only* source with order-book depth, hence the only source honest about executability. Line format: `{"ts": <UTC ISO-8601>, "type": "book"|"trade"|"settlement"|"nws_forecast"|"metar", "ticker"|"station": …, "payload": {…}}`.
   - **Kalshi historical endpoints** (candlesticks/trades — OD-14, unverified) — **no order-book depth exists**, so fills cannot be validated: results are **illustrative-only** and must be labeled as such in every place they appear.
   - Weather archives (IEM forecasts/METAR + NWS climate reports, OD-11) are *model inputs* (e.g., σ estimation), not trade-simulation data — keep the two roles separate.
2. **Check coverage before running**: the replay window has capture data for every channel the strategy consumes (books for its tickers, its external data channels, settlements). A silent gap shows up as fake staleness or missed settlements, not an error.
3. **Run the replay harness** (Stage 5's entry point — expected shape: `apacenye backtest --strategy <id> --from <date> --to <date>` or a `backtest/replay.py` invocation) with the strategy's real config. Never fork strategy logic "for the backtest" — if the worker needs modification to run in replay, that's a contract bug to fix, not to work around.
4. **Read calibration before P&L.** The primary outputs are the shadow-forecast records: Brier score, reliability curve, and sample count for `p_model` vs. outcomes. A well-calibrated model with thin P&L is progress; good P&L on 12 samples is noise.
5. **Read P&L with its bias stated**: paper fills are an **optimistic bound** — no queue competition, no market impact, no partial-fill adverse selection, and replayed books may themselves have been stale.
6. **Report with the mandatory limitations attached** (verbatim policy, binding per Stage 3 §9): until several weeks of our own capture exist, **all backtest results are illustrative-only and are NOT a basis for live confidence** — they may not be cited in any live-enablement argument. No synthetic data may ever be presented as historical. Every report/dashboard/summary showing backtest numbers must repeat this.

## Verified After Scaffolding (Stage 5, 2026-07-19)

- [x] Entry point: `apacenye backtest --strategy <id> --from YYYY-MM-DD --to YYYY-MM-DD` (the Stage 4 guess turned out exact). Module API: `apacenye.backtest.replay.run_replay(...)`.
- [x] Capture layout/schema confirmed: `data/capture/YYYY-MM-DD/<channel>.jsonl.gz`, line `{"ts", "type", "ticker"|"station", "payload"}`. Real channels: `book`, `trade`, `settlement`, `nws_forecast`, `metar`, **plus `market`** (catalog metadata: ticker→event + bracket bounds). The `market` channel is REQUIRED for honest replay — without it bracket bounds are unknown and the harness flags the strategy's probabilities as unusable.
- [x] Coverage gaps are auto-detected: `run_replay` returns them in `warnings` and the CLI prints them. No manual pre-check needed, but read the warnings.
- [x] Calibration is harness-computed: `brier_model` vs `brier_market` (the market-mid benchmark) in the result dict, from shadow evaluations joined to captured settlements. Deeper analysis (reliability curves) = pandas over the `evaluations` table or `GET /api/evaluations`.
- [ ] OD-14 (Kalshi historical candlestick/trade endpoints) remains UNVERIFIED — irrelevant to the harness, which replays only our own capture; any future use of those endpoints is illustrative-only by construction (no book depth).
- [x] Replay drives the same `TickScheduler` via `fire_due(virtual_now)` — the identical emission path live serving uses; the risk engine takes an injected `now_fn` so TTL/staleness gates run on replay time.
- [x] Results: `run_replay` returns a dict whose `label` field IS the illustrative-only statement (attached at the source); the CLI prints it first. Nothing is persisted — rerun to reproduce.
