"""W1-v0 probability model — Stage 2 §3.1.

Plain-language summary: model the day's maximum temperature as a normal
(bell-curve) distribution centered on the NWS forecast high, with a spread
σ equal to the station's historical forecast error. The probability that the
max lands inside a market bracket is the area under that curve between the
bracket's edges, widened by half a degree on each side because brackets are
whole degrees ("85–89" really covers 84.5–89.5 of the continuous variable).

This Gaussian is DELIBERATELY crude (Stage 2's words): its job is to prove
the architecture and start accumulating calibration data, not to be the best
possible forecast. The v1 upgrade replaces it with ensemble-model output
without touching any other code.
"""

from math import erf, sqrt


def _normal_cdf(x: float, mu: float, sigma: float) -> float:
    return 0.5 * (1.0 + erf((x - mu) / (sigma * sqrt(2.0))))


def bracket_probability(
    lo: float | None,
    hi: float | None,
    mu: float,
    sigma: float,
) -> float:
    """P(lo ≤ Tmax ≤ hi) under Normal(mu, sigma), continuity-corrected.

    lo=None means an open-ended "hi or below" bracket; hi=None means
    "lo or above". Both open ends use the same ±0.5° correction so a complete
    bracket set sums to exactly 1.
    """
    if sigma <= 0:
        raise ValueError(f"sigma must be positive, got {sigma}")
    upper = 1.0 if hi is None else _normal_cdf(hi + 0.5, mu, sigma)
    lower = 0.0 if lo is None else _normal_cdf(lo - 0.5, mu, sigma)
    return max(0.0, upper - lower)
