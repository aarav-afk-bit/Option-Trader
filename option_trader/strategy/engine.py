"""Payoff and risk analysis for multi-leg option strategies.

A strategy is a list of legs. Each leg is an option (CE/PE) or the underlying itself (FUT),
bought or sold at an entry premium. ``analyze`` evaluates it over a grid of underlying prices:

* the P&L at the nearest expiry (far-dated calendar legs are valued with Black-Scholes),
* the P&L today and at an optional target date using each leg's IV (optionally shifted),
* breakevens, max profit / max loss (flagging unlimited ones), net premium,
* probability of profit under a lognormal distribution, and aggregate Greeks.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Literal, Optional

import numpy as np
from scipy.stats import norm

from ..models import OptionChain, default_expiry_at
from ..pricing.black_scholes import greeks, implied_vol

LegType = Literal["CE", "PE", "FUT"]
Action = Literal["BUY", "SELL"]
YEAR_SECONDS = 365.0 * 24 * 3600


@dataclass
class Leg:
    option_type: LegType
    action: Action
    strike: float = 0.0
    lots: int = 1
    premium: float = 0.0  # entry price per unit (for FUT: the entry price of the underlying)
    expiry: Optional[date] = None
    iv: Optional[float] = None
    expiry_at: Optional[datetime] = None
    market_price: Optional[float] = None  # current live price, used to value the leg today
    bid: Optional[float] = None  # live best bid/ask when the leg was priced (for cost estimates)
    ask: Optional[float] = None

    @property
    def sign(self) -> int:
        return 1 if self.action == "BUY" else -1

    def expiry_datetime(self) -> Optional[datetime]:
        if self.expiry_at is not None:
            return self.expiry_at
        if self.expiry is None:
            return None
        return default_expiry_at(self.expiry)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["expiry"] = self.expiry.isoformat() if self.expiry else None
        d["expiry_at"] = self.expiry_at.isoformat() if self.expiry_at else None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Leg":
        exp = d.get("expiry")
        exp_at = d.get("expiry_at")
        return cls(
            option_type=str(d["option_type"]).upper(),  # type: ignore[arg-type]
            action=str(d["action"]).upper(),  # type: ignore[arg-type]
            strike=float(d.get("strike") or 0),
            lots=int(d.get("lots") or 1),
            premium=float(d.get("premium") or 0),
            expiry=date.fromisoformat(exp) if isinstance(exp, str) and exp else exp,
            iv=float(d["iv"]) if d.get("iv") not in (None, "") else None,
            expiry_at=datetime.fromisoformat(exp_at) if isinstance(exp_at, str) and exp_at else None,
            market_price=float(d["market_price"]) if d.get("market_price") not in (None, "") else None,
        )


@dataclass
class Strategy:
    name: str
    legs: list[Leg]
    lot_size: int = 1
    description: str = ""
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "lot_size": self.lot_size,
            "tags": self.tags,
            "legs": [leg.to_dict() for leg in self.legs],
        }


def leg_from_chain(chain: OptionChain, option_type: LegType, action: Action, strike: float = 0.0,
                   lots: int = 1, fill: str = "ltp") -> Leg:
    """Create a leg priced from a real option chain.

    ``fill``: ``ltp`` (last traded), ``mid`` (bid/ask mid) or ``market`` (pay the ask when
    buying, receive the bid when selling).
    """
    if option_type == "FUT":
        return Leg("FUT", action, 0.0, lots, chain.fut_price or chain.spot, chain.expiry, None, chain.expiry_at)
    q = chain.quote(strike, option_type)
    if q is None:
        raise ValueError(f"{strike:g} {option_type} is not in the {chain.symbol} chain")
    if fill == "market":
        px = (q.ask if action == "BUY" else q.bid) or q.price
    elif fill == "mid":
        px = q.mid or q.price
    else:
        px = q.price
    if not px:
        raise ValueError(f"No live price for {strike:g} {option_type} in the {chain.symbol} chain")
    return Leg(option_type, action, strike, lots, float(px), chain.expiry, q.iv, chain.expiry_at,
               q.mid or q.price, q.bid, q.ask)


def _years(start: datetime, end: Optional[datetime]) -> float:
    if end is None:
        return 0.0
    return max((end - start).total_seconds(), 0.0) / YEAR_SECONDS


def _leg_iv(leg: Leg, spot: float, now: datetime, r: float, default_iv: float, q: float = 0.0) -> float:
    """Volatility used to value the leg before expiry.

    Preference: IV implied by the current live price under this model (so a position opened
    at market shows ~zero P&L today), then the source's IV, then the IV implied by the entry
    premium, then ``default_iv``.
    """
    if leg.option_type == "FUT":
        return default_iv
    t = _years(now, leg.expiry_datetime())
    if leg.market_price:
        solved = implied_vol(leg.market_price, spot, leg.strike, t, r, leg.option_type, q)
        if solved:
            return solved
    if leg.iv:
        return leg.iv
    if leg.premium > 0:
        solved = implied_vol(leg.premium, spot, leg.strike, t, r, leg.option_type, q)
        if solved:
            return solved
    return default_iv


def bs_price_vec(s: np.ndarray, k: float, t: float, r: float, sigma: float,
                 option_type: str, q: float = 0.0) -> np.ndarray:
    """Vectorised Black-Scholes-Merton price over an array of spot prices (carry yield q)."""
    s = np.asarray(s, dtype=float)
    vs = sigma * math.sqrt(t)
    d1 = (np.log(s / k) + (r - q + 0.5 * sigma * sigma) * t) / vs
    d2 = d1 - vs
    disc, dq = math.exp(-r * t), math.exp(-q * t)
    if option_type == "CE":
        return s * dq * norm.cdf(d1) - k * disc * norm.cdf(d2)
    return k * disc * norm.cdf(-d2) - s * dq * norm.cdf(-d1)


def greeks_vec(s: np.ndarray, k: float, t: float, r: float, sigma: float,
               option_type: str, q: float = 0.0) -> dict[str, np.ndarray]:
    """Vectorised Black-Scholes Greeks (theta per day, vega per vol point)."""
    s = np.asarray(s, dtype=float)
    if t <= 0 or sigma <= 0:
        itm = s > k if option_type == "CE" else s < k
        delta = np.where(itm, 1.0 if option_type == "CE" else -1.0, 0.0)
        zero = np.zeros_like(s)
        return {"delta": delta, "gamma": zero, "theta": zero, "vega": zero}
    sqrt_t = math.sqrt(t)
    d1 = (np.log(s / k) + (r - q + 0.5 * sigma * sigma) * t) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    pdf = norm.pdf(d1)
    disc, dq = math.exp(-r * t), math.exp(-q * t)
    gamma = dq * pdf / (s * sigma * sqrt_t)
    vega = s * dq * pdf * sqrt_t / 100.0
    common = -s * dq * pdf * sigma / (2 * sqrt_t)
    if option_type == "CE":
        delta = dq * norm.cdf(d1)
        theta = common - r * k * disc * norm.cdf(d2) + q * s * dq * norm.cdf(d1)
    else:
        delta = -dq * norm.cdf(-d1)
        theta = common + r * k * disc * norm.cdf(-d2) - q * s * dq * norm.cdf(-d1)
    return {"delta": delta, "gamma": gamma, "theta": theta / 365.0, "vega": vega}


def _leg_value(leg: Leg, s: np.ndarray, at: datetime, r: float, iv: float, q: float = 0.0) -> np.ndarray:
    """Per-unit value of the leg's instrument at time ``at`` across prices ``s``."""
    t = _years(at, leg.expiry_datetime())
    if leg.option_type == "FUT":
        # A future is worth the forward of the spot over its remaining life (spot at expiry).
        return s.astype(float) * math.exp((r - q) * t)
    if t <= 0:
        if leg.option_type == "CE":
            return np.maximum(s - leg.strike, 0.0)
        return np.maximum(leg.strike - s, 0.0)
    return bs_price_vec(s, leg.strike, t, r, iv, leg.option_type, q)


def pnl_curve(strategy: Strategy, s: np.ndarray, at: datetime, r: float,
              ivs: list[float], q: float = 0.0) -> np.ndarray:
    total = np.zeros_like(s, dtype=float)
    for leg, iv in zip(strategy.legs, ivs):
        qty = leg.lots * strategy.lot_size
        total += leg.sign * qty * (_leg_value(leg, s, at, r, iv, q) - leg.premium)
    return total


def _breakevens(s: np.ndarray, pnl: np.ndarray) -> list[float]:
    out = []
    for i in range(len(s) - 1):
        a, b = pnl[i], pnl[i + 1]
        if a == 0 and (i == 0 or pnl[i - 1] != 0):
            out.append(float(s[i]))
        elif a * b < 0:
            out.append(float(s[i] - a * (s[i + 1] - s[i]) / (b - a)))
    return [round(x, 2) for x in out]


def _probability_of_profit(s: np.ndarray, pnl: np.ndarray, spot: float, t: float, r: float,
                           vol: float, q: float = 0.0) -> Optional[float]:
    if t <= 0 or vol <= 0:
        return None
    # Lognormal terminal distribution centred on the forward (risk-neutral drift r - q).
    mu = math.log(spot) + (r - q - 0.5 * vol * vol) * t
    sd = vol * math.sqrt(t)
    edges = np.concatenate(([0.0], (s[1:] + s[:-1]) / 2, [np.inf]))
    with np.errstate(divide="ignore"):
        z = (np.log(edges) - mu) / sd
    cdf = norm.cdf(z)
    probs = np.diff(cdf)
    return float(probs[pnl > 0].sum())


def price_grid(spot: float, strikes: list[float], range_pct: float = 0.15,
               points: int = 401) -> np.ndarray:
    lo, hi = spot * (1 - range_pct), spot * (1 + range_pct)
    ks = [k for k in strikes if k > 0]
    if ks:
        lo, hi = min(lo, min(ks) * 0.97), max(hi, max(ks) * 1.03)
    grid = np.linspace(max(lo, 0.01), hi, points)
    return np.unique(np.concatenate((grid, [k for k in ks if lo <= k <= hi], [spot])))


def analyze(strategy: Strategy, spot: float, r: float = 0.065, now: Optional[datetime] = None,
            target: Optional[datetime] = None, iv_shift: float = 0.0, range_pct: float = 0.15,
            default_iv: float = 0.15, points: int = 401, q: float = 0.0) -> dict:
    """Full payoff analysis of ``strategy`` around the current ``spot``."""
    if not strategy.legs:
        raise ValueError("Strategy has no legs")
    now = now or datetime.now(timezone.utc)
    ivs = [_leg_iv(leg, spot, now, r, default_iv, q) for leg in strategy.legs]
    shifted = [max(v + iv_shift, 0.001) for v in ivs]

    expiries = [leg.expiry_datetime() for leg in strategy.legs if leg.option_type != "FUT"]
    expiries = [e for e in expiries if e is not None]
    first_expiry = min(expiries) if expiries else now + timedelta(days=30)
    if target is None:
        target = now + (first_expiry - now) / 2
    target = min(max(target, now), first_expiry)

    s = price_grid(spot, [leg.strike for leg in strategy.legs], range_pct, points)
    at_expiry = pnl_curve(strategy, s, first_expiry, r, ivs, q)
    today = pnl_curve(strategy, s, now, r, shifted, q)
    at_target = pnl_curve(strategy, s, target, r, shifted, q)

    # Unlimited if the expiry payoff still slopes the profitable/losing way at the grid edges.
    # Slopes are per unit of underlying; far-dated legs valued before their own expiry have
    # deltas slightly below 1, so ignore slopes smaller than 5% of the position size.
    right_slope = (at_expiry[-1] - at_expiry[-2]) / (s[-1] - s[-2])
    left_slope = (at_expiry[1] - at_expiry[0]) / (s[1] - s[0])
    eps = 0.05 * max(sum(leg.lots for leg in strategy.legs) * strategy.lot_size, 1)
    max_profit = float(at_expiry.max())
    max_loss = float(at_expiry.min())
    unlimited_profit = right_slope > eps or left_slope < -eps
    unlimited_loss = right_slope < -eps or left_slope > eps
    unlimited_profit = bool(unlimited_profit and (at_expiry[-1] == max_profit or at_expiry[0] == max_profit))
    unlimited_loss = bool(unlimited_loss and (at_expiry[-1] == max_loss or at_expiry[0] == max_loss))

    net_premium = -sum(
        leg.sign * leg.premium * leg.lots * strategy.lot_size
        for leg in strategy.legs
        if leg.option_type != "FUT"
    )
    option_ivs = [v for leg, v in zip(strategy.legs, ivs) if leg.option_type != "FUT"]
    pop_vol = float(np.mean(option_ivs)) if option_ivs else default_iv

    agg = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    for leg, iv in zip(strategy.legs, shifted):
        qty = leg.sign * leg.lots * strategy.lot_size
        if leg.option_type == "FUT":
            agg["delta"] += qty
            continue
        g = greeks(spot, leg.strike, _years(now, leg.expiry_datetime()), r, iv, leg.option_type, q)
        for k in agg:
            agg[k] += qty * getattr(g, k)

    # Position Greeks across the price range (today, shifted IV), sampled for charting.
    idx = np.unique(np.linspace(0, len(s) - 1, min(81, len(s))).astype(int))
    xs = s[idx]
    tot = {k: np.zeros_like(xs) for k in ("delta", "gamma", "theta", "vega")}
    for leg, iv in zip(strategy.legs, shifted):
        qty = leg.sign * leg.lots * strategy.lot_size
        if leg.option_type == "FUT":
            tot["delta"] += qty
            continue
        g = greeks_vec(xs, leg.strike, _years(now, leg.expiry_datetime()), r, iv, leg.option_type, q)
        for k in tot:
            tot[k] += qty * g[k]
    curve = {"prices": [round(float(x), 4) for x in xs]}
    curve.update({k: [round(float(v), 5) for v in arr] for k, arr in tot.items()})

    reward_risk = None
    if not unlimited_profit and not unlimited_loss and max_loss < 0:
        reward_risk = round(max_profit / abs(max_loss), 3)

    return {
        "strategy": strategy.to_dict(),
        "spot": spot,
        "now": now.isoformat(),
        "target": target.isoformat(),
        "expiry": first_expiry.isoformat(),
        "leg_ivs": ivs,
        "prices": [round(float(x), 4) for x in s],
        "pnl_expiry": [round(float(x), 2) for x in at_expiry],
        "pnl_today": [round(float(x), 2) for x in today],
        "pnl_target": [round(float(x), 2) for x in at_target],
        "breakevens": _breakevens(s, at_expiry),
        "max_profit": None if unlimited_profit else round(max_profit, 2),
        "max_loss": None if unlimited_loss else round(max_loss, 2),
        "unlimited_profit": unlimited_profit,
        "unlimited_loss": unlimited_loss,
        "net_premium": round(net_premium, 2),
        "premium_type": "credit" if net_premium > 0 else "debit",
        "reward_risk": reward_risk,
        "pop": _probability_of_profit(s, at_expiry, spot, _years(now, first_expiry), r, pop_vol, q),
        "greeks": {k: round(v, 4) for k, v in agg.items()},
        "current_pnl": round(float(np.interp(spot, s, today)), 2),
        "greeks_curve": curve,
    }
