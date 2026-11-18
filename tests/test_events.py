from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from option_trader.app import create_app
from option_trader.events import (
    EventsService, assignment_risk, earnings_move, historical_moves, parse_yahoo, reaction_expiry_ok,
)
from option_trader.models import Candle

from .helpers import manager_with
from .test_valuation_lab import EXP, SpyProvider, spy_chain

TODAY = date(2026, 10, 15)


class FakeTicker:
    def __init__(self, calendar=None, earnings=None, dividends=None, fail=False):
        self._cal, self._earn, self._div, self.fail = calendar, earnings, dividends, fail

    @property
    def calendar(self):
        if self.fail:
            raise ConnectionError("blocked")
        return self._cal

    def get_earnings_dates(self, limit=12):
        if self.fail:
            raise ConnectionError("blocked")
        return self._earn

    @property
    def dividends(self):
        if self.fail:
            raise ConnectionError("blocked")
        return self._div


def et(y, m, d, h):
    return pd.Timestamp(datetime(y, m, d, h, 0), tz="America/New_York")


def apple_like():
    idx = [et(2026, 10, 20, 16), et(2026, 7, 30, 16), et(2026, 4, 30, 16), et(2026, 1, 29, 7)]
    earnings = pd.DataFrame({"EPS Estimate": [1.6, 1.4, 1.5, 2.1]}, index=pd.DatetimeIndex(idx))
    divs = pd.Series([0.26, 0.26, 0.27, 0.27],
                     index=pd.DatetimeIndex([et(2026, 2, 9, 0), et(2026, 5, 11, 0), et(2026, 8, 10, 0), et(2026, 9, 1, 0)]))
    cal = {"Earnings Date": [date(2026, 10, 20)], "Ex-Dividend Date": date(2026, 10, 19), "Dividend Date": date(2026, 10, 26)}
    return FakeTicker(cal, earnings, divs)


def test_parse_yahoo_reads_dates_timing_and_dividends():
    ev = parse_yahoo("aapl", apple_like(), today=TODAY)
    assert ev.symbol == "AAPL" and ev.next_earnings == date(2026, 10, 20) and ev.earnings_confirmed
    assert ev.earnings_timing == "after close"
    assert ev.past_earnings == [date(2026, 7, 30), date(2026, 4, 30), date(2026, 1, 29)]
    assert ev.past_timing["2026-01-29"] == "before open"
    assert ev.ex_dividend == date(2026, 10, 19) and ev.dividend == pytest.approx(0.27) and ev.dividends_per_year == 4
    d = ev.to_dict()
    assert d["next_earnings"] == "2026-10-20" and d["error"] is None


def test_estimated_range_is_not_confirmed_and_failures_are_reported():
    t = FakeTicker({"Earnings Date": [date(2026, 10, 28), date(2026, 11, 2)]}, None, None)
    ev = parse_yahoo("MSFT", t, today=TODAY)
    assert ev.next_earnings == date(2026, 10, 28) and not ev.earnings_confirmed and ev.ex_dividend is None
    bad = parse_yahoo("MSFT", FakeTicker(fail=True), today=TODAY)
    assert bad.next_earnings is None and "blocked" in bad.error


def test_service_caches_and_skips_indices():
    calls = []

    def factory(sym):
        calls.append(sym)
        return apple_like()

    svc = EventsService(factory)
    assert svc.get("AAPL").next_earnings and svc.get("aapl").next_earnings
    assert calls == ["AAPL"]
    assert svc.get("SPX").source == "n/a" and calls == ["AAPL"]
    assert EventsService(lambda s: FakeTicker(fail=True)).get("XYZ").error


def test_reaction_expiry_rules():
    e = date(2026, 10, 20)
    assert reaction_expiry_ok(date(2026, 10, 20), e, "before open")
    assert not reaction_expiry_ok(date(2026, 10, 20), e, "after close")
    assert not reaction_expiry_ok(date(2026, 10, 20), e, None)
    assert reaction_expiry_ok(date(2026, 10, 23), e, "after close")


def test_earnings_move_from_term_structure():
    pre, post = spy_chain(date(2026, 10, 16)), spy_chain(EXP)
    for q in (post.quote(post.atm_strike, "CE"), post.quote(post.atm_strike, "PE")):
        q.iv = 0.40  # an earnings expiry: much higher IV than the one before
    m = earnings_move(post, pre)
    T = post.years_to_expiry()
    expected = (2 / 3.141592653589793) ** 0.5 * ((0.40 ** 2 - m["iv_pre"] ** 2) * T) ** 0.5 * 100
    assert m["event_move_pct"] == pytest.approx(expected, abs=0.006)
    assert m["straddle_move_pct"] > 0 and m["pre_expiry"] == "2026-10-16"
    assert earnings_move(post)["event_move_pct"] is None  # no earlier expiry: straddle bound only


def test_historical_moves_respect_timing():
    days = [date(2026, 7, 28) + timedelta(days=i) for i in range(5)]  # Tue..Sat; feed has Tue-Fri
    closes = [100, 101, 102, 110, 0]
    candles = [Candle(datetime(d.year, d.month, d.day, tzinfo=timezone.utc), c, c, c, c, 0) for d, c in zip(days[:4], closes[:4])]
    h = historical_moves([date(2026, 7, 30)], candles, {"2026-07-30": "after close"})
    assert h["moves"][0]["move_pct"] == pytest.approx((110 / 102 - 1) * 100, abs=0.01)
    h = historical_moves([date(2026, 7, 30)], candles, {"2026-07-30": "before open"})
    assert h["moves"][0]["move_pct"] == pytest.approx((102 / 101 - 1) * 100, abs=0.01)
    assert historical_moves([date(2026, 7, 31)], candles, {"2026-07-31": "after close"})["count"] == 0


def test_assignment_risk_flags_deep_itm_calls_only():
    ch = spy_chain()
    r = assignment_risk(ch, date(2026, 10, 19), 1.50)
    assert r["applies"] and r["exercise_by"] == "2026-10-16"
    strikes = [c["strike"] for c in r["contracts"]]
    assert strikes and max(strikes) < 600
    for c in r["contracts"]:
        assert c["put_price"] < 1.50
    assert not assignment_risk(ch, date(2026, 10, 30), 1.5)["applies"]  # ex-date after expiry
    ch.symbol = "SPX"
    assert not assignment_risk(ch, date(2026, 10, 19), 1.5)["applies"]  # European


def test_events_endpoint_and_recommendation_warnings():
    mgr = manager_with(SpyProvider())
    mgr.events = EventsService(lambda s: apple_like())
    client = TestClient(create_app(mgr))
    d = client.get("/api/analytics/events", params={"symbol": "SPY", "expiry": EXP.isoformat()}).json()
    assert d["events"]["next_earnings"] == "2026-10-20" and d["earnings_before_expiry"] is True
    assert d["implied"]["expiry"] == EXP.isoformat() and d["implied"]["pre_expiry"] == "2026-10-16"
    assert d["ex_div_before_expiry"] and d["assignment"]["applies"]
    warnings = client.get("/api/analytics/recommendations", params={"symbol": "SPY", "expiry": EXP.isoformat()}).json()["warnings"]
    assert any("IV crush" in w for w in warnings) and any("Ex-dividend" in w for w in warnings)
    # no events service: still answers
    mgr.events = None
    assert client.get("/api/analytics/events", params={"symbol": "SPY"}).json()["available"] is False
