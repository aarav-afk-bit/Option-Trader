"""Yahoo Finance provider (via the ``yfinance`` package).

Yahoo publishes option chains for US-listed underlyings; for NSE symbols it usually has
spot prices and history but no options, in which case this provider raises
``DataUnavailableError`` and the manager moves on to the next source.

Symbol mapping:

* NSE indices map to Yahoo index tickers (``NIFTY`` -> ``^NSEI``).
* Bare NSE symbols get the ``.NS`` suffix (``RELIANCE`` -> ``RELIANCE.NS``).
* Anything with an explicit suffix/prefix (``^``, ``.``, ``=``) is used as-is.
* ``US:AAPL`` forces a US ticker (``AAPL``).
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime, time, timezone
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

from ..errors import DataUnavailableError
from ..models import Candle, OptionChain, OptionQuote, StrikeRow, utcnow
from .base import DataProvider, normalize_symbol

log = logging.getLogger(__name__)

YAHOO_INDEX_MAP = {
    "NIFTY": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "FINNIFTY": "NIFTY_FIN_SERVICE.NS",
    "MIDCPNIFTY": "NIFTY_MID_SELECT.NS",
    "NIFTYNXT50": "^NSMIDCP",
    "SENSEX": "^BSESN",
}
US_CLOSE = time(16, 0)
NEW_YORK = ZoneInfo("America/New_York")


def yahoo_symbol(symbol: str) -> str:
    raw = symbol.strip()
    if raw.upper().startswith("US:"):
        return raw[3:].strip().upper()
    if any(ch in raw for ch in "^.="):
        return raw.upper()
    sym = normalize_symbol(raw)
    return YAHOO_INDEX_MAP.get(sym, f"{sym}.NS")


def _clean(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _clean_int(v: Any) -> Optional[int]:
    f = _clean(v)
    return int(f) if f is not None else None


def _sane_iv(v: Any) -> Optional[float]:
    # Yahoo reports placeholder IVs (~0 or several hundred %) for untraded contracts.
    f = _clean(v)
    return f if f is not None and 0.01 <= f <= 5 else None


def _default_ticker_factory(sym: str):
    import yfinance as yf

    return yf.Ticker(sym)


class YahooProvider(DataProvider):
    name = "yahoo"

    def __init__(self, ticker_factory: Optional[Callable[[str], Any]] = None):
        self._ticker = ticker_factory or _default_ticker_factory

    def get_expiries(self, symbol: str) -> list[date]:
        ysym = yahoo_symbol(symbol)
        try:
            raw = self._ticker(ysym).options
        except Exception as exc:  # yfinance raises a variety of errors
            raise DataUnavailableError(f"Yahoo Finance request for {ysym} failed: {exc}") from exc
        expiries = sorted(date.fromisoformat(e) for e in raw or ())
        if not expiries:
            raise DataUnavailableError(f"Yahoo Finance has no option expiries for {ysym}")
        return expiries

    def get_spot(self, symbol: str) -> float:
        ysym = yahoo_symbol(symbol)
        t = self._ticker(ysym)
        price = None
        try:
            price = _clean(t.fast_info["last_price"])
        except Exception:
            pass
        if not price:
            try:
                hist = t.history(period="5d", interval="1d")
                if len(hist):
                    price = _clean(hist["Close"].iloc[-1])
            except Exception as exc:
                raise DataUnavailableError(f"Yahoo Finance price for {ysym} failed: {exc}") from exc
        if not price:
            raise DataUnavailableError(f"Yahoo Finance returned no price for {ysym}")
        return price

    def get_option_chain(self, symbol: str, expiry: Optional[date] = None) -> OptionChain:
        ysym = yahoo_symbol(symbol)
        expiries = self.get_expiries(symbol)
        if expiry is None:
            expiry = next((e for e in expiries if e >= date.today()), expiries[0])
        elif expiry not in expiries:
            raise DataUnavailableError(f"{expiry} is not a listed Yahoo expiry for {ysym}")
        try:
            oc = self._ticker(ysym).option_chain(expiry.isoformat())
        except Exception as exc:
            raise DataUnavailableError(f"Yahoo Finance option chain for {ysym} failed: {exc}") from exc

        rows: dict[float, StrikeRow] = {}
        currency = "USD"
        for frame, opt_type in ((oc.calls, "CE"), (oc.puts, "PE")):
            for rec in frame.to_dict("records"):
                strike = _clean(rec.get("strike"))
                if strike is None:
                    continue
                currency = rec.get("currency") or currency
                q = OptionQuote(
                    strike=strike,
                    option_type=opt_type,  # type: ignore[arg-type]
                    expiry=expiry,
                    ltp=_clean(rec.get("lastPrice")),
                    bid=_clean(rec.get("bid")),
                    ask=_clean(rec.get("ask")),
                    volume=_clean_int(rec.get("volume")),
                    oi=_clean(rec.get("openInterest")),
                    change=_clean(rec.get("change")),
                    iv=_sane_iv(rec.get("impliedVolatility")),
                    symbol=rec.get("contractSymbol"),
                )
                row = rows.setdefault(strike, StrikeRow(strike))
                if opt_type == "CE":
                    row.call = q
                else:
                    row.put = q

        is_nse = ysym.endswith(".NS") or ysym.startswith("^NSE")
        chain = OptionChain(
            symbol=ysym,
            expiry=expiry,
            spot=self.get_spot(symbol),
            rows=list(rows.values()),
            source="yahoo",
            fetched_at=utcnow(),
            expiries=expiries,
            currency=currency,
            lot_size=None if is_nse else 100,
            expiry_at=None if is_nse else datetime.combine(expiry, US_CLOSE, tzinfo=NEW_YORK),
            notes=["Yahoo Finance quotes can be delayed by 15 minutes or more"],
        )
        return chain.validate()

    def get_history(self, symbol: str, period: str = "1y", interval: str = "1d") -> list[Candle]:
        ysym = yahoo_symbol(symbol)
        try:
            df = self._ticker(ysym).history(period=period, interval=interval, auto_adjust=False)
        except Exception as exc:
            raise DataUnavailableError(f"Yahoo Finance history for {ysym} failed: {exc}") from exc
        candles = []
        for ts, rec in df.iterrows():
            o, h, l, c = (_clean(rec.get(k)) for k in ("Open", "High", "Low", "Close"))
            if None in (o, h, l, c):
                continue
            dt = ts.to_pydatetime()
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            candles.append(Candle(dt, o, h, l, c, _clean(rec.get("Volume")) or 0.0))
        if not candles:
            raise DataUnavailableError(f"Yahoo Finance returned no history for {ysym}")
        return candles
