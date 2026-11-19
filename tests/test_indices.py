import pytest
from fastapi.testclient import TestClient

from option_trader import indices
from option_trader.app import create_app
from option_trader.errors import DataUnavailableError
from option_trader.indices import fetch_from_cboe, fetch_from_yahoo, merge, overlay_ticks

from .helpers import RecordedProvider, manager_with


class Cboe:
    name = "cboe"

    def get_quote(self, sym):
        data = {"SPX": (6025.5, 6000.0), "NDX": (25012.35, 25100.0), "VIX": (15.2, 16.0)}
        if sym not in data:
            raise DataUnavailableError("no quote")
        last, prev = data[sym]
        return {"last": last, "prev_close": prev, "high": None, "low": None}


def test_cboe_rows_and_changes():
    rows = fetch_from_cboe(Cboe())
    assert [r["name"] for r in rows] == ["S&P 500", "NASDAQ 100", "VIX"]
    spx = rows[0]
    assert spx["symbol"] == "SPX" and spx["change"] == pytest.approx(25.5) and spx["pct"] == pytest.approx(0.425, abs=0.01)
    assert rows[1]["change"] < 0


class FakeTicker:
    def __init__(self, sym):
        self.sym = sym

    @property
    def fast_info(self):
        if self.sym == "^VIX":
            raise RuntimeError("nope")
        return {"last_price": 101.0, "previous_close": 100.0, "day_high": 102, "day_low": 99}


def test_yahoo_fallback_and_merge_adds_the_dow():
    y = fetch_from_yahoo(FakeTicker)
    assert len(y) == 4 and all(r["pct"] == pytest.approx(1.0) for r in y)
    merged = merge(fetch_from_cboe(Cboe()), y)
    assert [r["name"] for r in merged] == ["S&P 500", "NASDAQ 100", "DOW JONES", "RUSSELL 2000", "VIX"]
    assert merged[0]["source"] == "cboe" and merged[2]["source"] == "yahoo"


class Stream:
    def tick(self, token):
        return {"ltp": 6050.0} if token == "SPX" else None


def test_overlay_ticks_keeps_previous_close():
    rows = fetch_from_cboe(Cboe())
    overlay_ticks(rows, Stream())
    assert rows[0]["last"] == 6050.0 and rows[0]["change"] == pytest.approx(50.0) and rows[0]["source"] == "cboe+ticks"


def test_indices_endpoint(monkeypatch):
    monkeypatch.setattr(indices, "fetch_from_yahoo", lambda: fetch_from_cboe(Cboe()))
    client = TestClient(create_app(manager_with(RecordedProvider())))
    assert client.get("/api/indices").json()["indices"][0]["name"] == "S&P 500"
