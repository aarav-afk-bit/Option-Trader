import math
from datetime import datetime, timezone

import pytest

from option_trader.pricing import bs_price, enrich_chain, greeks, implied_vol

from .helpers import RecordedNSEProvider


def test_textbook_values():
    # Hull, Options Futures & Other Derivatives: S=42, K=40, r=10%, sigma=20%, T=0.5
    assert bs_price(42, 40, 0.5, 0.10, 0.20, "CE") == pytest.approx(4.76, abs=0.01)
    assert bs_price(42, 40, 0.5, 0.10, 0.20, "PE") == pytest.approx(0.81, abs=0.01)


def test_put_call_parity():
    s, k, t, r, v = 25000, 25200, 0.05, 0.065, 0.13
    c = bs_price(s, k, t, r, v, "CE")
    p = bs_price(s, k, t, r, v, "PE")
    assert c - p == pytest.approx(s - k * math.exp(-r * t), abs=1e-6)


def test_greeks_signs_and_finite_difference():
    s, k, t, r, v = 100, 100, 0.25, 0.05, 0.3
    g = greeks(s, k, t, r, v, "CE")
    h = 0.01
    fd_delta = (bs_price(s + h, k, t, r, v, "CE") - bs_price(s - h, k, t, r, v, "CE")) / (2 * h)
    assert g.delta == pytest.approx(fd_delta, rel=1e-4)
    fd_vega = (bs_price(s, k, t, r, v + 0.01, "CE") - bs_price(s, k, t, r, v - 0.01, "CE")) / 2
    assert g.vega == pytest.approx(fd_vega, rel=1e-3)
    fd_theta = bs_price(s, k, t - 1 / 365, r, v, "CE") - bs_price(s, k, t, r, v, "CE")
    assert g.theta == pytest.approx(fd_theta, rel=2e-2)
    assert greeks(s, k, t, r, v, "PE").delta < 0 and g.gamma > 0


def test_expiry_behaviour():
    assert bs_price(110, 100, 0, 0.05, 0.2, "CE") == pytest.approx(10)
    assert greeks(90, 100, 0, 0.05, 0.2, "PE").delta == -1


def test_implied_vol_roundtrip_and_bounds():
    px = bs_price(25000, 25100, 0.04, 0.065, 0.14, "PE")
    assert implied_vol(px, 25000, 25100, 0.04, 0.065, "PE") == pytest.approx(0.14, abs=1e-5)
    assert implied_vol(0.5, 120, 100, 0.1, 0.05, "CE") is None  # below intrinsic
    assert implied_vol(0, 100, 100, 0.1, 0.05, "CE") is None


def test_enrich_only_fills_missing():
    ch = RecordedNSEProvider().get_option_chain("NIFTY")
    q = ch.quote(25000, "CE")
    q.iv = None
    src_iv = ch.quote(25000, "PE").iv
    enrich_chain(ch, 0.065, now=datetime(2026, 10, 14, 10, tzinfo=timezone.utc))
    assert q.iv is not None and 0.05 < q.iv < 0.5
    assert ch.quote(25000, "PE").iv == src_iv
    assert q.delta is not None and 0 < q.delta < 1


def test_enrich_note_not_duplicated():
    ch = RecordedNSEProvider().get_option_chain("NIFTY")
    for _ in range(3):
        ch.quote(25000, "CE").iv = None
        enrich_chain(ch, 0.065, now=datetime(2026, 10, 14, 10, tzinfo=timezone.utc))
    assert sum(n.startswith("IV solved") for n in ch.notes) == 1
