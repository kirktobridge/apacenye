# Stage 1 Handoff — Foundations & Market Research

**Project:** Apacenyë — personal Kalshi trading platform (PAPER-ONLY bootstrap)
**Written:** 2026-07-18 (Session 1 of 5)
**Audience:** Session 2 (Strategy Design), which starts with zero memory of this session. This document must be self-sufficient.
**Reader profile:** see `docs/initial-bootstrap/ABOUT_ME.md` — strong STEM/data background, Python-only fluency, minimal trading intuition. All later stages must keep money-touching logic in Python and remain paper-only.

**Data honesty note (applies to the whole document):** This session had **no live Kalshi access**. Every quantitative claim below is tagged either **[general knowledge]** (stable, verifiable from public documentation) or **[estimate — verify live]** (a specific figure a strategy would depend on; do not build on it until confirmed against live Kalshi data). All [estimate — verify live] items are collected in Open Decisions.

---

## 1. How Kalshi works, from first principles

### 1.1 The contract: a $1 bet on a yes/no question

Kalshi is a CFTC-regulated exchange for **binary event contracts** [general knowledge]. Each contract is a yes/no question with a precisely defined resolution rule, e.g. *"Will the high temperature at Central Park (station KNYC) on July 18 be 85°F or above?"*

- Every contract settles at exactly **$1.00** if the event happens (YES wins) or **$0.00** if it doesn't (NO wins).
- Before settlement, the contract trades at a price between 1¢ and 99¢.
- You can buy either side: a **YES contract** at price *P* pays $1 if the event occurs; a **NO contract** at price *(100¢ − P)* pays $1 if it doesn't. Buying NO *is* how you "short" YES — there is no separate shorting mechanism to learn.

**Key structural simplification vs. stocks:** positions are **fully collateralized**. When you buy a contract you pay your maximum possible loss up front (the price). There is no leverage, no margin call, no "losing more than you put in." This makes risk accounting in later stages genuinely simple: worst case on any position = what you paid for it.

### 1.2 Price = implied probability (the single most important idea)

Because the payoff is exactly $1-or-$0, the price *is* a probability statement. If YES trades at **37¢**, the market is collectively saying: *"the probability of this event is about 37%."*

The math, in expected-value terms you already know:

```
EV(buy YES at price P, true probability p) = p × $1.00 − P
```

- If *p* = 0.37 and *P* = $0.37 → EV = 0. The price is "fair."
- If you believe *p* = 0.45 and the market price is $0.37 → EV = +$0.08 per contract. That 8-cent (equivalently, 8-percentage-point) gap is your **edge**.

So a Kalshi price is exactly analogous to a calibrated classifier's output probability, and trading is a bet that **your estimator is better calibrated than the market's aggregate estimator.** Two distinct quantities matter and must never be conflated:

| Quantity | What it is | Who produces it |
|---|---|---|
| **Market-implied probability** | The trading price, read as a probability | The order book (everyone else) |
| **Model/true probability** | Your best estimate of the real chance | Your model — and it is an *estimate*, with its own error bars |

Everything Apacenyë does downstream reduces to: *estimate p better than the market, quantify how confident you are, and only trade when the gap exceeds all costs.*

**Multi-outcome events** are just sets of mutually exclusive binaries (e.g., "high temp: ≤79 / 80–84 / 85–89 / ≥90"). The YES prices across a complete, mutually exclusive set should sum to ~100¢. When they don't (beyond fee width), that's a structural mispricing — noted in §3.5 [general knowledge that such sets exist; frequency/size of violations is estimate — verify live].

### 1.3 The order book: bids, asks, and what the spread costs you

Kalshi runs a standard **central limit order book** per contract [general knowledge]. In data terms: two priority queues per side, sorted by price then time.

- **Bid:** the highest price someone will pay right now to buy.
- **Ask (offer):** the lowest price someone will accept right now to sell.
- The gap between them is the **bid–ask spread**.

Worked example — YES book for a temperature contract:

```
        ASKS (sellers)          BIDS (buyers)
        58¢ × 300 contracts     55¢ × 200 contracts
        60¢ × 500               53¢ × 400
```

- Want in *immediately*? You pay the ask: **58¢**. Want out immediately? You sell to the bid: **55¢**.
- Buy-then-immediately-sell loses 3¢ per contract with no market movement. **The spread is the price of impatience.** A "taker" (crossing the spread) pays it; a "maker" (posting a resting order and waiting to be filled) earns it — but risks never being filled, or being filled precisely when new information has moved the true probability against them (*adverse selection*: the resting order is a free option you've given to better-informed traders).
- **Slippage:** buying 500 contracts when only 300 are offered at 58¢ walks the book — 300 fill at 58¢, the next 200 at 60¢. Average cost rises with order size. This is why "liquidity" (book depth) directly caps how much capital a strategy can deploy.
- **Best probability estimate from a book:** the **midpoint** ((55+58)/2 = 56.5¢ → 56.5%) is a better read of market-implied probability than either quote alone.

One Kalshi-specific mechanic worth knowing [general knowledge]: YES and NO books are mirror images (a YES bid at 55¢ *is* a NO ask at 45¢), and the exchange can match a YES buyer at *P* with a NO buyer at *(100−P)* by minting a new contract pair. You don't need to model this specially; it just means "buy NO" and "sell YES" are interchangeable ways to express the same position.

### 1.4 Settlement

When the event's outcome is determined, Kalshi settles the contract per its **market rules** — a written specification naming the exact data source (e.g., a specific NWS climatological report for a named station, or a specific BLS release) [general knowledge]. Winners receive $1.00 per contract; losers receive $0. You can also exit early by selling at the current market price rather than waiting.

**Resolution clarity is a first-class strategy criterion:** a market that settles off one unambiguous published number is automatable; a market that requires interpreting words or intentions is not.

### 1.5 Fees, and the total round-trip cost (Stage 2 dependency)

Kalshi's published general trading-fee formula [general knowledge as of knowledge cutoff — exact current schedule is **estimate — verify live**, see Open Decision OD-1]:

```
fee = 0.07 × C × P × (1 − P)      (dollars; P = price in dollars, C = contracts)
```

rounded **up** to the next cent per order, charged on executed trades (taker executions; maker fees have historically been zero or reduced in most markets, and some series — notably certain index/sports series — have had different rates. **Verify the live schedule before Stage 2 locks thresholds.**). There is **no settlement fee**: holding to expiration incurs no exit fee [general knowledge — verify live].

Properties worth internalizing:

- The fee is proportional to `P(1−P)` — the *variance of a Bernoulli trial*. It's maximal at P = 50¢ (0.07 × 0.25 = **1.75¢/contract**) and shrinks toward the extremes (at P = 95¢: 0.07 × 0.0475 ≈ **0.33¢/contract**).
- Fees are therefore *worst exactly where uncertainty is highest*.

**Copy-pasteable cost model (Stage 2 must require every trade's net edge to exceed this):**

```python
import math

FEE_RATE = 0.07  # Kalshi general taker fee rate. OD-1: VERIFY against live
                 # fee schedule per series before any threshold is finalized.

def taker_fee(contracts: int, price: float, rate: float = FEE_RATE) -> float:
    """Fee in dollars for one executed order, rounded up to the next cent.
    price is in dollars (0.01–0.99)."""
    raw = rate * contracts * price * (1.0 - price)
    return math.ceil(raw * 100) / 100.0

def round_trip_cost_per_contract(entry_price: float,
                                 exit_price: float | None = None,
                                 spread_cost: float = 0.0,
                                 rate: float = FEE_RATE) -> float:
    """Total cost per contract in dollars (pre-rounding), to be beaten by edge.

    exit_price=None  -> held to settlement (no exit fee, no settlement fee).
    spread_cost      -> explicit slippage/spread charge in dollars per contract
                        (Stage 2 must state its assumption; see OD-4).
    """
    cost = rate * entry_price * (1.0 - entry_price)
    if exit_price is not None:
        cost += rate * exit_price * (1.0 - exit_price)
    return cost + spread_cost

def breakeven_probability(entry_price: float, rate: float = FEE_RATE,
                          spread_cost: float = 0.0) -> float:
    """Minimum TRUE probability at which buying YES at entry_price, held to
    settlement, has non-negative expected value."""
    return entry_price + rate * entry_price * (1.0 - entry_price) + spread_cost
```

**Worked numbers:**

| Scenario | Cost per contract |
|---|---|
| Buy at 50¢, hold to settlement | 0.07 × 0.25 = **1.75¢** (the worst case for hold-to-settle) |
| Buy at 60¢, sell at 70¢ before settlement | 0.07 × (0.24 + 0.21) = **3.15¢** |
| Buy at 90¢, hold to settlement | 0.07 × 0.09 = **0.63¢** |

Full worked trade: buy 100 YES at 57¢ → pay $57.00 + fee ceil(0.07×100×0.57×0.43) = $1.72 → total $58.72. If YES settles: receive $100.00, **profit $41.28**. Fee-adjusted breakeven: you need the true probability to exceed **58.72%**, not 57%. The market says 57%; you need to genuinely believe ≥ ~59% *before* accounting for spread — and you probably paid the ask, so the midpoint was lower still.

**The rule Stage 2 must encode:** a trade qualifies only if

```
model_probability − market_implied_probability  >  fees + spread/slippage + safety margin
```

with every term explicit. As a conservative starting default, pending live spread data: **require net edge ≥ 4 percentage points after fees and assumed spread** for taker entries (OD-4 — confirm/tune).

---

## 2. What "inefficiency" means here (the exploitability test)

A market is *inefficient in a way you can exploit* only if **all four** hold:

1. **A gap exists:** market-implied probability persistently deviates from the best achievable estimate of true probability.
2. **You can be on the right side of it:** there is public data + a modeling approach that produces a demonstrably better-calibrated estimate than the price. (A gap you can't out-model — e.g., prices moving on private information — is an inefficiency that exploits *you*.)
3. **The gap exceeds total cost:** fee + spread + slippage, per §1.5.
4. **There's a reason it persists:** someone is systematically wrong (retail anchoring on point forecasts, slow updating as new data arrives, structural inattention in sleepy markets) and the capital that *could* correct it is too small, too slow, or not present. Otherwise the edge evaporates as soon as you find it.

Corollary for strategy selection: **recurring markets beat one-offs.** A daily-resolving market gives hundreds of independent trials a year — enough to measure whether your model is actually calibrated (a data-science problem you're equipped for). A once-every-four-years market gives you one sample and no feedback loop.

---

## 3. Where a systematic edge is plausible (3–5 categories)

Ranked by fit for *this* project: free public data, recurring resolution, objective settlement, tolerance for non-HFT infrastructure, Python-friendliness.

### 3.1 Daily weather markets (high/low temperature at named stations) — **top recommendation**

Kalshi lists daily temperature markets for several US cities, settling on the official NWS climatological report for a specific station (e.g., Central Park KNYC) [general knowledge].

- **Data availability — excellent and free:** NWS/NOAA forecast API; hourly METAR observations *from the exact settlement station*; global model output (GFS/GEFS ensembles, and ECMWF open data) [general knowledge]. All consumable from Python with standard libraries.
- **Why an edge is plausible:** the right estimator is a *probability distribution* over the day's max temp, built from ensemble forecasts and updated intraday as actual observations arrive. Retail participants anchor on single-point forecasts ("the app says 84°") and systematically misprice the tails and the bracket boundaries. Late in the day the outcome becomes progressively *determined* (by mid-afternoon the running max already bounds the result), yet contracts can still trade at non-degenerate prices [estimate — verify live: whether stale late-day quotes actually persist and at what size].
- **Resolution clarity — excellent:** one published number from one named station. Zero interpretation.
- **Recurrence:** daily, per city → fast calibration feedback, many small independent-ish bets.
- **Honest caveats:** liquidity is modest [estimate — verify live: typical book depth and spreads per city]; other bots exist in these markets, so assume the *easy* mispricings are picked over; capacity is limited (fine for a personal paper platform); correlated across brackets of the same day/city (one weather outcome drives all of them — risk layer must treat same-event brackets as one exposure).

### 3.2 Fed rate decisions & economic indicators (FOMC target rate, CPI, payrolls)

- **Data availability — excellent:** for FOMC markets there is an *independent liquid venue pricing the same event*: CME fed funds futures, whose implied probabilities (popularized as "FedWatch") are publicly derivable [general knowledge]. For CPI, public nowcasts (e.g., the Cleveland Fed's inflation nowcast) have a strong published track record [general knowledge].
- **Why an edge is plausible:** a **cross-venue fair-value anchor**. When Kalshi's implied probability deviates from the futures-implied probability by more than total costs, that's a near-mechanical signal requiring no proprietary forecasting — only correct plumbing and arithmetic. Deviations occur because Kalshi's retail flow updates slower than professional futures markets [estimate — verify live: deviation frequency and magnitude].
- **Resolution clarity — excellent:** official Fed/BLS releases.
- **Liquidity:** these have historically been among Kalshi's deeper markets [estimate — verify live].
- **Honest caveats:** sophisticated participants are present, so persistent free gaps may be rare; events are monthly-ish → slow feedback loop; prices jump discontinuously at release time (you must be flat or sized-for-it going in). Best as the *second* category, added once plumbing exists.

### 3.3 Recurring administrative-data series (weekly gas prices via AAA, weekly jobless claims, similar)

- **Why an edge is plausible:** these settle on slow-moving *averages* published daily (e.g., AAA national average gas price). By mid-week, the weekly outcome is substantially determined by already-published daily readings — a pure nowcasting exercise where diligence beats attention-deficit retail flow. Structurally similar to the weather edge (outcome becomes progressively known; prices lag).
- **Data availability:** the settlement source itself publishes daily [general knowledge].
- **Resolution clarity:** excellent — settles on the named published number.
- **Honest caveats:** these books are likely thin [estimate — verify live], and Kalshi's listed series rotate — **verify which series currently exist** (OD-3). Treat as opportunistic additions, not the core.

### 3.4 Sports (cross-market fair value vs. sharp sportsbook lines) — plausible but *not* first

Kalshi's sports event contracts (launched 2025) carry large volume [general knowledge; magnitudes estimate — verify live].

- **Why plausible:** sharp sportsbooks' closing lines are among the best-calibrated public probability estimates in existence; Kalshi retail flow can deviate from them. Fair value is imported rather than modeled.
- **Why not first:** the edge decays in minutes (speed competition); sharp-line data access/licensing is a real dependency; fee schedules on sports series may differ (OD-1); and it teaches the least about building forecasting infrastructure. Revisit after the platform exists.

### 3.5 Cross-cutting: bracket-coherence checks (structural, not a category)

For any mutually exclusive bracket set, `sum(YES asks) < $1 − total fees` (buy the whole set) or `sum(YES bids) > $1 + total fees` (sell the whole set) is a near-riskless structural arbitrage. Rare and small [estimate — verify live], but the *check* is nearly free once market data is flowing, and doubles as a data-sanity monitor. Stage 2 may specify it as a micro-strategy or a monitoring feature.

---

## 4. Categories to explicitly avoid (and why)

### 4.1 Elections & politics
One-shot events (no calibration feedback loop); months-long capital lockup with fee-adjusted returns diluted by time; prices driven by sentiment and narrative, where documented episodes in prediction markets include large actors trading to *shape perception* rather than to profit — i.e., the "inefficiency" you see may be someone's information or someone's manipulation, and you can't tell which; resolution can turn on contested interpretation. Fails exploitability tests #2 and #4, and partially #1 (the gap may be *right*).

### 4.2 One-off novelty / pop-culture markets (awards, "will X say Y", Rotten Tomatoes scores, celebrity events)
Severe insider-information asymmetry (people adjacent to the outcome trade on private knowledge — the classic adverse-selection trap for a model-based trader); thin books; no recurring data-generating process to model; each market is bespoke, so nothing automates. The opposite of systematic.

### 4.3 Intraday financial index & crypto price markets (S&P/Nasdaq/BTC ranges by date/hour)
Here Kalshi prices are tightly coupled to deeply liquid underlying markets (index futures, spot crypto) and are kept in line by latency-sensitive professionals. Any gap a Python bot on home infrastructure can see is either already gone or is adverse selection. After the fee (worst near 50¢, which is where these trade), expected edge is negative. This is the one category where the *counterparty* is systematically sharper than you — the reverse of weather.

---

## 5. Recommendation for Stage 2

**Pursue first: daily weather (temperature) markets.** Rationale: entirely free public data including the exact settlement source; daily recurrence → fast, statistically meaningful calibration feedback (plays to the user's data-science strengths); objective settlement; minutes-scale (not milliseconds-scale) tempo tolerant of simple Python infrastructure; naturally bounded position sizes suited to paper trading honestly.

**Second (design for, build later): FOMC/CPI cross-venue fair value** — near-mechanical signal, deep books, teaches the cross-market-anchor pattern.

**Keep on the shelf:** administrative-data nowcasting (3.3) and bracket-coherence checks (3.5) as low-cost add-ons; sports (3.4) deferred.

This is a recommendation, not a user-confirmed decision — see OD-6.

---

## 6. Open Decisions (Stage 2 starting questions)

- **OD-1 — Live fee schedule [verify against live data before use].** Confirm the current general fee rate (0.07 assumed), rounding rule, maker-fee status, and any per-series differences (index and sports series have historically differed). Every Stage 2 threshold depends on this. *Conservative default until verified: assume 0.07 taker on both legs and zero maker rebate.*
- **OD-2 — Liquidity/spread reality check [verify live].** For each candidate series (each weather city; FOMC; CPI): typical bid–ask spread, top-of-book depth, and daily volume. Proposed gate: only trade series where crossing the spread costs ≤ 2¢ and top-of-book depth supports intended size; quantify from live observation.
- **OD-3 — Which series currently exist [verify live].** Confirm current listings for weather cities and administrative-data series (gas prices, jobless claims); Kalshi rotates listings.
- **OD-4 — Slippage assumption & minimum net edge.** Proposed conservative defaults: model every entry as a taker fill at the full quoted spread (never assume mid or maker fills in paper P&L), and require **net edge ≥ 4 percentage points** after fees + spread. Stage 2 must state its assumption explicitly; user has not confirmed the 4-point figure.
- **OD-5 — API access & paper environment [verify live].** Kalshi has offered a demo/paper API environment [general knowledge, current status unverified]. Confirm availability, auth model, and rate limits before Stage 3 architecture assumes it. Paper-only constraint (ABOUT_ME) stands regardless: live enablement stays hard-disabled this bootstrap.
- **OD-6 — Category confirmation (user decision, explicitly deferred).** Weather-first is Stage 1's recommendation with reasoning in §5. The user was asked in-session and chose to **defer ratification to Stage 2**: proceed with weather as the provisional design target, and Stage 2 MUST put the choice to the user at session start (with its deeper strategy analysis in hand) before ranking strategies around it.
- **OD-7 — Same-event correlation handling.** Brackets on the same event (same day/city temperature set) are one exposure, not several. Flagging now so Stage 2's risk limits and Stage 3's portfolio risk layer treat them as such.

---

## 7. Handoff self-check (acceptance criteria)

**(1) How is contract price related to probability?** Answerable from §1.2: price in cents = market-implied probability in percent, derived from the $1/$0 payoff via the EV identity `EV = p·$1 − P`; midpoint of bid/ask is the better estimator (§1.3). **Fully supported.**

**(2) What makes a market "inefficient" in a way I could exploit?** Answerable from §2: a persistent gap between market-implied and best-estimate true probability, that *you* can be on the right side of with public data, that exceeds total transaction cost (§1.5 formula), and that persists for an identifiable structural reason. §§3–4 apply the test positively and negatively. **Fully supported.**

**(3) Which category first, and why?** Answerable from §5: daily weather markets — free settlement-source data, daily calibration feedback, objective resolution, latency-tolerant, Python-friendly. **Supported as a recommendation; user ratification pending (OD-6)** — flagged rather than resolved silently, per the unattended-session protocol.

**Known gaps, stated plainly:** all liquidity/volume/spread figures and the current fee schedule are unverified estimates (OD-1/OD-2/OD-3); no strategy math in Stage 2 may treat them as facts until confirmed live.
