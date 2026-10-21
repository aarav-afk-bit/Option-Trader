"""Paper positions persisted as JSON, opened, valued and closed at live chain prices.

Nothing is simulated: entry and exit prices come from the live chain at the moment of the
action, and open positions are marked to market from freshly fetched chains. If a chain can't
be fetched, the position's value is reported as unavailable rather than estimated.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from ..errors import DataUnavailableError
from ..models import OptionChain, OptionQuote


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def leg_price(q: Optional[OptionQuote], action: str, fill: str) -> Optional[float]:
    if q is None:
        return None
    if fill == "market":
        return (q.ask if action == "BUY" else q.bid) or q.price
    if fill == "mid":
        return q.mid or q.price
    return q.price


def exit_price(q: Optional[OptionQuote], action: str, fill: str) -> Optional[float]:
    """Closing a BUY leg sells it (hit the bid); closing a SELL leg buys it back (pay the ask)."""
    return leg_price(q, "SELL" if action == "BUY" else "BUY", fill)


def mark_price(q: Optional[OptionQuote]) -> Optional[float]:
    return (q.mid or q.price) if q else None


class PortfolioStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _read(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            return json.loads(self.path.read_text()).get("positions", [])
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"Portfolio file {self.path} is unreadable: {exc}") from exc

    def _write(self, positions: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".portfolio-", suffix=".json")
        with os.fdopen(fd, "w") as fh:
            json.dump({"version": 1, "positions": positions}, fh, indent=2)
        os.replace(tmp, self.path)

    def list(self) -> list[dict]:
        with self._lock:
            return self._read()

    def get(self, pid: str) -> Optional[dict]:
        return next((p for p in self.list() if p["id"] == pid), None)

    def _update(self, pid: str, fn: Callable[[dict], None]) -> dict:
        with self._lock:
            items = self._read()
            for p in items:
                if p["id"] == pid:
                    fn(p)
                    self._write(items)
                    return p
        raise KeyError(pid)

    def delete(self, pid: str) -> bool:
        with self._lock:
            items = self._read()
            keep = [p for p in items if p["id"] != pid]
            if len(keep) == len(items):
                return False
            self._write(keep)
            return True

    # -- actions ---------------------------------------------------------------------------
    def open(self, name: str, chain_for: Callable[[Optional[date]], OptionChain], legs: list[dict],
             fill: str = "ltp", notes: str = "") -> dict:
        """Open a position with every leg priced from the live chain right now."""
        base = chain_for(None)
        out_legs = []
        for leg in legs:
            otype = str(leg["option_type"]).upper()
            action = str(leg["action"]).upper()
            exp = date.fromisoformat(leg["expiry"]) if leg.get("expiry") else base.expiry
            ch = base if exp == base.expiry else chain_for(exp)
            if otype == "FUT":
                price, strike = ch.fut_price or ch.spot, 0.0
            else:
                strike = float(leg["strike"])
                price = leg_price(ch.quote(strike, otype), action, fill)  # type: ignore[arg-type]
            if not price:
                raise ValueError(f"No live price for {strike:g} {otype} {exp}")
            out_legs.append({"option_type": otype, "action": action, "strike": strike,
                             "lots": int(leg.get("lots") or 1), "expiry": exp.isoformat(),
                             "entry": float(price), "exit": None})
        from ..charges import Fill, order_charges

        lot = base.lot_size or 1
        entry_charges = order_charges(
            [Fill(l["action"], l["entry"], l["lots"] * lot, l["option_type"] != "FUT") for l in out_legs])
        pos = {
            "entry_charges": entry_charges,
            "id": uuid.uuid4().hex[:10], "name": name or "Position", "symbol": base.symbol,
            "source": base.source, "lot_size": base.lot_size or 1, "opened_at": _now(),
            "entry_spot": base.spot, "fill": fill, "status": "open", "closed_at": None,
            "notes": notes, "legs": out_legs,
        }
        with self._lock:
            items = self._read()
            items.append(pos)
            self._write(items)
        return pos

    def close(self, pid: str, chain_for: Callable[[Optional[date]], OptionChain], fill: str = "ltp") -> dict:
        pos = self.get(pid)
        if pos is None:
            raise KeyError(pid)
        if pos["status"] != "open":
            raise ValueError("Position is already closed")
        chains: dict[str, OptionChain] = {}
        exits = []
        for leg in pos["legs"]:
            exp = date.fromisoformat(leg["expiry"])
            if exp < date.today():
                raise ValueError(f"Leg expired on {exp}; delete it or record the settlement yourself")
            ch = chains.get(leg["expiry"]) or chain_for(exp)
            chains[leg["expiry"]] = ch
            if leg["option_type"] == "FUT":
                px = ch.fut_price or ch.spot
            else:
                px = exit_price(ch.quote(leg["strike"], leg["option_type"]), leg["action"], fill)
            if not px:
                raise ValueError(f"No live price to close {leg['strike']:g} {leg['option_type']}")
            exits.append(float(px))

        from ..charges import Fill, flip, order_charges

        exit_charges = order_charges([Fill(flip(l["action"]), px, l["lots"] * pos["lot_size"], l["option_type"] != "FUT")
                                      for l, px in zip(pos["legs"], exits)])

        def apply(p: dict) -> None:
            for leg, px in zip(p["legs"], exits):
                leg["exit"] = px
            p["status"] = "closed"
            p["closed_at"] = _now()
            p["realized_pnl"] = round(position_pnl(p, exits), 2)
            p["exit_charges"] = exit_charges
            p["charges_total"] = round((p.get("entry_charges") or {}).get("total", 0) + exit_charges["total"], 2)
            p["net_realized_pnl"] = round(p["realized_pnl"] - p["charges_total"], 2)

        return self._update(pid, apply)


def position_pnl(pos: dict, prices: list[Optional[float]]) -> float:
    qty = pos["lot_size"]
    total = 0.0
    for leg, px in zip(pos["legs"], prices):
        sign = 1 if leg["action"] == "BUY" else -1
        total += sign * leg["lots"] * qty * ((px or 0.0) - leg["entry"])
    return total


def mark_to_market(pos: dict, chain_for: Callable[[date], OptionChain]) -> dict:
    """Value an open position from live chains; returns the position plus live fields."""
    out = dict(pos)
    if pos["status"] != "open":
        out["pnl"] = pos.get("realized_pnl")
        out["net_pnl"] = pos.get("net_realized_pnl")
        return out
    marks: list[Optional[float]] = []
    errors = []
    chains: dict[str, OptionChain] = {}
    for leg in pos["legs"]:
        exp = date.fromisoformat(leg["expiry"])
        try:
            ch = chains.get(leg["expiry"]) or chain_for(exp)
            chains[leg["expiry"]] = ch
        except DataUnavailableError as exc:
            errors.append(f"{leg['expiry']}: {exc}")
            marks.append(None)
            continue
        if leg["option_type"] == "FUT":
            marks.append(ch.fut_price or ch.spot)
        else:
            marks.append(mark_price(ch.quote(leg["strike"], leg["option_type"])))
    out["marks"] = marks
    out["errors"] = errors
    if any(m is None for m in marks):
        out["pnl"] = None
        if not errors:
            out["errors"] = ["A leg has no live quote right now"]
    else:
        from ..charges import Fill, flip, order_charges

        out["pnl"] = round(position_pnl(pos, marks), 2)
        exit_est = order_charges([Fill(flip(l["action"]), m, l["lots"] * pos["lot_size"], l["option_type"] != "FUT")
                                  for l, m in zip(pos["legs"], marks)])
        out["charges_total"] = round((pos.get("entry_charges") or {}).get("total", 0) + exit_est["total"], 2)
        out["net_pnl"] = round(out["pnl"] - out["charges_total"], 2)
    out["spot"] = next(iter(chains.values())).spot if chains else None
    out["marked_at"] = _now()
    return out
