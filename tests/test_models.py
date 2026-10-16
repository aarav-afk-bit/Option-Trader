from datetime import date, datetime, timezone

import pytest

from option_trader.errors import InvalidDataError
from option_trader.models import OptionChain, OptionQuote, StrikeRow
from option_trader.providers.base import is_index, normalize_symbol

EXP = date(2026, 10, 27)


def _row(k, c_ltp=None, p_ltp=None, oi=None):
    return StrikeRow(
        k,
        call=OptionQuote(k, "CE", EXP, ltp=c_ltp, oi=oi),
        put=OptionQuote(k, "PE", EXP, ltp=p_ltp, oi=oi),
    )


def _chain(rows, spot=100.0, source="nse"):
    return OptionChain("TEST", EXP, spot, rows, source, datetime.now(timezone.utc))


def test_rows_sorted_and_atm():
    ch = _chain([_row(110, 1, 9), _row(90, 11, 1), _row(100, 5, 5)], spot=102)
    assert ch.strikes == [90, 100, 110]
    assert ch.atm_strike == 100
    assert ch.strike_step == 10
    assert ch.quote(100, "CE").ltp == 5


def test_validate_rejects_empty_and_unsourced():
    with pytest.raises(InvalidDataError):
        _chain([]).validate()
    with pytest.raises(InvalidDataError):
        _chain([_row(100, 5, 5)], source="").validate()
    with pytest.raises(InvalidDataError):
        _chain([_row(100, 5, 5)], spot=0).validate()
    with pytest.raises(InvalidDataError):
        _chain([_row(100)]).validate()
    assert _chain([_row(100, 5, 5)]).validate()


def test_price_falls_back_to_mid():
    q = OptionQuote(100, "CE", EXP, ltp=0, bid=4, ask=6)
    assert q.price == 5


def test_years_to_expiry_never_negative():
    ch = _chain([_row(100, 5, 5)])
    assert ch.years_to_expiry(datetime(2030, 1, 1, tzinfo=timezone.utc)) == 0


def test_symbol_normalisation():
    assert normalize_symbol(" nifty50 ") == "NIFTY"
    assert is_index("banknifty")
    assert not is_index("RELIANCE")
