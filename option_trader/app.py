"""FastAPI application factory."""

from __future__ import annotations

from typing import Optional

from fastapi import FastAPI

from . import __version__
from .api import analytics, market, strategies
from .config import get_settings
from .providers.manager import ProviderManager
from .strategy.store import StrategyStore


def create_app(manager: Optional[ProviderManager] = None,
               store: Optional[StrategyStore] = None) -> FastAPI:
    app = FastAPI(title="Option Trader", version=__version__)
    app.state.manager = manager or ProviderManager.from_settings()
    app.state.store = store or StrategyStore(get_settings().data_dir / "strategies.json")

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    app.include_router(market.router)
    app.include_router(strategies.router)
    app.include_router(analytics.router)
    return app


app = create_app()
