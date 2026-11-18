import pytest

from option_trader.config import Settings


def test_defaults(monkeypatch):
    monkeypatch.delenv("OPTION_TRADER_PROVIDERS", raising=False)
    monkeypatch.delenv("OPTION_TRADER_RISK_FREE_RATE", raising=False)
    s = Settings()
    assert s.providers == ["tradier", "cboe", "yahoo"]  # US sources by default (Tradier skipped without a token)
    assert s.risk_free_rate == pytest.approx(0.065)


def test_provider_order(monkeypatch):
    monkeypatch.setenv("OPTION_TRADER_PROVIDERS", "yahoo, NSE")
    assert Settings().providers == ["yahoo", "nse"]


def test_unknown_provider(monkeypatch):
    monkeypatch.setenv("OPTION_TRADER_PROVIDERS", "nse,madeup")
    with pytest.raises(ValueError):
        Settings()


def test_angel_configured(monkeypatch):
    for k in ("ANGEL_API_KEY", "ANGEL_CLIENT_CODE", "ANGEL_PIN", "ANGEL_TOTP_SECRET"):
        monkeypatch.setenv(k, "x")
    assert Settings().angel_configured
    monkeypatch.setenv("ANGEL_PIN", "")
    assert not Settings().angel_configured
