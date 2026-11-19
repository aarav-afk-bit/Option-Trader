"""Daily 30-day implied volatility history, recorded by this app, for IV rank and percentile.

No free US source publishes per-symbol IV history, so the app keeps one value per trading day
for each symbol it sees (``<data dir>/iv_history/<SYMBOL>.json``): Cboe's published IV30 when
available, otherwise the app's own constant-maturity 30-day ATM IV. IV rank/percentile are only
reported once enough days are recorded; until then the response says how many days exist.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from .models import ET

log = logging.getLogger(__name__)
KEEP_DAYS = 400
MIN_DAYS = 20


class IVHistory:
    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir) / "iv_history"
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

    def record(self, symbol: str, iv30: float, source: str, today: Optional[date] = None) -> None:
        """Store today's IV30 (decimal). Published values beat computed ones for the same day."""
        if not iv30 or not 0.01 <= iv30 <= 5:
            return
        today = today or datetime.now(ET).date()
        key = today.isoformat()
        with self._lock:
            data = self._load(symbol)
            old = data.get(key)
            if old and old.get("source") == "published" and source != "published":
                return
            if old and abs(old["iv"] - iv30) < 1e-6 and old.get("source") == source:
                return
            data[key] = {"iv": round(iv30, 5), "source": source}
            for d in sorted(data)[:-KEEP_DAYS]:
                del data[d]
            try:
                self.dir.mkdir(parents=True, exist_ok=True)
                tmp = self._path(symbol).with_suffix(".tmp")
                tmp.write_text(json.dumps(data, separators=(",", ":")))
                os.replace(tmp, self._path(symbol))
            except OSError as exc:
                log.warning("Could not save IV history for %s: %s", symbol, exc)

    def series(self, symbol: str) -> list[tuple[str, float]]:
        with self._lock:
            return sorted((d, v["iv"]) for d, v in self._load(symbol).items())

    def rank(self, symbol: str, current: Optional[float], lookback: int = 252) -> dict:
        hist = self.series(symbol)[-lookback:]
        out = {"days": len(hist), "min_days": MIN_DAYS, "current": current, "iv_rank": None, "iv_percentile": None,
               "low": None, "high": None, "history": [{"date": d, "iv": v} for d, v in hist]}
        if current is None or len(hist) < MIN_DAYS:
            return out
        vals = [v for _, v in hist]
        lo, hi = min(vals), max(vals)
        out.update(low=lo, high=hi,
                   iv_rank=round((current - lo) / (hi - lo) * 100, 1) if hi > lo else 50.0,
                   iv_percentile=round(sum(v < current for v in vals) / len(vals) * 100, 1))
        return out
