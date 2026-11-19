from fastapi.testclient import TestClient

from option_trader.app import create_app
from option_trader.verify import compare

from .helpers import RecordedProvider, manager_with


class Angel(RecordedProvider):
    name = "angelone"

    def __init__(self, bump=0.0, bump_strike=None):
        super().__init__()
        self.bump, self.bump_strike = bump, bump_strike

    def get_option_chain(self, symbol, expiry=None):
        ch = super().get_option_chain(symbol, expiry)
        ch.source = "angelone"
        for r in ch.rows:
            if self.bump_strike in (None, r.strike):
                for q in (r.call, r.put):
                    q.ltp += self.bump
                    if q.bid:
                        q.bid += self.bump
                        q.ask += self.bump
        return ch


def chains(bump=0.0, strike=None):
    return RecordedProvider().get_option_chain("NDX"), Angel(bump, strike).get_option_chain("NDX")


def test_identical_sources_agree():
    a, b = chains()
    out = compare(a, b)
    assert out["agree_pct"] == 100 and out["confidence"] == "high" and out["matched"] == 6


def test_small_timing_noise_within_tolerance():
    a, b = chains(0.4)  # within spreads / 1% of price
    assert compare(a, b)["agree_pct"] == 100


def test_disagreement_is_reported_and_marked():
    a, b = chains(12.0, 25000.0)
    out = compare(a, b)
    assert out["agree"] == 4 and out["confidence"] == "low"
    assert {d["strike"] for d in out["disagreements"]} == {25000.0}
    assert any("differs from ANGELONE" in i for i in a.quote(25000.0, "CE").issues)


def test_verify_endpoint():
    client = TestClient(create_app(manager_with(RecordedProvider(), Angel(12.0, 25000.0))))
    d = client.get("/api/verify", params={"symbol": "NDX"}).json()
    assert d["available"] and d["secondary"] == "angelone" and d["confidence"] == "low"
    assert client.get("/api/chain", params={"symbol": "NDX"}).json()["verification"]["confidence"] == "low"
    solo = TestClient(create_app(manager_with(RecordedProvider())))
    assert solo.get("/api/verify", params={"symbol": "NDX"}).json()["available"] is False
