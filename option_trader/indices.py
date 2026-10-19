"""Live index levels and day moves (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, India VIX).

Primary source is NSE's ``/api/allIndices``; Yahoo Finance is the fallback. When the Angel
One tick stream is running, the index levels it streams replace the snapshot values.
"""

from __future__ import annotations

import logging
from typing import Optional

from .errors import DataUnavailableError
from .models import utcnow

log = logging.getLogger(__name__)

# (display name, NSE allIndices name, Yahoo ticker, option symbol or None, Angel NSE token)
INDEX_SPECS = [
    ("NIFTY 50", "NIFTY 50", "^NSEI", "NIFTY", "99926000"),
    ("NIFTY BANK", "NIFTY BANK", "^NSEBANK", "BANKNIFTY", "99926009"),
    ("FIN NIFTY", "NIFTY FINANCIAL SERVICES", "NIFTY_FIN_SERVICE.NS", "FINNIFTY", "99926037"),
    ("MIDCAP SELECT", "NIFTY MIDCAP SELECT", "NIFTY_MID_SELECT.NS", "MIDCPNIFTY", "99926074"),
    ("INDIA VIX", "INDIA VIX", "^INDIAVIX", None, None),
]


def _row(name, symbol, last, prev, high=None, low=None, source="", ts=None, token=None) -> dict:
    change = (last - prev) if (last is not None and prev) else None
    return {
        "name": name, "symbol": symbol, "last": last, "prev_close": prev,
        "change": round(change, 2) if change is not None else None,
        "pct": round(change / prev * 100, 2) if change is not None else None,
        "high": high, "low": low, "source": source, "timestamp": ts, "token": token,
    }


def parse_all_indices(payload: dict) -> list[dict]:
    by_name = {str(d.get("index", "")).upper(): d for d in payload.get("data") or []}
    out = []
    for name, nse_name, _y, sym, token in INDEX_SPECS:
        d = by_name.get(nse_name)
        if not d:
            continue
        try:
            last = float(d["last"])
            prev = float(d.get("previousClose") or 0) or None
        except (KeyError, TypeError, ValueError):
            continue
        out.append(_row(name, sym, last, prev, d.get("high"), d.get("low"), "nse", payload.get("timestamp"), token))
    return out


def fetch_from_nse(nse) -> list[dict]:
    rows = parse_all_indices(nse._get_json("/api/allIndices"))
    if not rows:
        raise DataUnavailableError("NSE allIndices returned none of the tracked indices")
    return rows


def fetch_from_yahoo(ticker_factory=None) -> list[dict]:
    if ticker_factory is None:
        import yfinance as yf

        ticker_factory = yf.Ticker
    out = []
    for name, _n, ysym, sym, token in INDEX_SPECS:
        try:
            fi = ticker_factory(ysym).fast_info
            last, prev = float(fi["last_price"]), float(fi["previous_close"])
            high, low = fi.get("day_high"), fi.get("day_low")
        except Exception as exc:
            log.info("Yahoo index %s unavailable: %s", ysym, exc)
            continue
        out.append(_row(name, sym, last, prev, high, low, "yahoo", utcnow().isoformat(), token))
    if not out:
        raise DataUnavailableError("Yahoo Finance returned no index levels")
    return out


def overlay_ticks(rows: list[dict], stream) -> None:
    """Replace levels with streamed ticks where available (keeps prev close for the change)."""
    if stream is None:
        return
    for r in rows:
        t = stream.tick(r["token"]) if r.get("token") else None
        if t and t.get("ltp"):
            prev = r["prev_close"] or t.get("close")
            fresh = _row(r["name"], r["symbol"], t["ltp"], prev, t.get("high") or r["high"],
                         t.get("low") or r["low"], r["source"] + "+ticks", r["timestamp"], r["token"])
            r.update(fresh)


def get_indices(manager) -> dict:
    cached = manager._cached(("indices",))
    if cached is not None:
        rows = [dict(r) for r in cached]
    else:
        if not manager.is_online():
            raise DataUnavailableError("No internet connection: index levels unavailable")
        nse = next((p for p in manager.providers if p.name == "nse"), None)
        errors = []
        rows: Optional[list[dict]] = None
        if nse is not None and hasattr(nse, "_get_json"):
            try:
                rows = fetch_from_nse(nse)
            except DataUnavailableError as exc:
                errors.append(f"nse: {exc}")
        if rows is None:
            try:
                rows = fetch_from_yahoo()
            except DataUnavailableError as exc:
                errors.append(f"yahoo: {exc}")
        if rows is None:
            raise DataUnavailableError("Index levels unavailable. " + " | ".join(errors))
        manager._store(("indices",), rows)
        rows = [dict(r) for r in rows]
    live = getattr(manager, "live", None)
    if live is not None:
        from .providers.angel_stream import EXCHANGE_NSE_CM

        live.stream.subscribe(EXCHANGE_NSE_CM, [r["token"] for r in rows if r.get("token")])
        overlay_ticks(rows, live.stream)
    return {"indices": rows, "fetched_at": utcnow().isoformat()}
