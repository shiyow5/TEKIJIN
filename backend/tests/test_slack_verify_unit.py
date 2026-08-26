"""Unit tests for tekijin.slack.verify.verify_signature (Slack's documented
"v0" HMAC-SHA256 request-signing scheme)."""

from __future__ import annotations

import hashlib
import hmac

from tekijin.slack.verify import verify_signature

SECRET = "test-signing-secret"
BODY = b'{"type":"url_verification","challenge":"abc"}'
TIMESTAMP = "1700000000"


def _sign(secret: str, timestamp: str, body: bytes) -> str:
    base = f"v0:{timestamp}:".encode() + body
    digest = hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return f"v0={digest}"


def test_valid_signature_and_fresh_timestamp_verifies() -> None:
    signature = _sign(SECRET, TIMESTAMP, BODY)
    assert verify_signature(
        signing_secret=SECRET,
        timestamp=TIMESTAMP,
        signature=signature,
        body=BODY,
        now=int(TIMESTAMP),
    )


def test_wrong_secret_fails() -> None:
    signature = _sign("a-different-secret", TIMESTAMP, BODY)
    assert not verify_signature(
        signing_secret=SECRET,
        timestamp=TIMESTAMP,
        signature=signature,
        body=BODY,
        now=int(TIMESTAMP),
    )


def test_tampered_body_fails() -> None:
    signature = _sign(SECRET, TIMESTAMP, BODY)
    assert not verify_signature(
        signing_secret=SECRET,
        timestamp=TIMESTAMP,
        signature=signature,
        body=b'{"type":"tampered"}',
        now=int(TIMESTAMP),
    )


def test_stale_timestamp_fails_even_with_a_correct_signature() -> None:
    signature = _sign(SECRET, TIMESTAMP, BODY)
    ten_minutes_later = int(TIMESTAMP) + 601
    assert not verify_signature(
        signing_secret=SECRET,
        timestamp=TIMESTAMP,
        signature=signature,
        body=BODY,
        now=ten_minutes_later,
    )


def test_blank_signing_secret_never_verifies() -> None:
    signature = _sign("", TIMESTAMP, BODY)
    assert not verify_signature(
        signing_secret="",
        timestamp=TIMESTAMP,
        signature=signature,
        body=BODY,
        now=int(TIMESTAMP),
    )


def test_non_numeric_timestamp_fails() -> None:
    assert not verify_signature(
        signing_secret=SECRET,
        timestamp="not-a-number",
        signature="v0=whatever",
        body=BODY,
    )


def test_missing_signature_fails() -> None:
    assert not verify_signature(
        signing_secret=SECRET,
        timestamp=TIMESTAMP,
        signature="",
        body=BODY,
        now=int(TIMESTAMP),
    )
