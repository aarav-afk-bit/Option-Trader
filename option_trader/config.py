"""Runtime configuration, read from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

VALID_PROVIDERS = ("nse", "angelone", "yahoo")


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc


def _providers_env() -> list[str]:
    raw = os.getenv("OPTION_TRADER_PROVIDERS", ",".join(VALID_PROVIDERS))
    providers = [p.strip().lower() for p in raw.split(",") if p.strip()]
    unknown = [p for p in providers if p not in VALID_PROVIDERS]
    if unknown:
        raise ValueError(f"Unknown provider(s) in OPTION_TRADER_PROVIDERS: {unknown}")
    return providers or list(VALID_PROVIDERS)


@dataclass
class Settings:
    providers: list[str] = field(default_factory=_providers_env)
    risk_free_rate: float = field(default_factory=lambda: _float_env("OPTION_TRADER_RISK_FREE_RATE", 0.065))
    cache_ttl: float = field(default_factory=lambda: _float_env("OPTION_TRADER_CACHE_TTL", 30.0))
    data_dir: Path = field(
        default_factory=lambda: Path(
            os.getenv("OPTION_TRADER_DATA_DIR") or Path.home() / ".option_trader"
        ).expanduser()
    )
    stale_seconds: float = field(default_factory=lambda: _float_env("OPTION_TRADER_STALE_SECONDS", 180.0))
    live_ticks: bool = field(default_factory=lambda: os.getenv("OPTION_TRADER_LIVE_TICKS", "1") not in ("0", "false", "no"))
    angel_api_key: str = field(default_factory=lambda: os.getenv("ANGEL_API_KEY", ""))
    angel_client_code: str = field(default_factory=lambda: os.getenv("ANGEL_CLIENT_CODE", ""))
    angel_pin: str = field(default_factory=lambda: os.getenv("ANGEL_PIN", ""))
    angel_totp_secret: str = field(default_factory=lambda: os.getenv("ANGEL_TOTP_SECRET", ""))

    @property
    def angel_configured(self) -> bool:
        return all((self.angel_api_key, self.angel_client_code, self.angel_pin, self.angel_totp_secret))


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
