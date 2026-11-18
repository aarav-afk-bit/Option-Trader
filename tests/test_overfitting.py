"""Guards against the Heston smile fit overfitting the quotes.

The only model in the app with parameters fitted to market data is the Heston calibration in
the Valuation Lab (the other pricers take observed inputs only). These tests check that it
generalises: held-out strikes are priced about as well as fitted ones, it does not chase quote
noise, and parameters the quotes can't pin down are reported as such.
"""

import math
from datetime import date

import numpy as np
import pytest

from option_trader.pricing import lab
from option_trader.pricing import valuation as V
from option_trader.pricing.black_scholes import implied_vol

from .test_valuation_lab import spy_chain

R = 0.04


def heston_iv(S, T, q, p, Ks):
    kinds = ["CE" if k >= S else "PE" for k in Ks]
    px = V.heston_prices(S, list(Ks), T, R, q, kinds, p)
    return np.array([implied_vol(float(x), S, k, T, R, kd, q) or np.nan for x, k, kd in zip(px, Ks, kinds)])


def fit(S, T, q, K, iv, w=None):
    return V.calibrate_heston(S, T, R, q, list(K), list(iv), None if w is None else list(w), max_evals=150)[0]


def rmse(a, b):
    return float(np.sqrt(np.nanmean((np.asarray(a) - np.asarray(b)) ** 2)))


def smile(exp):
    ch = spy_chain(exp)
    pts = lab.smile_points(ch, R)
    K = np.array([p["strike"] for p in pts])
    iv = np.array([p["iv"] for p in pts])
    w = np.array([p["weight"] for p in pts])
    return ch.spot, ch.years_to_expiry(), ch.carry, K, iv, w


def test_leave_one_out_error_stays_close_to_in_sample_on_real_quotes():
    S, T, q, K, iv, w = smile(date(2026, 10, 23))
    in_sample = rmse(heston_iv(S, T, q, fit(S, T, q, K, iv, w), K), iv)
    held = []
    for i in range(len(K)):
        m = np.arange(len(K)) != i
        held.append(heston_iv(S, T, q, fit(S, T, q, K[m], iv[m], w[m]), K[~m])[0])
    out = rmse(held, iv)
    flat = rmse(np.full(len(K), iv[np.argmin(abs(K - S))]), iv)
    assert out < 2.0 * in_sample + 0.0005  # measured: 1.4x, the same as a 3-parameter quadratic
    assert out < 0.2 * flat  # and it genuinely explains the smile
    assert out < 0.001  # 0.1 vol pt, inside the ~0.08-pt half-spread of these quotes


def test_fit_near_the_money_extrapolates_to_the_wings():
    S, T, q, K, iv, w = smile(date(2026, 10, 23))
    m = np.abs(np.log(K / S)) < 0.012
    p = fit(S, T, q, K[m], iv[m], w[m])
    wing = rmse(heston_iv(S, T, q, p, K[~m]), iv[~m])
    quad = rmse(np.polyval(np.polyfit(np.log(K[m] / S), iv[m], 2), np.log(K[~m] / S)), iv[~m])
    assert wing < 0.5 * quad  # measured 0.04 vs 0.13 vol pts: structure, not curve fitting


def test_does_not_chase_quote_noise():
    truth = V.HestonParams(v0=0.04, kappa=2.0, theta=0.05, xi=0.6, rho=-0.7)
    S, q, T = 100.0, 0.01, 30 / 365
    K = np.round(np.exp(np.linspace(-0.1, 0.06, 26)) * S, 2)
    true_iv = heston_iv(S, T, q, truth, K)
    rng = np.random.default_rng(3)
    noise = 0.005
    tr, te = np.arange(len(K)) % 2 == 0, np.arange(len(K)) % 2 == 1
    ins, vs_truth = [], []
    for _ in range(4):
        noisy = true_iv + rng.normal(0, noise, len(K))
        p = fit(S, T, q, K[tr], noisy[tr])
        ins.append(rmse(heston_iv(S, T, q, p, K[tr]), noisy[tr]))
        vs_truth.append(rmse(heston_iv(S, T, q, p, K[te]), true_iv[te]))
    # An overfit model would push the in-sample error well below the noise; this one stays near it
    assert np.mean(ins) > 0.6 * noise
    # ...and lands closer to the true smile on unseen strikes than the noise itself
    assert np.mean(vs_truth) < 0.7 * noise


def test_unidentified_parameters_are_flagged_with_honest_errors():
    S, T, q, K, iv, w = smile(date(2026, 10, 23))
    p = fit(S, T, q, K, iv, w)
    assert p.identified["v0"] and not p.identified["kappa"] and not p.identified["theta"]
    d = p.as_dict()
    assert set(d["stderr"]) == {"v0", "kappa", "theta", "xi", "rho"} and "identified" in d
    # The reported uncertainty matches refits with mids redrawn inside the bid/ask (factor ~2)
    ch = spy_chain(date(2026, 10, 23))
    half = []
    for k in K:
        qt = ch.quote(k, "CE" if k >= ch.forward else "PE")
        vega = (V.bsm(S, k, T, R, q, qt.iv + 1e-4, qt.option_type) - V.bsm(S, k, T, R, q, qt.iv, qt.option_type)) / 1e-4
        half.append((qt.ask - qt.bid) / 2 / vega)
    rng = np.random.default_rng(5)
    rhos = [fit(S, T, q, K, iv + rng.uniform(-1, 1, len(K)) * np.array(half), w).rho for _ in range(10)]
    assert np.std(rhos) / 4 < p.stderr["rho"] < np.std(rhos) * 4


def test_lab_exposes_uncertainty():
    lab._HESTON_CACHE.clear()
    sm = lab.heston_smile(spy_chain(), R)
    assert "stderr" in sm["heston"] and sm["heston"]["identified"]["kappa"] is False
    assert math.isfinite(sm["heston"]["stderr"]["rho"])
