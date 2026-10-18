import math
from datetime import date, datetime, timezone

import pytest

from option_trader.models import OptionChain, OptionQuote, StrikeRow
from option_trader.pricing import bs_price, enrich_chain
from option_trader.pricing.forward import carry_yield, implied_forward
from option_trader.strategy.engine import Leg, Strategy, analyze

NOW = datetime(2026, 10, 14, 4, 0, tzinfo=timezone.utc)
EXP = date(2026, 11, 26)


def synthetic_chain(spot=1400.0, r=0.065, q=0.02, vol=0.25, smile=0.0):
    """Prices generated from Black-Scholes-Merton with a dividend yield (test-only data)."""
    ch = OptionChain("STOCK", EXP, spot, [], "test", NOW)
    t = ch.years_to_expiry(NOW)
    rows = []
    for k in range(1200, 1620, 20):
        v = vol + smile * (k / spot - 1) ** 2
        rows.append(StrikeRow(float(k),
                              OptionQuote(k, "CE", EXP, ltp=round(bs_price(spot, k, t, r, v, "CE", q), 4)),
                              OptionQuote(k, "PE", EXP, ltp=round(bs_price(spot, k, t, r, v, "PE", q), 4))))
    ch.rows = rows
    return ch, t


def test_implied_forward_recovers_dividend_adjusted_forward():
    ch, t = synthetic_chain()
    f = implied_forward(ch, 0.065, t)
    assert f == pytest.approx(1400 * math.exp((0.065 - 0.02) * t), rel=1e-5)
    assert carry_yield(1400, f, 0.065, t) == pytest.approx(0.02, abs=1e-4)


def test_enrich_recovers_true_iv_across_strikes():
    ch, t = synthetic_chain()
    enrich_chain(ch, 0.065, now=NOW)
    assert ch.carry == pytest.approx(0.02, abs=1e-4)
    for k in (1300.0, 1400.0, 1500.0):
        for typ in ("CE", "PE"):
            assert ch.quote(k, typ).iv == pytest.approx(0.25, abs=2e-4)
    # Ignoring the dividend (spot-based) would show a false skew between calls and puts.
    from option_trader.pricing import implied_vol

    naive_call = implied_vol(ch.quote(1400.0, "CE").ltp, 1400, 1400, t, 0.065, "CE")
    naive_put = implied_vol(ch.quote(1400.0, "PE").ltp, 1400, 1400, t, 0.065, "PE")
    assert abs(naive_put - naive_call) > 0.01


def test_strategy_valued_on_forward_shows_zero_pnl_today():
    ch, t = synthetic_chain()
    enrich_chain(ch, 0.065, now=NOW)
    c, p = ch.quote(1400.0, "CE"), ch.quote(1400.0, "PE")
    st = Strategy("Straddle", [Leg("CE", "BUY", 1400, 1, c.ltp, EXP, market_price=c.ltp),
                               Leg("PE", "BUY", 1400, 1, p.ltp, EXP, market_price=p.ltp)])
    res = analyze(st, ch.spot, 0.065, now=NOW, q=ch.carry)
    assert res["current_pnl"] == pytest.approx(0, abs=0.01)
    assert res["leg_ivs"] == pytest.approx([0.25, 0.25], abs=2e-4)


def test_forward_rejected_when_quotes_inconsistent():
    ch, t = synthetic_chain()
    for r_ in ch.rows:
        r_.put.ltp = r_.put.ltp * 50  # garbage puts
    assert implied_forward(ch, 0.065, t) is None
