"""Chooses which real data source to use and caches results briefly.

Sources are tried in the configured order (``OPTION_TRADER_PROVIDERS``). The first one that
returns a valid chain wins. If none succeed, ``DataUnavailableError`` lists why each failed;
nothing is ever invented to fill the gap.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Optional

from ..config import Settings, get_settings
from ..errors import DataUnavailableError
from ..models import Candle, OptionChain, utcnow
from .base import DataProvider, normalize_symbol

log = logging.getLogger(__name__)

CONNECTIVITY_HOSTS = (("www.nseindia.com", 443), ("query1.finance.yahoo.com", 443), ("1.1.1.1", 443))


def check_online(timeout: float = 3.0) -> bool:
    for host, port in CONNECTIVITY_HOSTS:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False


@dataclass
class ProviderHealth:
    last_success: Optional[str] = None
    last_error: Optional[str] = None
    last_error_at: Optional[str] = None


@dataclass
class _CacheEntry:
    value: object
    at: float


@dataclass
class ProviderManager:
    providers: list[DataProvider]
    cache_ttl: float = 30.0
    online_check: Callable[[], bool] = check_online
    health: dict[str, ProviderHealth] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._cache: dict[tuple, _CacheEntry] = {}
        self._lock = threading.Lock()
        self._online: Optional[bool] = None
        self._online_at = 0.0
        for p in self.providers:
            self.health.setdefault(p.name, ProviderHealth())

    @classmethod
    def from_settings(cls, settings: Optional[Settings] = None) -> "ProviderManager":
        from .angelone import AngelOneProvider
        from .nse import NSEProvider
        from .yahoo import YahooProvider

        settings = settings or get_settings()
        factories = {
            "nse": NSEProvider,
            "yahoo": YahooProvider,
            "angelone": lambda: AngelOneProvider(settings),
        }
        return cls([factories[name]() for name in settings.providers], cache_ttl=settings.cache_ttl)

    # -- helpers ----------------------------------------------------------------------------
    def provider(self, name: str) -> DataProvider:
        for p in self.providers:
            if p.name == name:
                return p
        raise DataUnavailableError(f"Unknown or disabled data source: {name}")

    def _candidates(self, source: Optional[str]) -> list[DataProvider]:
        if source:
            p = self.provider(source)
            if not p.is_configured():
                raise DataUnavailableError(f"{source} is not configured (missing credentials?)")
            return [p]
        return [p for p in self.providers if p.is_configured()]

    def is_online(self) -> bool:
        if self._online is None or time.time() - self._online_at > 15:
            self._online = self.online_check()
            self._online_at = time.time()
        return bool(self._online)

    def _cached(self, key: tuple):
        with self._lock:
            entry = self._cache.get(key)
            if entry and time.time() - entry.at < self.cache_ttl:
                return entry.value
        return None

    def _store(self, key: tuple, value) -> None:
        with self._lock:
            self._cache[key] = _CacheEntry(value, time.time())

    def _try_each(self, what: str, source: Optional[str], call: Callable[[DataProvider], object]):
        if not self.is_online():
            raise DataUnavailableError(
                "No internet connection: live market data cannot be fetched right now"
            )
        errors = []
        for p in self._candidates(source):
            try:
                result = call(p)
            except NotImplementedError:
                continue
            except DataUnavailableError as exc:
                errors.append(f"{p.name}: {exc}")
                h = self.health[p.name]
                h.last_error, h.last_error_at = str(exc), utcnow().isoformat()
                log.info("%s failed for %s: %s", p.name, what, exc)
                continue
            except Exception as exc:  # unexpected parsing/library failure: treat as unavailable
                log.exception("%s crashed fetching %s", p.name, what)
                errors.append(f"{p.name}: unexpected error {exc!r}")
                continue
            self.health[p.name].last_success = utcnow().isoformat()
            return result
        if not errors:
            raise DataUnavailableError(f"No configured data source can provide {what}")
        raise DataUnavailableError(f"Could not fetch {what} from any source. " + " | ".join(errors))

    # -- public API -------------------------------------------------------------------------
    def get_chain(self, symbol: str, expiry: Optional[date] = None,
                  source: Optional[str] = None, use_cache: bool = True) -> OptionChain:
        symbol = normalize_symbol(symbol)
        key = ("chain", symbol, expiry, source)
        if use_cache and (hit := self._cached(key)) is not None:
            return hit  # type: ignore[return-value]
        chain = self._try_each(
            f"the {symbol} option chain", source, lambda p: p.get_option_chain(symbol, expiry)
        )
        self._store(key, chain)
        return chain  # type: ignore[return-value]

    def get_expiries(self, symbol: str, source: Optional[str] = None) -> tuple[str, list[date]]:
        symbol = normalize_symbol(symbol)
        key = ("expiries", symbol, source)
        if (hit := self._cached(key)) is not None:
            return hit  # type: ignore[return-value]
        result = self._try_each(
            f"{symbol} expiries", source, lambda p: (p.name, p.get_expiries(symbol))
        )
        self._store(key, result)
        return result  # type: ignore[return-value]

    def get_history(self, symbol: str, period: str = "1y", interval: str = "1d") -> list[Candle]:
        key = ("history", normalize_symbol(symbol), period, interval)
        if (hit := self._cached(key)) is not None:
            return hit  # type: ignore[return-value]
        candles = self._try_each(
            f"{symbol} price history", None, lambda p: p.get_history(symbol, period, interval)
        )
        with self._lock:
            # History changes slowly; keep it for 10 minutes regardless of the chain TTL.
            self._cache[key] = _CacheEntry(candles, time.time() + max(0.0, 600 - self.cache_ttl))
        return candles  # type: ignore[return-value]

    def status(self) -> dict:
        return {
            "online": self.is_online(),
            "providers": [
                {
                    "name": p.name,
                    "configured": p.is_configured(),
                    "last_success": self.health[p.name].last_success,
                    "last_error": self.health[p.name].last_error,
                    "last_error_at": self.health[p.name].last_error_at,
                }
                for p in self.providers
            ],
        }
