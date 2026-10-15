"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI

from . import __version__


def create_app() -> FastAPI:
    app = FastAPI(title="Option Trader", version=__version__)

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
