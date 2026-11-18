import json
from datetime import date

import pytest

from option_trader.errors import DataUnavailableError, ProviderNotConfiguredError
from option_trader.live import TradierLiveHub
from option_trader.providers.tradier import TradierProvider, TradierStream, parse_chain, parse_event

EXP = date(2026, 10, 16)


def opt(sym, typ, k, last, bid, ask, size=100, oi=1000):
    return {"symbol": sym, "option_type": typ, "strike": k, "last": last, "bid": bid, "ask": ask,
            "bidsize": 10, "asksize": 12, "volume": 500, "open_interest": oi, "change": -0.1,
            "contract_size": size, "trade_date": 1792072800000,
            "greeks": {"delta": 0.5 if typ == "call" else -0.5, "gamma": 0.04, "theta": -0.3, "vega": 0.2, "mid_iv": 0.17}}


CHAIN = {"options": {"option": [
    opt("SPY261016C00600000", "call", 600.0, 2.10, 2.08, 2.12),
    opt("SPY261016P00600000", "put", 600.0, 1.90, 1.88, 1.92),
    opt("SPY261016C00602500", "call", 602.5, 1.05, 1.04, 1.07),
    opt("SPY1261016C00600000", "call", 600.0, 9.9, 9.8, 10.0, size=111),  # adjusted: skipped
]}}


def test_parse_chain():
    rows = {r.strike: r for r in parse_chain(CHAIN, "SPY", EXP)}
    assert rows[600.0].call.ltp == 2.10 and rows[600.0].call.symbol == "SPY261016C00600000"
    assert rows[600.0].put.iv == pytest.approx(0.17) and rows[600.0].put.bid_qty == 10
    assert rows[600.0].call.tick_ts is not None
    assert len(rows) == 2


class Resp:
    def __init__(self, status, data=None):
        self.status_code, self._data = status, data

    def json(self):
        return self._data


class Sess:
    def __init__(self, routes):
        self.routes, self.seen = routes, []

    def get(self, url, params=None, headers=None, timeout=None):
        self.seen.append((url, params, headers))
        for k, v in self.routes.items():
            if url.endswith(k):
                return v
        return Resp(404)


def provider(routes, token="TOKEN", sandbox=False):
    p = TradierProvider(token, sandbox, session=Sess(routes))
    p._sleep = lambda s: None
    return p


def test_provider_chain():
    p = provider({
        "/markets/options/expirations": Resp(200, {"expirations": {"date": ["2026-10-14", "2026-10-16", "2026-10-23"]}}),
        "/markets/options/chains": Resp(200, CHAIN),
        "/markets/quotes": Resp(200, {"quotes": {"quote": {"symbol": "SPY", "last": 600.4, "prevclose": 599.1}}}),
    })
    ch = p.get_option_chain("spy")
    assert ch.expiry == EXP and ch.expiries == [EXP, date(2026, 10, 23)]  # past expiry dropped
    assert ch.spot == 600.4 and ch.source == "tradier" and ch.lot_size == 100
    url, params, headers = p._http.seen[1]
    assert headers["Authorization"] == "Bearer TOKEN" and params["greeks"] == "true"
    assert url.startswith("https://api.tradier.com/v1")
    assert provider({}, sandbox=True).base.startswith("https://sandbox.tradier.com")


def test_errors():
    with pytest.raises(ProviderNotConfiguredError):
        TradierProvider("").get_expiries("SPY")
    with pytest.raises(DataUnavailableError, match="rejected the token"):
        provider({"/markets/options/expirations": Resp(401)}).get_expiries("SPY")
    with pytest.raises(DataUnavailableError, match="sandbox"):
        TradierProvider("t", sandbox=True).create_stream_session()


def test_stream_events_and_overlay():
    assert parse_event({"type": "quote", "symbol": "X", "bid": "1.1", "ask": "1.2", "bidsz": "5", "asksz": "6",
                        "biddate": "1792072800000", "askdate": "1792072801000"})["exchange_ts"] == 1792072801000
    assert parse_event({"type": "trade", "symbol": "X", "price": "1.15", "cvol": "900", "date": "1792072800000"})["ltp"] == 1.15
    assert parse_event({"type": "heartbeat"}) is None

    s = TradierStream(lambda: "sid")
    s.start = lambda: None  # no network thread in tests
    s.on_message("\n".join(json.dumps(m) for m in [
        {"type": "trade", "symbol": "SPY261016C00600000", "price": "2.40", "cvol": "1500", "date": "1792072800000"},
        {"type": "quote", "symbol": "SPY261016C00600000", "bid": "2.38", "ask": "2.42", "bidsz": "7", "asksz": "9",
         "biddate": "1792072800500", "askdate": "1792072800500"},
        {"type": "trade", "symbol": "SPY", "price": "601.10", "date": "1792072800600"},
    ]))
    assert s.version == 3 and s.tick("SPY261016C00600000")["ask"] == 2.42

    from option_trader.models import OptionChain, StrikeRow
    from option_trader.models import utcnow

    rows = parse_chain(CHAIN, "SPY", EXP)
    ch = OptionChain("SPY", EXP, 600.4, rows, "cboe", utcnow(), lot_size=100)
    hub = TradierLiveHub(provider({}), stream=s)
    assert hub.apply(ch) == 1
    q = ch.quote(600.0, "CE")
    assert q.ltp == 2.40 and q.bid == 2.38 and q.ask == 2.42 and q.volume == 1500
    assert ch.spot == 601.10 and ch.live_at and any("Tradier" in n for n in ch.notes)
    assert {"SPY", "SPY261016C00600000"} <= s._symbols


def test_manager_streams_with_production_token(monkeypatch):
    from option_trader.providers.manager import ProviderManager

    monkeypatch.setenv("TRADIER_TOKEN", "abc")
    monkeypatch.setenv("TRADIER_SANDBOX", "0")
    m = ProviderManager.from_settings()
    assert isinstance(m.live, TradierLiveHub) and m.providers[0].is_configured()
    monkeypatch.setenv("TRADIER_SANDBOX", "1")
    from option_trader import config

    config._settings = None
    assert ProviderManager.from_settings().live is None  # sandbox: delayed, no stream
