"""Estimate W1's forecast-error σ from an archive (OD-11) — replaces the 3.0 placeholder.

Plain-language summary
----------------------
W1 models the day's high as a bell curve centered on the NWS forecast high, with
spread σ = the station's historical forecast error. This script measures that error
honestly for KNYC (Central Park) and prints a defensible σ to put in w1.yaml.

Method (deliberately boring and explicit, so the owner can read it):
  1. FORECAST: GFS extended MOS ("MEX") archived by the Iowa Environmental Mesonet.
     Each model run publishes a daytime-maximum temperature ("n_x" at valid hour
     00Z) for each of the next several days. We treat that as the archived analogue
     of the live NWS/NDFD forecast high W1 consumes. (MEX is a PROXY, not the exact
     NDFD signal — see caveats. Its shortest max lead is ~24–36 h, so σ measured here
     is a slightly CONSERVATIVE upper bound for W1's same-day trading.)
  2. TRUTH: the observed daily maximum at KNYC (IEM ASOS daily summary, "max_tmpf").
     Close proxy for the NWS Climatological Report high Kalshi settles on.
  3. ERROR: forecast_max − observed_max, per local day, bucketed by forecast lead.
     σ per bucket = population standard deviation of that error.

Two hard-won archive facts (verified 2026-07-19):
  * IEM's csv.php IGNORES sts/ets range params — it serves only a recent window.
    Historical depth requires iterating explicit `runtime=` queries. We do that.
  * n_x holds BOTH max and min; the MAX is the row whose valid time (ftime) is 00Z,
    and its LOCAL day is ftime.astimezone(America/New_York).date() (00Z ≈ 8pm EDT
    the prior evening, i.e. the same local calendar day the daytime max occurred).

This is offline research, NOT money-path code: nothing here runs inside `serve`.
The output is one number (σ) reviewed by the owner and pasted into config.

Usage:
  python research/estimate_sigma_w1.py            # live fetch (~180 polite requests)
  python research/estimate_sigma_w1.py --months 6 --stride 3
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import statistics as st
import time
import urllib.request
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

STATION = "KNYC"
OBS_NETWORK = "NY_ASOS"
OBS_STATION = "NYC"
STATION_TZ = ZoneInfo("America/New_York")
MODEL = "MEX"  # GFS extended MOS: dense archive, clean daily n_x max/min
UA = {"User-Agent": "apacenye-research/0.1 (isaacsnc@gmail.com)"}
IEM = "https://mesonet.agron.iastate.edu"

# Lead buckets in hours (upper edges). W1 trades same-day, so the shortest bucket
# is what matters; longer buckets are reported for context/monotonicity sanity.
LEAD_BUCKETS_H = [48, 72, 96, 120, 168, 240]


# ----------------------------------------------------------------- pure helpers
# (unit-tested in tests/test_sigma_study.py — these carry the math the owner reviews)

@dataclass(frozen=True)
class ForecastMax:
    local_day: str      # ISO date of the local calendar day the max belongs to
    lead_h: float       # hours from model runtime to the 00Z valid time
    forecast_max_f: int  # forecast daytime high (°F)


def extract_daily_max_forecasts(rows: list[dict], tz: ZoneInfo = STATION_TZ) -> list[ForecastMax]:
    """Pull the daytime-MAX forecasts from one model run's CSV rows.

    The max is the n_x value whose forecast valid time (ftime) is at 00Z; its local
    calendar day is the tz-local date of that 00Z instant (which lands on the prior
    evening, i.e. the day the max actually occurred). Rows without n_x, or whose
    ftime is not 00Z (those are overnight mins), are skipped.
    """
    out: list[ForecastMax] = []
    for r in rows:
        if r.get("model") != MODEL:
            continue
        nx = (r.get("n_x") or "").strip()
        ftime = (r.get("ftime") or "").strip()
        runtime = (r.get("runtime") or "").strip()
        if not nx or not ftime or not runtime:
            continue
        ft = datetime.fromisoformat(ftime)
        if ft.astimezone(timezone.utc).strftime("%H:%M") != "00:00":
            continue  # 12Z rows are overnight minima, not the daytime max
        rt = datetime.fromisoformat(runtime)
        lead_h = (ft - rt).total_seconds() / 3600.0
        local_day = ft.astimezone(tz).date().isoformat()
        out.append(ForecastMax(local_day=local_day, lead_h=lead_h, forecast_max_f=int(nx)))
    return out


def forecast_errors(
    forecasts: list[ForecastMax], observed: dict[str, float]
) -> list[tuple[str, float, float]]:
    """Join forecasts to observed highs → (local_day, lead_h, error=forecast−observed)."""
    errs: list[tuple[str, float, float]] = []
    for f in forecasts:
        o = observed.get(f.local_day)
        if o is None:
            continue
        errs.append((f.local_day, f.lead_h, f.forecast_max_f - o))
    return errs


def sigma_by_lead_bucket(
    errors: list[tuple[str, float, float]], buckets: list[int] = LEAD_BUCKETS_H
) -> dict[str, dict]:
    """Population σ, bias (mean error) and n of the error, grouped by lead bucket.

    Bucket key is the upper edge in hours ("≤48h"); an error falls in the first
    bucket whose edge it does not exceed. Population (not sample) std is used
    deliberately: we are describing the observed error spread, not inferring a
    parameter, and it keeps small-n buckets from looking spuriously wide.
    """
    grouped: dict[int, list[float]] = {b: [] for b in buckets}
    for _day, lead_h, err in errors:
        for b in buckets:
            if lead_h <= b:
                grouped[b].append(err)
                break
    result: dict[str, dict] = {}
    for b in buckets:
        vals = grouped[b]
        if not vals:
            continue
        result[f"<= {b}h"] = {
            "n": len(vals),
            "bias_f": round(st.mean(vals), 2),
            "sigma_f": round(st.pstdev(vals), 2) if len(vals) > 1 else None,
        }
    return result


def recommend_sigma(
    errors: list[tuple[str, float, float]], short_lead_max_h: float = 48.0
) -> dict:
    """Conservative σ for W1's same-day horizon: the population std of the error at
    the shortest available lead (≤ short_lead_max_h), rounded UP to one decimal.

    Rounding up (never down) keeps the model honestly humble — a slightly wide σ
    under-sizes trades, which is the safe direction. Intraday tightening below this
    is left to live shadow-forecast calibration (OD-9), the sanctioned evidence path.
    """
    short = [e for e in errors if e[1] <= short_lead_max_h]
    vals = [e[2] for e in short]
    if len(vals) < 2:
        raise ValueError(f"not enough short-lead samples (n={len(vals)}) to estimate σ")
    raw = st.pstdev(vals)
    import math
    return {
        "short_lead_max_h": short_lead_max_h,
        "n": len(vals),
        "bias_f": round(st.mean(vals), 2),
        "raw_sigma_f": round(raw, 3),
        "recommended_sigma_f": math.ceil(raw * 10) / 10,  # round UP to 0.1°F
    }


# ------------------------------------------------------------------- live fetch

def _get(url: str, timeout: int = 60) -> str:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def fetch_observed_highs(years: list[int]) -> dict[str, float]:
    obs: dict[str, float] = {}
    for yr in years:
        url = f"{IEM}/api/1/daily.json?network={OBS_NETWORK}&station={OBS_STATION}&year={yr}"
        data = json.loads(_get(url))
        for row in data.get("data", []):
            if row.get("max_tmpf") is not None:
                obs[row["date"]] = float(row["max_tmpf"])
    return obs


def fetch_run(runtime: datetime) -> list[dict]:
    """One model run via explicit runtime= (the only way to reach the archive)."""
    stamp = runtime.strftime("%Y-%m-%dT%H%M")
    url = f"{IEM}/mos/csv.php?station={STATION}&model={MODEL}&runtime={stamp}"
    return list(csv.DictReader(io.StringIO(_get(url))))


def run_study(months: int, stride_days: int, delay_s: float = 0.1) -> dict:
    end = datetime.now(timezone.utc).date() - timedelta(days=1)
    start = end - timedelta(days=int(months * 30.4))
    years = sorted({start.year, end.year})
    print(f"observed: fetching KNYC daily highs for {years} ...")
    observed = fetch_observed_highs(years)
    print(f"  {len(observed)} observed days")

    forecasts: list[ForecastMax] = []
    runtimes: list[datetime] = []
    d = start
    while d <= end:
        # 12Z run each sampled day (shortest max lead ~36h)
        runtimes.append(datetime(d.year, d.month, d.day, 12, tzinfo=timezone.utc))
        d += timedelta(days=stride_days)
    print(f"forecast: fetching {len(runtimes)} {MODEL} runs "
          f"({months} months, every {stride_days}d) ...")
    ok = 0
    for i, rt in enumerate(runtimes):
        try:
            forecasts.extend(extract_daily_max_forecasts(fetch_run(rt)))
            ok += 1
        except Exception as exc:  # a missing run must not abort the study
            print(f"  [{i}] {rt:%Y-%m-%d} skipped: {exc!r}")
        if delay_s:
            time.sleep(delay_s)
        if (i + 1) % 25 == 0:
            print(f"  {i + 1}/{len(runtimes)} runs, {len(forecasts)} max-forecasts so far")

    errors = forecast_errors(forecasts, observed)
    by_lead = sigma_by_lead_bucket(errors)
    rec = recommend_sigma(errors)
    provenance = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "station": STATION,
        "forecast_source": f"IEM {MODEL} (GFS extended MOS) daytime max n_x @00Z",
        "truth_source": f"IEM {OBS_NETWORK}/{OBS_STATION} daily max_tmpf (ASOS)",
        "window": {"start": start.isoformat(), "end": end.isoformat(),
                   "months": months, "stride_days": stride_days},
        "runs_requested": len(runtimes), "runs_ok": ok,
        "n_forecast_maxes": len(forecasts), "n_errors_joined": len(errors),
        "sigma_by_lead": by_lead,
        "recommendation": rec,
        "caveats": [
            "MEX is a PROXY for the live NDFD/api.weather.gov gridpoint signal W1 uses,"
            " not the exact same forecast; shadow-forecast calibration (OD-9) is the"
            " eventual truth.",
            "Observed = ASOS max_tmpf, a close but not identical proxy for the NWS"
            " Climatological Report high Kalshi settles on.",
            "Shortest MEX max lead is ~36h, so σ here is a slightly conservative upper"
            " bound for W1's same-day (intraday) horizon.",
            "Illustrative-only until corroborated by our own shadow forecasts.",
        ],
    }
    return provenance


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--stride", type=int, default=2, help="days between sampled runs")
    ap.add_argument("--delay", type=float, default=0.1)
    ap.add_argument("--out", default="research/sigma_w1_study.json")
    args = ap.parse_args()
    prov = run_study(args.months, args.stride, args.delay)
    print("\n=== σ by lead bucket ===")
    for k, v in prov["sigma_by_lead"].items():
        print(f"  {k:>7}: n={v['n']:>4} bias={v['bias_f']:+.2f}  sigma={v['sigma_f']}")
    print("\n=== recommendation ===")
    for k, v in prov["recommendation"].items():
        print(f"  {k}: {v}")
    with open(args.out, "w") as f:
        json.dump(prov, f, indent=2)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
