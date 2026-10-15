"""Strategy analysis endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..errors import DataUnavailableError
from ..providers.manager import ProviderManager
from ..strategy.engine import Leg, Strategy, analyze
from .deps import get_manager, parse_expiry

router = APIRouter(prefix="/api/strategy")


class LegIn(BaseModel):
    option_type: str
    action: str
    strike: float = 0.0
    lots: int = 1
    premium: Optional[float] = None  # None: take the live price from the chain
    expiry: Optional[str] = None
    iv: Optional[float] = None


class AnalyzeIn(BaseModel):
    symbol: str
    expiry: Optional[str] = None
    source: Optional[str] = None
    name: str = "Custom"
    legs: list[LegIn]
    lot_size: Optional[int] = None
    target_days: Optional[float] = Field(None, description="Days from now for the target-date curve")
    iv_shift: float = 0.0
    range_pct: float = 0.15
    fill: str = "ltp"


def build_strategy(body: AnalyzeIn, manager: ProviderManager):
    """Price every leg off the real chain; legs without a live price are rejected."""
    chain = manager.get_chain(body.symbol, parse_expiry(body.expiry), body.source)
    legs = []
    for raw in body.legs:
        otype, action = raw.option_type.upper(), raw.action.upper()
        if otype not in ("CE", "PE", "FUT") or action not in ("BUY", "SELL"):
            raise HTTPException(400, f"Invalid leg {raw.option_type} {raw.action}")
        leg_chain = chain
        if raw.expiry and parse_expiry(raw.expiry) != chain.expiry:
            leg_chain = manager.get_chain(body.symbol, parse_expiry(raw.expiry), chain.source)
        if otype == "FUT":
            leg = Leg("FUT", action, 0.0, raw.lots, raw.premium or chain.spot, leg_chain.expiry,
                      None, leg_chain.expiry_at)  # type: ignore[arg-type]
        else:
            q = leg_chain.quote(raw.strike, otype)  # type: ignore[arg-type]
            if q is None:
                raise HTTPException(400, f"{raw.strike:g} {otype} is not listed for {leg_chain.expiry}")
            if raw.premium is not None:
                premium = raw.premium
            elif body.fill == "market":
                premium = (q.ask if action == "BUY" else q.bid) or q.price
            elif body.fill == "mid":
                premium = q.mid or q.price
            else:
                premium = q.price
            if not premium:
                raise HTTPException(400, f"No live price for {raw.strike:g} {otype}")
            leg = Leg(otype, action, raw.strike, raw.lots, float(premium), leg_chain.expiry,  # type: ignore[arg-type]
                      raw.iv if raw.iv is not None else q.iv, leg_chain.expiry_at)
        legs.append(leg)
    lot = body.lot_size or chain.lot_size or 1
    return chain, Strategy(body.name, legs, lot)


@router.post("/analyze")
def analyze_endpoint(body: AnalyzeIn, manager: ProviderManager = Depends(get_manager)) -> dict:
    if not body.legs:
        raise HTTPException(400, "Add at least one leg")
    try:
        chain, strategy = build_strategy(body, manager)
    except DataUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc
    now = datetime.now(timezone.utc)
    target = now + timedelta(days=body.target_days) if body.target_days is not None else None
    result = analyze(strategy, chain.spot, manager.risk_free_rate, now=now, target=target,
                     iv_shift=body.iv_shift, range_pct=body.range_pct)
    result["source"] = chain.source
    result["symbol"] = chain.symbol
    result["chain_fetched_at"] = chain.fetched_at.isoformat()
    return result
