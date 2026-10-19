from fastapi.testclient import TestClient

from option_trader.app import create_app

from .helpers import RecordedNSEProvider, manager_with


def test_term_structure_uses_live_chains_per_expiry():
    nse = RecordedNSEProvider()
    client = TestClient(create_app(manager_with(nse)))
    d = client.get("/api/analytics/term-structure", params={"symbol": "NIFTY"}).json()
    assert [p["expiry"] for p in d["points"]] == ["2026-10-27", "2026-11-03"]
    assert d["points"][0]["atm_iv"] is not None
    assert d["source"] == "nse"


def test_intraday_records_each_fresh_fetch():
    m = manager_with(RecordedNSEProvider())
    client = TestClient(create_app(m))
    client.get("/api/chain", params={"symbol": "NIFTY"})
    client.get("/api/chain", params={"symbol": "NIFTY"})  # cached: not recorded again
    client.get("/api/chain", params={"symbol": "NIFTY", "refresh": "true"})
    d = client.get("/api/analytics/intraday", params={"symbol": "NIFTY", "expiry": "2026-10-27"}).json()
    assert len(d["points"]) == 2
    p = d["points"][0]
    assert p["spot"] == 25012.35 and p["pcr_oi"] and p["source"] == "nse"
