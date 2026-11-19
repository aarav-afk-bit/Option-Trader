import pytest
from fastapi.testclient import TestClient

from option_trader.app import create_app
from option_trader.charges import ChargeRates, Fill, order_charges, strategy_costs
from option_trader.portfolio.store import PortfolioStore

from .helpers import RecordedProvider, manager_with

R = ChargeRates(commission_per_contract=0.65, commission_per_order=0.0, orf_per_contract=0.0229,
                occ_per_contract=0.02, taf_per_contract=0.00279, sec_fee_rate=0.0000278)


def test_sell_10_contracts_hand_computed():
    c = order_charges([Fill("SELL", 2.50, 1000)], R)  # 10 contracts, $2,500 proceeds
    assert c["commission"] == pytest.approx(6.50)
    assert c["orf"] == pytest.approx(0.23, abs=0.01)
    assert c["occ"] == pytest.approx(0.20)
    assert c["finra_taf"] == pytest.approx(0.03, abs=0.01)
    assert c["sec"] == pytest.approx(0.07, abs=0.01)
    assert c["total"] == pytest.approx(6.5 + 0.23 + 0.2 + 0.03 + 0.07, abs=0.02)


def test_buys_pay_no_taf_or_sec_fee_and_taf_is_capped():
    c = order_charges([Fill("BUY", 4.0, 500)], R)
    assert c["finra_taf"] == 0 and c["sec"] == 0 and c["commission"] == pytest.approx(3.25)
    big = order_charges([Fill("SELL", 1.0, 500_000)], R)  # 5,000 contracts
    assert big["finra_taf"] == pytest.approx(8.30)


def test_stock_legs_pay_regulatory_fees_on_sales_only():
    sell = order_charges([Fill("SELL", 600.0, 100, is_option=False)], R)
    assert sell["commission"] == 0 and sell["orf"] == 0
    assert sell["sec"] == pytest.approx(60000 * 0.0000278, abs=0.01) and sell["finra_taf"] == pytest.approx(0.02, abs=0.01)
    assert order_charges([Fill("BUY", 600.0, 100, is_option=False)], R)["total"] == 0


def test_per_order_ticket_and_defaults_from_env(monkeypatch):
    rates = ChargeRates(0.5, 1.0, 0, 0, 0, 0)
    assert order_charges([Fill("BUY", 1, 200), Fill("BUY", 1, 100)], rates)["commission"] == pytest.approx(3.5)
    monkeypatch.setenv("OPTION_TRADER_COMMISSION_PER_CONTRACT", "0")
    assert ChargeRates().commission_per_contract == 0 and ChargeRates().orf_per_contract == pytest.approx(0.0229)


def test_strategy_costs_round_trip_and_slippage():
    legs = [{"action": "BUY", "option_type": "CE", "lots": 2, "premium": 5.10, "bid": 5.05, "ask": 5.15},
            {"action": "SELL", "option_type": "CE", "lots": 2, "premium": 2.40, "bid": 2.35, "ask": 2.45}]
    c = strategy_costs(legs, 100, R)
    assert c["entry"]["commission"] == pytest.approx(2.60) and c["exit"]["commission"] == pytest.approx(2.60)
    # entry: SEC fee on the sold leg; exit: SEC fee on selling back the bought leg
    assert c["entry"]["sec"] == pytest.approx(round(480 * 0.0000278, 2))
    assert c["exit"]["sec"] == pytest.approx(round(1020 * 0.0000278, 2))
    assert c["round_trip"] == pytest.approx(c["entry"]["total"] + c["exit"]["total"])
    assert c["spread_slippage"] == pytest.approx((0.05 + 0.05) * 2 * 100 * 2)


def test_analyze_returns_net_figures():
    client = TestClient(create_app(manager_with(RecordedProvider())))
    res = client.post("/api/strategy/analyze", json={"symbol": "NDX", "legs": [
        {"option_type": "CE", "action": "BUY", "strike": 25000},
        {"option_type": "CE", "action": "SELL", "strike": 25100}]}).json()
    c = res["costs"]
    assert c["net_max_profit"] == pytest.approx(res["max_profit"] - c["round_trip"])
    assert c["spread_slippage"] is not None and 2 < c["round_trip"] < 20
    assert set(c["entry"]) == {"commission", "orf", "occ", "finra_taf", "sec", "total"}


def test_portfolio_net_pnl(tmp_path):
    st = PortfolioStore(tmp_path / "p.json")
    chain_for = lambda exp=None: RecordedProvider().get_option_chain("NDX", exp)
    pos = st.open("S", chain_for, [{"option_type": "CE", "action": "BUY", "strike": 25000}])
    assert 0.65 < pos["entry_charges"]["total"] < 1
    from option_trader.portfolio.store import mark_to_market

    m = mark_to_market(pos, chain_for)
    assert m["net_pnl"] == pytest.approx(m["pnl"] - m["charges_total"])
