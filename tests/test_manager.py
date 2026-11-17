import pytest
from fastapi.testclient import TestClient

from option_trader.app import create_app
from option_trader.errors import DataUnavailableError

from .helpers import DownProvider, RecordedProvider, manager_with


def test_fallback_to_next_provider_and_cache():
    nse = RecordedProvider()
    m = manager_with(DownProvider(), nse)
    ch = m.get_chain("ndx")
    assert ch.source == "cboe"
    m.get_chain("NDX")
    assert nse.calls == 1  # cached
    m.get_chain("NDX", use_cache=False)
    assert nse.calls == 2
    st = m.status()
    yahoo = [p for p in st["providers"] if p["name"] == "yahoo"][0]
    assert yahoo["last_error"] == "down"


def test_all_down_raises_with_reasons():
    m = manager_with(DownProvider())
    with pytest.raises(DataUnavailableError, match="yahoo: down"):
        m.get_chain("NDX")


def test_offline_refuses():
    m = manager_with(RecordedProvider(), online=False)
    with pytest.raises(DataUnavailableError, match="No internet"):
        m.get_chain("NDX")


def test_explicit_source():
    m = manager_with(DownProvider(), RecordedProvider())
    with pytest.raises(DataUnavailableError):
        m.get_chain("NDX", source="yahoo")
    assert m.get_chain("NDX", source="cboe").source == "cboe"


def test_api_endpoints():
    client = TestClient(create_app(manager_with(RecordedProvider())))
    r = client.get("/api/chain", params={"symbol": "NDX"})
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "cboe" and body["atm_strike"] == 25000
    assert len(body["rows"]) == 3
    assert client.get("/api/expiries", params={"symbol": "NDX"}).json()["expiries"][0] == "2026-10-27"
    assert client.get("/api/status").json()["online"] is True
    assert client.get("/api/chain", params={"symbol": "NDX", "expiry": "bad"}).status_code == 400


def test_api_returns_503_when_no_data():
    client = TestClient(create_app(manager_with(DownProvider())))
    r = client.get("/api/chain", params={"symbol": "NDX"})
    assert r.status_code == 503
    assert "down" in r.json()["detail"]
