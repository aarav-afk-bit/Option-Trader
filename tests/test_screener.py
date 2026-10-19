from fastapi.testclient import TestClient

from option_trader.app import create_app
from option_trader.strategy.screener import screen
from option_trader.strategy.templates import BUILTIN_TEMPLATES

from .helpers import RecordedNSEProvider, manager_with


def chain():
    return RecordedNSEProvider().get_option_chain("NIFTY")


def test_screen_filters_and_sorts():
    ch = chain()
    out = screen(ch, BUILTIN_TEMPLATES, 0.065, sort="pop")
    pops = [r["pop"] or 0 for r in out["results"]]
    assert pops == sorted(pops, reverse=True)
    assert all(not r["unlimited_loss"] for r in out["results"])
    assert out["skipped"] > 0  # wide structures don't fit a 3-strike chain


def test_screen_view_and_max_loss():
    ch = chain()
    out = screen(ch, BUILTIN_TEMPLATES, 0.065, view="bullish", max_loss=12000)
    assert out["results"]
    for r in out["results"]:
        assert r["view"] in ("bullish", "any")
        assert r["max_loss"] is not None and -r["max_loss"] <= 12000


def test_screen_api():
    client = TestClient(create_app(manager_with(RecordedNSEProvider())))
    r = client.post("/api/strategy/screen", json={"symbol": "NIFTY", "allow_unlimited_loss": True, "sort": "credit"})
    assert r.status_code == 200, r.text
    d = r.json()
    credits = [x["net_premium"] for x in d["results"]]
    assert credits == sorted(credits, reverse=True) and d["source"] == "nse"
