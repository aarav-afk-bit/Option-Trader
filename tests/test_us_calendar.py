from datetime import date, datetime

from option_trader.market_hours import ET, nyse_early_closes, nyse_holidays, us_next_open, us_session
from option_trader.models import OptionChain, StrikeRow, OptionQuote
from option_trader.providers.base import exercise_style, is_us_index, normalize_symbol


def et(*a):
    return datetime(*a, tzinfo=ET)


def test_nyse_holidays_2026_match_published_calendar():
    # NYSE published 2026 schedule
    assert set(nyse_holidays(2026)) == {
        date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3), date(2026, 5, 25),
        date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25)}
    assert set(nyse_early_closes(2026)) == {date(2026, 11, 27), date(2026, 12, 24)}


def test_nyse_holidays_2025_and_observed_rules():
    h = nyse_holidays(2025)
    assert date(2025, 4, 18) in h  # Good Friday
    assert date(2025, 1, 9) not in h  # one-off closures (national days of mourning) aren't rules
    assert date(2025, 7, 3) in nyse_early_closes(2025)
    assert date(2022, 12, 26) in nyse_holidays(2022)  # Christmas on Sunday -> Monday
    assert date(2021, 12, 31) not in nyse_holidays(2021) and date(2022, 1, 1) not in nyse_holidays(2022)  # Sat New Year


def test_us_session_states():
    assert us_session(et(2026, 10, 15, 10, 0))["open"] is True
    assert us_session(et(2026, 10, 15, 8, 0))["state"] == "pre-market"
    assert us_session(et(2026, 10, 15, 17, 0))["state"] == "after-hours"
    assert us_session(et(2026, 10, 17, 11, 0))["state"] == "weekend"
    assert us_session(et(2026, 11, 26, 11, 0))["state"] == "holiday"
    early = us_session(et(2026, 11, 27, 13, 30))
    assert not early["open"] and early["state"] == "after-hours"
    assert us_session(et(2026, 11, 27, 12, 0))["closes_at"].startswith("2026-11-27T13:00")


def test_next_open_skips_holidays():
    assert us_next_open(et(2026, 11, 25, 17, 0)) == et(2026, 11, 27, 9, 30)  # skips Thanksgiving
    assert us_next_open(et(2026, 10, 16, 16, 30)) == et(2026, 10, 19, 9, 30)


def test_us_symbols_and_exercise_style():
    assert normalize_symbol("^gspc") == "SPX" and normalize_symbol("$spx") == "SPX" and normalize_symbol("SPXW") == "SPX"
    assert normalize_symbol("brk/b") == "BRK.B" and normalize_symbol("US:aapl") == "AAPL"
    assert is_us_index("NDX") and not is_us_index("SPY")
    assert exercise_style("SPX") == "european" and exercise_style("AAPL") == "american"


def test_us_chain_defaults_to_usd_and_4pm_eastern_expiry():
    ch = OptionChain("SPY", date(2026, 10, 16), 600.0, [StrikeRow(600.0, OptionQuote(600, "CE", date(2026, 10, 16), ltp=2))],
                     "cboe", datetime(2026, 10, 15, tzinfo=ET))
    assert ch.currency == "USD"
    assert ch.expiry_datetime() == et(2026, 10, 16, 16, 0)


def test_market_endpoint_uses_nyse_calendar():
    from fastapi.testclient import TestClient

    from option_trader.app import create_app

    from .helpers import RecordedProvider, manager_with

    client = TestClient(create_app(manager_with(RecordedProvider())))
    m = client.get("/api/market").json()
    assert m["estimate"] is False and m["source"] == "nyse-calendar"
    assert m["open"] is True  # tests run on Thu 2026-10-15 10:00 ET


def test_stream_endpoint_explains_how_to_get_live_ticks():
    from fastapi.testclient import TestClient

    from option_trader.app import create_app

    from .helpers import RecordedProvider, manager_with

    client = TestClient(create_app(manager_with(RecordedProvider())))
    with client.stream("GET", "/api/stream", params={"symbol": "NDX"}) as r:
        body = "".join(r.iter_text())
    assert '"live": false' in body and "Tradier" in body
