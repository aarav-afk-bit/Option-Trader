import pytest
from fastapi.testclient import TestClient

from option_trader.api.strategies import DistributionIn, apply_view
from option_trader.app import create_app
from option_trader.providers.base import DataProvider

from .helpers import manager_with, trending_candles
from .test_opt_search import smile_chain


class Synth(DataProvider):
    name = "cboe"

    def get_expiries(self, symbol):
        return [smile_chain()[0].expiry]

    def get_option_chain(self, symbol, expiry=None):
        ch, _ = smile_chain()
        ch.symbol = symbol.upper()
        return ch

    def get_history(self, symbol, period="1y", interval="1d"):
        return trending_candles(start=900)


def test_views_translate_to_distribution():
    ch, _ = smile_chain()
    from option_trader.pricing import enrich_chain

    enrich_chain(ch, 0.0)
    assert apply_view(ch, DistributionIn(view="up", strength=1)).drift > 0
    assert apply_view(ch, DistributionIn(view="down", strength=0.5)).drift < 0
    assert apply_view(ch, DistributionIn(view="range")).vol_mult < 1
    assert apply_view(ch, DistributionIn(view="breakout")).vol_mult > 1
    assert apply_view(ch, DistributionIn(view="auto")).mode == "auto"


def _quick(client, view, **prefs):
    r = client.post("/api/strategy/optimize", json={
        "symbol": "TEST", "legs": [], "search": "global",
        "prefs": {"max_loss": 3000, "win_weight": 0.5, **prefs},
        "distribution": {"view": view, "strength": 1.0}})
    assert r.status_code == 200, r.text
    return r.json()


def position_delta(client, out):
    legs = [{k: l[k] for k in ("option_type", "action", "strike", "lots")} for l in out["best"]["legs"]]
    r = client.post("/api/strategy/analyze", json={"symbol": "TEST", "legs": legs})
    assert r.status_code == 200, r.text
    return r.json()["greeks"]["delta"]


def test_quick_trade_direction_follows_the_view():
    client = TestClient(create_app(manager_with(Synth())))
    up, down = _quick(client, "up"), _quick(client, "down")
    assert up["best"]["metrics"]["risk"] <= 3000 and down["best"]["metrics"]["risk"] <= 3000
    # An "up" view must produce a position that gains when the price rises, "down" the opposite.
    assert position_delta(client, up) > 0 > position_delta(client, down)
    rng, brk = _quick(client, "range"), _quick(client, "breakout")
    assert rng["best"]["metrics"]["win_rate"] > brk["best"]["metrics"]["win_rate"]


def test_quick_trade_rejects_unknown_view():
    client = TestClient(create_app(manager_with(Synth())))
    r = client.post("/api/strategy/optimize", json={"symbol": "TEST", "legs": [], "search": "global",
                                                    "distribution": {"view": "sideways-ish"}})
    assert r.status_code == 422
