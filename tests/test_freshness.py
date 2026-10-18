from datetime import date, datetime, timedelta, timezone

import pytest

from option_trader.models import IST, OptionQuote

from .helpers import DownProvider, RecordedNSEProvider, manager_with

EXP = date(2026, 10, 27)


def test_price_uses_mid_when_last_trade_is_outside_spread():
    q = OptionQuote(100, "CE", EXP, ltp=10.0, bid=12.0, ask=12.4)
    assert q.ltp_stale and q.price == pytest.approx(12.2)
    q2 = OptionQuote(100, "CE", EXP, ltp=12.1, bid=12.0, ask=12.4)
    assert not q2.ltp_stale and q2.price == 12.1
    assert q2.spread_pct == pytest.approx(3.28, abs=0.01)
    assert OptionQuote(100, "CE", EXP, ltp=5).price == 5  # no book: LTP


def test_data_age_from_exchange_timestamp_and_live_tick():
    ch = RecordedNSEProvider().get_option_chain("NIFTY")  # exchange time 14-Oct-2026 15:30 IST
    now = datetime(2026, 10, 14, 15, 31, tzinfo=IST)
    assert ch.data_age_seconds(now) == pytest.approx(60)
    ch.live_at = (now - timedelta(seconds=2)).isoformat()
    assert ch.data_age_seconds(now) == pytest.approx(2)


class StaleNSE(RecordedNSEProvider):
    pass


class FreshAngel(RecordedNSEProvider):
    name = "angelone"

    def get_option_chain(self, symbol, expiry=None):
        ch = super().get_option_chain(symbol, expiry)
        ch.source = "angelone"
        ch.exchange_timestamp = datetime.now(IST).strftime("%d-%b-%Y %H:%M:%S")
        return ch


def _market_open(monkeypatch, is_open=True):
    monkeypatch.setattr("option_trader.market_hours.clock_session", lambda now, holidays=None: {"open": is_open})


def test_stale_snapshot_fails_over_to_fresher_source(monkeypatch):
    _market_open(monkeypatch)
    m = manager_with(StaleNSE(), FreshAngel())
    assert m.get_chain("NIFTY").source == "angelone"


def test_stale_snapshot_used_with_warning_if_nothing_fresher(monkeypatch):
    _market_open(monkeypatch)
    m = manager_with(StaleNSE(), DownProvider())
    ch = m.get_chain("NIFTY")
    assert ch.source == "nse" and any("min old" in n for n in ch.notes)


def test_closed_market_accepts_last_traded(monkeypatch):
    _market_open(monkeypatch, False)
    m = manager_with(StaleNSE(), FreshAngel())
    ch = m.get_chain("NIFTY")
    assert ch.source == "nse" and not any("min old" in n for n in ch.notes)


def test_pinned_source_is_never_swapped(monkeypatch):
    _market_open(monkeypatch)
    m = manager_with(StaleNSE(), FreshAngel())
    assert m.get_chain("NIFTY", source="nse").source == "nse"
