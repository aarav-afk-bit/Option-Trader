from datetime import date

import pytest

from option_trader.errors import DataUnavailableError
from option_trader.providers.angelone import InstrumentMaster
from option_trader.providers.nse import NSEProvider, parse_futures_quote

from .helpers import RecordedNSEProvider, manager_with


class Resp:
    def __init__(self, status=200, data=None, ctype="application/json", headers=None):
        self.status_code = status
        self._data = data
        self.headers = {"Content-Type": ctype, **(headers or {})}
        self.text = ""

    def json(self):
        if self._data is None:
            raise ValueError("not json")
        return self._data


class Seq:
    """Session replaying a sequence of responses for the API path; warm-up page always OK."""

    def __init__(self, seq):
        self.seq = list(seq)
        self.headers = {}
        self.warmups = 0
        self.api_calls = 0

    def get(self, url, params=None, timeout=None):
        if url.endswith("/option-chain"):
            self.warmups += 1
            return Resp(200, None, "text/html")
        self.api_calls += 1
        return self.seq.pop(0)


def provider(seq):
    p = NSEProvider(session=Seq(seq))
    p._sleep = lambda s: None
    return p


def test_rewarms_after_block_page_then_succeeds():
    p = provider([Resp(200, None, "text/html"), Resp(403), Resp(200, {"ok": 1})])
    assert p._get_json("/api/x") == {"ok": 1}
    assert p._session.warmups == 3  # initial + after HTML page + after 403


def test_backoff_honours_retry_after():
    waits = []
    p = provider([Resp(429, headers={"Retry-After": "2"}), Resp(200, {"ok": 1})])
    p._sleep = waits.append
    assert p._get_json("/api/x") == {"ok": 1}
    assert 2 <= waits[0] <= 2.3


def test_404_fails_fast_and_exhaustion_reports_attempts():
    p = provider([Resp(404)])
    with pytest.raises(DataUnavailableError, match="HTTP 404"):
        p._get_json("/api/x")
    assert p._session.api_calls == 1
    p = provider([Resp(503)] * 4)
    with pytest.raises(DataUnavailableError, match="after 4 attempts"):
        p._get_json("/api/x")


def test_parse_futures_quote():
    payload = {"stocks": [
        {"metadata": {"instrumentType": "Index Futures", "expiryDate": "27-Oct-2026", "lastPrice": 25101.5}},
        {"metadata": {"instrumentType": "Index Futures", "expiryDate": "24-Nov-2026", "lastPrice": 25230}},
        {"metadata": {"instrumentType": "Index Options", "expiryDate": "27-Oct-2026", "lastPrice": 145}},
    ]}
    assert parse_futures_quote(payload) == {date(2026, 10, 27): 25101.5, date(2026, 11, 24): 25230.0}


def test_angel_master_indexes_futures():
    m = InstrumentMaster([{"token": "53001", "symbol": "NIFTY27OCT26FUT", "name": "NIFTY", "expiry": "27OCT2026",
                           "strike": "-1", "lotsize": "75", "instrumenttype": "FUTIDX", "exch_seg": "NFO"}])
    assert m.futures["NIFTY"][date(2026, 10, 27)] == "53001"


class WithFutures(RecordedNSEProvider):
    def get_futures(self, symbol):
        return {date(2026, 10, 27): 25088.0}


def test_manager_prices_futures_legs_at_exchange_quote():
    ch = manager_with(WithFutures()).get_chain("NIFTY")
    assert ch.futures_ltp == 25088.0 and ch.fut_price == 25088.0
    plain = manager_with(RecordedNSEProvider()).get_chain("NIFTY")
    assert plain.futures_ltp is None and plain.fut_price is not None
