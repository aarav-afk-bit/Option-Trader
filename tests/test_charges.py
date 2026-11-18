import pytest
from fastapi.testclient import TestClient

from option_trader.app import create_app
from option_trader.charges import ChargeRates, Fill, order_charges, strategy_costs
from option_trader.portfolio.store import PortfolioStore

from .helpers import RecordedProvider, manager_with

R = ChargeRates(brokerage_per_order=20, stt_sell=0.001, exchange_txn=0.0003503, sebi=0.000001,
                stamp_buy=0.00003, gst=0.18)


def test_sell_side_charges_hand_computed():
    c = order_charges([Fill("SELL", 100.0, 75)], R)  # turnover 7,500
    assert c["brokerage"] == 20
    assert c["stt"] == pytest.approx(7.5)
    assert c["exchange"] == pytest.approx(2.63, abs=0.01)
    assert c["stamp"] == 0
    assert c["gst"] == pytest.approx(0.18 * (20 + 2.62725 + 0.0075), abs=0.01)
    assert c["total"] == pytest.approx(20 + 7.5 + 2.63 + 0.01 + 4.07, abs=0.02)


def test_buy_side_pays_stamp_not_stt():
    c = order_charges([Fill("BUY", 200.0, 50)], R)  # turnover 10,000
    assert c["stt"] == 0 and c["stamp"] == pytest.approx(0.3)


def test_strategy_costs_round_trip_and_slippage():
    legs = [{"action": "BUY", "option_type": "CE", "lots": 1, "premium": 145.0, "bid": 144.5, "ask": 145.5},
            {"action": "SELL", "option_type": "CE", "lots": 1, "premium": 92.0, "bid": 91.5, "ask": 92.5}]
    c = strategy_costs(legs, 75, R)
    assert c["entry"]["brokerage"] == 40 and c["exit"]["brokerage"] == 40
    # entry: STT on the sold leg; exit: STT on selling back the bought leg
    assert c["entry"]["stt"] == pytest.approx(92 * 75 * 0.001, abs=0.01)
    assert c["exit"]["stt"] == pytest.approx(145 * 75 * 0.001, abs=0.01)
    assert c["round_trip"] == pytest.approx(c["entry"]["total"] + c["exit"]["total"])
    assert c["spread_slippage"] == pytest.approx((0.5 + 0.5) * 75 * 2)


def test_analyze_returns_net_figures():
    client = TestClient(create_app(manager_with(RecordedProvider())))
    res = client.post("/api/strategy/analyze", json={"symbol": "NDX", "legs": [
        {"option_type": "CE", "action": "BUY", "strike": 25000},
        {"option_type": "CE", "action": "SELL", "strike": 25100}]}).json()
    c = res["costs"]
    assert c["net_max_profit"] == pytest.approx(res["max_profit"] - c["round_trip"])
    assert c["spread_slippage"] is not None and c["round_trip"] > 80


def test_portfolio_net_pnl(tmp_path):
    st = PortfolioStore(tmp_path / "p.json")
    chain_for = lambda exp=None: RecordedProvider().get_option_chain("NDX", exp)
    pos = st.open("S", chain_for, [{"option_type": "CE", "action": "BUY", "strike": 25000}])
    assert pos["entry_charges"]["total"] > 20
    from option_trader.portfolio.store import mark_to_market

    m = mark_to_market(pos, chain_for)
    assert m["net_pnl"] == pytest.approx(m["pnl"] - m["charges_total"])
