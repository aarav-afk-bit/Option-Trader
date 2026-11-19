import math

import pytest

from option_trader.pricing.valuation import (
    HestonParams, bachelier, baw, binomial_crr, bjerksund_stensland, black76, bsm, calibrate_heston, heston,
    mc_european, mc_heston, mc_lsm, merton_jump)

S, K, T, r, q, v = 100.0, 100.0, 0.5, 0.05, 0.0, 0.25


def test_bsm_reference_and_parity():
    assert bsm(42, 40, 0.5, 0.10, 0.0, 0.20, "CE") == pytest.approx(4.7594, abs=1e-3)  # Hull
    c, p = bsm(S, K, T, r, 0.02, v, "CE"), bsm(S, K, T, r, 0.02, v, "PE")
    assert c - p == pytest.approx(S * math.exp(-0.02 * T) - K * math.exp(-r * T), abs=1e-9)


def test_black76_equals_bsm_on_forward():
    F = S * math.exp((r - 0.02) * T)
    for kind in ("CE", "PE"):
        assert black76(F, K, T, r, v, kind) == pytest.approx(bsm(S, K, T, r, 0.02, v, kind), abs=1e-9)


def test_bachelier_parity_and_closeness_atm():
    F = S * math.exp((r - q) * T)
    c, p = bachelier(S, K, T, r, q, v, "CE"), bachelier(S, K, T, r, q, v, "PE")
    assert c - p == pytest.approx(math.exp(-r * T) * (F - K), abs=1e-9)
    assert c == pytest.approx(bsm(S, K, T, r, q, v, "CE"), rel=0.03)


def test_crr_converges_to_bsm_for_european():
    for kind in ("CE", "PE"):
        assert binomial_crr(S, 110, T, r, 0.01, v, kind, steps=800, american=False) == pytest.approx(
            bsm(S, 110, T, r, 0.01, v, kind), abs=0.005)


def test_american_vs_european():
    euro_put = bsm(S, K, T, r, 0.0, v, "PE")
    am_put = binomial_crr(S, K, T, r, 0.0, v, "PE", steps=1500)
    assert am_put > euro_put + 0.1  # early exercise premium for puts
    # no dividends: an American call is worth the European call
    assert binomial_crr(S, K, T, r, 0.0, v, "CE", steps=1500) == pytest.approx(bsm(S, K, T, r, 0.0, v, "CE"), abs=0.01)


@pytest.mark.parametrize("S_,K_,T_,r_,q_,v_,kind", [
    (100, 100, 0.5, 0.05, 0.0, 0.25, "PE"),
    (90, 100, 1.0, 0.08, 0.0, 0.30, "PE"),
    (110, 100, 0.25, 0.04, 0.06, 0.20, "CE"),  # dividend-paying call: early exercise matters
    (100, 95, 2.0, 0.06, 0.03, 0.35, "PE"),
])
def test_american_approximations_match_a_fine_tree(S_, K_, T_, r_, q_, v_, kind):
    ref = binomial_crr(S_, K_, T_, r_, q_, v_, kind, steps=3000)
    assert baw(S_, K_, T_, r_, q_, v_, kind) == pytest.approx(ref, rel=0.015, abs=0.01)
    bs = bjerksund_stensland(S_, K_, T_, r_, q_, v_, kind)
    assert bs == pytest.approx(ref, rel=0.012, abs=0.01) and bs <= ref + 0.01  # a lower bound


def test_monte_carlo_european_within_error():
    for kind in ("CE", "PE"):
        res = mc_european(S, 105, T, r, 0.01, v, kind, paths=200_000)
        exact = bsm(S, 105, T, r, 0.01, v, kind)
        assert abs(res.price - exact) < 4 * res.stderr and res.stderr < 0.02


def test_lsm_american_put():
    ref = binomial_crr(S, K, T, r, 0.0, v, "PE", steps=2000)
    res = mc_lsm(S, K, T, r, 0.0, v, "PE", paths=60_000, steps=50)
    assert res.price == pytest.approx(ref, rel=0.02)


def test_merton_reduces_to_bsm_without_jumps_and_adds_value_with_them():
    assert merton_jump(S, K, T, r, q, v, "CE", lam=0.0) == pytest.approx(bsm(S, K, T, r, q, v, "CE"), abs=1e-9)
    assert merton_jump(S, 80, T, r, q, v, "PE", lam=1.0, mu_j=-0.1, sigma_j=0.15) > bsm(S, 80, T, r, q, v, "PE")


def test_heston_formula_bsm_limit_and_mc_agreement():
    flat = HestonParams(v0=0.0625, kappa=5.0, theta=0.0625, xi=1e-3, rho=0.0)
    assert heston(S, K, T, r, q, "CE", flat) == pytest.approx(bsm(S, K, T, r, q, 0.25, "CE"), abs=2e-3)
    p = HestonParams(v0=0.04, kappa=2.0, theta=0.05, xi=0.6, rho=-0.7)
    for kind, k in (("CE", 110), ("PE", 90)):
        exact = heston(S, k, T, r, q, kind, p)
        sim = mc_heston(S, k, T, r, q, kind, p, paths=80_000, steps=100)
        assert abs(sim.price - exact) < 4 * sim.stderr + 0.02


def test_heston_calibration_recovers_a_skew():
    true = HestonParams(v0=0.04, kappa=1.5, theta=0.05, xi=0.5, rho=-0.6)
    from option_trader.pricing import implied_vol

    strikes = [85, 90, 95, 100, 105, 110, 115]
    ivs = [implied_vol(heston(S, k, 0.25, 0.04, 0.0, "CE" if k >= S else "PE", true), S, k, 0.25, 0.04,
                       "CE" if k >= S else "PE") for k in strikes]
    fit, rmse = calibrate_heston(S, 0.25, 0.04, 0.0, strikes, ivs)
    assert rmse < 0.002  # within 0.2 vol points
    assert fit.rho < 0  # recovers the negative skew
