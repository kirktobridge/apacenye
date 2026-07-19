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
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sqlite3
import sys
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
