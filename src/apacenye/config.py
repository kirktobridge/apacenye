"""Configuration — Stage 3 §10 layering, exactly.

- `.env` (gitignored): secrets and machine/mode facts only. Loaded here into
  `AppSettings`; secret fields use `SecretStr` so any serialization or log
  line shows `**********`, never the value.
- `config/risk.yaml` (committed): risk limits — reviewable in git. Env
  overrides use the `RISK__` prefix (e.g. `RISK__MAX_ORDER_CONTRACTS=50`)
  and are LOGGED at startup so the effective config is always visible.
- `config/strategies/<id>.yaml` (committed): strategy parameters.

RUN_MODE=LIVE refuses to boot here — this is hard-disable wall #1 of two
(the second is execution/live.py, which contains no submission code at all).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml
from pydantic import BaseModel, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from apacenye.contract import RunMode

log = logging.getLogger(__name__)


class LiveDisabledStartupError(RuntimeError):
    """Raised at boot when RUN_MODE=LIVE is requested in this bootstrap."""


class AppSettings(BaseSettings):
    """Process-level settings from `.env` / environment."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Kalshi API — read-only market data in this bootstrap
    kalshi_api_key_id: SecretStr = SecretStr("")
    kalshi_private_key_path: Path = Path("secrets/kalshi_private.pem")
    kalshi_env: str = "prod"  # prod | demo

    run_mode: RunMode = RunMode.PAPER

    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8642
    dashboard_token: SecretStr = SecretStr("")

    data_dir: Path = Path("data")

    # Backups (B-5): out-of-tree snapshots of the ledger + capture tree. The
    # default lives OUTSIDE the working tree so a data/ wipe cannot take the
    # backups with it. interval_s <= 0 disables the periodic loop in serve;
    # manual `apacenye backup` always works. B-15 points BACKUP_DIR off-box.
    backup_dir: Path = Path.home() / "apacenye-backups"
    backup_interval_s: float = 3600.0   # hourly
    backup_retention: int = 24          # rolling ~24h of snapshots

    @field_validator("run_mode")
    @classmethod
    def _refuse_live(cls, v: RunMode) -> RunMode:
        # ALWAYS-APPLY RULE 1 (CLAUDE.md): PAPER-ONLY bootstrap. Live
        # enablement is deferred to a dedicated future hardening session with
        # its own acceptance gate; nothing in this repo may shortcut that.
        if v is RunMode.LIVE:
            raise LiveDisabledStartupError(
                "RUN_MODE=LIVE refuses to boot: live trading is hard-disabled in "
                "this bootstrap. Enabling real capital requires a future dedicated "
                "hardening session with its own acceptance gate (Stage 3 §6)."
            )
        return v

    def validate_dashboard_binding(self) -> None:
        """Non-localhost binding without a token is a startup error (OD-18)."""
        if self.dashboard_host not in ("127.0.0.1", "localhost") and not self.dashboard_token.get_secret_value():
            raise RuntimeError(
                f"DASHBOARD_HOST={self.dashboard_host} requires a non-empty DASHBOARD_TOKEN"
            )

    @property
    def kill_sentinel_path(self) -> Path:
        return self.data_dir / "KILL"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "apacenye.sqlite"

    @property
    def ack_log_path(self) -> Path:
        return self.data_dir / "acks" / "acknowledgments.jsonl"

    @property
    def capture_dir(self) -> Path:
        return self.data_dir / "capture"


class RiskConfig(BaseModel):
    """Every value here is enforceable by the gate pipeline (Stage 3 §3.2).

    Defaults mirror config/risk.yaml; conservative pending live data. Do not
    loosen without user ratification (ALWAYS-APPLY RULE 4).
    """

    bankroll_usd: float = 1000.0          # OD-8, paper notional
    max_event_exposure_pct: float = 5.0   # all brackets of one event = one exposure (OD-7)
    max_strategy_exposure_pct: float = 20.0
    max_portfolio_exposure_pct: float = 50.0  # OD-16 — always dominates
    max_order_contracts: int = 100        # unit-bug backstop — never remove
    max_depth_fraction: float = 0.25      # of visible top-of-book depth
    strategy_daily_loss_pct: float = 2.0  # breach ⇒ auto-PAUSE (human un-pause)
    portfolio_daily_loss_pct: float = 5.0  # OD-17 — breach ⇒ kill switch trips
    heartbeat_timeout_s: float = 120.0
    max_worker_restarts_per_day: int = 3
    # sizing hyperparameters (OD-9 — change only on calibration evidence)
    shrinkage_lambda: float = 0.5
    kelly_multiplier: float = 0.25
    min_net_edge: float = 0.04            # OD-4, user-ratified
    slippage_allowance_dollars: float = 0.01

    @property
    def max_event_exposure_dollars(self) -> float:
        return self.bankroll_usd * self.max_event_exposure_pct / 100.0

    @property
    def max_strategy_exposure_dollars(self) -> float:
        return self.bankroll_usd * self.max_strategy_exposure_pct / 100.0

    @property
    def max_portfolio_exposure_dollars(self) -> float:
        return self.bankroll_usd * self.max_portfolio_exposure_pct / 100.0


def load_risk_config(path: str | Path = "config/risk.yaml") -> RiskConfig:
    """YAML file + `RISK__` env overrides; every override is logged so the
    effective configuration is never a surprise."""
    raw: dict = {}
    p = Path(path)
    if p.exists():
        raw = yaml.safe_load(p.read_text()) or {}
    for key, value in os.environ.items():
        if key.startswith("RISK__"):
            field = key[len("RISK__"):].lower()
            if field in RiskConfig.model_fields:
                log.warning("risk config override from env: %s=%s", field, value)
                raw[field] = value
    return RiskConfig(**raw)


def load_strategy_config(strategy_id: str, base: str | Path = "config/strategies") -> dict:
    p = Path(base) / f"{strategy_id.lower()}.yaml"
    if not p.exists():
        raise FileNotFoundError(f"strategy config not found: {p}")
    return yaml.safe_load(p.read_text()) or {}
