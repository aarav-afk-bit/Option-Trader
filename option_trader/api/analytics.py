"""Analytics endpoints."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from ..analytics.chain import analyze_chain
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
