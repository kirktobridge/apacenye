"""METAR capture wiring (B-3): the adapter records observations to the `metar`
channel from day one, and the capture-only poll loop survives the missed
observations that are common on the free feed."""

import asyncio
import gzip
import json

import pytest

from apacenye.backtest.capture import CaptureWriter
from apacenye.dataadapters.metar import MetarAdapter, MetarAdapterError


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeClient:
    """Stands in for httpx.AsyncClient: each get() returns the next queued
    payload (a dict → served as JSON, or an Exception → raised). Optionally
    calls `on_call(n)` before returning so a test can stop a poll loop at an
    exact iteration."""

    def __init__(self, responses, on_call=None):
        self._responses = list(responses)
        self.calls = 0
        self._on_call = on_call

    async def get(self, url):
        self.calls += 1
        if self._on_call is not None:
            self._on_call(self.calls)
        item = self._responses[min(self.calls - 1, len(self._responses) - 1)]
        if isinstance(item, Exception):
            raise item
        return FakeResponse(item)


def _obs_payload(temp_c=25.0, ts="2026-07-19T18:51:00+00:00"):
    return {"properties": {"temperature": {"value": temp_c}, "timestamp": ts}}


async def test_fetch_latest_writes_metar_channel(tmp_path):
    cap = CaptureWriter(tmp_path)
    adapter = MetarAdapter("KNYC", client=FakeClient([_obs_payload()]), capture=cap)
    obs = await adapter.fetch_latest()
    assert obs.temp_f == pytest.approx(77.0)  # 25°C → 77°F

    day = obs.fetched_ts.date().isoformat()
    records = CaptureWriter.read_day(tmp_path, day, "metar")
    assert len(records) == 1
    rec = records[0]
    assert rec["type"] == "metar" and rec["station"] == "KNYC"
    assert rec["payload"]["temp_f"] == pytest.approx(77.0)
    assert rec["payload"]["source_ts"] == "2026-07-19T18:51:00+00:00"


async def test_fetch_latest_no_capture_is_fine():
    adapter = MetarAdapter("KNYC", client=FakeClient([_obs_payload()]))
    obs = await adapter.fetch_latest()
    assert obs.station == "KNYC"


async def test_missing_temperature_raises_loudly():
    payload = {"properties": {"temperature": {"value": None}, "timestamp": "x"}}
    adapter = MetarAdapter("KNYC", client=FakeClient([payload]))
    with pytest.raises(MetarAdapterError):
        await adapter.fetch_latest()


async def test_capture_loop_survives_missed_metar_then_records(tmp_path):
    # First poll: a missed observation (no temperature) → loop must NOT die.
    # Second poll: a good observation → gets captured, then the loop stops.
    cap = CaptureWriter(tmp_path)
    responses = [
        {"properties": {"temperature": {"value": None}, "timestamp": "x"}},
        _obs_payload(temp_c=30.0),
    ]

    client = FakeClient(responses)
    adapter = MetarAdapter("KNYC", client=client, capture=cap)
    # stop the loop on the 2nd poll (the good one): stop() only affects the
    # while-condition after this fetch completes, so the good record is written.
    client._on_call = lambda n: adapter.stop() if n >= 2 else None

    await adapter.run_capture(interval_s=0)

    # exactly one record — the good second poll; the missed first wrote nothing
    all_records = []
    for day_dir in tmp_path.iterdir():
        f = day_dir / "metar.jsonl.gz"
        if f.exists():
            with gzip.open(f, "rt") as fh:
                all_records += [json.loads(ln) for ln in fh if ln.strip()]
    assert adapter._client.calls == 2
    assert len(all_records) == 1
    assert all_records[0]["payload"]["temp_f"] == pytest.approx(86.0)  # 30°C
