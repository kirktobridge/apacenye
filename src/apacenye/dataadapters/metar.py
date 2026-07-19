"""METAR observation adapter — scaffolded for W2 (build-blocked on OD-12).

Reads the latest observation from the EXACT settlement station via the NWS
observations API. W2 (late-day determinism) is design-complete but may not
trade until OD-12 is verified live (do stale late-day quotes persist at
tradeable size, and how often does the live feed disagree with the official
climate report?). The adapter exists now so the capture writer records
observations from day one — replay backtests are only possible for data we
captured.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

log = logging.getLogger(__name__)

USER_AGENT = "apacenye-paper-bootstrap (personal research)"


class MetarAdapterError(RuntimeError):
    pass


@dataclass
class StationObservation:
    station: str
    temp_f: float
    source_ts: datetime  # observation time — for the 75-minute staleness rule
    fetched_ts: datetime


class MetarAdapter:
    def __init__(self, station: str, client: httpx.AsyncClient | None = None):
        self.station = station
        self.url = f"https://api.weather.gov/stations/{station}/observations/latest"
        self._client = client or httpx.AsyncClient(
            timeout=20.0, headers={"User-Agent": USER_AGENT}
        )

    async def fetch_latest(self) -> StationObservation:
        try:
            resp = await self._client.get(self.url)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise MetarAdapterError(f"METAR fetch failed for {self.station}: {exc}") from exc
        props = data.get("properties") or {}
        temp_c = (props.get("temperature") or {}).get("value")
        ts_raw = props.get("timestamp")
        if temp_c is None or ts_raw is None:
            raise MetarAdapterError(
                f"METAR observation missing temperature/timestamp for {self.station} "
                "(missed METARs are common — this must fail loudly, not return stale data)"
            )
        return StationObservation(
            station=self.station,
            temp_f=temp_c * 9.0 / 5.0 + 32.0,
            source_ts=datetime.fromisoformat(ts_raw.replace("Z", "+00:00")),
            fetched_ts=datetime.now(timezone.utc),
        )

    async def close(self) -> None:
        await self._client.aclose()
