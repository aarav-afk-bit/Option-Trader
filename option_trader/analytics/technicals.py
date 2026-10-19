"""Technical indicators on the underlying's real daily price history.

Indicators: EMA 20/50 crossover, 200-day SMA trend, RSI(14), MACD(12,26,9), Bollinger
Bands(20,2), Supertrend(10,3), ADX(14) and 20-day historical volatility.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd

from ..models import Candle
from .signals import Signal


def to_frame(candles: list[Candle]) -> pd.DataFrame:
    df = pd.DataFrame(
        {"open": [c.open for c in candles], "high": [c.high for c in candles],
         "low": [c.low for c in candles], "close": [c.close for c in candles],
         "volume": [c.volume for c in candles]},
        index=pd.DatetimeIndex([c.ts for c in candles]),
    )
    return df.sort_index()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder's RSI: seeded with a simple average of the first ``n`` moves, then smoothed."""
    delta = close.diff().to_numpy()
    out = np.full(len(close), np.nan)
    if len(close) <= n:
        return pd.Series(out, index=close.index)
    gains = np.clip(delta, 0, None)
    losses = np.clip(-delta, 0, None)
    avg_g = gains[1 : n + 1].mean()
    avg_l = losses[1 : n + 1].mean()
    for i in range(n, len(close)):
        if i > n:
            avg_g = (avg_g * (n - 1) + gains[i]) / n
            avg_l = (avg_l * (n - 1) + losses[i]) / n
        out[i] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
    return pd.Series(out, index=close.index)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = close.rolling(n).mean()
    sd = close.rolling(n).std(ddof=0)
    return pd.DataFrame({"mid": mid, "upper": mid + k * sd, "lower": mid - k * sd})


def true_range(df: pd.DataFrame) -> pd.Series:
    prev = df["close"].shift()
    return pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()],
                     axis=1).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return true_range(df).ewm(alpha=1 / n, adjust=False).mean()


def supertrend(df: pd.DataFrame, n: int = 10, mult: float = 3.0) -> pd.DataFrame:
    a = atr(df, n)
    hl2 = (df["high"] + df["low"]) / 2
    upper = (hl2 + mult * a).to_numpy()
    lower = (hl2 - mult * a).to_numpy()
    close = df["close"].to_numpy()
    fu, fl = upper.copy(), lower.copy()
    trend = np.ones(len(df), dtype=int)
    for i in range(1, len(df)):
        fu[i] = upper[i] if upper[i] < fu[i - 1] or close[i - 1] > fu[i - 1] else fu[i - 1]
        fl[i] = lower[i] if lower[i] > fl[i - 1] or close[i - 1] < fl[i - 1] else fl[i - 1]
        if close[i] > fu[i - 1]:
            trend[i] = 1
        elif close[i] < fl[i - 1]:
            trend[i] = -1
        else:
            trend[i] = trend[i - 1]
    line = np.where(trend == 1, fl, fu)
    return pd.DataFrame({"supertrend": line, "trend": trend}, index=df.index)


def adx(df: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    a = atr(df, n)
    plus_di = 100 * plus_dm.ewm(alpha=1 / n, adjust=False).mean() / a
    minus_di = 100 * minus_dm.ewm(alpha=1 / n, adjust=False).mean() / a
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return pd.DataFrame({"adx": dx.ewm(alpha=1 / n, adjust=False).mean(), "plus_di": plus_di,
                         "minus_di": minus_di})


def historical_vol(close: pd.Series, n: int = 20, periods_per_year: int = 252) -> pd.Series:
    return np.log(close / close.shift()).rolling(n).std() * math.sqrt(periods_per_year)


def _last(s: pd.Series) -> Optional[float]:
    s = s.dropna()
    return float(s.iloc[-1]) if len(s) else None


def technical_snapshot(candles: list[Candle], atm_iv: Optional[float] = None) -> dict:
    """Latest indicator values plus a Signal for each."""
    df = to_frame(candles)
    if len(df) < 35:
        raise ValueError(f"Need at least 35 daily candles for indicators, got {len(df)}")
    close = df["close"]
    last = float(close.iloc[-1])
    e20, e50 = ema(close, 20), ema(close, 50)
    sma200 = close.rolling(200).mean()
    r = rsi(close)
    m = macd(close)
    bb = bollinger(close)
    st = supertrend(df)
    ad = adx(df)
    hv = historical_vol(close)

    v = {
        "close": last,
        "ema20": _last(e20), "ema50": _last(e50), "sma200": _last(sma200),
        "rsi14": _last(r),
        "macd": _last(m["macd"]), "macd_signal": _last(m["signal"]), "macd_hist": _last(m["hist"]),
        "bb_upper": _last(bb["upper"]), "bb_mid": _last(bb["mid"]), "bb_lower": _last(bb["lower"]),
        "supertrend": _last(st["supertrend"]), "supertrend_dir": int(st["trend"].iloc[-1]),
        "adx": _last(ad["adx"]), "plus_di": _last(ad["plus_di"]), "minus_di": _last(ad["minus_di"]),
        "hv20": _last(hv),
        "hv20_percentile": None,
        "change_1d_pct": (last / float(close.iloc[-2]) - 1) * 100,
    }
    hv_hist = hv.dropna()
    if len(hv_hist) > 20 and v["hv20"] is not None:
        v["hv20_percentile"] = float((hv_hist < v["hv20"]).mean() * 100)

    signals: list[Signal] = []
    T = "technical"
    # EMA crossover (trend), stronger if a cross happened in the last 5 sessions.
    if v["ema20"] and v["ema50"]:
        diff = (e20 - e50).dropna()
        recent_cross = len(diff) > 5 and (np.sign(diff.iloc[-1]) != np.sign(diff.iloc[-6]))
        bias = "bullish" if v["ema20"] > v["ema50"] else "bearish"
        signals.append(Signal("EMA 20/50", bias, 0.7 if recent_cross else 0.45, round(v["ema20"] - v["ema50"], 2),
                              f"20-EMA {'above' if bias == 'bullish' else 'below'} 50-EMA"
                              + (" (fresh crossover)" if recent_cross else ""), T))
    if v["sma200"]:
        bias = "bullish" if last > v["sma200"] else "bearish"
        signals.append(Signal("200-day SMA", bias, 0.35, round(v["sma200"], 2),
                              f"Price {'above' if bias == 'bullish' else 'below'} the 200-day average", T))
    if v["rsi14"] is not None:
        x = v["rsi14"]
        if x >= 70:
            signals.append(Signal("RSI 14", "bearish", min((x - 70) / 15, 1) * 0.4 + 0.3, round(x, 1),
                                  f"RSI {x:.0f}: overbought, pullback risk", T))
        elif x <= 30:
            signals.append(Signal("RSI 14", "bullish", min((30 - x) / 15, 1) * 0.4 + 0.3, round(x, 1),
                                  f"RSI {x:.0f}: oversold, bounce likely", T))
        else:
            bias = "bullish" if x > 55 else "bearish" if x < 45 else "neutral"
            signals.append(Signal("RSI 14", bias, 0.25 if bias != "neutral" else 0.1, round(x, 1),
                                  f"RSI {x:.0f}: {'positive' if x > 55 else 'negative' if x < 45 else 'flat'} momentum", T))
    if v["macd_hist"] is not None:
        h = m["hist"].dropna()
        rising = len(h) > 1 and h.iloc[-1] > h.iloc[-2]
        bias = "bullish" if v["macd_hist"] > 0 else "bearish"
        strength = 0.5 if (bias == "bullish") == rising else 0.25
        signals.append(Signal("MACD", bias, strength, round(v["macd_hist"], 3),
                              f"MACD {'above' if bias == 'bullish' else 'below'} signal, histogram "
                              f"{'rising' if rising else 'falling'}", T))
    if v["bb_upper"] and v["bb_lower"]:
        width = v["bb_upper"] - v["bb_lower"]
        pos = (last - v["bb_lower"]) / width if width else 0.5
        if pos > 1:
            signals.append(Signal("Bollinger", "bearish", 0.35, round(pos, 2), "Close above upper band: stretched", T))
        elif pos < 0:
            signals.append(Signal("Bollinger", "bullish", 0.35, round(pos, 2), "Close below lower band: stretched", T))
        else:
            signals.append(Signal("Bollinger", "neutral", 0.1, round(pos, 2),
                                  f"Close at {pos * 100:.0f}% of the band width", T))
    signals.append(Signal("Supertrend", "bullish" if v["supertrend_dir"] == 1 else "bearish", 0.55,
                          round(v["supertrend"], 2) if v["supertrend"] else None,
                          f"Supertrend(10,3) is {'up' if v['supertrend_dir'] == 1 else 'down'}", T))
    if v["adx"] is not None and v["plus_di"] is not None:
        trending = v["adx"] >= 25
        bias = ("bullish" if v["plus_di"] > v["minus_di"] else "bearish") if trending else "neutral"
        signals.append(Signal("ADX", bias, 0.4 if trending else 0.2, round(v["adx"], 1),
                              f"ADX {v['adx']:.0f}: {'trending' if trending else 'no clear trend (range-bound)'}", T))

    vol = volatility_view(v["hv20"], atm_iv, v["hv20_percentile"])
    return {"values": v, "signals": [s.to_dict() for s in signals], "volatility": vol,
            "trend_strength": v["adx"]}


def volatility_view(hv: Optional[float], iv: Optional[float], hv_pct: Optional[float]) -> dict:
    """Is option premium rich or cheap? Compares ATM IV with realised (historical) volatility."""
    if hv is None or iv is None or hv <= 0:
        return {"view": "unknown", "iv": iv, "hv20": hv, "iv_hv_ratio": None, "hv20_percentile": hv_pct,
                "detail": "Need both ATM IV and price history to judge premium"}
    ratio = iv / hv
    if ratio >= 1.25:
        view, detail = "high", f"IV {iv * 100:.1f}% is {ratio:.2f}x realised vol: options look expensive (favour selling premium)"
    elif ratio <= 0.85:
        view, detail = "low", f"IV {iv * 100:.1f}% is only {ratio:.2f}x realised vol: options look cheap (favour buying premium)"
    else:
        view, detail = "fair", f"IV {iv * 100:.1f}% is close to realised vol {hv * 100:.1f}%"
    return {"view": view, "iv": iv, "hv20": hv, "iv_hv_ratio": round(ratio, 3), "hv20_percentile": hv_pct,
            "detail": detail}
