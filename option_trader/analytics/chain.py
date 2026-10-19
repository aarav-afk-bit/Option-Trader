"""Analytics computed from a real option chain.

* Put-call ratio by open interest and by volume
* Max pain (strike where option holders' total payout is smallest)
* Support / resistance from the heaviest put / call open interest
* Open-interest build-up classification (long build-up, short covering, ...)
* ATM implied volatility, expected move and volatility skew
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from ..models import OptionChain, OptionQuote
from .signals import Signal


def _sum(values) -> float:
    return float(sum(v for v in values if v))


def pcr(chain: OptionChain) -> dict:
    call_oi = _sum(r.call.oi for r in chain.rows if r.call)
    put_oi = _sum(r.put.oi for r in chain.rows if r.put)
    call_vol = _sum(r.call.volume for r in chain.rows if r.call)
    put_vol = _sum(r.put.volume for r in chain.rows if r.put)
    return {
        "call_oi": call_oi,
        "put_oi": put_oi,
        "call_volume": call_vol,
        "put_volume": put_vol,
        "pcr_oi": round(put_oi / call_oi, 3) if call_oi else None,
        "pcr_volume": round(put_vol / call_vol, 3) if call_vol else None,
    }


def max_pain(chain: OptionChain) -> Optional[float]:
    best, best_cost = None, math.inf
    for settle in chain.strikes:
        cost = 0.0
        for r in chain.rows:
            if r.call and r.call.oi:
                cost += r.call.oi * max(settle - r.strike, 0)
            if r.put and r.put.oi:
                cost += r.put.oi * max(r.strike - settle, 0)
        if cost < best_cost:
            best, best_cost = settle, cost
    return best if _has_oi(chain) else None


def _has_oi(chain: OptionChain) -> bool:
    return any((r.call and r.call.oi) or (r.put and r.put.oi) for r in chain.rows)


def oi_levels(chain: OptionChain, top: int = 3) -> dict:
    calls = sorted((r for r in chain.rows if r.call and r.call.oi), key=lambda r: r.call.oi, reverse=True)
    puts = sorted((r for r in chain.rows if r.put and r.put.oi), key=lambda r: r.put.oi, reverse=True)
    call_add = sorted((r for r in chain.rows if r.call and r.call.oi_change),
                      key=lambda r: r.call.oi_change, reverse=True)
    put_add = sorted((r for r in chain.rows if r.put and r.put.oi_change),
                     key=lambda r: r.put.oi_change, reverse=True)
    return {
        "resistance": [{"strike": r.strike, "oi": r.call.oi} for r in calls[:top]],
        "support": [{"strike": r.strike, "oi": r.put.oi} for r in puts[:top]],
        "max_call_oi_addition": [{"strike": r.strike, "oi_change": r.call.oi_change} for r in call_add[:top]],
        "max_put_oi_addition": [{"strike": r.strike, "oi_change": r.put.oi_change} for r in put_add[:top]],
    }


BUILDUP_LABELS = {
    (1, 1): "long build-up",
    (-1, 1): "short build-up",
    (1, -1): "short covering",
    (-1, -1): "long unwinding",
}


def classify_buildup(q: OptionQuote) -> Optional[str]:
    if not q.change or not q.oi_change:
        return None
    return BUILDUP_LABELS[(1 if q.change > 0 else -1, 1 if q.oi_change > 0 else -1)]


def buildup(chain: OptionChain, around_atm: int = 5) -> dict:
    """OI build-up per strike near the money, plus an aggregate directional reading.

    Call long build-up / short covering and put short build-up / long unwinding lean bullish;
    the opposites lean bearish. Each strike is weighted by the size of its OI change.
    """
    ks = chain.strikes
    i = ks.index(chain.atm_strike)
    near = set(ks[max(0, i - around_atm): i + around_atm + 1])
    rows, score, weight = [], 0.0, 0.0
    call_bull = {"long build-up": 1, "short covering": 1, "short build-up": -1, "long unwinding": -1}
    for r in chain.rows:
        if r.strike not in near:
            continue
        entry = {"strike": r.strike, "call": None, "put": None}
        for side, q, sign in (("call", r.call, 1), ("put", r.put, -1)):
            label = classify_buildup(q) if q else None
            entry[side] = label
            if label:
                w = abs(q.oi_change or 0)
                score += sign * call_bull[label] * w
                weight += w
        rows.append(entry)
    return {"strikes": rows, "net": round(score / weight, 3) if weight else None}


def atm_iv(chain: OptionChain) -> Optional[float]:
    r = chain.row(chain.atm_strike)
    ivs = [q.iv for q in (r.call, r.put) if q and q.iv] if r else []
    return sum(ivs) / len(ivs) if ivs else None


def iv_skew(chain: OptionChain, steps: int = 3) -> Optional[float]:
    """OTM put IV minus OTM call IV ``steps`` strikes from ATM (vol points, decimal)."""
    ks = chain.strikes
    i = ks.index(chain.atm_strike)
    if i - steps < 0 or i + steps >= len(ks):
        steps = min(i, len(ks) - 1 - i)
    if steps <= 0:
        return None
    put = chain.quote(ks[i - steps], "PE")
    call = chain.quote(ks[i + steps], "CE")
    if not (put and put.iv and call and call.iv):
        return None
    return put.iv - call.iv


def expected_move(chain: OptionChain) -> dict:
    iv = atm_iv(chain)
    t = chain.years_to_expiry()
    r = chain.row(chain.atm_strike)
    straddle = None
    if r and r.call and r.put and r.call.price and r.put.price:
        straddle = r.call.price + r.put.price
    move = chain.spot * iv * math.sqrt(t) if iv and t > 0 else None
    return {
        "atm_iv": iv,
        "days_to_expiry": round(t * 365, 2),
        "one_sd_move": round(move, 2) if move else None,
        "straddle_price": straddle,
        "range_low": round(chain.spot - move, 2) if move else None,
        "range_high": round(chain.spot + move, 2) if move else None,
    }


@dataclass
class ChainAnalytics:
    pcr: dict
    max_pain: Optional[float]
    levels: dict
    buildup: dict
    expected_move: dict
    iv_skew: Optional[float]
    signals: list[Signal]

    def to_dict(self) -> dict:
        return {
            "pcr": self.pcr,
            "max_pain": self.max_pain,
            "levels": self.levels,
            "buildup": self.buildup,
            "expected_move": self.expected_move,
            "iv_skew": self.iv_skew,
            "signals": [s.to_dict() for s in self.signals],
        }


def chain_signals(chain: OptionChain, p: dict, mp: Optional[float], levels: dict, bu: dict,
                  skew: Optional[float]) -> list[Signal]:
    sig: list[Signal] = []
    ratio = p["pcr_oi"]
    if ratio is not None:
        if ratio > 1.6:
            sig.append(Signal("PCR (OI)", "neutral", 0.3, ratio,
                              f"PCR {ratio:.2f} is extremely high: heavy put writing but the market may be overbought"))
        elif ratio >= 1.0:
            sig.append(Signal("PCR (OI)", "bullish", min((ratio - 1.0) / 0.5, 1.0) * 0.7 + 0.2, ratio,
                              f"PCR {ratio:.2f} > 1: put writers outnumber call writers, support below"))
        elif ratio <= 0.7:
            sig.append(Signal("PCR (OI)", "bearish", min((0.7 - ratio) / 0.3, 1.0) * 0.6 + 0.3, ratio,
                              f"PCR {ratio:.2f} is low: call writing dominates, resistance overhead"))
        else:
            sig.append(Signal("PCR (OI)", "neutral", 0.2, ratio, f"PCR {ratio:.2f} is in the neutral band"))

    if mp is not None and chain.spot:
        gap = (chain.spot - mp) / chain.spot
        days = chain.years_to_expiry() * 365
        # Max-pain gravity matters mostly in the last few days before expiry.
        weight = 0.6 if days <= 3 else 0.35 if days <= 7 else 0.15
        if abs(gap) < 0.002:
            sig.append(Signal("Max pain", "neutral", weight, mp, f"Spot is at max pain {mp:g}"))
        else:
            bias = "bearish" if gap > 0 else "bullish"
            sig.append(Signal("Max pain", bias, min(abs(gap) / 0.02, 1.0) * weight, mp,
                              f"Spot {chain.spot:g} is {'above' if gap > 0 else 'below'} max pain {mp:g}; "
                              f"expiry pinning pulls toward it ({days:.1f} days left)"))

    res = levels["resistance"][0]["strike"] if levels["resistance"] else None
    sup = levels["support"][0]["strike"] if levels["support"] else None
    if res and sup and res > sup:
        pos = (chain.spot - sup) / (res - sup)
        if pos >= 0.8:
            sig.append(Signal("OI range", "bearish", 0.4, round(pos, 2),
                              f"Spot near call-OI resistance {res:g} (support {sup:g})"))
        elif pos <= 0.2:
            sig.append(Signal("OI range", "bullish", 0.4, round(pos, 2),
                              f"Spot near put-OI support {sup:g} (resistance {res:g})"))
        else:
            sig.append(Signal("OI range", "neutral", 0.3, round(pos, 2),
                              f"Spot inside OI range {sup:g}-{res:g}: range-bound setup"))
    elif res and sup:
        sig.append(Signal("OI range", "bearish" if chain.spot > sup else "bullish", 0.3, None,
                          f"Heaviest call OI ({res:g}) is below heaviest put OI ({sup:g})"))

    if bu["net"] is not None:
        n = bu["net"]
        bias = "bullish" if n > 0.15 else "bearish" if n < -0.15 else "neutral"
        sig.append(Signal("OI build-up", bias, min(abs(n), 1.0) * 0.6, n,
                          "Net open-interest build-up near the money "
                          f"({'calls bought / puts written' if n > 0 else 'calls written / puts bought'})"))

    if skew is not None:
        if skew > 0.03:
            sig.append(Signal("IV skew", "bearish", min(skew / 0.08, 1.0) * 0.4, round(skew, 4),
                              f"OTM puts {skew * 100:.1f} vol pts richer than calls: demand for downside protection"))
        elif skew < -0.01:
            sig.append(Signal("IV skew", "bullish", min(-skew / 0.04, 1.0) * 0.4, round(skew, 4),
                              f"OTM calls richer than puts by {-skew * 100:.1f} vol pts: upside demand"))
        else:
            sig.append(Signal("IV skew", "neutral", 0.1, round(skew, 4), "Normal volatility skew"))
    return sig


def analyze_chain(chain: OptionChain) -> ChainAnalytics:
    p = pcr(chain)
    mp = max_pain(chain)
    lv = oi_levels(chain)
    bu = buildup(chain)
    sk = iv_skew(chain)
    return ChainAnalytics(p, mp, lv, bu, expected_move(chain), sk, chain_signals(chain, p, mp, lv, bu, sk))
