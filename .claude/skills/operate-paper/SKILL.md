---
name: operate-paper
description: Operate the running Apacenyë paper platform — startup checks, daily health review, incident response (kill events, pauses, staleness), and recovery. Use for "is it healthy", "why did it pause/kill", daily ops check-ins, or restarting after downtime.
---

# Operate the Paper Platform

The operating phase's product is DATA (capture + shadow forecasts), not P&L.
Paper P&L is an optimistic bound; treat it as a sanity signal only.

## Startup

1. `uv run apacenye status` — kill state clear? (If ACTIVE, that's an
   incident review, not a restart — see below.)
2. Ack valid? A strategy only STARTs with a PASSED paper ack for the current
   risk-relevant config hash; if risk.yaml changed, the owner re-runs
   `apacenye ack --strategy <id> --gate paper` — the gate is theirs to pass,
   never the session's.
3. `uv run apacenye serve` → confirm the boot lines: event resolved, N
   brackets tracked, "W1 start: started", dashboard URL.

## Daily health review (dashboard or API, ~5 minutes)

- **Strategies view**: every strategy in the expected state; heartbeat age
  seconds-fresh; day P&L vs. the −2% stop.
- **Signals view**: shadow evaluations flowing at the configured cadence
  (gaps = feed or worker trouble); intents' dispositions sane.
- **Gate-binding patterns** (`/api/intents`): frequent G5 resizes = our size
  assumptions exceed real depth (OD-2 evidence — record it); any G4 = data
  staleness worth chasing; G7/G8/G9 binding constantly = caps doing the work
  Kelly should have (check sizing inputs).
- **Alerts**: S1 bracket-coherence fires are BAD-FEED alarms first,
  opportunities second — verify the feed before celebrating.
- **Capture**: today's `data/capture/YYYY-MM-DD/` has book + nws_forecast
  (+ market) files that are growing. An uncaptured day is backtest data lost.

## Incidents

- **Kill switch tripped**: read `data/KILL` (source + reason) and
  `kill_events`. Positions stay in place by design — do not rush to flatten;
  they're fully collateralized. Diagnose first (G10 portfolio breach? manual?
  dashboard?). Recovery is deliberately two-step: `apacenye unkill` (typed
  confirmation), then resume each strategy individually — and only after the
  cause is understood and written down.
- **Strategy auto-PAUSEd** (−2% day or heartbeat silence): read the reason in
  alerts/log. A loss-pause warrants reviewing that day's explanations
  (`/api/explanations/{intent_id}`) before resuming — resume is a human
  decision, on purpose.
- **Human flatten during a kill**: allowed (reduce/close pass G1); use the
  dashboard buttons or a manual intent; worker proposals stay rejected.
- **Worker won't INIT** (data access): the adapter fails loudly — fix the
  feed, don't stub data.

## Weekly

- Kill-switch drill (server up): `apacenye kill "drill"` → confirm workers
  pause and resting orders cancel → `unkill` → resume. Log it.
- Backups run automatically inside `serve` (hourly → `~/apacenye-backups`,
  B-5); confirm recent snapshots exist. Off `serve`, or for an ad-hoc snapshot,
  run `apacenye backup`. Point `BACKUP_DIR` off-box once B-15 lands.
- `apacenye ack --verify-log` — chain intact.
- Skim disposition stats for the week; anything systematic goes to
  `triage-idea` as an ops/research item.
- Append an ops DEV_LOG entry only for notable events (incidents, drills
  with surprises, first-of-something) — quiet weeks don't need entries.
