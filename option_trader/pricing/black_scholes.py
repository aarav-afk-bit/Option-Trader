"""Black-Scholes(-Merton) pricing for European options.

Conventions:

* ``t`` is time to expiry in years, ``r`` and ``q`` are continuously-compounded annual rates,
  ``sigma`` is annualised volatility as a decimal.
* ``theta`` is per calendar day, ``vega`` and ``rho`` are per 1 percentage point.

US index options (SPX, NDX, RUT...) are European. Equity and ETF options are American, but the
market convention is still to quote their IV with Black-Scholes on the implied forward (which
absorbs dividends); the Valuation lab adds American models and the early-exercise premium.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Optional

from scipy.optimize import brentq
from scipy.stats import norm

from ..models import OptionChain

MIN_T = 1e-6  # avoid division by zero at the expiry instant


@dataclass
class Greeks:
    price: float
    delta: float
    gamma: float
    theta: float
    vega: float
    rho: float

    def to_dict(self) -> dict:
        return asdict(self)


def _d1d2(s: float, k: float, t: float, r: float, sigma: float, q: float) -> tuple[float, float]:
    vs = sigma * math.sqrt(t)
    d1 = (math.log(s / k) + (r - q + 0.5 * sigma * sigma) * t) / vs
    return d1, d1 - vs


def intrinsic(s: float, k: float, option_type: str) -> float:
    return max(s - k, 0.0) if option_type == "CE" else max(k - s, 0.0)


def bs_price(s: float, k: float, t: float, r: float, sigma: float, option_type: str,
             q: float = 0.0) -> float:
    if t <= MIN_T or sigma <= 0:
        # At expiry (or with zero vol) the option is worth its (discounted) intrinsic value.
        fwd = s * math.exp((r - q) * max(t, 0))
        return math.exp(-r * max(t, 0)) * intrinsic(fwd, k, option_type)
    d1, d2 = _d1d2(s, k, t, r, sigma, q)
    if option_type == "CE":
        return s * math.exp(-q * t) * norm.cdf(d1) - k * math.exp(-r * t) * norm.cdf(d2)
    return k * math.exp(-r * t) * norm.cdf(-d2) - s * math.exp(-q * t) * norm.cdf(-d1)


def greeks(s: float, k: float, t: float, r: float, sigma: float, option_type: str,
           q: float = 0.0) -> Greeks:
    price = bs_price(s, k, t, r, sigma, option_type, q)
    if t <= MIN_T or sigma <= 0:
        itm = (s > k) if option_type == "CE" else (s < k)
        delta = (1.0 if option_type == "CE" else -1.0) if itm else 0.0
        return Greeks(price, delta, 0.0, 0.0, 0.0, 0.0)
    d1, d2 = _d1d2(s, k, t, r, sigma, q)
    sqrt_t = math.sqrt(t)
    pdf = norm.pdf(d1)
    disc_q, disc_r = math.exp(-q * t), math.exp(-r * t)
    gamma = disc_q * pdf / (s * sigma * sqrt_t)
    vega = s * disc_q * pdf * sqrt_t / 100.0
    common = -s * disc_q * pdf * sigma / (2 * sqrt_t)
    if option_type == "CE":
        delta = disc_q * norm.cdf(d1)
        theta = common - r * k * disc_r * norm.cdf(d2) + q * s * disc_q * norm.cdf(d1)
        rho = k * t * disc_r * norm.cdf(d2) / 100.0
    else:
        delta = -disc_q * norm.cdf(-d1)
        theta = common + r * k * disc_r * norm.cdf(-d2) - q * s * disc_q * norm.cdf(-d1)
        rho = -k * t * disc_r * norm.cdf(-d2) / 100.0
    return Greeks(price, delta, gamma, theta / 365.0, vega, rho)


def implied_vol(price: float, s: float, k: float, t: float, r: float, option_type: str,
                q: float = 0.0, lo: float = 1e-4, hi: float = 5.0) -> Optional[float]:
    """Volatility that reproduces ``price``; None if the price is outside no-arbitrage bounds."""
    if price is None or price <= 0 or t <= MIN_T or s <= 0 or k <= 0:
        return None
    lower = bs_price(s, k, t, r, lo, option_type, q)
    upper = bs_price(s, k, t, r, hi, option_type, q)
    if not (lower - 1e-9 <= price <= upper):
        return None
    try:
        return brentq(lambda v: bs_price(s, k, t, r, v, option_type, q) - price, lo, hi,
                      xtol=1e-6, maxiter=200)
    except ValueError:
        return None


def enrich_chain(chain: OptionChain, r: float, now: Optional[datetime] = None) -> OptionChain:
    """Compute IV and Greeks for every priced contract from its live price.

    Pricing uses the market-implied forward (put-call parity) as the underlying, which is how
    listed options are actually priced; this keeps IV and Greeks consistent across strikes and
    correct for dividend-paying stocks. The IV a source published is kept in ``source_iv``.
    Contracts without a usable price keep the source's IV (if any) and are never estimated.
    """
    from .forward import carry_yield, implied_forward

    t = chain.years_to_expiry(now)
    fwd = implied_forward(chain, r, t)
    chain.forward = round(fwd, 2) if fwd else None
    chain.carry = carry_yield(chain.spot, fwd, r, t) if fwd else 0.0
    # A futures/underlying leg is never free money: it trades at the forward (cost of carry).
    if chain.futures_ltp:
        chain.fut_price = chain.futures_ltp  # a real exchange futures quote beats any estimate
    else:
        chain.fut_price = round(fwd if fwd else chain.spot * math.exp(r * t), 2) if chain.spot else None
    q_ = chain.carry
    solved = 0
    for row in chain.rows:
        for q in (row.call, row.put):
            if q is None:
                continue
            if q.iv is not None and q.source_iv is None:
                q.source_iv = q.iv
            px = q.price
            iv = implied_vol(px, chain.spot, q.strike, t, r, q.option_type, q_) if px else None
            if iv is not None:
                q.iv = iv
                solved += 1
            elif q.iv is None:
                q.iv = q.source_iv
            if q.iv:
                g = greeks(chain.spot, q.strike, t, r, q.iv, q.option_type, q_)
                q.delta, q.gamma, q.theta, q.vega = g.delta, g.gamma, g.theta, g.vega
    chain.notes = [n for n in chain.notes
                   if not n.startswith(("IV solved from market prices", "Greeks computed with", "IV/Greeks computed"))]
    basis = f" (forward {fwd:,.2f}, from put-call parity)" if fwd else " (spot; forward unavailable)"
    chain.notes.append(f"IV/Greeks computed with Black-Scholes on the implied forward from live prices{basis}")
    return chain
