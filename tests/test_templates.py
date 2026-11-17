from datetime import date

import pytest
from fastapi.testclient import TestClient

from option_trader.app import create_app
from option_trader.strategy.engine import analyze
from option_trader.strategy.templates import (
    BUILTIN_BY_ID,
    BUILTIN_TEMPLATES,
    ResolveError,
    StrategyTemplate,
    TemplateLeg,
    resolve,
)

from .helpers import RecordedProvider, manager_with


def wide_chain():
    """The recorded NDX chain, which has strikes 24900/25000/25100 with live prices."""
    return RecordedProvider().get_option_chain("NDX")


def test_builtin_ids_unique_and_valid():
    assert len(BUILTIN_BY_ID) == len(BUILTIN_TEMPLATES) >= 25
    for t in BUILTIN_TEMPLATES:
        assert t.builtin and t.legs


def test_resolve_uses_listed_strikes_and_live_prices():
    ch = wide_chain()
    t = StrategyTemplate("x", "Bull call", [TemplateLeg("CE", "BUY", 0), TemplateLeg("CE", "SELL", 1)])
    st = resolve(t, ch)
    assert [leg.strike for leg in st.legs] == [25000, 25100]
    assert [leg.premium for leg in st.legs] == [145.0, 92.0]
    assert st.lot_size == 100
    st2 = resolve(t, ch, lots=3, atm_shift=-1)
    assert [leg.strike for leg in st2.legs] == [24900, 25000]
    assert st2.legs[0].lots == 3


def test_resolve_out_of_range():
    with pytest.raises(ResolveError):
        resolve(BUILTIN_BY_ID["iron_condor"], wide_chain())


def test_calendar_needs_later_chain():
    ch = wide_chain()
    with pytest.raises(ResolveError):
        resolve(BUILTIN_BY_ID["long_call_calendar"], ch)
    later = RecordedProvider().get_option_chain("NDX", date(2026, 11, 3))
    st = resolve(BUILTIN_BY_ID["long_call_calendar"], ch, chain_for_offset=lambda n: later)
    assert st.legs[1].expiry == date(2026, 11, 3) and st.legs[1].premium == 260.0


def test_every_single_strike_builtin_analyses():
    ch = wide_chain()
    for t in BUILTIN_TEMPLATES:
        if any(abs(leg.offset) > 1 or leg.expiry_offset for leg in t.legs):
            continue
        res = analyze(resolve(t, ch), ch.spot)
        assert res["prices"] and res["pnl_expiry"]


def test_template_validation():
    with pytest.raises(ValueError):
        TemplateLeg("XX", "BUY")
    with pytest.raises(ValueError):
        StrategyTemplate("a", "A", [])
    d = BUILTIN_BY_ID["iron_condor"].to_dict()
    assert StrategyTemplate.from_dict(d) == BUILTIN_BY_ID["iron_condor"]


def test_template_api():
    client = TestClient(create_app(manager_with(RecordedProvider())))
    names = [t["id"] for t in client.get("/api/strategy/templates").json()]
    assert "iron_condor" in names
    r = client.post("/api/strategy/templates/long_straddle/build", json={"symbol": "NDX"})
    assert r.status_code == 200, r.text
    assert r.json()["net_premium"] == pytest.approx(-(145 + 128) * 100)
    assert client.post("/api/strategy/templates/iron_condor/build", json={"symbol": "NDX"}).status_code == 422
    assert client.post("/api/strategy/templates/nope/build", json={"symbol": "NDX"}).status_code == 404
