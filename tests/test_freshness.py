from datetime import date, datetime, timedelta, timezone

import pytest

from option_trader.models import ET, OptionQuote

from .helpers import DownProvider, RecordedProvider, manager_with

EXP = date(2026, 10, 27)


def test_price_uses_mid_when_last_trade_is_outside_spread():
    q = OptionQuote(100, "CE", EXP, ltp=10.0, bid=12.0, ask=12.4)
    assert q.ltp_stale and q.price == pytest.approx(12.2)
    q2 = OptionQuote(100, "CE", EXP, ltp=12.1, bid=12.0, ask=12.4)
    assert not q2.ltp_stale and q2.price == 12.1
    assert q2.spread_pct == pytest.approx(3.28, abs=0.01)
    assert OptionQuote(100, "CE", EXP, ltp=5).price == 5  # no book: LTP


def test_data_age_from_exchange_timestamp_and_live_tick():
    ch = RecordedProvider().get_option_chain("NDX")  # exchange time 2026-10-15 10:00:00 New York
    now = datetime(2026, 10, 15, 10, 1, tzinfo=ET)
    assert ch.data_age_seconds(now) == pytest.approx(60)
    ch.live_at = (now - timedelta(seconds=2)).isoformat()
    assert ch.data_age_seconds(now) == pytest.approx(2)


class StaleCboe(RecordedProvider):
    def get_option_chain(self, symbol, expiry=None):
        ch = super().get_option_chain(symbol, expiry)
        ch.exchange_timestamp = "2026-10-15 09:40:00"  # 20 minutes old
        return ch


class FreshTradier(RecordedProvider):
    name = "tradier"

    def get_option_chain(self, symbol, expiry=None):
        ch = super().get_option_chain(symbol, expiry)
        ch.source = "tradier"
        ch.exchange_timestamp = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S")
        return ch


def _market_open(monkeypatch, is_open=True):
    monkeypatch.setattr("option_trader.market_hours.us_session", lambda now: {"open": is_open})


def test_stale_snapshot_fails_over_to_fresher_source(monkeypatch):
    _market_open(monkeypatch)
    m = manager_with(StaleCboe(), FreshTradier())
    assert m.get_chain("NDX").source == "tradier"


def test_stale_snapshot_used_with_warning_if_nothing_fresher(monkeypatch):
    _market_open(monkeypatch)
    m = manager_with(StaleCboe(), DownProvider())
    ch = m.get_chain("NDX")
    assert ch.source == "cboe" and any("min old" in n for n in ch.notes)


def test_closed_market_accepts_last_traded(monkeypatch):
    _market_open(monkeypatch, False)
    m = manager_with(StaleCboe(), FreshTradier())
    ch = m.get_chain("NDX")
    assert ch.source == "cboe" and not any("min old" in n for n in ch.notes)


def test_pinned_source_is_never_swapped(monkeypatch):
    _market_open(monkeypatch)
    m = manager_with(StaleCboe(), FreshTradier())
    assert m.get_chain("NDX", source="cboe").source == "cboe"
