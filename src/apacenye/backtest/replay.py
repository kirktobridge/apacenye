"""Replay backtest harness — Stage 3 §9: replay, not simulation-from-scratch.

Recorded capture is replayed through the SAME TickScheduler, the SAME
unmodified worker code, the SAME G0–G10 gate pipeline, and the SAME paper
fill simulator, on a virtual clock. Workers cannot tell replay from live —
which is exactly why the contract forbids workers owning wall-clock
scheduling.

*** BINDING HONESTY RULE (Stage 3 §9 / ALWAYS-APPLY RULE 6) ***
Until several weeks of our own capture exist, ALL backtest results are
ILLUSTRATIVE-ONLY and are NOT a basis for live confidence. They may not be
cited in any live-enablement argument. No synthetic data may ever be
presented as historical. Every result dict this module returns carries that
label at the source; keep it attached wherever results are shown.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path

from apacenye.backtest.capture import CaptureWriter
from apacenye.config import AppSettings, RiskConfig
from apacenye.contract import MarketSnapshot, RunMode, Side
from apacenye.dataadapters.nws import ForecastHigh
from apacenye.marketdata.catalog import MarketCatalog, MarketInfo
from apacenye.orchestrator.kill import KillSwitch
from apacenye.orchestrator.ledger import Ledger
from apacenye.orchestrator.orchestrator import Orchestrator
from apacenye.scheduler import TickScheduler
from apacenye.marketdata.snapshots import SnapshotCache
from apacenye.workers.w1_forecast import W1ForecastWorker

log = logging.getLogger(__name__)

ILLUSTRATIVE_LABEL = (
    "ILLUSTRATIVE-ONLY: replayed from limited capture; paper fills are an "
    "optimistic bound; NOT a basis for live confidence and may not be cited "
    "in any live-enablement argument."
)


class ReplayNwsAdapter:
    """Feeds captured forecasts to the worker on the virtual clock. Same
    interface as NwsForecastAdapter — the worker cannot tell the difference."""

    def __init__(self, station: str):
        self.station = station
        self._current: ForecastHigh | None = None

    def push(self, record: dict) -> None:
        payload = record["payload"]
        self._current = ForecastHigh(
            station=record.get("station", self.station),
            high_f=float(payload["high_f"]),
            source_ts=datetime.fromisoformat(payload["source_ts"]),
            fetched_ts=datetime.fromisoformat(record["ts"]),
            period_name=payload.get("period_name", ""),
        )

    async def fetch_forecast_high(self) -> ForecastHigh:
        if self._current is None:
            raise RuntimeError("no forecast in capture yet at this replay time")
        return self._current


def _days(from_day: str, to_day: str) -> list[str]:
    d0, d1 = date.fromisoformat(from_day), date.fromisoformat(to_day)
    return [(d0 + timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]


async def run_replay(
    strategy_config: dict,
    risk: RiskConfig,
    capture_dir: str | Path,
    from_day: str,
    to_day: str,
    work_dir: str | Path,
    strategy_id: str = "W1",
) -> dict:
    """Replay [from_day, to_day] of capture for one W1-style strategy.

    Returns a result dict whose `label` field is the mandatory
    illustrative-only statement. Coverage gaps are reported in `warnings`
    (a silent gap would otherwise masquerade as staleness or missed
    settlements — Stage 4 skill, step 2).
    """
    days = _days(from_day, to_day)
    events: list[tuple[datetime, str, dict]] = []
    warnings: list[str] = []
    for day in days:
        found_any = False
        for channel in ("book", "nws_forecast", "settlement", "market"):
            records = CaptureWriter.read_day(capture_dir, day, channel)
            if records:
                found_any = True
            elif channel not in ("settlement", "market"):  # legitimately sparse
                warnings.append(f"coverage gap: no '{channel}' capture for {day}")
            for rec in records:
                events.append((datetime.fromisoformat(rec["ts"]), channel, rec))
        if not found_any:
            warnings.append(f"coverage gap: no capture at all for {day}")
    events.sort(key=lambda e: e[0])

    if not events:
        return {"label": ILLUSTRATIVE_LABEL, "warnings": warnings, "days": days,
                "evaluations": 0, "qualified": 0, "fills": 0,
                "realized_pnl_dollars": 0.0, "brier_model": None,
                "brier_market": None, "note": "no capture data in window"}

    # Live serving resolves event_ticker from the Kalshi API at boot; replay
    # resolves it from the capture itself (the book records carry it).
    strategy_config = dict(strategy_config)
    if "event_ticker" not in strategy_config:
        for _, channel, rec in events:
            if channel == "book" and rec["payload"].get("event_ticker"):
                strategy_config["event_ticker"] = rec["payload"]["event_ticker"]
                break
        else:
            warnings.append("no event_ticker resolvable from capture; worker cannot evaluate")

    # Isolated replay environment: its own ledger/kill/ack under work_dir —
    # never the live data/ directory.
    work_dir = Path(work_dir)
    settings = AppSettings(run_mode=RunMode.PAPER, data_dir=work_dir,
                           _env_file=None)
    ledger = Ledger(settings.db_path, risk.bankroll_usd)
    cache = SnapshotCache()
    catalog = MarketCatalog()
    scheduler = TickScheduler()
    # virtual clock: the risk engine's TTL/staleness gates must judge intents
    # against REPLAY time, not the wall clock
    virtual = {"now": datetime.fromisoformat(events[0][2]["ts"])}
    orch = Orchestrator(settings, risk, ledger, KillSwitch(settings.kill_sentinel_path),
                        cache, catalog, scheduler, now_fn=lambda: virtual["now"])
    adapter = ReplayNwsAdapter(strategy_config.get("station", ""))
    worker = W1ForecastWorker(strategy_id, strategy_config, orch.make_context(),
                              adapter=adapter)
    orch.register_worker(worker, cadence_s=float(strategy_config.get("cadence_s", 600)))
    # NOTE: replay calls worker.start() directly (below, after the first
    # captured forecast initializes it) instead of orch.start_strategy —
    # bypassing the interactive paper-ack gate on purpose: that gate protects
    # the live paper loop; a backtest mutates only its own throwaway ledger.
    # Worker and gate-pipeline code are otherwise unmodified.

    # Pre-populate the catalog from the capture — reference data the live
    # system loads from the API at boot, not information leaking backwards.
    # "market" records carry bracket bounds (needed for p_model); book
    # records are a bounds-less fallback for old captures and get a warning,
    # because a bracket without bounds cannot be modeled honestly.
    for _, channel, rec in events:
        if channel == "market":
            p = rec["payload"]
            catalog.add(MarketInfo(p["ticker"], p.get("event_ticker", ""),
                                   title=p.get("title", ""),
                                   bracket_lo=p.get("bracket_lo"),
                                   bracket_hi=p.get("bracket_hi")))
            ledger.upsert_market(p["ticker"], p.get("event_ticker", ""),
                                 p.get("bracket_lo"), p.get("bracket_hi"))
    for _, channel, rec in events:
        if channel == "book":
            payload = rec["payload"]
            if catalog.get(payload["ticker"]) is None:
                warnings.append(
                    f"no 'market' metadata for {payload['ticker']}: bracket "
                    "bounds unknown; its model probabilities are unusable")
                catalog.add(MarketInfo(payload["ticker"], payload.get("event_ticker", "")))
                ledger.upsert_market(payload["ticker"], payload.get("event_ticker", ""))

    settled: dict[str, str] = {}
    initialized = False
    for ts, channel, rec in events:
        virtual["now"] = ts
        if channel == "nws_forecast":
            adapter.push(rec)
            if not initialized:
                await worker.initialize()
                worker.start()
                initialized = True
        elif channel == "book":
            payload = rec["payload"]
            snap = MarketSnapshot(**payload)
            if catalog.get(snap.ticker) is None:
                catalog.add(MarketInfo(snap.ticker, snap.event_ticker))
                ledger.upsert_market(snap.ticker, snap.event_ticker)
            cache.update(snap)  # also re-checks resting paper orders
        elif channel == "settlement":
            ticker = rec["ticker"]
            result = rec["payload"].get("result", "")
            if result in ("yes", "no") and catalog.get(ticker) is not None:
                settled[ticker] = result
                await orch.on_settlement(ticker,
                                         Side.YES if result == "yes" else Side.NO)
        if initialized:
            await scheduler.fire_due(ts)  # the SAME scheduler live uses
            while not orch.queue.empty():
                await orch.dispatch(await orch.queue.get())
    if orch.paper is not None:
        orch.paper.expire_stale(events[-1][0] + timedelta(days=1))

    # Calibration before P&L (run-backtest skill, step 4): join shadow
    # forecasts to settled outcomes; benchmark = the market mid's own Brier.
    evals = ledger.recent_evaluations(strategy_id, limit=100000)
    scored = [(e["model_probability"], e["market_implied_probability"],
               1.0 if settled[e["market_ticker"]] == "yes" else 0.0)
              for e in evals
              if e["market_ticker"] in settled and e["market_implied_probability"] is not None]
    brier_model = (sum((p - o) ** 2 for p, _, o in scored) / len(scored)) if scored else None
    brier_market = (sum((m - o) ** 2 for _, m, o in scored) / len(scored)) if scored else None

    result = {
        "label": ILLUSTRATIVE_LABEL,
        "days": days,
        "warnings": warnings,
        "evaluations": len(evals),
        "qualified": sum(1 for e in evals if e["qualified"]),
        "scored_samples": len(scored),
        "realized_pnl_dollars": round(ledger.realized_pnl_total_dollars(), 2),
        "open_positions_at_end": len(ledger.open_positions()),
        "brier_model": round(brier_model, 4) if brier_model is not None else None,
        "brier_market": round(brier_market, 4) if brier_market is not None else None,
    }
    ledger.close()
    return result
