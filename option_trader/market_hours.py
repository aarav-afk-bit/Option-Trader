"""US options trading sessions from the NYSE calendar.

Holidays and 1 pm early closes are computed from the exchange's published rules, so the app
knows when prices are live without asking a server. Unscheduled closures (e.g. a national day
of mourning) are not predictable and are not included.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Optional

from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
US_OPEN, US_CLOSE, US_EARLY_CLOSE = time(9, 30), time(16, 0), time(13, 0)
US_PREMARKET = time(4, 0)
US_AFTER_HOURS_END = time(20, 0)


def _easter(year: int) -> date:
    """Gregorian Easter Sunday (anonymous Gregorian algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month = (h + l_ - 7 * m + 114) // 31
    day = ((h + l_ - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    d = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: date) -> Optional[date]:
    """NYSE rule: Saturday holidays move to Friday, Sunday holidays to Monday."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def nyse_holidays(year: int) -> dict[date, str]:
    """Full-day NYSE closures for ``year`` from the exchange's published rules."""
    h: dict[date, str] = {}
    ny = date(year, 1, 1)
    if ny.weekday() == 6:
        h[ny + timedelta(days=1)] = "New Year's Day (observed)"
    elif ny.weekday() != 5:  # NYSE does not close the preceding Friday for a Saturday New Year
        h[ny] = "New Year's Day"
    h[_nth_weekday(year, 1, 0, 3)] = "Martin Luther King Jr. Day"
    h[_nth_weekday(year, 2, 0, 3)] = "Washington's Birthday"
    h[_easter(year) - timedelta(days=2)] = "Good Friday"
    h[_last_weekday(year, 5, 0)] = "Memorial Day"
    if year >= 2022:
        h[_observed(date(year, 6, 19))] = "Juneteenth"
    h[_observed(date(year, 7, 4))] = "Independence Day"
    h[_nth_weekday(year, 9, 0, 1)] = "Labor Day"
    h[_nth_weekday(year, 11, 3, 4)] = "Thanksgiving Day"
    h[_observed(date(year, 12, 25))] = "Christmas Day"
    return h


def nyse_early_closes(year: int) -> dict[date, str]:
    """1:00 pm ET early closes: day before Independence Day, day after Thanksgiving, Christmas Eve."""
    out = {}
    holidays = nyse_holidays(year)
    jul3 = date(year, 7, 3)
    if jul3.weekday() < 5 and jul3 not in holidays and date(year, 7, 4).weekday() < 5:
        out[jul3] = "Day before Independence Day"
    out[_nth_weekday(year, 11, 3, 4) + timedelta(days=1)] = "Day after Thanksgiving"
    xmas_eve = date(year, 12, 24)
    if xmas_eve.weekday() < 5 and xmas_eve not in holidays:
        out[xmas_eve] = "Christmas Eve"
    return out


def us_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in nyse_holidays(d.year)


def us_close_time(d: date) -> time:
    return US_EARLY_CLOSE if d in nyse_early_closes(d.year) else US_CLOSE


def us_next_open(now: datetime) -> datetime:
    local = now.astimezone(ET)
    d = local.date()
    if local.time() >= US_OPEN or not us_trading_day(d):
        d += timedelta(days=1)
    while not us_trading_day(d):
        d += timedelta(days=1)
    return datetime.combine(d, US_OPEN, tzinfo=ET)


def us_session(now: datetime) -> dict:
    """US options session state from the NYSE calendar (holidays and early closes included)."""
    local = now.astimezone(ET)
    d, t = local.date(), local.time()
    hol = nyse_holidays(d.year)
    close = us_close_time(d)
    if d.weekday() >= 5:
        state, is_open = "weekend", False
    elif d in hol:
        state, is_open = "holiday", False
    elif t < US_PREMARKET:
        state, is_open = "closed", False
    elif t < US_OPEN:
        state, is_open = "pre-market", False
    elif t < close:
        state, is_open = "open", True
    elif t < US_AFTER_HOURS_END:
        state, is_open = "after-hours", False
    else:
        state, is_open = "closed", False
    msg = hol.get(d) or (nyse_early_closes(d.year).get(d) + " (early close 1:00 pm ET)" if d in nyse_early_closes(d.year) else None)
    return {
        "open": is_open,
        "state": state,
        "message": msg,
        "next_open": None if is_open else us_next_open(now).isoformat(),
        "closes_at": datetime.combine(d, close, tzinfo=ET).isoformat() if is_open else None,
        "timezone": "America/New_York",
        "source": "nyse-calendar",
        "estimate": False,
    }
