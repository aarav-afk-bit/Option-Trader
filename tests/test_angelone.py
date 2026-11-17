import json
from datetime import date

import pytest

from option_trader.config import Settings
from option_trader.errors import ProviderNotConfiguredError
from option_trader.providers import angelone
from option_trader.providers.angelone import AngelOneProvider, InstrumentMaster

EXP = date(2026, 10, 27)

MASTER = [
    {"token": "99926000", "symbol": "Nifty 50", "name": "NIFTY", "expiry": "", "strike": "0.000000",
     "lotsize": "1", "instrumenttype": "AMXIDX", "exch_seg": "NSE", "tick_size": "0.000000"},
    {"token": "2885", "symbol": "RELIANCE-EQ", "name": "RELIANCE", "expiry": "", "strike": "-1.000000",
     "lotsize": "1", "instrumenttype": "", "exch_seg": "NSE", "tick_size": "5.000000"},
    {"token": "41001", "symbol": "NIFTY27OCT2625000CE", "name": "NIFTY", "expiry": "27OCT2026",
     "strike": "2500000.000000", "lotsize": "75", "instrumenttype": "OPTIDX", "exch_seg": "NFO",
     "tick_size": "5.000000"},
    {"token": "41002", "symbol": "NIFTY27OCT2625000PE", "name": "NIFTY", "expiry": "27OCT2026",
     "strike": "2500000.000000", "lotsize": "75", "instrumenttype": "OPTIDX", "exch_seg": "NFO",
     "tick_size": "5.000000"},
    {"token": "41003", "symbol": "NIFTY27OCT2625100CE", "name": "NIFTY", "expiry": "27OCT2026",
     "strike": "2510000.000000", "lotsize": "75", "instrumenttype": "OPTIDX", "exch_seg": "NFO",
     "tick_size": "5.000000"},
    {"token": "41010", "symbol": "NIFTY27OCT2025000CE", "name": "NIFTY", "expiry": "27OCT2020",
     "strike": "2500000.000000", "lotsize": "75", "instrumenttype": "OPTIDX", "exch_seg": "NFO",
     "tick_size": "5.000000"},
]


def test_instrument_master_indexing():
    m = InstrumentMaster(MASTER)
    assert date(2026, 10, 27) in m.expiries("NIFTY")
    assert set(m.contracts("NIFTY", EXP)) == {(25000.0, "CE"), (25000.0, "PE"), (25100.0, "CE")}
    assert m.lot_sizes["NIFTY"] == 75
    assert m.equity_tokens["RELIANCE"] == "2885"


class Resp:
    def __init__(self, data, status=200):
        self.status_code = status
        self._data = data

    def json(self):
        return self._data

    def raise_for_status(self):
        pass


class FakeHTTP:
    def __init__(self):
        self.posts = []
        self.logins = 0

    def get(self, url, timeout=None):
        assert url == angelone.SCRIP_MASTER_URL
        return Resp(MASTER)

    def post(self, url, json=None, headers=None, timeout=None):
        self.posts.append((url, json, headers))
        if url.endswith(angelone.LOGIN_PATH):
            self.logins += 1
            assert json["clientcode"] == "C123" and len(json["totp"]) == 6
            return Resp({"status": True, "data": {"jwtToken": "jwt-abc"}})
        assert headers["Authorization"] == "Bearer jwt-abc"
        assert headers["X-PrivateKey"] == "key"
        if url.endswith(angelone.QUOTE_PATH):
            (exch, tokens), = json["exchangeTokens"].items()
            fetched = []
            for t in tokens:
                if t == "99926000":
                    fetched.append({"symbolToken": t, "ltp": 25012.35})
                else:
                    fetched.append({
                        "symbolToken": t, "tradingSymbol": f"SYM{t}", "ltp": 100.0 + int(t) % 10,
                        "tradeVolume": 5000, "opnInterest": 120000, "netChange": -2.5,
                        "depth": {"buy": [{"price": 99.5, "quantity": 750}],
                                  "sell": [{"price": 100.5, "quantity": 300}]},
                    })
            return Resp({"status": True, "data": {"fetched": fetched, "unfetched": []}})
        if url.endswith(angelone.GREEKS_PATH):
            assert json == {"name": "NIFTY", "expirydate": "27OCT2026"}
            return Resp({"status": True, "data": [
                {"strikePrice": "25000.0", "optionType": "CE", "delta": "0.52", "gamma": "0.0009",
                 "theta": "-12.1", "vega": "14.2", "impliedVolatility": "11.3"}]})
        raise AssertionError(url)


def settings(tmp_path, **kw):
    s = Settings()
    s.data_dir = tmp_path
    s.angel_api_key = kw.get("key", "key")
    s.angel_client_code = "C123"
    s.angel_pin = "1234"
    s.angel_totp_secret = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
    return s


def test_chain(tmp_path, monkeypatch):
    monkeypatch.setattr(angelone, "date", _FixedDate)
    http = FakeHTTP()
    p = AngelOneProvider(settings(tmp_path), session=http)
    ch = p.get_option_chain("NIFTY")
    assert ch.source == "angelone" and ch.expiry == EXP
    assert ch.spot == 25012.35 and ch.lot_size == 75
    c = ch.quote(25000, "CE")
    assert c.ltp == 101.0 and c.bid == 99.5 and c.ask == 100.5 and c.oi == 120000
    assert c.delta == pytest.approx(0.52) and c.iv == pytest.approx(0.113)
    assert ch.quote(25000, "PE").delta is None
    assert http.logins == 1
    assert (tmp_path / "angel_scrip_master.json").exists()


def test_relogin_on_token_error(tmp_path, monkeypatch):
    monkeypatch.setattr(angelone, "date", _FixedDate)
    http = FakeHTTP()
    orig = http.post
    state = {"failed": False}

    def flaky(url, json=None, headers=None, timeout=None):
        if url.endswith(angelone.QUOTE_PATH) and not state["failed"]:
            state["failed"] = True
            return Resp({"status": False, "message": "Invalid Token", "errorcode": "AG8001"}, 401)
        return orig(url, json=json, headers=headers, timeout=timeout)

    http.post = flaky
    p = AngelOneProvider(settings(tmp_path), session=http)
    assert p.get_spot("NIFTY") == 25012.35
    assert http.logins == 2


def test_not_configured(tmp_path):
    s = settings(tmp_path, key="")
    p = AngelOneProvider(s, session=FakeHTTP())
    assert not p.is_configured()
    with pytest.raises(ProviderNotConfiguredError):
        p.get_spot("NIFTY")


class _FixedDate(date):
    @classmethod
    def today(cls):
        return date(2026, 10, 15)
