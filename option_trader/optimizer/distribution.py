"""Probability distributions for the underlying at expiry.

The optimiser judges a strategy by its win rate and expected P&L, which need a view of where
the underlying may finish. Every distribution here is a discrete set of price points with
probabilities, built from live market inputs:

* ``atm`` (default): lognormal around the market forward with the live ATM implied vol. Under
  this "fair" model, contracts priced at a higher IV than ATM (rich wings) show positive
  expected value to sellers, and cheap ones to buyers.
* ``realised``: same, but with the underlying's realised (historical) volatility, so options
  priced above or below what the market has actually been moving show up as edge.
* A directional view shifts the centre (``drift``: expected % move by expiry) and a volatility
  view scales the width (``vol_mult``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from scipy.stats import norm


@dataclass
class Distribution:
    prices: np.ndarray  # price points (bin centres)
    probs: np.ndarray  # probability of finishing in each bin (sums to ~1)
    kind: str = "atm"
    params: dict = field(default_factory=dict)

    def mean(self) -> float:
        return float((self.prices * self.probs).sum())

    def quantile(self, p: float) -> float:
        cdf = np.cumsum(self.probs)
        return float(np.interp(p, cdf, self.prices))

    def describe(self) -> dict:
        return {"kind": self.kind, "mean": round(self.mean(), 2),
                "p05": round(self.quantile(0.05), 2), "p95": round(self.quantile(0.95), 2), **self.params}


def lognormal(forward: float, vol: float, t: float, drift: float = 0.0, points: int = 801,
              width_sd: float = 5.0, kind: str = "atm", extra: Optional[dict] = None) -> Distribution:
    """Lognormal terminal distribution with median ~ forward * (1 + drift)."""
    if t <= 0 or vol <= 0 or forward <= 0:
        raise ValueError("Distribution needs positive forward, volatility and time to expiry")
    sd = vol * math.sqrt(t)
    centre = math.log(forward * (1 + drift)) - 0.5 * sd * sd
    edges = np.exp(np.linspace(centre - width_sd * sd, centre + width_sd * sd, points + 1))
    cdf = norm.cdf((np.log(edges) - centre) / sd)
    probs = np.diff(cdf)
    probs /= probs.sum()
    prices = np.sqrt(edges[:-1] * edges[1:])  # geometric bin centres
    params = {"vol": round(vol, 4), "drift": drift, "years": round(t, 5)}
    params.update(extra or {})
    return Distribution(prices, probs, kind, params)
