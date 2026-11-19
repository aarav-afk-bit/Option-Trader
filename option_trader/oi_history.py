"""Day-over-day change in open interest, from snapshots this app records.

US sources publish today's open interest (set overnight by the OCC) but not yesterday's, so the
"Chg OI" column would be empty. Each time a chain is fetched, its OI is saved per trading date
(``<data dir>/oi_history/<SYMBOL>.json``); the change is today's OI minus the most recent
earlier date recorded. Until the app has seen a symbol on two trading days the column stays
empty, and the chain notes say which date the change is measured from.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from .models import ET, OptionChain

log = logging.getLogger(__name__)
KEEP_DAYS = 10


class OIHistory:
    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir) / "oi_history"
        self._lock = threading.Lock()
        self._mem: dict[str, dict] = {}

    def _path(self, symbol: str) -> Path:
        safe = "".join(c for c in symbol.upper() if c.isalnum() or c in "._-") or "X"
        return self.dir / f"{safe}.json"

    def _load(self, symbol: str) -> dict:
        if symbol not in self._mem:
            try:
                self._mem[symbol] = json.loads(self._path(symbol).read_text())
            except (OSError, ValueError):
                self._mem[symbol] = {}
        return self._mem[symbol]

    def _save(self, symbol: str, data: dict) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self._path(symbol).with_suffix(".tmp")
            tmp.write_text(json.dumps(data, separators=(",", ":")))
            os.replace(tmp, self._path(symbol))
        except OSError as exc:
            log.warning("Could not save OI history for %s: %s", symbol, exc)

    def update(self, chain: OptionChain, today: Optional[date] = None) -> None:
        """Record today's OI and fill ``oi_change`` from the latest earlier trading date."""
        today = today or datetime.now(ET).date()
        exp = chain.expiry.isoformat()
        snap = {"CE": {}, "PE": {}}
        for row in chain.rows:
            for q in (row.call, row.put):
                if q is not None and q.oi is not None:
                    snap[q.option_type][f"{row.strike:g}"] = q.oi
        if not snap["CE"] and not snap["PE"]:
            return
        with self._lock:
            data = self._load(chain.symbol)
            days = data.setdefault(exp, {})
            prev_dates = sorted(d for d in days if d < today.isoformat())
            if prev_dates:
                ref = prev_dates[-1]
                prev = days[ref]
                filled = 0
                for row in chain.rows:
                    for q in (row.call, row.put):
                        if q is None or q.oi is None or q.oi_change is not None:
                            continue  # a source that publishes the change wins
                        old = prev.get(q.option_type, {}).get(f"{row.strike:g}")
                        if old is not None:
                            q.oi_change = q.oi - old
                            filled += 1
                if filled:
                    chain.notes = [n for n in chain.notes if not n.startswith("Chg OI:")]
                    chain.notes.append(f"Chg OI: open interest versus {ref} (recorded by this app)")
            if days.get(today.isoformat()) != snap:
                days[today.isoformat()] = snap
                for old in sorted(days)[:-KEEP_DAYS]:
                    del days[old]
                # forget expired series
                for e in [e for e in data if e < today.isoformat()]:
                    del data[e]
                self._save(chain.symbol, data)
