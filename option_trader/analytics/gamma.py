"""Dealer gamma exposure (GEX) from live open interest.

Convention (the one most US desks publish): customers are net **sellers of calls** (covered calls,
overwriting) and net **buyers of puts** (hedges), so market makers are long the calls and short
the puts. Dealer gamma per contract is then ``+gamma`` for calls and ``-gamma`` for puts.

* GEX at a strike, in dollars of delta per 1% move:
  ``gamma * OI * 100 * S^2 * 0.01`` (calls positive, puts negative)
* Positive total GEX: dealers hedge against the move (sell rallies, buy dips), which damps
  volatility. Negative: they hedge with the move, which amplifies it.
* Zero-gamma ("flip") level: the spot price at which the total crosses zero, found by
  re-pricing every contract's gamma at hypothetical spots with its own IV and expiry.

It is a model of positioning, not a measurement: real dealer books are not public.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Iterable, Optional

import numpy as np
from scipy.stats import norm

from ..models import OptionChain


def _contracts(chains: Iterable[OptionChain], now: Optional[datetime]):
    """Arrays of (strike, T, iv, oi, sign, q) over every contract with OI and IV."""
    K, T, IV, OI, SG, Q = [], [], [], [], [], []
    for ch in chains:
        t = max(ch.years_to_expiry(now), 1.0 / (365 * 24))  # floor at one hour
        for row in ch.rows:
            for quote, sign in ((row.call, 1.0), (row.put, -1.0)):
                if quote and quote.oi and quote.iv and quote.iv > 0:
                    K.append(row.strike); T.append(t); IV.append(quote.iv); OI.append(quote.oi)
                    SG.append(sign); Q.append(ch.carry or 0.0)
    return tuple(np.array(a, float) for a in (K, T, IV, OI, SG, Q))


def _gamma(S: np.ndarray, K, T, iv, r, q):
    """Black-Scholes gamma for every (spot, contract) pair: S shape (m,1), contracts (n,)."""
    vs = iv * np.sqrt(T)
    d1 = (np.log(S / K) + (r - q + 0.5 * iv * iv) * T) / vs
    return np.exp(-q * T) * norm.pdf(d1) / (S * vs)


def gamma_exposure(chains: list[OptionChain], r: float, now: Optional[datetime] = None,
                   grid_pct: float = 0.12, grid_points: int = 121) -> dict:
    if not chains or not chains[0].spot:
        raise ValueError("Need a live chain with a spot price")
    S0 = chains[0].spot
    K, T, IV, OI, SG, Q = _contracts(chains, now)
    if not len(K):
        raise ValueError("No contracts with both open interest and implied volatility")
    scale = 100 * 0.01  # contract multiplier x 1% move
    g0 = _gamma(np.array([[S0]]), K, T, IV, r, Q)[0]
    per = SG * g0 * OI * scale * S0 * S0

    by_strike: dict[float, dict] = {}
    for k, v, s in zip(K, per, SG):
        d = by_strike.setdefault(float(k), {"strike": float(k), "call": 0.0, "put": 0.0})
        d["call" if s > 0 else "put"] += float(v)
    strikes = sorted(by_strike.values(), key=lambda d: d["strike"])
    for d in strikes:
        d["net"] = d["call"] + d["put"]

    # Total GEX profile across hypothetical spots -> zero-gamma level.
    grid = np.linspace(S0 * (1 - grid_pct), S0 * (1 + grid_pct), grid_points)
    G = _gamma(grid[:, None], K, T, IV, r, Q)
    profile = (G * (SG * OI)).sum(axis=1) * scale * grid * grid
    flip = None
    sign_changes = np.where(np.diff(np.sign(profile)) != 0)[0]
    if len(sign_changes):
        # the crossing nearest today's spot
        i = min(sign_changes, key=lambda j: abs(grid[j] - S0))
        x0, x1, y0, y1 = grid[i], grid[i + 1], profile[i], profile[i + 1]
        flip = float(x0 - y0 * (x1 - x0) / (y1 - y0))

    total = float(per.sum())
    call_wall = max(strikes, key=lambda d: d["call"])["strike"]
    put_wall = min(strikes, key=lambda d: d["put"])["strike"]
    regime = "positive" if total > 0 else "negative"
    expl = ("Dealers are long gamma: they sell rallies and buy dips, which tends to pin the price and damp moves."
            if total > 0 else
            "Dealers are short gamma: their hedging follows the move, which can accelerate rallies and sell-offs.")
    if flip is not None:
        expl += f" Below about {flip:,.2f} the sign flips." if total > 0 else f" Above about {flip:,.2f} it turns positive."
    return {
        "symbol": chains[0].symbol, "spot": S0, "expiries": [c.expiry.isoformat() for c in chains],
        "total_gex": round(total, 0), "regime": regime, "zero_gamma": round(flip, 2) if flip else None,
        "call_wall": call_wall, "put_wall": put_wall,
        "strikes": [{k: (round(v, 0) if k != "strike" else v) for k, v in d.items()} for d in strikes],
        "profile": [{"spot": round(float(x), 2), "gex": round(float(y), 0)} for x, y in zip(grid, profile)],
        "contracts": int(len(K)), "explanation": expl,
        "assumption": "Customers sell calls and buy puts (dealers long calls, short puts). Units: $ of dealer delta per 1% move.",
    }
