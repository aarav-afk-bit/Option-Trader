"""Test doubles. These exist only for the test suite; the app never uses them."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from option_trader.errors import DataUnavailableError
from option_trader.providers.base import DataProvider
from option_trader.providers.manager import ProviderManager
from option_trader.models import CONTRACT_MULTIPLIER, OptionChain, OptionQuote, StrikeRow, utcnow

FIX = Path(__file__).parent / "fixtures"
FIXTURE_EXPIRY = date(2026, 10, 27)


def recorded_chain(symbol: str = "NDX", expiry: date | None = None, source: str = "cboe") -> OptionChain:
    """The recorded chain fixture as an OptionChain (test-only)."""
    data = json.loads((FIX / "recorded_chain.json").read_text())
    expiry = expiry or FIXTURE_EXPIRY
    rows = []
    for r in data["rows"]:
        if r["expiry"] != expiry.isoformat():
            continue
        k = float(r["strike"])
        mk = lambda q, t: OptionQuote(k, t, expiry, **{f: v for f, v in q.items() if v is not None})
        rows.append(StrikeRow(k, mk(r["call"], "CE"), mk(r["put"], "PE")))
    ch = OptionChain(symbol.upper(), expiry, data["spot"], rows, source, utcnow(),
                     exchange_timestamp=data["exchange_timestamp"], lot_size=CONTRACT_MULTIPLIER,
                     expiries=[date.fromisoformat(e) for e in data["expiries"]])
    return ch.validate()


class RecordedProvider(DataProvider):
    """Replays the recorded chain fixture."""

    name = "cboe"

    def __init__(self, candles=None):
        self.calls = 0
        self.candles = candles

    def get_history(self, symbol, period="1y", interval="1d"):
        if self.candles is None:
            raise NotImplementedError
        return self.candles

    def get_expiries(self, symbol):
        return [FIXTURE_EXPIRY, date(2026, 11, 3)]

    def get_option_chain(self, symbol, expiry=None):
        self.calls += 1
        return recorded_chain(symbol, expiry, self.name)


def trending_candles(n=260, start=100.0, drift=0.002, seed=7):
    """Deterministic OHLC series for indicator unit tests (test-only, never used by the app)."""
    import math
    import random
    from datetime import datetime, timedelta, timezone

    from option_trader.models import Candle

    rnd = random.Random(seed)
    out, px = [], start
    t0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
    for i in range(n):
        o = px
        px = px * math.exp(drift + rnd.gauss(0, 0.01))
        hi, lo = max(o, px) * 1.004, min(o, px) * 0.996
        out.append(Candle(t0 + timedelta(days=i), o, hi, lo, px, 1000))
    return out


class DownProvider(DataProvider):
    name = "yahoo"

    def get_expiries(self, symbol):
        raise DataUnavailableError("down")

    def get_option_chain(self, symbol, expiry=None):
        raise DataUnavailableError("down")


def manager_with(*providers, online=True) -> ProviderManager:
    return ProviderManager(list(providers), cache_ttl=30, online_check=lambda: online)
