import json
from datetime import date, datetime
from pathlib import Path

import pytest

from option_trader.errors import DataUnavailableError
from option_trader.models import ET
from option_trader.providers.cboe import CboeProvider, build_chain, cboe_symbol, parse_occ, parse_payload

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text())


def test_occ_symbols():
    assert parse_occ("SPY261016C00600000") == ("SPY", date(2026, 10, 16), "CE", 600.0)
    assert parse_occ("SPXW261019P05987500") == ("SPXW", date(2026, 10, 19), "PE", 5987.5)
    assert parse_occ("BRK.B261016C00450000")[0] == "BRK.B"
    assert parse_occ("garbage") is None
    assert cboe_symbol("^gspc") == "_SPX" and cboe_symbol("spy") == "SPY"


def test_spy_chain_parsed_with_greeks_and_adjusted_root_skipped():
    payload = load("cboe_SPY.json")
    ch = build_chain(payload, "SPY", date(2026, 10, 16))
    assert ch.source == "cboe" and ch.currency == "USD" and ch.lot_size == 100
    assert ch.spot == 600.25 and ch.iv30 == 16.4
    assert ch.expiries == [date(2026, 10, 16), date(2026, 10, 23)]
    q = ch.quote(600.0, "CE")
    assert q.bid < q.ltp < q.ask and q.iv and q.delta and q.oi > 0 and q.symbol == "SPY261016C00600000"
    assert any("adjusted" in n for n in ch.notes)
    assert ch.expiry_datetime() == datetime(2026, 10, 16, 16, 0, tzinfo=ET)
    assert ch.exchange_time() == datetime(2026, 10, 15, 10, 0, 2, tzinfo=ET)


def test_spx_prefers_pm_weekly_root_and_marks_am_only_dates():
    payload = load("cboe_SPX.json")
    g = parse_payload(payload, "SPX")
    assert g["expiries"][date(2026, 10, 16)]["root"] == "SPXW"
    ch = build_chain(payload, "SPX", date(2026, 10, 16), g)
    assert any("SPX" in n and "also listed" in n for n in ch.notes)
    assert ch.expiry_datetime().hour == 16
    # if only the AM root existed, the contract would stop at the 9:30 open
    am_only = json.loads(json.dumps(payload))
    am_only["data"]["options"] = [o for o in am_only["data"]["options"] if not o["option"].startswith("SPXW")]
    am = build_chain(am_only, "SPX", date(2026, 10, 16))
    assert am.expiry_datetime() == datetime(2026, 10, 16, 9, 30, tzinfo=ET)


class Resp:
    def __init__(self, status, data=None):
        self.status_code, self._data = status, data

    def json(self):
        if self._data is None:
            raise ValueError
        return self._data


class Sess:
    def __init__(self, routes):
        self.routes, self.headers, self.calls = routes, {}, []

    def get(self, url, timeout=None):
        self.calls.append(url)
        for k, v in self.routes.items():
            if url.endswith(k):
                return v.pop(0) if isinstance(v, list) else v
        return Resp(404)


def provider(routes):
    p = CboeProvider(session=Sess(routes))
    p._sleep = lambda s: None
    return p


def test_provider_chain_expiries_cache_and_retry():
    p = provider({"/options/SPY.json": [Resp(502), Resp(200, load("cboe_SPY.json"))]})
    assert p.get_expiries("spy") == [date(2026, 10, 16), date(2026, 10, 23)]
    ch = p.get_option_chain("SPY", date(2026, 10, 23))
    assert ch.expiry == date(2026, 10, 23)
    assert len(p._session.calls) == 2  # one retry, then served from the 15 s cache
    with pytest.raises(DataUnavailableError):
        p.get_option_chain("SPY", date(2026, 12, 18))


def test_unknown_symbol_and_history():
    p = provider({"/charts/historical/SPY.json": Resp(200, load("cboe_SPY_history.json"))})
    with pytest.raises(DataUnavailableError, match="check the symbol"):
        p.get_expiries("NOPE")
    candles = p.get_history("SPY", "1y")
    assert len(candles) == 30 and candles[-1].close > candles[0].close


def test_manager_uses_cboe_by_default(monkeypatch):
    from option_trader.providers.manager import ProviderManager

    monkeypatch.delenv("OPTION_TRADER_PROVIDERS", raising=False)
    m = ProviderManager.from_settings()
    assert [p.name for p in m.providers] == ["cboe", "yahoo"]
