"""Build the distribution the optimiser uses from a live chain (and optional history)."""

from __future__ import annotations

import math
from typing import Optional

from ..analytics.chain import atm_iv
from ..models import Candle, OptionChain
from .distribution import Distribution, lognormal

DIST_MODES = ("atm", "realised")


def realised_vol(candles: list[Candle], n: int = 20) -> Optional[float]:
    closes = [c.close for c in candles][-(n + 1):]
    if len(closes) < n + 1:
        return None
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0 and b > 0]
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((x - mean) ** 2 for x in rets) / (len(rets) - 1)
    return math.sqrt(var * 252)


def build_distribution(chain: OptionChain, r: float, mode: str = "atm", drift: float = 0.0,
                       vol_mult: float = 1.0, candles: Optional[list[Candle]] = None) -> Distribution:
    t = chain.years_to_expiry()
    forward = chain.forward or chain.spot * math.exp((r - chain.carry) * t)
    iv = atm_iv(chain)
    if mode == "realised":
        vol = realised_vol(candles or [])
        if vol is None:
            raise ValueError("Realised volatility needs at least 21 days of price history")
    else:
        mode = "atm"
        vol = iv
        if vol is None:
            raise ValueError("The chain has no ATM implied volatility to build a distribution from")
    vol *= max(vol_mult, 0.05)
    return lognormal(forward, vol, t, drift, kind=mode, extra={"forward": round(forward, 2),
                                                              "atm_iv": round(iv, 4) if iv else None,
                                                              "vol_mult": vol_mult})
