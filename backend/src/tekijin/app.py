"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from tekijin import __version__
from tekijin.api.health import router as health_router
from tekijin.api.routes import router as api_router
from tekijin.api.service import AgentService
from tekijin.config import get_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    # The session dispatch registry is in-process, so the API MUST run a SINGLE
    # worker (a durable/sticky queue for multi-worker is a separate issue). On
    # shutdown, release the checkpointer pool and DB engine.
    logger.info("TEKIJIN API started (in-memory session registry — run a single worker)")
    yield
    app.state.agent_service.close()


def create_app(agent_service: AgentService | None = None) -> FastAPI:
    """Create and configure the FastAPI application instance.

    ``agent_service`` is injectable for tests (a MemorySaver / FakeEmbedder / stub
    service bound to a disposable DB); production builds the default from settings.
    """

    settings = get_settings()
    app = FastAPI(title="TEKIJIN", version=__version__, lifespan=_lifespan)

    # Explicit origins: a wildcard origin combined with allow_credentials=True is
    # rejected by browsers, so the allowed origins come from settings.cors_origins.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if agent_service is None:
        from tekijin.api.factory import build_default_service

        agent_service = build_default_service(settings)
    app.state.agent_service = agent_service

    app.include_router(health_router)
    app.include_router(api_router)
    return app
