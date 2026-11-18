"""Turns live-data signals into concrete trade ideas.

1. Score direction: option-chain signals (PCR, max pain, OI range, OI build-up, IV skew)
   and technical signals on the underlying are averaged per group, then blended.
2. Judge volatility: ATM IV versus realised volatility says whether premium is rich or cheap;
   a quiet, non-trending market with cheap premium flags a possible breakout.
3. Pick the strategy families that fit the (direction, volatility) view and place each one on
   the real chain at several widths. Every candidate is analysed with the payoff engine and
   ranked on probability of profit, reward:risk, fit with the view and tail risk.
4. Return the best ideas as explicit BUY/SELL instructions priced from the live chain.

This is decision support, not financial advice.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from ..models import Candle, OptionChain
from ..strategy.engine import analyze
from ..strategy.templates import BUILTIN_BY_ID, ResolveError, resolve
from .chain import analyze_chain, atm_iv
from .signals import Signal
from .technicals import technical_snapshot

DISCLAIMER = (
    "Generated automatically from live market data using rule-based signals. Not investment "
    "advice: options can lose value quickly; check liquidity, margin and your own risk limits "
    "before trading."
)

PLAYBOOK: dict[tuple[str, str], list[str]] = {
    ("bullish", "high"): ["bull_put_spread", "jade_lizard", "short_put"],
    ("bullish", "fair"): ["bull_call_spread", "bull_put_spread", "long_call"],
    ("bullish", "low"): ["long_call", "bull_call_spread", "call_ratio_backspread"],
    ("bearish", "high"): ["bear_call_spread", "short_call"],
    ("bearish", "fair"): ["bear_put_spread", "bear_call_spread", "long_put"],
    ("bearish", "low"): ["long_put", "bear_put_spread", "put_ratio_backspread"],
    ("neutral", "high"): ["iron_condor", "iron_butterfly", "short_strangle"],
    ("neutral", "fair"): ["iron_condor", "long_call_butterfly", "long_call_calendar"],
    ("neutral", "low"): ["long_call_calendar", "long_call_butterfly", "long_put_calendar"],
    ("volatile", "low"): ["long_straddle", "long_strangle"],
    ("volatile", "fair"): ["long_strangle", "long_straddle"],
}
VIEW_THRESHOLD = 0.2


@dataclass
class MarketView:
    direction: str  # bullish / bearish / neutral / volatile
    score: float  # -1..1
    confidence: float  # 0..1
    volatility: str  # high / fair / low / unknown
    options_score: Optional[float]
    technical_score: Optional[float]
    reasons: list[str]


def _group_score(signals: list[dict]) -> Optional[float]:
    weight = sum(s["strength"] for s in signals)
    if not signals or weight == 0:
        return None
    return sum(s["score"] for s in signals) / weight


def market_view(chain_signals: list[dict], tech_signals: list[dict], vol: dict,
                trend_strength: Optional[float]) -> MarketView:
    o = _group_score(chain_signals)
    t = _group_score(tech_signals)
    parts = [(x, w) for x, w in ((o, 0.5), (t, 0.5)) if x is not None]
    score = sum(x * w for x, w in parts) / sum(w for _, w in parts) if parts else 0.0
    vol_view = vol.get("view", "unknown")
    if score > VIEW_THRESHOLD:
        direction = "bullish"
    elif score < -VIEW_THRESHOLD:
        direction = "bearish"
    else:
        direction = "neutral"
    reasons = []
    hv_pct = vol.get("hv20_percentile")
    if (direction == "neutral" and vol_view in ("low", "fair") and trend_strength is not None
            and trend_strength < 20 and hv_pct is not None and hv_pct < 25):
        direction = "volatile"
        reasons.append("Realised volatility is near a 1-year low with no trend: a breakout is likely")
    strongest = sorted(chain_signals + tech_signals, key=lambda s: abs(s["score"]), reverse=True)
    reasons += [s["detail"] for s in strongest[:5] if s["score"]]
    if vol.get("detail"):
        reasons.append(vol["detail"])
    return MarketView(direction, round(score, 3), round(min(abs(score) / 0.6, 1.0), 3), vol_view,
                      None if o is None else round(o, 3), None if t is None else round(t, 3), reasons)


def _fit(direction: str, greeks: dict, spot: float, lot_qty: float) -> float:
    """How well the position's Greeks match the view, 0..1."""
    delta = greeks["delta"] / max(lot_qty, 1)  # per lot unit
    vega = greeks["vega"]
    if direction == "bullish":
        return min(max(delta / 0.5, 0), 1)
    if direction == "bearish":
        return min(max(-delta / 0.5, 0), 1)
    if direction == "volatile":
        return 1.0 if vega > 0 else 0.0
    return max(0.0, 1 - abs(delta) / 0.25)


def _rank(result: dict, direction: str) -> float:
    pop = result["pop"] or 0.0
    rr = result["reward_risk"]
    rr_score = min(rr, 3.0) / 3.0 if rr is not None else (0.6 if result["unlimited_profit"] else 0.0)
    qty = sum(leg["lots"] for leg in result["strategy"]["legs"]) * result["strategy"]["lot_size"]
    fit = _fit(direction, result["greeks"], result["spot"], qty)
    tail_penalty = 0.25 if result["unlimited_loss"] else 0.0
    return round(0.4 * pop + 0.3 * rr_score + 0.3 * fit - tail_penalty, 4)


def _actions(chain: OptionChain, strategy_dict: dict) -> list[str]:
    lot = strategy_dict["lot_size"]
    out = []
    for leg in strategy_dict["legs"]:
        exp = leg["expiry"]
        n = leg["lots"]
        qty = (f"{n * lot} shares" if leg["option_type"] == "FUT"
               else f"{n} contract{'s' if n > 1 else ''}" + (f" (x{lot})" if lot > 1 else ""))
        if leg["option_type"] == "FUT":
            out.append(f"{leg['action']} {qty} of {chain.symbol} @ {leg['premium']:.2f}")
        else:
            kind = {"CE": "call", "PE": "put"}.get(leg["option_type"], leg["option_type"])
            out.append(f"{leg['action']} {qty} {chain.symbol} {exp} {leg['strike']:g} {kind} "
                       f"@ {leg['premium']:.2f}")
    return out


def recommend(chain: OptionChain, candles: Optional[list[Candle]], r: float, lots: int = 1,
              chain_for_offset: Optional[Callable[[int], OptionChain]] = None, top: int = 3) -> dict:
    ca = analyze_chain(chain)
    chain_sigs = [s.to_dict() for s in ca.signals]
    notes = []
    tech = None
    if candles:
        try:
            tech = technical_snapshot(candles, atm_iv(chain))
        except ValueError as exc:
            notes.append(f"Technical analysis skipped: {exc}")
    else:
        notes.append("Price history unavailable; using option-chain signals only")
    tech_sigs = tech["signals"] if tech else []
    vol = tech["volatility"] if tech else {"view": "unknown", "detail": None}
    view = market_view(chain_sigs, tech_sigs, vol, tech["trend_strength"] if tech else None)

    vol_key = view.volatility if view.volatility != "unknown" else "fair"
    families = PLAYBOOK.get((view.direction, vol_key)) or PLAYBOOK[("neutral", "fair")]

    ideas = []
    for fam in families:
        template = BUILTIN_BY_ID[fam]
        best = None
        for width in (1, 2, 3):
            try:
                strategy = resolve(template, chain, lots=lots, width=width,
                                   chain_for_offset=chain_for_offset)
            except ResolveError:
                continue
            res = analyze(strategy, chain.spot, r, q=chain.carry)
            rank = _rank(res, view.direction)
            if best is None or rank > best[0]:
                best = (rank, res, width)
        if best is None:
            continue
        rank, res, width = best
        ideas.append({
            "template_id": fam,
            "name": template.name,
            "description": template.description,
            "width": width,
            "rank": rank,
            "actions": _actions(chain, res["strategy"]),
            "legs": res["strategy"]["legs"],
            "lot_size": res["strategy"]["lot_size"],
            "net_premium": res["net_premium"],
            "premium_type": res["premium_type"],
            "max_profit": res["max_profit"],
            "max_loss": res["max_loss"],
            "unlimited_profit": res["unlimited_profit"],
            "unlimited_loss": res["unlimited_loss"],
            "breakevens": res["breakevens"],
            "pop": res["pop"],
            "reward_risk": res["reward_risk"],
            "greeks": res["greeks"],
        })
    ideas.sort(key=lambda i: i["rank"], reverse=True)

    return {
        "symbol": chain.symbol,
        "expiry": chain.expiry.isoformat(),
        "spot": chain.spot,
        "source": chain.source,
        "fetched_at": chain.fetched_at.isoformat(),
        "view": view.__dict__,
        "chain_analytics": ca.to_dict(),
        "technicals": tech,
        "ideas": ideas[:top],
        "notes": notes,
        "disclaimer": DISCLAIMER,
    }
