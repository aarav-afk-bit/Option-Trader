import os
import stat

import pytest
from fastapi.testclient import TestClient

from option_trader import config
from option_trader.app import create_app
from option_trader.errors import DataUnavailableError

from .helpers import RecordedProvider, manager_with

KEYS = sorted(config.EDITABLE_KEYS | config.SECRET_KEYS)


@pytest.fixture
def client(monkeypatch, tmp_path):
    # Let monkeypatch restore every key save_settings may write to os.environ.
    for k in KEYS:
        monkeypatch.setenv(k, os.environ.get(k, ""))
    monkeypatch.setenv("OPTION_TRADER_PROVIDERS", "tradier,cboe,yahoo")
    monkeypatch.setenv("OPTION_TRADER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "_settings", None)
    return TestClient(create_app(manager_with(RecordedProvider())))


def test_save_settings_and_credentials(client, tmp_path):
    r = client.post("/api/settings", json={
        "providers": ["cboe", "yahoo"], "risk_free_rate": 0.045, "live_ticks": False,
        "tradier": {"token": "  TOKEN1234567  ", "sandbox": True}})
    assert r.status_code == 200, r.text
    view = r.json()
    assert view["providers"] == ["cboe", "yahoo"] and view["risk_free_rate"] == 0.045 and view["live_ticks"] is False
    t = view["tradier"]
    assert t["configured"] and t["sandbox"] and t["token"].endswith("4567") and "TOKEN1" not in t["token"]
    creds = tmp_path / "credentials.env"
    assert stat.S_IMODE(creds.stat().st_mode) == 0o600
    assert 'TRADIER_TOKEN="TOKEN1234567"' in creds.read_text()
    assert "TRADIER_TOKEN" not in (tmp_path / "settings.env").read_text()
    assert 'TRADIER_SANDBOX="1"' in (tmp_path / "settings.env").read_text()
    assert [p.name for p in client.app.state.manager.providers] == ["cboe", "yahoo"]  # applied live


def test_blank_token_keeps_saved_value_and_clear_removes(client, tmp_path):
    client.post("/api/settings", json={"tradier": {"token": "K1"}})
    client.post("/api/settings", json={"tradier": {"token": "", "sandbox": False}})
    assert 'TRADIER_TOKEN="K1"' in (tmp_path / "credentials.env").read_text()
    v = client.post("/api/settings", json={"tradier": {"clear": True}}).json()
    assert not v["tradier"]["configured"]


def test_validation(client):
    assert client.post("/api/settings", json={"providers": ["bogus"]}).status_code == 422
    assert client.post("/api/settings", json={"risk_free_rate": 2}).status_code == 422
    assert client.post("/api/settings", json={"charges": {"OPTION_TRADER_ORF": "-1"}}).status_code == 422
    assert client.post("/api/settings", json={"charges": {"OPTION_TRADER_GST": "1"}}).status_code == 422  # not a US fee


def test_remote_clients_cannot_change_settings(monkeypatch):
    remote = TestClient(create_app(manager_with(RecordedProvider())), client=("203.0.113.9", 5000))
    assert remote.post("/api/settings", json={"risk_free_rate": 0.07}).status_code == 403
    assert remote.get("/api/settings").status_code == 200


def test_connection_doctor_gives_advice(client, monkeypatch):
    from option_trader.providers import cboe, manager, yahoo

    monkeypatch.setattr(manager, "check_online", lambda: True)
    monkeypatch.setattr(cboe.CboeProvider, "get_expiries", lambda self, s: (_ for _ in ()).throw(
        DataUnavailableError("Cannot reach Cboe: ProxyError('Unable to connect to proxy')")))
    monkeypatch.setattr(yahoo.YahooProvider, "get_history", lambda self, *a: (_ for _ in ()).throw(DataUnavailableError("Cannot reach")))
    res = {r["name"]: r for r in client.post("/api/settings/test").json()["results"]}
    assert res["Internet"]["ok"]
    assert not res["Cboe"]["ok"] and "proxy or firewall" in res["Cboe"]["advice"]
    assert not res["Tradier"]["ok"] and "Tradier API token" in res["Tradier"]["advice"]
    assert not res["Yahoo Finance"]["ok"]


def test_advice_is_source_aware_and_detects_proxies():
    from option_trader.api.settings import _advice

    assert "proxy or firewall" in _advice("Cboe", "Tunnel connection failed: 403 Forbidden")
    assert "rejected the token" in _advice("Tradier", "Tradier rejected the token (HTTP 401)")
    assert "Tradier" not in _advice("Yahoo Finance", "HTTP 403")


def test_fee_settings_round_trip(client, tmp_path):
    v = client.post("/api/settings", json={"charges": {"OPTION_TRADER_COMMISSION_PER_CONTRACT": "0.5"}}).json()
    assert v["charges"]["OPTION_TRADER_COMMISSION_PER_CONTRACT"] == "0.5"
    assert v["fee_defaults"]["OPTION_TRADER_ORF"] == 0.0229
    from option_trader.charges import ChargeRates

    assert ChargeRates().commission_per_contract == 0.5
