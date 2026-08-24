"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from tekijin import __version__
from tekijin.api.auth_routes import router as auth_router
from tekijin.api.health import router as health_router
from tekijin.api.routes import router as api_router
from tekijin.api.service import AgentService
from tekijin.auth.service import LoginRateLimiter
from tekijin.config import DEV_ADMIN_PASSWORD, DEV_AUTH_SECRET, Settings, get_settings

logger = logging.getLogger(__name__)


def _enforce_secure_auth(settings: Settings) -> None:
    """Refuse to start on the INSECURE default auth secrets when auth is enforced.

    Without this, forgetting ``TEKIJIN_AUTH_SECRET`` in a non-dev deploy would boot
    silently with a publicly-known JWT signing secret — anyone could forge an admin
    token offline. Mirrors the ``durability_enforced`` fail-closed pattern (#241).
    """

    if not settings.auth_enforced():
        return
    insecure = []
    if settings.auth_secret == DEV_AUTH_SECRET:
        insecure.append("TEKIJIN_AUTH_SECRET")
    if settings.admin_password == DEV_ADMIN_PASSWORD:
        insecure.append("TEKIJIN_ADMIN_PASSWORD")
    if insecure:
        raise RuntimeError(
            "Refusing to start with insecure default auth credentials "
            f"({', '.join(insecure)}) while auth is enforced "
            f"(app_env={settings.app_env!r}, strict_auth={settings.strict_auth!r}). "
            "Set them to strong values, or set TEKIJIN_STRICT_AUTH=false to override."
        )


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
    _enforce_secure_auth(settings)
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
    # In-process login throttle (single-worker API; see _lifespan). Shared by all
    # /auth/login calls so brute-force attempts are counted per email across the
    # process.
    app.state.login_rate_limiter = LoginRateLimiter(
        max_attempts=settings.login_max_attempts,
        window_seconds=settings.login_window_seconds,
    )

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(api_router)
    return app
