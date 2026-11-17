"""Volatility surface and skew from live chains.

* Surface: out-of-the-money implied volatility by expiry and moneyness ``ln(K/F)`` (each expiry
  measured against its own put-call-parity forward).
* Skew per expiry, in the standard delta terms:
  25-delta risk reversal ``RR = IV(25d call) - IV(25d put)`` (negative = puts dearer: downside
  fear) and 25-delta butterfly ``BF = (IV(25d call) + IV(25d put)) / 2 - IV(ATM)`` (how fat the
  tails are priced). IVs at exactly 25 delta are interpolated between the listed strikes.
* Constant-maturity 30-day ATM IV, interpolated in total variance between the expiries either
  side of 30 days (used for IV rank when the source doesn't publish IV30).
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Optional

from ..models import OptionChain


def _forward(ch: OptionChain, r: float, t: float) -> float:
    return ch.forward or ch.spot * math.exp((r - (ch.carry or 0.0)) * t)


def _interp(xs: list[float], ys: list[float], x: float) -> Optional[float]:
    pts = sorted(zip(xs, ys))
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if min(x0, x1) <= x <= max(x0, x1) and x1 != x0:
            return float(y0 + (y1 - y0) * (x - x0) / (x1 - x0))
    return None


def atm_iv_at_forward(ch: OptionChain, r: float, now: Optional[datetime] = None) -> Optional[float]:
    t = ch.years_to_expiry(now)
    F = _forward(ch, r, t)
    pts = [(row.strike, q.iv) for row in ch.rows for q in ((row.call if row.strike >= F else row.put),)
           if q and q.iv and not q.suspect]
    if not pts:
        return None
    below = [p for p in pts if p[0] <= F]
    above = [p for p in pts if p[0] >= F]
    if below and above:
        (k0, v0), (k1, v1) = max(below), min(above)
        return float(v0 if k1 == k0 else v0 + (v1 - v0) * (F - k0) / (k1 - k0))
    return float(min(pts, key=lambda p: abs(p[0] - F))[1])


def skew_metrics(ch: OptionChain, r: float, now: Optional[datetime] = None) -> dict:
    atm = atm_iv_at_forward(ch, r, now)
    calls = [(q.delta, q.iv) for row in ch.rows for q in (row.call,) if q and q.iv and q.delta and not q.suspect]
    puts = [(q.delta, q.iv) for row in ch.rows for q in (row.put,) if q and q.iv and q.delta and not q.suspect]
    c25 = _interp([d for d, _ in calls], [v for _, v in calls], 0.25) if calls else None
    p25 = _interp([d for d, _ in puts], [v for _, v in puts], -0.25) if puts else None
    c10 = _interp([d for d, _ in calls], [v for _, v in calls], 0.10) if calls else None
    p10 = _interp([d for d, _ in puts], [v for _, v in puts], -0.10) if puts else None
    rr = c25 - p25 if c25 is not None and p25 is not None else None
    bf = (c25 + p25) / 2 - atm if c25 is not None and p25 is not None and atm else None
    return {"atm_iv": atm, "call25_iv": c25, "put25_iv": p25, "call10_iv": c10, "put10_iv": p10,
            "rr25": rr, "bf25": bf}


def surface(chains: list[OptionChain], r_for, now: Optional[datetime] = None, max_abs_moneyness: float = 0.4) -> dict:
    """``r_for(chain)`` gives the rate for each chain (term-matched)."""
    rows = []
    for ch in chains:
        r = r_for(ch)
        t = ch.years_to_expiry(now)
        if t <= 0 or not ch.spot:
            continue
        F = _forward(ch, r, t)
        pts = []
        for row in ch.rows:
            q = row.call if row.strike >= F else row.put
            if not q or not q.iv or q.suspect:
                continue
            m = math.log(row.strike / F)
            if abs(m) > max_abs_moneyness:
                continue
            pts.append({"strike": row.strike, "moneyness": round(m, 5), "iv": float(q.iv),
                        "delta": None if q.delta is None else float(q.delta),
                        "type": q.option_type})
        sk = skew_metrics(ch, r, now)
        rows.append({"expiry": ch.expiry.isoformat(), "days": round(t * 365, 2), "years": t, "forward": round(F, 4),
                     **sk, "points": pts})
    rows.sort(key=lambda x: x["years"])
    return {"symbol": chains[0].symbol if chains else None, "spot": chains[0].spot if chains else None,
            "expiries": rows, "iv30": constant_maturity_iv(rows)}


def constant_maturity_iv(rows: list[dict], days: float = 30.0) -> Optional[float]:
    """ATM IV for exactly ``days``, interpolating total variance between bracketing expiries."""
    pts = [(x["years"], x["atm_iv"]) for x in rows if x.get("atm_iv") and x["years"] > 0]
    if not pts:
        return None
    T = days / 365
    below = [p for p in pts if p[0] <= T]
    above = [p for p in pts if p[0] >= T]
    if below and above:
        (t0, v0), (t1, v1) = max(below), min(above)
        if t1 == t0:
            return v0
        w0, w1 = v0 * v0 * t0, v1 * v1 * t1
        w = w0 + (w1 - w0) * (T - t0) / (t1 - t0)
        return math.sqrt(max(w, 0) / T)
    # outside the listed range: use the nearest expiry only if it's reasonably close
    t, v = min(pts, key=lambda p: abs(p[0] - T))
    return v if 0.5 * T <= t <= 2 * T else None
