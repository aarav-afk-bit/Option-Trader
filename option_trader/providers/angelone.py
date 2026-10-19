"""Angel One SmartAPI provider.

Needs a SmartAPI app key plus your client code, PIN and TOTP secret (see ``.env.example``).

Flow:

1. Log in with client code + PIN + current TOTP to get a JWT (valid for the trading day).
2. Download Angel's public instrument master (cached on disk, refreshed daily) to find the
   NFO option contracts and their tokens for the requested underlying and expiry.
3. Pull live quotes for those tokens in FULL mode (LTP, depth, volume, OI), 50 per call.
4. Pull Angel's option Greeks (delta/gamma/theta/vega/IV) for the expiry and merge them in.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import requests

from ..config import Settings, get_settings
from ..errors import DataUnavailableError, ProviderNotConfiguredError
from ..models import OptionChain, OptionQuote, StrikeRow, utcnow
from .base import DataProvider, normalize_symbol

log = logging.getLogger(__name__)

ROOT = "https://apiconnect.angelone.in"
LOGIN_PATH = "/rest/auth/angelbroking/user/v1/loginByPassword"
QUOTE_PATH = "/rest/secure/angelbroking/market/v1/quote/"
GREEKS_PATH = "/rest/secure/angelbroking/marketData/v1/optionGreek"
SCRIP_MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
QUOTE_BATCH = 50

# NSE index tokens on SmartAPI (exchange "NSE").
INDEX_TOKENS = {
    "NIFTY": "99926000",
    "BANKNIFTY": "99926009",
    "FINNIFTY": "99926037",
    "MIDCPNIFTY": "99926074",
}


def parse_angel_date(value: str) -> date:
    return datetime.strptime(value.strip().upper(), "%d%b%Y").date()


def format_angel_date(value: date) -> str:
    return value.strftime("%d%b%Y").upper()


def _f(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class InstrumentMaster:
    """Option contracts and equity tokens from Angel's instrument master."""

    def __init__(self, records: list[dict]):
        # {underlying: {expiry: {(strike, "CE"/"PE"): record}}}
        self.options: dict[str, dict[date, dict[tuple[float, str], dict]]] = {}
        self.equity_tokens: dict[str, str] = {}
        self.lot_sizes: dict[str, int] = {}
        for r in records:
            seg = r.get("exch_seg")
            itype = r.get("instrumenttype")
            if seg == "NFO" and itype in ("OPTIDX", "OPTSTK"):
                sym = str(r.get("symbol", ""))
                opt = sym[-2:]
                if opt not in ("CE", "PE"):
                    continue
                try:
                    exp = parse_angel_date(r["expiry"])
                    strike = float(r["strike"]) / 100.0
                except (KeyError, ValueError):
                    continue
                name = str(r.get("name", "")).upper()
                self.options.setdefault(name, {}).setdefault(exp, {})[(strike, opt)] = r
                lot = _f(r.get("lotsize"))
                if lot:
                    self.lot_sizes[name] = int(lot)
            elif seg == "NSE" and str(r.get("symbol", "")).endswith("-EQ"):
                self.equity_tokens[str(r.get("name", "")).upper()] = str(r["token"])

    def expiries(self, name: str) -> list[date]:
        return sorted(self.options.get(name, {}))

    def contracts(self, name: str, expiry: date) -> dict[tuple[float, str], dict]:
        return self.options.get(name, {}).get(expiry, {})


class AngelOneProvider(DataProvider):
    name = "angelone"
    min_interval = 0.5

    def __init__(self, settings: Optional[Settings] = None, session: Optional[requests.Session] = None,
                 timeout: float = 15.0):
        self.settings = settings or get_settings()
        self._http = session or requests.Session()
        self._timeout = timeout
        self._jwt: Optional[str] = None
        self._feed_token: Optional[str] = None
        self._jwt_day: Optional[date] = None
        self._master: Optional[InstrumentMaster] = None
        self._master_day: Optional[date] = None
        self._lock = threading.Lock()

    def is_configured(self) -> bool:
        return self.settings.angel_configured

    # -- auth / http --------------------------------------------------------------------------
    def _headers(self) -> dict:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-UserType": "USER",
            "X-SourceID": "WEB",
            "X-ClientLocalIP": "127.0.0.1",
            "X-ClientPublicIP": "127.0.0.1",
            "X-MACAddress": "00:00:00:00:00:00",
            "X-PrivateKey": self.settings.angel_api_key,
        }
        if self._jwt:
            h["Authorization"] = f"Bearer {self._jwt}"
        return h

    def _login(self) -> None:
        if not self.is_configured():
            raise ProviderNotConfiguredError(
                "Angel One credentials missing: set ANGEL_API_KEY, ANGEL_CLIENT_CODE, ANGEL_PIN "
                "and ANGEL_TOTP_SECRET"
            )
        import pyotp

        self._jwt = None
        body = {
            "clientcode": self.settings.angel_client_code,
            "password": self.settings.angel_pin,
            "totp": pyotp.TOTP(self.settings.angel_totp_secret).now(),
        }
        data = self._raw_post(LOGIN_PATH, body)
        token = (data or {}).get("jwtToken")
        if not token:
            raise DataUnavailableError("Angel One login returned no session token")
        self._jwt = token
        self._feed_token = (data or {}).get("feedToken")
        self._jwt_day = date.today()

    def _raw_post(self, path: str, body: dict) -> Any:
        try:
            resp = self._http.post(f"{ROOT}{path}", json=body, headers=self._headers(),
                                   timeout=self._timeout)
        except requests.RequestException as exc:
            raise DataUnavailableError(f"Cannot reach Angel One: {exc}") from exc
        try:
            payload = resp.json()
        except ValueError as exc:
            raise DataUnavailableError(f"Angel One returned HTTP {resp.status_code} without JSON") from exc
        if resp.status_code in (401, 403) or not payload.get("status", False):
            raise _AngelAPIError(payload.get("message") or f"HTTP {resp.status_code}",
                                 payload.get("errorcode", ""))
        return payload.get("data")

    def _post(self, path: str, body: dict) -> Any:
        with self._lock:
            if self._jwt is None or self._jwt_day != date.today():
                self._login()
            try:
                return self._raw_post(path, body)
            except _AngelAPIError as exc:
                if exc.code.startswith("AG") or "token" in str(exc).lower():
                    self._login()
                    return self._raw_post(path, body)
                raise DataUnavailableError(f"Angel One error: {exc}") from exc

    # -- instrument master -------------------------------------------------------------------
    def _master_cache(self) -> Path:
        return self.settings.data_dir / "angel_scrip_master.json"

    def master(self) -> InstrumentMaster:
        today = date.today()
        if self._master is not None and self._master_day == today:
            return self._master
        cache = self._master_cache()
        records: Optional[list] = None
        if cache.exists() and date.fromtimestamp(cache.stat().st_mtime) == today:
            try:
                records = json.loads(cache.read_text())
            except (OSError, ValueError):
                records = None
        if records is None:
            try:
                resp = self._http.get(SCRIP_MASTER_URL, timeout=60)
                resp.raise_for_status()
                records = resp.json()
            except (requests.RequestException, ValueError) as exc:
                raise DataUnavailableError(f"Cannot download Angel One instrument list: {exc}") from exc
            try:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps(records))
            except OSError as exc:
                log.warning("Could not cache Angel instrument list: %s", exc)
        self._master = InstrumentMaster(records)
        self._master_day = today
        return self._master

    # -- market data ------------------------------------------------------------------------
    def _quotes(self, exchange: str, tokens: list[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for i in range(0, len(tokens), QUOTE_BATCH):
            batch = tokens[i : i + QUOTE_BATCH]
            data = self._post(QUOTE_PATH, {"mode": "FULL", "exchangeTokens": {exchange: batch}})
            for q in (data or {}).get("fetched", []):
                out[str(q.get("symbolToken"))] = q
            if i + QUOTE_BATCH < len(tokens):
                time.sleep(0.11)  # stay under SmartAPI's per-second quote limit
        return out

    def get_spot(self, symbol: str) -> float:
        name = normalize_symbol(symbol)
        token = INDEX_TOKENS.get(name) or self.master().equity_tokens.get(name)
        if not token:
            raise DataUnavailableError(f"Angel One has no NSE token for {name}")
        q = self._quotes("NSE", [token]).get(token)
        ltp = _f((q or {}).get("ltp"))
        if not ltp:
            raise DataUnavailableError(f"Angel One returned no price for {name}")
        return ltp

    def get_expiries(self, symbol: str) -> list[date]:
        name = normalize_symbol(symbol)
        expiries = [e for e in self.master().expiries(name) if e >= date.today()]
        if not expiries:
            raise DataUnavailableError(f"Angel One lists no option expiries for {name}")
        return expiries

    def _greeks(self, name: str, expiry: date) -> dict[tuple[float, str], dict]:
        try:
            data = self._post(GREEKS_PATH, {"name": name, "expirydate": format_angel_date(expiry)})
        except DataUnavailableError as exc:
            log.info("Angel option greeks unavailable for %s %s: %s", name, expiry, exc)
            return {}
        out = {}
        for g in data or []:
            k = _f(g.get("strikePrice"))
            t = g.get("optionType")
            if k is not None and t in ("CE", "PE"):
                out[(k, t)] = g
        return out

    def get_option_chain(self, symbol: str, expiry: Optional[date] = None) -> OptionChain:
        name = normalize_symbol(symbol)
        expiries = self.get_expiries(name)
        if expiry is None:
            expiry = expiries[0]
        elif expiry not in expiries:
            raise DataUnavailableError(f"{expiry} is not a listed Angel One expiry for {name}")

        contracts = self.master().contracts(name, expiry)
        spot = self.get_spot(name)
        # Limit to the strikes nearest the money to keep the request count reasonable.
        strikes = sorted({k for k, _ in contracts}, key=lambda k: abs(k - spot))[:60]
        wanted = {key: rec for key, rec in contracts.items() if key[0] in strikes}
        quotes = self._quotes("NFO", [str(r["token"]) for r in wanted.values()])
        greeks = self._greeks(name, expiry)

        rows: dict[float, StrikeRow] = {}
        feed_times = []
        for (strike, opt), rec in wanted.items():
            q = quotes.get(str(rec["token"]))
            if not q:
                continue
            if q.get("exchFeedTime"):
                try:
                    feed_times.append(datetime.strptime(q["exchFeedTime"], "%d-%b-%Y %H:%M:%S"))
                except ValueError:
                    pass
            depth = q.get("depth") or {}
            buy = (depth.get("buy") or [{}])[0]
            sell = (depth.get("sell") or [{}])[0]
            g = greeks.get((strike, opt), {})
            iv = _f(g.get("impliedVolatility"))
            quote = OptionQuote(
                strike=strike,
                option_type=opt,  # type: ignore[arg-type]
                expiry=expiry,
                ltp=_f(q.get("ltp")),
                bid=_f(buy.get("price")),
                ask=_f(sell.get("price")),
                bid_qty=int(_f(buy.get("quantity")) or 0) or None,
                ask_qty=int(_f(sell.get("quantity")) or 0) or None,
                volume=int(_f(q.get("tradeVolume")) or 0),
                oi=_f(q.get("opnInterest")),
                change=_f(q.get("netChange")),
                iv=iv / 100.0 if iv else None,
                delta=_f(g.get("delta")),
                gamma=_f(g.get("gamma")),
                theta=_f(g.get("theta")),
                vega=_f(g.get("vega")),
                symbol=q.get("tradingSymbol") or rec.get("symbol"),
            )
            row = rows.setdefault(strike, StrikeRow(strike))
            if opt == "CE":
                row.call = quote
            else:
                row.put = quote

        chain = OptionChain(
            symbol=name,
            expiry=expiry,
            spot=spot,
            rows=list(rows.values()),
            source="angelone",
            fetched_at=utcnow(),
            lot_size=self.master().lot_sizes.get(name),
            expiries=expiries,
            exchange_timestamp=max(feed_times).strftime("%d-%b-%Y %H:%M:%S") if feed_times else None,
        )
        if len(contracts) > len(wanted):
            chain.notes.append(f"Showing the {len(strikes)} strikes nearest the money")
        return chain.validate()


class _AngelAPIError(DataUnavailableError):
    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code or ""


def stream_credentials(provider: "AngelOneProvider") -> dict:
    """Log in if needed and return what the market-data WebSocket needs."""
    with provider._lock:
        if provider._jwt is None or provider._jwt_day != date.today() or not provider._feed_token:
            provider._login()
    if not provider._feed_token:
        raise DataUnavailableError("Angel One login returned no feed token for streaming")
    return {
        "jwt": provider._jwt,
        "api_key": provider.settings.angel_api_key,
        "client_code": provider.settings.angel_client_code,
        "feed_token": provider._feed_token,
    }
