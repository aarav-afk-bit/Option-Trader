import json
from datetime import date
from pathlib import Path

import pytest

from option_trader.errors import DataUnavailableError
from option_trader.providers.nse import NSEProvider, parse_chain_payload, parse_lot_sizes

FIX = Path(__file__).parent / "fixtures"
EXP = date(2026, 10, 27)


def load(name):
    return json.loads((FIX / name).read_text())


def test_parse_legacy_filters_expiry():
    ch = parse_chain_payload(load("nse_legacy.json"), "NIFTY", EXP)
    assert ch.source == "nse"
    assert ch.spot == pytest.approx(25012.35)
    assert ch.strikes == [24900, 25000, 25100]
    q = ch.quote(25000, "PE")
    assert q.ltp == 128.0 and q.bid == 127.5 and q.ask == 128.5
    assert q.oi == 101234 and q.oi_change == 9200
    assert q.iv == pytest.approx(0.115)
    assert ch.expiries == [date(2026, 10, 27), date(2026, 11, 3)]
    assert ch.exchange_timestamp == "14-Oct-2026 15:30:00"


def test_parse_v3_field_names():
    ch = parse_chain_payload(load("nse_v3.json"), "RELIANCE", EXP)
    q = ch.quote(1400, "CE")
    assert q.bid == 31.0 and q.ask == 31.4 and q.bid_qty == 500
    assert ch.atm_strike == 1400


def test_parse_rejects_empty():
    with pytest.raises(DataUnavailableError):
        parse_chain_payload({"records": {"data": [], "underlyingValue": 100}}, "X", EXP)


def test_lot_sizes():
    lots = parse_lot_sizes((FIX / "fo_mktlots.csv").read_text())
    assert lots["NIFTY"] == 75
    assert lots["BANKNIFTY"] == 35
    assert lots["NEWCO"] == 1200


class FakeResp:
    def __init__(self, status=200, data=None, text=""):
        self.status_code = status
        self._data = data
        self.text = text

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data

    def raise_for_status(self):
        if self.status_code != 200:
            import requests

            raise requests.HTTPError(str(self.status_code))


class FakeSession:
    """Replays canned NSE responses keyed by URL path."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        for path, resp in self.routes.items():
            if url.endswith(path):
                return resp(params) if callable(resp) else resp
        return FakeResp(404)


def test_provider_uses_v3_then_lot_size(monkeypatch):
    sess = FakeSession(
        {
            "/option-chain": FakeResp(200, text="<html>"),
            "/api/option-chain-contract-info": FakeResp(
                200, {"expiryDates": ["03-Nov-2026", "27-Oct-2026"]}
            ),
            "/api/option-chain-v3": FakeResp(200, load("nse_legacy.json")),
            "fo_mktlots.csv": FakeResp(200, text=(FIX / "fo_mktlots.csv").read_text()),
        }
    )
    p = NSEProvider(session=sess)
    monkeypatch.setattr("option_trader.providers.nse.date", _FixedDate)
    ch = p.get_option_chain("nifty")
    assert ch.expiry == EXP
    assert ch.lot_size == 75
    v3_call = [c for c in sess.calls if c[0].endswith("option-chain-v3")][0]
    assert v3_call[1] == {"type": "Indices", "symbol": "NIFTY", "expiry": "27-Oct-2026"}


def test_provider_falls_back_to_legacy(monkeypatch):
    sess = FakeSession(
        {
            "/option-chain": FakeResp(200, text="<html>"),
            "/api/option-chain-contract-info": FakeResp(404),
            "/api/option-chain-v3": FakeResp(404),
            "/api/option-chain-indices": FakeResp(200, load("nse_legacy.json")),
            "fo_mktlots.csv": FakeResp(500),
        }
    )
    monkeypatch.setattr("option_trader.providers.nse.time.sleep", lambda s: None)
    monkeypatch.setattr("option_trader.providers.nse.date", _FixedDate)
    ch = NSEProvider(session=sess).get_option_chain("NIFTY", EXP)
    assert ch.strikes == [24900, 25000, 25100]
    assert ch.lot_size is None and ch.notes


def test_provider_unknown_expiry(monkeypatch):
    sess = FakeSession(
        {
            "/option-chain": FakeResp(200, text=""),
            "/api/option-chain-contract-info": FakeResp(200, {"expiryDates": ["27-Oct-2026"]}),
        }
    )
    with pytest.raises(DataUnavailableError):
        NSEProvider(session=sess).get_option_chain("NIFTY", date(2026, 12, 1))


class _FixedDate(date):
    @classmethod
    def today(cls):
        return date(2026, 10, 15)
