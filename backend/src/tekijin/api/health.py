"""Health-check endpoint."""

from __future__ import annotations

from fastapi import APIRouter

from tekijin import __version__
from tekijin.config import get_settings

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    """Return service liveness, version, and environment."""
    settings = get_settings()
    return {"status": "ok", "version": __version__, "env": settings.app_env}
