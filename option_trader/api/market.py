"""Market data endpoints: status, expiries, option chain, price history."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from ..errors import DataUnavailableError
from ..providers.manager import ProviderManager
from .deps import get_manager, parse_expiry

router = APIRouter(prefix="/api")


@router.get("/status")
def status(manager: ProviderManager = Depends(get_manager)) -> dict:
    return manager.status()


@router.get("/expiries")
def expiries(symbol: str, source: Optional[str] = None,
             manager: ProviderManager = Depends(get_manager)) -> dict:
    try:
        used, exps = manager.get_expiries(symbol, source)
    except DataUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"symbol": symbol.upper(), "source": used, "expiries": [e.isoformat() for e in exps]}


@router.get("/chain")
def chain(symbol: str, expiry: Optional[str] = None, source: Optional[str] = None,
          refresh: bool = False, manager: ProviderManager = Depends(get_manager)) -> dict:
    try:
        ch = manager.get_chain(symbol, parse_expiry(expiry), source, use_cache=not refresh)
    except DataUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc
    return ch.to_dict()


@router.get("/history")
def history(symbol: str, period: str = "1y", interval: str = "1d",
            manager: ProviderManager = Depends(get_manager)) -> dict:
    try:
        candles = manager.get_history(symbol, period, interval)
    except DataUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {
        "symbol": symbol.upper(),
        "candles": [
            {"t": c.ts.isoformat(), "o": c.open, "h": c.high, "l": c.low, "c": c.close, "v": c.volume}
            for c in candles
        ],
    }
