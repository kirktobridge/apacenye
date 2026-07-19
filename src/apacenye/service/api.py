"""Service layer — FastAPI REST + WebSocket + dashboard (Stage 3 §7).

Security posture (OD-18): binds 127.0.0.1 by default with no auth; any other
host REQUIRES a bearer token (enforced at startup by AppSettings and per
request here). The dangerous direction is simply absent from this surface:
there is NO un-kill endpoint and NO live-enable endpoint — both are CLI-only.
The dashboard can stop the system (POST /api/kill) but never restart it.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from apacenye.checkpoint.ack import AckLog, risk_relevant_config_hash
from apacenye.contract import LifecycleState, RunMode, utcnow
from apacenye.orchestrator.orchestrator import Orchestrator
from apacenye.service.ws import WsHub

log = logging.getLogger(__name__)

_HERE = Path(__file__).parent


def create_app(orch: Orchestrator, ws_hub: WsHub) -> FastAPI:
    app = FastAPI(title="Apacenyë", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=_HERE / "static"), name="static")
    templates = Jinja2Templates(directory=_HERE / "templates")
    settings = orch.settings
    started_at = utcnow()

    async def require_token(request: Request) -> None:
        """Bearer auth — mandatory iff bound beyond localhost (OD-18)."""
        token = settings.dashboard_token.get_secret_value()
        if settings.dashboard_host in ("127.0.0.1", "localhost") and not token:
            return
        supplied = request.headers.get("authorization", "")
        if supplied != f"Bearer {token}" or not token:
            raise HTTPException(status_code=401, detail="missing or bad token")

    auth = Depends(require_token)

    # ------------------------------------------------------------- REST API

    @app.get("/api/state", dependencies=[auth])
    async def api_state():
        return {
            "run_mode": orch.run_mode.value,
            "killed": orch.kill.is_killed(),
            "kill_state": orch.kill.read_state(),
            "bankroll_usd": orch.risk.bankroll_usd,
            "equity_dollars": orch.ledger.equity_dollars(),
            "uptime_s": (utcnow() - started_at).total_seconds(),
        }

    @app.get("/api/positions", dependencies=[auth])
    async def api_positions():
        positions = orch.ledger.open_positions()
        for pos in positions:
            snap = orch.cache.get(pos["market_ticker"])
            mid = snap.mid_dollars if snap else None
            # marks are INDICATIVE (mid); fills/qualification never use them
            pos["mark_mid_dollars"] = mid
            if mid is not None:
                mark = mid if pos["side"] == "yes" else 1.0 - mid
                pos["unrealized_dollars"] = round(
                    pos["count"] * mark - pos["cost_basis_dollars"] - pos["fees_paid_dollars"], 2
                )
        return {"positions": positions}

    def _strategy_view(sid: str) -> dict:
        worker = orch.workers[sid]
        hb = orch.ledger.latest_heartbeat(sid)
        return {
            "strategy_id": sid,
            "state": worker.state.value,
            "config": {k: v for k, v in worker.config.items()},
            "heartbeat_ts": hb["ts"] if hb else None,
            "day_pnl_dollars": round(orch.ledger.day_pnl_dollars(
                sid, orch.risk_engine._current_marks(sid)), 2),
            "ack_valid": orch.ack_log.has_valid_paper_ack(
                sid, risk_relevant_config_hash(orch.risk)),
        }

    @app.get("/api/strategies", dependencies=[auth])
    async def api_strategies():
        return {"strategies": [_strategy_view(sid) for sid in orch.workers]}

    @app.get("/api/strategies/{sid}", dependencies=[auth])
    async def api_strategy(sid: str):
        if sid not in orch.workers:
            raise HTTPException(404)
        return _strategy_view(sid)

    @app.post("/api/strategies/{sid}/pause", dependencies=[auth])
    async def api_pause(sid: str):
        if sid not in orch.workers:
            raise HTTPException(404)
        orch.pause_strategy(sid, "dashboard pause")
        return {"ok": True, "state": orch.workers[sid].state.value}

    @app.post("/api/strategies/{sid}/resume", dependencies=[auth])
    async def api_resume(sid: str):
        """Resume routes through the SAME gated start (ack + kill checks)."""
        if sid not in orch.workers:
            raise HTTPException(404)
        ok, reason = orch.start_strategy(sid)
        return {"ok": ok, "reason": reason, "state": orch.workers[sid].state.value}

    @app.post("/api/strategies/{sid}/config", dependencies=[auth])
    async def api_update_config(sid: str, body: dict):
        if sid not in orch.workers:
            raise HTTPException(404)
        ok, reason = await orch.workers[sid].update_config(body)
        return {"ok": ok, "reason": reason}

    @app.get("/api/intents", dependencies=[auth])
    async def api_intents(since: str | None = None):
        return {"intents": orch.ledger.recent_intents(since_iso=since)}

    @app.get("/api/explanations/{intent_id}", dependencies=[auth])
    async def api_explanation(intent_id: str):
        exp = orch.ledger.get_explanation(intent_id)
        if exp is None:
            raise HTTPException(404)
        return exp

    @app.get("/api/evaluations", dependencies=[auth])
    async def api_evaluations(strategy: str | None = None):
        return {"evaluations": orch.ledger.recent_evaluations(strategy)}

    @app.get("/api/risk", dependencies=[auth])
    async def api_risk():
        summary = orch.risk_engine.risk_summary()
        summary["events"] = [
            {"event_ticker": e,
             "exposure_dollars": round(orch.ledger.event_exposure_dollars(e), 2),
             "cap_dollars": orch.risk.max_event_exposure_dollars}
            for e in orch.catalog.events()
        ]
        summary["strategies"] = [
            {"strategy_id": sid,
             "exposure_dollars": round(orch.ledger.strategy_exposure_dollars(sid), 2),
             "cap_dollars": orch.risk.max_strategy_exposure_dollars}
            for sid in orch.workers
        ]
        return summary

    @app.get("/api/acks", dependencies=[auth])
    async def api_acks():
        """Read-only render of the acknowledgment log (§11.4). The dashboard
        can view acks; it can never create or edit them."""
        ack_log = AckLog(settings.ack_log_path)
        ok, msg = ack_log.verify()
        return {"chain": msg, "chain_ok": ok, "records": ack_log.read_all()}

    @app.post("/api/kill", dependencies=[auth])
    async def api_kill(body: dict | None = None):
        """In-band kill trigger — writes the same sentinel file the CLI does.
        NOTE deliberately absent: there is NO /api/unkill. Un-kill is
        CLI-only with typed confirmation (ALWAYS-APPLY RULE 5)."""
        reason = (body or {}).get("reason", "dashboard kill button")
        orch.kill.trip("dashboard", reason)
        orch.ledger.record_kill_event("kill", "dashboard", reason)
        return {"ok": True, "killed": True}

    # ------------------------------------------------------------ WebSocket

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket):
        token = settings.dashboard_token.get_secret_value()
        if settings.dashboard_host not in ("127.0.0.1", "localhost") or token:
            supplied = websocket.headers.get("authorization", "")
            if supplied != f"Bearer {token}" or not token:
                await websocket.close(code=4401)
                return
        await websocket.accept()
        ws_hub.register(websocket)
        try:
            while True:
                await websocket.receive_text()  # client pings; we only push
        except WebSocketDisconnect:
            ws_hub.unregister(websocket)

    # ------------------------------------------------------------ dashboard

    def _overview_ctx(request: Request) -> dict:
        return {
            "request": request,
            "run_mode": orch.run_mode.value,
            "killed": orch.kill.is_killed(),
            "kill_state": orch.kill.read_state(),
            "equity": orch.ledger.equity_dollars(),
            "risk": orch.risk,
        }

    @app.get("/", response_class=HTMLResponse, dependencies=[auth])
    async def page_overview(request: Request):
        return templates.TemplateResponse(request, "overview.html", _overview_ctx(request))

    @app.get("/strategies", response_class=HTMLResponse, dependencies=[auth])
    async def page_strategies(request: Request):
        return templates.TemplateResponse(request, "strategies.html", _overview_ctx(request))

    @app.get("/signals", response_class=HTMLResponse, dependencies=[auth])
    async def page_signals(request: Request):
        return templates.TemplateResponse(request, "signals.html", _overview_ctx(request))

    @app.get("/risk", response_class=HTMLResponse, dependencies=[auth])
    async def page_risk(request: Request):
        return templates.TemplateResponse(request, "risk.html", _overview_ctx(request))

    # htmx fragments (polled)

    @app.get("/fragments/positions", response_class=HTMLResponse, dependencies=[auth])
    async def frag_positions(request: Request):
        data = await api_positions()
        return templates.TemplateResponse(request, "_positions.html", data)

    @app.get("/fragments/state", response_class=HTMLResponse, dependencies=[auth])
    async def frag_state(request: Request):
        return templates.TemplateResponse(request, "_state.html", _overview_ctx(request))

    @app.get("/fragments/strategies", response_class=HTMLResponse, dependencies=[auth])
    async def frag_strategies(request: Request):
        views = [_strategy_view(sid) for sid in orch.workers]
        return templates.TemplateResponse(
            request, "_strategies.html",
            {"strategies": views,
             "daily_stop": -orch.risk.bankroll_usd * orch.risk.strategy_daily_loss_pct / 100.0})

    @app.get("/fragments/signals", response_class=HTMLResponse, dependencies=[auth])
    async def frag_signals(request: Request):
        evals = orch.ledger.recent_evaluations(limit=50)
        intents = orch.ledger.recent_intents(limit=50)
        return templates.TemplateResponse(
            request, "_signals.html", {"evaluations": evals, "intents": intents})

    @app.get("/fragments/risk", response_class=HTMLResponse, dependencies=[auth])
    async def frag_risk(request: Request):
        data = await api_risk()
        acks = await api_acks()
        return templates.TemplateResponse(
            request, "_risk.html", {"risk_data": data, "acks": acks})

    return app
