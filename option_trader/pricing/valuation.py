"""Option valuation models.

Every pricer takes the same inputs and returns a price per unit of underlying:

    S spot, K strike, T years to expiry, r risk-free rate (continuous), q dividend/carry yield,
    sigma volatility (annual, decimal), kind "CE"/"PE".

Closed form / semi-analytic
* ``bsm``                 Black-Scholes-Merton (European)
* ``black76``             Black-76 on a forward/futures price (European)
* ``bachelier``           normal (arithmetic) model; volatility given as lognormal-equivalent
* ``merton_jump``         Merton jump-diffusion (European), Poisson series
* ``heston``              Heston stochastic volatility (European), characteristic function
                          (Albrecher "little trap" form) on a Gauss-Legendre grid; ``heston_prices``
                          values a whole smile in one pass

American exercise
* ``binomial_crr``        Cox-Ross-Rubinstein tree, averaged over N and N+1 steps
* ``baw``                 Barone-Adesi & Whaley (1987) quadratic approximation
* ``bjerksund_stensland`` Bjerksund & Stensland (2002) two-step flat boundary

Monte Carlo (return price and standard error)
* ``mc_european``         GBM with antithetic variates and a terminal-price control variate
* ``mc_lsm``              Longstaff-Schwartz least-squares Monte Carlo (American)
* ``mc_heston``           Heston paths, full-truncation Euler (European)

``calibrate_heston`` fits Heston parameters to market implied volatilities.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np
from scipy import optimize
from scipy.stats import multivariate_normal, norm

N = norm.cdf


def _intrinsic(S: float, K: float, kind: str) -> float:
    return max(S - K, 0.0) if kind == "CE" else max(K - S, 0.0)


# ------------------------------------------------------------------------------------------
# Closed form
# ------------------------------------------------------------------------------------------
def bsm(S, K, T, r, q, sigma, kind) -> float:
    if T <= 0 or sigma <= 0:
        return math.exp(-r * max(T, 0)) * _intrinsic(S * math.exp((r - q) * max(T, 0)), K, kind)
    vs = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / vs
    d2 = d1 - vs
    if kind == "CE":
        return S * math.exp(-q * T) * N(d1) - K * math.exp(-r * T) * N(d2)
    return K * math.exp(-r * T) * N(-d2) - S * math.exp(-q * T) * N(-d1)


def black76(F, K, T, r, sigma, kind) -> float:
    """European option on a forward/futures price F."""
    if T <= 0 or sigma <= 0:
        return math.exp(-r * max(T, 0)) * _intrinsic(F, K, kind)
    vs = sigma * math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * sigma ** 2 * T) / vs
    d2 = d1 - vs
    df = math.exp(-r * T)
    return df * (F * N(d1) - K * N(d2)) if kind == "CE" else df * (K * N(-d2) - F * N(-d1))


def bachelier(S, K, T, r, q, sigma, kind) -> float:
    """Normal model on the forward; ``sigma`` is lognormal-equivalent (sigma_n = sigma * F)."""
    F = S * math.exp((r - q) * T)
    if T <= 0 or sigma <= 0:
        return math.exp(-r * max(T, 0)) * _intrinsic(F, K, kind)
    sn = sigma * F * math.sqrt(T)
    d = (F - K) / sn
    df = math.exp(-r * T)
    call = df * ((F - K) * N(d) + sn * norm.pdf(d))
    return call if kind == "CE" else call - df * (F - K)


def merton_jump(S, K, T, r, q, sigma, kind, lam=0.1, mu_j=-0.05, sigma_j=0.1, terms=60) -> float:
    """Merton (1976): lognormal jumps with intensity ``lam`` per year, mean log-jump ``mu_j``."""
    if T <= 0:
        return _intrinsic(S, K, kind)
    k = math.exp(mu_j + 0.5 * sigma_j ** 2) - 1
    lam_p = lam * (1 + k)
    price, w = 0.0, math.exp(-lam_p * T)
    for n in range(terms):
        if n:
            w *= lam_p * T / n
        sig_n = math.sqrt(sigma ** 2 + n * sigma_j ** 2 / T)
        r_n = r - lam * k + n * math.log(1 + k) / T
        price += w * bsm(S, K, T, r_n, q, sig_n, kind) * math.exp((r_n - r) * T)
        if w < 1e-14 and n > lam_p * T:
            break
    return price


@dataclass
class HestonParams:
    v0: float  # initial variance
    kappa: float  # mean-reversion speed
    theta: float  # long-run variance
    xi: float  # vol of vol
    rho: float  # spot/vol correlation

    def as_dict(self) -> dict:
        return {"v0": self.v0, "kappa": self.kappa, "theta": self.theta, "xi": self.xi, "rho": self.rho,
                "feller": 2 * self.kappa * self.theta - self.xi ** 2}


def _heston_cf(u, T, r, q, S, p: HestonParams):
    """Characteristic function of ln S_T (Albrecher et al. 'little trap' formulation)."""
    i = 1j
    a = p.kappa * p.theta
    b = p.kappa
    d = np.sqrt((p.rho * p.xi * i * u - b) ** 2 + p.xi ** 2 * (i * u + u ** 2))
    g = (b - p.rho * p.xi * i * u - d) / (b - p.rho * p.xi * i * u + d)
    e = np.exp(-d * T)
    C = (r - q) * i * u * T + a / p.xi ** 2 * ((b - p.rho * p.xi * i * u - d) * T - 2 * np.log((1 - g * e) / (1 - g)))
    D = (b - p.rho * p.xi * i * u - d) / p.xi ** 2 * ((1 - e) / (1 - g * e))
    return np.exp(C + D * p.v0 + i * u * math.log(S))


_GL_X, _GL_W = np.polynomial.legendre.leggauss(256)


def heston_prices(S, strikes, T, r, q, kinds, params: HestonParams, u_max: float = 250.0) -> np.ndarray:
    """Heston prices for many strikes at one expiry.

    The characteristic function does not depend on the strike, so it is evaluated once on a
    256-point Gauss-Legendre grid and reused for the whole smile (fast enough to calibrate live).
    """
    K = np.atleast_1d(np.asarray(strikes, float))
    kinds = [kinds] * len(K) if isinstance(kinds, str) else list(kinds)
    if T <= 0:
        return np.array([_intrinsic(S, k, kd) for k, kd in zip(K, kinds)])
    u = 0.5 * u_max * (_GL_X + 1.0) + 1e-10
    w = 0.5 * u_max * _GL_W
    cf2 = _heston_cf(u, T, r, q, S, params)
    cf1 = _heston_cf(u - 1j, T, r, q, S, params) / _heston_cf(-1j, T, r, q, S, params)
    phase = np.exp(-1j * np.outer(np.log(K), u)) / (1j * u)
    P1 = 0.5 + (phase * cf1).real @ w / math.pi
    P2 = 0.5 + (phase * cf2).real @ w / math.pi
    dq, dr = math.exp(-q * T), math.exp(-r * T)
    call = S * dq * P1 - K * dr * P2
    put = call - S * dq + K * dr
    out = np.where(np.array(kinds) == "CE", call, put)
    return np.maximum(out, 0.0)


def heston(S, K, T, r, q, kind, params: HestonParams) -> float:
    """Heston (1993) stochastic-volatility price of a European option."""
    return float(heston_prices(S, [K], T, r, q, [kind], params)[0])


# ------------------------------------------------------------------------------------------
# American exercise
# ------------------------------------------------------------------------------------------
def _crr(S, K, T, r, q, sigma, kind, steps, american) -> float:
    dt = T / steps
    u = math.exp(sigma * math.sqrt(dt))
    d = 1 / u
    p = (math.exp((r - q) * dt) - d) / (u - d)
    disc = math.exp(-r * dt)
    j = np.arange(steps + 1)
    prices = S * u ** (steps - j) * d ** j
    values = np.maximum(prices - K, 0) if kind == "CE" else np.maximum(K - prices, 0)
    for n in range(steps, 0, -1):
        values = disc * (p * values[:-1] + (1 - p) * values[1:])
        if american:
            prices = prices[:-1] / u
            exercise = prices - K if kind == "CE" else K - prices
            values = np.maximum(values, exercise)
    return float(values[0])


def binomial_crr(S, K, T, r, q, sigma, kind, steps=400, american=True) -> float:
    if T <= 0 or sigma <= 0:
        return _intrinsic(S, K, kind)
    # Averaging an even and an odd tree removes most of CRR's odd/even oscillation.
    return 0.5 * (_crr(S, K, T, r, q, sigma, kind, steps, american) + _crr(S, K, T, r, q, sigma, kind, steps + 1, american))


def baw(S, K, T, r, q, sigma, kind) -> float:
    """Barone-Adesi & Whaley (1987) American approximation."""
    if T <= 0 or sigma <= 0:
        return _intrinsic(S, K, kind)
    b = r - q
    euro = bsm(S, K, T, r, q, sigma, kind)
    if kind == "CE" and b >= r:
        return euro  # never optimal to exercise an American call early without dividends
    s2 = sigma ** 2
    M, Nn = 2 * r / s2, 2 * b / s2
    Kq = 1 - math.exp(-r * T)
    if kind == "CE":
        q2 = (-(Nn - 1) + math.sqrt((Nn - 1) ** 2 + 4 * M / Kq)) / 2

        def f(Sx):
            d1 = (math.log(Sx / K) + (b + 0.5 * s2) * T) / (sigma * math.sqrt(T))
            return Sx - K - bsm(Sx, K, T, r, q, sigma, "CE") - (1 - math.exp((b - r) * T) * N(d1)) * Sx / q2
        lo, hi = K, K * 2
        while f(hi) < 0 and hi < K * 1e4:
            hi *= 2
        Sstar = optimize.brentq(f, lo + 1e-12, hi)
        d1 = (math.log(Sstar / K) + (b + 0.5 * s2) * T) / (sigma * math.sqrt(T))
        A2 = Sstar / q2 * (1 - math.exp((b - r) * T) * N(d1))
        return euro + A2 * (S / Sstar) ** q2 if S < Sstar else S - K
    q1 = (-(Nn - 1) - math.sqrt((Nn - 1) ** 2 + 4 * M / Kq)) / 2

    def g(Sx):
        d1 = (math.log(Sx / K) + (b + 0.5 * s2) * T) / (sigma * math.sqrt(T))
        return K - Sx - bsm(Sx, K, T, r, q, sigma, "PE") + (1 - math.exp((b - r) * T) * N(-d1)) * Sx / q1
    lo = K * 1e-4
    Sstar = optimize.brentq(g, lo, K - 1e-12)
    d1 = (math.log(Sstar / K) + (b + 0.5 * s2) * T) / (sigma * math.sqrt(T))
    A1 = -Sstar / q1 * (1 - math.exp((b - r) * T) * N(-d1))
    return euro + A1 * (S / Sstar) ** q1 if S > Sstar else K - S


def _bs2002_call(S, K, T, r, b, sigma) -> float:
    if b >= r:  # no early exercise
        return bsm(S, K, T, r, r - b, sigma, "CE")
    s2 = sigma ** 2
    t1 = 0.5 * (math.sqrt(5) - 1) * T
    beta = (0.5 - b / s2) + math.sqrt((b / s2 - 0.5) ** 2 + 2 * r / s2)
    B_inf = beta / (beta - 1) * K
    B0 = max(K, r / (r - b) * K)
    h1 = -(b * t1 + 2 * sigma * math.sqrt(t1)) * K ** 2 / ((B_inf - B0) * B0)
    h2 = -(b * T + 2 * sigma * math.sqrt(T)) * K ** 2 / ((B_inf - B0) * B0)
    I1 = B0 + (B_inf - B0) * (1 - math.exp(h1))
    I2 = B0 + (B_inf - B0) * (1 - math.exp(h2))
    if S >= I2:
        return S - K
    a1 = (I1 - K) * I1 ** (-beta)
    a2 = (I2 - K) * I2 ** (-beta)

    def phi(S_, T_, g, H, I):
        lam = (-r + g * b + 0.5 * g * (g - 1) * s2) * T_
        d = -(math.log(S_ / H) + (b + (g - 0.5) * s2) * T_) / (sigma * math.sqrt(T_))
        kap = 2 * b / s2 + 2 * g - 1
        return math.exp(lam) * S_ ** g * (N(d) - (I / S_) ** kap * N(d - 2 * math.log(I / S_) / (sigma * math.sqrt(T_))))

    def M2(a, bb, rho):
        return float(multivariate_normal.cdf([a, bb], mean=[0, 0], cov=[[1, rho], [rho, 1]]))

    def psi(S_, T_, g, H, I2_, I1_, t1_):
        e1 = (math.log(S_ / I1_) + (b + (g - 0.5) * s2) * t1_) / (sigma * math.sqrt(t1_))
        e2 = (math.log(I2_ ** 2 / (S_ * I1_)) + (b + (g - 0.5) * s2) * t1_) / (sigma * math.sqrt(t1_))
        e3 = (math.log(S_ / I1_) - (b + (g - 0.5) * s2) * t1_) / (sigma * math.sqrt(t1_))
        e4 = (math.log(I2_ ** 2 / (S_ * I1_)) - (b + (g - 0.5) * s2) * t1_) / (sigma * math.sqrt(t1_))
        f1 = (math.log(S_ / H) + (b + (g - 0.5) * s2) * T_) / (sigma * math.sqrt(T_))
        f2 = (math.log(I2_ ** 2 / (S_ * H)) + (b + (g - 0.5) * s2) * T_) / (sigma * math.sqrt(T_))
        f3 = (math.log(I1_ ** 2 / (S_ * H)) + (b + (g - 0.5) * s2) * T_) / (sigma * math.sqrt(T_))
        f4 = (math.log(S_ * I1_ ** 2 / (H * I2_ ** 2)) + (b + (g - 0.5) * s2) * T_) / (sigma * math.sqrt(T_))
        rho = math.sqrt(t1_ / T_)
        lam = -r + g * b + 0.5 * g * (g - 1) * s2
        kap = 2 * b / s2 + 2 * g - 1
        return math.exp(lam * T_) * S_ ** g * (
            M2(-e1, -f1, rho) - (I2_ / S_) ** kap * M2(-e2, -f2, rho)
            - (I1_ / S_) ** kap * M2(-e3, -f3, -rho) + (I1_ / I2_) ** kap * M2(-e4, -f4, -rho))

    return (a2 * S ** beta - a2 * phi(S, t1, beta, I2, I2) + phi(S, t1, 1, I2, I2) - phi(S, t1, 1, I1, I2)
            - K * phi(S, t1, 0, I2, I2) + K * phi(S, t1, 0, I1, I2) + a1 * phi(S, t1, beta, I1, I2)
            - a1 * psi(S, T, beta, I1, I2, I1, t1) + psi(S, T, 1, I1, I2, I1, t1) - psi(S, T, 1, K, I2, I1, t1)
            - K * psi(S, T, 0, I1, I2, I1, t1) + K * psi(S, T, 0, K, I2, I1, t1))


def bjerksund_stensland(S, K, T, r, q, sigma, kind) -> float:
    """Bjerksund & Stensland (2002) American approximation, a tight lower bound (puts via put-call transformation)."""
    if T <= 0 or sigma <= 0:
        return _intrinsic(S, K, kind)
    b = r - q
    if kind == "CE":
        return max(_bs2002_call(S, K, T, r, b, sigma), _intrinsic(S, K, kind))
    return max(_bs2002_call(K, S, T, r - b, -b, sigma), _intrinsic(S, K, kind))


# ------------------------------------------------------------------------------------------
# Monte Carlo
# ------------------------------------------------------------------------------------------
@dataclass
class MCResult:
    price: float
    stderr: float
    paths: int

    @property
    def ci95(self) -> tuple[float, float]:
        return self.price - 1.96 * self.stderr, self.price + 1.96 * self.stderr

    def to_dict(self) -> dict:
        lo, hi = self.ci95
        return {"price": self.price, "stderr": self.stderr, "ci95": [lo, hi], "paths": self.paths}


def mc_european(S, K, T, r, q, sigma, kind, paths=200_000, seed: Optional[int] = 7) -> MCResult:
    """GBM terminal prices with antithetic variates and the discounted spot as a control variate."""
    rng = np.random.default_rng(seed)
    half = paths // 2
    z = rng.standard_normal(half)
    z = np.concatenate([z, -z])
    ST = S * np.exp((r - q - 0.5 * sigma ** 2) * T + sigma * math.sqrt(T) * z)
    pay = np.maximum(ST - K, 0) if kind == "CE" else np.maximum(K - ST, 0)
    disc = math.exp(-r * T)
    X = disc * pay
    Y = disc * ST  # E[Y] = S e^{-qT} exactly
    cov = np.cov(X, Y)
    beta = cov[0, 1] / cov[1, 1] if cov[1, 1] > 0 else 0.0
    adj = X - beta * (Y - S * math.exp(-q * T))
    # pair antithetic samples before the error estimate (they are not independent)
    paired = 0.5 * (adj[:half] + adj[half:])
    return MCResult(float(adj.mean()), float(paired.std(ddof=1) / math.sqrt(half)), len(adj))


def mc_lsm(S, K, T, r, q, sigma, kind, paths=40_000, steps=50, seed: Optional[int] = 7) -> MCResult:
    """Longstaff-Schwartz (2001): regress continuation values on ITM paths (quadratic basis)."""
    rng = np.random.default_rng(seed)
    dt = T / steps
    half = paths // 2
    z = rng.standard_normal((half, steps))
    z = np.concatenate([z, -z])
    logS = math.log(S) + np.cumsum((r - q - 0.5 * sigma ** 2) * dt + sigma * math.sqrt(dt) * z, axis=1)
    St = np.exp(logS)
    ex = (lambda s: np.maximum(s - K, 0)) if kind == "CE" else (lambda s: np.maximum(K - s, 0))
    cash = ex(St[:, -1])
    disc = math.exp(-r * dt)
    for t in range(steps - 2, -1, -1):
        cash *= disc
        itm = ex(St[:, t]) > 0
        if itm.sum() > 10:
            x = St[itm, t] / K
            A = np.vstack([np.ones_like(x), x, x ** 2]).T
            coef, *_ = np.linalg.lstsq(A, cash[itm], rcond=None)
            cont = A @ coef
            exercise = ex(St[itm, t])
            idx = np.where(itm)[0][exercise > cont]
            cash[idx] = ex(St[idx, t])
    cash *= disc
    price = max(float(cash.mean()), _intrinsic(S, K, kind))
    paired = 0.5 * (cash[:half] + cash[half:])
    return MCResult(price, float(paired.std(ddof=1) / math.sqrt(half)), len(cash))


def mc_heston(S, K, T, r, q, kind, params: HestonParams, paths=100_000, steps=100, seed: Optional[int] = 7) -> MCResult:
    """Heston paths with full-truncation Euler; antithetic normals for both drivers."""
    rng = np.random.default_rng(seed)
    half = paths // 2
    dt = T / steps
    lnS = np.full(2 * half, math.log(S))
    v = np.full(2 * half, params.v0)
    for _ in range(steps):
        z1 = rng.standard_normal(half)
        z2 = params.rho * z1 + math.sqrt(1 - params.rho ** 2) * rng.standard_normal(half)
        z1, z2 = np.concatenate([z1, -z1]), np.concatenate([z2, -z2])
        vp = np.maximum(v, 0)
        lnS += (r - q - 0.5 * vp) * dt + np.sqrt(vp * dt) * z1
        v += params.kappa * (params.theta - vp) * dt + params.xi * np.sqrt(vp * dt) * z2
    ST = np.exp(lnS)
    pay = math.exp(-r * T) * (np.maximum(ST - K, 0) if kind == "CE" else np.maximum(K - ST, 0))
    paired = 0.5 * (pay[:half] + pay[half:])
    return MCResult(float(pay.mean()), float(paired.std(ddof=1) / math.sqrt(half)), len(pay))


# ------------------------------------------------------------------------------------------
# Calibration
# ------------------------------------------------------------------------------------------
def calibrate_heston(S, T, r, q, strikes: list[float], ivs: list[float], weights: Optional[list[float]] = None,
                     max_evals: int = 200) -> tuple[HestonParams, float]:
    """Fit Heston to market IVs at one expiry by least squares on price differences in vol units."""
    K = np.asarray(strikes, float)
    iv = np.asarray(ivs, float)
    w = np.asarray(weights if weights is not None else np.ones_like(K), float)
    atm = float(iv[np.argmin(abs(K - S))])
    vegas = np.array([max(S * math.exp(-q * T) * norm.pdf((math.log(S / k) + (r - q + 0.5 * v * v) * T) / (v * math.sqrt(T)))
                          * math.sqrt(T), 1e-4) for k, v in zip(K, iv)])
    kinds = ["CE" if k >= S else "PE" for k in K]  # out-of-the-money side is the liquid one
    market = np.array([bsm(S, k, T, r, q, v, kd) for k, v, kd in zip(K, iv, kinds)])

    def resid(x):
        p = HestonParams(*x)
        model = heston_prices(S, K, T, r, q, kinds, p)
        return w * (model - market) / vegas

    x0 = [atm ** 2, 2.0, atm ** 2, 0.6, -0.6]
    lo = [1e-4, 0.05, 1e-4, 0.01, -0.99]
    hi = [2.0, 20.0, 2.0, 3.0, 0.99]
    sol = optimize.least_squares(resid, x0, bounds=(lo, hi), max_nfev=max_evals, x_scale="jac")
    rmse = float(math.sqrt(np.mean((sol.fun / np.maximum(w, 1e-9)) ** 2)))  # in vol units (decimal)
    return HestonParams(*sol.x), rmse


# ------------------------------------------------------------------------------------------
# Registry
# ------------------------------------------------------------------------------------------
@dataclass
class ModelInfo:
    key: str
    name: str
    family: str
    american: bool
    description: str


MODELS = [
    ModelInfo("bsm", "Black-Scholes-Merton", "closed form", False, "Lognormal prices, constant volatility; the market standard for European options."),
    ModelInfo("black76", "Black-76", "closed form", False, "Black-Scholes on the forward/futures price; used for index and futures options."),
    ModelInfo("bachelier", "Bachelier (normal)", "closed form", False, "Prices move by normal amounts; behaves better than lognormal for very short-dated or near-zero prices."),
    ModelInfo("merton", "Merton jump-diffusion", "closed form", False, "Adds sudden jumps (e.g. earnings, crashes) to Black-Scholes; fattens tails."),
    ModelInfo("heston", "Heston stochastic volatility", "semi-analytic", False, "Volatility itself moves and is correlated with price; reproduces the skew."),
    ModelInfo("binomial", "Binomial tree (CRR)", "lattice", True, "Step-by-step price tree that checks early exercise at every node."),
    ModelInfo("baw", "Barone-Adesi-Whaley", "approximation", True, "Fast analytic approximation of the American early-exercise premium."),
    ModelInfo("bs2002", "Bjerksund-Stensland 2002", "approximation", True, "Accurate analytic American approximation with a two-step exercise boundary."),
    ModelInfo("mc", "Monte Carlo (GBM)", "simulation", False, "Simulates many price paths; antithetic and control variates reduce noise."),
    ModelInfo("lsm", "Longstaff-Schwartz Monte Carlo", "simulation", True, "Monte Carlo with regression to decide early exercise along each path."),
    ModelInfo("mc_heston", "Heston Monte Carlo", "simulation", False, "Simulated paths with stochastic volatility; cross-checks the Heston formula."),
]
