"""Load config.yaml + .env into one Config object. Nothing else reads YAML or os.environ directly."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(os.environ.get("TITAN_ROOT", Path(__file__).resolve().parent.parent))

SECRET_KEYS = [
    "APIFY_TOKEN", "ANTHROPIC_API_KEY", "GMAIL_OAUTH_JSON", "GMAIL_TOKEN_JSON", "GMAIL_SENDER",
    "REPORT_TO", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_MESSAGING_SERVICE_SID",
    "WEBHOOK_PUBLIC_URL", "BLOB_READ_WRITE_TOKEN",
]

VALID_MODES = {"DRAFT_FOR_APPROVAL", "AUTO_SEND_FIRST_TOUCH", "FULL_AUTO"}
VALID_CHANNELS = {"SMS_FIRST", "EMAIL_FIRST", "BOTH"}


class ConfigError(Exception):
    pass


@dataclass
class Config:
    raw: dict[str, Any]
    secrets: dict[str, str] = field(default_factory=dict, repr=False)
    root: Path = ROOT

    # --- generic access -------------------------------------------------
    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.raw
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def __getitem__(self, key: str) -> Any:
        return self.raw[key]

    def secret(self, key: str) -> str | None:
        """Return a credential or None. Never log the value."""
        v = self.secrets.get(key) or None
        return v

    def has(self, *keys: str) -> bool:
        return all(self.secret(k) for k in keys)

    # --- convenience -----------------------------------------------------
    @property
    def mode(self) -> str:
        m = os.environ.get("TITAN_MODE") or self.get("outreach.mode", "DRAFT_FOR_APPROVAL")
        if m not in VALID_MODES:
            raise ConfigError(f"outreach.mode must be one of {sorted(VALID_MODES)}, got {m!r}")
        return m

    @property
    def channel(self) -> str:
        c = os.environ.get("TITAN_CHANNEL") or self.get("outreach.channel", "SMS_FIRST")
        if c not in VALID_CHANNELS:
            raise ConfigError(f"outreach.channel must be one of {sorted(VALID_CHANNELS)}, got {c!r}")
        return c

    def path(self, key: str) -> Path:
        p = Path(self.get(f"paths.{key}"))
        return p if p.is_absolute() else self.root / p

    @property
    def db_path(self) -> Path:
        override = os.environ.get("TITAN_DB")
        return Path(override) if override else self.path("db")


def _validate(raw: dict[str, Any]) -> None:
    required = [
        "operator.sender_name", "market.counties", "market.price_min", "market.price_max",
        "formula.arv_multiplier", "formula.assignment_fee", "ranking.hot_min_motivation",
        "outreach.daily_email_cap", "outreach.daily_sms_cap", "scoring.model", "repairs.cosmetic",
    ]
    c = Config(raw)
    missing = [k for k in required if c.get(k) is None]
    if missing:
        raise ConfigError(f"config.yaml missing keys: {missing}")
    if c.get("market.price_min") >= c.get("market.price_max"):
        raise ConfigError("market.price_min must be < market.price_max")
    _ = c.mode, c.channel


_CACHE: Config | None = None


def load(path: Path | str | None = None, env_path: Path | str | None = None, force: bool = False) -> Config:
    global _CACHE
    if _CACHE is not None and not force and path is None:
        return _CACHE
    cfg_path = Path(path) if path else ROOT / "config.yaml"
    env_file = Path(env_path) if env_path else ROOT / ".env"
    if env_file.exists():
        load_dotenv(env_file, override=False)
    with open(cfg_path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    _validate(raw)
    secrets = {k: os.environ.get(k, "") for k in SECRET_KEYS}
    cfg = Config(raw=raw, secrets=secrets, root=cfg_path.parent)
    if path is None:
        _CACHE = cfg
    return cfg
