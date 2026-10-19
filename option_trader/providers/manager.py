"""Chooses which real data source to use and caches results briefly.

Sources are tried in the configured order (``OPTION_TRADER_PROVIDERS``). The first one that
returns a valid chain wins. If none succeed, ``DataUnavailableError`` lists why each failed;
nothing is ever invented to fill the gap.
"""

from __future__ import annotations

import logging
import socket
from collections import deque
from concurrent.futures import Future
import threading
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Optional

from ..config import Settings, get_settings
from ..errors import DataUnavailableError
from ..models import Candle, OptionChain, utcnow
from ..pricing import enrich_chain
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
    risk_free_rate: float = 0.065
    stale_seconds: float = 180.0
    live: Optional[object] = None  # LiveHub when real-time ticks are available
    online_check: Callable[[], bool] = check_online
    health: dict[str, ProviderHealth] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._cache: dict[tuple, _CacheEntry] = {}
        self._lock = threading.Lock()
        self._inflight: dict[tuple, Future] = {}
        # Intraday snapshots of each freshly fetched chain: {(symbol, expiry): deque[dict]}
        self.snapshots: dict[tuple, deque] = {}
        self._last_call: dict[str, float] = {}
        self._throttle_lock = threading.Lock()
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
        mgr = cls([factories[name]() for name in settings.providers], cache_ttl=settings.cache_ttl,
                  risk_free_rate=settings.risk_free_rate, stale_seconds=settings.stale_seconds)
        angel = next((p for p in mgr.providers if p.name == "angelone" and p.is_configured()), None)
        if angel is None and settings.angel_configured:
            angel = AngelOneProvider(settings)  # stream ticks even if Angel isn't a snapshot source
        if angel is not None and settings.live_ticks:
            from ..live import LiveHub

            mgr.live = LiveHub(angel, risk_free_rate=settings.risk_free_rate)
        return mgr

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

    def _record_snapshot(self, chain: OptionChain) -> None:
        from ..analytics.chain import atm_iv, max_pain, pcr

        p = pcr(chain)
        snap = {
            "t": chain.fetched_at.isoformat(),
            "spot": chain.spot,
            "pcr_oi": p["pcr_oi"],
            "call_oi": p["call_oi"],
            "put_oi": p["put_oi"],
            "atm_iv": atm_iv(chain),
            "max_pain": max_pain(chain),
            "source": chain.source,
        }
        with self._lock:
            buf = self.snapshots.setdefault((chain.symbol, chain.expiry), deque(maxlen=2000))
            if buf and buf[-1]["t"][:10] != snap["t"][:10]:
                buf.clear()  # new trading day
            buf.append(snap)

    def get_snapshots(self, symbol: str, expiry: date) -> list[dict]:
        with self._lock:
            return list(self.snapshots.get((normalize_symbol(symbol), expiry), ()))

    def _single_flight(self, key: tuple, fn: Callable[[], object]):
        """Run ``fn`` once for concurrent callers asking for the same ``key``.

        Several browser tabs/panels refreshing at once share one upstream request instead of
        each hitting NSE/Angel (which rate-limit aggressively).
        """
        with self._lock:
            fut = self._inflight.get(key)
            owner = fut is None
            if owner:
                fut = Future()
                self._inflight[key] = fut
        if not owner:
            return fut.result()
        try:
            result = fn()
        except BaseException as exc:
            fut.set_exception(exc)
            raise
        else:
            fut.set_result(result)
            return result
        finally:
            with self._lock:
                self._inflight.pop(key, None)

    def _throttle(self, provider: DataProvider) -> None:
        if provider.min_interval <= 0:
            return
        with self._throttle_lock:
            wait = self._last_call.get(provider.name, 0.0) + provider.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_call[provider.name] = time.monotonic()

    def _try_each(self, what: str, source: Optional[str], call: Callable[[DataProvider], object],
                  accept: Optional[Callable[[object], bool]] = None):
        """Return the first source's result; with ``accept``, keep looking past rejected results
        (e.g. stale prices) and fall back to the first rejected one if nothing better exists."""
        fallback = None
        if not self.is_online():
            raise DataUnavailableError(
                "No internet connection: live market data cannot be fetched right now"
            )
        errors = []
        for p in self._candidates(source):
            try:
                self._throttle(p)
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
            if accept is None or accept(result):
                return result
            if fallback is None:
                fallback = result
        if fallback is not None:
            return fallback
        if not errors:
            raise DataUnavailableError(f"No configured data source can provide {what}")
        raise DataUnavailableError(f"Could not fetch {what} from any source. " + " | ".join(errors))

    # -- public API -------------------------------------------------------------------------
    def get_chain(self, symbol: str, expiry: Optional[date] = None,
                  source: Optional[str] = None, use_cache: bool = True) -> OptionChain:
        symbol = normalize_symbol(symbol)
        key = ("chain", symbol, expiry, source)
        if use_cache and (hit := self._cached(key)) is not None:
            return self._apply_live(hit)  # type: ignore[arg-type]

        def fetch():
            chain = self._try_each(
                f"the {symbol} option chain", source, lambda p: p.get_option_chain(symbol, expiry),
                accept=None if source else self._is_fresh,
            )
            self._flag_staleness(chain)  # type: ignore[arg-type]
            self._attach_futures(chain)  # type: ignore[arg-type]
            enrich_chain(chain, self.risk_free_rate)  # type: ignore[arg-type]
            self._store(key, chain)
            self._record_snapshot(chain)  # type: ignore[arg-type]
            return chain

        return self._apply_live(self._single_flight(key, fetch))  # type: ignore[arg-type]

    def _attach_futures(self, chain: OptionChain) -> None:
        """Best effort: the exchange futures price for this expiry from the same source."""
        key = ("futures", chain.symbol, chain.source)
        prices = self._cached(key)
        if prices is None:
            prices = {}
            try:
                p = self.provider(chain.source)
                if hasattr(p, "get_futures"):
                    prices = p.get_futures(chain.symbol)  # type: ignore[attr-defined]
            except Exception as exc:  # futures are an enhancement; never fail the chain
                log.info("Futures quote unavailable for %s: %s", chain.symbol, exc)
            self._store(key, prices)
        chain.futures_ltp = prices.get(chain.expiry) if prices else None
        if chain.futures_ltp:
            chain.notes.append(f"Futures leg priced at the exchange futures quote {chain.futures_ltp:,.2f}")

    def _is_fresh(self, chain) -> bool:
        """During market hours, reject snapshots older than ``stale_seconds`` so the next source
        is tried. Outside market hours last-traded prices are the current prices."""
        from ..market_hours import clock_session

        if not clock_session(utcnow())["open"]:
            return True
        age = chain.data_age_seconds()
        return age is None or age <= self.stale_seconds

    def _flag_staleness(self, chain: OptionChain) -> None:
        if not self._is_fresh(chain):
            age = chain.data_age_seconds() or 0
            chain.notes.append(
                f"Warning: {chain.source.upper()} prices are {age / 60:.0f} min old during market hours "
                "and no fresher source responded"
            )

    def _apply_live(self, chain: OptionChain) -> OptionChain:
        if self.live is not None:
            try:
                self.live.apply(chain)  # type: ignore[attr-defined]
            except Exception as exc:  # never let the tick overlay break snapshot delivery
                log.warning("Live tick overlay failed: %s", exc)
        return chain

    def get_expiries(self, symbol: str, source: Optional[str] = None) -> tuple[str, list[date]]:
        symbol = normalize_symbol(symbol)
        key = ("expiries", symbol, source)
        if (hit := self._cached(key)) is not None:
            return hit  # type: ignore[return-value]

        def fetch():
            result = self._try_each(
                f"{symbol} expiries", source, lambda p: (p.name, p.get_expiries(symbol))
            )
            self._store(key, result)
            return result

        return self._single_flight(key, fetch)  # type: ignore[return-value]

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
            "live": self.live.status() if self.live is not None else None,  # type: ignore[attr-defined]
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

    def market_status(self) -> dict:
        """Current NSE session: NSE's own status when reachable, else a labelled clock estimate."""
        from ..market_hours import clock_session

        cached = self._cached(("market",))
        if cached is not None:
            return cached  # type: ignore[return-value]
        now = utcnow()
        nse = next((p for p in self.providers if p.name == "nse"), None)
        holidays = None
        result = None
        if nse is not None and hasattr(nse, "get_market_status") and self.is_online():
            try:
                result = nse.get_market_status()  # type: ignore[attr-defined]
            except DataUnavailableError as exc:
                log.info("NSE market status unavailable: %s", exc)
            try:
                holidays = nse.get_holidays()  # type: ignore[attr-defined]
            except DataUnavailableError as exc:
                log.info("NSE holiday list unavailable: %s", exc)
        clock = clock_session(now, holidays)
        if result is None:
            result = clock
        else:
            result["next_open"] = clock["next_open"] if not result["open"] else None
            result["closes_at"] = clock["closes_at"] if result["open"] else None
        with self._lock:
            # Session state changes slowly; refresh at most once a minute.
            self._cache[("market",)] = _CacheEntry(result, time.time() + max(0.0, 60 - self.cache_ttl))
        return result
