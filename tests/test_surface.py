import json
import math
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from option_trader.analytics.surface import constant_maturity_iv, skew_metrics, surface
from option_trader.app import create_app
from option_trader.iv_history import IVHistory
from option_trader.models import OptionChain, OptionQuote, StrikeRow, utcnow
from option_trader.pricing.black_scholes import enrich_chain, greeks

from .helpers import manager_with
from .test_valuation_lab import SpyProvider, spy_chain


def smile_chain(expiry=date(2026, 11, 20), spot=100.0, atm=0.20, slope=-0.30, curve=0.8, r=0.04):
    """A chain whose IVs follow a known smile in ln(K/S): atm + slope*m + curve*m^2."""
    rows = []
    ch = OptionChain("XYZ", expiry, spot, [], "cboe", utcnow(), lot_size=100, expiries=[expiry])
    t = ch.years_to_expiry()
    for k in range(70, 131, 2):
        m = math.log(k / spot)
        iv = atm + slope * m + curve * m * m
        qs = []
        for kind in ("CE", "PE"):
            g = greeks(spot, k, t, r, iv, kind)
            qs.append(OptionQuote(float(k), kind, expiry, iv=iv, delta=g.delta))
        rows.append(StrikeRow(float(k), *qs))
    ch.rows = rows
    return ch


def test_skew_metrics_on_a_known_smile():
    ch = smile_chain()
    sk = skew_metrics(ch, 0.04)
    assert sk["atm_iv"] == pytest.approx(0.20, abs=0.002)
    assert sk["rr25"] < -0.02  # downward-sloping smile: 25d puts dearer than 25d calls
    assert sk["bf25"] > 0  # convex smile: wings above ATM
    flat = skew_metrics(smile_chain(slope=0, curve=0), 0.04)
    assert flat["rr25"] == pytest.approx(0, abs=1e-6) and flat["bf25"] == pytest.approx(0, abs=1e-6)


def test_surface_rows_and_constant_maturity_iv():
    a = smile_chain(date(2026, 10, 30), atm=0.18)
    b = smile_chain(date(2026, 12, 18), atm=0.24)
    s = surface([b, a], lambda c: 0.04)
    assert [e["expiry"] for e in s["expiries"]] == ["2026-10-30", "2026-12-18"]  # sorted by time
    assert all(abs(p["moneyness"]) <= 0.4 for e in s["expiries"] for p in e["points"])
    t0, t1 = s["expiries"][0]["years"], s["expiries"][1]["years"]
    v0, v1 = s["expiries"][0]["atm_iv"], s["expiries"][1]["atm_iv"]
    T = 30 / 365
    expect = math.sqrt((v0 ** 2 * t0 + (v1 ** 2 * t1 - v0 ** 2 * t0) * (T - t0) / (t1 - t0)) / T)
    assert s["iv30"] == pytest.approx(expect, rel=1e-6) and v0 < s["iv30"] < v1
    # nothing near 30 days: refuse to extrapolate far
    assert constant_maturity_iv([{"years": 2 / 365, "atm_iv": 0.2}]) is None
    assert constant_maturity_iv([{"years": 25 / 365, "atm_iv": 0.2}]) == 0.2


def test_iv_history_rank_and_percentile(tmp_path):
    h = IVHistory(tmp_path)
    start = date(2026, 1, 1)
    for i in range(30):
        h.record("XYZ", 0.20 + 0.01 * (i % 10), "computed", start + timedelta(days=i))  # 0.20..0.29
    r = h.rank("XYZ", 0.27)
    assert r["days"] == 30 and r["low"] == pytest.approx(0.20) and r["high"] == pytest.approx(0.29)
    assert r["iv_rank"] == pytest.approx(77.8, abs=0.1) and r["iv_percentile"] == pytest.approx(70.0)
    assert IVHistory(tmp_path).rank("XYZ", 0.27)["days"] == 30  # persisted
    few = IVHistory(tmp_path / "x")
    few.record("ABC", 0.3, "computed", start)
    assert few.rank("ABC", 0.3)["iv_rank"] is None and few.rank("ABC", 0.3)["days"] == 1
    # a published value wins over a computed one for the same day; junk is ignored
    h.record("XYZ", 0.50, "published", start)
    h.record("XYZ", 0.10, "computed", start)
    h.record("XYZ", 80.0, "published", start + timedelta(days=1))  # percent by mistake: rejected
    assert dict(h.series("XYZ"))[start.isoformat()] == 0.50
    assert dict(h.series("XYZ"))[(start + timedelta(days=1)).isoformat()] == pytest.approx(0.21)


def test_surface_endpoint_records_published_iv30(tmp_path):
    mgr = manager_with(SpyProvider())
    mgr.iv_history = IVHistory(tmp_path)
    client = TestClient(create_app(mgr))
    d = client.get("/api/analytics/surface", params={"symbol": "SPY"}).json()
    assert len(d["expiries"]) == 2 and d["expiries"][0]["points"]
    assert d["iv30_current"] == pytest.approx(0.164) and d["iv30_source"].startswith("Cboe")
    assert d["iv_rank"]["days"] == 1 and d["iv_rank"]["iv_rank"] is None
    json.dumps(d)  # plain JSON types only
    assert client.get("/api/analytics/surface", params={"symbol": "QQQ"}).status_code == 503


def test_real_chain_skew_is_finite():
    sk = skew_metrics(spy_chain(), 0.04)
    assert all(sk[k] is not None for k in ("atm_iv", "call25_iv", "put25_iv", "rr25", "bf25"))
    assert enrich_chain  # imported for fixtures above
