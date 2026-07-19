"""Kalshi API client — READ-ONLY in this bootstrap, by construction.

This class exposes market data only: catalog, order books, trades, and
settlement status. THERE ARE NO ORDER METHODS IN THIS FILE — order
submission in PAPER mode is the internal fill simulator (execution/paper.py)
and the live path is a stub that raises (execution/live.py). Adding an order
method here would violate ALWAYS-APPLY RULE 1 and must not happen in this
bootstrap.

Auth (OD-19, verified against docs.kalshi.com on 2026-07-18):
  headers  KALSHI-ACCESS-KEY / KALSHI-ACCESS-TIMESTAMP / KALSHI-ACCESS-SIGNATURE
  message  = timestamp_ms + HTTP method + path (path WITHOUT query string)
  signature= RSA-PSS(SHA-256, MGF1-SHA-256, salt = digest length), base64
Public market-data endpoints work unauthenticated; when a key is configured
we sign anyway (higher rate-limit tier, and it exercises the auth path).

Rate limits (verified same day): token bucket, Basic tier ≈ 200 read
tokens/s, most requests cost 10 tokens (≈20 req/s). We self-limit well below
that and apply exponential backoff with jitter on 429, per the docs.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import random
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from apacenye.contract import MarketSnapshot

log = logging.getLogger(__name__)

PROD_BASE = "https://api.elections.kalshi.com/trade-api/v2"
DEMO_BASE = "https://demo-api.kalshi.co/trade-api/v2"


class KalshiApiError(RuntimeError):
    pass


class _RateLimiter:
    """Simple async token bucket. Conservative default: 5 requests/second —
    a quarter of the documented Basic-tier read budget."""

    def __init__(self, rate_per_s: float = 5.0, burst: int = 5):
        self.rate = rate_per_s
        self.capacity = burst
        self.tokens = float(burst)
        self.updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return
                await asyncio.sleep((1.0 - self.tokens) / self.rate)


class KalshiClient:
    def __init__(
        self,
        api_key_id: str = "",
        private_key_path: str | Path | None = None,
        env: str = "prod",
        rate_per_s: float = 5.0,
        max_retries: int = 5,
    ):
        self.base = PROD_BASE if env == "prod" else DEMO_BASE
        self.api_key_id = api_key_id
        self._private_key = None
        if api_key_id and private_key_path and Path(private_key_path).exists():
            from cryptography.hazmat.primitives.serialization import load_pem_private_key

            self._private_key = load_pem_private_key(
                Path(private_key_path).read_bytes(), password=None
            )
        self._limiter = _RateLimiter(rate_per_s)
        self._max_retries = max_retries
        self._client = httpx.AsyncClient(timeout=15.0)

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------- auth

    def _auth_headers(self, method: str, path: str) -> dict[str, str]:
        if self._private_key is None:
            return {}
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        ts_ms = str(int(datetime.now(timezone.utc).timestamp() * 1000))
        # sign timestamp + method + path, path WITHOUT query parameters
        message = ts_ms + method.upper() + path.split("?")[0]
        signature = self._private_key.sign(
            message.encode(),
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                        salt_length=hashes.SHA256().digest_size),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts_ms,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
        }

    # ---------------------------------------------------------------- request

    async def _get(self, path: str, params: dict | None = None) -> dict:
        """GET with rate limiting and exponential backoff on 429/5xx."""
        url = self.base + path
        backoff = 1.0
        for attempt in range(self._max_retries + 1):
            await self._limiter.acquire()
            # signature covers the path portion after /trade-api/v2's host,
            # i.e. the full request path
            full_path = "/trade-api/v2" + path
            try:
                resp = await self._client.get(
                    url, params=params, headers=self._auth_headers("GET", full_path)
                )
            except httpx.HTTPError as exc:
                if attempt == self._max_retries:
                    raise KalshiApiError(f"GET {path} failed after retries: {exc}") from exc
                log.warning("kalshi GET %s network error (%s); retrying", path, exc)
                await asyncio.sleep(backoff + random.uniform(0, backoff / 2))
                backoff = min(backoff * 2, 30.0)
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == self._max_retries:
                    raise KalshiApiError(f"GET {path} -> {resp.status_code} after retries")
                log.warning("kalshi GET %s -> %s; backing off %.1fs", path,
                            resp.status_code, backoff)
                await asyncio.sleep(backoff + random.uniform(0, backoff / 2))
                backoff = min(backoff * 2, 30.0)
                continue
            if resp.status_code >= 400:
                raise KalshiApiError(f"GET {path} -> {resp.status_code}: {resp.text[:300]}")
            return resp.json()
        raise KalshiApiError(f"GET {path}: unreachable")  # pragma: no cover

    # ------------------------------------------------- read-only market data

    async def get_exchange_status(self) -> dict:
        return await self._get("/exchange/status")

    async def get_markets(self, *, series_ticker: str | None = None,
                          event_ticker: str | None = None,
                          status: str | None = None, limit: int = 200,
                          cursor: str | None = None) -> dict:
        params = {"limit": limit}
        if series_ticker:
            params["series_ticker"] = series_ticker
        if event_ticker:
            params["event_ticker"] = event_ticker
        if status:
            params["status"] = status
        if cursor:
            params["cursor"] = cursor
        return await self._get("/markets", params)

    async def get_market(self, ticker: str) -> dict:
        return await self._get(f"/markets/{ticker}")

    async def get_orderbook(self, ticker: str, depth: int = 8) -> dict:
        return await self._get(f"/markets/{ticker}/orderbook", {"depth": depth})

    async def get_trades(self, ticker: str, limit: int = 100) -> dict:
        return await self._get("/markets/trades", {"ticker": ticker, "limit": limit})

    async def get_snapshot(self, ticker: str, event_ticker: str = "") -> MarketSnapshot:
        """Fetch the order book and reduce it to a top-of-book MarketSnapshot.

        Kalshi's book lists resting BIDS per side in cents: `yes` levels are
        YES bids; `no` levels are NO bids, and a NO bid at c IS a YES ask at
        (100 − c) — the mirror identity from Stage 1 §1.3.
        """
        book = (await self.get_orderbook(ticker)).get("orderbook") or {}
        yes_levels = book.get("yes") or []
        no_levels = book.get("no") or []
        best_yes_bid = max(yes_levels, key=lambda l: l[0], default=None)
        best_no_bid = max(no_levels, key=lambda l: l[0], default=None)
        return MarketSnapshot(
            ticker=ticker,
            event_ticker=event_ticker,
            yes_bid_dollars=None if best_yes_bid is None else best_yes_bid[0] / 100.0,
            yes_ask_dollars=None if best_no_bid is None else (100 - best_no_bid[0]) / 100.0,
            yes_bid_depth=0 if best_yes_bid is None else int(best_yes_bid[1]),
            yes_ask_depth=0 if best_no_bid is None else int(best_no_bid[1]),
        )
