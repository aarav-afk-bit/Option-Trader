"""Market data endpoints: status, expiries, option chain, price history."""

from __future__ import annotations

from typing import Optional

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..errors import DataUnavailableError
from ..providers.manager import ProviderManager
from .deps import get_manager, parse_expiry

router = APIRouter(prefix="/api")


@router.get("/status")
def status(manager: ProviderManager = Depends(get_manager)) -> dict:
    return manager.status()


@router.get("/market")
def market(manager: ProviderManager = Depends(get_manager)) -> dict:
    return manager.market_status()


@router.get("/indices")
def indices(manager: ProviderManager = Depends(get_manager)) -> dict:
    from ..indices import get_indices

    try:
        return get_indices(manager)
    except DataUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc


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


@router.get("/stream")
async def stream(request: Request, symbol: str, expiry: Optional[str] = None, source: Optional[str] = None,
                 manager: ProviderManager = Depends(get_manager)):
    """Server-Sent Events: pushes the chain each time a live exchange tick changes it."""
    exp = parse_expiry(expiry)

    async def events():
        if manager.live is None:
            reason = ("Live ticks need Angel One credentials (ANGEL_API_KEY, ANGEL_CLIENT_CODE, ANGEL_PIN, "
                      "ANGEL_TOTP_SECRET). Showing polled snapshots instead.")
            yield f"event: status\ndata: {json.dumps({'live': False, 'reason': reason})}\n\n"
            return
        last_version, last_beat = -1, 0.0
        loop = asyncio.get_running_loop()
        while not await request.is_disconnected():
            version = manager.live.version
            if version != last_version:
                try:
                    ch = await asyncio.to_thread(manager.get_chain, symbol, exp, source)
                except DataUnavailableError as exc:
                    yield f"event: error\ndata: {json.dumps({'detail': str(exc)})}\n\n"
                    await asyncio.sleep(5)
                    continue
                last_version = version
                yield f"event: chain\ndata: {json.dumps(ch.to_dict())}\n\n"
                yield f"event: status\ndata: {json.dumps({'live': True, **manager.live.status()})}\n\n"
                last_beat = loop.time()
            elif loop.time() - last_beat > 15:
                yield f"event: status\ndata: {json.dumps({'live': True, **manager.live.status()})}\n\n"
                last_beat = loop.time()
            await asyncio.sleep(1.0)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
