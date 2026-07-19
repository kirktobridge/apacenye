"""The ledger — SQLite (WAL), explicit SQL, single source of truth.

Plain-language summary: every intent, disposition, order, fill, position,
evaluation, heartbeat, and kill event lands here. Workers hold NO
authoritative position state — on restart they rebuild from this ledger via
the orchestrator. All SQL in the project lives in this module (CLAUDE.md).

Money conventions:
- `cost_basis_dollars` = price × contracts actually paid for the OPEN side,
  excluding fees; `fees_paid_dollars` tracked alongside. A position's worst
  case (fully collateralized) = cost basis + fees paid.
- Exposure (for the risk gates) = open cost basis. All brackets of one
  settlement event aggregate into ONE exposure (OD-7), across strategies.
- equity = initial bankroll + all realized P&L. Paper equity is an
  OPTIMISTIC BOUND — see execution/paper.py.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from apacenye.contract import (
    CancelIntent,
    Disposition,
    Evaluation,
    ExplanationRecord,
    Fill,
    Heartbeat,
    OrderIntent,
    Side,
)

_DDL = """
CREATE TABLE IF NOT EXISTS markets (
    ticker TEXT PRIMARY KEY,
    event_ticker TEXT NOT NULL,
    bracket_lo REAL,
    bracket_hi REAL,
    status TEXT NOT NULL DEFAULT 'open',      -- open | settled
    settled_side TEXT,                        -- yes | no
    settled_ts TEXT
);
CREATE TABLE IF NOT EXISTS events (
    event_ticker TEXT PRIMARY KEY,
    title TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS intents (
    intent_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    market_ticker TEXT NOT NULL,
    side TEXT NOT NULL,
    action TEXT NOT NULL,
    limit_price_dollars REAL NOT NULL,
    size_contracts INTEGER NOT NULL,
    ttl_seconds INTEGER NOT NULL,
    payload_json TEXT NOT NULL                -- full OrderIntent, serialized
);
CREATE TABLE IF NOT EXISTS cancels (
    cancel_id TEXT PRIMARY KEY,
    intent_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dispositions (
    intent_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    status TEXT NOT NULL,                     -- APPROVED | RESIZED | REJECTED
    requested_size INTEGER NOT NULL,
    final_size INTEGER NOT NULL,
    binding_gates TEXT NOT NULL,              -- JSON list
    reason TEXT NOT NULL,
    ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,                -- == intent_id (idempotency key)
    strategy_id TEXT NOT NULL,
    market_ticker TEXT NOT NULL,
    side TEXT NOT NULL,
    action TEXT NOT NULL,
    limit_price_dollars REAL NOT NULL,
    size_contracts INTEGER NOT NULL,
    status TEXT NOT NULL,                     -- submitted | filled | partial | expired | cancelled
    ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fills (
    fill_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    intent_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    market_ticker TEXT NOT NULL,
    side TEXT NOT NULL,
    action TEXT NOT NULL,
    price_dollars REAL NOT NULL,
    count INTEGER NOT NULL,
    fee_dollars REAL NOT NULL,
    ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS positions (
    position_id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id TEXT NOT NULL,
    market_ticker TEXT NOT NULL,
    event_ticker TEXT NOT NULL,
    side TEXT NOT NULL,
    count INTEGER NOT NULL,
    cost_basis_dollars REAL NOT NULL,
    fees_paid_dollars REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',      -- open | closed
    opened_ts TEXT NOT NULL,
    closed_ts TEXT,
    UNIQUE (strategy_id, market_ticker, side, status)
);
CREATE TABLE IF NOT EXISTS realizations (
    realization_id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    market_ticker TEXT NOT NULL,
    event_ticker TEXT NOT NULL,
    kind TEXT NOT NULL,                       -- exit | settlement
    amount_dollars REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS cash_ledger (
    entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    strategy_id TEXT,
    kind TEXT NOT NULL,                       -- deposit | buy | sell | fee | settlement
    delta_dollars REAL NOT NULL,
    ref_id TEXT
);
CREATE TABLE IF NOT EXISTS evaluations (
    evaluation_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    market_ticker TEXT NOT NULL,
    event_ticker TEXT NOT NULL,
    model_probability REAL NOT NULL,
    market_implied_probability REAL,
    executable_price_dollars REAL,
    net_edge REAL,
    qualified INTEGER NOT NULL,
    intent_id TEXT,
    note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS heartbeats (
    heartbeat_id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    state TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS explanations (
    intent_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS config_versions (
    version_id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    scope TEXT NOT NULL,                      -- risk | strategy:<id>
    config_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kill_events (
    kill_id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,                       -- kill | unkill
    source TEXT NOT NULL,
    reason TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fills_strategy ON fills (strategy_id, ts);
CREATE INDEX IF NOT EXISTS idx_evals_strategy ON evaluations (strategy_id, ts);
CREATE INDEX IF NOT EXISTS idx_real_ts ON realizations (ts);
"""


def _iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).isoformat()


class Ledger:
    """All reads/writes go through named methods; no SQL leaves this module."""

    def __init__(self, db_path: str | Path, initial_bankroll_dollars: float):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(self._db_path))
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA foreign_keys=ON")
        self._con.executescript(_DDL)
        self.initial_bankroll_dollars = initial_bankroll_dollars
        self._con.commit()

    def close(self) -> None:
        self._con.close()

    # ------------------------------------------------------------------ markets

    def upsert_market(
        self,
        ticker: str,
        event_ticker: str,
        bracket_lo: float | None = None,
        bracket_hi: float | None = None,
    ) -> None:
        with self._con:
            self._con.execute(
                "INSERT INTO markets (ticker, event_ticker, bracket_lo, bracket_hi) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(ticker) DO UPDATE SET event_ticker=excluded.event_ticker, "
                "bracket_lo=excluded.bracket_lo, bracket_hi=excluded.bracket_hi",
                (ticker, event_ticker, bracket_lo, bracket_hi),
            )
            self._con.execute(
                "INSERT OR IGNORE INTO events (event_ticker) VALUES (?)", (event_ticker,)
            )

    def market_status(self, ticker: str) -> str | None:
        row = self._con.execute(
            "SELECT status FROM markets WHERE ticker = ?", (ticker,)
        ).fetchone()
        return row["status"] if row else None

    def event_for_ticker(self, ticker: str) -> str | None:
        row = self._con.execute(
            "SELECT event_ticker FROM markets WHERE ticker = ?", (ticker,)
        ).fetchone()
        return row["event_ticker"] if row else None

    # ------------------------------------------------------- intents & rulings

    def record_intent(self, intent: OrderIntent) -> None:
        with self._con:
            self._con.execute(
                "INSERT OR IGNORE INTO intents (intent_id, strategy_id, ts, market_ticker, "
                "side, action, limit_price_dollars, size_contracts, ttl_seconds, payload_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    intent.intent_id, intent.strategy_id, _iso(intent.ts),
                    intent.market_ticker, intent.side.value, intent.action.value,
                    intent.limit_price_dollars, intent.size_contracts,
                    intent.ttl_seconds, intent.model_dump_json(),
                ),
            )

    def record_cancel(self, cancel: CancelIntent) -> None:
        with self._con:
            self._con.execute(
                "INSERT OR IGNORE INTO cancels (cancel_id, intent_id, strategy_id, ts, reason) "
                "VALUES (?, ?, ?, ?, ?)",
                (cancel.cancel_id, cancel.intent_id, cancel.strategy_id,
                 _iso(cancel.ts), cancel.reason),
            )

    def record_disposition(self, d: Disposition) -> None:
        with self._con:
            self._con.execute(
                "INSERT OR REPLACE INTO dispositions (intent_id, strategy_id, status, "
                "requested_size, final_size, binding_gates, reason, ts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (d.intent_id, d.strategy_id, d.status.value, d.requested_size,
                 d.final_size, json.dumps(d.binding_gates), d.reason, _iso(d.ts)),
            )

    def record_order(self, intent: OrderIntent, final_size: int, status: str = "submitted") -> None:
        with self._con:
            self._con.execute(
                "INSERT OR IGNORE INTO orders (order_id, strategy_id, market_ticker, side, "
                "action, limit_price_dollars, size_contracts, status, ts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (intent.intent_id, intent.strategy_id, intent.market_ticker,
                 intent.side.value, intent.action.value, intent.limit_price_dollars,
                 final_size, status, _iso(intent.ts)),
            )

    def update_order_status(self, order_id: str, status: str) -> None:
        with self._con:
            self._con.execute(
                "UPDATE orders SET status = ? WHERE order_id = ?", (status, order_id)
            )

    # ----------------------------------------------------------------- fills

    def record_fill(self, fill: Fill) -> None:
        """Insert the fill and update position + cash, atomically.

        A duplicate fill_id is a no-op — replays can never double-count.
        Buys (open/increase) add to the position; sells (reduce/close)
        realize P&L proportionally against average cost.
        """
        event_ticker = self.event_for_ticker(fill.market_ticker) or ""
        with self._con:
            cur = self._con.execute(
                "INSERT OR IGNORE INTO fills (fill_id, order_id, intent_id, strategy_id, "
                "market_ticker, side, action, price_dollars, count, fee_dollars, ts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (fill.fill_id, fill.order_id, fill.intent_id, fill.strategy_id,
                 fill.market_ticker, fill.side.value, fill.action.value,
                 fill.price_dollars, fill.count, fill.fee_dollars, _iso(fill.ts)),
            )
            if cur.rowcount == 0:
                return  # duplicate — already applied

            if fill.action.value in ("open", "increase"):
                row = self._con.execute(
                    "SELECT position_id FROM positions WHERE strategy_id=? AND "
                    "market_ticker=? AND side=? AND status='open'",
                    (fill.strategy_id, fill.market_ticker, fill.side.value),
                ).fetchone()
                if row is None:
                    self._con.execute(
                        "INSERT INTO positions (strategy_id, market_ticker, event_ticker, "
                        "side, count, cost_basis_dollars, fees_paid_dollars, status, opened_ts) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?)",
                        (fill.strategy_id, fill.market_ticker, event_ticker,
                         fill.side.value, fill.count,
                         fill.count * fill.price_dollars, fill.fee_dollars, _iso(fill.ts)),
                    )
                else:
                    self._con.execute(
                        "UPDATE positions SET count = count + ?, "
                        "cost_basis_dollars = cost_basis_dollars + ?, "
                        "fees_paid_dollars = fees_paid_dollars + ? WHERE position_id = ?",
                        (fill.count, fill.count * fill.price_dollars,
                         fill.fee_dollars, row["position_id"]),
                    )
                self._con.execute(
                    "INSERT INTO cash_ledger (ts, strategy_id, kind, delta_dollars, ref_id) "
                    "VALUES (?, ?, 'buy', ?, ?)",
                    (_iso(fill.ts), fill.strategy_id,
                     -(fill.count * fill.price_dollars + fill.fee_dollars), fill.fill_id),
                )
            else:  # reduce / close — realize proportionally
                row = self._con.execute(
                    "SELECT * FROM positions WHERE strategy_id=? AND market_ticker=? "
                    "AND side=? AND status='open'",
                    (fill.strategy_id, fill.market_ticker, fill.side.value),
                ).fetchone()
                if row is None:
                    raise ValueError(
                        f"sell fill {fill.fill_id} has no open position "
                        f"({fill.strategy_id}/{fill.market_ticker}/{fill.side.value})"
                    )
                if fill.count > row["count"]:
                    raise ValueError("sell fill larger than open position")
                frac = fill.count / row["count"]
                cost_share = row["cost_basis_dollars"] * frac
                entry_fee_share = row["fees_paid_dollars"] * frac
                proceeds = fill.count * fill.price_dollars
                realized = proceeds - cost_share - entry_fee_share - fill.fee_dollars
                remaining = row["count"] - fill.count
                if remaining == 0:
                    self._con.execute(
                        "UPDATE positions SET count=0, cost_basis_dollars=0, "
                        "fees_paid_dollars=0, status='closed', closed_ts=? WHERE position_id=?",
                        (_iso(fill.ts), row["position_id"]),
                    )
                else:
                    self._con.execute(
                        "UPDATE positions SET count=?, cost_basis_dollars=?, "
                        "fees_paid_dollars=? WHERE position_id=?",
                        (remaining, row["cost_basis_dollars"] - cost_share,
                         row["fees_paid_dollars"] - entry_fee_share, row["position_id"]),
                    )
                self._con.execute(
                    "INSERT INTO realizations (ts, strategy_id, market_ticker, event_ticker, "
                    "kind, amount_dollars) VALUES (?, ?, ?, ?, 'exit', ?)",
                    (_iso(fill.ts), fill.strategy_id, fill.market_ticker,
                     event_ticker, realized),
                )
                self._con.execute(
                    "INSERT INTO cash_ledger (ts, strategy_id, kind, delta_dollars, ref_id) "
                    "VALUES (?, ?, 'sell', ?, ?)",
                    (_iso(fill.ts), fill.strategy_id,
                     proceeds - fill.fee_dollars, fill.fill_id),
                )

    # ------------------------------------------------------------- settlement

    def settle_market(self, ticker: str, settled_side: Side, ts: datetime | None = None) -> None:
        """Realize every open position in `ticker`: the `settled_side` holders
        receive $1 per contract, the other side receives $0. No settlement fee."""
        from apacenye.contract import utcnow

        ts = ts or utcnow()
        event_ticker = self.event_for_ticker(ticker) or ""
        with self._con:
            rows = self._con.execute(
                "SELECT * FROM positions WHERE market_ticker=? AND status='open'", (ticker,)
            ).fetchall()
            for row in rows:
                won = row["side"] == settled_side.value
                payout = float(row["count"]) if won else 0.0
                realized = payout - row["cost_basis_dollars"] - row["fees_paid_dollars"]
                self._con.execute(
                    "UPDATE positions SET count=0, cost_basis_dollars=0, fees_paid_dollars=0, "
                    "status='closed', closed_ts=? WHERE position_id=?",
                    (_iso(ts), row["position_id"]),
                )
                self._con.execute(
                    "INSERT INTO realizations (ts, strategy_id, market_ticker, event_ticker, "
                    "kind, amount_dollars) VALUES (?, ?, ?, ?, 'settlement', ?)",
                    (_iso(ts), row["strategy_id"], ticker, event_ticker, realized),
                )
                self._con.execute(
                    "INSERT INTO cash_ledger (ts, strategy_id, kind, delta_dollars, ref_id) "
                    "VALUES (?, ?, 'settlement', ?, ?)",
                    (_iso(ts), row["strategy_id"], payout, ticker),
                )
            self._con.execute(
                "UPDATE markets SET status='settled', settled_side=?, settled_ts=? WHERE ticker=?",
                (settled_side.value, _iso(ts), ticker),
            )

    # ---------------------------------------------------------------- queries

    def open_positions(self, strategy_id: str | None = None) -> list[dict]:
        sql = "SELECT * FROM positions WHERE status='open'"
        args: tuple = ()
        if strategy_id:
            sql += " AND strategy_id=?"
            args = (strategy_id,)
        return [dict(r) for r in self._con.execute(sql, args).fetchall()]

    def event_exposure_dollars(self, event_ticker: str) -> float:
        """Open cost basis across ALL brackets of one settlement event,
        across all strategies (OD-7: one event = one exposure)."""
        row = self._con.execute(
            "SELECT COALESCE(SUM(cost_basis_dollars), 0) AS s FROM positions "
            "WHERE event_ticker=? AND status='open'",
            (event_ticker,),
        ).fetchone()
        return float(row["s"])

    def strategy_exposure_dollars(self, strategy_id: str) -> float:
        row = self._con.execute(
            "SELECT COALESCE(SUM(cost_basis_dollars), 0) AS s FROM positions "
            "WHERE strategy_id=? AND status='open'",
            (strategy_id,),
        ).fetchone()
        return float(row["s"])

    def portfolio_exposure_dollars(self) -> float:
        row = self._con.execute(
            "SELECT COALESCE(SUM(cost_basis_dollars), 0) AS s FROM positions "
            "WHERE status='open'"
        ).fetchone()
        return float(row["s"])

    def realized_pnl_total_dollars(self) -> float:
        row = self._con.execute(
            "SELECT COALESCE(SUM(amount_dollars), 0) AS s FROM realizations"
        ).fetchone()
        return float(row["s"])

    def realized_pnl_today_dollars(self, strategy_id: str | None = None) -> float:
        """Realized P&L since 00:00 UTC today (daily-loss stops, G10)."""
        today = datetime.now(timezone.utc).date().isoformat()
        sql = "SELECT COALESCE(SUM(amount_dollars), 0) AS s FROM realizations WHERE ts >= ?"
        args: list = [today]
        if strategy_id:
            sql += " AND strategy_id=?"
            args.append(strategy_id)
        return float(self._con.execute(sql, args).fetchone()["s"])

    def equity_dollars(self) -> float:
        """Paper equity = initial bankroll + total realized P&L.

        OPTIMISTIC BOUND: realized P&L comes from the paper fill simulator,
        which ignores queue competition and market impact (Stage 3 §6.1).
        """
        return self.initial_bankroll_dollars + self.realized_pnl_total_dollars()

    def day_pnl_dollars(self, strategy_id: str | None, marks: dict[str, float]) -> float:
        """Day P&L for the G10 stops = realized today + unrealized vs. entry
        for open positions, marked at the supplied INDICATIVE mid prices
        (marks are for risk triggers only, never fills — Stage 3 §6.1).

        `marks` maps ticker → YES mid in dollars; NO positions are marked at
        (1 − mid). Positions with no mark contribute 0 unrealized (their risk
        remains bounded by cost basis, which the exposure caps already limit).
        """
        total = self.realized_pnl_today_dollars(strategy_id)
        for pos in self.open_positions(strategy_id):
            mid = marks.get(pos["market_ticker"])
            if mid is None:
                continue
            mark = mid if pos["side"] == Side.YES.value else 1.0 - mid
            total += pos["count"] * mark - pos["cost_basis_dollars"] - pos["fees_paid_dollars"]
        return total

    # ------------------------------------------------------------ evals & misc

    def record_evaluation(self, ev: Evaluation) -> None:
        with self._con:
            self._con.execute(
                "INSERT OR IGNORE INTO evaluations (evaluation_id, strategy_id, ts, "
                "market_ticker, event_ticker, model_probability, market_implied_probability, "
                "executable_price_dollars, net_edge, qualified, intent_id, note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ev.evaluation_id, ev.strategy_id, _iso(ev.ts), ev.market_ticker,
                 ev.event_ticker, ev.model_probability, ev.market_implied_probability,
                 ev.executable_price_dollars, ev.net_edge, int(ev.qualified),
                 ev.intent_id, ev.note),
            )

    def record_heartbeat(self, hb: Heartbeat) -> None:
        with self._con:
            self._con.execute(
                "INSERT INTO heartbeats (strategy_id, ts, state, payload_json) "
                "VALUES (?, ?, ?, ?)",
                (hb.strategy_id, _iso(hb.ts), hb.state.value, hb.model_dump_json()),
            )

    def record_explanation(self, rec: ExplanationRecord) -> None:
        with self._con:
            self._con.execute(
                "INSERT OR REPLACE INTO explanations (intent_id, strategy_id, ts, payload_json) "
                "VALUES (?, ?, ?, ?)",
                (rec.intent_id, rec.strategy_id, _iso(datetime.now(timezone.utc)),
                 rec.model_dump_json()),
            )

    def get_explanation(self, intent_id: str) -> dict | None:
        row = self._con.execute(
            "SELECT payload_json FROM explanations WHERE intent_id=?", (intent_id,)
        ).fetchone()
        return json.loads(row["payload_json"]) if row else None

    def record_kill_event(self, kind: str, source: str, reason: str) -> None:
        with self._con:
            self._con.execute(
                "INSERT INTO kill_events (ts, kind, source, reason) VALUES (?, ?, ?, ?)",
                (_iso(datetime.now(timezone.utc)), kind, source, reason),
            )

    def record_config_version(self, scope: str, config_hash: str, payload: dict) -> None:
        with self._con:
            self._con.execute(
                "INSERT INTO config_versions (ts, scope, config_hash, payload_json) "
                "VALUES (?, ?, ?, ?)",
                (_iso(datetime.now(timezone.utc)), scope, config_hash, json.dumps(payload)),
            )

    def recent_intents(self, since_iso: str | None = None, limit: int = 200) -> list[dict]:
        sql = (
            "SELECT i.intent_id, i.strategy_id, i.ts, i.market_ticker, i.side, i.action, "
            "i.limit_price_dollars, i.size_contracts, d.status, d.final_size, "
            "d.binding_gates, d.reason "
            "FROM intents i LEFT JOIN dispositions d ON d.intent_id = i.intent_id "
        )
        args: list = []
        if since_iso:
            sql += "WHERE i.ts >= ? "
            args.append(since_iso)
        sql += "ORDER BY i.ts DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in self._con.execute(sql, args).fetchall()]

    def recent_evaluations(self, strategy_id: str | None = None, limit: int = 500) -> list[dict]:
        sql = "SELECT * FROM evaluations "
        args: list = []
        if strategy_id:
            sql += "WHERE strategy_id=? "
            args.append(strategy_id)
        sql += "ORDER BY ts DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in self._con.execute(sql, args).fetchall()]

    def latest_heartbeat(self, strategy_id: str) -> dict | None:
        row = self._con.execute(
            "SELECT * FROM heartbeats WHERE strategy_id=? ORDER BY heartbeat_id DESC LIMIT 1",
            (strategy_id,),
        ).fetchone()
        return dict(row) if row else None
