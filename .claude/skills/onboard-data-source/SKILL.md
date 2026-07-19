---
name: onboard-data-source
description: Onboard a new market's external data source into Apacenyë — data adapter, capture channel, staleness rules, ticker→event catalog, and liquidity gate. Use when adding a new city, a new market category, or any new external data feed (NWS, METAR, futures, AAA, etc.).
---

# Onboard a New Market's Data Source

A market is only tradeable if its data is honest: sourced as close to the **settlement source** as possible, timestamped, captured for replay, and staleness-guarded. Read `stage1-foundations.md` §1.4 (resolution clarity) and `stage3-architecture.md` §2, §9, §10 first.

## Steps

1. **Identify the settlement source, then prefer it.** Read the Kalshi market rules for the series: which exact published number settles it (e.g., the NWS climate report for station KNYC)? The best signal feed is the settlement source itself or the nearest upstream observable (W2 reads METAR from the exact settlement station). A market that settles on interpretation rather than one published number fails the automatability bar — flag it and stop.
2. **Verify access before building**: is the feed free, keyless or key-based, what are its rate limits and terms? If it needs a credential, the key goes in `.env` (add a placeholder + comment to `.env.example`), the value never appears in source/YAML/logs, and the config field is marked secret so serialization redacts it.
3. **Write the adapter** in `src/apacenye/dataadapters/<source>.py`, matching the existing adapters' interface (async `httpx`; retries/backoff per the source's limits). Non-negotiables: every returned datum carries its **source timestamp** (publication/observation time, not fetch time) so it can flow into `key_inputs`; adapter failures raise loudly rather than returning stale data silently.
4. **Register a capture channel** with the capture writer so the feed is recorded from day one to `data/capture/YYYY-MM-DD/<channel>.jsonl.gz` — replay backtesting is only possible for data we captured; an uncaptured feed is future backtest data thrown away.
5. **Define the staleness window** in the consuming strategy's config (precedents: W1 forecast ≤ 12 h, W2 station ob ≤ 75 min) — pick from the source's actual update cadence, conservatively. The G4 gate and the worker's own checks both use it.
6. **Extend the market catalog** (`src/apacenye/marketdata/catalog.py`): ticker→event mapping for the new series, so G7 can aggregate all brackets of one settlement event as one exposure. This mapping is orchestrator-owned truth — never derived from worker claims. Contract-mapping is where the bugs live (Stage 2 §3.3): verify the market's exact resolution terms against what the adapter measures (station, timezone, rounding, revision policy).
7. **Cover it with the S1 monitor**: if the series has complete mutually-exclusive bracket sets, ensure the bracket-coherence monitor watches them — a "violation" is a bad-feed alarm first, an opportunity second.
8. **Measure liquidity before any strategy trades it** (OD-2 gate): observe typical spread, top-of-book depth, and volume from live read-only data. Gate: spread cost ≤ 2¢ and depth supporting intended size, else the series is untradeable regardless of model quality — record the measurement.
9. **Test with recorded fixtures**: save real captured responses as test fixtures; unit-test parsing, timestamp extraction, timezone/DST handling (bit W2's design already), and the failure modes (missing observations, revised values, schema drift).

## Verified After Scaffolding (Stage 5, 2026-07-19)

- [x] Adapter home `src/apacenye/dataadapters/`. Interface: **no base class** — duck-typed classes (`NwsForecastAdapter`, `MetarAdapter`) exposing `async fetch_*()` methods that return frozen dataclasses carrying `source_ts` (publication time) AND `fetched_ts`; failures raise adapter-specific errors loudly. Match this shape; workers take the adapter via constructor injection (replay substitutes a canned one).
- [x] Capture registration: pass the `CaptureWriter` into the adapter constructor (see `NwsForecastAdapter(capture=...)`) or call `capture.write(channel, payload, ticker=|station=, ts=)` directly. Real channels: `book`, `trade`, `settlement`, `nws_forecast`, `metar`, `market` (catalog metadata — REQUIRED for replay to know bracket bounds; `feed.load_event_markets` writes it).
- [x] Catalog: `src/apacenye/marketdata/catalog.py`, code-declared via `add_from_kalshi_market(market_dict)`. **Bracket-bound semantics verified live (KXHIGHNY, 2026-07-19): floor+cap = inclusive range; floor-only = STRICTLY greater (lo = floor+1); cap-only = STRICTLY less (hi = cap−1).** Re-verify these edge semantics per new series — an off-by-one misprices every tail.
- [x] S1 discovers bracket sets automatically from the catalog (`brackets_of_event`); no explicit registration. It alerts only when EVERY bracket has a two-sided quote.
- [x] Staleness windows: strategy YAML key `staleness_s` (e.g. W1 `43200`); G4 reads it via the orchestrator and checks every `*_ts` key in `key_inputs` against it. One window per strategy in v0.
- [x] `.env` convention: `<SOURCE>_API_KEY_ID` / `<SOURCE>_..._PATH` style (see `KALSHI_*`); add the field to `AppSettings` as `SecretStr` (redaction is automatic) and a placeholder to `.env.example`.
- [x] Rate limiting: `_RateLimiter` (async token bucket) lives in `execution/kalshi.py` alongside its backoff-on-429 `_get`; for a new rate-limited source, lift that pattern (or import the class) rather than reimplementing. NWS needs only a polite `User-Agent` and modest poll cadence.
