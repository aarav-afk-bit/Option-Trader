"""Move a strategy step by step toward a better score (beam-search hill climbing).

Starting from the legs you built, each step tries small, trader-sensible changes and keeps the
best few:

* move one leg's strike up/down by one or two listed strikes
* add or remove a lot on one leg
* shift the whole structure one strike up/down
* add a protective wing to an uncovered short leg (caps the loss)
* remove a leg
* add a short OTM option to collect premium, or a long one to add upside

Every candidate is priced from the live chain (by default paying the ask / receiving the bid)
and scored with the objective, so the search only "moves toward" strategies that are better on
today's real prices. It stops when no change improves the score.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable, Optional

from ..models import OptionChain
from ..strategy.engine import Leg, Strategy, leg_from_chain
from .distribution import Distribution
from .objective import Preferences, evaluate, score

Spec = tuple  # (option_type, action, strike, lots, expiry_iso)


def spec_of(leg: dict) -> Spec:
    exp = leg.get("expiry")
    exp = exp.isoformat() if isinstance(exp, date) else exp
    return (leg["option_type"], leg["action"], float(leg.get("strike") or 0), int(leg.get("lots") or 1), exp)


def normalize(specs: list[Spec]) -> tuple:
    """Merge duplicate contracts (net BUY vs SELL lots) into a canonical, hashable form."""
    net: dict[tuple, int] = {}
    for t, a, k, lots, e in specs:
        key = (t, k if t != "FUT" else 0.0, e)
        net[key] = net.get(key, 0) + (lots if a == "BUY" else -lots)
    out = []
    for (t, k, e), n in sorted(net.items(), key=lambda kv: (kv[0][2] or "", kv[0][0], kv[0][1])):
        if n:
            out.append((t, "BUY" if n > 0 else "SELL", k, abs(n), e))
    return tuple(out)


def describe(spec: Spec) -> str:
    t, a, k, lots, e = spec
    return f"{a} {lots}x {'underlying' if t == 'FUT' else f'{k:g} {t}'}"


@dataclass
class Node:
    specs: tuple
    strategy: Strategy
    metrics: dict
    score: float
    history: list  # list of move descriptions from the start


class Optimizer:
    def __init__(self, chain: OptionChain, chain_for_expiry: Callable[[date], OptionChain],
                 dist: Distribution, prefs: Preferences, r: float, fill: str = "market"):
        self.chain = chain
        self.chain_for_expiry = chain_for_expiry
        self.dist = dist
        self.prefs = prefs
        self.r = r
        self.fill = fill
        self.evaluated = 0
        self._cache: dict[tuple, Optional[Node]] = {}

    # -- pricing & scoring ------------------------------------------------------------------
    def _chain(self, exp: Optional[str]) -> OptionChain:
        if not exp or exp == self.chain.expiry.isoformat():
            return self.chain
        return self.chain_for_expiry(date.fromisoformat(exp))

    def build(self, specs: tuple) -> Optional[Strategy]:
        legs: list[Leg] = []
        for t, a, k, lots, e in specs:
            try:
                legs.append(leg_from_chain(self._chain(e), t, a, k, lots, self.fill))
            except ValueError:
                return None  # strike not listed or no live price: not a tradable candidate
        return Strategy("Optimised", legs, self.chain.lot_size or 1) if legs else None

    def node(self, specs: tuple, history: list) -> Optional[Node]:
        if specs in self._cache:
            n = self._cache[specs]
            return None if n is None else Node(n.specs, n.strategy, n.metrics, n.score, history)
        st = self.build(specs)
        result = None
        if st is not None and len(specs) <= self.prefs.max_legs:
            self.evaluated += 1
            m = evaluate(st, self.dist, self.chain.spot, self.r, self.chain.carry)
            sc = score(m, self.prefs)
            if sc is not None:
                result = Node(specs, st, m, sc, history)
        self._cache[specs] = result
        return result

    # -- moves --------------------------------------------------------------------------------
    def _strike_at(self, chain: OptionChain, strike: float, steps: int) -> Optional[float]:
        ks = chain.strikes
        if strike not in ks:
            return None
        i = ks.index(strike) + steps
        return ks[i] if 0 <= i < len(ks) else None

    def neighbours(self, specs: tuple) -> list[tuple[tuple, str]]:
        out = []
        legs = list(specs)
        maxl = self.prefs.max_lots_per_leg
        for i, (t, a, k, lots, e) in enumerate(legs):
            if t == "FUT":
                continue
            ch = self._chain(e)
            for step in (-2, -1, 1, 2):
                nk = self._strike_at(ch, k, step)
                if nk is not None:
                    new = legs[:i] + [(t, a, nk, lots, e)] + legs[i + 1:]
                    out.append((new, f"Move {describe(legs[i])} to {nk:g}"))
            if lots + 1 <= maxl:
                out.append((legs[:i] + [(t, a, k, lots + 1, e)] + legs[i + 1:], f"Add a lot to {describe(legs[i])}"))
            if lots > 1:
                out.append((legs[:i] + [(t, a, k, lots - 1, e)] + legs[i + 1:], f"Remove a lot from {describe(legs[i])}"))
            if len(legs) > 1:
                out.append((legs[:i] + legs[i + 1:], f"Drop {describe(legs[i])}"))
            if a == "SELL" and len(legs) < self.prefs.max_legs:
                wing = self._strike_at(ch, k, 2 if t == "CE" else -2)
                if wing is not None:
                    out.append((legs + [(t, "BUY", wing, lots, e)], f"Protect {describe(legs[i])} with a {wing:g} {t} wing"))
        # shift everything one strike
        for step in (-1, 1):
            moved = []
            for t, a, k, lots, e in legs:
                nk = k if t == "FUT" else self._strike_at(self._chain(e), k, step)
                if nk is None:
                    break
                moved.append((t, a, nk, lots, e))
            else:
                out.append((moved, f"Shift the whole strategy {'up' if step > 0 else 'down'} one strike"))
        # add a premium-collecting or upside leg
        if len(legs) < self.prefs.max_legs:
            ch, e = self.chain, self.chain.expiry.isoformat()
            atm = ch.atm_strike
            for t, steps in (("CE", 2), ("PE", -2), ("CE", 4), ("PE", -4)):
                k = self._strike_at(ch, atm, steps)
                if k is not None:
                    out.append((legs + [(t, "SELL", k, 1, e)], f"Sell a {k:g} {t} for premium"))
            for t, steps in (("CE", 1), ("PE", -1)):
                k = self._strike_at(ch, atm, steps)
                if k is not None:
                    out.append((legs + [(t, "BUY", k, 1, e)], f"Buy a {k:g} {t}"))
        return [(normalize(n), d) for n, d in out]

    # -- search -------------------------------------------------------------------------------
    def run(self, start_specs: list[Spec], max_iters: int = 12, beam: int = 4) -> dict:
        start_key = normalize(start_specs)
        start = self.node(start_key, [])
        start_info = self._summary(start_key, start)
        frontier = [start] if start else []
        if not frontier:
            # Start breaks a constraint: begin from its best valid neighbour instead.
            cands = [self.node(k, [d]) for k, d in self.neighbours(start_key)]
            frontier = sorted([c for c in cands if c], key=lambda n: n.score, reverse=True)[:beam]
        if not frontier:
            return {"start": start_info, "best": None, "steps": [], "evaluated": self.evaluated,
                    "message": "No valid strategy found near these legs under your limits"}
        best = max(frontier, key=lambda n: n.score)
        for _ in range(max_iters):
            seen, cands = set(), []
            for n in frontier:
                for key, desc in self.neighbours(n.specs):
                    if key in seen or key == n.specs:
                        continue
                    seen.add(key)
                    c = self.node(key, n.history + [desc])
                    if c:
                        cands.append(c)
            if not cands:
                break
            cands.sort(key=lambda n: n.score, reverse=True)
            if cands[0].score <= best.score + 1e-4:
                break
            best = cands[0]
            frontier = cands[:beam]
        return {
            "start": start_info,
            "best": self._summary(best.specs, best),
            "steps": self._replay(start_key, best),
            "evaluated": self.evaluated,
            "improved": start is None or best.score > start.score + 1e-4,
        }

    def _summary(self, specs: tuple, node: Optional[Node]) -> dict:
        return {
            "legs": node.strategy.to_dict()["legs"] if node else [
                {"option_type": t, "action": a, "strike": k, "lots": l, "expiry": e} for t, a, k, l, e in specs],
            "lot_size": self.chain.lot_size or 1,
            "metrics": node.metrics if node else None,
            "score": node.score if node else None,
        }

    def _replay(self, start_key: tuple, best: Node) -> list[dict]:
        """Re-trace the path so each step shows the strategy and score after that move."""
        steps = []
        cur = start_key
        for desc in best.history:
            nxt = next((key for key, d in self.neighbours(cur) if d == desc), None)
            if nxt is None:
                break
            steps.append({"move": desc, **self._summary(nxt, self._cache.get(nxt))})
            cur = nxt
        return steps
