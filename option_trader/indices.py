"""Live US index levels and day moves: S&P 500, Nasdaq-100, Dow, Russell 2000 and the VIX.

Primary source is Cboe's delayed quotes; Yahoo Finance is the fallback. When the Tradier
stream is running, its real-time index prints replace the snapshot values.
"""

from __future__ import annotations

import logging
from typing import Optional

from .errors import DataUnavailableError
from .models import utcnow

log = logging.getLogger(__name__)

# (display name, option symbol (or None), Cboe symbol (or None), Yahoo ticker, stream symbol)
INDEX_SPECS = [
    ("S&P 500", "SPX", "SPX", "^GSPC", "SPX"),
    ("NASDAQ 100", "NDX", "NDX", "^NDX", "NDX"),
    ("DOW JONES", None, None, "^DJI", None),
    ("RUSSELL 2000", "RUT", "RUT", "^RUT", "RUT"),
    ("VIX", "VIX", "VIX", "^VIX", "VIX"),
]


def _row(name, symbol, last, prev, high=None, low=None, source="", ts=None, token=None) -> dict:
    change = (last - prev) if (last is not None and prev) else None
    return {
        "name": name, "symbol": symbol, "last": last, "prev_close": prev,
        "change": round(change, 2) if change is not None else None,
        "pct": round(change / prev * 100, 2) if change is not None else None,
        "high": high, "low": low, "source": source, "timestamp": ts, "token": token,
    }


def fetch_from_cboe(cboe) -> list[dict]:
    out = []
    for name, sym, csym, _y, tok in INDEX_SPECS:
        if not csym:
            continue
        try:
            q = cboe.get_quote(csym)
        except DataUnavailableError as exc:
            log.info("Cboe quote %s unavailable: %s", csym, exc)
            continue
        out.append(_row(name, sym, q["last"], q["prev_close"], q["high"], q["low"], "cboe", utcnow().isoformat(), tok))
    if not out:
        raise DataUnavailableError("Cboe returned none of the tracked indices")
    return out


def fetch_from_yahoo(ticker_factory=None) -> list[dict]:
    if ticker_factory is None:
        import yfinance as yf

        ticker_factory = yf.Ticker
    out = []
    for name, sym, _c, ysym, tok in INDEX_SPECS:
        try:
            fi = ticker_factory(ysym).fast_info
            last, prev = float(fi["last_price"]), float(fi["previous_close"])
            high, low = fi.get("day_high"), fi.get("day_low")
        except Exception as exc:
            log.info("Yahoo index %s unavailable: %s", ysym, exc)
            continue
        out.append(_row(name, sym, last, prev, high, low, "yahoo", utcnow().isoformat(), tok))
    if not out:
        raise DataUnavailableError("Yahoo Finance returned no index levels")
    return out


def overlay_ticks(rows: list[dict], stream) -> None:
    """Replace levels with streamed prints where available (keeps prev close for the change)."""
    if stream is None:
        return
    for r in rows:
        t = stream.tick(r["token"]) if r.get("token") else None
        if t and t.get("ltp"):
            prev = r["prev_close"] or t.get("close")
            r.update(_row(r["name"], r["symbol"], t["ltp"], prev, r["high"], r["low"],
                          r["source"] + "+ticks", r["timestamp"], r["token"]))


def merge(primary: list[dict], extra: list[dict]) -> list[dict]:
    """Primary rows, plus any index only the other source has (e.g. the Dow from Yahoo), in spec order."""
    names = {r["name"] for r in primary}
    rows = primary + [r for r in extra if r["name"] not in names]
    order = {spec[0]: i for i, spec in enumerate(INDEX_SPECS)}
    return sorted(rows, key=lambda r: order.get(r["name"], 99))


def get_indices(manager) -> dict:
    cached = manager._cached(("indices",))
    if cached is not None:
        rows = [dict(r) for r in cached]
    else:
        if not manager.is_online():
            raise DataUnavailableError("No internet connection: index levels unavailable")
        cboe = next((p for p in manager.providers if p.name == "cboe"), None)
        errors = []
        rows: Optional[list[dict]] = None
        if cboe is not None and hasattr(cboe, "get_quote"):
            try:
                rows = fetch_from_cboe(cboe)
            except DataUnavailableError as exc:
                errors.append(f"cboe: {exc}")
        try:
            yahoo_rows = fetch_from_yahoo()
            rows = merge(rows, yahoo_rows) if rows else yahoo_rows
        except DataUnavailableError as exc:
            errors.append(f"yahoo: {exc}")
        if not rows:
            raise DataUnavailableError("Index levels unavailable. " + " | ".join(errors))
        manager._store(("indices",), rows)
        rows = [dict(r) for r in rows]
    live = getattr(manager, "live", None)
    if live is not None:
        live.stream.subscribe([r["token"] for r in rows if r.get("token")])
        overlay_ticks(rows, live.stream)
    return {"indices": rows, "fetched_at": utcnow().isoformat()}
