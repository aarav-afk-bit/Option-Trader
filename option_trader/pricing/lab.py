"""Valuation lab: price one live contract with every model and compare with the market.

Inputs come only from the live chain: spot, the put-call-parity forward (as carry yield ``q``),
the term-matched Treasury rate, the contract's own implied volatility and, for Heston, a fit to
the same expiry's smile. Nothing is assumed except the Merton jump shape, which is scaled so the
model's total variance equals the market's (it changes the shape of the distribution, not its
overall width).
"""

from __future__ import annotations

import math
import time
from datetime import datetime
from typing import Optional

from scipy.optimize import brentq

from ..models import OptionChain
from ..providers.base import exercise_style
from . import valuation as V
from .black_scholes import MIN_T, implied_vol

# Merton jump shape: about one jump a year averaging -4% with 8% dispersion (equity-like).
JUMP_LAMBDA, JUMP_MEAN, JUMP_VOL = 1.0, -0.04, 0.08
MAX_SMILE_MONEYNESS = 0.35  # |ln K/F| used for the Heston fit
_HESTON_CACHE: dict[tuple, tuple[V.HestonParams, float, int]] = {}


class LabError(ValueError):
    pass


def _inputs(chain: OptionChain, r: float, now: Optional[datetime]) -> tuple[float, float, float]:
    S = chain.spot
    if not S:
        raise LabError("The chain has no live underlying price")
    T = max(chain.years_to_expiry(now), MIN_T)
    q = chain.carry or 0.0
    return S, T, q


def smile_points(chain: OptionChain, r: float, now: Optional[datetime] = None) -> list[dict]:
    """Out-of-the-money market IVs (the liquid side) for this expiry, nearest the forward first."""
    S, T, q = _inputs(chain, r, now)
    F = S * math.exp((r - q) * T)
    pts = []
    for row in chain.rows:
        kind = "CE" if row.strike >= F else "PE"
        quote = row.call if kind == "CE" else row.put
        if not quote or not quote.iv or quote.suspect or quote.price is None:
            continue
        if abs(math.log(row.strike / F)) > MAX_SMILE_MONEYNESS:
            continue
        spread = quote.spread_pct
        pts.append({"strike": row.strike, "kind": kind, "iv": quote.iv, "price": quote.price,
                    "weight": 1.0 / (1.0 + (spread or 20) / 10.0)})  # tight markets count more
    return pts


def fit_heston(chain: OptionChain, r: float, now: Optional[datetime] = None) -> tuple[V.HestonParams, float, int]:
    """Calibrate Heston to this expiry's smile; cached per chain snapshot."""
    key = (chain.symbol, chain.expiry, chain.fetched_at, round(r, 6))
    if key in _HESTON_CACHE:
        return _HESTON_CACHE[key]
    S, T, q = _inputs(chain, r, now)
    pts = smile_points(chain, r, now)
    if len(pts) < 5:
        raise LabError("Need at least 5 priced out-of-the-money strikes to fit Heston")
    params, rmse = V.calibrate_heston(S, T, r, q, [p["strike"] for p in pts], [p["iv"] for p in pts],
                                      [p["weight"] for p in pts], max_evals=150)
    if len(_HESTON_CACHE) > 32:
        _HESTON_CACHE.clear()
    _HESTON_CACHE[key] = (params, rmse, len(pts))
    return _HESTON_CACHE[key]


def heston_smile(chain: OptionChain, r: float, now: Optional[datetime] = None) -> dict:
    """Market smile next to the fitted Heston smile (as Black-Scholes IVs)."""
    S, T, q = _inputs(chain, r, now)
    pts = smile_points(chain, r, now)
    params, rmse, n = fit_heston(chain, r, now)
    strikes = [p["strike"] for p in pts]
    kinds = [p["kind"] for p in pts]
    model_px = V.heston_prices(S, strikes, T, r, q, kinds, params)
    fitted = [implied_vol(float(px), S, k, T, r, kd, q) for px, k, kd in zip(model_px, strikes, kinds)]
    return {"symbol": chain.symbol, "expiry": chain.expiry.isoformat(), "spot": S, "years": T,
            "points": [{**p, "heston_iv": iv} for p, iv in zip(pts, fitted)],
            "heston": params.as_dict(), "rmse_vol": rmse, "fitted_on": n}


def _american_iv(price: float, S, K, T, r, q, kind) -> Optional[float]:
    intrinsic = max(S - K, 0) if kind == "CE" else max(K - S, 0)
    if price is None or price <= intrinsic + 1e-6:
        return None
    try:
        return brentq(lambda v: V.baw(S, K, T, r, q, v, kind) - price, 1e-3, 5.0, xtol=1e-5)
    except ValueError:
        return None


def value_contract(chain: OptionChain, strike: float, kind: str, r: float, now: Optional[datetime] = None,
                   sigma: Optional[float] = None, mc_paths: int = 100_000, steps: int = 400) -> dict:
    """Price one contract with every model in ``valuation.MODELS``."""
    kind = kind.upper()
    if kind not in ("CE", "PE"):
        raise LabError("kind must be CE (call) or PE (put)")
    quote = chain.quote(strike, kind)
    if quote is None:
        raise LabError(f"{strike:g} {kind} is not listed for {chain.symbol} {chain.expiry}")
    S, T, q = _inputs(chain, r, now)
    vol = sigma or quote.iv
    if not vol:
        raise LabError("This contract has no implied volatility (no live price); enter a volatility to use")
    style = exercise_style(chain.symbol)
    F = S * math.exp((r - q) * T)
    market = {"bid": quote.bid, "ask": quote.ask, "mid": quote.mid, "last": quote.ltp, "price": quote.price,
              "iv": quote.iv, "spread_pct": quote.spread_pct}

    heston_params, heston_note = None, None
    try:
        heston_params, rmse, n = fit_heston(chain, r, now)
        heston_note = f"Fitted to {n} strikes of this expiry, RMSE {rmse * 100:.2f} vol pts"
    except LabError as exc:
        heston_note = str(exc)
    except Exception as exc:  # optimiser failure: report it, keep the other models
        heston_note = f"Heston fit failed: {exc}"

    jump_var = JUMP_LAMBDA * (JUMP_MEAN ** 2 + JUMP_VOL ** 2)
    merton_sigma = math.sqrt(max(vol * vol - jump_var, 0.25 * vol * vol))

    def run(key):
        if key == "bsm":
            return V.bsm(S, strike, T, r, q, vol, kind), None, None
        if key == "black76":
            return V.black76(F, strike, T, r, vol, kind), None, f"Forward {F:,.2f}"
        if key == "bachelier":
            return V.bachelier(S, strike, T, r, q, vol, kind), None, f"Normal vol ${vol * F:,.2f}/yr"
        if key == "merton":
            px = V.merton_jump(S, strike, T, r, q, merton_sigma, kind, JUMP_LAMBDA, JUMP_MEAN, JUMP_VOL)
            return px, None, f"Diffusion vol {merton_sigma * 100:.1f}% + jumps (same total variance)"
        if key == "heston":
            if heston_params is None:
                return None, None, heston_note
            return V.heston(S, strike, T, r, q, kind, heston_params), None, heston_note
        if key == "binomial":
            return V.binomial_crr(S, strike, T, r, q, vol, kind, steps=steps, american=True), None, f"{steps} steps"
        if key == "baw":
            return V.baw(S, strike, T, r, q, vol, kind), None, None
        if key == "bs2002":
            return V.bjerksund_stensland(S, strike, T, r, q, vol, kind), None, None
        if key == "mc":
            res = V.mc_european(S, strike, T, r, q, vol, kind, paths=mc_paths)
            return res.price, res, f"{res.paths:,} paths"
        if key == "lsm":
            res = V.mc_lsm(S, strike, T, r, q, vol, kind, paths=max(mc_paths // 4, 5000), steps=50)
            return res.price, res, f"{res.paths:,} paths × 50 steps"
        if key == "mc_heston":
            if heston_params is None:
                return None, None, heston_note
            res = V.mc_heston(S, strike, T, r, q, kind, heston_params, paths=max(mc_paths // 2, 5000), steps=100)
            return res.price, res, f"{res.paths:,} paths × 100 steps"
        raise KeyError(key)

    mid = quote.mid if quote.mid is not None else quote.price
    rows = []
    for info in V.MODELS:
        t0 = time.perf_counter()
        try:
            price, mc, note = run(info.key)
        except Exception as exc:  # one model failing must not hide the others
            price, mc, note = None, None, f"Failed: {exc}"
        ms = (time.perf_counter() - t0) * 1000
        applies = style == "american" or not info.american
        row = {"key": info.key, "name": info.name, "family": info.family, "american": info.american,
               "description": info.description, "price": None if price is None else round(float(price), 4),
               "ms": round(ms, 1), "note": note, "applies": applies}
        if mc is not None:
            row["stderr"] = round(mc.stderr, 5)
            row["ci95"] = [round(x, 4) for x in mc.ci95]
        if price is not None and mid:
            row["vs_mid"] = round(float(price) - mid, 4)
            row["in_spread"] = bool(quote.bid is not None and quote.ask is not None
                                    and quote.bid - 1e-9 <= price <= quote.ask + 1e-9)
        rows.append(row)

    euro = V.bsm(S, strike, T, r, q, vol, kind)
    amer = V.binomial_crr(S, strike, T, r, q, vol, kind, steps=steps, american=True)
    out = {
        "symbol": chain.symbol, "expiry": chain.expiry.isoformat(), "strike": strike, "kind": kind,
        "exercise": style, "source": chain.source, "fetched_at": chain.fetched_at.isoformat(),
        "inputs": {"spot": S, "forward": round(F, 4), "years": T, "days": round(T * 365, 2), "rate": r,
                   "rate_source": chain.rate_source, "carry": q, "sigma": vol,
                   "sigma_source": "your input" if sigma else "contract's market implied volatility"},
        "market": market, "models": rows,
        "early_exercise_premium": round(max(amer - euro, 0.0), 4),
        "heston": heston_params.as_dict() if heston_params else None, "heston_note": heston_note,
    }
    if style == "american" and quote.price:
        out["american_iv"] = _american_iv(quote.price, S, strike, T, r, q, kind)
    return out


def convergence(chain: OptionChain, strike: float, kind: str, r: float, now: Optional[datetime] = None,
                sigma: Optional[float] = None) -> dict:
    """How the tree and Monte Carlo approach the closed form as steps / paths grow."""
    quote = chain.quote(strike, kind.upper())
    if quote is None:
        raise LabError(f"{strike:g} {kind} is not listed")
    S, T, q = _inputs(chain, r, now)
    vol = sigma or quote.iv
    if not vol:
        raise LabError("This contract has no implied volatility")
    kind = kind.upper()
    american = exercise_style(chain.symbol) == "american"
    tree = [{"steps": n, "price": V.binomial_crr(S, strike, T, r, q, vol, kind, steps=n, american=american)}
            for n in (10, 25, 50, 100, 200, 400, 800)]
    mc = []
    for n in (1_000, 5_000, 20_000, 100_000, 400_000):
        res = V.mc_european(S, strike, T, r, q, vol, kind, paths=n, seed=11)
        mc.append({"paths": n, "price": res.price, "lo": res.ci95[0], "hi": res.ci95[1]})
    return {"bsm": V.bsm(S, strike, T, r, q, vol, kind), "american": american, "tree": tree, "mc": mc,
            "market_mid": quote.mid}

