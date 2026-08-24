"""JWT access tokens (HS256) carrying the :class:`Principal`.

Stateless auth: the token embeds the identity and an expiry (``exp``), so no
server-side session store is needed and "失効" is the ``exp`` claim. Logout is a
client-side token drop (documented on ``POST /auth/logout``). Tokens are sent as
``Authorization: Bearer <token>`` (never a cookie), so CSRF does not apply.
"""

from __future__ import annotations

import datetime as dt

import jwt

from tekijin.auth.principal import Principal

_ALGORITHM = "HS256"
# ``sub`` sentinel for the admin principal, which has no employee id.
_ADMIN_SUBJECT = "admin"


class TokenError(Exception):
    """Raised when a token is missing required claims, invalid, or expired."""


def create_access_token(
    principal: Principal,
    *,
    secret: str,
    ttl_hours: float,
    now: dt.datetime | None = None,
) -> str:
    """Sign a JWT for ``principal`` expiring ``ttl_hours`` from ``now`` (UTC)."""

    issued = now or dt.datetime.now(dt.UTC)
    expires = issued + dt.timedelta(hours=ttl_hours)
    subject = _ADMIN_SUBJECT if principal.employee_id is None else str(principal.employee_id)
    payload = {
        "sub": subject,
        "is_admin": principal.is_admin,
        "name": principal.name,
        "dept": principal.dept,
        "iat": int(issued.timestamp()),
        "exp": int(expires.timestamp()),
    }
    return jwt.encode(payload, secret, algorithm=_ALGORITHM)


def decode_token(token: str, *, secret: str) -> Principal:
    """Verify ``token``'s signature and expiry; reconstruct the :class:`Principal`.

    Raises :class:`TokenError` for any invalid/expired token or missing claim, so
    the caller fails closed with a 401 rather than trusting a partial identity.
    """

    try:
        payload = jwt.decode(token, secret, algorithms=[_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc

    subject = payload.get("sub")
    name = payload.get("name")
    if not isinstance(subject, str) or not subject or not isinstance(name, str):
        raise TokenError("token is missing required claims")
    is_admin = bool(payload.get("is_admin", False))
    dept = payload.get("dept")
    if dept is not None and not isinstance(dept, str):
        raise TokenError("token has a malformed 'dept' claim")

    if subject == _ADMIN_SUBJECT:
        # A non-admin token must never carry the admin subject sentinel.
        if not is_admin:
            raise TokenError("admin subject without admin flag")
        return Principal(employee_id=None, name=name, dept=dept, is_admin=True)

    try:
        employee_id = int(subject)
    except ValueError as exc:
        raise TokenError("token subject is not an employee id") from exc
    # A user token must not claim admin (admin has no employee id).
    return Principal(employee_id=employee_id, name=name, dept=dept, is_admin=False)
