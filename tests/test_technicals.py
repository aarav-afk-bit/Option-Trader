import pandas as pd
import pytest
from fastapi.testclient import TestClient

from option_trader.analytics.technicals import (
    adx,
    bollinger,
    macd,
    rsi,
    supertrend,
    technical_snapshot,
    to_frame,
    volatility_view,
)
from option_trader.app import create_app

from .helpers import RecordedProvider, manager_with, trending_candles


def test_rsi_extremes():
    up = pd.Series(range(1, 60), dtype=float)
    assert rsi(up).iloc[-1] == pytest.approx(100)
    down = pd.Series(range(60, 1, -1), dtype=float)
    assert rsi(down).iloc[-1] < 1


def test_rsi_wilder_reference():
    # Standard worked example for Wilder's RSI(14).
    closes = pd.Series([44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08,
                        45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64])
    r = rsi(closes)
    assert r.iloc[13] != r.iloc[13]  # NaN before 14 moves
    assert [round(x, 2) for x in r.iloc[14:]] == pytest.approx(
        [70.53, 66.32, 66.55, 69.41, 66.36, 57.97], abs=0.1)  # reference rounds its averages


def test_macd_and_bollinger_shapes():
    s = pd.Series([float(x) for x in range(100)])
    m = macd(s)
    assert m["macd"].iloc[-1] > 0 and abs(m["hist"].iloc[-1]) < 1e-6 + abs(m["macd"].iloc[-1])
    bb = bollinger(s)
    bb = bb.dropna()
    assert len(bb) == 81 and (bb["upper"] >= bb["lower"]).all()


def test_uptrend_reads_bullish():
    df = to_frame(trending_candles(drift=0.004))
    assert supertrend(df)["trend"].iloc[-1] == 1
    a = adx(df).iloc[-1]
    assert a["plus_di"] > a["minus_di"]
    snap = technical_snapshot(trending_candles(drift=0.004))
    bull = sum(s["score"] for s in snap["signals"])
    assert bull > 0


def test_downtrend_reads_bearish():
    snap = technical_snapshot(trending_candles(drift=-0.004))
    assert sum(s["score"] for s in snap["signals"]) < 0


def test_too_little_history():
    with pytest.raises(ValueError):
        technical_snapshot(trending_candles(n=20))


def test_volatility_view():
    assert volatility_view(0.10, 0.15, 50)["view"] == "high"
    assert volatility_view(0.20, 0.15, 50)["view"] == "low"
    assert volatility_view(0.15, 0.15, 50)["view"] == "fair"
    assert volatility_view(None, 0.15, None)["view"] == "unknown"


def test_technicals_api():
    client = TestClient(create_app(manager_with(RecordedProvider(trending_candles()))))
    r = client.get("/api/analytics/technicals", params={"symbol": "NDX"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["volatility"]["iv"] is not None
    assert body["candles"] == 260
    client = TestClient(create_app(manager_with(RecordedProvider())))
    assert client.get("/api/analytics/technicals", params={"symbol": "NDX"}).status_code == 503
