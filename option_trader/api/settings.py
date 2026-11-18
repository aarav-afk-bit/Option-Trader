"""In-app settings and connection doctor."""

from __future__ import annotations

import ipaddress
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import config
from ..charges import FEE_ENV, FEE_KEYS
from ..errors import DataUnavailableError

router = APIRouter(prefix="/api/settings")


def _mask(v: str) -> str:
    return ("•" * max(0, len(v) - 4) + v[-4:]) if v else ""


def _local_only(request: Request) -> None:
    """Settings can hold broker credentials: only accept changes from this computer."""
    if os.getenv("OPTION_TRADER_ALLOW_REMOTE_SETTINGS") == "1":
        return
    host = request.client.host if request.client else ""
    try:
        if ipaddress.ip_address(host).is_loopback:
            return
    except ValueError:
        if host in ("testclient", "localhost"):
            return
    raise HTTPException(403, "Settings can only be changed from the computer running the app "
                             "(set OPTION_TRADER_ALLOW_REMOTE_SETTINGS=1 to allow, e.g. in Docker)")


@router.get("")
def get_settings_view() -> dict:
    s = config.get_settings()
    return {
        "providers": s.providers,
        "risk_free_rate": s.risk_free_rate,
        "cache_ttl": s.cache_ttl,
        "stale_seconds": s.stale_seconds,
        "live_ticks": s.live_ticks,
        "charges": {k: os.getenv(k, "") for k in FEE_KEYS},
        "fee_defaults": {env: default for env, default in FEE_ENV.values()},
        "tradier": {"configured": s.tradier_configured, "token": _mask(s.tradier_token), "sandbox": s.tradier_sandbox},
        "data_dir": str(s.data_dir),
    }


class TradierIn(BaseModel):
    token: Optional[str] = None
    sandbox: Optional[bool] = None
    clear: bool = False


class SettingsIn(BaseModel):
    providers: Optional[list[str]] = None
    risk_free_rate: Optional[float] = None
    cache_ttl: Optional[float] = None
    stale_seconds: Optional[float] = None
    live_ticks: Optional[bool] = None
    charges: Optional[dict[str, str]] = None
    tradier: Optional[TradierIn] = None


def _rebuild_manager(request: Request) -> None:
    from ..providers.manager import ProviderManager

    old = request.app.state.manager
    live = getattr(old, "live", None)
    if live is not None:
        try:
            live.stream.stop()
        except Exception:
            pass
    request.app.state.manager = ProviderManager.from_settings(config.get_settings())


@router.post("")
def save(body: SettingsIn, request: Request) -> dict:
    _local_only(request)
    values, secrets = {}, {}
    if body.providers is not None:
        bad = [p for p in body.providers if p not in config.VALID_PROVIDERS]
        if bad or not body.providers:
            raise HTTPException(422, f"Choose sources from {config.VALID_PROVIDERS}")
        values["OPTION_TRADER_PROVIDERS"] = ",".join(body.providers)
    for field, key, lo, hi in (("risk_free_rate", "OPTION_TRADER_RISK_FREE_RATE", 0, 0.3),
                               ("cache_ttl", "OPTION_TRADER_CACHE_TTL", 1, 600),
                               ("stale_seconds", "OPTION_TRADER_STALE_SECONDS", 10, 3600)):
        v = getattr(body, field)
        if v is not None:
            if not lo <= v <= hi:
                raise HTTPException(422, f"{field} must be between {lo} and {hi}")
            values[key] = v
    if body.live_ticks is not None:
        values["OPTION_TRADER_LIVE_TICKS"] = "1" if body.live_ticks else "0"
    for k, v in (body.charges or {}).items():
        if k not in FEE_KEYS:
            raise HTTPException(422, f"Unknown fee setting {k}")
        if v != "":
            try:
                if float(v) < 0:
                    raise ValueError
            except ValueError as exc:
                raise HTTPException(422, f"{k} must be a non-negative number") from exc
        values[k] = v
    if body.tradier:
        if body.tradier.clear:
            secrets = {k: "" for k in config.SECRET_KEYS}
        elif body.tradier.token:  # blank keeps the saved token
            secrets["TRADIER_TOKEN"] = body.tradier.token.strip()
        if body.tradier.sandbox is not None:
            values["TRADIER_SANDBOX"] = "1" if body.tradier.sandbox else "0"
    try:
        config.save_settings(values, secrets)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    _rebuild_manager(request)
    return get_settings_view()


# (applies to source or "*", text to look for, advice) — first match wins, most specific first.
ADVICE = [
    ("*", "proxy", "A proxy or firewall on this network is blocking the connection. Try a different network (home/mobile internet) or ask your network admin to allow the site."),
    ("*", "tunnel", "A proxy or firewall on this network is blocking the connection. Try a different network (home/mobile internet) or ask your network admin to allow the site."),
    ("*", "No internet", "This computer appears to be offline."),
    ("Tradier", "token missing", "Optional: paste a Tradier API token above for real-time prices (free with a Tradier brokerage account; a developer sandbox token gives delayed data)."),
    ("Tradier", "rejected the token", "Tradier rejected the token. Check it was copied fully, and tick Sandbox only for a sandbox token."),
    ("Cboe", "check the symbol", "Cboe has no options for that symbol. Check the ticker."),
    ("Cboe", "non-JSON", "Cboe returned a web page instead of data. Wait a minute and retry."),
    ("*", "Cannot reach", "The site could not be reached. Check your internet connection, firewall or proxy."),
    ("*", "timed out", "The site is slow or unreachable right now. Retry in a minute."),
]


def _advice(name: str, err: str) -> str:
    low = err.lower()
    for who, needle, tip in ADVICE:
        if who in ("*", name) and needle.lower() in low:
            return tip
    return "See the error above; if it persists, try again in a minute."


def _timed(name: str, fn) -> dict:
    t0 = time.time()
    try:
        detail = fn()
        return {"name": name, "ok": True, "ms": round((time.time() - t0) * 1000), "detail": detail}
    except (DataUnavailableError, Exception) as exc:  # report every failure in plain words
        err = str(exc) or exc.__class__.__name__
        return {"name": name, "ok": False, "ms": round((time.time() - t0) * 1000), "error": err[:300], "advice": _advice(name, err)}


@router.post("/test")
def test_connections(request: Request) -> dict:
    from ..providers.cboe import CboeProvider
    from ..providers.manager import check_online
    from ..providers.tradier import TradierProvider
    from ..providers.yahoo import YahooProvider

    s = config.get_settings()

    def internet():
        if not check_online():
            raise DataUnavailableError("No internet connection")
        return "online"

    def cboe():
        exps = CboeProvider().get_expiries("SPY")
        return f"SPY expiries: {len(exps)} (next {exps[0]}), ~15 min delayed"

    def tradier():
        p = TradierProvider.from_settings(s)
        if not p.is_configured():
            raise DataUnavailableError("Tradier token missing")
        q = p.get_quote("SPY")
        return f"SPY {q['last']:,.2f} ({'sandbox, delayed' if p.sandbox else 'real-time'})"

    def yahoo():
        c = YahooProvider().get_history("SPY", "5d", "1d")
        return f"{len(c)} daily candles, last close {c[-1].close:,.2f}"

    checks = [("Internet", internet), ("Cboe", cboe), ("Tradier", tradier), ("Yahoo Finance", yahoo)]
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda c: _timed(*c), checks))
    return {"results": results}
