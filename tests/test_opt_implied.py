import math
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from option_trader.app import create_app
from option_trader.charges import ChargeRates
from option_trader.models import OptionChain, OptionQuote, StrikeRow
from option_trader.optimizer.context import auto_view, build_distribution
from option_trader.optimizer.distribution import lognormal
from option_trader.optimizer.implied import implied_distribution, shift
from option_trader.optimizer.objective import evaluate
from option_trader.pricing import bs_price, enrich_chain
from option_trader.strategy.engine import Strategy, leg_from_chain

from .helpers import RecordedProvider, manager_with, trending_candles

ZERO = ChargeRates(0, 0, 0, 0, 0, 0)


def chain_with(vol_fn, r=0.0):
    """Synthetic chain priced from a known smile (test-only data)."""
    now = datetime.now(timezone.utc)
    exp = (now + timedelta(days=30)).date()
    ch = OptionChain("TEST", exp, 1000.0, [], "test", now, lot_size=1)
    t = ch.years_to_expiry(now)
    rows = []
    for k in range(700, 1301, 10):
        v = vol_fn(math.log(k / 1000))
        rows.append(StrikeRow(float(k), OptionQuote(k, "CE", exp, ltp=bs_price(1000, k, t, r, v, "CE")),
                              OptionQuote(k, "PE", exp, ltp=bs_price(1000, k, t, r, v, "PE"))))
    ch.rows = rows
    enrich_chain(ch, r)
    return ch, t


def test_flat_smile_matches_lognormal():
    ch, t = chain_with(lambda x: 0.20)
    d = implied_distribution(ch, 0.0)
    ref = lognormal(1000, 0.20, t)
    assert d.mean() == pytest.approx(1000, rel=2e-3)
    for p in (0.05, 0.25, 0.5, 0.75, 0.95):
        assert d.quantile(p) == pytest.approx(ref.quantile(p), rel=4e-3)


def test_skew_fattens_left_tail():
    ch, t = chain_with(lambda x: 0.20 - 0.4 * x)  # puts richer than calls
    d = implied_distribution(ch, 0.0)
    ref = lognormal(1000, 0.20, t)
    p_down = d.probs[d.prices < 900].sum()
    assert p_down > ref.probs[ref.prices < 900].sum() * 1.15


def test_market_prices_have_zero_edge_under_implied():
    ch, t = chain_with(lambda x: 0.20 - 0.3 * x + 0.8 * x * x)
    d = implied_distribution(ch, 0.0)
    legs = [leg_from_chain(ch, "PE", "SELL", 950.0), leg_from_chain(ch, "PE", "BUY", 900.0)]
    m = evaluate(Strategy("BPS", legs, 1), d, ch.spot, 0.0, ch.carry, rates=ZERO)
    assert abs(m["expected_pnl"]) < 0.05 * m["risk"]  # fair on the market's own distribution
    assert 0.5 < m["win_rate"] < 0.95


def test_shift_applies_view():
    ch, _ = chain_with(lambda x: 0.2)
    d = implied_distribution(ch, 0.0)
    assert shift(d, 0.02).mean() == pytest.approx(d.mean() * 1.02, rel=1e-6)


def test_needs_enough_quotes():
    ch, _ = chain_with(lambda x: 0.2)
    ch.rows = ch.rows[:2]
    with pytest.raises(ValueError):
        implied_distribution(ch, 0.0)


def test_auto_view_from_signals_and_realised_vol():
    ch = RecordedProvider().get_option_chain("NDX")
    enrich_chain(ch, 0.065)
    v = auto_view(ch, trending_candles(drift=0.004))
    assert v["vol_source"] == "realised" and v["reasons"]
    if v["direction"] == "bullish":
        assert v["drift"] > 0
    d = build_distribution(ch, 0.065, "auto", candles=trending_candles(drift=0.004))
    assert d.kind == "auto" and d.params["view"] == v["direction"]


def test_optimize_endpoint_with_implied_and_auto():
    client = TestClient(create_app(manager_with(RecordedProvider(trending_candles()))))
    for mode in ("implied", "auto"):
        r = client.post("/api/strategy/optimize", json={
            "symbol": "NDX", "legs": [{"option_type": "CE", "action": "BUY", "strike": 25000}],
            "distribution": {"mode": mode}})
        assert r.status_code == 200, (mode, r.text)
        assert r.json()["distribution"]["kind"] == mode
