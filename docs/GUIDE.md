# Apacenyë — Owner's Quick Reference

How to run this project day to day using the skill suite. Everything below
works by just *saying it* in a Claude Code session — the matching skill picks
it up and follows the written procedure.

## The loop

```
idea ──▶ backlog ──▶ build & ship ──▶ operate ──▶ evidence ──▶ (better model) ──▶ …
 triage-idea    dev-cycle        operate-paper  review-calibration
```

## Say this → that happens

| You say… | What runs | Result |
|---|---|---|
| "Should we build X?" / "Idea: …" | `triage-idea` | Honest verdict; good ideas land in BACKLOG.md, bad ones get told why |
| "Work on B-3" / "implement X" | `dev-cycle` | Tests first, built, verified, committed to main, journaled in DEV_LOG.md |
| "Add a strategy for X" | `add-strategy-worker` (inside dev-cycle) | One-pager first, then the worker, tests, ack gate |
| "Add city / data feed X" | `onboard-data-source` (inside dev-cycle) | Adapter + capture + catalog + staleness rules |
| "Is it healthy?" / "why did it pause?" | `operate-paper` | Health check or incident diagnosis with the runbook |
| "How's W1 actually doing?" / "can we raise λ?" | `review-calibration` | Calibration verdict; parameter changes only with your sign-off |
| "Backtest W1 last week" | `run-backtest` | Replay results, always labeled illustrative-only |
| "Is W1 ready for real money?" | `review-risk-pre-live` | Evidence review → the live gate → (in this bootstrap) the recorded refusal |

## What only you can do — Claude will not do these for you

- **Pass the quiz gates**: `apacenye ack --strategy W1 --gate paper`. No
  strategy trades (even on paper) until *you* pass it.
- **Ratify numbers**: bankroll, caps, loss stops, λ/k, edge floor. Sessions
  propose; you approve. Changing one invalidates your ack (you'll re-take it).
- **Un-kill**: `apacenye unkill` in a terminal, typed confirmation. The
  browser can stop the system but never restart it.
- **Review the money code**: `src/apacenye/domain/`, `risk_engine.py`,
  `ledger.py`, `paper.py`, `ack.py` — written to be read by you.

## Your routine

- **Daily (~5 min):** open the dashboard (`127.0.0.1:8642`) or ask "is it
  healthy?" — states, heartbeats, alerts, evaluations flowing, capture growing.
- **Weekly:** kill-switch drill, back up `data/`, ask for a calibration
  check once samples accumulate.
- **When you have an idea:** just say it — triage is cheap, and "no" comes
  with a reason.

## The three living docs

- **BACKLOG.md** — what's worth doing, what's blocked and why. Shipped items
  disappear (the journal is the record).
- **DEV_LOG.md** — append-only journal of every real change. Read it to catch
  up after time away.
- **docs/strategies/** — one page per strategy: thesis, worst case, what it
  depends on.

Formats live in `docs/SCHEMAS.md`; the skills keep them consistent so you
never have to.

## Standing safety facts (can't be changed by asking casually)

Live trading is hard-disabled twice — no order-sending code exists, and
`RUN_MODE=LIVE` refuses to boot. Enabling it requires a future dedicated
hardening session, weeks of evidence, and a fresh gate. Paper P&L is an
optimistic bound; backtests are illustrative-only. If anything looks wrong:
`apacenye kill "reason"` — it works even when everything else is down, and
your open positions can't lose more than you already paid for them.
