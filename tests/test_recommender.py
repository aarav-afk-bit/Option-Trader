from fastapi.testclient import TestClient

from option_trader.analytics.recommender import PLAYBOOK, market_view, recommend
from option_trader.app import create_app
from option_trader.strategy.templates import BUILTIN_BY_ID

from .helpers import RecordedProvider, manager_with, trending_candles


def sig(score, strength=0.5):
    return {"score": score, "strength": strength, "detail": f"s{score}"}


def test_playbook_uses_real_templates():
    for fams in PLAYBOOK.values():
        for f in fams:
            assert f in BUILTIN_BY_ID


def test_market_view_directions():
    v = market_view([sig(0.5)], [sig(0.4)], {"view": "fair"}, 30)
    assert v.direction == "bullish" and v.score > 0.2
    v = market_view([sig(-0.5)], [sig(-0.5)], {"view": "high"}, 30)
    assert v.direction == "bearish" and v.volatility == "high"
    v = market_view([sig(0.0, 0.2)], [], {"view": "fair"}, None)
    assert v.direction == "neutral" and v.technical_score is None


def test_squeeze_flags_volatile():
    v = market_view([sig(0.0, 0.2)], [sig(0.05)], {"view": "low", "hv20_percentile": 10}, 15)
    assert v.direction == "volatile"


def test_recommend_on_recorded_chain():
    ch = RecordedProvider().get_option_chain("NDX")
    out = recommend(ch, trending_candles(drift=-0.004), 0.065)
    assert out["source"] == "cboe"
    assert out["view"]["direction"] in ("bearish", "neutral", "bullish", "volatile")
    assert out["ideas"], out
    for idea in out["ideas"]:
        assert idea["actions"]
        # every leg is priced from the recorded chain
        for leg in idea["legs"]:
            if leg["option_type"] != "FUT":
                assert leg["premium"] == ch.quote(leg["strike"], leg["option_type"]).price
    assert out["disclaimer"]


def test_recommend_without_history():
    ch = RecordedProvider().get_option_chain("NDX")
    out = recommend(ch, None, 0.065)
    assert out["technicals"] is None and out["notes"]


def test_recommendations_api():
    client = TestClient(create_app(manager_with(RecordedProvider(trending_candles()))))
    r = client.get("/api/analytics/recommendations", params={"symbol": "NDX", "lots": 2})
    assert r.status_code == 200, r.text
    ideas = r.json()["ideas"]
    assert ideas and all(leg["lots"] % 2 == 0 for i in ideas for leg in i["legs"])
