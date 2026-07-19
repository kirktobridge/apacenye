# CLAUDE.md — Apacenyë

Personal Kalshi trading platform. **PAPER-ONLY bootstrap.** The Always-Apply Rules at the bottom of this file override everything else here and in any session prompt.

Authoritative design docs: `docs/initial-bootstrap/handoffs/` — stage1 (fee math, market choice), stage2 (worker contract, sizing, risk defaults), stage3 (architecture, kill switch, checkpoints, secrets). When this file and a handoff disagree, flag the conflict; do not silently pick one.

## Overview

Kalshi trades binary event contracts: price ≈ market-implied probability. A strategy's whole job is to estimate the true probability better than the market and trade only when the gap survives all costs. User-ratified qualification rule:

```
net_edge = p_model − executable_price − fee − $0.01 slippage ≥ 0.04
fee per executed leg = 0.07 × C × P × (1−P), rounded up per order
```

Architecture is **propose-then-approve**: strategy workers (asyncio tasks in one process) evaluate on `TickScheduler` ticks and emit `OrderIntent` *proposals*; the orchestrator's risk engine runs gate pipeline G0–G10 (kill switch, run mode, lifecycle, staleness, liquidity/event/strategy/portfolio caps, daily-loss stops), then approves, resizes down, or rejects. Only the orchestrator can reach the execution client — in PAPER mode an internal fill simulator that fills at executable prices (ask/bid, never mid). The SQLite ledger is the single source of truth; workers are restart-safe and hold no authoritative position state.

Sizing: shrink model probability toward market (λ=0.5), quarter-Kelly (k=0.25), then hard caps — caps always dominate Kelly. Every evaluation logs a shadow forecast (traded or not) for calibration; every intent gets an `ExplanationRecord`.

Category: weather-first. Reference implementation: **W1-v0**, a Gaussian around the NWS forecast high — chosen to prove the architecture, not to be the best bet.

## Tech stack

All-Python monolith. One process, one lockfile, no build step, no npm.

- Python 3.12+, managed with `uv`, single `pyproject.toml`
- Pydantic v2 — contract messages and config models
- FastAPI + uvicorn — REST + WebSocket + dashboard, same event loop as orchestrator
- Jinja2 + **vendored, version-pinned** htmx — zero hand-written JavaScript, no CDN
- SQLite (WAL) via stdlib `sqlite3` — explicit SQL in the ledger module, **no ORM**
- `httpx` / `websockets` — Kalshi (read-only), NWS, METAR
- numpy / scipy / pandas — strategy math and calibration analysis
- In-house `TickScheduler` (no APScheduler) — one emission path (`fire_due(now)`) drives live serving and replay identically
- `cryptography` — Kalshi request signing (OD-19 VERIFIED 2026-07-18: RSA-PSS/SHA-256 over `timestamp_ms + METHOD + path`, headers `KALSHI-ACCESS-{KEY,TIMESTAMP,SIGNATURE}`)
- pytest — financial logic tested first (98 tests at Stage 5 close)
- Entry points: `apacenye serve` and the out-of-band CLI (`apacenye kill | unkill | ack | enable-live | status | backtest`)

## Directory layout (as built — Stage 5)

Deviations from the Stage 3 §12 plan are marked ★ and justified in
`docs/initial-bootstrap/handoffs/stage5-implementation-log.md`.

```
apacenye/
├── pyproject.toml  uv.lock  .env.example  .gitignore  CLAUDE.md
├── config/                 # committed: risk.yaml, strategies/w1.yaml
├── secrets/                # gitignored; README.md only committed file
├── data/                   # gitignored runtime: apacenye.sqlite, capture/, acks/, KILL
├── docs/strategies/        # per-strategy one-pagers (w1.md)
├── src/apacenye/
│   ├── contract/           # models.py — THE interface module: OrderIntent (incl. OD-15
│   │                       #   quote_seen), CancelIntent, Heartbeat, Evaluation, Disposition,
│   │                       #   Fill, MarketSnapshot, Tick, ExplanationRecord, enums
│   ├── domain/           ★ # pure financial math, tests-first: fees.py, sizing.py,
│   │                       #   weather.py (W1 Gaussian), pnl.py
│   ├── orchestrator/       # risk_engine.py (G0–G10 + reservations), ledger.py (all SQL),
│   │                       #   kill.py (sentinel), orchestrator.py ★ (wiring + supervision;
│   │                       #   there is no separate lifecycle.py)
│   ├── execution/          # paper.py (fill simulator §6.1), kalshi.py (READ-ONLY client,
│   │                       #   no order methods), live.py (LiveDisabledError stub)
│   ├── marketdata/         # feed.py (poll loop), snapshots.py (cache), catalog.py
│   │                       #   (ticker→event + bracket bounds; strict-tail semantics),
│   │                       #   monitors.py (S1)
│   ├── workers/            # base.py (lifecycle ABC + WorkerContext), w1_forecast.py
│   ├── dataadapters/       # nws.py (+ capture hook), metar.py (W2 scaffold)
│   ├── service/            # api.py (REST + WS + pages), ws.py ★ (WsHub), templates/,
│   │                       #   static/htmx.min.js (1.9.12, vendored)
│   ├── checkpoint/         # ack.py — K1–K5 gates, hash-chained AckLog, verify
│   ├── backtest/           # capture.py (writer + read_day), replay.py (virtual-clock harness)
│   ├── scheduler.py      ★ # TickScheduler (top-level, not a subpackage)
│   ├── config.py           # AppSettings (.env, SecretStr, LIVE boot refusal), RiskConfig
│   └── cli.py              # serve | kill | unkill | ack | enable-live | status | backtest
└── tests/                  # 98 tests; financial logic written tests-first
```

## Current state (Stage 5 complete, 2026-07-19)

**Implemented and smoke-tested end-to-end** against live read-only data: `serve`
boots in PAPER, resolves today's KXHIGHNY event, pulls the NWS forecast
(grid OKX 34,45 for KNYC — verified), polls real order books, logs shadow
forecasts, and serves the dashboard at `127.0.0.1:8642`. `RUN_MODE=LIVE`
refuses to boot; `enable-live` runs the full K1–K5 gate and terminates at the
recorded refusal; kill/unkill work with the server down; replay backtesting
runs off `data/capture/` with the illustrative-only label attached at the
source. Run: `uv sync && uv pip install -e . && uv run apacenye serve`.
Tests: `uv run pytest`. Before a strategy will START, the owner must pass
`apacenye ack --strategy W1 --gate paper` (smoke-test acks were deliberately
deleted — the acknowledgment must be the owner's own).

**Not yet done:** σ from the forecast-error archive (OD-11 — `sigma_f: 3.0`
is a placeholder), W2/E1/S1-as-strategy (design-complete, build-blocked on
their ODs), METAR capture wiring, and everything on the pre-live list in
`stage5-implementation-log.md`.

## Coding standards

- **Money-touching logic is Python only** and written to be *read*: the owner reviews all financial code personally (strong data-science background, Python-only fluency). Boring and explicit beats clever. Plain-language docstrings on every money-touching function.
- Type-annotate everything. Contract messages live in `src/apacenye/contract/` — the one module workers and orchestrator both import; change it only with an explicit, flagged contract amendment.
- **Units explicit in names**: `price_dollars` vs `price_cents`; never a bare `price`. The 100-contract per-order cap is the unit-bug backstop — never remove it.
- All timestamps UTC ISO-8601. Every external datum carries its source timestamp into `key_inputs` so staleness is enforceable.
- Workers consume platform ticks; they never own wall-clock scheduling, never import execution code, and never hold a Kalshi client.
- **Tests first** for financial logic: fee math, sizing, gates, ledger, fill simulator, idempotency (`client_order_id = intent_id`).
- SQL appears only in the ledger module, as explicit DDL + statements.
- Dashboard is Jinja2 + htmx attributes only; adding hand-written JavaScript requires explicit user sign-off.
- Tunables in committed `config/*.yaml` (env override prefix `RISK__`, overrides logged at startup); secrets only in `.env`.
- Paper P&L code must state in comments that it is an optimistic bound.

## Always-apply rules (safety — exempt from brevity; never trim or override)

1. **PAPER-ONLY, live hard-disabled twice.** `RUN_MODE=LIVE` must refuse to boot. `execution/live.py` contains only a stub raising `LiveDisabledError` — **no live order-submission code may be written in this bootstrap**, in any session, for any reason. Enabling live requires a future dedicated hardening session with its own acceptance gate; nothing in this repo may shortcut that.
2. **Workers never place orders.** Their only money-adjacent output is an `OrderIntent` proposal. There must be no code path from any worker to the order API; the execution client is constructed by and reachable only from the orchestrator. The orchestrator may approve, resize *down*, defer, or reject any intent.
3. **Secrets via `.env` only** (plus key files under gitignored `secrets/`). No credential, key, or token ever appears in Python source, YAML, logs, dashboards, explanation objects, or commits. Config serialization redacts secret fields. `.gitignore` must cover `.env`, `secrets/`, `*.pem`, `*.key`, `data/`, `*.sqlite*` before any such file exists.
4. **Numeric risk guardrails** (defaults in `config/risk.yaml`; conservative pending live data; do not loosen without user ratification):
   - Paper bankroll: **$1,000** notional (OD-8)
   - Per-event exposure: **≤ 5%** of bankroll — *all brackets of one settlement event are one exposure* (OD-7)
   - Per-strategy exposure: **≤ 20%**; portfolio total: **≤ 50%** (OD-16, always dominates)
   - Order size: **≤ 25%** of visible top-of-book depth and **≤ 100 contracts** per order
   - Daily loss: strategy **−2% ⇒ auto-PAUSE** (human un-pause); portfolio **−5% ⇒ kill switch trips** (OD-17)
   - Qualification: net edge **≥ 4 points** after fee + $0.01/leg slippage, at executable prices (never mid, never maker fills)
   - Sizing: **λ = 0.5** shrinkage, **k = 0.25** Kelly — changed only on shadow-forecast calibration evidence (OD-9)
5. **Kill switch is out-of-band.** `data/KILL` sentinel file is the kill state; `apacenye kill` works with the server down. Halt = reject new opens, cancel resting, pause workers, leave positions in place. **Un-kill is CLI-only** (`apacenye unkill`, typed confirmation); no HTTP un-kill endpoint may exist.
6. **Backtests are illustrative-only** until several weeks of our own order-book capture exist; they may never be cited as evidence for live enablement, and synthetic data is never presented as historical.
7. **Concept checkpoints gate risk.** A strategy cannot `START` in paper without a `PASSED` paper acknowledgment for its current risk-relevant config hash; live requests run the full live gate and, in this bootstrap, always terminate at the hard-disable wall with the refusal recorded. The ack log (`data/acks/acknowledgments.jsonl`) is append-only and hash-chained — never rewrite it.
8. **Ambiguity protocol.** If a convention or decision is ambiguous: ask the user if present; if unattended, choose the conservative option and record it (with reasoning) in the current stage's handoff doc — never invent silently.
