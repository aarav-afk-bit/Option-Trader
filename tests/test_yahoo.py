from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from option_trader.errors import DataUnavailableError
from option_trader.providers.yahoo import YahooProvider, yahoo_symbol


def test_symbol_mapping():
    assert yahoo_symbol("spx") == "^SPX" and yahoo_symbol("$NDX") == "^NDX" and yahoo_symbol("vix") == "^VIX"
    assert yahoo_symbol("aapl") == "AAPL" and yahoo_symbol("US:msft") == "MSFT"
    assert yahoo_symbol("BRK.B") == "BRK-B" and yahoo_symbol("^GSPC") == "^GSPC"


class FakeTicker:
    """Stands in for yfinance.Ticker with the column layout yfinance returns."""

    def __init__(self, options=("2026-10-16", "2026-10-23")):
        self.options = options
        self.fast_info = {"last_price": 231.5}

    def option_chain(self, d):
        calls = pd.DataFrame(
            {
                "contractSymbol": ["AAPL261016C00230000", "AAPL261016C00235000"],
                "strike": [230.0, 235.0],
                "lastPrice": [3.1, 1.05],
                "bid": [3.0, 1.0],
                "ask": [3.2, 1.1],
                "change": [0.2, -0.1],
                "volume": [1500.0, float("nan")],
                "openInterest": [8000, 6000],
                "impliedVolatility": [0.24, 0.00001],
                "currency": ["USD", "USD"],
            }
        )
        puts = calls.assign(
            contractSymbol=["AAPL261016P00230000", "AAPL261016P00235000"],
            lastPrice=[1.6, 4.4],
        )
        return SimpleNamespace(calls=calls, puts=puts)

    def history(self, period, interval, auto_adjust=True):
        idx = pd.date_range("2026-10-01", periods=3, freq="D")
        return pd.DataFrame(
            {"Open": [1, 2, float("nan")], "High": [2, 3, 4], "Low": [0.5, 1, 2],
             "Close": [1.5, 2.5, 3], "Volume": [10, 20, 30]},
            index=idx,
        )


def test_option_chain_parsing():
    p = YahooProvider(ticker_factory=lambda s: FakeTicker())
    ch = p.get_option_chain("AAPL", date(2026, 10, 16))
    assert ch.source == "yahoo" and ch.symbol == "AAPL"
    assert ch.spot == 231.5
    assert ch.strikes == [230.0, 235.0]
    assert ch.quote(230, "CE").iv == pytest.approx(0.24)
    assert ch.quote(235, "CE").iv is None  # placeholder IV discarded
    assert ch.quote(235, "CE").volume is None
    assert ch.quote(235, "PE").ltp == 4.4
    assert ch.lot_size == 100 and ch.currency == "USD"
    assert ch.expiry_datetime().hour == 16


def test_no_options_raises():
    p = YahooProvider(ticker_factory=lambda s: FakeTicker(options=()))
    with pytest.raises(DataUnavailableError):
        p.get_option_chain("RELIANCE")


def test_history_skips_nan_rows():
    p = YahooProvider(ticker_factory=lambda s: FakeTicker())
    candles = p.get_history("NDX")
    assert len(candles) == 2 and candles[-1].close == 2.5
