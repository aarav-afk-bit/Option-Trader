from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from option_trader.analytics.chain import (
    analyze_chain,
    buildup,
    classify_buildup,
    iv_skew,
    max_pain,
    oi_levels,
    pcr,
)
from option_trader.app import create_app
from option_trader.models import OptionChain, OptionQuote, StrikeRow

from .helpers import RecordedNSEProvider, manager_with

EXP = date(2026, 10, 27)


def chain_from(spec, spot=100.0):
    """spec: list of (strike, call_oi, put_oi)"""
    rows = [StrikeRow(k, OptionQuote(k, "CE", EXP, ltp=1, oi=c), OptionQuote(k, "PE", EXP, ltp=1, oi=p))
            for k, c, p in spec]
    return OptionChain("T", EXP, spot, rows, "nse", datetime.now(timezone.utc))


def test_pcr_and_levels_on_recorded_chain():
    ch = RecordedNSEProvider().get_option_chain("NIFTY")
    p = pcr(ch)
    assert p["call_oi"] == 51234 + 91234 + 121234
    assert p["pcr_oi"] == pytest.approx((81234 + 101234 + 41234) / (51234 + 91234 + 121234), abs=1e-3)
    lv = oi_levels(ch)
    assert lv["resistance"][0]["strike"] == 25100
    assert lv["support"][0]["strike"] == 25000


def test_max_pain_hand_computed():
    # Payout at 90: puts 100*... compute: calls OI at 90/100/110 = 10/50/100, puts = 100/50/10
    ch = chain_from([(90, 10, 100), (100, 50, 50), (110, 100, 10)])
    assert max_pain(ch) == 100


def test_buildup_labels():
    q = OptionQuote(100, "CE", EXP, change=2, oi_change=10)
    assert classify_buildup(q) == "long build-up"
    q.change = -2
    assert classify_buildup(q) == "short build-up"
    q.oi_change = -5
    assert classify_buildup(q) == "long unwinding"
    q.change = 1
    assert classify_buildup(q) == "short covering"
    assert classify_buildup(OptionQuote(100, "CE", EXP)) is None


def test_buildup_net_on_recorded_chain():
    ch = RecordedNSEProvider().get_option_chain("NIFTY")
    bu = buildup(ch)
    # Calls are falling with rising OI (short build-up) and puts rising with OI (long build-up): bearish
    assert bu["net"] < 0


def test_iv_skew():
    ch = RecordedNSEProvider().get_option_chain("NIFTY")
    assert iv_skew(ch, 1) == pytest.approx(0.121 - 0.105)


def test_full_analytics_and_api():
    ch = RecordedNSEProvider().get_option_chain("NIFTY")
    a = analyze_chain(ch).to_dict()
    names = {s["name"] for s in a["signals"]}
    assert {"PCR (OI)", "Max pain", "OI range", "OI build-up", "IV skew"} <= names
    client = TestClient(create_app(manager_with(RecordedNSEProvider())))
    r = client.get("/api/analytics/chain", params={"symbol": "NIFTY"})
    assert r.status_code == 200 and r.json()["source"] == "nse"
