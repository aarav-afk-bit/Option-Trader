from fastapi.testclient import TestClient

from option_trader.app import create_app
from option_trader.optimizer.objective import Preferences
from option_trader.optimizer.search import run_global, template_seeds
from option_trader.strategy.templates import BUILTIN_TEMPLATES

from .helpers import RecordedNSEProvider, manager_with
from .test_opt_search import make_opt, smile_chain


def test_global_search_beats_or_matches_local():
    ch, t = smile_chain()
    e = ch.expiry.isoformat()
    start = [("CE", "BUY", 1000.0, 1, e)]
    prefs = Preferences(max_loss=5000)
    local = make_opt(ch, t, prefs).run(start, 15, 4)
    glob = run_global(make_opt(ch, t, prefs), start, BUILTIN_TEMPLATES, 15, 4)
    assert glob["best"]["score"] >= local["best"]["score"] - 1e-9
    assert glob["seeds"][0] == "Your legs" and len(glob["seeds"]) > 1


def test_global_from_nothing_records_seed_step():
    ch, t = smile_chain()
    out = run_global(make_opt(ch, t, Preferences(max_loss=5000)), [], BUILTIN_TEMPLATES, 10, 3)
    assert out["best"] is not None and out["start"]["legs"] == []
    assert out["steps"][0]["move"].startswith("Start from ")


def test_template_seeds_are_valid_and_unique():
    ch, t = smile_chain()
    opt = make_opt(ch, t, Preferences(max_loss=5000))
    seeds = template_seeds(opt, BUILTIN_TEMPLATES, top=5)
    assert 0 < len(seeds) <= 5 and len({s for s, _ in seeds}) == len(seeds)


def test_liquidity_filter_rejects_wide_spreads():
    ch, t = smile_chain()
    q = ch.quote(1050.0, "CE")
    q.bid, q.ask = q.ltp * 0.5, q.ltp * 1.5  # 100% spread
    opt = make_opt(ch, t, Preferences(max_spread_pct=10))
    out = opt.run([("CE", "BUY", 1000.0, 1, ch.expiry.isoformat())], 12, 4)
    assert all(l["strike"] != 1050.0 or l["option_type"] != "CE" for l in out["best"]["legs"])
    assert opt.rejected_illiquid > 0


def test_robust_score_blends_in_shifted_scenarios():
    ch, t = smile_chain()
    e = ch.expiry.isoformat()
    legs = (("CE", "BUY", 990.0, 1, e), ("CE", "SELL", 1010.0, 1, e))
    plain = make_opt(ch, t, Preferences(robust=False)).node(legs, [])
    robust = make_opt(ch, t, Preferences(robust=True)).node(legs, [])
    assert "robust_score" in robust.metrics
    assert robust.score == round(0.6 * plain.score + 0.4 * robust.metrics["robust_score"], 5)


def test_endpoint_global_with_empty_legs():
    client = TestClient(create_app(manager_with(RecordedNSEProvider())))
    r = client.post("/api/strategy/optimize", json={"symbol": "NIFTY", "legs": [],
                                                     "prefs": {"allow_unlimited_loss": True}})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["search"] == "global" and d["best"] is not None
