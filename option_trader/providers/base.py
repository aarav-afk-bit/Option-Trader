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

# US cash-settled, European-style index options (the rest are American-style equity/ETF options).
US_INDEX_ALIASES = {
    "SPX": "SPX", "$SPX": "SPX", "^SPX": "SPX", "^GSPC": "SPX", "SPXW": "SPX", ".SPX": "SPX",
    "XSP": "XSP", "$XSP": "XSP", "^XSP": "XSP",
    "NDX": "NDX", "$NDX": "NDX", "^NDX": "NDX", "NDXP": "NDX",
    "RUT": "RUT", "$RUT": "RUT", "^RUT": "RUT", "RUTW": "RUT",
    "VIX": "VIX", "$VIX": "VIX", "^VIX": "VIX", "VIXW": "VIX",
    "DJX": "DJX", "$DJX": "DJX", "OEX": "OEX", "XEO": "XEO",
}
US_INDICES = set(US_INDEX_ALIASES.values())


def normalize_symbol(symbol: str) -> str:
    s = symbol.strip().upper().replace("/", ".")
    if s.startswith("US:"):
        s = s[3:].strip()
    return US_INDEX_ALIASES.get(s) or INDEX_ALIASES.get(s, s)


def is_us_index(symbol: str) -> bool:
    return normalize_symbol(symbol) in US_INDICES


def exercise_style(symbol: str) -> str:
    """US index options are European; equity and ETF options are American."""
    return "european" if is_us_index(symbol) else "american"


def is_index(symbol: str) -> bool:
    return normalize_symbol(symbol) in NSE_INDICES


class DataProvider(ABC):
    """A source of real market data."""

    name: str = "base"
    # Minimum seconds between requests to this source (protects against rate limits/bans).
    min_interval: float = 0.0

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
