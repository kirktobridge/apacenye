"""Capture writer — Stage 3 §9: records everything from day one.

Our own capture is the PRIMARY (and only executability-honest) backtest data
source: it is the only feed with order-book depth. Format, verbatim from the
handoff: `data/capture/YYYY-MM-DD/<channel>.jsonl.gz`, one JSON object per
line: {"ts": <UTC ISO-8601>, "type": <channel>, "ticker"|"station": …,
"payload": {…}}. Append-only, crash-tolerant, pandas-loadable.

Channels used in this bootstrap: book, trade, settlement, nws_forecast, metar.
"""

from __future__ import annotations

import gzip
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

CHANNELS = ("book", "trade", "settlement", "nws_forecast", "metar")


class CaptureWriter:
    def __init__(self, capture_dir: str | Path):
        self.capture_dir = Path(capture_dir)
        self._lock = threading.Lock()

    def _path_for(self, channel: str, ts: datetime) -> Path:
        day = ts.astimezone(timezone.utc).date().isoformat()
        d = self.capture_dir / day
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{channel}.jsonl.gz"

    def write(self, channel: str, payload: dict, *, ticker: str | None = None,
              station: str | None = None, ts: datetime | None = None) -> None:
        """Append one record. Never raises to the caller — capture must not
        take down trading — but failures are logged loudly."""
        ts = ts or datetime.now(timezone.utc)
        record: dict = {"ts": ts.astimezone(timezone.utc).isoformat(), "type": channel,
                        "payload": payload}
        if ticker is not None:
            record["ticker"] = ticker
        if station is not None:
            record["station"] = station
        try:
            line = (json.dumps(record) + "\n").encode()
            with self._lock:
                # gzip supports append mode: each write is its own member,
                # which gzip readers concatenate transparently (crash-tolerant)
                with gzip.open(self._path_for(channel, ts), "ab") as f:
                    f.write(line)
        except OSError:
            log.exception("capture write failed for channel %s", channel)

    @staticmethod
    def read_day(capture_dir: str | Path, day: str, channel: str) -> list[dict]:
        """Load one day's channel file (used by the replay harness)."""
        path = Path(capture_dir) / day / f"{channel}.jsonl.gz"
        if not path.exists():
            return []
        with gzip.open(path, "rt") as f:
            return [json.loads(ln) for ln in f if ln.strip()]
