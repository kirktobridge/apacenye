# Stage 3 Handoff — Platform Architecture

**Project:** Apacenyë — personal Kalshi trading platform (PAPER-ONLY bootstrap)
**Written:** 2026-07-18 (Session 3 of 5)
**Audience:** Session 4 (Conventions & Skills) and Session 5 (Implementation), each starting with zero memory. This document must be self-sufficient, but assumes both also read `stage1-foundations.md` (fee math), `stage2-strategies.md` (strategy contract, risk defaults), and `ABOUT_ME.md` (Python-only review fluency, paper-only, plain-language constraint).

**Data honesty note (inherited):** No live Kalshi access this session. Everything marked **[verify]** is unconfirmed; the architecture is deliberately structured so no component *depends* on an unverified figure being right.

---

## 0. Contract confirmation (Stage 2 §4, confirmed before designing around it)

Stage 3 confirms the strategy-worker contract as follows, and everything below is designed against exactly this:

1. **Propose-then-approve.** Workers never place orders — their only money-adjacent output is an **OrderIntent** (a proposal). The orchestrator may approve, resize *down*, defer, or reject any intent; workers must remain correct if every intent is rejected or expires unfilled.
2. **Five lifecycle states:** `INIT` (load config/calibration, validate data access, no intents), `START` (evaluate + emit), `PAUSE` (stop emitting immediately, stay warm, keep consuming data), `STOP` (cancel outstanding intents, persist, exit), `UPDATE_CONFIG` (hot-apply or reject with reason). Workers are restart-safe and hold no authoritative position state — the orchestrator's ledger is truth.
3. **Worker inputs:** market snapshots (bid/ask/top-of-book depth/last/timestamps), orchestrator-pushed own-positions and fills, versioned config, platform-issued clock ticks (workers never own wall-clock scheduling), external data via per-strategy adapters with timestamps carried into `key_inputs`.
4. **Worker outputs:** **(a)** OrderIntent with every field mandatory — `intent_id, strategy_id, ts, market_ticker, side, action, limit_price, size_contracts, ttl_seconds, model_probability, market_implied_probability, net_edge, confidence, key_inputs, sizing, rationale`; **(b)** CancelIntent; **(c)** heartbeats (silence ⇒ orchestrator pauses the strategy); **(d)** evaluation records ("shadow forecasts") on *every* evaluation, traded or not — the calibration dataset must not be dropped.
5. **The five explanation fields** (`model_probability`, `market_implied_probability`, `net_edge`, `confidence`, `key_inputs`) are non-negotiable on every intent and are the raw material for this stage's explanation objects (§8).
6. **Risk defaults (Stage 2 §5)** to be orchestrator-enforced: $1,000 paper bankroll; 5%/event; 20%/strategy; ≤25% of visible top-of-book depth; 100 contracts/order hard cap; −2%/day per strategy ⇒ auto-PAUSE; staleness rules; kill switch is orchestrator-owned.

**Stage 3's mechanical bindings** (Stage 2 explicitly deferred these to this session): contract messages are **Pydantic models**; delivery is **in-process `asyncio.Queue`s** (§2); ticks come from a platform `TickScheduler` that the backtest harness can drive identically (§9). One **additive field amendment** is proposed, not silently applied: `quote_seen` (§8, OD-15). Semantics above are otherwise unchanged.

---

## 1. Stack decision

### 1.1 The three criteria (from the Stage 3 brief)

1. **Library maturity for the actual problems:** async market-data streaming, REST/WS serving, numerical/probability computation, data wrangling for calibration, replay-style backtesting of *event contracts* (note: no mainstream backtesting library targets binary event books — backtrader/vectorbt etc. are equity-bar engines; a thin custom replay harness is required in any language, which neutralizes "backtesting engine maturity" as a differentiator and elevates numerics + data tooling).
2. **User's ability to review correctness of financial logic:** the user fluently reads **Python only** (ABOUT_ME). Money-touching code in any other language is unreviewable by its owner.
3. **Single-maintainer, long-horizon complexity:** fewer languages, runtimes, and build toolchains = fewer things that silently rot between sessions.

### 1.2 Viable options and trade-offs

**Option A — All-Python monolith** (FastAPI + asyncio backend; server-rendered Jinja2 templates + htmx for the dashboard; SQLite; numpy/scipy/pandas).
- *Criterion 1:* Excellent. `httpx`/`websockets` for streaming, FastAPI for REST+WS, numpy/scipy for the probability work, pandas for calibration analysis — all first-class and mature. Custom backtest harness needed, as in every option.
- *Criterion 2:* Excellent. 100% of money-touching logic in Python. The dashboard is HTML templates with declarative `hx-` attributes — no hand-written JavaScript to review (see §1.3 for the one honest caveat).
- *Criterion 3:* Excellent. One language, one venv, one process, no build step, no npm. This is the only option with a single toolchain.
- *Cost:* the dashboard will look utilitarian and interactivity is coarse-grained (fragment swaps, short-interval polling). For four read-mostly views this is a feature, not a bug.

**Option B — Python core + TypeScript/React SPA dashboard.**
- *Criterion 1:* Best-in-class dashboard ecosystem (charting, components); backend identical to A.
- *Criterion 2:* Fails for the frontend: the user cannot review TS/React. Defensible only by declaring the dashboard non-money-touching — but the dashboard carries *controls* (pause/resume/kill), so "unreviewable but harmless" is not quite true.
- *Criterion 3:* Worst. Adds Node, npm, a bundler, a second dependency treadmill, and a frontend/backend API versioning seam — the classic thing that rots between sessions for a single maintainer.

**Option C — All-TypeScript/Node end-to-end.**
- *Criterion 1:* Adequate streaming/serving; materially weaker numerics (no scipy equivalent; probability/calibration work becomes hand-rolled), weaker data wrangling for calibration analysis.
- *Criterion 2:* Fails outright: every line of money-touching logic would be in a language the user can't review. Disqualifying on its own.
- *Criterion 3:* Good (one language) — but it's the wrong language for criteria 1 and 2.

(Go/Rust variants were considered and dismissed without a full column: settlement is minutes-to-months, so raw speed — their main advantage — is explicitly not the binding constraint, and they fail criterion 2 the same way C does.)

### 1.3 Recommendation: **Option A — all-Python monolith**

Concrete stack (Session 5 builds exactly this unless an Open Decision overturns it):

| Layer | Choice | Note |
|---|---|---|
| Language / runtime | Python 3.12+, single `pyproject.toml`, managed with `uv` | one lockfile, reproducible |
| Contract & config models | Pydantic v2 | validation + serialization of OrderIntent etc. |
| Service layer | FastAPI + uvicorn (REST + WebSocket), same process/event loop as orchestrator | §7 |
| Dashboard | Jinja2 templates + **htmx** (vendored, version-pinned static file, no CDN) | §7.3 |
| Persistence | SQLite (WAL mode) via stdlib `sqlite3`, **explicit SQL in one module** — no ORM | reviewability: schema + plain statements beat ORM indirection for a ledger |
| HTTP / streaming clients | `httpx` (async), `websockets` | Kalshi + NWS/METAR |
| Numerics | numpy / scipy / pandas | strategy math + calibration analysis |
| Scheduling | in-house `TickScheduler` asyncio task (no APScheduler) | must be replayable by the backtest harness |
| Auth crypto | `cryptography` (Kalshi API request signing — scheme **[verify, OD-19]**) | |
| Tests | pytest | Stage 5 test-first constraint |

**What the user must be able to READ (not write) to stay a competent reviewer:**
- **Python** — all money-touching logic, which is the entire orchestrator, risk engine, ledger, workers, and execution simulator. This is the whole trust surface.
- **~15 lines of SQL DDL and a handful of INSERT/SELECT statements** in the ledger module.
- **Jinja2 templates** — HTML with `{{ variable }}` interpolation plus declarative `hx-get`/`hx-post`/`hx-trigger` attributes. Budget: ~30 minutes with the htmx attribute reference, once.
- **YAML config and `.env`** files (§10).
- **Honest caveat, stated rather than hidden:** htmx itself is a ~14 kB third-party JavaScript library. The user does not review its internals — it sits in the same trust category as the browser, FastAPI, or CPython itself. The design guarantee is that **money correctness never depends on the dashboard**: every dashboard command goes through the same risk engine as everything else, and the dangerous direction (un-kill, live enablement) is not exposed to the browser at all (§5, §6).

### 1.4 Unified vs. split codebase (single-maintainer consequence)

One repo, one `pyproject.toml`, one process (§2), two entry points (`apacenye serve`, `apacenye <admin-command>`) — both from the same codebase. A split stack (Option B) would add: a second package manager, a second deploy artifact, an API contract that can drift between halves, and duplicate model definitions (Pydantic + TS types). Every one of those is a seam that silently rots when the maintainer is one person who touches the project in bursts. The monolith's only real cost — a plainer UI — is the correct trade for this project. **Decision: unified single codebase.**

---

## 2. System topology

**One long-running process** (`apacenye serve`) hosting asyncio tasks; **one out-of-band CLI** (`apacenye …`) that must work even when the server is down (it operates via the sentinel file and read-only SQLite, §5).

```
┌────────────────────────── apacenye serve (one process, one asyncio loop) ─────────────────────────┐
│                                                                                                   │
│  MarketData service ──snapshots──▶ latest-quote cache ──(read on tick)──▶ Strategy workers (N)    │
│   │  Kalshi WS/REST [read-only]        │                                     │ intents/cancels    │
│   │  data adapters (NWS, METAR)        │                                     ▼                    │
│   │  S1 bracket-coherence monitor      │                            intent queue (asyncio.Queue)  │
│   ▼                                    │                                     │                    │
│  capture writer (JSONL.gz, §9)         └──────────▶ Orchestrator ◀───────────┘                    │
│                                                     │ risk engine (gate pipeline, §3)             │
│  TickScheduler ──eval ticks──▶ workers              │ ledger (SQLite, truth)                      │
│                                                     │ lifecycle supervisor (heartbeats)           │
│                                                     ▼                                             │
│                                          Execution client (RUN_MODE switch, §6)                   │
│                                            PAPER → internal fill simulator                        │
│                                            LIVE  → LiveDisabledError (hard-disabled)              │
│                                                                                                   │
│  FastAPI app (REST + WS + dashboard templates) — reads ledger, sends commands to orchestrator     │
└───────────────────────────────────────────────────────────────────────────────────────────────────┘
   data/KILL sentinel file ◀── apacenye kill (out-of-band CLI, works with server down)
```

**Why asyncio tasks, not OS processes, for workers:** the contract requires independent start/pause/stop/update — that is a *lifecycle* property, satisfied by per-worker task supervision (cancel/recreate a task, workers are restart-safe by contract). Separate processes would buy crash isolation at the price of IPC serialization, multi-process ledger coordination, and a supervisor — the single most rot-prone kind of complexity for one maintainer. Workload is I/O-bound at minutes tempo; the GIL is irrelevant here. The orchestrator's single in-process ledger is the strongest consistency guarantee available and the easiest to review.

**Bindings of contract mechanics (Stage 2 deferred these here):**
- Messages: Pydantic models in `src/apacenye/contract/` — the one module both workers and orchestrator import; the reviewable "interface file".
- Worker → orchestrator: one shared intent/cancel/heartbeat/evaluation queue (tagged by type).
- Orchestrator → worker: per-worker command queue (lifecycle + config) plus per-worker push of fills/position updates and intent dispositions.
- Market snapshots: workers *pull* the latest-quote cache on each tick (no per-tick fan-out queues, no backlog problem; staleness is visible via snapshot timestamps).
- Ticks: `TickScheduler` emits per-worker evaluation ticks per that worker's configured cadence (W1 v0: on forecast refresh + every 10 min during market hours, config-driven). In backtests, the replay harness emits the same tick objects — workers cannot tell the difference, which is the point.

**Supervision rules:** worker heartbeat silence > `HEARTBEAT_TIMEOUT_S` (default 120 s) ⇒ orchestrator pauses that strategy and raises a dashboard alert. A crashed worker task is restarted into `INIT` at most `MAX_RESTARTS_PER_DAY` (default 3) times, then left stopped with an alert.

---

## 3. The order path (the risk layer is real because it sits here)

Full data flow for every order, concretely:

```
worker evaluates on tick
  → emits OrderIntent (proposal) onto intent queue
    → orchestrator risk engine runs the gate pipeline (below), in order
      → disposition: APPROVED(size) | RESIZED(size, reason) | REJECTED(reason)
        → disposition recorded in ledger + pushed to worker + WS feed (always, all three)
          → if approved: execution client submits with client_order_id = intent_id (idempotent)
            → fills → ledger (transactional) → position update → pushed to worker + WS
              → explanation object completed (§8)
```

**Workers never hold a Kalshi client. Ever.** The execution client is constructed by, and reachable only from, the orchestrator. There is no code path from a worker to the order API — this is a structural guarantee, not a convention.

### 3.1 Gate pipeline (evaluated in this order; every disposition records which gates bound)

| # | Gate | Action on breach |
|---|---|---|
| G0 | Schema/validity: well-formed intent, TTL unexpired, price in 1–99¢, side/action consistent | REJECT |
| G1 | **Kill switch** (§5): `data/KILL` exists or in-memory kill flag set | REJECT (exception: human-initiated `reduce`/`close`, §5) |
| G2 | **Run-mode** (§6): LIVE always refused in bootstrap; DRY_RUN short-circuits to logged-no-execution after G10 | REJECT / annotate |
| G3 | Strategy lifecycle: emitting strategy is `START`ed, not paused/stopped | REJECT |
| G4 | Staleness: every `key_inputs` timestamp within the strategy's configured staleness window (W1: forecast ≤ 12 h; W2: station ob ≤ 75 min) | REJECT |
| G5 | Liquidity cap: size ≤ 25% of top-of-book depth at the limit price, **from the orchestrator's own latest snapshot** (never the worker's claim) | RESIZE down |
| G6 | Per-order absolute cap: ≤ `MAX_ORDER_CONTRACTS` (100) | RESIZE down |
| G7 | **Per-event headroom:** open cost basis in this event + reserved pending intents + this intent ≤ 5% × bankroll. *Event = all brackets of one settlement event*, derived by the orchestrator from its market catalog (ticker → event mapping), never trusted from the worker. This is the v0 correlation check (OD-7 enforced in the ledger, exactly as Stage 2 required). | RESIZE / REJECT |
| G8 | Per-strategy headroom: strategy total ≤ 20% × bankroll | RESIZE / REJECT |
| G9 | **Portfolio headroom:** total open + reserved ≤ `MAX_PORTFOLIO_EXPOSURE_PCT` (default 50%, **new — OD-16**) | RESIZE / REJECT |
| G10 | Daily loss: per-strategy day P&L ≤ −2% ⇒ strategy auto-PAUSEd (so G3 rejects); portfolio day P&L ≤ −5% (**new — OD-17**) ⇒ **kill switch trips automatically** | REJECT + side-effect |

**Composition rule (authoritative):** effective approved size = **min over all applicable caps' headroom** — and therefore **the portfolio cap always dominates**: no combination of per-strategy or per-event allowances can exceed portfolio headroom, because the final size must fit inside every gate simultaneously. If the effective size falls below 1 contract, the intent is REJECTED with the binding gate named in the disposition.

**Exposure accounting:** an approved intent *reserves* its cost against all three headrooms (event/strategy/portfolio) at approval time; the reservation converts to open cost basis on fill and is released on expiry, cancel, or rejection-by-venue. Without reservation, two concurrent intents could each pass headroom checks and jointly breach a cap.

**Cancels:** CancelIntents bypass G5–G9 (reducing risk is always allowed) but still log dispositions.

### 3.2 Risk limits — concrete, configurable, conservative

All limits live in `config/risk.yaml` (committed, reviewed in git) with optional environment overrides (prefix `RISK__`, §10). Secrets never appear in YAML; limits never appear only in code.

```yaml
# config/risk.yaml — every value enforceable by the gate pipeline above
bankroll_usd: 1000              # OD-8 (paper notional)
max_event_exposure_pct: 5       # all brackets of one event = one exposure (OD-7)
max_strategy_exposure_pct: 20
max_portfolio_exposure_pct: 50  # OD-16 — portfolio cap; always dominates
max_order_contracts: 100        # unit-bug backstop
max_depth_fraction: 0.25        # of visible top-of-book depth
strategy_daily_loss_pct: 2      # breach ⇒ auto-PAUSE that strategy (human un-pause)
portfolio_daily_loss_pct: 5     # OD-17 — breach ⇒ kill switch trips automatically
heartbeat_timeout_s: 120
max_worker_restarts_per_day: 3
```

---

## 4. Portfolio risk layer — what the orchestrator owns beyond the gates

- **Aggregate exposure view:** per-event / per-strategy / portfolio cost basis + reservations, exposed at `GET /api/risk` and on the dashboard as headroom bars.
- **Correlation handling v0:** the same-event rule (G7). Known residual correlation — e.g., adjacent-day or multi-city weather moving together — is *not* modeled in v0; the portfolio cap is the blunt mitigation. Flagged as future work, not silently ignored (OD-20).
- **Daily-loss stops** per G10, computed by the ledger from realized P&L plus mark-to-market of open positions at latest mid (marks are for *risk triggers*; qualification and paper fills still use executable prices per Stage 2 §2).
- **Kill switch** (§5) — owned here, triggerable from outside.
- **Data-sanity monitor:** S1 bracket-coherence check (Stage 2 §3.6) runs in the market-data service as a monitor: a "violation" raises a dashboard/log alert as probable bad feed first, opportunity second. It emits no intents in this bootstrap.

---

## 5. Kill switch — out-of-band by construction

**Mechanism.** The kill state is a **sentinel file**: `data/KILL` (JSON: `{ts, source, reason}`). Authority: *the file's existence is the kill state.* The risk engine (a) checks the file with an `os.stat` immediately before every execution submission, and (b) runs a watcher task polling it every 2 s to trip the in-memory flag, pause all workers, and cancel resting orders.

**Triggers.**
1. `apacenye kill "reason"` — out-of-band CLI: writes `data/KILL` directly. **Works when the server, API, and dashboard are all down or hung** (it needs only filesystem access); best-effort appends a `kill_events` row when the DB is reachable.
2. Dashboard red button → `POST /api/kill` (in-band convenience; server writes the same file).
3. Automatic: portfolio daily loss breach (G10); optionally repeated data-sanity alarms (future).

**What "halt" does (exact semantics).**
- All new `open`/`increase` intents: REJECTED at G1.
- All resting orders: cancelled best-effort.
- All workers: commanded to `PAUSE`.
- **Open positions are left in place, deliberately.** Kalshi positions are fully collateralized (Stage 1 §1.1) — the maximum loss is already paid, so a halt cannot make positions lose more than they could before. Auto-liquidation, by contrast, would cross spreads and pay fees on the way out, converting a precautionary halt into a guaranteed realized cost. Position risk was capped at entry by the gates; the kill switch stops *new* risk.
- **Human-initiated** `reduce`/`close` intents (dashboard or CLI, explicitly confirmed) remain allowed through G1 so a human can still flatten during a kill. Worker-emitted intents of any kind are rejected while killed.

**Un-kill is asymmetric on purpose:** only `apacenye unkill` (CLI, requires typing the phrase `RESUME TRADING`) removes the file and logs the event. There is **no un-kill over HTTP** — the browser can stop the system but never restart it. After un-kill, workers remain PAUSEd until individually resumed: recovering from a kill is a deliberate two-step, per strategy.

---

## 6. Run modes and the live hard-disable

`RUN_MODE` ∈ `DRY_RUN | PAPER | LIVE`, set in `.env`, immutable at runtime (change requires restart — mode is not hot-updatable by design).

- **DRY_RUN:** full pipeline through every gate and disposition, real market data — but execution logs the approved order and stops. No simulated fills, no position changes. (Purpose: first-light testing and data capture with zero state mutation.)
- **PAPER (default):** approved orders go to the **internal fill simulator** (§6.1) against live *read-only* production market data. No order ever leaves the process.
- **LIVE: hard-disabled in this bootstrap, twice, structurally.**
  1. Config validation at startup: `RUN_MODE=LIVE` raises immediately — the process refuses to boot, printing that live enablement is deferred to a dedicated future hardening session with its own acceptance gate.
  2. The execution factory's live branch constructs nothing: `live.py` contains only a stub raising `LiveDisabledError`. **No live order-submission code exists to enable.** Session 5 scaffolds the client *shape* (auth, request signing, endpoints for market data) but the order-placement method of the live path must raise, not submit.
  A flag that "cannot be turned on within this bootstrap" therefore means: turning it on requires *writing new code in a future session*, not flipping any value that exists today. The concept-checkpoint live gate (§11) is still fully implemented and exercisable — it simply terminates at this wall, and records that it did.

### 6.1 Paper fill simulator — exact semantics (Session 5 implements verbatim)

- Submission is checked against the orchestrator's latest snapshot for that ticker.
- **Buy YES at limit L:** if `ask ≤ L`: immediate fill **at the ask** (never mid, never better than quoted), size capped at `max_depth_fraction × ask_depth`. Any remainder rests.
- **Resting orders:** re-checked on every snapshot update until `ttl_seconds` expires, then expired. **No maker-queue simulation:** a resting order fills only when the opposing quote *crosses* our limit, and then at our limit price — i.e., we always model ourselves as the taker and never award ourselves maker rebates or queue priority.
- Fees: Stage 1 §1.5 formula (`0.07 × C × P × (1−P)`, rounded **up** per order) charged on every simulated execution; exits before settlement charged again; settlement itself free.
- Settlement: at event resolution (from market data), positions pay $1/$0, ledger realizes P&L, capital frees.
- **Stated in code comments, per the Stage 5 brief: paper P&L is an optimistic bound** — no queue competition, no market impact, no partial-fill adverse selection, and the book we fill against may itself be stale.
- Mid-prices are used for *marks* (risk triggers, dashboard P&L display) and clearly labeled "indicative"; executable prices are used for all fill and qualification math (Stage 2 lock).

**Idempotency:** `client_order_id = intent_id` end-to-end; the simulator (and any future venue client) treats a duplicate `client_order_id` as the same order — retries and rate-limit backoff can never double-submit. Unit-tested per the Stage 5 brief.

**Kalshi API touchpoints in this bootstrap** (all read-only): market catalog, order books/quotes, trades, settlement results. Auth scheme (API key ID + RSA request signing) and rate limits are **[verify — OD-19]** before Session 5 builds the client; the demo environment (OD-5) is *not* a dependency of this architecture — the simulator replaces it — but remains optional later.

---

## 7. Service/API layer and dashboard

### 7.1 REST (FastAPI, JSON)

| Endpoint | Purpose |
|---|---|
| `GET /api/state` | run mode, kill status, bankroll, uptime |
| `GET /api/positions` | open positions with cost basis, marks, P&L |
| `GET /api/strategies` / `GET /api/strategies/{id}` | lifecycle state, config version, heartbeat age, day P&L |
| `POST /api/strategies/{id}/pause` / `resume` / `config` | lifecycle commands (routed through orchestrator; resume blocked while killed or ack-invalid, §11) |
| `GET /api/intents?since=` | intents + dispositions (which gates bound) |
| `GET /api/explanations/{intent_id}` | full explanation object (§8) |
| `GET /api/evaluations?strategy=` | shadow-forecast records (calibration data) |
| `GET /api/risk` | headroom per cap: event / strategy / portfolio / daily loss |
| `GET /api/acks` | acknowledgment log, read-only render (§11) |
| `POST /api/kill` | in-band kill trigger. **No unkill endpoint exists** (§5) |

### 7.2 WebSocket

`GET /ws` — server-push JSON events, `{channel, ts, payload}` on channels: `positions`, `intents` (incl. dispositions), `fills`, `heartbeats`, `risk`, `signals` (explanation summaries), `alerts` (staleness, S1 monitor, kill/unkill). This is the live-state feed for the dashboard's signal view and any future client.

### 7.3 Dashboard (four core views, zero hand-written JS)

Server-rendered Jinja2 fragments + vendored htmx. Live-ness v0 = fragment polling (`hx-trigger="every 3s"`); the signal feed may upgrade to the htmx WebSocket extension (declarative, still no hand-written JS) once the polling version works. Buttons are `hx-post` with `hx-confirm` for destructive actions (built-in confirm dialog).

1. **Overview:** positions table with cost/mark/P&L, portfolio risk headroom bars, kill status banner, the red KILL button.
2. **Strategy detail (per strategy):** lifecycle state + pause/resume, config (with version), heartbeat age, day P&L vs. −2% stop, recent intents with dispositions, ack status (§11).
3. **Signal/reasoning feed:** stream of explanation objects — each row: strategy, market, model p vs. market p, net edge, confidence, one-sentence rationale; expandable to the full object.
4. **Risk & compliance:** every cap vs. current usage, daily-loss meters, staleness/alert log, acknowledgment log (read-only).

**Binding & auth (conservative default, OD-18):** bind `127.0.0.1` only; no auth locally. If `DASHBOARD_HOST` is set to anything else, a non-empty `DASHBOARD_TOKEN` becomes mandatory (bearer header) and startup fails without it. Un-kill and live-enable are CLI-only regardless (§5, §11), so the browser surface can never perform the dangerous direction.

---

## 8. Explanation objects

One `ExplanationRecord` per intent, assembled by the **orchestrator** (workers supply their fields via the intent; the orchestrator appends what only it knows), persisted keyed by `intent_id`, served at `/api/explanations/{id}`, summarized on the `signals` channel.

```
ExplanationRecord
├─ from the OrderIntent (Stage 2 contract fields, verbatim):
│    strategy_id, market_ticker, side, action,
│    model_probability          # our estimate of the true probability
│    market_implied_probability # the market's estimate (mid) when deciding
│    net_edge                   # after fees + slippage allowance, at executable price
│    confidence                 # worker's self-assessed estimate quality [0,1]
│    key_inputs                 # decisive inputs WITH timestamps
│    sizing                     # {p_used, kelly_f, k, lambda, bankroll_seen, caps_applied}
│    rationale                  # the one plain-English sentence
├─ from the orchestrator:
│    disposition {status, requested_size, final_size, binding_gates[], reason}
│    risk_context {event/strategy/portfolio headroom at decision time}
│    execution {fills[], avg_price, fees_paid} | dry_run | none
└─ appended at settlement:
     outcome {settled_side, realized_pnl, model_vs_outcome}   # feeds calibration
```

**Field-by-field check against the Stage 2 contract (per the hardening instruction):** all five mandatory explanation fields plus `sizing` and `rationale` exist on every intent — **no blocking gap.** One fidelity gap found and recorded rather than worked around: the intent does not carry the *exact quote the worker saw* (bid/ask/depth/timestamp) — `market_implied_probability` is the mid only. **Contract amendment OD-15 (additive):** add `quote_seen: {bid, ask, bid_depth, ask_depth, ts}` to OrderIntent. Adopted provisionally under Stage 2's "Stage 3 may refine field forms" clause; flagged for ratification because it touches the contract.

---

## 9. Backtesting: harness, data provenance, honesty

**Harness:** replay, not simulation-from-scratch. The backtest driver replays recorded market snapshots and data-adapter records through the **same `TickScheduler` interface and unmodified worker + gate-pipeline code**, with the paper fill simulator (§6.1) executing. Clock is virtual; workers cannot tell replay from live (this is why the contract forbids workers owning wall-clock scheduling).

**Data provenance — where historical data actually comes from:**

1. **Primary: our own recorded capture** (the only source with order-book depth, hence the only source honest about executability). The capture writer records, from day one of Stage 5 running:
   - On-disk format the harness consumes: `data/capture/YYYY-MM-DD/<channel>.jsonl.gz`, one JSON object per line: `{"ts": <UTC ISO-8601>, "type": "book"|"trade"|"settlement"|"nws_forecast"|"metar", "ticker"|"station": …, "payload": {…}}`. Append-only, crash-tolerant, pandas-loadable.
2. **Secondary: Kalshi's documented historical endpoints** (per-market candlesticks and trade history) **[verify — OD-14]**. Known limitation, stated plainly: **no historical order-book depth** ⇒ executable prices and fill feasibility cannot be reconstructed ⇒ any backtest on this data is **illustrative-only**.
3. **Weather ground truth & forecast archive:** Iowa Environmental Mesonet archives (historical NWS forecasts + METAR) and official NWS climate reports for outcomes — availability **[verify — OD-11]**; needed for W1's σ estimation, which is a *model input*, separate from trade-simulation data.

**Honesty rule (binding on Stages 4 and 5, per the hardening instruction):** until several weeks of our own capture exist, **all backtest results are illustrative-only and are NOT a basis for live confidence** — they may not be cited in any live-enablement argument, and the backtest skill/docs must repeat this limitation wherever results are shown. No synthetic data may ever be presented as historical.

---

## 10. Secrets, configuration, and the .env convention (binding for Stages 4–5)

**Layering — secrets and tunables never mix:**
- **`.env` (gitignored, machine-local):** secrets and machine/mode facts only.
- **`config/*.yaml` (committed):** risk limits (§3.2) and strategy parameters — being in git and reviewable is a feature. Env override prefix `RISK__` for limits (e.g., `RISK__MAX_ORDER_CONTRACTS=50`), loaded via pydantic-settings; overrides logged at startup so the effective config is always visible.
- **Hard rule: no credential, key, or token ever appears in Python source, YAML, logs, dashboards, or explanation objects.** Config printing/serialization must redact fields marked secret.

**`.env` structure (authoritative; `.env.example` with placeholders + these comments is committed):**

```bash
# --- Kalshi API (read-only market data in this bootstrap) ---
KALSHI_API_KEY_ID=                                   # key ID from Kalshi portal
KALSHI_PRIVATE_KEY_PATH=secrets/kalshi_private.pem   # RSA private key FILE PATH; file never committed
KALSHI_ENV=prod                                      # prod = real read-only market data; demo optional (OD-5)

# --- Run mode (§6) ---
RUN_MODE=PAPER                                       # DRY_RUN | PAPER | LIVE (LIVE refuses to boot in bootstrap)

# --- Dashboard (§7.3) ---
DASHBOARD_HOST=127.0.0.1
DASHBOARD_PORT=8642
DASHBOARD_TOKEN=                                     # REQUIRED iff host != 127.0.0.1

# --- Optional risk overrides (committed defaults live in config/risk.yaml) ---
# RISK__MAX_ORDER_CONTRACTS=50
```

**Required `.gitignore` entries (verbatim):**

```gitignore
.env
.env.*
!.env.example
secrets/
!secrets/README.md
*.pem
*.key
data/
*.sqlite*
__pycache__/
.venv/
```

**Sequencing requirement (the repo is not yet a git repository):** whichever session runs `git init` first — Stage 4 by default — must make **`.gitignore` part of the very first commit, before any `.env`, key file, or `data/` content exists on disk**, so no secret is ever in the index even transiently. `secrets/README.md` (committed) explains what belongs there and that nothing in the directory is ever committed.

---

## 11. Concept checkpoints — exact mechanism, content, trigger, log

### 11.1 Mechanism decision: interactive CLI, dashboard read-only

The checkpoint is an **interactive CLI command**: `apacenye ack --strategy <id> --gate <paper|live>`. Chosen over a dashboard modal because: (a) a multi-step typed-input form in the browser means hand-written JavaScript, violating the minimal-JS constraint; (b) it shares the out-of-band admin path with kill/unkill, working even when the server is down; (c) typing in a terminal is materially more deliberate than clicking through a modal. The dashboard shows acknowledgment *status* and the log, read-only.

### 11.2 Triggers (enforced by the orchestrator, not by convention)

- **`paper` gate:** the orchestrator refuses to `START` a strategy for paper trading unless a `PASSED` paper acknowledgment exists for that `strategy_id` + current *risk-relevant config hash*. Risk-relevant params (changing any invalidates the ack and forces re-acknowledgment): `bankroll_usd`, all exposure caps, `k`, `lambda`, the net-edge floor. Other tunables (staleness windows, cadences) do not invalidate. This gate makes the mechanism exercisable end-to-end within the bootstrap.
- **`live` gate (the hardening-mandated trigger):** a request to enable real capital for a specific strategy — `apacenye enable-live --strategy <id>` — requires completing the full live-gate checkpoint first. In this bootstrap the flow **always terminates at the §6 wall**: the checkpoint runs, is recorded, and the command then prints the hard-disable refusal and records `outcome_note: "live refused: bootstrap hard-disable"`. A bootstrap-era live ack does **not** pre-authorize anything; the future hardening session defines its own acceptance gate and will require a fresh acknowledgment.

### 11.3 Content — the five concepts, with computed questions and required typed acknowledgments

Each concept is one screen: a short plain-language recap (values computed live from that strategy's current config — never hardcoded examples), then **one validated question**, then **one required typed sentence** (matched case-insensitively, whitespace-normalized, otherwise exact). A wrong answer shows the explanation and re-asks; **3 failures on any concept aborts the gate** and logs the aborted attempt. Numeric answers accept ±1% tolerance.

| # | Concept | Question (computed at runtime) | Required typed acknowledgment |
|---|---|---|---|
| K1 | Implied vs. true probability | "A YES contract trades at 62¢. Is 62% (a) the true probability, or (b) the market's aggregate estimate?" → `b` | "I understand a market price is the market's estimate of probability, not the true probability." |
| K2 | Sizing runs on an ESTIMATE | "With p_model=0.60, p_market=0.50, λ=0.5 — what probability does sizing actually use?" → `0.55` | "I understand position sizing runs on an estimated probability, and over-trusting that estimate can turn a real edge into a loss." |
| K3 | Worst-case drawdown, this strategy | "Per-event cap $«X», per-strategy cap $«Y» (from current config). If every «STRAT» position went to $0, what is the maximum dollar loss?" → `«Y»` | "I understand «STRAT» can lose up to $«Y» («y»% of bankroll) and I accept that worst case." |
| K4 | Round-trip fees + slippage | "Fee formula 0.07 × C × P × (1−P): what is the fee for 100 contracts at 50¢?" → `$1.75` | "I understand every round trip costs fees plus spread, and the model's edge must exceed those costs before a trade is worth taking." |
| K5 | Paper-vs-live fidelity | (recap of §6.1 limits; confirm the optimistic-bound statement, answer `yes`) | "I understand paper results are an optimistic bound and are not evidence the strategy makes money live." |

- **Paper gate:** K1, K2, K4, K5.
- **Live gate:** all five (K1–K5) in full — a prior paper ack does not skip any — followed by a final typed request line: `ENABLE LIVE <strategy_id> CONFIG <config_hash>` (the CLI prints the exact line to type, hash included). In this bootstrap, completing it produces the recorded refusal per §11.2.

### 11.4 Append-only acknowledgment log (exact format)

`data/acks/acknowledgments.jsonl` — opened append-only (`O_APPEND`), never rewritten or edited; one JSON object per line:

```json
{"seq": 4, "prev_sha256": "<sha256 of previous line, 64 hex chars; 0^64 for seq 1>",
 "ts": "2026-07-18T21:04:11Z", "gate": "paper", "strategy_id": "W1",
 "config_hash": "sha256:…", "config_snapshot": {"bankroll_usd": 1000, "max_event_exposure_pct": 5, "…": "…"},
 "concepts": [{"id": "K1", "question": "…", "answer_given": "b", "correct": true, "attempts": 1,
               "typed_ack": "I understand a market price is …"}],
 "result": "PASSED", "outcome_note": null}
```

The `prev_sha256` chain makes any retroactive edit detectable by a trivial verification pass (`apacenye ack --verify-log`). The exact typed sentences, the config snapshot, and per-question attempt counts are all persisted — the log answers "what exactly did I acknowledge, about which parameters, and when." The orchestrator reads this file (via the checkpoint module) to enforce §11.2; the dashboard renders it read-only.

---

## 12. Proposed directory layout (provisional — Stage 4 documents it, Stage 5 reconciles)

```
apacenye/
├── pyproject.toml  .env.example  .gitignore  CLAUDE.md(Stage 4)
├── config/                 # committed: risk.yaml, strategies/w1.yaml
├── secrets/                # gitignored; README.md only committed file
├── data/                   # gitignored runtime: apacenye.sqlite, capture/, acks/, KILL
├── src/apacenye/
│   ├── contract/           # Pydantic models: OrderIntent, CancelIntent, Heartbeat,
│   │                       #   Evaluation, Disposition, ExplanationRecord  ← THE interface module
│   ├── orchestrator/       # risk_engine.py (gates G0–G10), ledger.py (SQLite, explicit SQL),
│   │                       #   lifecycle.py (supervisor), kill.py (sentinel logic)
│   ├── execution/          # paper.py (fill simulator §6.1), kalshi.py (auth + read-only data),
│   │                       #   live.py (LiveDisabledError stub — no submission code)
│   ├── marketdata/         # feed.py, snapshots.py (latest-quote cache), catalog.py (ticker→event),
│   │                       #   monitors.py (S1 bracket-coherence)
│   ├── workers/            # base.py (lifecycle ABC), w1_forecast.py
│   ├── dataadapters/       # nws.py, metar.py
│   ├── service/            # api.py, ws.py, templates/, static/htmx.min.js (vendored, pinned)
│   ├── checkpoint/         # ack.py (gates §11), log verification
│   ├── backtest/           # capture.py (writer), replay.py (harness §9)
│   └── cli.py              # apacenye: serve | kill | unkill | ack | enable-live | status
└── tests/                  # financial logic tested FIRST (Stage 5 constraint)
```

SQLite tables (ledger module, explicit DDL): `markets`, `events`, `intents`, `dispositions`, `orders`, `fills`, `positions`, `cash_ledger`, `evaluations`, `heartbeats`, `config_versions`, `kill_events`. The acknowledgment log stays in JSONL (§11.4) as the append-only authority.

---

## 13. Open Decisions

**Resolved this session (architectural, within delegated authority):**
- Stack = all-Python monolith (§1.3); unified codebase (§1.4); asyncio-task workers with Pydantic/queue contract binding (§2); SQLite + explicit SQL; internal paper simulator rather than a demo-API dependency (§6.1) — this *narrows* OD-5: demo access is now optional, not blocking.
- Checkpoint mechanism, content, triggers, and log format (§11) — fixed, as the brief required.
- Secrets/.env/.gitignore convention (§10) — fixed.

**New this session — conservative defaults chosen unattended, flagged for user ratification (do not silently change):**
- **OD-15 — Contract amendment `quote_seen` (additive).** Add `{bid, ask, bid_depth, ask_depth, ts}` to OrderIntent for explanation fidelity (§8). Provisionally adopted under Stage 2's field-form clause; ratify or strike.
- **OD-16 — Portfolio aggregate exposure cap:** default **50% of bankroll**. New (Stage 2 defined no portfolio-level cap). It always dominates per §3.1.
- **OD-17 — Portfolio daily-loss auto-kill:** default **−5% of bankroll ⇒ kill switch trips**. New; complements Stage 2's per-strategy −2% auto-PAUSE.
- **OD-18 — Dashboard exposure:** localhost-only, no auth; non-localhost binding requires `DASHBOARD_TOKEN`. Un-kill and live-enable are CLI-only regardless.
- **OD-20 — Residual correlation:** v0 enforces only same-event aggregation (OD-7); cross-day/cross-city weather correlation is mitigated only by the portfolio cap. Acceptable for paper; revisit before any live discussion.

**Carried forward, still [verify] (unchanged from Stages 1–2 unless noted):**
- **OD-1** fee schedule; **OD-2** liquidity/spread reality; **OD-3** current listings; **OD-11** forecast-error archive (IEM); **OD-12** W2 precondition; **OD-13** E1 data path.
- **OD-5 (narrowed):** demo environment now optional (§6.1); still worth verifying for a future venue-integration test.
- **OD-14 (new [verify]):** Kalshi historical candlestick/trade endpoints — availability, granularity, terms. Illustrative-only regardless, per §9.
- **OD-19 (new [verify], blocks Session 5's client):** Kalshi auth scheme (API key + RSA request signing assumed) and rate limits. Session 5 must confirm against current docs before writing `kalshi.py`, and implement rate-limit backoff per whatever the documented limits are.

**Action required at next attended opportunity:** ratify OD-15…OD-18; confirm git-init sequencing (§10) happens before any secret exists.

---

## 14. Handoff self-check (acceptance criteria)

**(1) Can Session 4 write conventions/skills from this document alone?** Directory layout (§12, provisional as its brief requires), stack and tooling (§1.3), always-apply safety rules (paper-only + boot refusal §6, intents-only §3, secrets §10, numeric guardrails §3.2), and the workflows its skills must describe (backtest §9 with limitations, pre-live review → §11 checkpoint — the same gate and log, not a second one). **Supported.**

**(2) Can Session 5 implement the checkpoint and secrets handling exactly, with no interpretation?** §11 fixes mechanism (CLI command name and flags), triggers (orchestrator-enforced, both gates), the five concepts with their questions, answers, tolerances, retry/abort rules, and required sentences, and the log's path, format, and hash chain. §10 fixes `.env` keys, YAML split, redaction rule, `.gitignore` verbatim, and init sequencing. **Supported.**

**(3) Is the order path concrete enough that the risk layer is real?** §3: workers structurally cannot reach the order API; the gate pipeline G0–G10 is enumerated with actions, the composition rule (portfolio cap dominates via min-of-headrooms), and reservation accounting; §5 fixes out-of-band kill semantics including what halt does to open positions; §6 fixes the double hard-disable of LIVE and exact paper-fill semantics. **Supported.**

**Known gaps, stated plainly:** OD-19 (Kalshi auth/rate limits) must be verified before `kalshi.py` is written; all Stage 1–2 [verify] items remain open; backtests are illustrative-only until our own capture accumulates (§9); OD-15–OD-18 are unattended conservative defaults awaiting ratification, chosen per the ABOUT_ME protocol rather than resolved silently.
