# Stage 5 — WORK IN PROGRESS resume document

**Status: PAUSED mid-implementation by user request (session limit), 2026-07-18.**
**Delete this file once Stage 5 completes and `stage5-implementation-log.md` exists.**

A resuming session should: read this file, then continue at "NEXT STEP" below. All
prior handoffs were read and confirmed; the confirmation paragraph requirement
(STAGE5.md line 7) was satisfied earlier this session.

## Environment facts (needed to resume)

- `uv` installed this session at `~/.local/bin/uv` — **every shell needs
  `export PATH="$HOME/.local/bin:$PATH"`**.
- `uv sync` did NOT install the project itself; fixed with `uv pip install -e .`
  (already done; venv is `.venv/`, Python 3.12.3). Tests: `uv run pytest tests/ -q`.
- **82/82 tests passing** at pause time.
- Network access WORKS. Verified live this session:
  - **OD-19 RESOLVED** (docs.kalshi.com, 2026-07-18): auth headers
    `KALSHI-ACCESS-KEY` / `KALSHI-ACCESS-TIMESTAMP` (ms) / `KALSHI-ACCESS-SIGNATURE`;
    sign `timestamp_ms + METHOD + path` (path WITHOUT query string); RSA-PSS
    SHA-256, MGF1-SHA256, salt = digest length, base64. Rate limits: token
    bucket, Basic tier ≈200 read tokens/s, most requests cost 10 (≈20 req/s);
    exponential backoff on 429, no penalty cooldown.
  - Base URLs probed live and working: prod `https://api.elections.kalshi.com/trade-api/v2`,
    demo `https://demo-api.kalshi.co/trade-api/v2` (`/exchange/status` returned 200 on both).
  - htmx vendoring: `https://unpkg.com/htmx.org@1.9.12/dist/htmx.min.js` returns 200
    (not yet downloaded — do this when building the service layer).

## Built and tested (all committed in this WIP commit)

| Module | State |
|---|---|
| `pyproject.toml` | deps: pydantic(+settings), fastapi, uvicorn, jinja2, httpx, websockets, numpy/scipy/pandas, cryptography, pyyaml; dev: pytest(+asyncio, `asyncio_mode=auto`) |
| `src/apacenye/domain/` fees, sizing, weather, pnl | DONE — tests-first (test_fees/test_sizing/test_weather_model/test_pnl, 27 tests) |
| `src/apacenye/contract/` models + `__init__` re-exports | DONE — includes OD-15 `quote_seen` (provisionally adopted), `SizingTrace.lam` aliased to `"lambda"` |
| `src/apacenye/config.py` | DONE — `AppSettings` (.env, SecretStr redaction, **LIVE refuses to boot in a field_validator** = wall #1), `RiskConfig` + `load_risk_config()` with `RISK__` env overrides logged |
| `src/apacenye/orchestrator/ledger.py` | DONE — full DDL (markets/events/intents/cancels/dispositions/orders/fills/positions/realizations/cash_ledger/evaluations/heartbeats/explanations/config_versions/kill_events), 9 tests. Exposure = open cost basis EXCLUDING fees; equity = initial bankroll + realized; `day_pnl_dollars(strategy, marks)` = realized today + unrealized-vs-entry at supplied mid marks |
| `src/apacenye/orchestrator/kill.py` | DONE — sentinel `data/KILL`, atomic write, corrupt-file-still-killed, `clear()` CLI-only |
| `src/apacenye/orchestrator/risk_engine.py` | DONE — G0–G10, 19 tests incl. synthetic multi-position/multi-strategy scenarios. In-memory reservation accounting (`release_reservation(intent_id)` on fill/expiry/cancel). **G10 checks portfolio (kill-trip) BEFORE strategy (auto-pause)** — deliberate, tested. `risk_summary()` for /api/risk. `evaluate(intent, human_initiated=False)`; human reduce/close bypasses G1/G3 |
| `src/apacenye/execution/paper.py` | DONE — §6.1 verbatim; 11 tests. First-touch fills at opposing quote, resting fills at OUR limit on cross, depth-capped 25%, idempotent duplicate submit, expire/cancel. NOT yet wired to ledger (orchestrator's job) |
| `src/apacenye/execution/live.py` | DONE — `LiveDisabledError` + `make_live_client()` raises; documents the 4 preconditions for a future hardening session (wall #2) |
| `src/apacenye/execution/kalshi.py` | DONE (untested against live) — READ-ONLY (no order methods, comment explains why), RSA-PSS signing per OD-19, token-bucket 5 req/s, backoff on 429/5xx, `get_snapshot()` builds MarketSnapshot from orderbook (yes levels = YES bids; no levels mirror to YES asks at 100−c) |
| `src/apacenye/checkpoint/ack.py` | DONE — 8 tests. K1–K5 computed from config, paper gate = K1,K2,K4,K5; live gate = all 5 + typed `ENABLE LIVE <id> CONFIG <hash>` line then ALWAYS records `outcome_note: "live refused: bootstrap hard-disable"`. Hash-chained `AckLog` (O_APPEND, verify(), `has_valid_paper_ack`). `RISK_RELEVANT_FIELDS` = bankroll, 3 exposure pcts, max_order_contracts, max_depth_fraction, k, λ, min_net_edge |
| `src/apacenye/scheduler.py` | DONE — `TickScheduler.fire_due(now)` is the single emission path; `run()` polls wall clock; replay calls `fire_due(virtual_now)` |
| `src/apacenye/marketdata/` snapshots, catalog, monitors, feed | DONE — SnapshotCache (listeners), MarketCatalog (`add_from_kalshi_market`, floor/cap_strike + subtitle parse), S1 BracketCoherenceMonitor (alerts only), MarketDataService (poll loop → cache+capture, settlement detection → callback, S1 per event) |
| `src/apacenye/dataadapters/` nws, metar | DONE — source timestamps carried; fail loudly. NWS gridpoints forecast (daytime period high); METAR latest obs (W2 scaffold) |
| `src/apacenye/backtest/capture.py` | DONE — `data/capture/YYYY-MM-DD/<channel>.jsonl.gz`, gzip append members, channels book/trade/settlement/nws_forecast/metar, `read_day()` for replay |
| `src/apacenye/workers/base.py` | DONE — lifecycle ABC (INIT/START/PAUSE/STOP/update_config), `WorkerContext` (emit, get_snapshot, get_positions, get_bankroll_dollars, risk, list_event_brackets), heartbeats every tick, PAUSE blocks `emit_intent` |
| `src/apacenye/workers/w1_forecast.py` | DONE — 8 tests. Gaussian brackets, both-side (YES/NO) evaluation, shadow Evaluation on EVERY bracket, self-enforced event budget, staleness → no intents + cancel outstanding, capital-at-risk documented in module docstring. Note: exact p=0.5698 → 39 contracts (Stage 2's rounded example said 40) |
| `src/apacenye/service/ws.py` | DONE — WsHub broadcast {channel, ts, payload}, signals ring buffer |

## NEXT STEP (was about to write when paused)

1. **`src/apacenye/orchestrator/orchestrator.py`** — the wiring. Planned design:
   - One `asyncio.Queue` for worker messages; dispatch by isinstance
     (OrderIntent/CancelIntent/Evaluation/Heartbeat).
   - Intent flow: `ledger.record_intent` → `risk_engine.evaluate` →
     `ledger.record_disposition` + WS `intents` → if approved & PAPER:
     `paper.submit(intent, final_size)` → fills → `ledger.record_fill` +
     `risk.release_reservation` (release on FULL fill/expiry/cancel; partial
     fill keeps reservation — conservative double-count, comment it) → WS
     `fills` → ExplanationRecord assembled (intent fields + disposition +
     risk_context from `risk_summary()` + execution) → `ledger.record_explanation`
     + WS `signals`. DRY_RUN: log approved order, no simulator call.
   - Lifecycle: `start_strategy` gated on (a) not killed, (b)
     `AckLog.has_valid_paper_ack(id, risk_relevant_config_hash(risk))` —
     refuse otherwise; `pause_strategy(id, reason)` (used by risk engine
     callback); resume; stop.
   - Background tasks: kill watcher (2 s poll → pause all + `paper.cancel_all`),
     heartbeat supervisor (age > `heartbeat_timeout_s` ⇒ pause + alert),
     resting-order TTL sweeper (`paper.expire_stale` + release reservations),
     snapshot listener wired at construction (`cache.add_listener` →
     `paper.on_snapshot` → record fills).
   - `on_settlement(ticker, side)` → `ledger.settle_market` + WS.
   - Worker registration: construct WorkerContext with queue.put, cache.get,
     ledger.open_positions, ledger.equity_dollars, risk config,
     catalog.brackets_of_event; register cadence with TickScheduler.
2. **Service layer** `service/api.py` + templates + vendored htmx:
   endpoints per Stage 3 §7.1 (state/positions/strategies/{id}/pause|resume|config,
   intents?since, explanations/{id}, evaluations?strategy, risk, acks,
   POST /api/kill — **NO unkill endpoint**). Bearer token iff non-localhost
   (settings.validate_dashboard_binding). 4 Jinja2 views + htmx polling
   fragments. Download htmx 1.9.12 to `service/static/htmx.min.js`.
3. **`backtest/replay.py`** — load capture via `CaptureWriter.read_day`,
   merge-sort by ts, feed book records into SnapshotCache (+ nws_forecast
   records into a replay adapter for W1), advance virtual clock, call
   `scheduler.fire_due(virtual_now)`, orchestrator in PAPER mode with a
   replay ledger (tmp sqlite), settlements from capture. Output: evaluations
   + P&L labeled **illustrative-only** at the source.
4. **`cli.py`** — argparse: `serve` (uvicorn + orchestrator tasks),
   `kill "reason"` / `unkill` (typed `RESUME TRADING`), `ack --strategy --gate
   [--verify-log]`, `enable-live --strategy`, `status` (reads KILL + sqlite
   read-only, works server-down), `backtest --strategy --from --to`.
5. **Config files**: `config/risk.yaml` (Stage 3 §3.2 verbatim numbers),
   `config/strategies/w1.yaml` (station KNYC, grid OKX/33,37 — VERIFY grid
   via api.weather.gov/points/40.783,-73.967 before first run; sigma_f 3.0,
   staleness_s 43200, cadence_s 600, event_ticker per current listing,
   forecast_refresh_s 1800, intent_ttl_s 600), `.env.example` (Stage 3 §10
   verbatim), `secrets/README.md`. **.gitignore already covers everything —
   committed before any secret exists (D4-1 satisfied).**
6. **Smoke test**: `uv run apacenye status`, serve boots in PAPER,
   RUN_MODE=LIVE refuses, kill/unkill round-trip, ack paper gate interactive.
7. **Reconcile** CLAUDE.md (directory layout — note NEW `src/apacenye/domain/`
   + `src/apacenye/scheduler.py` + `src/apacenye/service/ws.py` deviations
   from Stage 3 §12; "Current state" rewrite; entry-point spellings) and all
   four skills' "Verify After Scaffolding" checklists (see
   stage4-conventions-summary.md §3 master list).
8. **Write `stage5-implementation-log.md`** (required output): what was
   built, deviations from Stage 3/4 + why, what remains before live could
   responsibly be enabled, backtest honesty statement (no capture data
   exists yet ⇒ any backtest run is illustrative-only), OD status table.
   Then delete THIS file and commit.

## Decisions made unattended so far (record in the final log; flag, don't silently change)

- **D5-1**: Pure financial math extracted to `src/apacenye/domain/` (fees,
  sizing, weather, pnl) — deviation from Stage 3 §12 layout, made to satisfy
  the tests-first constraint cleanly. Reconcile in CLAUDE.md.
- **D5-2**: Qualification uses UNROUNDED per-contract fee
  (`per_contract_fee_dollars`); order accounting uses per-order
  ceil-to-cent (`order_fee_dollars`) — matches Stage 1 §1.5 formula split.
- **D5-3**: Exposure = cost basis excluding fees (fees tracked separately;
  worst case = cost + fees, stated in ledger docstring).
- **D5-4**: G10 portfolio check ordered BEFORE per-strategy check so a joint
  breach still trips the kill switch (tested).
- **D5-5**: Reservation released on full fill/expiry/cancel; kept on partial
  fill (conservative double-count of the filled part until terminal state).
- **D5-6**: `RISK_RELEVANT_FIELDS` for the ack hash includes
  max_order_contracts + max_depth_fraction beyond the Stage 3 §11.2 minimum
  (reading "all exposure caps" broadly — conservative direction).
- **D5-7**: Kalshi client rate self-limit 5 req/s (¼ of documented Basic
  budget); MarketDataService polls books every 15 s.
- **D5-8**: OD-15 `quote_seen` implemented as typed model on every intent
  (was only "provisionally adopted" — still needs user ratification).
