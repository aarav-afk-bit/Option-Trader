import json
import math
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from option_trader.app import create_app
from option_trader.rates import TreasuryCurve, YieldCurve, parse_curve_csv

from .helpers import RecordedProvider, manager_with

CSV = (Path(__file__).parent / "fixtures" / "treasury_curve.csv").read_text()


def test_parse_latest_row():
    c = parse_curve_csv(CSV)
    assert c.as_of == date(2026, 10, 14) and c.points[0] == (pytest.approx(1 / 12), pytest.approx(0.0402))
    assert len(c.points) == 14


def test_interpolation_and_continuous_conversion():
    c = parse_curve_csv(CSV)
    assert c.yield_at(0.01) == pytest.approx(0.0402)  # flat below 1 month
    assert c.yield_at(0.75) == pytest.approx(0.0385 + (0.0370 - 0.0385) * 0.5)
    assert c.rate(1.0) == pytest.approx(2 * math.log(1 + 0.037 / 2))
    assert c.rate(40) == pytest.approx(2 * math.log(1 + 0.045 / 2))


class Resp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status

    def raise_for_status(self):
        if self.status_code != 200:
            import requests

            raise requests.HTTPError(str(self.status_code))


class Sess:
    def __init__(self, resp):
        self.resp, self.calls = resp, 0

    def get(self, url, timeout=None):
        self.calls += 1
        return self.resp


def test_fetch_once_a_day_and_cache_to_disk(tmp_path):
    s = Sess(Resp(CSV))
    tc = TreasuryCurve(tmp_path, 0.04, session=s)
    r, label = tc.rate(30 / 365)
    assert r == pytest.approx(2 * math.log(1 + 0.0402 / 2), rel=1e-3) and "Treasury curve 2026-10-14" in label
    tc.rate(1.0)
    assert s.calls == 1
    assert json.loads((tmp_path / "treasury_curve.json").read_text())["as_of"] == "2026-10-14"
    again = TreasuryCurve(tmp_path, 0.04, session=Sess(Resp("", 500)))
    assert again.rate(1.0)[1].startswith("US Treasury curve")  # served from today's cache


def test_fallback_when_unavailable(tmp_path):
    tc = TreasuryCurve(tmp_path, 0.045, session=Sess(Resp("", 503)))
    r, label = tc.rate(0.1)
    assert r == 0.045 and "fallback" in label and tc.last_error


def test_chain_gets_term_matched_rate_and_endpoint(tmp_path):
    m = manager_with(RecordedProvider())
    m.curve = TreasuryCurve(tmp_path, 0.04, session=Sess(Resp(CSV)))
    ch = m.get_chain("NDX")
    assert ch.rate == pytest.approx(2 * math.log(1 + 0.0402 / 2), rel=1e-3) and "Treasury" in ch.rate_source
    client = TestClient(create_app(m))
    d = client.get("/api/rates").json()
    assert d["available"] and d["as_of"] == "2026-10-14" and "1 year" in d["examples"]
    assert client.get("/api/chain", params={"symbol": "NDX"}).json()["rate_source"].startswith("US Treasury")
