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
    tick_ts: Optional[str] = None  # exchange time of the last streamed tick applied to this quote

    @property
    def mid(self) -> Optional[float]:
        if self.bid and self.ask and self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2
        return None

    @property
    def price(self) -> Optional[float]:
        """Best available traded/quoted price: LTP, else bid/ask mid."""
        if self.ltp is not None and self.ltp > 0:
            return self.ltp
        return self.mid

    def to_dict(self) -> dict:
        d = asdict(self)
        d["expiry"] = self.expiry.isoformat()
        d["mid"] = self.mid
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
