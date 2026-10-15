"""Persistent storage for user-defined strategies (a JSON file in the data directory)."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Optional

from ..models import OptionChain
from .templates import BUILTIN_BY_ID, StrategyTemplate, TemplateLeg, slugify


class StrategyStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _read(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"Saved strategies file {self.path} is unreadable: {exc}") from exc
        return {d["id"]: d for d in data.get("strategies", [])}

    def _write(self, items: dict[str, dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".strategies-", suffix=".json")
        with os.fdopen(fd, "w") as fh:
            json.dump({"version": 1, "strategies": list(items.values())}, fh, indent=2)
        os.replace(tmp, self.path)  # atomic: never leaves a half-written file

    def list(self) -> list[StrategyTemplate]:
        with self._lock:
            return [StrategyTemplate.from_dict(d) for d in self._read().values()]

    def get(self, strategy_id: str) -> Optional[StrategyTemplate]:
        with self._lock:
            d = self._read().get(strategy_id)
        return StrategyTemplate.from_dict(d) if d else None

    def save(self, template: StrategyTemplate, overwrite: bool = True) -> StrategyTemplate:
        template.builtin = False
        base = slugify(template.id or template.name)
        if base in BUILTIN_BY_ID:
            base = f"my_{base}"
        with self._lock:
            items = self._read()
            sid = base
            if not overwrite:
                n = 2
                while sid in items:
                    sid = f"{base}_{n}"
                    n += 1
            template.id = sid
            items[sid] = template.to_dict()
            self._write(items)
        return template

    def delete(self, strategy_id: str) -> bool:
        with self._lock:
            items = self._read()
            if strategy_id not in items:
                return False
            del items[strategy_id]
            self._write(items)
            return True


def template_from_legs(name: str, chain: OptionChain, legs: list[dict], view: str = "any",
                       description: str = "") -> StrategyTemplate:
    """Convert concrete legs (absolute strikes on ``chain``) into a reusable template.

    Strikes become offsets from the chain's current ATM strike, so the saved strategy can be
    placed on any later chain, symbol or expiry. Legs on later expiries keep their expiry
    position relative to ``chain.expiry``.
    """
    strikes = chain.strikes
    atm_idx = strikes.index(chain.atm_strike)
    later = [e.isoformat() for e in chain.expiries if e > chain.expiry]
    out = []
    for leg in legs:
        otype = str(leg["option_type"]).upper()
        offset = 0
        if otype != "FUT":
            strike = float(leg["strike"])
            if strike not in strikes:
                raise ValueError(f"Strike {strike:g} is not listed on this chain")
            offset = strikes.index(strike) - atm_idx
        exp_off = 0
        exp = leg.get("expiry")
        if exp and exp != chain.expiry.isoformat():
            if exp not in later:
                raise ValueError(f"Expiry {exp} is not after {chain.expiry}")
            exp_off = later.index(exp) + 1
        out.append(TemplateLeg(otype, str(leg["action"]), offset, int(leg.get("lots") or 1), exp_off))
    return StrategyTemplate(slugify(name), name, out, view, description)
