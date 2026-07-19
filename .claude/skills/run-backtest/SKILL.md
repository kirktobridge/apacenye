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

## Verify After Scaffolding

- [ ] Actual backtest entry point: CLI subcommand vs. module invocation, and its real flags.
- [ ] Capture directory layout and JSONL schema match `data/capture/YYYY-MM-DD/<channel>.jsonl.gz` and the line format above; confirm real channel names.
- [ ] Whether the harness auto-detects coverage gaps or step 2 must be done manually (and with what tool).
- [ ] Where calibration outputs (Brier/reliability) actually come from: harness-computed, a pandas notebook, or an `/api/evaluations` export.
- [ ] OD-14: do the Kalshi historical endpoints exist, at what granularity, under what terms?
- [ ] That replay drives the same `TickScheduler` tick objects (not a parallel scheduler implementation).
- [ ] Where backtest results get written, so the illustrative-only label is attached at the source.
