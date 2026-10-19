"""Paper-trading endpoints."""

from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..errors import DataUnavailableError
from ..portfolio.store import PortfolioStore, mark_to_market
from ..providers.manager import ProviderManager
from .deps import get_manager, parse_expiry

router = APIRouter(prefix="/api/portfolio")


def get_portfolio(request: Request) -> PortfolioStore:
    return request.app.state.portfolio


class LegIn(BaseModel):
    option_type: str
    action: str
    strike: float = 0.0
    lots: int = 1
    expiry: Optional[str] = None


class OpenIn(BaseModel):
    name: str = Field("Position", max_length=80)
    symbol: str
    expiry: Optional[str] = None
    source: Optional[str] = None
    fill: str = "ltp"
    notes: str = ""
    legs: list[LegIn]


class CloseIn(BaseModel):
    fill: str = "ltp"


def _chain_for(manager: ProviderManager, symbol: str, source: Optional[str], default: Optional[date]):
    def get(exp: Optional[date]):
        return manager.get_chain(symbol, exp or default, source)
    return get


@router.get("")
def list_positions(store: PortfolioStore = Depends(get_portfolio),
                   manager: ProviderManager = Depends(get_manager)) -> dict:
    out = []
    for pos in store.list():
        out.append(mark_to_market(pos, _chain_for(manager, pos["symbol"], pos["source"], None)))
    open_pnl = [p["pnl"] for p in out if p["status"] == "open"]
    return {
        "positions": out,
        "open_pnl": None if any(v is None for v in open_pnl) else round(sum(open_pnl), 2),
        "realized_pnl": round(sum(p.get("realized_pnl") or 0 for p in out if p["status"] == "closed"), 2),
    }


@router.post("/open")
def open_position(body: OpenIn, store: PortfolioStore = Depends(get_portfolio),
                  manager: ProviderManager = Depends(get_manager)) -> dict:
    if not body.legs:
        raise HTTPException(400, "Add at least one leg")
    try:
        return store.open(body.name, _chain_for(manager, body.symbol, body.source, parse_expiry(body.expiry)),
                          [leg.model_dump() for leg in body.legs], body.fill, body.notes)
    except DataUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/{pid}/close")
def close_position(pid: str, body: CloseIn, store: PortfolioStore = Depends(get_portfolio),
                   manager: ProviderManager = Depends(get_manager)) -> dict:
    pos = store.get(pid)
    if pos is None:
        raise HTTPException(404, "No such position")
    try:
        return store.close(pid, _chain_for(manager, pos["symbol"], pos["source"], None), body.fill)
    except DataUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.delete("/{pid}")
def delete_position(pid: str, store: PortfolioStore = Depends(get_portfolio)) -> dict:
    if not store.delete(pid):
        raise HTTPException(404, "No such position")
    return {"deleted": pid}
