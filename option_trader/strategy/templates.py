"""Strike-relative strategy templates.

A template describes legs relative to the at-the-money strike: ``offset`` counts *listed*
strikes away from ATM (+2 = two strikes above ATM), ``lots`` is the ratio of lots and
``expiry_offset`` selects a later listed expiry (for calendars). ``resolve`` maps a template
onto a real option chain, pricing each leg from live quotes, so the same template works for
any underlying and expiry. Both built-in and user-saved strategies use this format.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional

from ..models import OptionChain
from .engine import Leg, Strategy, leg_from_chain

VIEWS = ("bullish", "bearish", "neutral", "volatile", "any")


@dataclass
class TemplateLeg:
    option_type: str  # CE, PE or FUT
    action: str  # BUY or SELL
    offset: int = 0
    lots: int = 1
    expiry_offset: int = 0

    def __post_init__(self) -> None:
        self.option_type = self.option_type.upper()
        self.action = self.action.upper()
        if self.option_type not in ("CE", "PE", "FUT"):
            raise ValueError(f"option_type must be CE, PE or FUT, got {self.option_type}")
        if self.action not in ("BUY", "SELL"):
            raise ValueError(f"action must be BUY or SELL, got {self.action}")
        if self.lots < 1:
            raise ValueError("lots must be at least 1")
        if self.expiry_offset < 0:
            raise ValueError("expiry_offset cannot be negative")


@dataclass
class StrategyTemplate:
    id: str
    name: str
    legs: list[TemplateLeg]
    view: str = "any"
    description: str = ""
    tags: list[str] = field(default_factory=list)
    builtin: bool = False

    def __post_init__(self) -> None:
        if not self.legs:
            raise ValueError("A strategy needs at least one leg")
        if self.view not in VIEWS:
            raise ValueError(f"view must be one of {VIEWS}")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "StrategyTemplate":
        return cls(
            id=d.get("id") or slugify(d["name"]),
            name=d["name"],
            legs=[TemplateLeg(**{k: v for k, v in leg.items() if k in TemplateLeg.__dataclass_fields__})
                  for leg in d["legs"]],
            view=d.get("view", "any"),
            description=d.get("description", ""),
            tags=list(d.get("tags", [])),
            builtin=bool(d.get("builtin", False)),
        )


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "strategy"


class ResolveError(ValueError):
    """The template cannot be placed on this chain (strike out of range, no live price...)."""


def resolve(template: StrategyTemplate, chain: OptionChain, lots: int = 1, width: int = 1,
            fill: str = "ltp", atm_shift: int = 0,
            chain_for_offset: Optional[Callable[[int], OptionChain]] = None) -> Strategy:
    """Turn ``template`` into concrete, live-priced legs on ``chain``.

    ``width`` multiplies every strike offset (wider wings), ``atm_shift`` moves the whole
    structure up/down by that many strikes, ``lots`` multiplies every leg's lot ratio.
    """
    if width < 1 or lots < 1:
        raise ResolveError("width and lots must be at least 1")
    legs: list[Leg] = []
    for tl in template.legs:
        leg_chain = chain
        if tl.expiry_offset:
            if chain_for_offset is None:
                raise ResolveError(f"{template.name} needs a later expiry chain")
            leg_chain = chain_for_offset(tl.expiry_offset)
        strikes = leg_chain.strikes
        atm_idx = strikes.index(leg_chain.atm_strike)
        strike = 0.0
        if tl.option_type != "FUT":
            idx = atm_idx + atm_shift + tl.offset * width
            if not 0 <= idx < len(strikes):
                raise ResolveError(
                    f"{template.name}: strike {tl.offset * width:+d} from ATM is outside the "
                    f"listed strikes for {leg_chain.symbol} {leg_chain.expiry}"
                )
            strike = strikes[idx]
        try:
            legs.append(leg_from_chain(leg_chain, tl.option_type, tl.action, strike,  # type: ignore[arg-type]
                                       tl.lots * lots, fill))
        except ValueError as exc:
            raise ResolveError(str(exc)) from exc
    return Strategy(template.name, legs, chain.lot_size or 1, template.description, list(template.tags))


def _t(id_, name, view, desc, *legs, tags=()):
    return StrategyTemplate(id_, name, [TemplateLeg(*leg) for leg in legs], view, desc, list(tags), True)


B, S = "BUY", "SELL"

BUILTIN_TEMPLATES: list[StrategyTemplate] = [
    _t("long_call", "Long Call", "bullish", "Buy an ATM call: unlimited upside, risk limited to premium.",
       ("CE", B, 0)),
    _t("long_put", "Long Put", "bearish", "Buy an ATM put: profits as the underlying falls.",
       ("PE", B, 0)),
    _t("short_call", "Short Call", "bearish", "Sell an OTM call to collect premium; unlimited risk.",
       ("CE", S, 1)),
    _t("short_put", "Short Put", "bullish", "Sell an OTM put to collect premium; large downside risk.",
       ("PE", S, -1)),
    _t("covered_call", "Covered Call", "bullish", "Own the underlying and sell an OTM call against it.",
       ("FUT", B, 0), ("CE", S, 2)),
    _t("protective_put", "Protective Put", "bullish", "Own the underlying and buy an OTM put as insurance.",
       ("FUT", B, 0), ("PE", B, -2)),
    _t("collar", "Collar", "bullish", "Underlying + long OTM put financed by a short OTM call.",
       ("FUT", B, 0), ("PE", B, -2), ("CE", S, 2)),
    _t("bull_call_spread", "Bull Call Spread", "bullish", "Buy ATM call, sell higher call. Limited risk/reward debit.",
       ("CE", B, 0), ("CE", S, 2), tags=("spread",)),
    _t("bull_put_spread", "Bull Put Spread", "bullish", "Sell ATM put, buy lower put. Credit spread.",
       ("PE", S, 0), ("PE", B, -2), tags=("spread", "credit")),
    _t("bear_put_spread", "Bear Put Spread", "bearish", "Buy ATM put, sell lower put. Limited risk/reward debit.",
       ("PE", B, 0), ("PE", S, -2), tags=("spread",)),
    _t("bear_call_spread", "Bear Call Spread", "bearish", "Sell ATM call, buy higher call. Credit spread.",
       ("CE", S, 0), ("CE", B, 2), tags=("spread", "credit")),
    _t("long_straddle", "Long Straddle", "volatile", "Buy ATM call and put: profits from a big move either way.",
       ("CE", B, 0), ("PE", B, 0)),
    _t("short_straddle", "Short Straddle", "neutral", "Sell ATM call and put: profits if price stays put; unlimited risk.",
       ("CE", S, 0), ("PE", S, 0), tags=("credit",)),
    _t("long_strangle", "Long Strangle", "volatile", "Buy OTM call and OTM put: cheaper than a straddle, needs a bigger move.",
       ("CE", B, 2), ("PE", B, -2)),
    _t("short_strangle", "Short Strangle", "neutral", "Sell OTM call and OTM put: wide profit zone; unlimited risk.",
       ("CE", S, 2), ("PE", S, -2), tags=("credit",)),
    _t("iron_condor", "Iron Condor", "neutral", "Short strangle with protective wings: limited risk range-bound credit.",
       ("PE", B, -4), ("PE", S, -2), ("CE", S, 2), ("CE", B, 4), tags=("credit",)),
    _t("iron_butterfly", "Iron Butterfly", "neutral", "Short straddle with protective wings.",
       ("PE", B, -3), ("PE", S, 0), ("CE", S, 0), ("CE", B, 3), tags=("credit",)),
    _t("long_call_butterfly", "Long Call Butterfly", "neutral", "Buy 1 lower, sell 2 ATM, buy 1 higher call: cheap pin bet.",
       ("CE", B, -2), ("CE", S, 0, 2), ("CE", B, 2)),
    _t("long_put_butterfly", "Long Put Butterfly", "neutral", "Buy 1 higher, sell 2 ATM, buy 1 lower put.",
       ("PE", B, 2), ("PE", S, 0, 2), ("PE", B, -2)),
    _t("broken_wing_butterfly", "Broken Wing Call Butterfly", "bullish", "Butterfly with a wider upper wing, often for a credit.",
       ("CE", B, -2), ("CE", S, 0, 2), ("CE", B, 3)),
    _t("call_ratio_spread", "Call Ratio Spread (1x2)", "neutral", "Buy 1 ATM call, sell 2 higher calls; risk above upper strike.",
       ("CE", B, 0), ("CE", S, 2, 2)),
    _t("put_ratio_spread", "Put Ratio Spread (1x2)", "neutral", "Buy 1 ATM put, sell 2 lower puts; risk below lower strike.",
       ("PE", B, 0), ("PE", S, -2, 2)),
    _t("call_ratio_backspread", "Call Ratio Backspread", "volatile", "Sell 1 ATM call, buy 2 higher calls: profits on a sharp rally.",
       ("CE", S, 0), ("CE", B, 2, 2)),
    _t("put_ratio_backspread", "Put Ratio Backspread", "volatile", "Sell 1 ATM put, buy 2 lower puts: profits on a sharp fall.",
       ("PE", S, 0), ("PE", B, -2, 2)),
    _t("jade_lizard", "Jade Lizard", "bullish", "Short OTM put + short call spread; no upside risk if credit > spread width.",
       ("PE", S, -2), ("CE", S, 2), ("CE", B, 4), tags=("credit",)),
    _t("synthetic_long", "Synthetic Long", "bullish", "Long call + short put at ATM: behaves like owning the underlying.",
       ("CE", B, 0), ("PE", S, 0)),
    _t("synthetic_short", "Synthetic Short", "bearish", "Short call + long put at ATM: behaves like shorting the underlying.",
       ("CE", S, 0), ("PE", B, 0)),
    _t("long_call_calendar", "Long Call Calendar", "neutral", "Sell near-expiry ATM call, buy next-expiry ATM call.",
       ("CE", S, 0, 1, 0), ("CE", B, 0, 1, 1), tags=("calendar",)),
    _t("long_put_calendar", "Long Put Calendar", "neutral", "Sell near-expiry ATM put, buy next-expiry ATM put.",
       ("PE", S, 0, 1, 0), ("PE", B, 0, 1, 1), tags=("calendar",)),
]

BUILTIN_BY_ID = {t.id: t for t in BUILTIN_TEMPLATES}
