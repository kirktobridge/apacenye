"""LIVE execution — hard-disabled. This file intentionally contains NO order
submission code (ALWAYS-APPLY RULE 1; Stage 3 §6).

"Hard-disabled" here means structural, not a flag: turning live trading on
would require WRITING NEW CODE in a future dedicated hardening session with
its own acceptance gate — there is no value anywhere in this repository that
can be flipped to make real capital reachable.

What must be true before that future session may even begin (Stage 5 brief,
documented here at the wall itself):
1. OD-1/OD-2/OD-3 verified against live data (real fee schedule per series,
   real spreads/depth, current listings).
2. Weeks of our own order-book capture, with calibration evidence (shadow
   forecasts vs. outcomes) — illustrative-only backtests count for NOTHING.
3. A fresh live-gate concept checkpoint (bootstrap-era acks pre-authorize
   nothing).
4. Its own acceptance gate, defined in that session, reviewed by the owner.
"""


class LiveDisabledError(RuntimeError):
    """Raised anywhere the live execution path is touched in this bootstrap."""

    def __init__(self) -> None:
        super().__init__(
            "live trading is hard-disabled in this bootstrap: no live "
            "order-submission code exists. Enabling real capital requires a "
            "future dedicated hardening session with its own acceptance gate."
        )


def make_live_client(*_args, **_kwargs):
    """The execution factory's live branch. Constructs nothing, ever."""
    raise LiveDisabledError()
