"""Core market-data models.

Every chain carries the name of the source it was fetched from and the time it was fetched.
``OptionChain.validate`` rejects chains that are empty or have no provenance, so nothing
synthetic can flow through the app by accident.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal, Optional

from .errors import InvalidDataError

OptionType = Literal["CE", "PE"]

IST = timezone(timedelta(hours=5, minutes=30))
NSE_EXPIRY_TIME = time(15, 30)


@dataclass
class OptionQuote:
    """One side (call or put) of one strike."""

    strike: float
    option_type: OptionType
    expiry: date
    ltp: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    bid_qty: Optional[int] = None
    ask_qty: Optional[int] = None
    volume: Optional[int] = None
    oi: Optional[float] = None
    oi_change: Optional[float] = None
    change: Optional[float] = None
    iv: Optional[float] = None  # annualised, as a decimal (0.15 = 15%)
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None  # per calendar day
    vega: Optional[float] = None  # per 1 vol point (1%)
    symbol: Optional[str] = None  # source-specific identifier/trading symbol
    source_iv: Optional[float] = None  # IV as published by the data source, kept for reference
    suspect: bool = False  # failed a data-quality check (see quality.py); shown but not relied on
    issues: list = field(default_factory=list)
    tick_ts: Optional[str] = None  # exchange time of the last streamed tick applied to this quote

    @property
    def mid(self) -> Optional[float]:
        if self.bid and self.ask and self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2
        return None

    @property
    def ltp_stale(self) -> bool:
        """True when the last trade is outside the current bid/ask, i.e. no longer tradable."""
        if not (self.ltp and self.bid and self.ask and self.bid > 0 and self.ask >= self.bid):
            return False
        return not (self.bid <= self.ltp <= self.ask)

    @property
    def spread_pct(self) -> Optional[float]:
        m = self.mid
        return round((self.ask - self.bid) / m * 100, 2) if m else None  # type: ignore[operator]

    @property
    def price(self) -> Optional[float]:
        """Most accurate current premium.

        The last traded price, unless the market has moved away from it (LTP outside the live
        bid/ask), in which case the bid/ask mid is the price you could actually deal near.
        """
        if self.ltp is not None and self.ltp > 0 and not self.ltp_stale:
            return self.ltp
        return self.mid or (self.ltp if self.ltp and self.ltp > 0 else None)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["expiry"] = self.expiry.isoformat()
        d["mid"] = self.mid
        d["price"] = self.price
        d["ltp_stale"] = self.ltp_stale
        d["spread_pct"] = self.spread_pct
        return d


@dataclass
class StrikeRow:
    strike: float
    call: Optional[OptionQuote] = None
    put: Optional[OptionQuote] = None

    def to_dict(self) -> dict:
        return {
            "strike": self.strike,
            "call": self.call.to_dict() if self.call else None,
            "put": self.put.to_dict() if self.put else None,
        }


@dataclass
class OptionChain:
    symbol: str
    expiry: date
    spot: float
    rows: list[StrikeRow]
    source: str
    fetched_at: datetime
    exchange_timestamp: Optional[str] = None
    lot_size: Optional[int] = None
    expiries: list[date] = field(default_factory=list)
    currency: str = "INR"
    notes: list[str] = field(default_factory=list)
    # Exact expiry moment when it differs from NSE's 15:30 IST close (e.g. US options).
    expiry_at: Optional[datetime] = None
    # Set when streamed exchange ticks have been applied on top of the snapshot.
    live_at: Optional[str] = None
    # Market-implied forward (put-call parity) and the carry yield q it implies.
    forward: Optional[float] = None
    carry: float = 0.0
    # Price a futures leg for this expiry trades at: the implied forward, or spot carried at r.
    fut_price: Optional[float] = None
    # Exchange-traded futures price for this expiry, when a future exists (monthly expiries).
    futures_ltp: Optional[float] = None
    quality: Optional[dict] = None  # data-quality report (quality.report)

    def __post_init__(self) -> None:
        self.rows.sort(key=lambda r: r.strike)

    @property
    def strikes(self) -> list[float]:
        return [r.strike for r in self.rows]

    def row(self, strike: float) -> Optional[StrikeRow]:
        for r in self.rows:
            if abs(r.strike - strike) < 1e-6:
                return r
        return None

    def quote(self, strike: float, option_type: OptionType) -> Optional[OptionQuote]:
        r = self.row(strike)
        if r is None:
            return None
        return r.call if option_type == "CE" else r.put

    @property
    def atm_strike(self) -> float:
        return min(self.strikes, key=lambda k: abs(k - self.spot))

    @property
    def strike_step(self) -> float:
        """Most common gap between consecutive strikes around ATM."""
        ks = self.strikes
        if len(ks) < 2:
            return 0.0
        gaps = [round(b - a, 6) for a, b in zip(ks, ks[1:]) if b > a]
        return max(set(gaps), key=gaps.count) if gaps else 0.0

    def expiry_datetime(self) -> datetime:
        if self.expiry_at is not None:
            return self.expiry_at
        return datetime.combine(self.expiry, NSE_EXPIRY_TIME, tzinfo=IST)

    def exchange_time(self) -> Optional[datetime]:
        """Exchange timestamp of the snapshot (e.g. NSE's '14-Oct-2026 15:30:00', IST)."""
        if not self.exchange_timestamp:
            return None
        for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(self.exchange_timestamp.strip(), fmt).replace(tzinfo=IST)
            except ValueError:
                continue
        return None

    def data_age_seconds(self, now: Optional[datetime] = None) -> Optional[float]:
        """Seconds since the exchange produced these prices (live tick time if streaming)."""
        now = now or datetime.now(timezone.utc)
        ref = None
        if self.live_at:
            ref = datetime.fromisoformat(self.live_at)
        else:
            ref = self.exchange_time()
        return max(0.0, (now - ref).total_seconds()) if ref else None

    def years_to_expiry(self, now: Optional[datetime] = None) -> float:
        now = now or datetime.now(timezone.utc)
        seconds = (self.expiry_datetime() - now).total_seconds()
        return max(seconds, 0.0) / (365.0 * 24 * 3600)

    def validate(self) -> "OptionChain":
        if not self.source:
            raise InvalidDataError("Option chain has no data source; refusing to use it")
        if not self.rows:
            raise InvalidDataError(f"{self.source} returned an empty option chain for {self.symbol}")
        if not (self.spot and self.spot > 0):
            raise InvalidDataError(f"{self.source} returned no underlying price for {self.symbol}")
        priced = sum(
            1 for r in self.rows for q in (r.call, r.put) if q is not None and (q.price or q.oi)
        )
        if priced == 0:
            raise InvalidDataError(
                f"{self.source} returned a chain for {self.symbol} with no prices or open interest"
            )
        return self

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "expiry": self.expiry.isoformat(),
            "spot": self.spot,
            "source": self.source,
            "fetched_at": self.fetched_at.isoformat(),
            "exchange_timestamp": self.exchange_timestamp,
            "lot_size": self.lot_size,
            "expiries": [e.isoformat() for e in self.expiries],
            "currency": self.currency,
            "atm_strike": self.atm_strike if self.rows else None,
            "strike_step": self.strike_step,
            "notes": self.notes,
            "live_at": self.live_at,
            "forward": self.forward,
            "fut_price": self.fut_price,
            "quality": self.quality,
            "futures_ltp": self.futures_ltp,
            "carry": self.carry,
            "data_age_seconds": self.data_age_seconds(),
            "rows": [r.to_dict() for r in self.rows],
        }


@dataclass
class Candle:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
