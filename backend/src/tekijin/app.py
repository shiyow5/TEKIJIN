"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from tekijin import __version__
from tekijin.api.health import router as health_router
from tekijin.config import get_settings


def create_app() -> FastAPI:
    """Create and configure the FastAPI application instance."""
    settings = get_settings()
    app = FastAPI(title="TEKIJIN", version=__version__)

    # Explicit origins: a wildcard origin combined with allow_credentials=True is
    # rejected by browsers, so the allowed origins come from settings.cors_origins.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router)
    return app
