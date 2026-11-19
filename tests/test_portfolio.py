from datetime import date

import pytest
from fastapi.testclient import TestClient

from option_trader.app import create_app
from option_trader.portfolio.store import PortfolioStore, exit_price, mark_to_market
from option_trader.errors import DataUnavailableError
from option_trader.models import OptionQuote

from .helpers import RecordedProvider, manager_with


def chain_for(exp=None):
    return RecordedProvider().get_option_chain("NDX", exp)


LEGS = [{"option_type": "CE", "action": "BUY", "strike": 25000, "lots": 2},
        {"option_type": "CE", "action": "SELL", "strike": 25100}]


def test_open_prices_from_live_chain(tmp_path):
    st = PortfolioStore(tmp_path / "p.json")
    pos = st.open("Bull", chain_for, LEGS, fill="market")
    assert [l["entry"] for l in pos["legs"]] == [145.5, 91.5]  # pay ask, receive bid
    assert pos["lot_size"] == 100 and pos["source"] == "cboe"
    assert st.list()[0]["id"] == pos["id"]


def test_mark_and_close(tmp_path, monkeypatch):
    st = PortfolioStore(tmp_path / "p.json")
    pos = st.open("Bull", chain_for, LEGS, fill="ltp")
    m = mark_to_market(pos, chain_for)
    # marks at bid/ask mid: 145.0 and 92.0 -> (145-145)*200 - (92-92)*100 = 0
    assert m["pnl"] == pytest.approx(0)
    monkeypatch.setattr("option_trader.portfolio.store.date", _FixedDate)
    closed = st.close(pos["id"], chain_for, fill="market")
    # exit: sell 25000CE at bid 144.5, buy back 25100CE at ask 92.5
    assert closed["realized_pnl"] == pytest.approx((144.5 - 145.0) * 200 - (92.5 - 92.0) * 100)
    with pytest.raises(ValueError):
        st.close(pos["id"], chain_for)


def test_mark_unavailable_is_not_estimated(tmp_path):
    st = PortfolioStore(tmp_path / "p.json")
    pos = st.open("Bull", chain_for, LEGS)

    def down(exp=None):
        raise DataUnavailableError("NSE down")

    m = mark_to_market(pos, down)
    assert m["pnl"] is None and "NSE down" in m["errors"][0]


def test_exit_price_side():
    q = OptionQuote(100, "CE", date(2026, 10, 27), ltp=5, bid=4.9, ask=5.1)
    assert exit_price(q, "BUY", "market") == 4.9
    assert exit_price(q, "SELL", "market") == 5.1


def test_api_flow(tmp_path):
    client = TestClient(create_app(manager_with(RecordedProvider()),
                                   portfolio_store=PortfolioStore(tmp_path / "p.json")))
    r = client.post("/api/portfolio/open", json={"name": "S", "symbol": "NDX", "legs": LEGS})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    d = client.get("/api/portfolio").json()
    assert d["positions"][0]["pnl"] is not None and d["open_pnl"] is not None
    bad = client.post("/api/portfolio/open", json={"symbol": "NDX", "legs": [
        {"option_type": "CE", "action": "BUY", "strike": 1}]})
    assert bad.status_code == 422
    assert client.delete(f"/api/portfolio/{pid}").status_code == 200
    assert client.get("/api/portfolio").json()["positions"] == []


class _FixedDate(date):
    @classmethod
    def today(cls):
        return date(2026, 10, 15)
