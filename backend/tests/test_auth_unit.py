"""Unit tests for the auth primitives (DB-free): passwords, tokens, principal,
and the login rate limiter (#241)."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi import HTTPException

from tekijin.app import _enforce_secure_auth
from tekijin.auth.deps import require_can_act_as, require_session_participant
from tekijin.auth.passwords import hash_password, verify_password
from tekijin.auth.principal import Principal
from tekijin.auth.service import LoginRateLimiter
from tekijin.auth.tokens import TokenError, create_access_token, decode_token
from tekijin.config import DEV_ADMIN_PASSWORD, DEV_AUTH_SECRET, Settings


# --- passwords -------------------------------------------------------------- #
def test_hash_verify_roundtrip() -> None:
    encoded = hash_password("s3cret-pw")
    assert encoded.startswith("pbkdf2_sha256$")
    assert verify_password("s3cret-pw", encoded) is True
    assert verify_password("wrong", encoded) is False


def test_hash_is_salted_unique() -> None:
    # Two hashes of the same password differ (random salt) but both verify.
    a = hash_password("same")
    b = hash_password("same")
    assert a != b
    assert verify_password("same", a) and verify_password("same", b)


def test_empty_password_rejected() -> None:
    with pytest.raises(ValueError, match="empty"):
        hash_password("")


@pytest.mark.parametrize(
    "pw,stored",
    [
        ("x", None),
        ("", "pbkdf2_sha256$1$aa$bb"),
        ("x", ""),
        ("x", "not-a-hash"),
        ("x", "bcrypt$12$abc$def"),  # unknown scheme
        ("x", "pbkdf2_sha256$notint$aa$bb"),
        ("x", "pbkdf2_sha256$0$aa$bb"),  # non-positive iterations
        ("x", "pbkdf2_sha256$1$!!!$bb"),  # bad base64
    ],
)
def test_verify_fails_closed_on_bad_input(pw: str, stored: str | None) -> None:
    assert verify_password(pw, stored) is False


# --- tokens ----------------------------------------------------------------- #
def test_token_roundtrip_user() -> None:
    p = Principal(employee_id=17, name="社員17", dept="SE", is_admin=False)
    token = create_access_token(p, secret="k", ttl_hours=1)
    assert decode_token(token, secret="k") == p


def test_token_roundtrip_admin_has_no_employee_id() -> None:
    p = Principal(employee_id=None, name="管理者", dept=None, is_admin=True)
    got = decode_token(create_access_token(p, secret="k", ttl_hours=1), secret="k")
    assert got == p and got.employee_id is None and got.is_admin


def test_token_wrong_secret_rejected() -> None:
    token = create_access_token(Principal(1, "a", None, False), secret="right", ttl_hours=1)
    with pytest.raises(TokenError):
        decode_token(token, secret="wrong")


def test_token_expired_rejected() -> None:
    token = create_access_token(Principal(1, "a", None, False), secret="k", ttl_hours=-1)
    with pytest.raises(TokenError):
        decode_token(token, secret="k")


def test_token_garbage_rejected() -> None:
    with pytest.raises(TokenError):
        decode_token("not.a.jwt", secret="k")


# --- principal authorization ------------------------------------------------ #
def test_may_act_as() -> None:
    user = Principal(5, "u", None, False)
    admin = Principal(None, "管理者", None, True)
    assert user.may_act_as(5) is True
    assert user.may_act_as(6) is False
    assert admin.may_act_as(6) is True  # admin may impersonate anyone


# --- object-level session authorization ------------------------------------ #
def test_require_session_participant() -> None:
    admin = Principal(None, "管理者", None, True)
    asker = Principal(10, "asker", None, False)
    responder = Principal(1, "responder", None, False)
    stranger = Principal(99, "他人", None, False)

    # Admin may access any session.
    require_session_participant(admin, 10, 1)
    # The asker and the responder may access their session.
    require_session_participant(asker, 10, 1)
    require_session_participant(responder, 10, 1)
    # An unknown session (both None) never raises — the handler 404s instead.
    require_session_participant(stranger, None, None)

    # A non-participant is rejected.
    with pytest.raises(HTTPException) as exc:
        require_session_participant(stranger, 10, 1)
    assert exc.value.status_code == 403

    # A rerouted-away responder (no longer the current responder) is rejected.
    with pytest.raises(HTTPException):
        require_session_participant(responder, 10, 2)


def test_require_can_act_as_raises_403_for_other() -> None:
    with pytest.raises(HTTPException) as exc:
        require_can_act_as(Principal(5, "u", None, False), 6)
    assert exc.value.status_code == 403
    # self and admin do not raise
    require_can_act_as(Principal(5, "u", None, False), 5)
    require_can_act_as(Principal(None, "a", None, True), 6)


# --- fail-closed on insecure default auth secrets --------------------------- #
def test_enforce_secure_auth_refuses_default_secret_when_enforced() -> None:
    # Non-dev env + default secret → refuse to boot.
    with pytest.raises(RuntimeError, match="TEKIJIN_AUTH_SECRET"):
        _enforce_secure_auth(Settings(app_env="production", auth_secret=DEV_AUTH_SECRET))
    # Default admin password is also flagged.
    with pytest.raises(RuntimeError, match="TEKIJIN_ADMIN_PASSWORD"):
        _enforce_secure_auth(
            Settings(
                app_env="production",
                auth_secret="a-strong-secret",
                admin_password=DEV_ADMIN_PASSWORD,
            )
        )


def test_enforce_secure_auth_allows_dev_and_overridden_secrets() -> None:
    # Development env: defaults are fine (no raise).
    _enforce_secure_auth(Settings(app_env="development"))
    # Non-dev but real secrets set: fine.
    _enforce_secure_auth(
        Settings(app_env="production", auth_secret="a-strong-secret", admin_password="a-strong-pw")
    )
    # Explicit escape hatch: strict_auth=False overrides even in prod.
    _enforce_secure_auth(
        Settings(app_env="production", strict_auth=False, auth_secret=DEV_AUTH_SECRET)
    )


# --- rate limiter ----------------------------------------------------------- #
def test_rate_limiter_blocks_after_max_and_resets() -> None:
    now = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    limiter = LoginRateLimiter(max_attempts=3, window_seconds=300)
    for _ in range(3):
        assert limiter.check("a@x", now=now) is True
        limiter.record_failure("a@x", now=now)
    assert limiter.check("a@x", now=now) is False  # 4th blocked
    # A different email is unaffected.
    assert limiter.check("b@x", now=now) is True
    # A successful login resets the counter.
    limiter.reset("a@x")
    assert limiter.check("a@x", now=now) is True


def test_rate_limiter_window_expires() -> None:
    now = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    limiter = LoginRateLimiter(max_attempts=2, window_seconds=60)
    limiter.record_failure("a@x", now=now)
    limiter.record_failure("a@x", now=now)
    assert limiter.check("a@x", now=now) is False
    later = now + dt.timedelta(seconds=61)
    assert limiter.check("a@x", now=later) is True  # old failures aged out


def test_rate_limiter_is_case_insensitive_on_email() -> None:
    now = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    limiter = LoginRateLimiter(max_attempts=1, window_seconds=300)
    limiter.record_failure("A@X.com", now=now)
    assert limiter.check("a@x.com", now=now) is False
