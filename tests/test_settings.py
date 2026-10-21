import os
import stat

import pytest
from fastapi.testclient import TestClient

from option_trader import config
from option_trader.app import create_app
from option_trader.errors import DataUnavailableError

from .helpers import RecordedNSEProvider, manager_with

KEYS = sorted(config.EDITABLE_KEYS | config.SECRET_KEYS)


@pytest.fixture
def client(monkeypatch, tmp_path):
    # Let monkeypatch restore every key save_settings may write to os.environ.
    for k in KEYS:
        monkeypatch.setenv(k, os.environ.get(k, ""))
    monkeypatch.setenv("OPTION_TRADER_PROVIDERS", "nse,angelone,yahoo")
    monkeypatch.setenv("OPTION_TRADER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "_settings", None)
    return TestClient(create_app(manager_with(RecordedNSEProvider())))


def test_save_settings_and_credentials(client, tmp_path):
    r = client.post("/api/settings", json={
        "providers": ["angelone", "nse"], "risk_free_rate": 0.07, "live_ticks": False,
        "charges": {"OPTION_TRADER_BROKERAGE_PER_ORDER": "15"},
        "angel": {"api_key": "KEY12345", "client_code": "C1234", "pin": "9999", "totp_secret": "JBSW Y3DP EHPK 3PXP"}})
    assert r.status_code == 200, r.text
    view = r.json()
    assert view["providers"] == ["angelone", "nse"] and view["risk_free_rate"] == 0.07 and view["live_ticks"] is False
    assert view["angel"]["configured"] and view["angel"]["api_key"].endswith("2345") and "KEY1" not in view["angel"]["api_key"]
    creds = tmp_path / "credentials.env"
    assert stat.S_IMODE(creds.stat().st_mode) == 0o600
    assert 'ANGEL_TOTP_SECRET="JBSWY3DPEHPK3PXP"' in creds.read_text()  # spaces removed
    assert "ANGEL" not in (tmp_path / "settings.env").read_text()
    assert [p.name for p in client.app.state.manager.providers] == ["angelone", "nse"]  # applied live


def test_blank_secret_keeps_saved_value_and_clear_removes(client, tmp_path):
    client.post("/api/settings", json={"angel": {"api_key": "K1", "client_code": "C", "pin": "1", "totp_secret": "JBSWY3DPEHPK3PXP"}})
    client.post("/api/settings", json={"angel": {"pin": "2"}})
    assert 'ANGEL_API_KEY="K1"' in (tmp_path / "credentials.env").read_text()
    v = client.post("/api/settings", json={"angel": {"clear": True}}).json()
    assert not v["angel"]["configured"]


def test_validation(client):
    assert client.post("/api/settings", json={"providers": ["bogus"]}).status_code == 422
    assert client.post("/api/settings", json={"risk_free_rate": 2}).status_code == 422
    assert client.post("/api/settings", json={"charges": {"OPTION_TRADER_GST": "-1"}}).status_code == 422


def test_remote_clients_cannot_change_settings(monkeypatch):
    remote = TestClient(create_app(manager_with(RecordedNSEProvider())), client=("203.0.113.9", 5000))
    assert remote.post("/api/settings", json={"risk_free_rate": 0.07}).status_code == 403
    assert remote.get("/api/settings").status_code == 200


def test_connection_doctor_gives_advice(client, monkeypatch):
    from option_trader.providers import manager, nse, yahoo

    monkeypatch.setattr(manager, "check_online", lambda: True)

    def blocked(self, symbol):
        raise DataUnavailableError("NSE request failed after 4 attempts: HTTP 403 (access refused)")

    monkeypatch.setattr(nse.NSEProvider, "get_expiries", blocked)
    monkeypatch.setattr(yahoo.YahooProvider, "get_history", lambda self, *a: (_ for _ in ()).throw(DataUnavailableError("Cannot reach")))
    res = {r["name"]: r for r in client.post("/api/settings/test").json()["results"]}
    assert res["Internet"]["ok"]
    assert not res["NSE"]["ok"] and "VPN" in res["NSE"]["advice"]
    assert not res["Angel One"]["ok"] and "Enter your Angel One" in res["Angel One"]["advice"]


def test_advice_is_source_aware_and_detects_proxies():
    from option_trader.api.settings import _advice

    proxy = "ProxyError('Unable to connect to proxy', OSError('Tunnel connection failed: 403 Forbidden'))"
    assert "proxy or firewall" in _advice("NSE", proxy)
    assert "proxy or firewall" in _advice("Yahoo Finance", "curl: (7) CONNECT tunnel failed, response 403")
    assert "NSE refused" in _advice("NSE", "HTTP 403 (access refused)")
    assert "NSE" not in _advice("Yahoo Finance", "HTTP 403")
