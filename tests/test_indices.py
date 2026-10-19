import pytest
from fastapi.testclient import TestClient

from option_trader import indices
from option_trader.app import create_app
from option_trader.indices import fetch_from_yahoo, overlay_ticks, parse_all_indices

from .helpers import RecordedNSEProvider, manager_with

NSE_PAYLOAD = {
    "timestamp": "16-Oct-2026 15:30",
    "data": [
        {"index": "NIFTY 50", "last": 25100.5, "previousClose": 25000.0, "high": 25150, "low": 24980},
        {"index": "NIFTY BANK", "last": 55800.0, "previousClose": 56000.0},
        {"index": "INDIA VIX", "last": 12.4, "previousClose": 13.1},
        {"index": "NIFTY IT", "last": 1, "previousClose": 1},
    ],
}


def test_parse_all_indices():
    rows = parse_all_indices(NSE_PAYLOAD)
    assert [r["name"] for r in rows] == ["NIFTY 50", "NIFTY BANK", "INDIA VIX"]
    n = rows[0]
    assert n["symbol"] == "NIFTY" and n["change"] == pytest.approx(100.5) and n["pct"] == pytest.approx(0.4, abs=0.01)
    assert rows[1]["change"] < 0 and rows[2]["symbol"] is None


class FakeTicker:
    def __init__(self, sym):
        self.sym = sym

    @property
    def fast_info(self):
        if self.sym == "^INDIAVIX":
            raise RuntimeError("nope")
        return {"last_price": 101.0, "previous_close": 100.0, "day_high": 102, "day_low": 99}


def test_yahoo_fallback_skips_failures():
    rows = fetch_from_yahoo(FakeTicker)
    assert len(rows) == 4 and all(r["pct"] == pytest.approx(1.0) for r in rows)


class Stream:
    def tick(self, token):
        return {"ltp": 25200.0, "high": 25210} if token == "99926000" else None


def test_overlay_ticks_keeps_previous_close():
    rows = parse_all_indices(NSE_PAYLOAD)
    overlay_ticks(rows, Stream())
    assert rows[0]["last"] == 25200.0 and rows[0]["change"] == pytest.approx(200.0)
    assert rows[0]["source"] == "nse+ticks" and rows[1]["last"] == 55800.0


def test_indices_endpoint_uses_fallback(monkeypatch):
    monkeypatch.setattr(indices, "fetch_from_yahoo", lambda: parse_all_indices(NSE_PAYLOAD))
    client = TestClient(create_app(manager_with(RecordedNSEProvider())))
    d = client.get("/api/indices").json()
    assert d["indices"][0]["name"] == "NIFTY 50"
