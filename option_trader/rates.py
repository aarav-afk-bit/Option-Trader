"""Risk-free rates from the US Treasury's daily par yield curve.

The Treasury publishes the Daily Treasury Par Yield Curve Rates (1-month to 30-year constant
maturity yields) as a public CSV. Option pricing needs a continuously-compounded rate for the
option's own life, so the curve is interpolated at the time to expiry and converted from the
bond-equivalent (semi-annual) yield: r = 2 * ln(1 + y / 2).

The curve is fetched at most once a day (cached in the data directory). If it cannot be
fetched, the configured OPTION_TRADER_RISK_FREE_RATE is used and labelled as such.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import math
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import requests

from .errors import DataUnavailableError

log = logging.getLogger(__name__)

CSV_URL = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/"
           "{year}/all?type=daily_treasury_yield_curve&field_tdr_date_value={year}&page&_format=csv")
TENORS = {"1 Mo": 1 / 12, "1.5 Month": 1.5 / 12, "2 Mo": 2 / 12, "3 Mo": 3 / 12, "4 Mo": 4 / 12, "6 Mo": 6 / 12,
          "1 Yr": 1.0, "2 Yr": 2.0, "3 Yr": 3.0, "5 Yr": 5.0, "7 Yr": 7.0, "10 Yr": 10.0, "20 Yr": 20.0, "30 Yr": 30.0}


@dataclass
class YieldCurve:
    as_of: date
    points: list[tuple[float, float]]  # (years, yield as a decimal, bond-equivalent)
    source: str = "us-treasury"

    def yield_at(self, years: float) -> float:
        pts = self.points
        if years <= pts[0][0]:
            return pts[0][1]
        for (t0, y0), (t1, y1) in zip(pts, pts[1:]):
            if years <= t1:
                return y0 + (y1 - y0) * (years - t0) / (t1 - t0)
        return pts[-1][1]

    def rate(self, years: float) -> float:
        """Continuously-compounded risk-free rate for an option with ``years`` to expiry."""
        y = self.yield_at(max(years, 0.0))
        return 2 * math.log(1 + y / 2)

    def to_dict(self) -> dict:
        return {"as_of": self.as_of.isoformat(), "source": self.source,
                "points": [{"years": round(t, 4), "yield_pct": round(y * 100, 3)} for t, y in self.points]}


def parse_curve_csv(text: str) -> Optional[YieldCurve]:
    """Latest row of the Treasury CSV (rows are newest first, but sort to be safe)."""
    rows = list(csv.DictReader(io.StringIO(text)))
    best = None
    for r in rows:
        try:
            d = datetime.strptime(r.get("Date", "").strip(), "%m/%d/%Y").date()
        except ValueError:
            continue
        pts = []
        for col, t in TENORS.items():
            v = (r.get(col) or "").strip()
            if v and v.upper() != "N/A":
                try:
                    pts.append((t, float(v) / 100))
                except ValueError:
                    continue
        if len(pts) >= 3 and (best is None or d > best.as_of):
            best = YieldCurve(d, sorted(pts))
    return best


class TreasuryCurve:
    """Fetches and caches the latest curve; never raises to callers that just want a rate."""

    def __init__(self, data_dir: Path, fallback_rate: float, session: Optional[requests.Session] = None,
                 timeout: float = 15.0):
        self.cache_file = Path(data_dir) / "treasury_curve.json"
        self.fallback_rate = fallback_rate
        self._http = session or requests.Session()
        self._timeout = timeout
        self._curve: Optional[YieldCurve] = None
        self._checked_day: Optional[date] = None
        self._failed_at = 0.0
        self.last_error: Optional[str] = None
        self._lock = threading.Lock()

    def _load_cache(self) -> Optional[YieldCurve]:
        try:
            d = json.loads(self.cache_file.read_text())
            return YieldCurve(date.fromisoformat(d["as_of"]), [tuple(p) for p in d["points"]], d.get("source", "us-treasury"))
        except (OSError, ValueError, KeyError):
            return None

    def _save_cache(self, c: YieldCurve) -> None:
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            self.cache_file.write_text(json.dumps({"as_of": c.as_of.isoformat(), "points": c.points, "source": c.source,
                                                   "fetched": date.today().isoformat()}))
        except OSError as exc:
            log.info("Could not cache the Treasury curve: %s", exc)

    def fetch(self, today: Optional[date] = None) -> YieldCurve:
        today = today or date.today()
        last_exc: Optional[Exception] = None
        for year in (today.year, today.year - 1):  # early January has no rows for the new year yet
            try:
                resp = self._http.get(CSV_URL.format(year=year), timeout=self._timeout)
                resp.raise_for_status()
                c = parse_curve_csv(resp.text)
                if c:
                    return c
            except requests.RequestException as exc:
                last_exc = exc
        raise DataUnavailableError(f"Treasury yield curve unavailable: {last_exc or 'no rows'}")

    def curve(self) -> Optional[YieldCurve]:
        today = date.today()
        with self._lock:
            if self._curve is not None and self._checked_day == today:
                return self._curve
            if self._curve is None:
                self._curve = self._load_cache()
            fetched_today = False
            try:
                fetched_today = json.loads(self.cache_file.read_text()).get("fetched") == today.isoformat()
            except (OSError, ValueError):
                pass
            if not fetched_today and time.time() - self._failed_at > 3600:
                try:
                    self._curve = self.fetch(today)
                    self._save_cache(self._curve)
                    self.last_error = None
                except DataUnavailableError as exc:
                    self._failed_at = time.time()
                    self.last_error = str(exc)
                    log.info("%s; using %s", exc, "cached curve" if self._curve else "the fallback rate")
            self._checked_day = today if self._curve is not None else None
            return self._curve

    def rate(self, years: float) -> tuple[float, str]:
        """(continuous rate, human label) for an option with ``years`` to expiry."""
        c = self.curve()
        if c is None:
            return self.fallback_rate, f"fallback {self.fallback_rate * 100:.2f}% (Treasury curve unavailable)"
        return c.rate(years), f"US Treasury curve {c.as_of.isoformat()}"
