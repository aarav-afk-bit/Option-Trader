"""Valuation lab endpoints: price one live contract with every model."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from ..errors import DataUnavailableError
from ..pricing import lab
from ..pricing.valuation import MODELS
from ..providers.manager import ProviderManager
from .deps import get_manager, parse_expiry

router = APIRouter(prefix="/api/valuation")


def _chain(manager: ProviderManager, symbol: str, expiry: Optional[str], source: Optional[str]):
    try:
        return manager.get_chain(symbol, parse_expiry(expiry), source)
    except DataUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("/models")
def models() -> dict:
    return {"models": [m.__dict__ for m in MODELS]}


@router.get("/contract")
def contract(symbol: str, strike: float, kind: str, expiry: Optional[str] = None, source: Optional[str] = None,
             sigma: Optional[float] = None, paths: int = 100_000,
             manager: ProviderManager = Depends(get_manager)) -> dict:
    """Every model's value for one contract next to its live bid/ask."""
    if sigma is not None and not 0.005 <= sigma <= 5:
        raise HTTPException(422, "sigma must be between 0.005 and 5 (0.5% to 500%)")
    chain = _chain(manager, symbol, expiry, source)
    try:
        return lab.value_contract(chain, strike, kind, chain.rate or manager.risk_free_rate, sigma=sigma,
                                  mc_paths=min(max(paths, 5_000), 400_000))
    except lab.LabError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/smile")
def smile(symbol: str, expiry: Optional[str] = None, source: Optional[str] = None,
          manager: ProviderManager = Depends(get_manager)) -> dict:
    """Market IV smile and the Heston model calibrated to it."""
    chain = _chain(manager, symbol, expiry, source)
    try:
        return lab.heston_smile(chain, chain.rate or manager.risk_free_rate)
    except lab.LabError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/convergence")
def convergence(symbol: str, strike: float, kind: str, expiry: Optional[str] = None, source: Optional[str] = None,
                sigma: Optional[float] = None, manager: ProviderManager = Depends(get_manager)) -> dict:
    chain = _chain(manager, symbol, expiry, source)
    try:
        return lab.convergence(chain, strike, kind, chain.rate or manager.risk_free_rate, sigma=sigma)
    except lab.LabError as exc:
        raise HTTPException(422, str(exc)) from exc
