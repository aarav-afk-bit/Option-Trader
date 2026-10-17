"""Analytics endpoints."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from ..analytics.chain import analyze_chain, atm_iv
from ..analytics.recommender import recommend
from ..analytics.technicals import technical_snapshot
from ..strategy.templates import ResolveError
from ..errors import DataUnavailableError
from ..providers.manager import ProviderManager
from .deps import get_manager, parse_expiry

router = APIRouter(prefix="/api/analytics")


@router.get("/chain")
def chain_analytics(symbol: str, expiry: Optional[str] = None, source: Optional[str] = None,
                    manager: ProviderManager = Depends(get_manager)) -> dict:
    try:
        chain = manager.get_chain(symbol, parse_expiry(expiry), source)
    except DataUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc
    out = analyze_chain(chain).to_dict()
    out.update(symbol=chain.symbol, expiry=chain.expiry.isoformat(), spot=chain.spot,
               source=chain.source, fetched_at=chain.fetched_at.isoformat())
    return out


@router.get("/technicals")
def technicals(symbol: str, expiry: Optional[str] = None, source: Optional[str] = None,
               manager: ProviderManager = Depends(get_manager)) -> dict:
    try:
        candles = manager.get_history(symbol, "1y", "1d")
    except DataUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc
    iv = None
    try:
        iv = atm_iv(manager.get_chain(symbol, parse_expiry(expiry), source))
    except DataUnavailableError:
        pass  # technicals still work without the chain; volatility view will say "unknown"
    try:
        snap = technical_snapshot(candles, iv)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    snap.update(symbol=symbol.upper(), candles=len(candles), last_candle=candles[-1].ts.isoformat())
    return snap


@router.get("/recommendations")
def recommendations(symbol: str, expiry: Optional[str] = None, source: Optional[str] = None,
                    lots: int = 1, manager: ProviderManager = Depends(get_manager)) -> dict:
    """What to buy/sell right now, derived from the live chain and price history."""
    try:
        chain = manager.get_chain(symbol, parse_expiry(expiry), source)
    except DataUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc
    try:
        candles = manager.get_history(symbol, "1y", "1d")
    except DataUnavailableError:
        candles = None

    def later_chain(offset: int):
        later = [e for e in chain.expiries if e > chain.expiry]
        if len(later) < offset:
            raise ResolveError("No later expiry listed")
        try:
            return manager.get_chain(symbol, later[offset - 1], chain.source)
        except DataUnavailableError as exc:
            raise ResolveError(str(exc)) from exc

    return recommend(chain, candles, manager.risk_free_rate, max(lots, 1), later_chain)
