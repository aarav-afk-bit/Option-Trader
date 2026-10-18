"""Screen every strategy template on the live chain and rank the results."""

from __future__ import annotations

from typing import Callable, Optional

from ..models import OptionChain
from .engine import analyze
from .templates import ResolveError, StrategyTemplate, resolve

SORT_KEYS = ("score", "pop", "reward_risk", "max_profit", "credit")


def _score(res: dict, view: str) -> float:
    pop = res["pop"] or 0.0
    rr = res["reward_risk"]
    rr_score = min(rr, 3.0) / 3.0 if rr is not None else (0.6 if res["unlimited_profit"] else 0.0)
    qty = max(sum(l["lots"] for l in res["strategy"]["legs"]) * res["strategy"]["lot_size"], 1)
    delta = res["greeks"]["delta"] / qty
    fit = {
        "bullish": min(max(delta / 0.5, 0), 1),
        "bearish": min(max(-delta / 0.5, 0), 1),
        "volatile": 1.0 if res["greeks"]["vega"] > 0 else 0.0,
        "neutral": max(0.0, 1 - abs(delta) / 0.25),
    }.get(view, 0.5)
    return round(0.4 * pop + 0.3 * rr_score + 0.3 * fit - (0.25 if res["unlimited_loss"] else 0), 4)


def screen(chain: OptionChain, templates: list[StrategyTemplate], r: float, view: str = "any",
           lots: int = 1, max_loss: Optional[float] = None, min_pop: float = 0.0,
           allow_unlimited_loss: bool = False, sort: str = "score", widths=(1, 2, 3),
           chain_for_offset: Optional[Callable[[int], OptionChain]] = None, top: int = 25) -> dict:
    results, skipped = [], 0
    for t in templates:
        if view != "any" and t.view not in (view, "any"):
            continue
        for w in widths:
            if w > 1 and all(leg.offset == 0 for leg in t.legs):
                continue  # width has no effect on single-strike structures
            try:
                st = resolve(t, chain, lots=lots, width=w, chain_for_offset=chain_for_offset)
            except ResolveError:
                skipped += 1
                continue
            res = analyze(st, chain.spot, r)
            if res["unlimited_loss"] and not allow_unlimited_loss:
                continue
            if max_loss is not None and res["max_loss"] is not None and -res["max_loss"] > max_loss:
                continue
            if (res["pop"] or 0) < min_pop:
                continue
            results.append({
                "template_id": t.id, "name": t.name, "view": t.view, "builtin": t.builtin, "width": w,
                "legs": res["strategy"]["legs"], "lot_size": res["strategy"]["lot_size"],
                "net_premium": res["net_premium"], "premium_type": res["premium_type"],
                "max_profit": res["max_profit"], "max_loss": res["max_loss"],
                "unlimited_profit": res["unlimited_profit"], "unlimited_loss": res["unlimited_loss"],
                "breakevens": res["breakevens"], "pop": res["pop"], "reward_risk": res["reward_risk"],
                "greeks": res["greeks"], "score": _score(res, view),
            })
    key = {
        "score": lambda x: x["score"],
        "pop": lambda x: x["pop"] or 0,
        "reward_risk": lambda x: x["reward_risk"] if x["reward_risk"] is not None else (99 if x["unlimited_profit"] else -1),
        "max_profit": lambda x: x["max_profit"] if x["max_profit"] is not None else float("inf"),
        "credit": lambda x: x["net_premium"],
    }[sort if sort in SORT_KEYS else "score"]
    results.sort(key=key, reverse=True)
    return {"results": results[:top], "matched": len(results), "skipped": skipped}
