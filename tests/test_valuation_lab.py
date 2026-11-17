import json
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from option_trader.app import create_app
from option_trader.errors import DataUnavailableError
from option_trader.pricing import lab
from option_trader.pricing.black_scholes import enrich_chain
from option_trader.providers.base import DataProvider
from option_trader.providers.cboe import build_chain

from .helpers import manager_with

FIX = Path(__file__).parent / "fixtures"
EXP = date(2026, 10, 23)


def spy_chain(expiry=EXP):
    ch = build_chain(json.loads((FIX / "cboe_SPY.json").read_text()), "SPY", expiry)
    return enrich_chain(ch, 0.04)


class SpyProvider(DataProvider):
    name = "cboe"

    def get_expiries(self, symbol):
        return [date(2026, 10, 16), EXP]

    def get_option_chain(self, symbol, expiry=None):
        if symbol.upper() != "SPY":
            raise DataUnavailableError("only SPY recorded")
        return build_chain(json.loads((FIX / "cboe_SPY.json").read_text()), "SPY", expiry or EXP)


def test_every_model_prices_a_live_contract_near_the_market():
    out = lab.value_contract(spy_chain(), 600.0, "PE", 0.04)
    assert out["exercise"] == "american" and out["inputs"]["sigma"] == pytest.approx(out["market"]["iv"])
    by = {m["key"]: m for m in out["models"]}
    assert set(by) == {"bsm", "black76", "bachelier", "merton", "heston", "binomial", "baw", "bs2002", "mc", "lsm", "mc_heston"}
    mid = out["market"]["mid"]
    assert by["bsm"]["price"] == pytest.approx(mid, abs=0.01)  # IV was solved from this price
    assert by["black76"]["price"] == pytest.approx(by["bsm"]["price"], abs=1e-6)
    for k in ("binomial", "baw", "bs2002", "lsm"):  # American >= European for a put
        assert by[k]["price"] >= by["bsm"]["price"] - 3 * by[k].get("stderr", 0.003)
    assert abs(by["mc"]["price"] - by["bsm"]["price"]) < 4 * by["mc"]["stderr"] + 1e-3
    assert by["mc"]["ci95"][0] < by["mc"]["price"] < by["mc"]["ci95"][1]
    assert by["heston"]["price"] == pytest.approx(mid, rel=0.05) and "Fitted to" in by["heston"]["note"]
    assert out["early_exercise_premium"] > 0 and out["american_iv"] < out["market"]["iv"]
    assert all(m["applies"] for m in out["models"])


def test_index_options_mark_american_models_as_not_applicable():
    ch = spy_chain()
    ch.symbol = "SPX"
    out = lab.value_contract(ch, 600.0, "CE", 0.04, mc_paths=5000)
    assert out["exercise"] == "european" and "american_iv" not in out
    assert {m["key"] for m in out["models"] if not m["applies"]} == {"binomial", "baw", "bs2002", "lsm"}


def test_heston_fit_reproduces_the_smile():
    sm = lab.heston_smile(spy_chain(), 0.04)
    assert sm["fitted_on"] >= 5 and sm["rmse_vol"] < 0.01
    for p in sm["points"]:
        assert p["heston_iv"] == pytest.approx(p["iv"], abs=0.02)


def test_errors_and_overrides():
    ch = spy_chain()
    with pytest.raises(lab.LabError):
        lab.value_contract(ch, 1234.0, "CE", 0.04)
    with pytest.raises(lab.LabError):
        lab.value_contract(ch, 600.0, "XX", 0.04)
    hi = lab.value_contract(ch, 600.0, "CE", 0.04, sigma=0.40, mc_paths=5000)
    assert hi["inputs"]["sigma_source"] == "your input"
    assert hi["models"][0]["price"] > hi["market"]["ask"] and not hi["models"][0]["in_spread"]


def test_convergence_approaches_the_closed_form():
    c = lab.convergence(spy_chain(), 600.0, "CE", 0.04)
    assert abs(c["tree"][-1]["price"] - c["bsm"]) < abs(c["tree"][0]["price"] - c["bsm"]) + 1e-9
    last = c["mc"][-1]
    assert last["lo"] <= c["bsm"] + 0.01 and last["hi"] >= c["bsm"] - 0.01


def test_api_endpoints():
    client = TestClient(create_app(manager_with(SpyProvider())))
    r = client.get("/api/valuation/contract", params={"symbol": "SPY", "strike": 600, "kind": "CE", "expiry": EXP.isoformat(), "paths": 5000})
    assert r.status_code == 200, r.text
    assert r.json()["inputs"]["rate"] > 0 and len(r.json()["models"]) == 11
    assert client.get("/api/valuation/smile", params={"symbol": "SPY", "expiry": EXP.isoformat()}).json()["heston"]["v0"] > 0
    assert client.get("/api/valuation/models").json()["models"][0]["key"] == "bsm"
    assert client.get("/api/valuation/contract", params={"symbol": "SPY", "strike": 600, "kind": "CE", "sigma": 9}).status_code == 422
    assert client.get("/api/valuation/contract", params={"symbol": "QQQ", "strike": 600, "kind": "CE"}).status_code == 503
    assert client.get("/api/valuation/convergence", params={"symbol": "SPY", "strike": 600, "kind": "PE", "expiry": EXP.isoformat()}).status_code == 200
    assert client.get("/api/chain", params={"symbol": "SPY", "expiry": EXP.isoformat()}).json()["exercise"] == "american"
