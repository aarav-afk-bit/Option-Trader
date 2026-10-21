"""Data-quality checks on live option chains.

Exchange feeds occasionally carry quotes that cannot be right: a crossed book, a last trade
from hours ago that is now below intrinsic value, a call priced above the call one strike
lower, or an IV wildly off the smile. Such quotes are *kept and shown* (nothing is invented
or deleted) but flagged ``suspect`` with the reasons, and excluded from anything that would
amplify them: the implied forward, the implied distribution, IV analytics and the optimiser.

Two passes:

1. ``sanitize`` (before pricing): structural problems that need no model.
2. ``validate`` (after pricing): checks that need the forward and IVs.
"""

from __future__ import annotations

import math
from statistics import median
from typing import Optional

from .models import OptionChain, OptionQuote


def _flag(q: OptionQuote, reason: str) -> None:
    q.suspect = True
    if reason not in q.issues:
        q.issues.append(reason)


def sanitize(chain: OptionChain) -> None:
    for r in chain.rows:
        for q in (r.call, r.put):
            if q is None:
                continue
            if q.ltp is not None and q.ltp <= 0:
                q.ltp = None  # 0 means "no trade today", not a price of zero
            if q.bid is not None and q.ask is not None and q.bid > 0 and q.ask > 0 and q.bid > q.ask:
                _flag(q, f"crossed book (bid {q.bid:g} > ask {q.ask:g})")
                q.bid = q.ask = None
            if q.bid is not None and q.bid <= 0:
                q.bid = None
            if q.ask is not None and q.ask <= 0:
                q.ask = None
    # No-arbitrage monotonicity: calls get cheaper as the strike rises, puts dearer.
    for side, sign in (("call", -1), ("put", 1)):
        quotes = [(r.strike, getattr(r, side)) for r in chain.rows if getattr(r, side) and getattr(r, side).price]
        for (k1, a), (k2, b) in zip(quotes, quotes[1:]):
            slack = max((a.ask or a.price) - (a.bid or a.price), (b.ask or b.price) - (b.bid or b.price), 0.05)
            if sign < 0 and b.price > a.price + slack:
                _flag(b if b.ltp_stale or not b.bid else a, f"call at {k2:g} priced above call at {k1:g}")
            if sign > 0 and a.price > b.price + slack:
                _flag(a if a.ltp_stale or not a.bid else b, f"put at {k1:g} priced above put at {k2:g}")


def validate(chain: OptionChain, r: float) -> None:
    t = chain.years_to_expiry()
    fwd = chain.forward or chain.spot * math.exp(r * t)
    disc = math.exp(-r * t)
    for row in chain.rows:
        for q in (row.call, row.put):
            if q is None or not q.price:
                continue
            intrinsic = disc * max(fwd - q.strike, 0) if q.option_type == "CE" else disc * max(q.strike - fwd, 0)
            tol = max(0.05, 0.003 * intrinsic) + ((q.ask - q.bid) if q.bid and q.ask else 0)
            if q.price < intrinsic - tol:
                _flag(q, f"priced {q.price:g} below intrinsic value {intrinsic:.2f} (stale trade?)")
        # Put-call parity: C - P should equal (F - K) e^{-rT} within the spreads.
        c, p = row.call, row.put
        if c and p and c.price and p.price and not (c.suspect or p.suspect):
            gap = (c.price - p.price) - disc * (fwd - row.strike)
            spreads = sum((x.ask - x.bid) for x in (c, p) if x.bid and x.ask) or 0.0
            if abs(gap) > max(spreads * 1.5, 0.004 * chain.spot):
                worse = c if (c.ltp_stale or not c.bid) else p if (p.ltp_stale or not p.bid) else None
                for x in ([worse] if worse else [c, p]):
                    _flag(x, f"put-call parity off by {gap:+.2f}")
    # IV outliers against neighbouring strikes (robust: median absolute deviation).
    for side in ("call", "put"):
        pts = [(row.strike, getattr(row, side)) for row in chain.rows if getattr(row, side) and getattr(row, side).iv]
        for i, (k, q) in enumerate(pts):
            near = [x.iv for _, x in pts[max(0, i - 3): i] + pts[i + 1: i + 4]]
            if len(near) < 3:
                continue
            m = median(near)
            mad = median(abs(v - m) for v in near) or 0.005
            if abs(q.iv - m) > max(0.05, 6 * mad):
                _flag(q, f"IV {q.iv * 100:.1f}% far from neighbours ({m * 100:.1f}%)")


def report(chain: OptionChain) -> dict:
    quotes = [q for r in chain.rows for q in (r.call, r.put) if q is not None]
    priced = [q for q in quotes if q.price]
    suspect = [q for q in quotes if q.suspect]
    stale = [q for q in priced if q.ltp_stale]
    no_book = [q for q in priced if not (q.bid and q.ask)]
    n = max(len(priced), 1)
    score = 100 * (1 - len(suspect) / n) - 10 * (len(stale) / n) - 5 * (len(no_book) / n)
    return {
        "score": max(0, round(score)),
        "quotes": len(quotes), "priced": len(priced), "suspect": len(suspect),
        "stale_last_trade": len(stale), "no_bid_ask": len(no_book),
        "issues": [{"strike": q.strike, "type": q.option_type, "issues": q.issues} for q in suspect][:50],
    }
