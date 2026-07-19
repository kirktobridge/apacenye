"""WebSocket hub — server-push JSON events {channel, ts, payload} on the
channels from Stage 3 §7.2: positions, intents, fills, heartbeats, risk,
signals, alerts. The dashboard and any future client subscribe to /ws."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

CHANNELS = ("positions", "intents", "fills", "heartbeats", "risk", "signals", "alerts")


class WsHub:
    def __init__(self) -> None:
        self._clients: set = set()
        self._recent_signals: list[dict] = []  # small ring buffer for the dashboard

    def register(self, websocket) -> None:
        self._clients.add(websocket)

    def unregister(self, websocket) -> None:
        self._clients.discard(websocket)

    def broadcast(self, channel: str, payload: dict) -> None:
        """Fire-and-forget push to all connected clients; a slow or dead
        client is dropped rather than allowed to block trading."""
        event = {"channel": channel, "ts": datetime.now(timezone.utc).isoformat(),
                 "payload": payload}
        if channel == "signals":
            self._recent_signals = ([event] + self._recent_signals)[:100]
        if not self._clients:
            return
        message = json.dumps(event, default=str)
        for ws in list(self._clients):
            try:
                asyncio.get_running_loop().create_task(self._send(ws, message))
            except RuntimeError:
                pass  # no loop (sync test context) — WS push is best-effort

    async def _send(self, ws, message: str) -> None:
        try:
            await ws.send_text(message)
        except Exception:
            self.unregister(ws)

    def recent_signals(self) -> list[dict]:
        return list(self._recent_signals)
