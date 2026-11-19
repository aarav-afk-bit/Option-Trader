"""Corporate events that move US options: earnings and dividends.

Data: Yahoo Finance via ``yfinance`` (announced/confirmed earnings dates, past earnings dates,
announced ex-dividend date, dividend history). Nothing here is projected: if Yahoo doesn't list
an upcoming date, the app says so instead of guessing.

Analysis
* ``earnings_move``     the move the options market prices in for the report, from the jump in
                        implied variance between the expiries either side of it
* ``historical_moves``  how far the stock actually moved on past reports (daily closes)
* ``assignment_risk``   in-the-money American calls whose remaining time value is smaller than the
                        coming dividend: holders are likely to exercise early, so short calls there
                        are likely to be assigned the day before the ex-date
"""

from __future__ import annotations

import logging
import math
import threading
import time as _time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable, Optional

from .models import ET, Candle, OptionChain
from .providers.base import is_us_index, normalize_symbol

log = logging.getLogger(__name__)
CACHE_SECONDS = 6 * 3600


@dataclass
class CorporateEvents:
    symbol: str
    next_earnings: Optional[date] = None
    earnings_timing: Optional[str] = None  # "before open", "after close" or None (unknown)
    earnings_confirmed: bool = False  # Yahoo lists a single date (not an estimated range)
    past_earnings: list[date] = field(default_factory=list)
    past_timing: dict[str, str] = field(default_factory=dict)
    ex_dividend: Optional[date] = None
    dividend: Optional[float] = None  # last declared/paid amount per share
    dividends_per_year: Optional[int] = None
    source: str = "yahoo"
    fetched_at: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("next_earnings", "ex_dividend"):
            d[k] = d[k].isoformat() if d[k] else None
        d["past_earnings"] = [x.isoformat() for x in self.past_earnings]
        return d


def _to_date(v: Any) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.astimezone(ET).date() if v.tzinfo else v.date()
    if isinstance(v, date):
        return v
    if hasattr(v, "to_pydatetime"):
        return _to_date(v.to_pydatetime())
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _timing(ts: Any) -> Optional[str]:
    """Earnings timestamps carry the hour: before 12:00 ET = before the open, 16:00+ = after close."""
    if hasattr(ts, "to_pydatetime"):
        ts = ts.to_pydatetime()
    if not isinstance(ts, datetime) or ts.tzinfo is None:
        return None
    h = ts.astimezone(ET).hour
    if h == 0:
        return None  # date-only entries are stored at midnight
    return "before open" if h < 12 else "after close" if h >= 16 else None


def parse_yahoo(symbol: str, ticker: Any, today: Optional[date] = None) -> CorporateEvents:
    """Build CorporateEvents from a ``yfinance.Ticker`` (or any object with the same attributes)."""
    today = today or datetime.now(ET).date()
    ev = CorporateEvents(normalize_symbol(symbol))
    errors = []
    try:
        cal = ticker.calendar or {}
        if not isinstance(cal, dict) and hasattr(cal, "to_dict"):  # very old yfinance: DataFrame
            cal = {k: list(v.values()) for k, v in cal.to_dict().items()}
        dates = [d for d in (_to_date(x) for x in (cal.get("Earnings Date") or [])) if d]
        upcoming = sorted(d for d in dates if d >= today)
        if upcoming:
            ev.next_earnings = upcoming[0]
            ev.earnings_confirmed = len(dates) == 1
        exd = _to_date(cal.get("Ex-Dividend Date"))
        if exd and exd >= today:
            ev.ex_dividend = exd
    except Exception as exc:  # network/parse problems: keep going with what we have
        errors.append(f"calendar: {exc}")
    try:
        df = ticker.get_earnings_dates(limit=16)
        if df is not None and len(df):
            future = []
            for ts in df.index:
                d = _to_date(ts)
                if d is None:
                    continue
                t = _timing(ts)
                if d < today:
                    ev.past_earnings.append(d)
                    if t:
                        ev.past_timing[d.isoformat()] = t
                else:
                    future.append((d, t or ""))
            if future:
                d0, t0 = min(future)
                if ev.next_earnings is None or d0 <= ev.next_earnings:
                    ev.next_earnings, ev.earnings_timing = d0, t0 or None
            ev.past_earnings = sorted(set(ev.past_earnings), reverse=True)[:12]
    except Exception as exc:
        errors.append(f"earnings dates: {exc}")
    try:
        divs = ticker.dividends
        if divs is not None and len(divs):
            ev.dividend = float(divs.iloc[-1])
            last_year = [d for d in (_to_date(x) for x in divs.index) if d and d >= today - timedelta(days=370)]
            ev.dividends_per_year = len(last_year) or None
    except Exception as exc:
        errors.append(f"dividends: {exc}")
    if errors and ev.next_earnings is None and ev.dividend is None:
        ev.error = "; ".join(errors)
    ev.fetched_at = datetime.now(ET).isoformat()
    return ev


def _default_ticker_factory(sym: str):
    import yfinance as yf

    return yf.Ticker(sym)


class EventsService:
    """Cached corporate-events lookups (6 hours per symbol)."""

    def __init__(self, ticker_factory: Callable[[str], Any] = _default_ticker_factory):
        self.ticker_factory = ticker_factory
        self._cache: dict[str, tuple[float, CorporateEvents]] = {}
        self._lock = threading.Lock()

    def get(self, symbol: str) -> CorporateEvents:
        sym = normalize_symbol(symbol)
        if is_us_index(sym):  # cash-settled indices: no earnings, dividends are in the forward
            return CorporateEvents(sym, source="n/a", fetched_at=datetime.now(ET).isoformat())
        with self._lock:
            hit = self._cache.get(sym)
            if hit and _time.monotonic() - hit[0] < CACHE_SECONDS:
                return hit[1]
        from .providers.yahoo import yahoo_symbol

        try:
            ev = parse_yahoo(sym, self.ticker_factory(yahoo_symbol(sym)))
        except Exception as exc:
            ev = CorporateEvents(sym, error=f"Yahoo Finance unavailable: {exc}", fetched_at=datetime.now(ET).isoformat())
        with self._lock:
            # Failures are cached briefly so a dead network doesn't slow every request.
            self._cache[sym] = (_time.monotonic() - (CACHE_SECONDS - 300 if ev.error else 0), ev)
        return ev


# ------------------------------------------------------------------------------------------
# Analysis
# ------------------------------------------------------------------------------------------
def reaction_expiry_ok(expiry: date, earnings: date, timing: Optional[str]) -> bool:
    """Does an option expiring on ``expiry`` live through the market's reaction to the report?"""
    if timing == "before open":
        return expiry >= earnings
    return expiry > earnings  # after close (or unknown): reaction is the next session


def _atm(chain: OptionChain) -> tuple[Optional[float], Optional[float]]:
    """ATM implied vol and straddle price."""
    k = chain.atm_strike
    c, p = chain.quote(k, "CE"), chain.quote(k, "PE")
    ivs = [q.iv for q in (c, p) if q and q.iv]
    iv = sum(ivs) / len(ivs) if ivs else None
    straddle = (c.price + p.price) if c and p and c.price and p.price else None
    return iv, straddle


def earnings_move(post: OptionChain, pre: Optional[OptionChain] = None,
                  now: Optional[datetime] = None) -> dict:
    """The earnings move priced into the options.

    With an expiry before the report (``pre``) its ATM vol is the normal day-to-day volatility;
    the extra variance in the first expiry after the report is the event:
    ``event_var = iv_post^2 T_post - iv_pre^2 T_post``. The expected absolute move of a normal
    jump with that variance is ``sqrt(2/pi) * sqrt(event_var)``. Without a ``pre`` expiry, the
    ATM straddle / spot of ``post`` gives an upper bound (it also contains ordinary movement).
    """
    iv_post, straddle = _atm(post)
    out = {"expiry": post.expiry.isoformat(), "iv_post": iv_post, "straddle": straddle,
           "straddle_move_pct": round(straddle / post.spot * 100, 2) if straddle and post.spot else None,
           "event_move_pct": None, "iv_pre": None, "pre_expiry": None}
    if pre is not None and iv_post:
        iv_pre, _ = _atm(pre)
        T = post.years_to_expiry(now)
        if iv_pre and T > 0:
            event_var = iv_post ** 2 * T - iv_pre ** 2 * T
            out.update(iv_pre=iv_pre, pre_expiry=pre.expiry.isoformat())
            if event_var > 0:
                out["event_move_pct"] = round(math.sqrt(2 / math.pi) * math.sqrt(event_var) * 100, 2)
                out["event_sd_pct"] = round(math.sqrt(event_var) * 100, 2)
    return out


def historical_moves(past: list[date], candles: list[Candle], timing: dict[str, str]) -> dict:
    """Close-to-close moves on past reports (before open: prior close to that close; after close:
    that close to the next; unknown: prior close to next close)."""
    # Daily candles are stamped with their session date (often midnight UTC): don't shift time zones.
    days = sorted((c.ts.date(), c.close) for c in candles)
    idx = {d: i for i, (d, _) in enumerate(days)}
    moves = []
    for d in sorted(past):
        # the report date may be a non-trading day in the feed; use the next session
        i = idx.get(d)
        if i is None:
            later = [j for j, (x, _) in enumerate(days) if x > d]
            if not later:
                continue
            i = later[0]
        t = timing.get(d.isoformat())
        a, b = (i - 1, i) if t == "before open" else (i, i + 1) if t == "after close" else (i - 1, i + 1)
        if a < 0 or b >= len(days):
            continue
        move = days[b][1] / days[a][1] - 1
        moves.append({"date": d.isoformat(), "timing": t, "move_pct": round(move * 100, 2)})
    absm = [abs(m["move_pct"]) for m in moves]
    return {"moves": moves[::-1], "count": len(moves),
            "avg_abs_move_pct": round(sum(absm) / len(absm), 2) if absm else None,
            "max_abs_move_pct": round(max(absm), 2) if absm else None}


def assignment_risk(chain: OptionChain, ex_dividend: Optional[date], dividend: Optional[float]) -> dict:
    """In-the-money calls likely to be exercised early for the dividend (American options only)."""
    if not ex_dividend or not dividend or chain.exercise != "american" or ex_dividend > chain.expiry:
        return {"applies": False, "contracts": []}
    at_risk = []
    for row in chain.rows:
        c = row.call
        if not c or c.price is None or row.strike >= chain.spot:
            continue
        intrinsic = chain.spot - row.strike
        extrinsic = c.price - intrinsic
        # The put at the same strike approximates what the call holder gives up by exercising.
        put = row.put.price if row.put and row.put.price is not None else None
        give_up = put if put is not None else max(extrinsic, 0.0)
        if dividend > give_up:
            at_risk.append({"strike": row.strike, "call_price": c.price, "extrinsic": round(extrinsic, 4),
                            "put_price": put, "dividend": dividend})
    last_day = ex_dividend - timedelta(days=1)
    while last_day.weekday() >= 5:
        last_day -= timedelta(days=1)
    return {"applies": True, "ex_dividend": ex_dividend.isoformat(), "dividend": dividend,
            "exercise_by": last_day.isoformat(), "contracts": at_risk}
