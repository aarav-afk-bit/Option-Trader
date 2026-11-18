import math
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from option_trader.app import create_app
from option_trader.charges import ChargeRates
from option_trader.optimizer.context import build_distribution, realised_vol
from option_trader.optimizer.distribution import lognormal
from option_trader.optimizer.objective import Preferences, evaluate, score
from option_trader.strategy.engine import Leg, Strategy

from .helpers import RecordedProvider, manager_with, trending_candles

NOW = datetime(2026, 10, 14, 4, 0, tzinfo=timezone.utc)
EXP = date(2026, 10, 27)
T = (datetime(2026, 10, 27, 10, 0, tzinfo=timezone.utc) - NOW).total_seconds() / (365 * 86400)
ZERO = ChargeRates(0, 0, 0, 0, 0, 0)


def test_lognormal_distribution_moments():
    d = lognormal(100, 0.2, 0.25)
    assert d.probs.sum() == pytest.approx(1.0)
    assert d.mean() == pytest.approx(100, rel=2e-3)  # mean = forward
    assert d.quantile(0.5) < 100  # lognormal median below mean
    shifted = lognormal(100, 0.2, 0.25, drift=0.05)
    assert shifted.mean() == pytest.approx(105, rel=2e-3)


def test_fair_priced_call_has_zero_edge_and_known_win_rate():
    from option_trader.pricing import bs_price

    px = bs_price(100, 100, T, 0.0, 0.2, "CE")
    st = Strategy("LC", [Leg("CE", "BUY", 100, 1, px, EXP, 0.2)])
    d = lognormal(100, 0.2, T)
    m = evaluate(st, d, 100, 0.0, now=NOW, rates=ZERO)
    assert m["expected_pnl"] == pytest.approx(0, abs=0.02)
    # P(S_T > K + premium) under the same lognormal
    sd = 0.2 * math.sqrt(T)
    be = 100 + px
    want = 1 - __import__("scipy.stats", fromlist=["norm"]).norm.cdf((math.log(be / 100) + 0.5 * sd * sd) / sd)
    assert m["win_rate"] == pytest.approx(want, abs=0.01)
    assert m["max_loss"] == pytest.approx(-px, abs=0.01) and not m["unlimited_loss"]
    assert m["unlimited_profit"]


def test_overpriced_option_gives_seller_positive_edge():
    from option_trader.pricing import bs_price

    rich = bs_price(100, 110, T, 0.0, 0.35, "CE")  # priced at 35% vol, fair is 20%
    st = Strategy("SC", [Leg("CE", "SELL", 110, 1, rich, EXP, 0.35), Leg("CE", "BUY", 115, 1,
                         bs_price(100, 115, T, 0.0, 0.35, "CE"), EXP, 0.35)])
    m = evaluate(st, lognormal(100, 0.2, T), 100, 0.0, now=NOW, rates=ZERO)
    assert m["expected_pnl"] > 0 and m["edge"] > 0


def test_unlimited_loss_detection_and_constraints():
    st = Strategy("SS", [Leg("CE", "SELL", 100, 1, 3.0, EXP, 0.2)])
    m = evaluate(st, lognormal(100, 0.2, T), 100, 0.0, now=NOW, rates=ZERO)
    assert m["unlimited_loss"]
    assert score(m, Preferences()) is None
    assert score(m, Preferences(allow_unlimited_loss=True)) is not None
    lc = evaluate(Strategy("LC", [Leg("CE", "BUY", 100, 1, 3.0, EXP, 0.2)]), lognormal(100, 0.2, T), 100, 0.0,
                  now=NOW, rates=ZERO)
    assert score(lc, Preferences(max_loss=2.0)) is None
    assert score(lc, Preferences(max_loss=5.0)) is not None


def test_win_weight_moves_score_between_goals():
    m = {"unlimited_loss": False, "risk": 100, "win_rate": 0.8, "edge": -0.1}
    assert score(m, Preferences(win_weight=1.0)) == pytest.approx(0.8 * 0.6)  # negative edge penalty
    assert score({**m, "edge": 0.0}, Preferences(win_weight=1.0)) == pytest.approx(0.8)
    assert score(m, Preferences(win_weight=0.0)) < 0.5


def test_build_distribution_from_chain_and_realised():
    ch = RecordedProvider().get_option_chain("NDX")
    from option_trader.pricing import enrich_chain

    enrich_chain(ch, 0.065)
    d = build_distribution(ch, 0.065)
    assert d.kind == "atm" and d.params["forward"] > 0
    rv = realised_vol(trending_candles())
    assert 0.05 < rv < 0.5
    d2 = build_distribution(ch, 0.065, "realised", candles=trending_candles())
    assert d2.params["vol"] == pytest.approx(rv, abs=1e-4)
    with pytest.raises(ValueError):
        build_distribution(ch, 0.065, "realised", candles=[])


def test_score_endpoint():
    client = TestClient(create_app(manager_with(RecordedProvider())))
    r = client.post("/api/strategy/score", json={"symbol": "NDX", "legs": [
        {"option_type": "CE", "action": "BUY", "strike": 25000},
        {"option_type": "CE", "action": "SELL", "strike": 25100}], "prefs": {"win_weight": 0.7}})
    assert r.status_code == 200, r.text
    d = r.json()
    assert 0 < d["metrics"]["win_rate"] < 1 and d["score"] is not None
    assert d["metrics"]["charges"] > 0 and d["distribution"]["kind"] == "atm"
