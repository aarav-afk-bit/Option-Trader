"""Cboe delayed quotes: free US option chains with Greeks (about 15 minutes delayed).

Cboe publishes, for every optionable US symbol, a JSON document with the underlying quote and
every listed option: bid/ask with sizes, last trade, volume, open interest, IV and Greeks.

* ``/delayed_quotes/options/{SYM}.json``  full chain (all expiries); indices use ``_SPX`` etc.
* ``/delayed_quotes/quotes/{SYM}.json``   underlying quote
* ``/delayed_quotes/charts/historical/{SYM}.json``  daily history

Contracts are identified by OCC symbols: ROOT + YYMMDD + C/P + strike x 1000 (8 digits).
Index products list AM-settled monthlies and PM-settled weeklies under different roots
(SPX vs SPXW, NDX vs NDXP, RUT vs RUTW, VIX vs VIXW); the PM root is preferred when both
exist for a date, and AM-settled-only expiries stop trading at the 9:30 open. Adjusted
contracts (roots like ``AAPL1`` after corporate actions) have non-standard deliverables and
are skipped.
"""

from __future__ import annotations

import logging
import random
import re
import threading
import time
from datetime import date, datetime, timezone
from typing import Any, Optional

import requests

from ..errors import DataUnavailableError, InvalidDataError
from ..models import (CONTRACT_MULTIPLIER, ET, US_AM_SETTLE_TIME, Candle, OptionChain,
                      OptionQuote, StrikeRow, utcnow)
from .base import DataProvider, US_INDICES, normalize_symbol

log = logging.getLogger(__name__)

BASE = "https://cdn.cboe.com/api/global/delayed_quotes"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.cboe.com/",
}
OCC_RE = re.compile(r"^(?P<root>[A-Z0-9.]+?)(?P<date>\d{6})(?P<cp>[CP])(?P<strike>\d{8})$")
# For each index: (PM-settled root, AM-settled root)
INDEX_ROOTS = {"SPX": ("SPXW", "SPX"), "NDX": ("NDXP", "NDX"), "RUT": ("RUTW", "RUT"),
               "VIX": ("VIXW", "VIX"), "XSP": ("XSP", "XSP"), "DJX": ("DJX", "DJX"), "OEX": ("OEX", "OEX"),
               "XEO": ("XEO", "XEO")}


def cboe_symbol(symbol: str) -> str:
    s = normalize_symbol(symbol)
    return f"_{s}" if s in US_INDICES else s


def parse_occ(code: str) -> Optional[tuple[str, date, str, float]]:
    m = OCC_RE.match(code.strip().upper())
    if not m:
        return None
    try:
        exp = datetime.strptime(m["date"], "%y%m%d").date()
    except ValueError:
        return None
    return m["root"], exp, "CE" if m["cp"] == "C" else "PE", int(m["strike"]) / 1000.0


def _f(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


def _i(v: Any) -> Optional[int]:
    f = _f(v)
    return int(f) if f is not None else None


def _allowed_roots(symbol: str) -> tuple[str, ...]:
    return INDEX_ROOTS.get(symbol, (symbol,))


def parse_payload(payload: dict, symbol: str) -> dict:
    """Group a Cboe chain document by expiry: {expiry: {"root": str, "am": bool, "quotes": [...]}}."""
    data = payload.get("data") or {}
    pm_root, am_root = INDEX_ROOTS.get(symbol, (symbol, symbol))
    by_exp: dict[date, dict[str, list]] = {}
    skipped = 0
    for o in data.get("options") or []:
        p = parse_occ(str(o.get("option", "")))
        if not p:
            continue
        root, exp, typ, strike = p
        if root not in _allowed_roots(symbol):
            skipped += 1  # adjusted / non-standard deliverable
            continue
        by_exp.setdefault(exp, {}).setdefault(root, []).append((typ, strike, o))
    out = {}
    for exp, roots in by_exp.items():
        root = pm_root if pm_root in roots else next(iter(roots))
        out[exp] = {"root": root, "am": root == am_root and pm_root != am_root, "quotes": roots[root],
                    "other_roots": sorted(r for r in roots if r != root)}
    return {"expiries": out, "skipped": skipped}


def build_chain(payload: dict, symbol: str, expiry: date, grouped: Optional[dict] = None) -> OptionChain:
    data = payload.get("data") or {}
    grouped = grouped or parse_payload(payload, symbol)
    entry = grouped["expiries"].get(expiry)
    if not entry:
        raise DataUnavailableError(f"Cboe lists no {symbol} options expiring {expiry}")
    rows: dict[float, StrikeRow] = {}
    for typ, strike, o in entry["quotes"]:
        iv = _f(o.get("iv"))
        last = _f(o.get("last_trade_price"))
        prev = _f(o.get("prev_day_close"))
        q = OptionQuote(
            strike=strike, option_type=typ, expiry=expiry,  # type: ignore[arg-type]
            ltp=last if last and last > 0 else None,
            bid=_f(o.get("bid")) or None, ask=_f(o.get("ask")) or None,
            bid_qty=_i(o.get("bid_size")), ask_qty=_i(o.get("ask_size")),
            volume=_i(o.get("volume")), oi=_f(o.get("open_interest")),
            change=_f(o.get("change")),
            iv=iv if iv and 0.005 < iv < 5 else None,
            delta=_f(o.get("delta")), gamma=_f(o.get("gamma")), theta=_f(o.get("theta")), vega=_f(o.get("vega")),
            symbol=str(o.get("option")),
        )
        if prev and q.ltp is None and not (q.bid or q.ask):
            q.ltp = None  # no trade today and no book: leave unpriced rather than show yesterday as live
        row = rows.setdefault(strike, StrikeRow(strike))
        if typ == "CE":
            row.call = q
        else:
            row.put = q
    spot = _f(data.get("current_price")) or _f(data.get("close")) or _f(data.get("prev_day_close")) or 0.0
    chain = OptionChain(
        symbol=symbol, expiry=expiry, spot=spot, rows=list(rows.values()), source="cboe", fetched_at=utcnow(),
        exchange_timestamp=payload.get("timestamp"), lot_size=CONTRACT_MULTIPLIER,
        expiries=sorted(grouped["expiries"]), currency="USD",
        expiry_at=datetime.combine(expiry, US_AM_SETTLE_TIME, tzinfo=ET) if entry["am"] else None,
    )
    chain.notes.append("Cboe delayed quotes (about 15 minutes behind the market)")
    if entry["am"]:
        chain.notes.append(f"{entry['root']} options are AM-settled: they stop trading the day before and settle at the open")
    if entry["other_roots"]:
        chain.notes.append(f"Showing {entry['root']}; also listed for this date: {', '.join(entry['other_roots'])}")
    if grouped["skipped"]:
        chain.notes.append(f"Skipped {grouped['skipped']} adjusted contracts with non-standard deliverables")
    if data.get("iv30") is not None:
        chain.iv30 = _f(data.get("iv30"))
    return chain.validate()


class CboeProvider(DataProvider):
    name = "cboe"
    min_interval = 0.5

    def __init__(self, session: Optional[requests.Session] = None, timeout: float = 15.0, cache_seconds: float = 15.0):
        self._session = session
        self._timeout = timeout
        self._cache: dict[str, tuple[float, Any]] = {}
        self._cache_seconds = cache_seconds
        self._lock = threading.Lock()
        self._sleep = time.sleep

    def _http(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update(HEADERS)
        return self._session

    def _get(self, path: str, attempts: int = 3) -> Any:
        with self._lock:
            hit = self._cache.get(path)
            if hit and time.time() - hit[0] < self._cache_seconds:
                return hit[1]
        last: Optional[Exception] = None
        for attempt in range(attempts):
            try:
                resp = self._http().get(f"{BASE}{path}", timeout=self._timeout)
            except requests.RequestException as exc:
                last = DataUnavailableError(f"Cannot reach Cboe: {exc}")
            else:
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                    except ValueError:
                        last = InvalidDataError("Cboe returned a non-JSON page")
                    else:
                        with self._lock:
                            self._cache[path] = (time.time(), data)
                        return data
                elif resp.status_code in (403, 404):
                    raise DataUnavailableError(f"Cboe has no data at {path} (HTTP {resp.status_code}); check the symbol")
                else:
                    last = DataUnavailableError(f"Cboe HTTP {resp.status_code}")
            if attempt < attempts - 1:
                self._sleep(min(4.0, 0.5 * 2 ** attempt) + random.uniform(0, 0.2))
        raise DataUnavailableError(f"Cboe request {path} failed: {last}")

    def _chain_doc(self, symbol: str) -> tuple[dict, dict]:
        sym = normalize_symbol(symbol)
        payload = self._get(f"/options/{cboe_symbol(sym)}.json")
        return payload, parse_payload(payload, sym)

    def get_expiries(self, symbol: str) -> list[date]:
        _, grouped = self._chain_doc(symbol)
        today = datetime.now(ET).date()
        exps = sorted(e for e in grouped["expiries"] if e >= today)
        if not exps:
            raise DataUnavailableError(f"Cboe lists no option expiries for {normalize_symbol(symbol)}")
        return exps

    def get_option_chain(self, symbol: str, expiry: Optional[date] = None) -> OptionChain:
        sym = normalize_symbol(symbol)
        payload, grouped = self._chain_doc(sym)
        exps = self.get_expiries(sym)
        if expiry is None:
            expiry = exps[0]
        elif expiry not in grouped["expiries"]:
            raise DataUnavailableError(f"{expiry} is not a listed {sym} expiry on Cboe")
        return build_chain(payload, sym, expiry, grouped)

    def get_quote(self, symbol: str) -> dict:
        d = (self._get(f"/quotes/{cboe_symbol(symbol)}.json") or {}).get("data") or {}
        last = _f(d.get("current_price")) or _f(d.get("close"))
        if not last:
            raise DataUnavailableError(f"Cboe returned no price for {symbol}")
        return {"last": last, "prev_close": _f(d.get("prev_day_close")), "high": _f(d.get("high")),
                "low": _f(d.get("low")), "change": _f(d.get("price_change")),
                "pct": _f(d.get("price_change_percent")), "iv30": _f(d.get("iv30"))}

    def get_spot(self, symbol: str) -> float:
        return self.get_quote(symbol)["last"]

    def get_history(self, symbol: str, period: str = "1y", interval: str = "1d") -> list[Candle]:
        if interval != "1d":
            raise NotImplementedError("Cboe history is daily only")
        rows = (self._get(f"/charts/historical/{cboe_symbol(symbol)}.json") or {}).get("data") or []
        out = []
        for r in rows:
            try:
                ts = datetime.strptime(r["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                o, h, l, c = (float(r[k]) for k in ("open", "high", "low", "close"))
            except (KeyError, TypeError, ValueError):
                continue
            out.append(Candle(ts, o, h, l, c, _f(r.get("volume")) or 0.0))
        if not out:
            raise DataUnavailableError(f"Cboe returned no history for {symbol}")
        days = {"1mo": 31, "3mo": 92, "6mo": 183, "1y": 366, "2y": 731, "5y": 1827}.get(period, 366)
        cutoff = out[-1].ts.timestamp() - days * 86400
        return [c for c in out if c.ts.timestamp() >= cutoff]
