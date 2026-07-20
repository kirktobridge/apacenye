"""Calibration math tests — written BEFORE domain/calibration.py.

These pin the numbers the `review-calibration` skill quotes into DEV_LOG:
Brier score, decile reliability binning, the qualified/traded subset splits,
the 100-row insufficient-data gate, event-vs-row counting, and the mechanical
verdict. All functions are pure; no I/O, no SQL.
"""

import pytest

from apacenye.domain.calibration import (
    CALIBRATION_BIAS_THRESHOLD,
    INSUFFICIENT_DATA_ROWS,
    ScoredEval,
    brier_score,
    build_report,
    reliability_bins,
)


# ------------------------------------------------------------------ Brier


def test_brier_score_hand_computed():
    # perfect/worst mix: (0 + 0 + 1) / 3
    assert brier_score([1.0, 0.0, 1.0], [1.0, 0.0, 0.0]) == pytest.approx(1 / 3)
    # two coin-flip predictions: each (0.5)^2
    assert brier_score([0.5, 0.5], [1.0, 0.0]) == pytest.approx(0.25)
    # a single confident-and-right call
    assert brier_score([0.9], [1.0]) == pytest.approx(0.01)


def test_brier_score_rejects_empty_and_mismatch():
    with pytest.raises(ValueError):
        brier_score([], [])
    with pytest.raises(ValueError):
        brier_score([0.5], [1.0, 0.0])


# ------------------------------------------------------- reliability binning


def test_reliability_bins_counts_means_and_gap():
    preds = [0.05, 0.15, 0.15, 0.95]
    outs = [0.0, 0.0, 1.0, 1.0]
    bins = reliability_bins(preds, outs, n_bins=10)
    assert len(bins) == 10
    # 0.05 → bin 0
    assert bins[0].n == 1 and bins[0].mean_pred == pytest.approx(0.05)
    assert bins[0].observed_freq == pytest.approx(0.0)
    assert bins[0].gap == pytest.approx(-0.05)
    # both 0.15 → bin 1, one hit one miss
    assert bins[1].n == 2 and bins[1].mean_pred == pytest.approx(0.15)
    assert bins[1].observed_freq == pytest.approx(0.5)
    assert bins[1].gap == pytest.approx(0.35)
    # 0.95 → bin 9
    assert bins[9].n == 1 and bins[9].observed_freq == pytest.approx(1.0)
    # an untouched bin is present but empty (correct output, not a bug)
    assert bins[5].n == 0
    assert bins[5].mean_pred is None and bins[5].gap is None


def test_reliability_bins_top_edge_lands_in_last_bin():
    bins = reliability_bins([1.0], [1.0], n_bins=10)
    assert bins[9].n == 1 and bins[9].mean_pred == pytest.approx(1.0)


def test_reliability_bins_rejects_out_of_range():
    with pytest.raises(ValueError):
        reliability_bins([1.5], [1.0])
    with pytest.raises(ValueError):
        reliability_bins([-0.1], [0.0])


# --------------------------------------------------------------- build_report


def _rows(n, model_p, outcome, market_p=0.5, qualified=False, traded=False,
          event="E"):
    return [ScoredEval(model_probability=model_p,
                       market_implied_probability=market_p, outcome=outcome,
                       qualified=qualified, traded=traded, event_ticker=event)
            for _ in range(n)]


def _balanced(n, model_p, market_p=0.5, event="E", qualified=False, traded=False):
    """n rows, exactly half outcome=1 (n must be even) — mean outcome 0.5."""
    rows = []
    for i in range(n):
        rows.append(ScoredEval(model_probability=model_p,
                               market_implied_probability=market_p,
                               outcome=1.0 if i % 2 == 0 else 0.0,
                               qualified=qualified, traded=traded,
                               event_ticker=event))
    return rows


def test_insufficient_data_gate_at_100():
    below = build_report("W1", _balanced(INSUFFICIENT_DATA_ROWS - 1, 0.5))
    assert below.insufficient_data is True
    assert below.verdict == "insufficient-data"
    at = build_report("W1", _balanced(INSUFFICIENT_DATA_ROWS, 0.5, market_p=0.5))
    assert at.insufficient_data is False
    assert at.verdict != "insufficient-data"


def test_report_excludes_null_mid_and_counts_it():
    rows = _balanced(4, 0.5, market_p=0.5)
    rows += [ScoredEval(0.5, None, 1.0, False, False, "E"),
             ScoredEval(0.5, None, 0.0, False, False, "E")]
    rep = build_report("W1", rows)
    assert rep.n_excluded_null_mid == 2
    assert rep.n_scored == 4  # only the two-sided-quote rows are scoreable


def test_report_counts_events_not_just_rows():
    rows = _balanced(4, 0.5, event="A") + _balanced(4, 0.5, event="B")
    rep = build_report("W1", rows)
    assert rep.n_scored == 8
    assert rep.n_events == 2


def test_adverse_selection_flag_when_traded_subset_worse():
    # traded rows are confidently wrong; untraded rows are confidently right.
    traded = _rows(2, model_p=0.9, outcome=0.0, traded=True, qualified=True)
    untraded = _rows(2, model_p=0.1, outcome=0.0, traded=False)
    rep = build_report("W1", traded + untraded)
    assert rep.traded.brier_model > rep.untraded.brier_model
    assert rep.adverse_selection is True


def test_verdict_loses_to_market():
    # model confidently wrong (0.9 on outcomes that never happen); market 0.5.
    rep = build_report("W1", _rows(100, model_p=0.9, outcome=0.0, market_p=0.5))
    assert rep.verdict == "miscalibrated-loses-to-market-benchmark"


def test_verdict_overforecasting():
    # model says 0.7, outcomes land at 0.5 → systematic over-forecast.
    rep = build_report("W1", _balanced(100, 0.7, market_p=0.7))
    assert rep.weighted_gap == pytest.approx(-0.2)
    assert abs(rep.weighted_gap) > CALIBRATION_BIAS_THRESHOLD
    assert rep.verdict == "miscalibrated-overforecasting"


def test_verdict_underforecasting():
    rep = build_report("W1", _balanced(100, 0.3, market_p=0.3))
    assert rep.weighted_gap == pytest.approx(0.2)
    assert rep.verdict == "miscalibrated-underforecasting"


def test_verdict_calibrated():
    rep = build_report("W1", _balanced(100, 0.5, market_p=0.5))
    assert rep.weighted_gap == pytest.approx(0.0)
    assert rep.verdict == "calibrated"


def test_empty_rows_is_insufficient_not_a_crash():
    rep = build_report("W1", [])
    assert rep.n_scored == 0
    assert rep.brier_model is None
    assert rep.verdict == "insufficient-data"
    assert len(rep.reliability) == 10


# ------------------------------------------------- rendered report (golden-file)


def test_calibration_text_matches_golden():
    """The rendered text report must match the committed golden byte-for-byte
    (ledger path stubbed). Regenerate deliberately if the format changes."""
    from pathlib import Path

    from apacenye.cli import _calibration_text

    rows = [
        ScoredEval(0.05, 0.10, 0.0, False, False, "E1"),
        ScoredEval(0.15, 0.20, 1.0, True, True, "E1"),
        ScoredEval(0.15, 0.20, 0.0, True, True, "E2"),
        ScoredEval(0.95, 0.90, 1.0, False, False, "E2"),
        ScoredEval(0.50, None, 1.0, False, False, "E2"),
    ]
    cov = {"total": 7, "settled": 5, "unsettled": 2, "settled_null_mid": 1,
           "distinct_events": 2}
    report = build_report("W1", rows)
    rendered = _calibration_text("LEDGER_PATH", "2026-07-01", "2026-07-20", cov, report)
    golden = (Path(__file__).parent / "golden" / "calibration_w1.txt").read_text()
    assert rendered == golden


# ------------------------------------------------- settlement backfill (D4)


class _FakeKalshi:
    """Stand-in for the read-only Kalshi client: returns canned settlement
    results, no network."""

    def __init__(self, results):
        self._results = results

    async def get_market(self, ticker):
        return {"market": {"result": self._results.get(ticker, "")}}

    async def close(self):
        pass


async def test_backfill_marks_positionless_and_skips_open_positions(tmp_path, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace

    from pydantic import SecretStr

    import apacenye.execution.kalshi as kmod
    from apacenye.cli import _backfill_settlements
    from apacenye.contract import Action, Evaluation, Fill, Side
    from apacenye.orchestrator.ledger import Ledger

    led = Ledger(tmp_path / "t.sqlite", 1000.0)
    led.upsert_market("M-A", "E", 80, 84)
    led.upsert_market("M-B", "E", 85, 89)
    for t in ("M-A", "M-B"):
        led.record_evaluation(Evaluation(
            strategy_id="W1", market_ticker=t, event_ticker="E",
            model_probability=0.5, market_implied_probability=0.5,
            executable_price_dollars=None, net_edge=None, qualified=False))
    # an OPEN position on M-B — backfill must refuse to mark it
    led.record_fill(Fill(order_id="i", intent_id="i", strategy_id="W1",
                         market_ticker="M-B", side=Side.YES, action=Action.OPEN,
                         price_dollars=0.5, count=10, fee_dollars=0.1))

    fake = _FakeKalshi({"M-A": "yes", "M-B": "no"})
    monkeypatch.setattr(kmod, "KalshiClient", lambda **kw: fake)
    settings = SimpleNamespace(kalshi_api_key_id=SecretStr(""),
                               kalshi_private_key_path=Path("nope"), kalshi_env="prod")

    await _backfill_settlements(settings, led, "W1")

    assert led.market_status("M-A") == "settled"   # positionless → marked
    assert led.market_status("M-B") == "open"       # open position → left for serve
    assert len(led.open_positions()) == 1           # untouched: no realization
    assert led.equity_dollars() == pytest.approx(1000.0)
    led.close()
