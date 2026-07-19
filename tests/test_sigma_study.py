"""Unit tests for the W1 σ-from-archive study (research/estimate_sigma_w1.py).

The study is offline research, not money-path code, but its arithmetic is what
sets W1's σ — so the join, the max/min-convention filter, the lead bucketing and
the conservative round-up are pinned here with synthetic data (no network).
"""

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))

from estimate_sigma_w1 import (  # noqa: E402
    extract_daily_max_forecasts,
    forecast_errors,
    recommend_sigma,
    sigma_by_lead_bucket,
)


def _row(model, runtime, ftime, n_x):
    return {"model": model, "runtime": runtime, "ftime": ftime, "n_x": n_x}


def test_extract_picks_00z_max_maps_to_local_day():
    # 00Z valid time ≈ 8pm EDT the prior evening → same local calendar day the max
    # occurred. The 12Z row is the overnight MIN and must be ignored.
    rows = [
        _row("MEX", "2025-08-15 12:00:00+00", "2025-08-17 00:00:00+00", "84"),  # max, 08-16
        _row("MEX", "2025-08-15 12:00:00+00", "2025-08-16 12:00:00+00", "72"),  # min → skip
        _row("MEX", "2025-08-15 12:00:00+00", "2025-08-18 00:00:00+00", "88"),  # max, 08-17
    ]
    got = extract_daily_max_forecasts(rows)
    assert [(f.local_day, f.forecast_max_f) for f in got] == [
        ("2025-08-16", 84), ("2025-08-17", 88),
    ]
    # lead is ftime − runtime, in hours (08-17 00Z − 08-15 12Z = 36h)
    assert got[0].lead_h == pytest.approx(36.0)


def test_extract_ignores_other_models_and_blank_nx():
    rows = [
        _row("NBS", "2025-08-15 12:00:00+00", "2025-08-17 00:00:00+00", "99"),  # wrong model
        _row("MEX", "2025-08-15 12:00:00+00", "2025-08-17 00:00:00+00", ""),    # blank
    ]
    assert extract_daily_max_forecasts(rows) == []


def test_forecast_errors_joins_on_local_day():
    from estimate_sigma_w1 import ForecastMax
    fc = [ForecastMax("2025-08-16", 36.0, 84), ForecastMax("2025-08-17", 36.0, 88)]
    observed = {"2025-08-16": 84.0, "2025-08-17": 91.0}  # note 08-18 absent
    errs = forecast_errors(fc, observed)
    assert errs == [("2025-08-16", 36.0, 0.0), ("2025-08-17", 36.0, -3.0)]


def test_forecast_errors_drops_days_without_observation():
    from estimate_sigma_w1 import ForecastMax
    errs = forecast_errors([ForecastMax("2025-08-16", 36.0, 84)], observed={})
    assert errs == []


def test_sigma_by_lead_bucket_uses_population_std_and_first_matching_bucket():
    errors = [("d1", 40.0, 1.0), ("d2", 44.0, -1.0), ("d3", 60.0, 2.0), ("d4", 64.0, -2.0)]
    out = sigma_by_lead_bucket(errors, buckets=[48, 72])
    assert out["<= 48h"]["n"] == 2
    assert out["<= 48h"]["bias_f"] == 0.0
    assert out["<= 48h"]["sigma_f"] == pytest.approx(1.0)  # pstdev({+1,-1}) = 1
    assert out["<= 72h"]["n"] == 2
    assert out["<= 72h"]["sigma_f"] == pytest.approx(2.0)  # pstdev({+2,-2}) = 2


def test_recommend_sigma_rounds_up_to_next_tenth():
    # errors with pstdev between 3.1 and 3.2 must recommend 3.2 (never round down)
    errors = [("d", 40.0, e) for e in (-3.138, 3.138, -3.138, 3.138)]
    rec = recommend_sigma(errors, short_lead_max_h=48.0)
    assert rec["raw_sigma_f"] == pytest.approx(3.138, abs=1e-3)
    assert rec["recommended_sigma_f"] == 3.2
    assert rec["recommended_sigma_f"] >= rec["raw_sigma_f"]


def test_recommend_sigma_requires_samples():
    with pytest.raises(ValueError):
        recommend_sigma([("d", 40.0, 1.0)], short_lead_max_h=48.0)
