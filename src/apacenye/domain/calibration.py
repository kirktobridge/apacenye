"""Calibration math — the evidence tooling behind the `review-calibration`
skill (steps 2–6). Pure functions on plain sequences: Brier score, decile
reliability binning, and a composed report that splits the qualified/traded
subsets and stamps a mechanical verdict.

Plain-language summary: a shadow forecast is scored against what actually
happened. The Brier score is the mean squared error of a probability — lower
is better, and the number to beat is the market mid's own Brier (a model that
loses to the mid has no business proposing trades). The reliability table
sorts predictions into ten probability buckets and asks, in each, "of the
times the model said ~35%, how often did it actually happen?" — the gap
between claimed and observed probability is where miscalibration lives.

This module computes and reports; it NEVER recommends λ/k/σ values (OD-9
forbids auto-tuning). Parameter changes are a human step in the skill, then
owner ratification, then `dev-cycle`.

Money-adjacent: the owner reviews this personally, so it stays boring and
explicit. Nothing here touches SQL, the network, or the clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

# The sample-size honesty gate (review-calibration step 2): under this many
# SCOREABLE rows the verdict is hard-stamped insufficient-data. Same-event
# brackets are correlated (one weather outcome drives all of them), so the
# report also prints the distinct-event count — the effective sample size.
INSUFFICIENT_DATA_ROWS = 100

# How far the model's average probability may sit from the observed base rate
# before the aggregate bias is called out (5 percentage points). Stated in the
# printed report so the owner audits the rule, not just the answer.
CALIBRATION_BIAS_THRESHOLD = 0.05


@dataclass(frozen=True)
class ScoredEval:
    """One settled shadow forecast, ready to score.

    `model_probability` and `market_implied_probability` are BOTH P(YES) of
    the ticker (never side-relative — see the W1 worker's evaluation logging).
    `outcome` is 1.0 if the market settled YES else 0.0. `traded` is True when
    the evaluation emitted an intent (intent_id is not NULL).
    """

    model_probability: float
    market_implied_probability: float | None
    outcome: float
    qualified: bool
    traded: bool
    event_ticker: str = ""


@dataclass(frozen=True)
class ReliabilityBin:
    """One probability bucket of the reliability table. Empty buckets keep
    their place with n=0 and None metrics — a sparse table for weeks is
    correct output, not a bug."""

    lo: float
    hi: float
    n: int
    mean_pred: float | None
    observed_freq: float | None
    gap: float | None  # observed_freq − mean_pred (positive ⇒ model too low)


@dataclass(frozen=True)
class SubsetMetrics:
    """Brier model-vs-market over one subset (all / qualified / traded /
    untraded), on rows that carry a two-sided quote (non-null mid)."""

    label: str
    n_rows: int
    n_events: int
    brier_model: float | None
    brier_market: float | None


@dataclass(frozen=True)
class CalibrationReport:
    """The full report the CLI renders. `n_scored` is settled rows carrying a
    two-sided quote; `n_excluded_null_mid` is settled rows dropped for having
    no mid (reported, never silently discarded)."""

    strategy_id: str
    n_scored: int
    n_events: int
    n_excluded_null_mid: int
    brier_model: float | None
    brier_market: float | None
    weighted_gap: float | None
    reliability: tuple[ReliabilityBin, ...]
    qualified: SubsetMetrics
    traded: SubsetMetrics
    untraded: SubsetMetrics
    adverse_selection: bool
    insufficient_data: bool
    verdict: str


def brier_score(predictions: Sequence[float], outcomes: Sequence[float]) -> float:
    """Mean squared error of a probability forecast: mean (p − outcome)².

    Lower is better; 0 is perfect, 0.25 is a coin flip on a 50/50 event.
    Raises on empty or mismatched inputs — an empty Brier is a caller bug, not
    a zero.
    """
    if len(predictions) != len(outcomes):
        raise ValueError(
            f"predictions/outcomes length mismatch: {len(predictions)} vs {len(outcomes)}"
        )
    if not predictions:
        raise ValueError("brier_score requires at least one observation")
    return sum((p - o) ** 2 for p, o in zip(predictions, outcomes)) / len(predictions)


def reliability_bins(
    predictions: Sequence[float], outcomes: Sequence[float], n_bins: int = 10
) -> list[ReliabilityBin]:
    """Sort predictions into `n_bins` equal-width buckets over [0, 1] and, in
    each, report count, mean predicted probability, observed frequency, and
    their gap.

    Bucket i covers [i/n_bins, (i+1)/n_bins); a prediction of exactly 1.0
    lands in the top bucket. Every bucket is returned, empty ones included, so
    the table shape is stable across runs.
    """
    if len(predictions) != len(outcomes):
        raise ValueError(
            f"predictions/outcomes length mismatch: {len(predictions)} vs {len(outcomes)}"
        )
    buckets: list[list[tuple[float, float]]] = [[] for _ in range(n_bins)]
    for p, o in zip(predictions, outcomes):
        if p < 0.0 or p > 1.0:
            raise ValueError(f"probability out of [0, 1] range: {p}")
        idx = min(int(p * n_bins), n_bins - 1)  # 1.0 → top bucket
        buckets[idx].append((p, o))
    bins: list[ReliabilityBin] = []
    for i in range(n_bins):
        b = buckets[i]
        lo, hi = i / n_bins, (i + 1) / n_bins
        if not b:
            bins.append(ReliabilityBin(lo, hi, 0, None, None, None))
            continue
        mean_pred = sum(p for p, _ in b) / len(b)
        observed = sum(o for _, o in b) / len(b)
        bins.append(ReliabilityBin(lo, hi, len(b), mean_pred, observed, observed - mean_pred))
    return bins


def _weighted_gap(bins: Sequence[ReliabilityBin]) -> float | None:
    """Count-weighted mean of the per-bucket gaps — equal to (mean observed −
    mean predicted) over all rows. Positive ⇒ the model under-forecasts
    probability on average; negative ⇒ it over-forecasts."""
    total = sum(b.n for b in bins)
    if total == 0:
        return None
    return sum(b.n * b.gap for b in bins if b.n) / total  # type: ignore[operator]


def _subset_metrics(label: str, rows: Sequence[ScoredEval]) -> SubsetMetrics:
    """Brier model-vs-market for one subset. Rows are assumed to carry a
    non-null mid (build_report filters before splitting)."""
    n = len(rows)
    n_events = len({r.event_ticker for r in rows})
    if n == 0:
        return SubsetMetrics(label, 0, 0, None, None)
    outs = [r.outcome for r in rows]
    bm = brier_score([r.model_probability for r in rows], outs)
    mk = brier_score([float(r.market_implied_probability) for r in rows], outs)
    return SubsetMetrics(label, n, n_events, bm, mk)


def _verdict(
    n_scored: int, brier_model: float | None, brier_market: float | None,
    weighted_gap: float | None,
) -> str:
    """Mechanically derive the one-line verdict. Losing to the market mid is
    the headline failure; otherwise a persistent aggregate bias names its
    direction; otherwise calibrated."""
    if n_scored < INSUFFICIENT_DATA_ROWS or brier_model is None:
        return "insufficient-data"
    if brier_market is not None and brier_model > brier_market:
        return "miscalibrated-loses-to-market-benchmark"
    if weighted_gap is None:
        return "calibrated"
    if weighted_gap > CALIBRATION_BIAS_THRESHOLD:
        return "miscalibrated-underforecasting"
    if weighted_gap < -CALIBRATION_BIAS_THRESHOLD:
        return "miscalibrated-overforecasting"
    return "calibrated"


def build_report(
    strategy_id: str, rows: Sequence[ScoredEval], *, n_bins: int = 10
) -> CalibrationReport:
    """Compose the full calibration report from settled shadow forecasts.

    `rows` are all settled evaluations for the strategy/window. Rows without a
    two-sided quote (null mid) are excluded from every metric — model and
    market are always scored on the IDENTICAL row set — and their count is
    reported. The insufficient-data gate keys off the scoreable row count.
    """
    scored = [r for r in rows if r.market_implied_probability is not None]
    n_excluded = len(rows) - len(scored)
    n = len(scored)
    n_events = len({r.event_ticker for r in scored})
    reliability = tuple(reliability_bins([r.model_probability for r in scored],
                                         [r.outcome for r in scored], n_bins))

    if n == 0:
        empty = SubsetMetrics("", 0, 0, None, None)
        return CalibrationReport(
            strategy_id, 0, 0, n_excluded, None, None, None, reliability,
            empty, empty, empty, adverse_selection=False,
            insufficient_data=True, verdict="insufficient-data",
        )

    outs = [r.outcome for r in scored]
    brier_model = brier_score([r.model_probability for r in scored], outs)
    brier_market = brier_score([float(r.market_implied_probability) for r in scored], outs)
    weighted_gap = _weighted_gap(reliability)

    qualified = _subset_metrics("qualified", [r for r in scored if r.qualified])
    traded = _subset_metrics("traded", [r for r in scored if r.traded])
    untraded = _subset_metrics("untraded", [r for r in scored if not r.traded])
    # Selection should IMPROVE the edge; if the traded subset scores WORSE
    # (higher Brier) than the untraded, the qualification rule is picking
    # adverse spots — flag it loudly (review-calibration step 5).
    adverse = (traded.brier_model is not None and untraded.brier_model is not None
               and traded.brier_model > untraded.brier_model)

    return CalibrationReport(
        strategy_id, n, n_events, n_excluded, brier_model, brier_market,
        weighted_gap, reliability, qualified, traded, untraded,
        adverse_selection=adverse,
        insufficient_data=n < INSUFFICIENT_DATA_ROWS,
        verdict=_verdict(n, brier_model, brier_market, weighted_gap),
    )
