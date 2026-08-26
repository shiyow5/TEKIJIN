"""Slack Events API request signature verification (HMAC-SHA256, "v0" scheme).

See https://api.slack.com/authentication/verifying-requests-from-slack — every
Events API POST carries ``X-Slack-Signature`` and ``X-Slack-Request-Timestamp``
headers. This recomputes the same HMAC over ``v0:{timestamp}:{raw_body}`` using
the app's signing secret and compares it in constant time, and separately
rejects a stale timestamp (replay protection) — a request outside the window
fails even with a mathematically correct signature.
"""

from __future__ import annotations

import hashlib
import hmac
import time

_MAX_CLOCK_SKEW_SECONDS = 60 * 5


def verify_signature(
    *,
    signing_secret: str,
    timestamp: str,
    signature: str,
    body: bytes,
    now: float | None = None,
) -> bool:
    """True iff ``signature`` is a valid, fresh Slack signature for ``body``.

    Fails closed: a blank ``signing_secret`` (no Slack App configured yet, the
    default) never verifies, regardless of what's sent.
    """

    if not signing_secret or not timestamp or not signature:
        return False
    try:
        sent_at = int(timestamp)
    except ValueError:
        return False
    current = now if now is not None else time.time()
    if abs(current - sent_at) > _MAX_CLOCK_SKEW_SECONDS:
        return False
    base = f"v0:{timestamp}:".encode() + body
    digest = hmac.new(signing_secret.encode(), base, hashlib.sha256).hexdigest()
    expected = f"v0={digest}"
    return hmac.compare_digest(expected, signature)
