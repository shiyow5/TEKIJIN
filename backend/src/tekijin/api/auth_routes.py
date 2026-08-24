"""Authentication endpoints: POST /auth/login, GET /auth/me, POST /auth/logout.

Login verifies credentials (:func:`tekijin.auth.service.authenticate`) behind an
in-process brute-force limiter and returns a bearer JWT. ``/me`` echoes the token's
principal (the frontend's session-restore call). ``/logout`` is stateless — the
client drops the token — and exists only for a clean contract.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from tekijin.api import schemas
from tekijin.auth.deps import require_principal
from tekijin.auth.principal import Principal
from tekijin.auth.service import authenticate
from tekijin.auth.tokens import create_access_token
from tekijin.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _principal_response(principal: Principal) -> schemas.PrincipalResponse:
    return schemas.PrincipalResponse(
        id=(
            None
            if principal.employee_id is None
            else schemas.format_employee_id(principal.employee_id)
        ),
        name=principal.name,
        dept=principal.dept,
        is_admin=principal.is_admin,
    )


@router.post("/login", response_model=schemas.LoginResponse)
def login(req: schemas.LoginRequest, request: Request) -> schemas.LoginResponse:
    """Verify email+password; return a bearer token and the resolved principal.

    A uniform 401 is returned whether the email is unknown or the password is
    wrong (no account enumeration). Repeated failures for one email are throttled
    with a 429 before the password is even checked.
    """

    settings = get_settings()
    limiter = request.app.state.login_rate_limiter
    if not limiter.check(req.email):
        raise HTTPException(
            status_code=429,
            detail="ログイン試行が多すぎます。しばらくしてからお試しください。",
            headers={"Retry-After": "60"},
        )

    service = request.app.state.agent_service
    with service.session_factory() as session:
        principal = authenticate(req.email, req.password, session=session, settings=settings)

    if principal is None:
        limiter.record_failure(req.email)
        raise HTTPException(status_code=401, detail="メールアドレスまたはパスワードが違います。")

    limiter.reset(req.email)
    token = create_access_token(
        principal,
        secret=settings.auth_secret,
        ttl_hours=settings.auth_token_ttl_hours,
    )
    return schemas.LoginResponse(access_token=token, principal=_principal_response(principal))


@router.get("/me", response_model=schemas.PrincipalResponse)
def me(principal: Principal = Depends(require_principal)) -> schemas.PrincipalResponse:
    """Echo the authenticated principal (frontend session restore)."""

    return _principal_response(principal)


@router.post("/logout", response_model=schemas.AckResponse)
def logout() -> schemas.AckResponse:
    """Stateless logout: the client drops the token. No server session to revoke."""

    return schemas.AckResponse(session_id="", status="logged_out")
