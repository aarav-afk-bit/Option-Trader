from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from option_trader.app import create_app
from option_trader.strategy.engine import Leg, Strategy, analyze, leg_from_chain

from .helpers import RecordedNSEProvider, manager_with

NOW = datetime(2026, 10, 14, 4, 0, tzinfo=timezone.utc)
EXP = date(2026, 10, 27)


def leg(t, a, k, prem, lots=1, iv=0.15):
    return Leg(t, a, k, lots, prem, EXP, iv)


def test_long_call():
    res = analyze(Strategy("LC", [leg("CE", "BUY", 100, 5)]), 100, now=NOW)
    assert res["max_loss"] == -5
    assert res["unlimited_profit"] and res["max_profit"] is None
    assert res["breakevens"] == [105]
    assert res["premium_type"] == "debit" and res["net_premium"] == -5
    assert 0 < res["pop"] < 0.5
    assert res["greeks"]["delta"] > 0


def test_bull_call_spread_bounded():
    st = Strategy("BCS", [leg("CE", "BUY", 100, 6), leg("CE", "SELL", 110, 2)], lot_size=50)
    res = analyze(st, 102, now=NOW)
    assert res["max_profit"] == pytest.approx(6 * 50)
    assert res["max_loss"] == pytest.approx(-4 * 50)
    assert res["breakevens"] == [104]
    assert res["reward_risk"] == pytest.approx(1.5)


def test_short_straddle_unlimited_loss():
    st = Strategy("SS", [leg("CE", "SELL", 100, 5), leg("PE", "SELL", 100, 5)])
    res = analyze(st, 100, now=NOW)
    assert res["unlimited_loss"] and res["max_loss"] is None
    assert res["max_profit"] == pytest.approx(10)
    assert res["breakevens"] == [90, 110]
    assert res["premium_type"] == "credit"


def test_covered_call_with_underlying():
    st = Strategy("CC", [Leg("FUT", "BUY", 0, 1, 100, EXP), leg("CE", "SELL", 110, 3)])
    res = analyze(st, 100, now=NOW)
    assert res["max_profit"] == pytest.approx(13)
    assert res["breakevens"] == [97]


def test_calendar_uses_far_leg_value():
    from option_trader.pricing import bs_price

    near_t = (datetime(2026, 10, 27, 10, tzinfo=timezone.utc) - NOW).total_seconds() / (365 * 86400)
    near_px = bs_price(100, 100, near_t, 0.065, 0.15, "CE")
    far_px = bs_price(100, 100, near_t + 28 / 365, 0.065, 0.15, "CE")
    far = Leg("CE", "BUY", 100, 1, far_px, EXP + timedelta(days=28), 0.15)
    st = Strategy("Cal", [leg("CE", "SELL", 100, near_px), far])
    res = analyze(st, 100, now=NOW)
    assert res["max_profit"] > 0 and res["max_loss"] < 0
    assert len(res["breakevens"]) == 2


def test_iv_solved_from_premium_when_missing():
    st = Strategy("LC", [Leg("CE", "BUY", 100, 1, 3.0, EXP, None)])
    res = analyze(st, 100, now=NOW)
    assert 0.05 < res["leg_ivs"][0] < 1


def test_leg_from_real_chain():
    ch = RecordedNSEProvider().get_option_chain("NIFTY")
    l1 = leg_from_chain(ch, "PE", "BUY", 25000)
    assert l1.premium == 128.0
    assert leg_from_chain(ch, "PE", "BUY", 25000, fill="market").premium == 128.5
    assert leg_from_chain(ch, "PE", "SELL", 25000, fill="market").premium == 127.5
    with pytest.raises(ValueError):
        leg_from_chain(ch, "CE", "BUY", 99999)


def test_analyze_api_prices_from_chain():
    client = TestClient(create_app(manager_with(RecordedNSEProvider())))
    body = {"symbol": "NIFTY", "legs": [
        {"option_type": "CE", "action": "BUY", "strike": 25000},
        {"option_type": "CE", "action": "SELL", "strike": 25100},
    ]}
    r = client.post("/api/strategy/analyze", json=body)
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["strategy"]["lot_size"] == 75
    assert res["strategy"]["legs"][0]["premium"] == 145.0
    assert res["max_loss"] == pytest.approx(-(145 - 92) * 75)
    assert res["source"] == "nse"
    bad = client.post("/api/strategy/analyze", json={"symbol": "NIFTY", "legs": [
        {"option_type": "CE", "action": "BUY", "strike": 123}]})
    assert bad.status_code == 400


def test_vectorised_matches_scalar():
    import numpy as np

    from option_trader.pricing import bs_price
    from option_trader.strategy.engine import bs_price_vec

    xs = np.array([90.0, 100.0, 113.0])
    for t in ("CE", "PE"):
        vec = bs_price_vec(xs, 100, 0.1, 0.065, 0.2, t)
        assert list(vec) == pytest.approx([bs_price(x, 100, 0.1, 0.065, 0.2, t) for x in xs])


def test_position_opened_at_market_shows_zero_pnl_today():
    # Source IV deliberately inconsistent with the price: valuation must follow the price.
    st = Strategy("LC", [Leg("CE", "BUY", 100, 1, 3.0, EXP, iv=0.5, market_price=3.0)])
    res = analyze(st, 100, now=NOW)
    assert res["current_pnl"] == pytest.approx(0, abs=0.01)


def test_overridden_entry_price_valued_at_market():
    st = Strategy("LC", [Leg("CE", "BUY", 100, 1, 2.0, EXP, market_price=3.0)])
    assert analyze(st, 100, now=NOW)["current_pnl"] == pytest.approx(1.0, abs=0.01)


def test_api_current_pnl_zero_for_live_entry():
    client = TestClient(create_app(manager_with(RecordedNSEProvider())))
    res = client.post("/api/strategy/analyze", json={"symbol": "NIFTY", "fill": "mid", "legs": [
        {"option_type": "PE", "action": "BUY", "strike": 25000, "lots": 2}]}).json()
    assert res["current_pnl"] == pytest.approx(0, abs=1)


def test_greeks_curve():
    res = analyze(Strategy("LC", [leg("CE", "BUY", 100, 5)]), 100, now=NOW)
    c = res["greeks_curve"]
    assert len(c["prices"]) == len(c["delta"]) <= 81
    assert c["delta"] == sorted(c["delta"])  # long call delta rises with price
    assert all(g >= 0 for g in c["gamma"])


def test_vectorised_greeks_match_scalar():
    import numpy as np

    from option_trader.pricing import greeks
    from option_trader.strategy.engine import greeks_vec

    xs = np.array([95.0, 100.0, 108.0])
    for t in ("CE", "PE"):
        g = greeks_vec(xs, 100, 0.1, 0.065, 0.2, t)
        for i, x in enumerate(xs):
            ref = greeks(x, 100, 0.1, 0.065, 0.2, t)
            for k in ("delta", "gamma", "theta", "vega"):
                assert g[k][i] == pytest.approx(getattr(ref, k), rel=1e-9)
