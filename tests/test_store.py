import json

import pytest
from fastapi.testclient import TestClient

from option_trader.app import create_app
from option_trader.strategy.store import StrategyStore, template_from_legs
from option_trader.strategy.templates import StrategyTemplate, TemplateLeg

from .helpers import RecordedNSEProvider, manager_with


def tpl(name="My Spread"):
    return StrategyTemplate("", name, [TemplateLeg("CE", "BUY", 0), TemplateLeg("CE", "SELL", 1)], "bullish")


def test_save_list_get_delete(tmp_path):
    store = StrategyStore(tmp_path / "s.json")
    saved = store.save(tpl())
    assert saved.id == "my_spread" and not saved.builtin
    assert [t.id for t in store.list()] == ["my_spread"]
    assert store.get("my_spread").legs[1].offset == 1
    assert json.loads((tmp_path / "s.json").read_text())["version"] == 1
    assert store.delete("my_spread") and not store.delete("my_spread")
    assert store.list() == []


def test_no_overwrite_and_builtin_clash(tmp_path):
    store = StrategyStore(tmp_path / "s.json")
    store.save(tpl())
    assert store.save(tpl(), overwrite=False).id == "my_spread_2"
    assert store.save(tpl("Iron Condor")).id == "my_iron_condor"


def test_template_from_legs():
    ch = RecordedNSEProvider().get_option_chain("NIFTY")
    t = template_from_legs("Hedge", ch, [
        {"option_type": "PE", "action": "BUY", "strike": 24900, "lots": 2},
        {"option_type": "CE", "action": "SELL", "strike": 25100},
        {"option_type": "CE", "action": "BUY", "strike": 25000, "expiry": "2026-11-03"},
    ])
    assert [(leg.offset, leg.lots, leg.expiry_offset) for leg in t.legs] == [(-1, 2, 0), (1, 1, 0), (0, 1, 1)]
    with pytest.raises(ValueError):
        template_from_legs("Bad", ch, [{"option_type": "CE", "action": "BUY", "strike": 1}])


def test_saved_strategy_api_roundtrip(tmp_path):
    store = StrategyStore(tmp_path / "s.json")
    client = TestClient(create_app(manager_with(RecordedNSEProvider()), store))
    r = client.post("/api/strategy/saved/from-legs", json={
        "name": "My Bull", "symbol": "NIFTY",
        "legs": [{"option_type": "CE", "action": "BUY", "strike": 25000},
                 {"option_type": "CE", "action": "SELL", "strike": 25100}]})
    assert r.status_code == 200, r.text
    assert r.json()["id"] == "my_bull"
    ids = [t["id"] for t in client.get("/api/strategy/templates").json()]
    assert "my_bull" in ids and "iron_condor" in ids
    built = client.post("/api/strategy/templates/my_bull/build", json={"symbol": "NIFTY"}).json()
    assert [leg["strike"] for leg in built["strategy"]["legs"]] == [25000, 25100]
    r = client.post("/api/strategy/saved", json={"name": "Edited", "legs": [
        {"option_type": "PE", "action": "SELL", "offset": -1}]})
    assert r.json()["id"] == "edited"
    assert client.post("/api/strategy/saved", json={"name": "Bad", "legs": [
        {"option_type": "ZZ", "action": "SELL"}]}).status_code == 422
    assert client.delete("/api/strategy/saved/edited").status_code == 200
    assert client.delete("/api/strategy/saved/edited").status_code == 404
