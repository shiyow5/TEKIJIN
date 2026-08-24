"""FastAPI access-control dependencies — the single seam ADR-0005 anticipated.

``require_principal`` resolves the caller's :class:`Principal` from a bearer token
(``Authorization: Bearer <jwt>``) or, for the SSE ``/events`` stream where a browser
``EventSource`` cannot set headers, a ``?token=`` query parameter. ``require_admin``
adds the admin check. ``require_can_act_as`` enforces that a non-admin only acts on
their own ``asker_id``/``responder_id``. All auth decisions live here, not scattered
across handlers.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from tekijin.auth.principal import Principal
from tekijin.auth.tokens import TokenError, decode_token
from tekijin.config import get_settings

_UNAUTHENTICATED = HTTPException(
    status_code=401,
    detail="ログインが必要です。",
    headers={"WWW-Authenticate": "Bearer"},
)


def _extract_token(request: Request, *, allow_query: bool) -> str | None:
    header = request.headers.get("Authorization")
    if header:
        scheme, _, value = header.partition(" ")
        if scheme.lower() == "bearer" and value.strip():
            return value.strip()
    # SSE-only fallback: a browser EventSource cannot send an Authorization header,
    # so /events (and only /events) accepts ?token=. Scoping it here keeps the token
    # out of query strings — and thus access/proxy logs — on every other route.
    if allow_query:
        query_token = request.query_params.get("token")
        if query_token and query_token.strip():
            return query_token.strip()
    return None


def _resolve_principal(request: Request, *, allow_query: bool) -> Principal:
    cached = getattr(request.state, "principal", None)
    if isinstance(cached, Principal):
        return cached
    token = _extract_token(request, allow_query=allow_query)
    if token is None:
        raise _UNAUTHENTICATED
    try:
        principal = decode_token(token, secret=get_settings().auth_secret)
    except TokenError as exc:
        raise HTTPException(
            status_code=401,
            detail="セッションの有効期限が切れました。もう一度ログインしてください。",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    request.state.principal = principal
    return principal


def require_principal(request: Request) -> Principal:
    """Resolve the authenticated principal from the ``Authorization`` header, or 401.

    The resolved principal is cached on ``request.state`` so a handler depending on
    both ``require_principal`` and ``require_admin`` decodes the token once.
    """

    return _resolve_principal(request, allow_query=False)


def require_principal_sse(request: Request) -> Principal:
    """Like :func:`require_principal`, but ALSO accepts a ``?token=`` query param.

    ONLY for the SSE ``/events`` stream, where a browser ``EventSource`` cannot set
    an ``Authorization`` header. No other route should use this — a token in the URL
    can leak into logs.
    """

    return _resolve_principal(request, allow_query=True)


def require_admin(request: Request) -> Principal:
    """Resolve the principal and require admin, else 403."""

    principal = require_principal(request)
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="管理者のみが利用できます。")
    return principal


def require_can_act_as(principal: Principal, employee_id: int) -> None:
    """Raise 403 unless ``principal`` may act on behalf of ``employee_id``."""

    if not principal.may_act_as(employee_id):
        raise HTTPException(
            status_code=403,
            detail="他の利用者として操作する権限がありません。",
        )


def require_session_participant(
    principal: Principal,
    asker_id: int | None,
    responder_id: int | None,
) -> None:
    """Object-level auth for the session-scoped endpoints (#241).

    Raise 403 unless ``principal`` is admin or the session's asker/responder. If the
    session is unknown (both ids ``None``) NOTHING is raised — the handler then
    returns its own 404 rather than a 403 that would confirm nothing exists. This
    also revokes a rerouted-away responder: after a decline the responder id becomes
    the NEW candidate, so the old one is no longer a participant.
    """

    if principal.is_admin:
        return
    participants = {pid for pid in (asker_id, responder_id) if pid is not None}
    if not participants:
        return  # unknown session — let the handler 404
    if principal.employee_id not in participants:
        raise HTTPException(
            status_code=403,
            detail="このセッションにアクセスする権限がありません。",
        )
