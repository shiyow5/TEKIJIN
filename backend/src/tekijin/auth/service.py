"""Credential verification and login rate limiting.

:func:`authenticate` resolves an ``email``/``password`` pair to a
:class:`Principal` (or ``None``). The ADMIN is a settings-configured account (not a
DB employee): its credentials are matched first, constant-time. Otherwise the email
is looked up in ``employees`` and the password checked against the stored PBKDF2
hash. Password hashes are read straight off the ``Employee`` row here, never via the
general :class:`EmployeeDTO` (so the hash cannot leak through the ordinary read
path).

:class:`LoginRateLimiter` is an in-process sliding-window limiter keyed by email —
correct because the API runs a single worker (see ``app._lifespan``). It throttles
brute-force guessing without a datastore; a distributed limiter is a follow-up if
the API ever scales past one worker.
"""

from __future__ import annotations

import datetime as dt
import hmac
from collections import defaultdict, deque

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tekijin.auth.passwords import verify_password
from tekijin.auth.principal import Principal
from tekijin.config import Settings
from tekijin.models.tables import Employee


def _norm_email(email: str) -> str:
    return email.strip().lower()


def authenticate(
    email: str,
    password: str,
    *,
    session: Session,
    settings: Settings,
) -> Principal | None:
    """Return the :class:`Principal` for valid credentials, else ``None``.

    Never raises on bad input (empty email/password → ``None``): a login attempt
    must fail closed, and the caller returns a uniform 401 that does not reveal
    whether the email exists.
    """

    if not email or not password:
        return None
    normalized = _norm_email(email)

    # Admin: settings-configured, not an employee. Compare both fields
    # constant-time (email compared too, so timing can't confirm the admin email).
    admin_email_matches = hmac.compare_digest(normalized, _norm_email(settings.admin_email))
    admin_password_matches = hmac.compare_digest(password, settings.admin_password)
    if admin_email_matches and admin_password_matches and settings.admin_password:
        return Principal(employee_id=None, name=settings.admin_name, dept=None, is_admin=True)

    row = session.scalars(select(Employee).where(func.lower(Employee.email) == normalized)).first()
    if row is None or not verify_password(password, row.password_hash):
        return None
    return Principal(employee_id=row.id, name=row.name, dept=row.department, is_admin=False)


class LoginRateLimiter:
    """Sliding-window failed-attempt limiter, keyed by (normalized) email.

    ``check`` returns ``True`` while the key is under the limit; ``record_failure``
    logs a failed attempt; ``reset`` clears the key on a successful login so a user
    is not penalised by earlier typos. Time is injectable for tests.
    """

    def __init__(self, *, max_attempts: int = 5, window_seconds: float = 300.0) -> None:
        self._max_attempts = max_attempts
        self._window = dt.timedelta(seconds=window_seconds)
        self._failures: dict[str, deque[dt.datetime]] = defaultdict(deque)

    def _prune(self, key: str, now: dt.datetime) -> None:
        bucket = self._failures[key]
        cutoff = now - self._window
        while bucket and bucket[0] < cutoff:
            bucket.popleft()

    def check(self, email: str, *, now: dt.datetime | None = None) -> bool:
        """True when another attempt is allowed for ``email``."""

        now = now or dt.datetime.now(dt.UTC)
        key = _norm_email(email)
        self._prune(key, now)
        return len(self._failures[key]) < self._max_attempts

    def record_failure(self, email: str, *, now: dt.datetime | None = None) -> None:
        now = now or dt.datetime.now(dt.UTC)
        key = _norm_email(email)
        self._prune(key, now)
        self._failures[key].append(now)

    def reset(self, email: str) -> None:
        self._failures.pop(_norm_email(email), None)
