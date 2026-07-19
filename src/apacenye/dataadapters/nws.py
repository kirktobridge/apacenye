"""NWS forecast adapter — W1's signal source (free, keyless).

Non-negotiables (onboard-data-source skill): every returned datum carries its
SOURCE timestamp (the forecast's generation time, not our fetch time) so it
flows into `key_inputs` and the G4 staleness gate can act on it; failures
raise loudly rather than returning stale data silently.

The settlement source for temperature markets is the NWS climatological
report for the named station; this adapter reads the public forecast API for
the station's grid point (configured per city in config/strategies/w1.yaml).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

log = logging.getLogger(__name__)

USER_AGENT = "apacenye-paper-bootstrap (personal research)"


class NwsAdapterError(RuntimeError):
    pass


@dataclass
class ForecastHigh:
    station: str
    high_f: float
    source_ts: datetime  # when NWS generated the forecast — for staleness
    fetched_ts: datetime
    period_name: str


class NwsForecastAdapter:
    """Fetches today's forecast high for one station's NWS grid point."""

    def __init__(self, station: str, grid_office: str, grid_x: int, grid_y: int,
                 client: httpx.AsyncClient | None = None):
        self.station = station
        self.url = (f"https://api.weather.gov/gridpoints/{grid_office}/"
                    f"{grid_x},{grid_y}/forecast")
        self._client = client or httpx.AsyncClient(
            timeout=20.0, headers={"User-Agent": USER_AGENT}
        )

    async def fetch_forecast_high(self) -> ForecastHigh:
        try:
            resp = await self._client.get(self.url)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise NwsAdapterError(f"NWS forecast fetch failed for {self.station}: {exc}") from exc

        props = data.get("properties") or {}
        periods = props.get("periods") or []
        source_ts_raw = props.get("updateTime") or props.get("generatedAt")
        if not periods or not source_ts_raw:
            raise NwsAdapterError(f"NWS forecast response malformed for {self.station}")
        # today's daytime period carries the forecast high
        daytime = next((p for p in periods if p.get("isDaytime")), None)
        if daytime is None or daytime.get("temperature") is None:
            raise NwsAdapterError(
                f"no daytime period with a temperature for {self.station} "
                "(late-evening fetch? W1 trades next morning)"
            )
        return ForecastHigh(
            station=self.station,
            high_f=float(daytime["temperature"]),
            source_ts=datetime.fromisoformat(source_ts_raw.replace("Z", "+00:00")),
            fetched_ts=datetime.now(timezone.utc),
            period_name=daytime.get("name", ""),
        )

    async def close(self) -> None:
        await self._client.aclose()
