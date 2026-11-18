"""Runtime configuration, read from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

VALID_PROVIDERS = ("tradier", "cboe", "yahoo")

# Settings saved from the in-app Settings page live in the data directory and take precedence
# over .env (they are the most recent choice). Secrets go to a separate owner-only file.
SETTINGS_FILE = "settings.env"
CREDENTIALS_FILE = "credentials.env"
EDITABLE_KEYS = {
    "OPTION_TRADER_PROVIDERS", "OPTION_TRADER_RISK_FREE_RATE", "OPTION_TRADER_CACHE_TTL",
    "OPTION_TRADER_STALE_SECONDS", "OPTION_TRADER_LIVE_TICKS", "TRADIER_SANDBOX",
    # US fees (see charges.py)
    "OPTION_TRADER_COMMISSION_PER_CONTRACT", "OPTION_TRADER_COMMISSION_PER_ORDER", "OPTION_TRADER_ORF",
    "OPTION_TRADER_OCC_FEE", "OPTION_TRADER_FINRA_TAF", "OPTION_TRADER_SEC_FEE",
}
SECRET_KEYS = {"TRADIER_TOKEN"}


def data_dir_from_env() -> Path:
    return Path(os.getenv("OPTION_TRADER_DATA_DIR") or Path.home() / ".option_trader").expanduser()


def load_saved_settings() -> None:
    d = data_dir_from_env()
    for name in (SETTINGS_FILE, CREDENTIALS_FILE):
        if (d / name).exists():
            load_dotenv(d / name, override=True)


def _write_env(path: Path, values: dict, private: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if path.exists():
        from dotenv import dotenv_values

        existing = {k: v for k, v in dotenv_values(path).items() if v is not None}
    existing.update({k: str(v) for k, v in values.items()})
    lines = [f'{k}="{v}"' for k, v in sorted(existing.items()) if v != ""]
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    if private:
        os.chmod(tmp, 0o600)  # owner read/write only
    os.replace(tmp, path)


def save_settings(values: dict, secrets: dict) -> None:
    """Persist settings from the Settings page and apply them to this process."""
    global _settings
    unknown = (set(values) - EDITABLE_KEYS) | (set(secrets) - SECRET_KEYS)
    if unknown:
        raise ValueError(f"Not editable: {sorted(unknown)}")
    d = data_dir_from_env()
    if values:
        _write_env(d / SETTINGS_FILE, values, private=False)
    if secrets:
        _write_env(d / CREDENTIALS_FILE, secrets, private=True)
    for k, v in {**values, **secrets}.items():
        if v == "":
            os.environ.pop(k, None)
        else:
            os.environ[k] = str(v)
    _settings = None
    Settings()  # validate now so a bad value fails the save, not the next request


load_saved_settings()


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc


def _providers_env() -> list[str]:
    raw = os.getenv("OPTION_TRADER_PROVIDERS", "tradier,cboe,yahoo")
    providers = [p.strip().lower() for p in raw.split(",") if p.strip()]
    unknown = [p for p in providers if p not in VALID_PROVIDERS]
    if unknown:
        raise ValueError(f"Unknown provider(s) in OPTION_TRADER_PROVIDERS: {unknown}")
    return providers or list(VALID_PROVIDERS)


@dataclass
class Settings:
    providers: list[str] = field(default_factory=_providers_env)
    risk_free_rate: float = field(default_factory=lambda: _float_env("OPTION_TRADER_RISK_FREE_RATE", 0.04))
    cache_ttl: float = field(default_factory=lambda: _float_env("OPTION_TRADER_CACHE_TTL", 30.0))
    data_dir: Path = field(
        default_factory=lambda: Path(
            os.getenv("OPTION_TRADER_DATA_DIR") or Path.home() / ".option_trader"
        ).expanduser()
    )
    stale_seconds: float = field(default_factory=lambda: _float_env("OPTION_TRADER_STALE_SECONDS", 180.0))
    live_ticks: bool = field(default_factory=lambda: os.getenv("OPTION_TRADER_LIVE_TICKS", "1") not in ("0", "false", "no"))
    tradier_token: str = field(default_factory=lambda: os.getenv("TRADIER_TOKEN", ""))
    tradier_sandbox: bool = field(default_factory=lambda: os.getenv("TRADIER_SANDBOX", "0") in ("1", "true", "yes"))

    @property
    def tradier_configured(self) -> bool:
        return bool(self.tradier_token)



_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
