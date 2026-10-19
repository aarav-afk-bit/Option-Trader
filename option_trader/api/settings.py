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
        "charges": {k: os.getenv(k, "") for k in sorted(config.EDITABLE_KEYS) if "BROKERAGE" in k or k.endswith(("STT_SELL", "EXCHANGE_TXN", "SEBI_FEE", "STAMP_BUY", "GST"))},
        "angel": {
            "configured": s.angel_configured,
            "api_key": _mask(s.angel_api_key), "client_code": _mask(s.angel_client_code),
            "pin": "••••" if s.angel_pin else "", "totp_secret": "••••" if s.angel_totp_secret else "",
        },
        "data_dir": str(s.data_dir),
    }


class AngelIn(BaseModel):
    api_key: Optional[str] = None
    client_code: Optional[str] = None
    pin: Optional[str] = None
    totp_secret: Optional[str] = None
    clear: bool = False


class SettingsIn(BaseModel):
    providers: Optional[list[str]] = None
    risk_free_rate: Optional[float] = None
    cache_ttl: Optional[float] = None
    stale_seconds: Optional[float] = None
    live_ticks: Optional[bool] = None
    charges: Optional[dict[str, str]] = None
    angel: Optional[AngelIn] = None


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
        if k not in config.EDITABLE_KEYS:
            raise HTTPException(422, f"Unknown charge setting {k}")
        if v != "":
            try:
                if float(v) < 0:
                    raise ValueError
            except ValueError as exc:
                raise HTTPException(422, f"{k} must be a non-negative number") from exc
        values[k] = v
    if body.angel:
        if body.angel.clear:
            secrets = {k: "" for k in config.SECRET_KEYS}
        else:
            mapping = {"api_key": "ANGEL_API_KEY", "client_code": "ANGEL_CLIENT_CODE",
                       "pin": "ANGEL_PIN", "totp_secret": "ANGEL_TOTP_SECRET"}
            for f, key in mapping.items():
                v = getattr(body.angel, f)
                if v:  # blank fields keep the saved value
                    secrets[key] = v.strip().replace(" ", "") if f == "totp_secret" else v.strip()
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
    ("Angel One", "credentials missing", "Enter your Angel One API key, client code, PIN and TOTP secret above."),
    ("Angel One", "totp", "Angel One rejected the one-time code. Check the TOTP secret (the text code shown when you enabled TOTP) and that your computer's clock is correct."),
    ("Angel One", "invalid", "Angel One rejected the login. Check the client code, PIN and API key."),
    ("NSE", "403", "NSE refused the request. NSE blocks many VPNs, cloud servers and some networks: try your normal home or mobile internet, or add Angel One as a source."),
    ("NSE", "non-JSON", "NSE returned a web page instead of data (bot protection). Wait a minute and retry, or use Angel One."),
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
    from ..providers.angelone import AngelOneProvider
    from ..providers.manager import check_online
    from ..providers.nse import NSEProvider
    from ..providers.yahoo import YahooProvider

    s = config.get_settings()

    def internet():
        if not check_online():
            raise DataUnavailableError("No internet connection")
        return "online"

    def nse():
        exps = NSEProvider().get_expiries("NIFTY")
        return f"NIFTY expiries: {len(exps)} (next {exps[0]})"

    def angel():
        p = AngelOneProvider(s)
        if not p.is_configured():
            raise DataUnavailableError("Angel One credentials missing")
        return f"logged in; NIFTY {p.get_spot('NIFTY'):,.2f}"

    def yahoo():
        c = YahooProvider().get_history("NIFTY", "5d", "1d")
        return f"{len(c)} daily candles, last close {c[-1].close:,.2f}"

    checks = [("Internet", internet), ("NSE", nse), ("Angel One", angel), ("Yahoo Finance", yahoo)]
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda c: _timed(*c), checks))
    return {"results": results}
