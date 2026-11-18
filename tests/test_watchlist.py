from fastapi.testclient import TestClient

from option_trader.app import create_app
from option_trader.errors import DataUnavailableError

from .helpers import RecordedProvider, manager_with


class OnlyNifty(RecordedProvider):
    def get_option_chain(self, symbol, expiry=None):
        if symbol != "NDX":
            raise DataUnavailableError(f"no chain for {symbol}")
        return super().get_option_chain(symbol, expiry)


def test_watchlist_rows_and_errors():
    client = TestClient(create_app(manager_with(OnlyNifty())))
    rows = client.get("/api/analytics/watchlist", params={"symbols": "ndx, FOO"}).json()["rows"]
    assert rows[0]["symbol"] == "NDX" and rows[0]["spot"] == 25012.35
    assert rows[0]["straddle"] == 145.0 + 128.0
    assert rows[0]["bias"] in ("bullish", "bearish", "neutral")
    assert rows[1]["symbol"] == "FOO" and "no chain" in rows[1]["error"]
