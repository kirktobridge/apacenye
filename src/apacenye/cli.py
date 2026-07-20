"""Apacenyë CLI — `serve` plus the out-of-band admin commands.

kill / unkill / status work with the server DOWN (they touch only the
sentinel file and read-only SQLite) — that is the point of them (Stage 3 §5).
Un-kill and live-enable exist ONLY here, never over HTTP.

Commands:
  apacenye serve                          run the platform (PAPER by default)
  apacenye kill "reason"                  trip the kill switch, out-of-band
  apacenye unkill                         clear it (requires typing RESUME TRADING)
  apacenye ack --strategy W1 --gate paper|live    concept checkpoint
  apacenye ack --verify-log               verify the hash chain
  apacenye enable-live --strategy W1      full live gate → always refused (bootstrap)
  apacenye status                         kill state, equity, positions, heartbeats
  apacenye backtest --strategy W1 --from 2026-07-18 --to 2026-07-20
  apacenye calibration --strategy W1 [--since D --until D] [--json] [--backfill-settlements]
  apacenye backup                         one out-of-tree ledger+capture snapshot
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path

log = logging.getLogger("apacenye")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="apacenye",
                                description="Apacenyë — paper-only Kalshi platform")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("serve", help="run orchestrator + dashboard")

    k = sub.add_parser("kill", help="trip the kill switch (works with server down)")
    k.add_argument("reason", nargs="?", default="manual CLI kill")

    sub.add_parser("unkill", help="clear the kill switch (typed confirmation)")

    a = sub.add_parser("ack", help="concept checkpoint")
    a.add_argument("--strategy", default=None)
    a.add_argument("--gate", choices=["paper", "live"], default=None)
    a.add_argument("--verify-log", action="store_true")

    e = sub.add_parser("enable-live", help="request live enablement (always refused in bootstrap)")
    e.add_argument("--strategy", required=True)

    sub.add_parser("status", help="kill state, equity, positions (read-only)")

    b = sub.add_parser("backtest", help="replay backtest (illustrative-only)")
    b.add_argument("--strategy", default="W1")
    b.add_argument("--from", dest="from_day", required=True)
    b.add_argument("--to", dest="to_day", required=True)

    c = sub.add_parser("calibration", help="shadow-forecast calibration report (read-only)")
    c.add_argument("--strategy", default="W1")
    c.add_argument("--since", default=None, help="inclusive start date YYYY-MM-DD (UTC)")
    c.add_argument("--until", default=None, help="inclusive end date YYYY-MM-DD (UTC)")
    c.add_argument("--json", action="store_true", help="machine-readable output")
    c.add_argument("--backfill-settlements", action="store_true",
                   help="mark positionless evaluated markets settled from the "
                        "read-only Kalshi API before reporting (network access)")

    sub.add_parser("backup", help="write one out-of-tree ledger+capture snapshot")
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = _build_parser().parse_args(argv)
    return {
        "serve": cmd_serve,
        "kill": cmd_kill,
        "unkill": cmd_unkill,
        "ack": cmd_ack,
        "enable-live": cmd_enable_live,
        "status": cmd_status,
        "backtest": cmd_backtest,
        "calibration": cmd_calibration,
        "backup": cmd_backup,
    }[args.command](args)


# --------------------------------------------------------------- kill/unkill


def cmd_kill(args) -> int:
    from apacenye.config import AppSettings
    from apacenye.orchestrator.kill import KillSwitch

    settings = AppSettings()
    kill = KillSwitch(settings.kill_sentinel_path)
    kill.trip("cli", args.reason)
    print(f"KILL sentinel written: {kill.path} — {args.reason}")
    # best-effort ledger note; the FILE is the authority, DB may be locked/absent
    _log_kill_event(settings.db_path, "kill", args.reason)
    return 0


def _log_kill_event(db_path, kind: str, reason: str) -> None:
    if not Path(db_path).exists():  # never CREATE a db from the kill path
        print("(ledger note skipped — sentinel file is the authority)")
        return
    try:
        con = sqlite3.connect(db_path, timeout=2)
        con.execute("INSERT INTO kill_events (ts, kind, source, reason) "
                    "VALUES (datetime('now'), ?, 'cli', ?)", (kind, reason))
        con.commit()
        con.close()
    except sqlite3.Error:
        print("(ledger note skipped — sentinel file is the authority)")


def cmd_unkill(args) -> int:
    from apacenye.config import AppSettings
    from apacenye.orchestrator.kill import KillSwitch

    settings = AppSettings()
    kill = KillSwitch(settings.kill_sentinel_path)
    if not kill.is_killed():
        print("kill switch is not active")
        return 0
    print(f"Kill state: {json.dumps(kill.read_state())}")
    print("Un-kill leaves every strategy PAUSED; each must be resumed "
          "individually (and needs a valid paper ack).")
    typed = input('Type exactly "RESUME TRADING" to clear the kill switch:\n> ')
    if typed.strip() != "RESUME TRADING":
        print("confirmation mismatch; kill switch remains active")
        return 1
    kill.clear()
    print("kill switch cleared")
    _log_kill_event(settings.db_path, "unkill", "typed confirmation")
    return 0


# ---------------------------------------------------------------- checkpoint


def cmd_ack(args) -> int:
    from apacenye.checkpoint.ack import AckLog, run_gate
    from apacenye.config import AppSettings, load_risk_config

    settings = AppSettings()
    ack_log = AckLog(settings.ack_log_path)
    if args.verify_log:
        ok, msg = ack_log.verify()
        print(msg)
        return 0 if ok else 1
    if not args.strategy or not args.gate:
        print("usage: apacenye ack --strategy <id> --gate paper|live "
              "(or: apacenye ack --verify-log)")
        return 2
    risk = load_risk_config()
    record = run_gate(args.strategy, args.gate, risk, ack_log)
    return 0 if record["result"] == "PASSED" else 1


def cmd_enable_live(args) -> int:
    """The Stage 3 §11.2 live trigger: run the FULL live gate; in this
    bootstrap the flow always terminates at the hard-disable wall and the
    refusal is recorded in the ack log."""
    from apacenye.checkpoint.ack import AckLog, run_gate
    from apacenye.config import AppSettings, load_risk_config
    from apacenye.execution.live import LiveDisabledError

    settings = AppSettings()
    risk = load_risk_config()
    record = run_gate(args.strategy, "live", risk, AckLog(settings.ack_log_path))
    if record["result"] != "PASSED":
        print("live gate not passed; nothing further")
        return 1
    # The wall — reached even after a passed checkpoint, always:
    try:
        raise LiveDisabledError()
    except LiveDisabledError as exc:
        print(f"\n{exc}")
    return 1


# -------------------------------------------------------------------- status


def cmd_status(args) -> int:
    from apacenye.config import AppSettings
    from apacenye.orchestrator.kill import KillSwitch

    settings = AppSettings()
    kill = KillSwitch(settings.kill_sentinel_path)
    print(f"run mode (from .env): {settings.run_mode.value}")
    print(f"kill switch: {'ACTIVE ' + json.dumps(kill.read_state()) if kill.is_killed() else 'clear'}")
    if not Path(settings.db_path).exists():
        print("ledger: no database yet (server never ran)")
        return 0
    con = sqlite3.connect(f"file:{settings.db_path}?mode=ro", uri=True, timeout=2)
    con.row_factory = sqlite3.Row
    try:
        tables = {r["name"] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "realizations" not in tables:
            print("ledger: database present but uninitialized")
            return 0
        realized = con.execute(
            "SELECT COALESCE(SUM(amount_dollars), 0) s FROM realizations").fetchone()["s"]
        print(f"equity: ${1000.0 + realized:.2f} (initial $1000 + realized "
              f"${realized:.2f}; paper — optimistic bound)")
        print("open positions:")
        rows = con.execute("SELECT strategy_id, market_ticker, side, count, "
                           "cost_basis_dollars FROM positions WHERE status='open'").fetchall()
        for r in rows:
            print(f"  {r['strategy_id']} {r['market_ticker']} {r['side']} "
                  f"×{r['count']} cost ${r['cost_basis_dollars']:.2f}")
        if not rows:
            print("  (none)")
        print("latest heartbeats:")
        for r in con.execute(
            "SELECT strategy_id, MAX(ts) ts, state FROM heartbeats GROUP BY strategy_id"
        ).fetchall():
            print(f"  {r['strategy_id']}: {r['state']} at {r['ts']}")
    finally:
        con.close()
    return 0


# ------------------------------------------------------------------ backtest


def cmd_backtest(args) -> int:
    from apacenye.backtest.replay import run_replay
    from apacenye.config import AppSettings, load_risk_config, load_strategy_config

    settings = AppSettings()
    risk = load_risk_config()
    strategy_config = load_strategy_config(args.strategy)
    import tempfile

    with tempfile.TemporaryDirectory(prefix="apacenye-replay-") as work:
        result = asyncio.run(run_replay(
            strategy_config, risk, settings.capture_dir,
            args.from_day, args.to_day, work, strategy_id=args.strategy,
        ))
    print("=" * 72)
    print(result["label"])  # the honesty rule attaches at the source
    print("=" * 72)
    for key in ("days", "warnings", "evaluations", "qualified", "scored_samples",
                "realized_pnl_dollars", "open_positions_at_end",
                "brier_model", "brier_market"):
        if key in result:
            print(f"{key}: {result[key]}")
    if result.get("brier_model") is not None and result.get("brier_market") is not None:
        better = "model" if result["brier_model"] < result["brier_market"] else "market"
        print(f"calibration: {better} had the lower (better) Brier score on this window")
    print("Reminder: calibration before P&L; good P&L on a handful of samples is noise.")
    return 0


# ----------------------------------------------------------------- calibration


def cmd_calibration(args) -> int:
    """Owner-readable shadow-forecast calibration report (review-calibration
    skill, steps 2–6) — Brier vs. the market benchmark, a reliability decile
    table, the qualified/traded subset check, and a mechanical verdict.

    Read-only against the ledger (safe alongside a running server; WAL). This
    tool REPORTS only — it never recommends λ/k/σ (OD-9). With
    --backfill-settlements it first marks positionless evaluated markets
    settled from the read-only Kalshi API so late outcomes become scoreable.
    """
    from apacenye.config import AppSettings, load_risk_config
    from apacenye.domain.calibration import ScoredEval, build_report
    from apacenye.orchestrator.ledger import Ledger

    settings = AppSettings()
    if not Path(settings.db_path).exists():
        print("no ledger yet — nothing to calibrate (server never ran)")
        return 0
    risk = load_risk_config()
    ledger = Ledger(settings.db_path, risk.bankroll_usd)
    try:
        if args.backfill_settlements:
            asyncio.run(_backfill_settlements(settings, ledger, args.strategy))
        cov = ledger.evaluation_coverage(args.strategy)
        rows = ledger.settled_evaluations(args.strategy, args.since, args.until)
        scored = [ScoredEval(
            model_probability=r["model_probability"],
            market_implied_probability=r["market_implied_probability"],
            outcome=r["outcome"], qualified=bool(r["qualified"]),
            traded=r["intent_id"] is not None, event_ticker=r["event_ticker"],
        ) for r in rows]
        report = build_report(args.strategy, scored)
        if args.json:
            print(json.dumps(
                _calibration_json(str(settings.db_path), args.since, args.until, cov, report),
                indent=2))
        else:
            print(_calibration_text(str(settings.db_path), args.since, args.until, cov, report))
    finally:
        ledger.close()
    return 0


async def _backfill_settlements(settings, ledger, strategy_id: str) -> None:
    """Mark positionless evaluated markets settled from the read-only Kalshi
    API (D4). Markets with OPEN positions that appear settled venue-side are
    LISTED for attention, never marked here — position realization stays on
    the server's on_settlement path."""
    from apacenye.contract import Side
    from apacenye.execution.kalshi import KalshiClient

    pending = ledger.unsettled_evaluated_markets(strategy_id)
    if not pending:
        print("backfill: no unsettled evaluated markets — nothing to do")
        return
    kalshi = KalshiClient(
        api_key_id=settings.kalshi_api_key_id.get_secret_value(),
        private_key_path=settings.kalshi_private_key_path,
        env=settings.kalshi_env,
    )
    marked = 0
    still_open = 0
    needs_attention: list[tuple[str, str]] = []
    try:
        for m in pending:
            ticker = m["market_ticker"]
            try:
                data = await kalshi.get_market(ticker)
            except Exception as exc:  # a single bad ticker must not abort the run
                print(f"  {ticker}: venue query failed ({exc})")
                continue
            result = (data.get("market", {}).get("result") or "").lower()
            if result not in ("yes", "no"):
                still_open += 1  # not settled venue-side yet — leave it open
                continue
            if ledger.market_has_open_position(ticker):
                needs_attention.append((ticker, result))
                continue
            if ledger.mark_market_settled(
                ticker, Side.YES if result == "yes" else Side.NO):
                marked += 1
    finally:
        await kalshi.close()
    print(f"backfill: marked {marked} positionless market(s) settled; "
          f"{still_open} still open venue-side")
    for ticker, result in needs_attention:
        print(f"  ATTENTION: {ticker} settled '{result}' venue-side but has OPEN "
              "positions — resolve via a running serve (on_settlement realizes "
              "positions and cancels resting orders); NOT marked here")


def _fmt(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.4f}"


def _calibration_text(db_path, since, until, cov, r) -> str:
    """Render the report as plain text — the sections the review-calibration
    skill quotes into DEV_LOG, in order."""
    from apacenye.domain.calibration import CALIBRATION_BIAS_THRESHOLD, INSUFFICIENT_DATA_ROWS

    bar = "=" * 72
    out = [bar,
           f"CALIBRATION REPORT — strategy {r.strategy_id}  (PAPER shadow forecasts)",
           bar,
           f"ledger: {db_path}",
           f"window: {since or 'start'} … {until or 'now'}  (UTC evaluation dates, inclusive)",
           f"coverage (lifetime): {cov['total']} evaluations — {cov['settled']} settled/scoreable, "
           f"{cov['unsettled']} still open, {cov['settled_null_mid']} settled dropped for no "
           "two-sided quote"]
    if cov["unsettled"]:
        out.append(f"  NOTE: {cov['unsettled']} evaluated market(s) still 'open' in the ledger — "
                   "if settled while serve was down, run --backfill-settlements to score them.")
    out += ["",
            f"[sample] scoreable rows in window: {r.n_scored}   distinct events (effective n): "
            f"{r.n_events}   null-mid dropped in window: {r.n_excluded_null_mid}"]
    if r.insufficient_data:
        out.append(f"  INSUFFICIENT-DATA (< {INSUFFICIENT_DATA_ROWS} rows): the metrics below are "
                   "directionally interesting, evidentially nothing.")
    out += ["",
            "[brier] mean (p − outcome)², lower is better; the market mid is the benchmark to beat",
            f"  model : {_fmt(r.brier_model)}",
            f"  market: {_fmt(r.brier_market)}"]
    if r.brier_model is not None and r.brier_market is not None:
        rel = ("beats" if r.brier_model < r.brier_market
               else "ties" if r.brier_model == r.brier_market else "LOSES to")
        out.append(f"  -> model {rel} the market benchmark")
    out += ["",
            "[reliability] p_model deciles — calibrated when observed ≈ mean_pred",
            "  bucket       n    mean_pred  observed   gap"]
    for b in r.reliability:
        if b.n == 0:
            out.append(f"  {b.lo:.1f}-{b.hi:.1f}      0        —         —        —")
        else:
            out.append(f"  {b.lo:.1f}-{b.hi:.1f}  {b.n:5d}    {b.mean_pred:7.3f}   "
                       f"{b.observed_freq:7.3f}  {b.gap:+.3f}")
    if r.weighted_gap is not None:
        out.append(f"  aggregate gap (observed − predicted): {r.weighted_gap:+.3f}   "
                   f"(|gap| > {CALIBRATION_BIAS_THRESHOLD} is flagged)")
        out.append("  note: tail over-confidence (extreme buckets drifting toward 0.5) is W1-v0's "
                   "expected failure mode — watch the 0.0-0.1 and 0.9-1.0 rows.")
    out += ["",
            "[selection] does the qualification rule pick BETTER spots than it skips?"]
    for s in (r.qualified, r.traded, r.untraded):
        out.append(f"  {s.label:9s} n={s.n_rows:4d} events={s.n_events:3d}   "
                   f"brier_model={_fmt(s.brier_model)}   brier_market={_fmt(s.brier_market)}")
    if r.adverse_selection:
        out.append("  ADVERSE-SELECTION WARNING: the TRADED subset scores WORSE than the untraded "
                   "— the qualification rule is selecting bad spots.")
    out += ["",
            f"VERDICT: {r.verdict}",
            f"  (rule: < {INSUFFICIENT_DATA_ROWS} rows ⇒ insufficient-data; model Brier > market "
            f"⇒ loses-to-market; |aggregate gap| > {CALIBRATION_BIAS_THRESHOLD} ⇒ "
            "over/under-forecasting; else calibrated)",
            "",
            "Reports only. λ/k/σ changes go through review-calibration → owner ratification → "
            "dev-cycle (OD-9); never tuned here."]
    return "\n".join(out)


def _calibration_json(db_path, since, until, cov, r) -> dict:
    return {
        "strategy_id": r.strategy_id,
        "provenance": {"ledger": db_path, "mode": "PAPER shadow forecasts",
                       "since": since, "until": until},
        "coverage": cov,
        "sample": {"scoreable_rows": r.n_scored, "distinct_events": r.n_events,
                   "null_mid_dropped_in_window": r.n_excluded_null_mid,
                   "insufficient_data": r.insufficient_data},
        "brier": {"model": r.brier_model, "market": r.brier_market},
        "weighted_gap": r.weighted_gap,
        "reliability": [asdict(b) for b in r.reliability],
        "subsets": {"qualified": asdict(r.qualified), "traded": asdict(r.traded),
                    "untraded": asdict(r.untraded)},
        "adverse_selection": r.adverse_selection,
        "verdict": r.verdict,
    }


# -------------------------------------------------------------------- backup


def cmd_backup(args) -> int:
    """Write one out-of-tree snapshot of the ledger + capture tree (B-5).
    Works alongside a running serve (SQLite online backup) or with it down."""
    from apacenye.backup import create_backup, prune_backups
    from apacenye.config import AppSettings

    settings = AppSettings()
    dest = create_backup(settings.db_path, settings.capture_dir, settings.backup_dir)
    print(f"backup written: {dest}")
    print(f"  ledger : {'included' if Path(settings.db_path).exists() else 'no db yet'}")
    print(f"  capture: {'included' if Path(settings.capture_dir).exists() else 'none yet'}")
    removed = prune_backups(settings.backup_dir, settings.backup_retention)
    if removed:
        print(f"  pruned {len(removed)} old snapshot(s) beyond "
              f"retention={settings.backup_retention}")
    return 0


# --------------------------------------------------------------------- serve


def cmd_serve(args) -> int:
    from apacenye.config import AppSettings, load_risk_config, load_strategy_config

    settings = AppSettings()  # refuses to boot on RUN_MODE=LIVE (wall #1)
    settings.validate_dashboard_binding()
    risk = load_risk_config()
    print(f"Apacenyë starting — mode {settings.run_mode.value}, "
          f"bankroll ${risk.bankroll_usd:.0f} (paper)")
    try:
        asyncio.run(_serve(settings, risk))
    except KeyboardInterrupt:
        print("shutdown")
    return 0


async def _serve(settings, risk) -> None:
    import uvicorn

    from apacenye.backtest.capture import CaptureWriter
    from apacenye.config import load_strategy_config
    from apacenye.dataadapters.metar import MetarAdapter
    from apacenye.dataadapters.nws import NwsForecastAdapter
    from apacenye.execution.kalshi import KalshiClient
    from apacenye.marketdata.catalog import MarketCatalog
    from apacenye.marketdata.feed import MarketDataService
    from apacenye.marketdata.snapshots import SnapshotCache
    from apacenye.orchestrator.kill import KillSwitch
    from apacenye.orchestrator.ledger import Ledger
    from apacenye.orchestrator.orchestrator import Orchestrator
    from apacenye.scheduler import TickScheduler
    from apacenye.service.api import create_app
    from apacenye.service.ws import WsHub
    from apacenye.workers.w1_forecast import W1ForecastWorker

    ledger = Ledger(settings.db_path, risk.bankroll_usd)
    kill = KillSwitch(settings.kill_sentinel_path)
    cache = SnapshotCache()
    catalog = MarketCatalog()
    scheduler = TickScheduler()
    ws_hub = WsHub()
    capture = CaptureWriter(settings.capture_dir)
    orch = Orchestrator(settings, risk, ledger, kill, cache, catalog, scheduler,
                        ws_hub=ws_hub)

    kalshi = KalshiClient(
        api_key_id=settings.kalshi_api_key_id.get_secret_value(),
        private_key_path=settings.kalshi_private_key_path,
        env=settings.kalshi_env,
    )
    feed = MarketDataService(
        kalshi, cache, catalog, capture=capture,
        on_settlement=orch.on_settlement,
        on_alert=lambda a: ws_hub.broadcast("alerts", a),
    )

    # --- W1 wiring: resolve today's event in the configured series ---------
    w1_config = load_strategy_config("W1")
    series = w1_config.get("series_ticker")
    event_ticker = w1_config.get("event_ticker")
    if series and not event_ticker:
        event_ticker = await _resolve_soonest_event(kalshi, series)
        if event_ticker:
            w1_config["event_ticker"] = event_ticker
            print(f"W1: resolved today's event in {series}: {event_ticker}")
    if event_ticker:
        tickers = await feed.load_event_markets(event_ticker)
        for t in tickers:
            info = catalog.get(t)
            ledger.upsert_market(t, info.event_ticker, info.bracket_lo, info.bracket_hi)
        print(f"W1: tracking {len(tickers)} brackets of {event_ticker}")
    else:
        print("W1: no event resolved — worker will INIT but find no brackets")

    adapter = NwsForecastAdapter(
        station=w1_config["station"], grid_office=w1_config["grid_office"],
        grid_x=int(w1_config["grid_x"]), grid_y=int(w1_config["grid_y"]),
        capture=capture,
    )
    worker = W1ForecastWorker("W1", w1_config, orch.make_context(), adapter=adapter)

    # --- METAR capture (B-3): record the settlement station's observations
    # from day one so the OD-12 late-day-persistence study has data. No worker
    # consumes this yet (W2 is build-blocked); this is capture-only. The
    # settlement station is shared with W1 (KNYC).
    metar = MetarAdapter(station=w1_config["station"], capture=capture)
    orch.register_worker(worker, cadence_s=float(w1_config.get("cadence_s", 600)))
    try:
        await worker.initialize()
        ok, reason = orch.start_strategy("W1")
        print(f"W1 start: {reason}")
    except Exception as exc:
        print(f"W1 failed to initialize ({exc}); registered but stopped. "
              "Fix data access and resume from the dashboard.")

    # Periodic out-of-tree backups (B-5). Tied to orchestrator liveness so it
    # winds down with the rest on shutdown; interval_s <= 0 disables it.
    from apacenye.backup import backup_loop
    backup_tasks = []
    if settings.backup_interval_s > 0:
        backup_tasks.append(backup_loop(
            settings.db_path, settings.capture_dir, settings.backup_dir,
            interval_s=settings.backup_interval_s,
            retention=settings.backup_retention,
            should_continue=lambda: orch._running,
        ))
        print(f"backups: every {settings.backup_interval_s:.0f}s → "
              f"{settings.backup_dir} (keep {settings.backup_retention})")

    app = create_app(orch, ws_hub)
    server = uvicorn.Server(uvicorn.Config(
        app, host=settings.dashboard_host, port=settings.dashboard_port,
        log_level="warning",
    ))
    orch._running = True
    print(f"dashboard: http://{settings.dashboard_host}:{settings.dashboard_port}")
    await asyncio.gather(
        orch.run(),
        orch.kill_watcher(),
        orch.heartbeat_supervisor(),
        orch.expiry_sweeper(),
        scheduler.run(),
        feed.run(),
        metar.run_capture(),
        server.serve(),
        *backup_tasks,
    )


async def _resolve_soonest_event(kalshi, series_ticker: str) -> str | None:
    """Pick the open event in the series with the earliest close time —
    today's daily market. Failure returns None; serve still boots."""
    try:
        data = await kalshi.get_markets(series_ticker=series_ticker, status="open")
    except Exception as exc:
        log.warning("event resolution failed for %s: %s", series_ticker, exc)
        return None
    markets = data.get("markets", [])
    if not markets:
        return None
    soonest = min(markets, key=lambda m: m.get("close_time", ""))
    return soonest.get("event_ticker")


if __name__ == "__main__":
    sys.exit(main())
