"""FastAPI application factory."""

from __future__ import annotations

from typing import Optional

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import analytics, market, portfolio, settings, strategies, valuation
from .config import get_settings
from .providers.manager import ProviderManager
from .portfolio.store import PortfolioStore
from .strategy.store import StrategyStore


def create_app(manager: Optional[ProviderManager] = None,
               store: Optional[StrategyStore] = None,
               portfolio_store: Optional[PortfolioStore] = None) -> FastAPI:
    app = FastAPI(title="Option Trader", version=__version__)
    app.state.manager = manager or ProviderManager.from_settings()
    app.state.store = store or StrategyStore(get_settings().data_dir / "strategies.json")
    app.state.portfolio = portfolio_store or PortfolioStore(get_settings().data_dir / "portfolio.json")

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    app.include_router(market.router)
    app.include_router(strategies.router)
    app.include_router(analytics.router)
    app.include_router(portfolio.router)
    app.include_router(settings.router)
    app.include_router(valuation.router)

    static_dir = Path(__file__).parent / "web" / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    return app


app = create_app()
