# Stage 2 Handoff — Strategy Design

**Project:** Apacenyë — personal Kalshi trading platform (PAPER-ONLY bootstrap)
**Written:** 2026-07-18 (Session 2 of 5)
**Audience:** Session 3 (Orchestrator/Architecture design), which starts with zero memory of this session. This document must be self-sufficient, but assumes Session 3 also reads `stage1-foundations.md` (fee math, category rationale) and `ABOUT_ME.md` (Python-only money logic, paper-only, plain-language constraint).

**Decisions ratified by the user this session (2026-07-18):**
- **OD-6 resolved:** Weather-first confirmed. Primary category: daily temperature markets. Secondary (designed here, built later): FOMC/CPI cross-venue fair value. Shelf: administrative-data nowcasts and bracket-coherence checks.
- **OD-4 resolved:** Minimum net edge locked at **4 percentage points** after all costs, with the cost/slippage assumptions in §2 below.

**Data honesty note (inherited from Stage 1):** No live Kalshi access this session either. Every liquidity, spread, and fee figure remains **[estimate — verify live]**. Strategy *parameters* below are conservative defaults; strategy *structure* does not depend on them.

---

## 1. Being right vs. sizing correctly (read this first)

This section exists because the acceptance criterion for this stage is that you can explain the distinction in your own words.

### 1.1 The claim

Two traders with the **same model, the same trades, and the same win rate** can end up with opposite outcomes — one compounds wealth, the other goes broke. The difference is only *how much they bet each time*. Being right tells you the sign of your expected value; sizing determines whether you ever collect it.

### 1.2 The numeric example (over-sizing destroys a real edge)

Setup: a contract trades at **50¢** and your model says the true probability is **60%**. That is a genuine 10-point edge. If you stake a fraction *f* of your bankroll: a win multiplies your bankroll by (1 + *f*) (each 50¢ contract pays $1), a loss multiplies it by (1 − *f*).

Every single bet has positive expected value: EV per trade = 0.6·*f* − 0.4·*f* = **+0.2·f > 0, for any f**. Now run 100 such trades where the model is exactly right — **60 wins, 40 losses** — for two traders who differ only in *f*:

| | Trader A: f = 10% | Trader B: f = 50% |
|---|---|---|
| Per-trade EV | positive | positive (5× larger!) |
| Win rate realized | 60/100 | 60/100 |
| Final bankroll | 1.1⁶⁰ × 0.9⁴⁰ = **×4.50** | 1.5⁶⁰ × 0.5⁴⁰ = **×0.034** |
| Outcome | **+350%** | **−96.6%** |

Identical edge, identical accuracy, opposite results. The mechanism is one you already know from math: repeated multiplication is governed by the **mean of the logs**, not the log of the mean. Growth per trade is g(f) = 0.6·ln(1+f) + 0.4·ln(1−f). At f=0.10, g = +0.015 (compounds up). At f=0.50, g = −0.034 (compounds down) — *even though arithmetic EV rises with f*. Losses hurt multiplicatively more than equal-sized wins help (−50% needs +100% to recover), and past a critical size that asymmetry eats the entire edge.

### 1.3 Kelly: the size that maximizes growth

The **Kelly criterion** is just `argmax_f g(f)`. For a binary contract bought at cost *c* dollars with win probability *p*:

```
b  = (1 - c) / c            # net odds: profit per dollar staked if you win
f* = (b·p - (1 - p)) / b    # Kelly fraction of bankroll
```

In the example: b = 1, f* = 0.6 − 0.4 = **20%**. Trader B at 50% was betting 2.5× Kelly, which is why a real edge produced ruin. Key facts: growth is maximized *at* f\* and becomes **negative at roughly 2× f\***; betting more than Kelly is strictly worse than betting less, because under-sizing costs you some growth while over-sizing can cost you everything.

### 1.4 Why we never bet full Kelly: the probability is an estimate

Kelly's formula consumes *p* as if it were the true probability. **Our *p* is a model output with its own error bars.** If the model says 60% but the truth is 55%, full Kelly computed on 60% (f = 0.20) gives growth g = 0.55·ln(1.2) + 0.45·ln(0.8) ≈ **−0.0001** — a genuinely profitable situation (5 real points of edge) converted into zero growth purely by over-trusting the model. The same trades at quarter-Kelly (f = 0.05) still grow at ≈ +0.4% per trade.

Therefore every strategy in this document sizes as follows, and **no strategy may deviate**:

1. **Shrink the estimate toward the market:** `p_used = λ·p_model + (1−λ)·p_market`, default **λ = 0.5** (OD-9). Rationale: the market price is itself a competent estimator; averaging hedges our model's miscalibration until we have measured calibration data to justify raising λ.
2. **Fractional Kelly:** stake `k × f*(p_used)`, default **k = 0.25** (quarter-Kelly, OD-9).
3. **Hard caps that bind independently of Kelly** (§5). Even a wildly miscalibrated model (say it emits p = 0.99) cannot place more than the per-event cap. Kelly proposes; caps dispose.

```python
def kelly_fraction(p_win: float, cost: float) -> float:
    """Growth-optimal bankroll fraction for a binary contract costing `cost` dollars."""
    b = (1.0 - cost) / cost
    return max(0.0, (b * p_win - (1.0 - p_win)) / b)

def stake_dollars(p_model: float, p_market: float, cost: float,
                  bankroll: float, lam: float = 0.5, k: float = 0.25) -> float:
    p_used = lam * p_model + (1.0 - lam) * p_market   # shrinkage toward market
    return k * kelly_fraction(p_used, cost) * bankroll  # caps applied AFTER this (§5)
```

**One-sentence summary for the acceptance criterion:** *"Being right" makes the average trade profitable; "sizing correctly" makes the sequence of trades profitable — and because our probabilities are estimates, we deliberately bet a fraction of the mathematically optimal size and cap it with limits no model output can override.*

---

## 2. Cost model and the qualification rule (locked this session)

A trade **qualifies** only if the edge survives all costs with the ratified margin:

```
net_edge = p_model − executable_price − taker_fee(executable_price) − SLIPPAGE_ALLOWANCE
QUALIFIES iff net_edge ≥ 0.04                       # OD-4, user-ratified
```

Locked assumptions (every worker must use these; all revisit-able once OD-1/OD-2 are verified live):

- **Executable price, not mid:** buys are priced at the **ask**, exits at the **bid**. Paper P&L never assumes maker fills or mid fills.
- **Fee:** `0.07 × P × (1−P)` per executed leg, rounded up per order (Stage 1 §1.5 code). Applied on entry always; on exit only if exiting before settlement.
- **SLIPPAGE_ALLOWANCE = 1¢ per contract per leg**, on top of paying the full quoted spread, to cover book-walking. Kept honest by the liquidity cap: orders never exceed **25% of visible top-of-book depth** (§5), so the allowance stays realistic.
- Worked qualification at a 48¢ ask: fee = 0.07×0.48×0.52 ≈ 1.75¢; required model probability ≥ 0.48 + 0.0175 + 0.01 + 0.04 = **54.75%**.

---

## 3. Strategy archetypes

Every archetype below is a **self-contained, swappable worker** conforming to the single interface contract in §4. Multiple workers run concurrently from day one; none knows the others exist; the orchestrator owns all money-touching coordination.

Notation: **Confidence** = how sure we are the edge exists at all (not how big it is). **Complexity** = effort to implement correctly in Python on home infrastructure.

### 3.1 W1 — Forecast-anchored fair value (weather) — *the core archetype*

**Thesis:** retail flow anchors on point forecasts ("the app says 86°"); the correct object is a *probability distribution* over the day's max, so brackets near the point forecast are systematically overpriced and tail brackets mispriced.

- **Signal / data sources:** NWS forecast API for the settlement station's forecast high (free); station-specific historical forecast-error distribution built from archived forecasts vs. official climate reports (source: NWS/Iowa Environmental Mesonet archives — availability is OD-11 [estimate — verify]).
- **Fair value (v0 — deliberately simple):** model the day's max as `T_max ~ Normal(μ = NWS forecast high, σ = station's historical forecast-error std at that lead time)` (σ ≈ 2–4°F typical [estimate — verify from archive]). Bracket probability with continuity correction (brackets are integer °F):

```python
from math import erf, sqrt

def bracket_prob(lo: float, hi: float, mu: float, sigma: float) -> float:
    """P(lo <= Tmax <= hi) under Normal(mu, sigma); integer-degree brackets."""
    cdf = lambda x: 0.5 * (1.0 + erf((x - mu) / (sigma * sqrt(2.0))))
    return cdf(hi + 0.5) - cdf(lo - 0.5)
```

- **v1 upgrade path (same worker, config change):** replace the Gaussian with the empirical distribution from GEFS/ECMWF ensemble members, updated intraday. The interface, sizing, and risk logic do not change — only the `p_model` producer. This is deliberate: it proves the "swap the model, keep the contract" property.
- **Entry:** each morning after the forecast update (and on re-evaluation ticks), compute `p_model` for every bracket in the city/day set; emit an open intent for any bracket passing the §2 rule (either side — buying NO on an overpriced middle bracket is the same math with `c = 1 − price`).
- **Exit:** default **hold to settlement** (no exit fee, and daily resolution means capital is freed nightly). Early-exit intent only if the sign of the edge flips by more than the round-trip cost (i.e., the market has moved past fair value plus costs) or if a data-staleness rule (§5) fires.
- **Sizing:** §1.4 procedure. `p_market` = bid/ask midpoint.
- **Risk limits:** §5 defaults; all brackets of one city/day are **one exposure** (OD-7): the sum of costs across same-event positions counts against the single event cap.
- **Worked example:** forecast high 86°F, σ = 3.0 → P(85–89 bracket) = Φ(3.5/3) − Φ(−1.5/3) ≈ 0.879 − 0.309 = **0.57**. Market: bid 46¢ / ask 48¢ (mid 47¢). Net edge = 0.57 − 0.48 − 0.0175 − 0.01 = **6.25 pts ≥ 4 → qualifies**. Sizing: p_used = 0.5·0.57 + 0.5·0.47 = 0.52; b = 0.52/0.48 = 1.083; f* = 7.7%; quarter-Kelly → 1.9% of a $1,000 paper bankroll ≈ $19 ≈ **40 contracts**, then capped by event cap ($50) and 25%-of-depth.
- **Confidence: Medium.** The behavioral story is plausible and documented in adjacent domains, but "other bots exist" (Stage 1 §3.1); assume easy mispricings are picked over. **Complexity: Low (v0) / Medium-High (v1).**
- **Failure modes:** σ mis-estimated (mitigated by shrinkage + caps); forecast revision after entry (mitigated by re-evaluation ticks and early-exit rule); correlated same-day city weather across brackets (handled as one exposure).

### 3.2 W2 — Late-day determinism (weather) — *convergence nowcast*

**Thesis:** by mid-afternoon the running max *already bounds the outcome* (temperature can rise until ~4–6pm local, then the day's max is essentially locked), yet contracts can still trade at non-degenerate prices out of pure inattention. This is the same edge as W1 but driven by *observations replacing the forecast* as the day unfolds.

- **Signal / data sources:** hourly-or-better METAR observations **from the exact settlement station** (free, and it is the settlement source itself); station-specific historical "additional warming after hour h" distribution from the METAR archive.
- **Fair value:** with running max `M_t` at local hour `h`, for threshold K: if `M_t ≥ K`, `p_model = 1` (determined — subject only to the official report confirming, which is the settlement source we're reading). Otherwise `p_model = P(Δ_h ≥ K − M_t)` from the empirical additional-warming distribution for that station/season.
- **Entry:** late-day only (config window, default after 2pm local). Buy the near-determined side when the market still offers it below fair value net of the §2 rule. Fees help here: at 95¢ the fee is ~0.33¢, so even a 95¢ → $1.00 convergence can clear the 4-point floor.
- **Exit:** hold to settlement (hours away by construction).
- **Sizing:** §1.4 procedure unchanged. Note Kelly at p_used ≈ 0.97, c = 0.95 gives large f* — this is exactly where the **hard caps** (§5) do the real work, and where the "determined" logic must be conservative (use the *official running max*, not a derived guess).
- **Risk limits:** §5, plus a strategy-specific staleness rule: **no intent if the latest station observation is older than 75 minutes** (missed METARs are common [estimate — verify frequency]).
- **Confidence: High that the logic is sound; unverified that the opportunity survives contact** — whether stale late-day quotes persist at tradeable size is exactly Stage 1's flagged estimate (now **OD-12**: verify live before building W2). **Complexity: Medium** (intraday polling loop, archive-derived warming curves, timezone/DST care).
- **Failure modes:** the settlement report occasionally differs from live METAR feed (verify mismatch frequency, OD-12); a stale quote that fills instantly may be adverse selection (someone knows the ob you haven't seen yet — mitigated by the staleness rule).

### 3.3 E1 — Cross-venue fair value: FOMC (econ, secondary — design now, build later)

**Thesis:** CME fed funds futures price the same event with professional capital; Kalshi retail flow updates slower. Fair value is *imported*, not modeled.

- **Signal / data sources:** fed funds futures-implied meeting probabilities (public "FedWatch"-style derivation; access path and terms are **OD-13 [verify]** — scraping fragility and licensing are the real dependency).
- **Fair value:** `p_model` = futures-implied probability for the same rate outcome, mapped carefully to Kalshi's exact contract definition (contract-mapping is where the bugs live).
- **Entry:** §2 rule on the gap. **Position must be flat or explicitly sized-for-release before the announcement** (prices jump discontinuously).
- **Exit:** convergence (gap < round-trip cost) or hold through settlement if still edge-positive.
- **Sizing / risk:** §1.4 and §5 unchanged; additionally treat all rate brackets of one meeting as one exposure.
- **Confidence: High** (mechanical anchor). **Complexity: Medium** (data plumbing + contract mapping). **Cadence: poor for a bootstrap** — 8 meetings/year gives almost no calibration feedback, which is why the user ratified building it later.

### 3.4 E2 — Nowcast-anchored fair value: CPI (econ, secondary)

Same skeleton as E1 with `p_model` from the Cleveland Fed inflation nowcast mapped to a distribution over the print (nowcast error shrinks through the month; σ from published track record). **Confidence: Medium** (nowcast is good but the mapping from point nowcast to bracket distribution is on us). **Complexity: Medium.** Monthly cadence — same feedback-loop weakness as E1.

### 3.5 A1 — Partial-aggregate nowcast (admin data, shelf)

Weekly-resolving series that settle on slow-moving published dailies (AAA gas average, jobless claims). By midweek the outcome is substantially determined by already-published readings — structurally W2's logic on a weekly clock. **Confidence: Medium; Complexity: Low-Medium; books likely thin [OD-2/OD-3 verify].** Build only if listings and liquidity check out; the worker skeleton is W2's with a different data adapter — another argument for the shared contract.

### 3.6 S1 — Bracket-coherence check (structural, cross-cutting — ship as monitor first)

For any complete mutually-exclusive bracket set: `sum(asks) < $1 − total_fees` → buying every bracket locks a riskless profit (exactly one settles at $1); symmetrically for bids > $1. **Confidence: Very high when it fires; fires rarely and small [verify].** **Complexity: Very low** — and it doubles as a **data-sanity monitor** (a "violation" is more often a bad feed than free money, which is itself the alert we want). Ships inside the platform as a monitor emitting alerts from day one; may be promoted to an order-intent-emitting micro-strategy later. Note it is *not* fully representative of the contract (no probability model), which matters in §6.

---

## 4. The Strategy Worker contract (Session 3's dependency)

**Semantics are fixed here; the mechanical binding (classes vs. processes vs. queues, serialization, scheduling) is Stage 3's call, and Stage 3 may refine the field *forms* so long as these semantics survive.** Money-touching implementation stays in Python (ABOUT_ME).

### 4.1 Governing principle: propose-then-approve

**Workers never place orders. Ever.** A worker's only money-adjacent output is an **OrderIntent** — a *proposal*. The orchestrator owns submission and may **approve, resize down, defer, or reject** any intent to enforce portfolio-level limits and the kill switch. Workers must treat every intent as possibly-unfilled and possibly-rejected; correctness cannot depend on intents being honored. (Without this inversion, Stage 3's risk layer has nothing to enforce.)

### 4.2 Lifecycle states (every worker must implement all five)

| State/transition | Contract |
|---|---|
| `INIT` | Load config, validate data-source access, load persisted calibration state. May fail loudly; must not emit intents. |
| `START` | Begin evaluation loop; emit intents when rules fire. |
| `PAUSE` | Stop emitting intents immediately; keep state warm; keep consuming data. Orchestrator uses this for daily-loss stops and the kill switch. |
| `STOP` | Graceful shutdown; emit CancelIntents for anything outstanding; persist state. |
| `UPDATE_CONFIG` | Hot-apply new parameters or reject with a reason (no restart required for tunables like edge floor, k, λ). |

Workers must be **restart-safe**: on `INIT` after a crash, a worker rebuilds its view from orchestrator-supplied positions plus refetched data. Workers hold no authoritative position state — the orchestrator's ledger is truth.

### 4.3 Inputs a worker consumes

- **Market snapshots** for subscribed tickers: bid, ask, top-of-book depth, last trade, timestamps (delivery mechanism is Stage 3's).
- **Own-position state and fills**, attributed per strategy, pushed by the orchestrator (authoritative).
- **Config**: the tunables in §5 plus strategy-specific parameters, versioned.
- **Clock/schedule ticks** from the platform (workers do not own wall-clock scheduling; this keeps backtests honest later).
- **External data** via a per-strategy data adapter (NWS, METAR, futures, …). Whether adapters live inside the worker or in a shared data layer is a Stage 3 decision; the contract only requires each input's **timestamp** be carried through to `key_inputs` so staleness is enforceable.

### 4.4 Outputs a worker emits

**(a) OrderIntent** — every field mandatory:

| Field | Meaning |
|---|---|
| `intent_id`, `strategy_id`, `ts` | Identity, provenance, creation time |
| `market_ticker`, `side` | Contract and YES/NO |
| `action` | `open` / `increase` / `reduce` / `close` |
| `limit_price` | Worst acceptable executable price (workers never emit market orders) |
| `size_contracts` | Proposed size — **a request, not a command** |
| `ttl_seconds` | Intent expires unexecuted after this; no immortal intents |
| `model_probability` | The worker's `p_model` |
| `market_implied_probability` | The mid the worker saw when deciding |
| `net_edge` | Per §2: after fee + slippage allowance, at the executable price |
| `confidence` | Worker's self-assessed estimate quality in [0,1], with a stated basis (e.g., data freshness, σ of the model) — orchestrator may scale size by it |
| `key_inputs` | Dict of the decisive inputs *with their timestamps* (e.g., `{"nws_forecast_high": 86, "sigma": 3.0, "forecast_ts": …}`) |
| `sizing` | Dict: `{p_used, kelly_f, k, lambda, bankroll_seen, caps_applied}` — the full sizing trace |
| `rationale` | One plain-English sentence a human can read in a log |

The five explanation fields (`model_probability`, `market_implied_probability`, `net_edge`, `confidence`, `key_inputs`) are the raw material for Stage 3's explanation objects and are **non-negotiable** on every intent.

**(b) CancelIntent** — `intent_id` to withdraw; workers must cancel when their edge assessment flips or inputs go stale.

**(c) Heartbeat/health** — periodic: state, last-evaluation time, per-input data ages, error flags. Silence beyond a threshold ⇒ orchestrator pauses the strategy.

**(d) Evaluation records (shadow forecasts)** — **every** evaluation is logged with `p_model`, market prices, and the qualify/no-trade outcome, *even when no intent is emitted*. This is how we measure calibration (Brier score, reliability curves) without risking even paper capital, and it is the data that later justifies raising λ or k. Cheap to emit, extremely valuable — do not let Stage 3 drop it.

### 4.5 What the orchestrator promises workers (for Stage 3 to honor)

Per-intent disposition (approved/resized/rejected + reason), fills as they happen, authoritative position/bankroll state on demand, well-formed market snapshots, and lifecycle commands only via §4.2. Everything else — order submission, portfolio netting, kill switch, live-flag hard-disable — is orchestrator territory and out of scope for any worker.

---

## 5. Risk limits (defaults; orchestrator-enforced, worker-self-enforced first)

Workers apply these to their own proposals (first line); the orchestrator re-checks and enforces at the portfolio level (the binding line). All are config, all conservative pending live data:

| Limit | Default | Note |
|---|---|---|
| Paper bankroll (notional) | **$1,000** | OD-8; arbitrary but fixed so P&L percentages mean something |
| Per-event exposure cap | **5% of bankroll** | *All brackets of one event are one exposure* (OD-7 resolved into design). Binds regardless of Kelly output. |
| Per-strategy exposure cap | **20% of bankroll** | Across events |
| Liquidity cap | **≤ 25% of visible top-of-book depth** | Keeps the 1¢ slippage allowance honest |
| Per-order absolute cap | **100 contracts** | Crude backstop against unit bugs (cents/dollars, size-vs-price swaps) |
| Daily loss stop, per strategy | **−2% of bankroll ⇒ auto-PAUSE** | Human required to un-pause |
| Data staleness | Strategy-specific; W1: forecast > 12h; W2: station ob > 75 min | Stale input ⇒ no new intents + cancel outstanding |
| Kill switch | Orchestrator-owned, Stage 3 | PAUSE-all + cancel-all; workers need no logic beyond honoring PAUSE |

---

## 6. Ranking and the reference-implementation pick

| Rank | Strategy | Edge confidence | Complexity | Cadence/feedback |
|---|---|---|---|---|
| 1 | **W1-v0** forecast-anchored Gaussian | Medium | **Low** | Daily — excellent |
| 2 | W2 late-day determinism | High logic / unverified market (OD-12) | Medium | Daily — excellent |
| 3 | E1 FOMC cross-venue | High | Medium | ~8/year — poor |
| 4 | S1 bracket-coherence | Very high, rare | Very low | Continuous monitor |
| 5 | W1-v1 ensemble upgrade | Medium-High | Medium-High | Daily |
| 6 | E2 CPI nowcast | Medium | Medium | Monthly — poor |
| 7 | A1 partial-aggregate nowcast | Medium | Low-Medium | Weekly; listings unverified |

**Build first (Stage 5 reference implementation): W1-v0.**

The confidence and complexity rankings conflict — E1 and S1 score higher on edge confidence — so per the ratified tie-break rule, **the reference implementation is the simplest, most-representative strategy, because its job is to prove the architecture, not to be the best bet.** Applying that rule:

- **S1** is simpler but *degenerate*: no probability model, no shrinkage, no Kelly — it would exercise perhaps half the contract. It ships anyway, as a monitor (§3.6), so we lose nothing.
- **E1** has the strongest edge story but ~8 events/year means Stage 5 could run for months without a single trade — useless for proving a platform — and its data dependency (OD-13) is unverified.
- **W2** needs OD-12 verified live before it's worth building, and needs the intraday loop W1-v0 doesn't.
- **W1-v0** exercises *every* element of the contract — external data adapter, probability distribution → `p_model`, net-of-cost qualification, shrunk fractional-Kelly sizing with caps, intents, shadow forecasts, daily settlement feedback — in a few hundred lines of reviewable Python. **Stated tradeoff, honestly:** it is *not* the highest-confidence edge on this list, and its Gaussian is deliberately crude. We are optimizing for "the architecture demonstrably works and calibration data starts accumulating," not for paper P&L.

**Fast-follow order after Stage 5:** W2 (shares W1's data adapters; pending OD-12) → W1-v1 (same worker, better `p_model`) → E1 (pending OD-13) → others per live findings.

---

## 7. Open Decisions

**Resolved this session (user-ratified 2026-07-18):**
- **OD-6 → RESOLVED:** Weather-first category mix confirmed (§0).
- **OD-4 → RESOLVED:** 4-point net-edge floor; taker-at-quoted-price + 1¢/leg slippage allowance; no maker/mid fills in paper P&L (§2).
- **OD-7 → RESOLVED INTO DESIGN:** same-event brackets are one exposure; encoded in §5; Stage 3 must enforce it in the portfolio ledger, not only in workers.

**Carried forward (unchanged from Stage 1, still blocking where noted):**
- **OD-1 — Live fee schedule [verify before any live-data run]:** 0.07 taker assumed both legs; confirm per-series rates and rounding.
- **OD-2 — Liquidity/spread reality [verify]:** per-city spreads, depth, volume. Gate: spread cost ≤ 2¢ and depth supporting intended size, else the series is untradeable regardless of model quality.
- **OD-3 — Current listings [verify]:** which weather cities and admin-data series exist now.
- **OD-5 — API/paper environment [verify before Stage 3 assumes it]:** demo API availability, auth, rate limits. Paper-only stands regardless.

**New this session (conservative defaults chosen; do not silently change):**
- **OD-8 — Paper bankroll notional:** default **$1,000**. Pure bookkeeping choice; flag to user at next opportunity.
- **OD-9 — Sizing hyperparameters:** shrinkage **λ = 0.5**, Kelly multiplier **k = 0.25**. These stay fixed until shadow-forecast calibration data (§4.4d) justifies changing them — that evidence bar, not vibes, is the update rule.
- **OD-10 — Which cities first:** provisional **NYC (KNYC)** plus one more after OD-2 measurement picks the most liquid. σ and warming-curve archives are per-station work, so each added city has real cost.
- **OD-11 — Forecast-error archive [verify]:** confirm a usable archive of historical NWS forecasts vs. official climate-report outcomes (e.g., Iowa Environmental Mesonet) to estimate σ per station/lead-time; W1-v0's only hard data dependency beyond live feeds.
- **OD-12 — W2 precondition [verify live]:** do stale late-day quotes actually persist at tradeable size, and how often does the live METAR feed disagree with the official daily climate report? W2 is design-complete but build-blocked until measured.
- **OD-13 — E1 data path [verify]:** licensing/stability of a fed-funds-futures-implied probability source. E1 is build-blocked until resolved.

---

## 8. Handoff self-check (acceptance criteria)

**(1) Can the user explain "being right" vs. "sizing correctly"?** §1 gives the claim, a worked 100-trade example where identical 60% win rates produce +350% vs. −96.6% (over-sizing, not under-sizing, is the showcased failure), the Kelly derivation of *why*, and the miscalibration example showing why we shrink and cap. **Supported.**

**(2) Do we know which strategy is built first and why?** §6: **W1-v0**, chosen by the ratified tie-break — simplest strategy that exercises the entire worker contract with daily feedback — with the confidence-vs-representativeness tradeoff stated rather than hidden. **Supported.**

**(3) Can Session 3 design the orchestrator from this document alone?** §4 fixes lifecycle, inputs, outputs (incl. the five mandatory explanation fields and shadow forecasts), and the propose-then-approve boundary; §5 fixes the limits the orchestrator must enforce; the mechanical binding is explicitly deferred to Stage 3. **Supported.**

**Known gaps, stated plainly:** every liquidity/fee/archive figure is still unverified (OD-1/2/3/5/11/12/13); W2/E1 are design-complete but build-blocked on verification; σ = 3°F and all §5 defaults are placeholders pending data, and the shadow-forecast log is the designated mechanism for tuning them honestly.
