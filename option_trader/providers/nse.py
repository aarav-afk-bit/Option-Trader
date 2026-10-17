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
    )
    return chain.validate()


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

    def _get_json(self, path: str, params: Optional[dict] = None) -> Any:
        with self._lock:
            if not self._warmed:
                self._warm_up()
            s = self._get_session()
            last_error: Optional[Exception] = None
            for attempt in range(2):
                try:
                    resp = s.get(f"{BASE_URL}{path}", params=params, timeout=self._timeout)
                except requests.RequestException as exc:
                    last_error = exc
                else:
                    if resp.status_code == 200:
                        try:
                            data = resp.json()
                        except ValueError as exc:
                            last_error = exc
                        else:
                            if data:
                                return data
                            last_error = InvalidDataError("empty response")
                    else:
                        last_error = DataUnavailableError(f"HTTP {resp.status_code}")
                if attempt == 0:
                    # Cookies expire quickly; refresh them and retry once.
                    time.sleep(0.5)
                    self._warm_up()
            raise DataUnavailableError(f"NSE request {path} failed: {last_error}")

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
        return self._lot_sizes.get(normalize_symbol(symbol))
