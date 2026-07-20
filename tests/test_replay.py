"""Replay harness round-trip: write capture with the real CaptureWriter,
replay it through the real scheduler/worker/gates/simulator, check the
mandatory illustrative-only label and the calibration output."""

from datetime import datetime, timedelta, timezone

import pytest

from apacenye.backtest.capture import CaptureWriter
from apacenye.backtest.replay import ILLUSTRATIVE_LABEL, run_replay
from apacenye.config import RiskConfig
from apacenye.contract import MarketSnapshot

EVENT = "KXHIGHNY-26JUL18"
TICKER = f"{EVENT}-B85"
DAY = "2026-07-18"

W1_CONFIG = {
    "station": "KNYC", "grid_office": "OKX", "grid_x": 33, "grid_y": 37,
    "event_ticker": EVENT, "sigma_f": 3.0, "cadence_s": 600,
}


def write_capture(capture_dir):
    cap = CaptureWriter(capture_dir)
    t0 = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
    # market metadata FIRST — without bracket bounds the model is unusable
    cap.write("market", {"ticker": TICKER, "event_ticker": EVENT,
                         "bracket_lo": 85, "bracket_hi": 89,
                         "title": "", "status": "open"}, ticker=TICKER, ts=t0)
    cap.write("nws_forecast", {"high_f": 86.0, "source_ts": t0.isoformat(),
                               "period_name": "Today"}, station="KNYC", ts=t0)
    for i in range(6):  # books every 10 minutes; mispriced vs the model
        ts = t0 + timedelta(minutes=10 * i, seconds=30)
        snap = MarketSnapshot(
            ticker=TICKER, event_ticker=EVENT, yes_bid_dollars=0.46,
            yes_ask_dollars=0.48, yes_bid_depth=500, yes_ask_depth=500, ts=ts,
        )
        cap.write("book", snap.model_dump(mode="json"), ticker=TICKER, ts=ts)
    cap.write("settlement", {"result": "yes"}, ticker=TICKER,
              ts=t0 + timedelta(hours=10))


async def test_replay_round_trip(tmp_path):
    capture_dir = tmp_path / "capture"
    write_capture(capture_dir)
    result = await run_replay(W1_CONFIG, RiskConfig(), capture_dir,
                              DAY, DAY, tmp_path / "work")
    assert result["label"] == ILLUSTRATIVE_LABEL  # mandatory, at the source
    assert result["evaluations"] >= 1
    assert result["qualified"] >= 1
    # bracket settled YES and we bought it below fair value → positive paper
    # P&L (and: this number is an OPTIMISTIC BOUND, per the label)
    assert result["realized_pnl_dollars"] > 0
    assert result["open_positions_at_end"] == 0
    # calibration computed: model must beat the market mid on this rigged tape
    assert result["brier_model"] is not None
    assert result["brier_model"] < result["brier_market"]
    # and the model probability replayed was the honest Gaussian (≈0.57 for
    # the 85–89 bracket at μ=86, σ=3), not a degenerate bounds-less 1.0
    assert 0.5 < 1 - result["brier_model"] ** 0.5 < 0.7


async def test_replay_brier_output_is_pinned(tmp_path):
    # Guards the refactor of replay's inline Brier onto domain/calibration:
    # these exact numbers on the fixed tape must not drift.
    capture_dir = tmp_path / "capture"
    write_capture(capture_dir)
    result = await run_replay(W1_CONFIG, RiskConfig(), capture_dir,
                              DAY, DAY, tmp_path / "work")
    assert result["scored_samples"] == 6
    assert result["brier_model"] == pytest.approx(0.1851)
    assert result["brier_market"] == pytest.approx(0.2809)


async def test_replay_reports_coverage_gaps(tmp_path):
    capture_dir = tmp_path / "capture"
    write_capture(capture_dir)
    result = await run_replay(W1_CONFIG, RiskConfig(), capture_dir,
                              DAY, "2026-07-19", tmp_path / "work")  # extra empty day
    assert any("2026-07-19" in w for w in result["warnings"])


async def test_replay_empty_window_is_labeled_not_faked(tmp_path):
    result = await run_replay(W1_CONFIG, RiskConfig(), tmp_path / "capture",
                              "2026-01-01", "2026-01-02", tmp_path / "work")
    assert result["evaluations"] == 0
    assert result["label"] == ILLUSTRATIVE_LABEL
    assert "no capture data" in result["note"]
