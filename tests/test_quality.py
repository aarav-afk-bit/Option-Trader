from datetime import datetime, timedelta, timezone

from option_trader.models import OptionChain, OptionQuote, StrikeRow
from option_trader.pricing import bs_price, enrich_chain
from option_trader.providers.base import DataProvider
from option_trader.quality import report, sanitize, validate

from .helpers import manager_with


def clean_chain(r=0.065):
    """Arbitrage-free chain from Black-Scholes with tight books (test-only data)."""
    now = datetime.now(timezone.utc)
    exp = (now + timedelta(days=20)).date()
    ch = OptionChain("NDX", exp, 25000.0, [], "test", now, lot_size=75)
    t = ch.years_to_expiry(now)
    rows = []
    for k in range(24000, 26050, 100):
        qs = []
        for typ in ("CE", "PE"):
            px = round(bs_price(25000, k, t, r, 0.13 + 0.000002 * (k - 25000) ** 2 / 100, typ), 2)
            h = max(0.05, round(px * 0.004, 2))
            qs.append(OptionQuote(k, typ, exp, ltp=px, bid=round(px - h, 2), ask=round(px + h, 2), oi=1e5))
        rows.append(StrikeRow(float(k), *qs))
    ch.rows = rows
    return ch


def run(ch, r=0.065):
    sanitize(ch)
    enrich_chain(ch, r)
    validate(ch, r)
    return report(ch)


def test_clean_chain_has_no_false_alarms():
    rep = run(clean_chain())
    assert rep["suspect"] == 0 and rep["score"] == 100


def test_crossed_book_and_zero_ltp():
    ch = clean_chain()
    q = ch.quote(25000.0, "CE")
    q.bid, q.ask = q.ask + 5, q.bid
    ch.quote(24500.0, "PE").ltp = 0
    rep = run(ch)
    assert q.suspect and q.bid is None and "crossed book" in q.issues[0]
    assert ch.quote(24500.0, "PE").ltp is None  # 0 = no trade, not a price
    assert rep["suspect"] >= 1 and rep["score"] < 100


def test_stale_trade_below_intrinsic_is_flagged_and_excluded_from_forward():
    ch = clean_chain()
    deep = ch.quote(24000.0, "CE")
    deep.ltp, deep.bid, deep.ask = 300.0, None, None  # true value ~1,000: an old trade
    rep = run(ch)
    assert deep.suspect and any("intrinsic" in i for i in deep.issues)
    clean = clean_chain()
    run(clean)
    assert abs(ch.forward - clean.forward) < 1.0  # suspect quote did not move the forward
    assert any(i["strike"] == 24000.0 for i in rep["issues"])


def test_monotonicity_violation():
    ch = clean_chain()
    bad = ch.quote(25500.0, "CE")
    bad.ltp, bad.bid, bad.ask = ch.quote(25400.0, "CE").price + 20, None, None
    run(ch)
    assert bad.suspect


def test_iv_outlier_and_parity():
    ch = clean_chain()
    q = ch.quote(25300.0, "PE")
    q.ltp = round(q.ltp * 1.6, 2)
    q.bid, q.ask = None, None
    run(ch)
    assert q.suspect and any("parity" in i or "IV" in i for i in q.issues)


def test_manager_attaches_report_and_optimiser_skips_suspects():
    class P(DataProvider):
        name = "cboe"

        def get_expiries(self, symbol):
            return [clean_chain().expiry]

        def get_option_chain(self, symbol, expiry=None):
            ch = clean_chain()
            q = ch.quote(24000.0, "CE")
            q.ltp, q.bid, q.ask = 300.0, None, None
            return ch

    ch = manager_with(P()).get_chain("NDX")
    assert ch.quality["suspect"] >= 1 and ch.to_dict()["quality"]["score"] < 100
    from option_trader.optimizer.distribution import lognormal
    from option_trader.optimizer.objective import Preferences
    from option_trader.optimizer.search import Optimizer

    opt = Optimizer(ch, lambda e: ch, lognormal(25000, 0.13, 0.05), Preferences(), 0.065)
    assert not opt.liquid(ch, "CE", 24000.0)
