"""Market-implied probability distribution from the live option chain (Breeden-Litzenberger).

The second derivative of call prices with respect to strike is the discounted risk-neutral
probability density: ``f(K) = e^{rT} d2C/dK2``. Raw quotes are too noisy to differentiate,
so the smile is fitted first:

1. Take out-of-the-money quotes (puts below the forward, calls above) with a live IV.
2. Fit IV as a quadratic in log-moneyness ``ln(K/F)``, weighted towards the money.
3. Re-price calls on a fine strike grid from the fitted smile (flat IV beyond the quoted
   range) and differentiate numerically; negative densities from noise are clipped.

The result captures skew and fat tails the market is pricing in, so win rates reflect what
the live chain implies rather than a single-vol lognormal.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np
from scipy.stats import norm

from ..models import OptionChain
from .distribution import Distribution


def smile_points(chain: OptionChain, forward: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    xs, ivs, w = [], [], []
    for r in chain.rows:
        q = r.put if r.strike < forward else r.call
        if q is None or not q.iv or not q.price or q.suspect:
            continue
        x = math.log(r.strike / forward)
        xs.append(x)
        ivs.append(q.iv)
        w.append(1.0 / (0.02 + abs(x)))  # trust near-the-money quotes most
    return np.array(xs), np.array(ivs), np.array(w)


def fit_smile(xs: np.ndarray, ivs: np.ndarray, w: np.ndarray):
    deg = 2 if len(xs) >= 5 else (1 if len(xs) >= 3 else 0)
    coeffs = np.polyfit(xs, ivs, deg, w=w) if deg else np.array([float(np.average(ivs, weights=w))])
    lo, hi = float(xs.min()), float(xs.max())

    def iv_at(x: np.ndarray) -> np.ndarray:
        v = np.polyval(coeffs, np.clip(x, lo, hi))  # flat beyond the quoted range
        return np.clip(v, 0.01, 3.0)

    return iv_at, coeffs


def implied_distribution(chain: OptionChain, r: float, points: int = 801,
                         width_sd: float = 5.0) -> Distribution:
    t = chain.years_to_expiry()
    if t <= 0:
        raise ValueError("The chain has expired")
    forward = chain.forward or chain.spot * math.exp((r - chain.carry) * t)
    xs, ivs, w = smile_points(chain, forward)
    if len(xs) < 3:
        raise ValueError("Not enough live quotes with IV to infer the market-implied distribution")
    iv_at, coeffs = fit_smile(xs, ivs, w)
    atm = float(iv_at(np.array([0.0]))[0])
    sd = atm * math.sqrt(t)
    k = forward * np.exp(np.linspace(-width_sd * sd, width_sd * sd, points + 2))
    sig = iv_at(np.log(k / forward))
    q = r - math.log(forward / chain.spot) / t if chain.spot else 0.0
    # Price calls on the grid with the fitted smile (Black-Scholes-Merton, vectorised).
    vs = sig * math.sqrt(t)
    d1 = (np.log(chain.spot / k) + (r - q + 0.5 * sig * sig) * t) / vs
    calls = chain.spot * math.exp(-q * t) * norm.cdf(d1) - k * math.exp(-r * t) * norm.cdf(d1 - vs)
    # Second derivative on a non-uniform grid.
    d1 = np.diff(calls) / np.diff(k)
    mid = (k[1:] + k[:-1]) / 2
    d2 = np.diff(d1) / np.diff(mid)
    dens = np.clip(np.exp(r * t) * d2, 0, None)
    centres = k[1:-1]
    widths = (k[2:] - k[:-2]) / 2
    probs = dens * widths
    total = probs.sum()
    if total <= 0:
        raise ValueError("Implied density degenerate; quotes may be inconsistent")
    probs = probs / total
    return Distribution(centres, probs, "implied", {
        "forward": round(forward, 2), "atm_iv": round(atm, 4), "years": round(t, 5),
        "smile": [round(float(c), 5) for c in coeffs], "quotes_used": int(len(xs)),
        "mass_captured": round(float(total), 4),
    })


def shift(dist: Distribution, drift: float) -> Distribution:
    """Apply a directional view by scaling outcomes (keeps the shape of the distribution)."""
    if not drift:
        return dist
    params = dict(dist.params, drift=drift)
    return Distribution(dist.prices * (1 + drift), dist.probs, dist.kind, params)
