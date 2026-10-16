"""NSE trading-session awareness.

The session state comes from NSE's own ``/api/marketStatus`` when reachable. A clock-based
fallback (weekdays 09:15-15:30 IST, minus NSE's published F&O holidays when known) is used
otherwise, and is labelled as an estimate.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Iterable, Optional

from .models import IST

OPEN_TIME = time(9, 15)
CLOSE_TIME = time(15, 30)
PRE_OPEN_TIME = time(9, 0)


def is_trading_day(d: date, holidays: Iterable[date] = ()) -> bool:
    return d.weekday() < 5 and d not in set(holidays)


def next_open(now: datetime, holidays: Iterable[date] = ()) -> datetime:
    hol = set(holidays)
    local = now.astimezone(IST)
    d = local.date()
    if local.time() >= OPEN_TIME or not is_trading_day(d, hol):
        d += timedelta(days=1)
    while not is_trading_day(d, hol):
        d += timedelta(days=1)
    return datetime.combine(d, OPEN_TIME, tzinfo=IST)


def clock_session(now: datetime, holidays: Optional[Iterable[date]] = None) -> dict:
    """Session state from the clock and (optionally) the holiday list."""
    hol = list(holidays or [])
    local = now.astimezone(IST)
    t = local.time()
    trading_day = is_trading_day(local.date(), hol)
    if not trading_day:
        state = "holiday" if local.weekday() < 5 else "weekend"
        is_open = False
    elif t < PRE_OPEN_TIME:
        state, is_open = "closed", False
    elif t < OPEN_TIME:
        state, is_open = "pre-open", False
    elif t < CLOSE_TIME:
        state, is_open = "open", True
    else:
        state, is_open = "closed", False
    return {
        "open": is_open,
        "state": state,
        "next_open": None if is_open else next_open(now, hol).isoformat(),
        "closes_at": datetime.combine(local.date(), CLOSE_TIME, tzinfo=IST).isoformat() if is_open else None,
        "source": "clock" if holidays is None else "clock+nse-holidays",
        "estimate": True,
    }


def parse_nse_market_status(payload: dict) -> Optional[dict]:
    """Extract the F&O / capital-market session from NSE's marketStatus payload."""
    for m in payload.get("marketState") or []:
        if str(m.get("market", "")).lower() in ("capital market", "cm"):
            status = str(m.get("marketStatus", "")).strip().lower()
            return {
                "open": status == "open",
                "state": status or "unknown",
                "message": m.get("marketStatusMessage"),
                "trade_date": m.get("tradeDate"),
                "nifty": m.get("last"),
                "nifty_change_pct": m.get("percentChange"),
                "source": "nse",
                "estimate": False,
            }
    return None


def parse_nse_holidays(payload: dict) -> list[date]:
    out = []
    for h in payload.get("FO") or payload.get("CM") or []:
        try:
            out.append(datetime.strptime(h["tradingDate"], "%d-%b-%Y").date())
        except (KeyError, ValueError):
            continue
    return sorted(out)
