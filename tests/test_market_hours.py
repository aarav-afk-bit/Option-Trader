from datetime import date, datetime

from fastapi.testclient import TestClient

from option_trader.app import create_app
from option_trader.market_hours import (
    clock_session,
    next_open,
    parse_nse_holidays,
    parse_nse_market_status,
)
from option_trader.models import IST

from .helpers import RecordedNSEProvider, manager_with


def ist(*a):
    return datetime(*a, tzinfo=IST)


def test_clock_session_states():
    assert clock_session(ist(2026, 10, 15, 10, 0))["open"] is True  # Thursday
    assert clock_session(ist(2026, 10, 15, 9, 5))["state"] == "pre-open"
    assert clock_session(ist(2026, 10, 15, 16, 0))["state"] == "closed"
    assert clock_session(ist(2026, 10, 17, 11, 0))["state"] == "weekend"
    hol = clock_session(ist(2026, 10, 15, 11, 0), [date(2026, 10, 15)])
    assert hol["state"] == "holiday" and not hol["open"]


def test_next_open_skips_weekend_and_holidays():
    assert next_open(ist(2026, 10, 16, 16, 0)) == ist(2026, 10, 19, 9, 15)  # Fri evening -> Mon
    assert next_open(ist(2026, 10, 16, 16, 0), [date(2026, 10, 19)]) == ist(2026, 10, 20, 9, 15)
    assert next_open(ist(2026, 10, 15, 8, 0)) == ist(2026, 10, 15, 9, 15)


def test_parse_nse_payloads():
    st = parse_nse_market_status({"marketState": [
        {"market": "Capital Market", "marketStatus": "Closed", "tradeDate": "15-Oct-2026",
         "last": 25012.35, "percentChange": 0.4, "marketStatusMessage": "Market is Closed"},
        {"market": "Currency", "marketStatus": "Open"}]})
    assert st["open"] is False and st["source"] == "nse" and st["nifty"] == 25012.35
    assert parse_nse_market_status({}) is None
    hol = parse_nse_holidays({"FO": [{"tradingDate": "21-Oct-2026", "description": "Diwali"},
                                     {"tradingDate": "bad"}]})
    assert hol == [date(2026, 10, 21)]


def test_market_endpoint_uses_nyse_calendar():
    client = TestClient(create_app(manager_with(RecordedNSEProvider())))
    m = client.get("/api/market").json()
    assert m["estimate"] is False and m["source"] == "nyse-calendar"
    assert m["open"] is True  # tests run on Thu 2026-10-15 10:00 ET
