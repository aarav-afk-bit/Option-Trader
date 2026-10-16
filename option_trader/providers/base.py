"""Provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import Optional

from ..models import Candle, OptionChain

# Common index aliases, normalised to the NSE symbol.
INDEX_ALIASES = {
    "NIFTY": "NIFTY",
    "NIFTY50": "NIFTY",
    "NIFTY 50": "NIFTY",
    "BANKNIFTY": "BANKNIFTY",
    "NIFTYBANK": "BANKNIFTY",
    "FINNIFTY": "FINNIFTY",
    "MIDCPNIFTY": "MIDCPNIFTY",
    "NIFTYNXT50": "NIFTYNXT50",
}
NSE_INDICES = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"}


def normalize_symbol(symbol: str) -> str:
    s = symbol.strip().upper()
    return INDEX_ALIASES.get(s, s)


def is_index(symbol: str) -> bool:
    return normalize_symbol(symbol) in NSE_INDICES


class DataProvider(ABC):
    """A source of real market data."""

    name: str = "base"

    def is_configured(self) -> bool:
        """Whether the provider has everything it needs (e.g. credentials) to try a request."""
        return True

    @abstractmethod
    def get_expiries(self, symbol: str) -> list[date]:
        """Available option expiries for ``symbol``, nearest first."""

    @abstractmethod
    def get_option_chain(self, symbol: str, expiry: Optional[date] = None) -> OptionChain:
        """Full option chain for ``symbol``; nearest expiry when ``expiry`` is None.

        Must raise ``DataUnavailableError`` rather than return partial or invented data.
        """

    def get_spot(self, symbol: str) -> float:
        return self.get_option_chain(symbol).spot

    def get_history(self, symbol: str, period: str = "1y", interval: str = "1d") -> list[Candle]:
        raise NotImplementedError(f"{self.name} does not provide historical candles")
