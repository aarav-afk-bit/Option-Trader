import pytest
from fastapi.testclient import TestClient

from option_trader.app import create_app
from option_trader.errors import DataUnavailableError

from .helpers import DownProvider, RecordedNSEProvider, manager_with


def test_fallback_to_next_provider_and_cache():
    nse = RecordedNSEProvider()
    m = manager_with(DownProvider(), nse)
    ch = m.get_chain("nifty")
    assert ch.source == "nse"
    m.get_chain("NIFTY")
    assert nse.calls == 1  # cached
    m.get_chain("NIFTY", use_cache=False)
    assert nse.calls == 2
    st = m.status()
    yahoo = [p for p in st["providers"] if p["name"] == "yahoo"][0]
    assert yahoo["last_error"] == "down"


def test_all_down_raises_with_reasons():
    m = manager_with(DownProvider())
    with pytest.raises(DataUnavailableError, match="yahoo: down"):
        m.get_chain("NIFTY")


def test_offline_refuses():
    m = manager_with(RecordedNSEProvider(), online=False)
    with pytest.raises(DataUnavailableError, match="No internet"):
        m.get_chain("NIFTY")


def test_explicit_source():
    m = manager_with(DownProvider(), RecordedNSEProvider())
    with pytest.raises(DataUnavailableError):
        m.get_chain("NIFTY", source="yahoo")
    assert m.get_chain("NIFTY", source="nse").source == "nse"


def test_api_endpoints():
    client = TestClient(create_app(manager_with(RecordedNSEProvider())))
    r = client.get("/api/chain", params={"symbol": "NIFTY"})
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "nse" and body["atm_strike"] == 25000
    assert len(body["rows"]) == 3
    assert client.get("/api/expiries", params={"symbol": "NIFTY"}).json()["expiries"][0] == "2026-10-27"
    assert client.get("/api/status").json()["online"] is True
    assert client.get("/api/chain", params={"symbol": "NIFTY", "expiry": "bad"}).status_code == 400


def test_api_returns_503_when_no_data():
    client = TestClient(create_app(manager_with(DownProvider())))
    r = client.get("/api/chain", params={"symbol": "NIFTY"})
    assert r.status_code == 503
    assert "down" in r.json()["detail"]
