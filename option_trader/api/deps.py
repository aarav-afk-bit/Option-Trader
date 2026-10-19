"""Shared FastAPI dependencies."""

from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import HTTPException, Request

from ..providers.manager import ProviderManager


def get_manager(request: Request) -> ProviderManager:
    return request.app.state.manager


def parse_expiry(expiry: Optional[str]) -> Optional[date]:
    if not expiry:
        return None
    try:
        return date.fromisoformat(expiry)
    except ValueError as exc:
        raise HTTPException(400, f"expiry must be YYYY-MM-DD, got {expiry!r}") from exc
