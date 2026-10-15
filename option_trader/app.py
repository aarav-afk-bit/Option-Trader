"""FastAPI application factory."""

from __future__ import annotations

from typing import Optional

from fastapi import FastAPI

from . import __version__
from .api import market
from .providers.manager import ProviderManager


def create_app(manager: Optional[ProviderManager] = None) -> FastAPI:
    app = FastAPI(title="Option Trader", version=__version__)
    app.state.manager = manager or ProviderManager.from_settings()

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    app.include_router(market.router)
    return app


app = create_app()
