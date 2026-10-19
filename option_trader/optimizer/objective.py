"""Score a strategy: win rate, expected P&L and risk under a distribution, net of costs.

``score`` blends the two things a trader asks for, controlled by ``win_weight`` (0..1):

* win rate: probability the position is profitable at the near expiry after charges
* edge: expected P&L per rupee of risk, squashed to 0..1

Strategies with a negative expectation are scaled down, so a high win rate bought with rare
large losses does not win. Hard constraints (max loss, unlimited loss) reject a strategy outright.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Optional

import numpy as np

from ..charges import ChargeRates, strategy_costs
from ..strategy.engine import Strategy, _leg_iv, pnl_curve
from .distribution import Distribution


@dataclass
class Preferences:
    win_weight: float = 0.5  # 1 = only win rate matters, 0 = only expected return on risk
    max_loss: Optional[float] = None  # largest acceptable loss in rupees (positive)
    allow_unlimited_loss: bool = False
    min_win_rate: float = 0.0
    max_legs: int = 4
    max_lots_per_leg: int = 5

    def clamp(self) -> "Preferences":
        self.win_weight = min(max(self.win_weight, 0.0), 1.0)
        self.max_legs = min(max(self.max_legs, 1), 6)
        self.max_lots_per_leg = min(max(self.max_lots_per_leg, 1), 20)
        return self


def _first_expiry(strategy: Strategy, now: datetime) -> datetime:
    exps = [l.expiry_datetime() for l in strategy.legs if l.option_type != "FUT" and l.expiry_datetime()]
    return min(exps) if exps else now


def evaluate(strategy: Strategy, dist: Distribution, spot: float, r: float, q: float = 0.0,
             now: Optional[datetime] = None, rates: Optional[ChargeRates] = None) -> dict:
    """Metrics of ``strategy`` at its nearest expiry under ``dist`` (all P&L net of charges)."""
    now = now or datetime.now(timezone.utc)
    first = _first_expiry(strategy, now)
    # Legs expiring at the evaluation date are worth intrinsic value: IV only matters for later legs.
    ivs = [_leg_iv(leg, spot, now, r, 0.15, q) if (leg.expiry_datetime() or first) > first else 0.15
           for leg in strategy.legs]
    s = dist.prices
    gross = pnl_curve(strategy, s, first, r, ivs, q)
    costs = strategy_costs(strategy.to_dict()["legs"], strategy.lot_size, rates)
    pnl = gross - costs["round_trip"]
    p = dist.probs

    qty = max(sum(l.lots for l in strategy.legs) * strategy.lot_size, 1)
    left = (pnl[1] - pnl[0]) / (s[1] - s[0])
    right = (pnl[-1] - pnl[-2]) / (s[-1] - s[-2])
    unlimited_loss = bool(right < -0.05 * qty or left > 0.05 * qty)
    unlimited_profit = bool(right > 0.05 * qty or left < -0.05 * qty)
    # Loss measured over the distribution's range; for open-ended losses use the 1-in-1000 tails.
    lo_i, hi_i = np.searchsorted(np.cumsum(p), [0.0005, 0.9995])
    window = pnl[lo_i: hi_i + 1] if unlimited_loss else pnl
    worst = float(window.min())
    risk = max(-worst, 1.0)

    win = float(p[pnl > 0].sum())
    ev = float((p * pnl).sum())
    wins = pnl > 0
    avg_win = float((p[wins] * pnl[wins]).sum() / p[wins].sum()) if wins.any() else 0.0
    losses = ~wins
    avg_loss = float((p[losses] * pnl[losses]).sum() / p[losses].sum()) if losses.any() else 0.0
    return {
        "win_rate": round(win, 4),
        "expected_pnl": round(ev, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "max_profit": round(float(pnl.max()), 2),
        "max_loss": round(worst, 2),
        "unlimited_loss": unlimited_loss,
        "unlimited_profit": unlimited_profit,
        "risk": round(risk, 2),
        "edge": round(ev / risk, 4),
        "charges": costs["round_trip"],
        "net_premium": round(-sum(l.sign * l.premium * l.lots * strategy.lot_size
                                  for l in strategy.legs if l.option_type != "FUT"), 2),
    }


def score(metrics: dict, prefs: Preferences) -> Optional[float]:
    """Blend of win rate and edge; None if the strategy breaks a hard constraint."""
    if metrics["unlimited_loss"] and not prefs.allow_unlimited_loss:
        return None
    if prefs.max_loss is not None and metrics["risk"] > prefs.max_loss:
        return None
    if metrics["win_rate"] < prefs.min_win_rate:
        return None
    edge_score = 0.5 + 0.5 * math.tanh(8.0 * metrics["edge"])
    s = prefs.win_weight * metrics["win_rate"] + (1 - prefs.win_weight) * edge_score
    if metrics["edge"] < 0:
        # A high win rate with a negative expectation (small wins, big losses) is not a good
        # trade: scale the score down in proportion to the expected loss per rupee at risk.
        s *= max(0.1, 1 + 4 * metrics["edge"])
    return round(s, 5)


def prefs_from_dict(d: Optional[dict]) -> Preferences:
    d = d or {}
    allowed = {k: v for k, v in d.items() if k in Preferences.__dataclass_fields__ and v is not None}
    return Preferences(**allowed).clamp()


def prefs_to_dict(p: Preferences) -> dict:
    return asdict(p)
