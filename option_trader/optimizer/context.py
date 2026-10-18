"""Build the distribution the optimiser uses from a live chain (and optional history)."""

from __future__ import annotations

import math
from typing import Optional

from ..analytics.chain import atm_iv
from ..models import Candle, OptionChain
from .distribution import Distribution, lognormal

DIST_MODES = ("atm", "realised", "implied", "auto")


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


def auto_view(chain: OptionChain, candles: Optional[list[Candle]]) -> dict:
    """Direction from the app's signals and width from realised vs implied volatility.

    The composite signal score (-1..1) becomes an expected move of up to half a standard
    deviation by expiry; the volatility used is realised vol when history is available
    (so rich or cheap premium shows up as edge), otherwise ATM IV.
    """
    from ..analytics.recommender import market_view
    from ..analytics.chain import analyze_chain
    from ..analytics.technicals import technical_snapshot

    t = chain.years_to_expiry()
    iv = atm_iv(chain)
    chain_sigs = [s.to_dict() for s in analyze_chain(chain).signals]
    tech = None
    if candles:
        try:
            tech = technical_snapshot(candles, iv)
        except ValueError:
            tech = None
    view = market_view(chain_sigs, tech["signals"] if tech else [],
                       tech["volatility"] if tech else {"view": "unknown"},
                       tech["trend_strength"] if tech else None)
    rv = realised_vol(candles or [])
    vol = rv or iv
    if vol is None:
        raise ValueError("No volatility estimate available for the auto view")
    score = view.score if view.direction in ("bullish", "bearish") else 0.0
    drift = round(score * 0.5 * vol * math.sqrt(max(t, 0.0)), 5)
    return {"drift": drift, "vol": vol, "direction": view.direction, "score": view.score,
            "vol_source": "realised" if rv else "atm_iv", "reasons": view.reasons[:4]}


def build_distribution(chain: OptionChain, r: float, mode: str = "atm", drift: float = 0.0,
                       vol_mult: float = 1.0, candles: Optional[list[Candle]] = None) -> Distribution:
    t = chain.years_to_expiry()
    if mode == "implied":
        from .implied import implied_distribution, shift

        return shift(implied_distribution(chain, r), drift)
    if mode == "auto":
        v = auto_view(chain, candles)
        forward = chain.forward or chain.spot * math.exp((r - chain.carry) * t)
        return lognormal(forward, v["vol"] * max(vol_mult, 0.05), t, v["drift"] + drift, kind="auto",
                         extra={"forward": round(forward, 2), "view": v["direction"], "signal_score": v["score"],
                                "vol_source": v["vol_source"], "reasons": v["reasons"]})
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
