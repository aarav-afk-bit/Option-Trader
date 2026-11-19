"""Implied forward (futures-equivalent) price from put-call parity.

Listed options are effectively priced off the forward price, which differs from spot by
interest and expected dividends. For each strike near the money,
``F = K + e^{rT} (C - P)``; the median over those strikes is the market's forward. It is then
expressed as a continuous carry/dividend yield ``q`` so the Black-Scholes-Merton functions
price exactly as Black-76 on that forward.
"""

from __future__ import annotations

import math
from statistics import median
from typing import Optional

from ..models import OptionChain


def implied_forward(chain: OptionChain, r: float, t: float, n: int = 5,
                    max_gap: float = 0.10) -> Optional[float]:
    if t <= 0 or not chain.spot:
        return None
    vals = []
    for k in sorted(chain.strikes, key=lambda k: abs(k - chain.spot)):
        c, p = chain.quote(k, "CE"), chain.quote(k, "PE")
        if (c and c.suspect) or (p and p.suspect):
            continue
        cp = c.price if c else None
        pp = p.price if p else None
        if cp and pp:
            vals.append(k + math.exp(r * t) * (cp - pp))
        if len(vals) >= n:
            break
    if not vals:
        return None
    f = median(vals)
    if abs(f / chain.spot - 1) > max_gap:  # inconsistent quotes: don't trust it
        return None
    return f


def carry_yield(spot: float, forward: float, r: float, t: float) -> float:
    """Continuous yield q with F = S * exp((r - q) * t)."""
    return r - math.log(forward / spot) / t
