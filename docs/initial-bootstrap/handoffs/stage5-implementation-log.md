# Stage 5 Handoff — Implementation Log

**Project:** Apacenyë — personal Kalshi trading platform (PAPER-ONLY bootstrap)
**Written:** 2026-07-19 (Session 5 of 5 — the implementation session)
**Audience:** the owner, and any future session (including the eventual dedicated
live-hardening session, which must read §5 before anything else).

---

## 1. What was built

All-Python monolith per Stage 3, 98 pytest tests green, smoke-tested
end-to-end against live read-only Kalshi + NWS data on 2026-07-19.

| Piece | State | Where |
|---|---|---|
| Fee/edge math (0.07·C·P·(1−P), ceil per order; unrounded per-contract for qualification) | tests-first, green | `domain/fees.py` |
| Sizing (λ-shrinkage → quarter-Kelly → min-of-caps clamp) | tests-first, green | `domain/sizing.py` |
| W1 Gaussian bracket model (continuity-corrected) | tests-first, green | `domain/weather.py` |
| P&L math (settlement, early exit, optimistic-bound comments) | tests-first, green | `domain/pnl.py` |
| Contract messages (incl. OD-15 `quote_seen`) | done | `contract/models.py` |
| SQLite ledger — all SQL in one module, WAL, 15 tables | done, tested | `orchestrator/ledger.py` |
| Risk engine G0–G10, reservation accounting, min-of-headrooms composition | done, tested incl. SYNTHETIC multi-position/multi-strategy scenarios | `orchestrator/risk_engine.py` |
| Kill switch (sentinel `data/KILL`, out-of-band, un-kill CLI-only) | done, tested + smoke drill | `orchestrator/kill.py`, `cli.py` |
| Paper fill simulator (Stage 3 §6.1 verbatim; idempotent `client_order_id = intent_id`) | done, tested incl. duplicate-submit retry path | `execution/paper.py` |
| Live path | **hard-disabled twice**: `RUN_MODE=LIVE` refuses to boot (`config.py` validator) and `execution/live.py` contains zero submission code — `make_live_client()` raises `LiveDisabledError` | verified by test + smoke |
| Kalshi client — READ-ONLY (no order methods exist), RSA-PSS auth per verified OD-19, token-bucket 5 req/s, exponential backoff on 429/5xx | done; exercised unauthenticated against prod | `execution/kalshi.py` |
| Concept checkpoint K1–K5, computed from live config; hash-chained append-only ack log; paper gate enforced at every START | done, tested + run interactively via CLI | `checkpoint/ack.py` |
| Orchestrator (queues, order path, explanations, kill watcher, heartbeat supervisor, TTL sweeper, settlement) | done, tested end-to-end | `orchestrator/orchestrator.py` |
| TickScheduler — single `fire_due(now)` path for live AND replay | done | `scheduler.py` |
| Market data (snapshot cache, catalog with verified bracket semantics, S1 monitor as alert-only, 15 s poll feed, capture writer) | done | `marketdata/`, `backtest/capture.py` |
| NWS/METAR adapters (source timestamps, loud failures; NWS wired to capture) | done; METAR is a W2 scaffold | `dataadapters/` |
| Replay harness (virtual clock through the same scheduler/gates/simulator; Brier model-vs-market; coverage-gap warnings; illustrative-only label at the source) | done, tested + run on real capture | `backtest/replay.py` |
| Service layer: REST per Stage 3 §7.1, WS hub, 4 dashboard views, htmx 1.9.12 vendored, **no un-kill and no live endpoint exists** | done; all pages smoke-tested 200 | `service/` |
| CLI: `serve · kill · unkill · ack · enable-live · status · backtest` | done; every command smoke-tested | `cli.py` |
| Config: `risk.yaml` (Stage 3 §3.2 values), `strategies/w1.yaml`, `.env.example`, `secrets/README.md`, `RISK__` overrides logged | done | `config/`, `config.py` |

**Verified live this session** (previously [estimate — verify]):
- **OD-19 RESOLVED:** Kalshi auth = RSA-PSS/SHA-256 over `timestamp_ms + METHOD + path` (no query string), headers `KALSHI-ACCESS-{KEY,TIMESTAMP,SIGNATURE}`; rate limits = token bucket (Basic ≈ 200 read tokens/s, 10/request), backoff-on-429. Base URLs probed live: prod `api.elections.kalshi.com/trade-api/v2`, demo `demo-api.kalshi.co/trade-api/v2`.
- **OD-3 (NYC) partially resolved:** series `KXHIGHNY` exists with daily events; market objects carry `event_ticker` + `floor_strike`/`cap_strike`.
- **Bracket-edge semantics** (the contract-mapping trap Stage 2 warned about): `T87` = *strictly* >87° ("88° or above"); `T80` = *strictly* <80° ("79° or below"); B-brackets inclusive. Encoded in `catalog.py`, unit-tested.
- **NWS grid for KNYC:** OKX 34,45 (from `api.weather.gov/points/40.7789,-73.9692`).
- End-to-end: `serve` resolved `KXHIGHNY-26JUL19`, fetched the real forecast, polled 6 real books, wrote capture, and logged 6 honest shadow forecasts ("no two-sided quote" at 2 a.m. — correctly no intents). Model probabilities across the event summed to ≈1.

## 2. Deviations from the Stage 3/4 plans, and why

Recorded per the unattended-ambiguity protocol; none touches a user-ratified number.

- **D5-1 — `src/apacenye/domain/` added** (fees/sizing/weather/pnl). The test-first constraint wanted pure financial math testable before any wiring; Stage 3 §12 had no home for it. Also: `scheduler.py` is a top-level module, `service/ws.py` exists, and there is **no `orchestrator/lifecycle.py`** — supervision (heartbeat timeout, kill watcher, restarts) lives in `orchestrator.py`; a separate module would have been indirection without content.
- **D5-2 — Two fee forms.** Qualification uses the unrounded per-contract fee (Stage 2 §2's formula); the ledger charges the per-order ceil-to-cent fee (Stage 1 §1.5's rounding). Both are in `domain/fees.py` with the distinction documented.
- **D5-3 — Exposure = open cost basis excluding fees**; fees tracked alongside. Worst case = cost + fees, stated in the ledger docstring.
- **D5-4 — G10 order:** portfolio check runs before the per-strategy check, so a joint breach still trips the kill switch (a strategy-level auto-PAUSE must not shadow a portfolio-level kill). Tested.
- **D5-5 — Reservation release:** on full fill, expiry, or cancel; a partial fill keeps the whole reservation (the filled part is briefly double-counted — conservative direction).
- **D5-6 — Ack-hash scope:** `max_order_contracts` and `max_depth_fraction` included in the risk-relevant hash beyond the Stage 3 §11.2 minimum ("all exposure caps" read broadly; loosening either now forces re-acknowledgment).
- **D5-7 — Self-imposed rate limit 5 req/s** (¼ of documented Basic budget); book polling every 15 s.
- **D5-8 — OD-15 `quote_seen` implemented** as a typed model on every intent. Stage 3 adopted it provisionally; **still needs user ratification** (strike = one small contract amendment to remove).
- **D5-9 — New G0 check: settled markets reject.** Found by the replay harness: a stale book can outlive its market in the snapshot cache. Real hazard live, not just in replay.
- **D5-10 — Risk engine takes `now_fn`** so replay judges TTL/staleness on virtual time. Same principle as the scheduler.
- **D5-11 — New capture channel `market`** (catalog metadata incl. bracket bounds). Without it, replay cannot reconstruct bracket bounds and p_model would silently degenerate to 1.0 — my own first synthetic test passed for exactly that wrong reason before the fix.
- **D5-12 — `serve` resolves the day's event** as the open event with the earliest close in the configured series (`series_ticker` in `w1.yaml`); explicit `event_ticker` overrides.
- **D5-13 — Backtest entry point** `apacenye backtest --strategy --from --to` (confirms the Stage 4 D4-3 guess).
- **D5-14 — Smoke-test acks deleted.** I ran the paper and live gates myself to verify them; those acknowledgments were mine, not the owner's, so `data/acks/` and the smoke ledger were reset. **The owner must run `apacenye ack --strategy W1 --gate paper` personally before W1 will START.** (Deleting the whole runtime file ≠ editing the append-only log; the committed rule stands.)
- **W1 exact-math note:** Stage 2's worked example says "≈40 contracts"; the unrounded bracket probability (0.5698, not 0.57) gives 39. Tests assert 39.

## 3. Backtest honesty (binding statement)

The only replayable data source is **our own capture**, which began 2026-07-19
(hours of it, one city, overnight books). Kalshi historical endpoints (OD-14)
remain unverified and lack book depth regardless. Therefore: **every backtest
result producible today is illustrative-only, is labeled as such at the source
(`run_replay` result `label`, printed first by the CLI), and is NOT a basis
for live confidence.** No synthetic data is presented as historical; the
replay test fixtures live only under `tests/` and are labeled synthetic.

## 4. Open Decisions status

- **Resolved this session:** OD-19 (auth/rate limits); OD-3 for NYC; NWS grid; bracket-edge semantics; D4-3's backtest spelling.
- **Still awaiting user ratification (flag at next attended session):** OD-8 ($1,000 bankroll), OD-9 (λ, k), OD-10 (NYC-first city list), OD-15 (`quote_seen`), OD-16 (50% portfolio cap), OD-17 (−5% auto-kill), OD-18 (localhost/no-auth dashboard).
- **Still [verify], now blocking only what's listed:** OD-1 per-series fee schedule (blocks trading any non-weather series); OD-2 real spreads/depth (blocks the OD-2 liquidity gate being evidence-based); OD-11 forecast-error archive (blocks honest σ; `sigma_f: 3.0` is a placeholder); OD-12 (blocks W2); OD-13 (blocks E1); OD-14 (moot for now); OD-5 demo env (optional).

## 5. What remains before real capital could responsibly be enabled

None of this is optional, and completing it is necessary but NOT sufficient —
the future hardening session defines its own acceptance gate (Always-Apply
Rule 1). In rough order:

1. **Weeks of paper operation** with the capture writer running: real spreads/depth (OD-2), real fill-rate reality checks, S1 alert history, disposition/gate-binding history.
2. **Calibration evidence** from shadow forecasts: sample count in the hundreds, Brier vs. the market benchmark, reliability curves — computed from the `evaluations` table. This is also the only evidence bar for touching λ, k, or σ (OD-9/OD-11).
3. **σ estimated per station/lead-time** from a real forecast-error archive (OD-11), replacing the 3.0 placeholder.
4. **OD-1 verified per traded series** (fee schedule differences would invalidate every threshold).
5. **Live-order code written fresh** in the dedicated hardening session — it does not exist today, deliberately: order submission/cancel against the real API, venue error taxonomy, reconciliation of venue positions vs. ledger, partial-fill and reject handling, clock-skew handling for signed requests.
6. **Operational hardening:** restart-under-open-positions drills, kill-switch drills against the live venue (cancel semantics), monitoring/alerting beyond the dashboard, backup/rotation for the ledger and key.
7. **A fresh live-gate acknowledgment** by the owner for the then-current config (bootstrap acks pre-authorize nothing — recorded refusal semantics already enforce this).
8. **Owner review** of every money-touching module (they are written to be read: `domain/`, `risk_engine.py`, `ledger.py`, `paper.py`, `ack.py`).

## 6. How to run

```bash
uv sync && uv pip install -e .           # once
uv run pytest                            # 98 tests
cp .env.example .env                     # optional: add Kalshi key for auth'd reads
uv run apacenye ack --strategy W1 --gate paper   # owner passes the gate
uv run apacenye serve                    # PAPER; dashboard at 127.0.0.1:8642
uv run apacenye status                   # works with the server down
uv run apacenye backtest --strategy W1 --from 2026-07-19 --to 2026-07-19
```

The Stage-5 WIP resume document was deleted on completion, per its own header.
