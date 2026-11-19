import pytest

from option_trader.config import Settings


def test_defaults(monkeypatch):
    monkeypatch.delenv("OPTION_TRADER_PROVIDERS", raising=False)
    monkeypatch.delenv("OPTION_TRADER_RISK_FREE_RATE", raising=False)
    s = Settings()
    assert s.providers == ["tradier", "cboe", "yahoo"]  # US sources (Tradier skipped without a token)
    assert s.risk_free_rate == pytest.approx(0.04)


def test_provider_order(monkeypatch):
    monkeypatch.setenv("OPTION_TRADER_PROVIDERS", "yahoo, CBOE")
    assert Settings().providers == ["yahoo", "cboe"]


def test_unknown_provider(monkeypatch):
    monkeypatch.setenv("OPTION_TRADER_PROVIDERS", "cboe,nse")
    with pytest.raises(ValueError):
        Settings()


def test_tradier_configured(monkeypatch):
    monkeypatch.setenv("TRADIER_TOKEN", "x")
    monkeypatch.setenv("TRADIER_SANDBOX", "1")
    s = Settings()
    assert s.tradier_configured and s.tradier_sandbox
    monkeypatch.setenv("TRADIER_TOKEN", "")
    assert not Settings().tradier_configured
