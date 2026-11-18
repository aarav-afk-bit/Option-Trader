"""NSE India provider: option chains straight from www.nseindia.com.

NSE serves its option chain as JSON to its own website. The API requires the cookies the
site sets on a normal page visit, so the session first loads the option-chain page and then
calls the JSON endpoints with a browser-like User-Agent and Referer.

Two API generations are supported:

* ``/api/option-chain-contract-info`` + ``/api/option-chain-v3`` (current, per-expiry)
* ``/api/option-chain-indices`` / ``/api/option-chain-equities`` (legacy, all expiries)

Lot sizes come from NSE's published ``fo_mktlots.csv``.
"""

from __future__ import annotations

import csv
import io
import logging
import random
import threading
import time
from datetime import date, datetime
from typing import Any, Optional

import requests

from ..errors import DataUnavailableError, InvalidDataError
from ..models import OptionChain, OptionQuote, StrikeRow, utcnow
from .base import DataProvider, is_index, normalize_symbol

log = logging.getLogger(__name__)

BASE_URL = "https://www.nseindia.com"
LOT_SIZE_URL = "https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": f"{BASE_URL}/option-chain",
}
NSE_DATE_FMT = "%d-%b-%Y"


def parse_nse_date(value: str) -> date:
    return datetime.strptime(value.strip(), NSE_DATE_FMT).date()


def format_nse_date(value: date) -> str:
    return value.strftime(NSE_DATE_FMT)


def _num(d: dict, *keys: str) -> Optional[float]:
    for k in keys:
        v = d.get(k)
        if v in (None, "", "-"):
            continue
        try:
            return float(str(v).replace(",", ""))
        except ValueError:
            continue
    return None


def _int(d: dict, *keys: str) -> Optional[int]:
    v = _num(d, *keys)
    return int(v) if v is not None else None


def parse_quote(raw: dict, option_type: str, strike: float, expiry: date) -> OptionQuote:
    iv = _num(raw, "impliedVolatility")
    return OptionQuote(
        strike=strike,
        option_type=option_type,  # type: ignore[arg-type]
        expiry=expiry,
        ltp=_num(raw, "lastPrice"),
        bid=_num(raw, "bidprice", "buyPrice1", "bidPrice"),
        ask=_num(raw, "askPrice", "sellPrice1", "askprice"),
        bid_qty=_int(raw, "bidQty", "buyQuantity1"),
        ask_qty=_int(raw, "askQty", "sellQuantity1"),
        volume=_int(raw, "totalTradedVolume"),
        oi=_num(raw, "openInterest"),
        oi_change=_num(raw, "changeinOpenInterest"),
        change=_num(raw, "change"),
        iv=iv / 100.0 if iv else None,
        symbol=raw.get("identifier"),
    )


def parse_chain_payload(
    payload: dict, symbol: str, expiry: date, fetched_at: Optional[datetime] = None
) -> OptionChain:
    """Turn an NSE option-chain JSON payload into an ``OptionChain`` for one expiry."""
    records = payload.get("records") or {}
    data = records.get("data") or (payload.get("filtered") or {}).get("data") or []
    rows: dict[float, StrikeRow] = {}
    spot = _num(records, "underlyingValue")
    for item in data:
        row_exp_raw = item.get("expiryDate") or item.get("expiryDates")
        if row_exp_raw:
            try:
                if parse_nse_date(row_exp_raw) != expiry:
                    continue
            except ValueError:
                pass
        strike = _num(item, "strikePrice")
        if strike is None:
            continue
        row = rows.setdefault(strike, StrikeRow(strike))
        if item.get("CE"):
            row.call = parse_quote(item["CE"], "CE", strike, expiry)
            spot = spot or _num(item["CE"], "underlyingValue")
        if item.get("PE"):
            row.put = parse_quote(item["PE"], "PE", strike, expiry)
            spot = spot or _num(item["PE"], "underlyingValue")

    expiries = []
    for e in records.get("expiryDates") or []:
        try:
            expiries.append(parse_nse_date(e))
        except ValueError:
            continue

    chain = OptionChain(
        symbol=symbol,
        expiry=expiry,
        spot=spot or 0.0,
        rows=list(rows.values()),
        source="nse",
        fetched_at=fetched_at or utcnow(),
        exchange_timestamp=records.get("timestamp"),
        expiries=sorted(expiries),
        currency="INR",
    )
    return chain.validate()


def parse_futures_quote(payload: dict) -> dict[date, float]:
    """{expiry: last traded price} for the futures in an NSE quote-derivative payload."""
    out: dict[date, float] = {}
    for item in payload.get("stocks") or []:
        md = item.get("metadata") or {}
        if "future" not in str(md.get("instrumentType", "")).lower():
            continue
        try:
            exp = parse_nse_date(md["expiryDate"])
            px = float(md.get("lastPrice") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if px > 0:
            out[exp] = px
    return out


def parse_lot_sizes(csv_text: str) -> dict[str, int]:
    """Parse NSE's fo_mktlots.csv into {SYMBOL: lot size for the nearest listed month}."""
    lots: dict[str, int] = {}
    reader = csv.reader(io.StringIO(csv_text))
    header_seen = False
    for row in reader:
        cells = [c.strip() for c in row]
        if len(cells) < 3:
            continue
        if not header_seen:
            if cells[1].upper() == "SYMBOL":
                header_seen = True
            continue
        sym = cells[1].upper()
        for c in cells[2:]:
            if c.isdigit():
                lots[sym] = int(c)
                break
    return lots


class NSEProvider(DataProvider):
    name = "nse"
    min_interval = 1.0  # NSE blocks clients that hammer its API

    def __init__(self, session: Optional[requests.Session] = None, timeout: float = 10.0):
        self._session = session
        self._warmed = False
        self._timeout = timeout
        self._lock = threading.Lock()
        self._lot_sizes: dict[str, int] = {}
        self._lot_sizes_at = 0.0
        self._sleep = time.sleep
        self.last_ok: Optional[float] = None
        self._holidays: Optional[list[date]] = None
        self._holidays_day: Optional[date] = None

    # -- HTTP -------------------------------------------------------------------------------
    def _get_session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update(HEADERS)
        return self._session

    def _warm_up(self) -> None:
        s = self._get_session()
        try:
            s.get(f"{BASE_URL}/option-chain", timeout=self._timeout)
            self._warmed = True
        except requests.RequestException as exc:
            raise DataUnavailableError(f"Cannot reach nseindia.com: {exc}") from exc

    def _get_json(self, path: str, params: Optional[dict] = None, attempts: int = 4) -> Any:
        """GET an NSE JSON endpoint with the retries NSE's bot protection requires.

        * 401/403 or an HTML page instead of JSON (Akamai block / expired cookies): refresh the
          session cookies and retry.
        * 429 and 5xx: back off exponentially with jitter (honouring Retry-After).
        * Network errors: back off and retry.
        * 404 and other client errors: fail immediately (the endpoint does not exist).
        """
        with self._lock:
            if not self._warmed:
                self._warm_up()
            s = self._get_session()
            last_error: Optional[Exception] = None
            for attempt in range(attempts):
                rewarm, retry_after = False, None
                try:
                    resp = s.get(f"{BASE_URL}{path}", params=params, timeout=self._timeout)
                except requests.RequestException as exc:
                    last_error = exc
                else:
                    ctype = (getattr(resp, "headers", {}) or {}).get("Content-Type", "")
                    if resp.status_code == 200:
                        try:
                            data = resp.json()
                        except ValueError:
                            last_error = InvalidDataError(
                                f"NSE returned a non-JSON page ({ctype or 'unknown type'}): likely blocked or cookies expired")
                            rewarm = True
                        else:
                            if data:
                                self.last_ok = time.time()
                                return data
                            last_error = InvalidDataError("empty response")
                            rewarm = True
                    elif resp.status_code in (401, 403):
                        last_error = DataUnavailableError(f"HTTP {resp.status_code} (access refused)")
                        rewarm = True
                    elif resp.status_code == 429 or resp.status_code >= 500:
                        last_error = DataUnavailableError(f"HTTP {resp.status_code}")
                        try:
                            retry_after = float((getattr(resp, "headers", {}) or {}).get("Retry-After", ""))
                        except ValueError:
                            retry_after = None
                    else:
                        raise DataUnavailableError(f"NSE request {path} failed: HTTP {resp.status_code}")
                if attempt == attempts - 1:
                    break
                delay = retry_after if retry_after is not None else min(8.0, 0.5 * 2 ** attempt)
                self._sleep(delay + random.uniform(0, 0.3))
                if rewarm:
                    self._warm_up()
            raise DataUnavailableError(f"NSE request {path} failed after {attempts} attempts: {last_error}")

    # -- futures -----------------------------------------------------------------------------
    def get_futures(self, symbol: str) -> dict[date, float]:
        """Exchange futures prices by expiry, from NSE's derivative quote."""
        symbol = normalize_symbol(symbol)
        return parse_futures_quote(self._get_json("/api/quote-derivative", {"symbol": symbol}, attempts=2))

    # -- API --------------------------------------------------------------------------------
    def _legacy_payload(self, symbol: str) -> dict:
        path = "/api/option-chain-indices" if is_index(symbol) else "/api/option-chain-equities"
        return self._get_json(path, {"symbol": symbol})

    def get_expiries(self, symbol: str) -> list[date]:
        symbol = normalize_symbol(symbol)
        raw: list[str] = []
        try:
            info = self._get_json("/api/option-chain-contract-info", {"symbol": symbol})
            raw = info.get("expiryDates") or []
        except DataUnavailableError as exc:
            log.info("NSE contract-info failed for %s (%s); trying legacy endpoint", symbol, exc)
        if not raw:
            raw = (self._legacy_payload(symbol).get("records") or {}).get("expiryDates") or []
        expiries = sorted({parse_nse_date(e) for e in raw})
        if not expiries:
            raise DataUnavailableError(f"NSE lists no option expiries for {symbol}")
        return expiries

    def get_option_chain(self, symbol: str, expiry: Optional[date] = None) -> OptionChain:
        symbol = normalize_symbol(symbol)
        expiries = self.get_expiries(symbol)
        today = date.today()
        if expiry is None:
            expiry = next((e for e in expiries if e >= today), expiries[0])
        elif expiry not in expiries:
            raise DataUnavailableError(
                f"{expiry.isoformat()} is not a listed NSE expiry for {symbol}"
            )

        chain: Optional[OptionChain] = None
        try:
            payload = self._get_json(
                "/api/option-chain-v3",
                {
                    "type": "Indices" if is_index(symbol) else "Equity",
                    "symbol": symbol,
                    "expiry": format_nse_date(expiry),
                },
            )
            chain = parse_chain_payload(payload, symbol, expiry)
        except DataUnavailableError as exc:
            log.info("NSE v3 chain failed for %s (%s); trying legacy endpoint", symbol, exc)
        if chain is None:
            chain = parse_chain_payload(self._legacy_payload(symbol), symbol, expiry)

        chain.expiries = expiries
        chain.lot_size = self.get_lot_size(symbol)
        if chain.lot_size is None:
            chain.notes.append("Lot size unavailable from NSE; quantities shown per unit")
        return chain

    def get_lot_size(self, symbol: str) -> Optional[int]:
        if not self._lot_sizes or time.time() - self._lot_sizes_at > 6 * 3600:
            try:
                resp = self._get_session().get(LOT_SIZE_URL, timeout=self._timeout)
                resp.raise_for_status()
                self._lot_sizes = parse_lot_sizes(resp.text)
                self._lot_sizes_at = time.time()
            except requests.RequestException as exc:
                log.warning("Could not fetch NSE lot sizes: %s", exc)
                self._lot_sizes_at = time.time() - 6 * 3600 + 300  # retry in 5 minutes, not every call
        return self._lot_sizes.get(normalize_symbol(symbol))

    # -- market session ---------------------------------------------------------------------
    def get_market_status(self) -> dict:
        from ..market_hours import parse_nse_market_status

        status = parse_nse_market_status(self._get_json("/api/marketStatus"))
        if status is None:
            raise InvalidDataError("NSE marketStatus had no capital-market entry")
        return status

    def get_holidays(self) -> list[date]:
        from ..market_hours import parse_nse_holidays

        if self._holidays is None or self._holidays_day != date.today():
            self._holidays = parse_nse_holidays(self._get_json("/api/holiday-master", {"type": "trading"}))
            self._holidays_day = date.today()
        return self._holidays
