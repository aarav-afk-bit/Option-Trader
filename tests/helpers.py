"""Test doubles. These exist only for the test suite; the app never uses them."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from option_trader.errors import DataUnavailableError
from option_trader.providers.base import DataProvider
from option_trader.providers.manager import ProviderManager
from option_trader.providers.nse import parse_chain_payload

FIX = Path(__file__).parent / "fixtures"
FIXTURE_EXPIRY = date(2026, 10, 27)


class RecordedNSEProvider(DataProvider):
    """Replays the recorded NSE fixture payload."""

    name = "nse"

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
        payload = json.loads((FIX / "nse_legacy.json").read_text())
        ch = parse_chain_payload(payload, symbol.upper(), expiry or FIXTURE_EXPIRY)
        ch.lot_size = 75
        return ch


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
