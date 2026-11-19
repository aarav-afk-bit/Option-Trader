"""Regression for a model flaw found by the overfitting check: a futures/underlying leg priced
at spot (ignoring cost of carry) let the optimiser 'find' a riskless conversion arbitrage."""
import math
from datetime import datetime, timedelta, timezone

import pytest

from option_trader.charges import ChargeRates
from option_trader.models import OptionChain, OptionQuote, StrikeRow
from option_trader.optimizer.distribution import lognormal
from option_trader.optimizer.objective import evaluate
from option_trader.pricing import bs_price, enrich_chain
from option_trader.strategy.engine import Strategy, analyze, leg_from_chain

ZERO = ChargeRates(0, 0, 0, 0, 0, 0)


def chain(r=0.065):
    now = datetime.now(timezone.utc)
    exp = (now + timedelta(days=21)).date()
    ch = OptionChain("NDX", exp, 25000.0, [], "test", now, lot_size=75)
    t = ch.years_to_expiry(now)
    ch.rows = [StrikeRow(float(k), OptionQuote(k, "CE", exp, ltp=bs_price(25000, k, t, r, 0.14, "CE")),
                         OptionQuote(k, "PE", exp, ltp=bs_price(25000, k, t, r, 0.14, "PE")))
               for k in range(24500, 25550, 50)]
    enrich_chain(ch, r)
    return ch, t


def test_futures_leg_trades_at_forward():
    ch, t = chain()
    fut = leg_from_chain(ch, "FUT", "BUY")
    assert fut.premium == pytest.approx(25000 * math.exp(0.065 * t), rel=1e-4)
    assert ch.fut_price == fut.premium


def test_conversion_is_not_free_money():
    ch, t = chain()
    legs = [leg_from_chain(ch, "FUT", "BUY"), leg_from_chain(ch, "PE", "BUY", 25000.0),
            leg_from_chain(ch, "CE", "SELL", 25000.0)]
    st = Strategy("Conversion", legs, 75)
    m = evaluate(st, lognormal(ch.fut_price, 0.14, t), ch.spot, 0.065, ch.carry, rates=ZERO)
    assert abs(m["expected_pnl"]) < 75 * 0.5  # within rounding of the forward, not ~₹7,000
    res = analyze(st, ch.spot, 0.065, q=ch.carry)
    assert abs(res["current_pnl"]) < 75 * 0.5
