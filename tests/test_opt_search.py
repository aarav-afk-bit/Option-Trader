from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from option_trader.app import create_app
from option_trader.models import OptionChain, OptionQuote, StrikeRow
from option_trader.optimizer.distribution import lognormal
from option_trader.optimizer.objective import Preferences
from option_trader.optimizer.search import Optimizer, normalize
from option_trader.pricing import bs_price

from .helpers import RecordedNSEProvider, manager_with


def smile_chain(rich_calls=True):
    """A 21-strike chain priced with a smile (test-only data): OTM calls rich if rich_calls."""
    now = datetime.now(timezone.utc)
    exp = (now + timedelta(days=14)).date()
    ch = OptionChain("TEST", exp, 1000.0, [], "test", now, lot_size=50)
    t = ch.years_to_expiry(now)
    rows = []
    for k in range(900, 1101, 10):
        x = k / 1000 - 1
        vol_c = 0.20 + (1.2 * x * x + (0.15 * x if rich_calls else 0)) * 4
        vol_p = 0.20 + 1.2 * x * x * 4
        c = bs_price(1000, k, t, 0.0, vol_c, "CE")
        p = bs_price(1000, k, t, 0.0, vol_p, "PE")
        rows.append(StrikeRow(float(k), OptionQuote(k, "CE", exp, ltp=round(c, 2), bid=round(c * 0.99, 2), ask=round(c * 1.01, 2)),
                              OptionQuote(k, "PE", exp, ltp=round(p, 2), bid=round(p * 0.99, 2), ask=round(p * 1.01, 2))))
    ch.rows = rows
    ch.expiries = [exp]
    return ch, t


def make_opt(ch, t, prefs=None):
    return Optimizer(ch, lambda e: ch, lognormal(1000, 0.20, t), prefs or Preferences(), 0.0, fill="market")


def test_normalize_merges_and_cancels():
    e = "2026-10-27"
    n = normalize([("CE", "BUY", 100.0, 1, e), ("CE", "BUY", 100.0, 2, e), ("PE", "SELL", 90.0, 1, e),
                   ("PE", "BUY", 90.0, 1, e)])
    assert n == (("CE", "BUY", 100.0, 3, e),)


def test_optimizer_improves_score_monotonically_and_respects_limits():
    ch, t = smile_chain()
    e = ch.expiry.isoformat()
    prefs = Preferences(win_weight=0.5, max_loss=3000)
    opt = make_opt(ch, t, prefs)
    out = opt.run([("CE", "BUY", 1000.0, 1, e)], max_iters=8, beam=3)
    assert out["improved"]
    scores = [out["start"]["score"]] + [s["score"] for s in out["steps"]]
    assert all(b > a for a, b in zip(scores, scores[1:]))
    best = out["best"]
    assert best["score"] == scores[-1]
    assert best["metrics"]["risk"] <= 3000 and not best["metrics"]["unlimited_loss"]
    assert len(best["legs"]) <= prefs.max_legs
    # every leg priced from the chain (market fill: ask for buys, bid for sells)
    for leg in best["legs"]:
        q = ch.quote(leg["strike"], leg["option_type"])
        assert leg["premium"] == (q.ask if leg["action"] == "BUY" else q.bid)


def test_high_win_weight_prefers_higher_win_rate():
    ch, t = smile_chain()
    e = ch.expiry.isoformat()
    start = [("CE", "BUY", 1000.0, 1, e)]
    hi = make_opt(ch, t, Preferences(win_weight=0.95, max_loss=4000, robust=False)).run(start, 10, 3)["best"]["metrics"]
    lo = make_opt(ch, t, Preferences(win_weight=0.05, max_loss=4000, robust=False)).run(start, 10, 3)["best"]["metrics"]
    assert hi["win_rate"] >= lo["win_rate"]
    assert lo["edge"] >= hi["edge"]


def test_invalid_start_moves_to_valid_neighbour():
    ch, t = smile_chain()
    e = ch.expiry.isoformat()
    out = make_opt(ch, t, Preferences()).run([("CE", "SELL", 1020.0, 1, e)], 6, 3)  # naked short: not allowed
    assert out["start"]["score"] is None and out["best"] is not None
    assert not out["best"]["metrics"]["unlimited_loss"]
    assert out["steps"] and out["steps"][0]["score"] is not None


def test_optimize_endpoint():
    client = TestClient(create_app(manager_with(RecordedNSEProvider())))
    r = client.post("/api/strategy/optimize", json={"symbol": "NIFTY", "legs": [
        {"option_type": "CE", "action": "BUY", "strike": 25000}], "prefs": {"win_weight": 0.6}})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["start"]["score"] is not None and d["best"]["score"] >= d["start"]["score"]
    assert d["evaluated"] > 0 and d["source"] == "nse"
    assert client.post("/api/strategy/optimize", json={"symbol": "NIFTY", "legs": [], "search": "local"}).status_code == 400
