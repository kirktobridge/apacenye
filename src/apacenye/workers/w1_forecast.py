"""W1-v0 — forecast-anchored fair value (weather), the reference worker.

Thesis (Stage 2 §3.1): retail flow anchors on the point forecast ("the app
says 86°"), but the correct object is a probability DISTRIBUTION over the
day's high; brackets near the point forecast get overpriced and tails get
mispriced. v0 models T_max ~ Normal(NWS forecast high, σ) with σ from the
station's historical forecast error (config; default 3.0°F — a placeholder
pending OD-11 archive work, stated honestly).

CAPITAL AT RISK (paper): worst case per event is the 5% event cap ($50 at
the $1,000 bankroll); worst case for the whole strategy is the 20% cap
($200) — if every W1 position settled worthless on the same day. Weather
across brackets of one event is ONE outcome (OD-7), so the event cap — not
diversification — is what bounds a bad forecast day. Cross-day/cross-city
correlation is mitigated only by the portfolio cap in v0 (OD-20).

Every evaluation emits a shadow forecast (traded or not) — the calibration
dataset that eventually justifies touching λ, k, or σ. This worker holds no
Kalshi client and cannot place orders; it proposes OrderIntents only.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from apacenye.contract import (
    Action,
    Evaluation,
    OrderIntent,
    QuoteSeen,
    Side,
    SizingTrace,
    Tick,
)
from apacenye.dataadapters.nws import ForecastHigh, NwsForecastAdapter
from apacenye.domain.fees import net_edge
from apacenye.domain.sizing import (
    clamp_contracts_to_caps,
    kelly_fraction,
    proposed_stake_dollars,
    shrink_probability,
)
from apacenye.domain.weather import bracket_probability
from apacenye.workers.base import StrategyWorker, WorkerContext

log = logging.getLogger(__name__)

REQUIRED_CONFIG = ("station", "grid_office", "grid_x", "grid_y", "event_ticker")


class W1ForecastWorker(StrategyWorker):
    def __init__(self, strategy_id: str, config: dict, ctx: WorkerContext,
                 adapter: NwsForecastAdapter | None = None):
        super().__init__(strategy_id, config, ctx)
        self.adapter = adapter  # injectable for tests/replay
        self.forecast: ForecastHigh | None = None

    # ------------------------------------------------------------- lifecycle

    async def _initialize(self) -> None:
        for key in REQUIRED_CONFIG:
            if key not in self.config:
                raise ValueError(f"W1 config missing required key: {key}")
        if self.adapter is None:
            self.adapter = NwsForecastAdapter(
                station=self.config["station"],
                grid_office=self.config["grid_office"],
                grid_x=int(self.config["grid_x"]),
                grid_y=int(self.config["grid_y"]),
            )
        # validate data access loudly; INIT must not emit intents
        self.forecast = await self.adapter.fetch_forecast_high()

    def _validate_config(self, new_config: dict) -> tuple[bool, str]:
        if "sigma_f" in new_config and float(new_config["sigma_f"]) <= 0:
            return False, "sigma_f must be positive"
        for key in ("station", "grid_office", "grid_x", "grid_y"):
            if key in new_config:
                return False, f"{key} is not hot-updatable; restart the worker"
        return True, "ok"

    def data_ages_seconds(self) -> dict[str, float]:
        if self.forecast is None:
            return {}
        age = (datetime.now(timezone.utc) - self.forecast.source_ts).total_seconds()
        return {"nws_forecast": age}

    # ------------------------------------------------------------ evaluation

    async def _evaluate(self, tick: Tick) -> None:
        await self._refresh_forecast(tick)
        if self.forecast is None:
            self.error_flags = ["no forecast available"]
            return
        staleness_s = float(self.config.get("staleness_s", 12 * 3600))
        forecast_age = (tick.now - self.forecast.source_ts).total_seconds()
        stale = forecast_age > staleness_s

        mu = self.forecast.high_f
        sigma = float(self.config.get("sigma_f", 3.0))
        event_ticker = self.config["event_ticker"]
        brackets = self.ctx.list_event_brackets(event_ticker)
        if not brackets:
            self.error_flags = [f"no brackets in catalog for {event_ticker}"]
            return

        # Self-enforced event-cap tracking: our OWN open cost in this event
        # plus what we propose this tick must stay inside the 5% event cap.
        # (First line of defense; the orchestrator's G7 is the binding line.)
        my_positions = self.ctx.get_positions(self.strategy_id)
        event_cost = sum(p["cost_basis_dollars"] for p in my_positions
                         if p["event_ticker"] == event_ticker)
        event_budget = self.ctx.risk.max_event_exposure_dollars - event_cost

        for info in brackets:
            snap = self.ctx.get_snapshot(info.ticker)
            p_model = bracket_probability(info.bracket_lo, info.bracket_hi, mu, sigma)
            if snap is None or snap.mid_dollars is None:
                await self.emit_evaluation(Evaluation(
                    strategy_id=self.strategy_id, ts=tick.now, market_ticker=info.ticker,
                    event_ticker=event_ticker, model_probability=p_model,
                    market_implied_probability=None, executable_price_dollars=None,
                    net_edge=None, qualified=False, note="no two-sided quote",
                ))
                continue

            # Evaluate BOTH directions: buy YES if the model is above the ask,
            # buy NO (same math, cost = 1 − bid) if the model is below the bid.
            candidates = []
            ask = snap.yes_ask_dollars
            bid = snap.yes_bid_dollars
            if ask is not None and 0.0 < ask < 1.0:
                candidates.append((Side.YES, p_model, ask))
            if bid is not None and 0.0 < 1.0 - bid < 1.0:
                candidates.append((Side.NO, 1.0 - p_model, round(1.0 - bid, 4)))

            best = None
            for side, p_win, exe_price in candidates:
                edge = net_edge(p_win, exe_price,
                                slippage_dollars=self.ctx.risk.slippage_allowance_dollars)
                if best is None or edge > best[3]:
                    best = (side, p_win, exe_price, edge)
            side, p_win, exe_price, edge = best
            qualified = (edge >= self.ctx.risk.min_net_edge) and not stale
            intent_id = None

            if qualified and event_budget > 0:
                intent = self._build_intent(tick, info.ticker, side, p_win, exe_price,
                                            edge, snap, event_budget, forecast_age)
                if intent is not None:
                    await self.emit_intent(intent)
                    intent_id = intent.intent_id
                    event_budget -= intent.size_contracts * intent.limit_price_dollars

            note = "stale forecast; no intents" if stale else ""
            await self.emit_evaluation(Evaluation(
                strategy_id=self.strategy_id, ts=tick.now, market_ticker=info.ticker,
                event_ticker=event_ticker, model_probability=p_model,
                market_implied_probability=snap.mid_dollars,
                executable_price_dollars=exe_price, net_edge=edge,
                qualified=bool(intent_id), intent_id=intent_id, note=note,
            ))

        if stale:
            # Stale input ⇒ no new intents AND cancel outstanding (Stage 2 §5).
            for iid in list(self._outstanding_intents):
                await self.emit_cancel(iid, "forecast went stale")

    def _build_intent(self, tick: Tick, ticker: str, side: Side, p_win: float,
                      exe_price: float, edge: float, snap, event_budget: float,
                      forecast_age_s: float) -> OrderIntent | None:
        risk = self.ctx.risk
        p_market = snap.mid_dollars if side is Side.YES else 1.0 - snap.mid_dollars
        bankroll = self.ctx.get_bankroll_dollars()
        stake = proposed_stake_dollars(
            p_model=p_win, p_market=p_market, cost_dollars=exe_price,
            bankroll_dollars=bankroll, lam=risk.shrinkage_lambda,
            k=risk.kelly_multiplier,
        )
        kelly_contracts = int(stake / exe_price)
        depth = snap.executable_buy_depth(side)
        contracts, caps_applied = clamp_contracts_to_caps(
            kelly_contracts, price_dollars=exe_price,
            event_headroom_dollars=event_budget,
            strategy_headroom_dollars=risk.max_strategy_exposure_dollars,
            portfolio_headroom_dollars=risk.max_portfolio_exposure_dollars,
            top_of_book_depth=depth, max_depth_fraction=risk.max_depth_fraction,
            max_order_contracts=risk.max_order_contracts,
        )
        if contracts < 1:
            return None
        p_used = shrink_probability(p_win, p_market, risk.shrinkage_lambda)
        # confidence basis: forecast freshness (1.0 fresh → 0 at staleness),
        # scaled by a 0.7 ceiling because v0's Gaussian is deliberately crude
        staleness_s = float(self.config.get("staleness_s", 12 * 3600))
        confidence = round(0.7 * max(0.0, 1.0 - forecast_age_s / staleness_s), 3)
        assert self.forecast is not None
        return OrderIntent(
            strategy_id=self.strategy_id, ts=tick.now, market_ticker=ticker,
            side=side, action=Action.OPEN,
            limit_price_dollars=exe_price,
            size_contracts=contracts,
            ttl_seconds=int(self.config.get("intent_ttl_s", 600)),
            model_probability=p_win,
            market_implied_probability=p_market,
            net_edge=edge,
            confidence=confidence,
            key_inputs={
                "nws_forecast_high_f": self.forecast.high_f,
                "sigma_f": float(self.config.get("sigma_f", 3.0)),
                "forecast_ts": self.forecast.source_ts.isoformat(),
                "quote_ts": snap.ts.isoformat(),
            },
            sizing=SizingTrace(
                p_used=p_used, kelly_f=kelly_fraction(p_used, exe_price),
                k=risk.kelly_multiplier, lam=risk.shrinkage_lambda,
                bankroll_seen_dollars=bankroll, caps_applied=caps_applied,
            ),
            rationale=(f"Model gives {side.value.upper()} {p_win:.0%} vs executable "
                       f"{exe_price:.2f} — net edge {edge:.3f} after fees+slippage "
                       f"clears the {risk.min_net_edge:.2f} floor."),
            quote_seen=QuoteSeen(
                bid_dollars=snap.yes_bid_dollars, ask_dollars=snap.yes_ask_dollars,
                bid_depth=snap.yes_bid_depth, ask_depth=snap.yes_ask_depth, ts=snap.ts,
            ),
        )

    async def _refresh_forecast(self, tick: Tick) -> None:
        """Refetch at most every `forecast_refresh_s` (default 30 min); a
        failed refresh keeps the previous forecast — G4/staleness decides
        whether it is still usable, loudly."""
        refresh_s = float(self.config.get("forecast_refresh_s", 1800))
        if (self.forecast is not None
                and (tick.now - self.forecast.fetched_ts).total_seconds() < refresh_s):
            return
        try:
            assert self.adapter is not None
            self.forecast = await self.adapter.fetch_forecast_high()
        except Exception as exc:
            log.warning("[%s] forecast refresh failed: %s", self.strategy_id, exc)
            self.error_flags = [f"forecast refresh failed: {exc!r}"]
