from datetime import date

import pytest
from fastapi.testclient import TestClient

from option_trader.analytics.chain import analyze_chain, pcr_baseline
from option_trader.analytics.flow import unusual_activity
from option_trader.analytics.gamma import gamma_exposure
from option_trader.app import create_app
from option_trader.models import OptionChain, OptionQuote, StrikeRow, utcnow
from option_trader.oi_history import OIHistory

from .helpers import manager_with
from .test_valuation_lab import EXP, SpyProvider

EXPIRY = date(2026, 11, 20)


def make_chain(symbol="SPY", spot=100.0, strikes=range(80, 121, 5), call_oi=None, put_oi=None, iv=0.25, **quote_kw):
    rows = []
    for k in strikes:
        c = OptionQuote(float(k), "CE", EXPIRY, oi=(call_oi or {}).get(k, 0), iv=iv, **quote_kw.get("CE", {}).get(k, {}))
        p = OptionQuote(float(k), "PE", EXPIRY, oi=(put_oi or {}).get(k, 0), iv=iv, **quote_kw.get("PE", {}).get(k, {}))
        rows.append(StrikeRow(float(k), c, p))
    return OptionChain(symbol, EXPIRY, spot, rows, "cboe", utcnow(), lot_size=100, expiries=[EXPIRY])


def test_gex_sign_walls_and_zero_gamma_flip():
    calls = make_chain(call_oi={100: 1000, 110: 5000})
    g = gamma_exposure([calls], 0.04)
    assert g["total_gex"] > 0 and g["regime"] == "positive" and g["call_wall"] == 110
    puts = make_chain(put_oi={95: 2000, 90: 4000})
    g = gamma_exposure([puts], 0.04)
    # walls are gamma-weighted: 95 (nearer the money) outweighs twice the OI at 90
    assert g["total_gex"] < 0 and g["put_wall"] == 95 and "short gamma" in g["explanation"]
    # big put book below, call book above: positive at spot, flips negative lower down
    mixed = make_chain(call_oi={105: 3000, 110: 3000}, put_oi={90: 6000, 85: 6000})
    g = gamma_exposure([mixed], 0.04)
    assert g["total_gex"] > 0 and g["zero_gamma"] is not None and 88 < g["zero_gamma"] < 100
    prof = {round(p["spot"]): p["gex"] for p in g["profile"]}
    assert prof[100] > 0 and min(v for s, v in prof.items() if s < 92) < 0
    # GEX in $ per 1% move: gamma * OI * 100 * S^2 * 1%
    one = make_chain(call_oi={100: 1})
    from option_trader.pricing.black_scholes import greeks

    t = one.years_to_expiry()
    expect = greeks(100, 100, t, 0.04, 0.25, "CE").gamma * 100 * 100 ** 2 * 0.01
    assert gamma_exposure([one], 0.04)["total_gex"] == pytest.approx(expect, abs=1)
    with pytest.raises(ValueError):
        gamma_exposure([make_chain()], 0.04)  # no OI at all


def test_unusual_activity_filters_and_sides():
    kw = {"CE": {105: {"volume": 3000, "bid": 1.9, "ask": 2.1, "ltp": 2.1},
                 110: {"volume": 50, "bid": 0.4, "ask": 0.5, "ltp": 0.5}},
          "PE": {95: {"volume": 1500, "bid": 3.0, "ask": 3.2, "ltp": 3.0},
                 90: {"volume": 800, "bid": 1.0, "ask": 1.2, "ltp": 1.1}}}
    ch = make_chain(call_oi={105: 500, 110: 10}, put_oi={95: 400, 90: 5000}, **kw)
    f = unusual_activity([ch])
    keys = [(h["type"], h["strike"]) for h in f["hits"]]
    assert keys == [("CE", 105.0), ("PE", 95.0)]  # 110C too small, 90P volume < OI
    c105 = f["hits"][0]
    assert c105["premium"] == pytest.approx(3000 * 2.0 * 100) and c105["side"] == "ask" and c105["sentiment"] == "bullish"
    assert f["hits"][1]["side"] == "bid" and f["hits"][1]["sentiment"] == "bullish"  # puts sold at the bid
    assert f["premium_pcr"] == pytest.approx((1500 * 3.1 + 800 * 1.1) / (3000 * 2.0 + 50 * 0.45), rel=1e-3)


def test_pcr_reads_against_the_products_normal_level():
    assert pcr_baseline("SPX") == 1.3 and pcr_baseline("QQQ") == 1.3 and pcr_baseline("AAPL") == 0.7
    heavy_puts = make_chain("AAPL", call_oi={105: 1000}, put_oi={95: 1600})  # 1.6 vs normal 0.7
    sig = {s.name: s for s in analyze_chain(heavy_puts).signals}["PCR (OI)"]
    assert sig.bias == "bullish" and "contrarian" in sig.detail
    normal_index = make_chain("SPX", call_oi={105: 1000}, put_oi={95: 1400})
    assert {s.name: s for s in analyze_chain(normal_index).signals}["PCR (OI)"].bias == "neutral"
    calls_heavy = make_chain("AAPL", call_oi={105: 3000}, put_oi={95: 900})
    assert {s.name: s for s in analyze_chain(calls_heavy).signals}["PCR (OI)"].bias == "bearish"


def test_oi_history_fills_day_over_day_change(tmp_path):
    h = OIHistory(tmp_path)
    day1 = make_chain(call_oi={100: 1000}, put_oi={100: 500})
    h.update(day1, date(2026, 11, 2))
    assert day1.quote(100, "CE").oi_change is None  # nothing to compare with yet
    day2 = make_chain(call_oi={100: 1300}, put_oi={100: 450})
    h.update(day2, date(2026, 11, 3))
    assert day2.quote(100, "CE").oi_change == 300 and day2.quote(100, "PE").oi_change == -50
    assert any("versus 2026-11-02" in n for n in day2.notes)
    # same day again: still measured against the previous date, and persisted to disk
    again = make_chain(call_oi={100: 1350}, put_oi={100: 450})
    OIHistory(tmp_path).update(again, date(2026, 11, 3))
    assert again.quote(100, "CE").oi_change == 350
    # a source-provided change is never overwritten
    src = make_chain(call_oi={100: 1400})
    src.quote(100, "CE").oi_change = 7
    h.update(src, date(2026, 11, 4))
    assert src.quote(100, "CE").oi_change == 7


def test_api_gamma_and_flow(tmp_path):
    mgr = manager_with(SpyProvider())
    mgr.oi_history = OIHistory(tmp_path)
    client = TestClient(create_app(mgr))
    g = client.get("/api/analytics/gamma", params={"symbol": "SPY", "expiry": "2026-10-16", "expiries": 2}).json()
    assert g["expiries"] == ["2026-10-16", EXP.isoformat()] and g["strikes"] and g["profile"]
    f = client.get("/api/analytics/flow", params={"symbol": "SPY", "min_vol_oi": 0, "min_premium": 0, "min_volume": 1}).json()
    assert f["scanned"] > 0 and f["hits"] and f["hits"][0]["premium"] >= f["hits"][-1]["premium"]
    assert (tmp_path / "oi_history" / "SPY.json").exists()
    assert client.get("/api/analytics/gamma", params={"symbol": "QQQ"}).status_code == 503
