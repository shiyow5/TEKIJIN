"""Password hashing (PBKDF2-HMAC-SHA256, Django-style encoding).

We deliberately use the standard library (:func:`hashlib.pbkdf2_hmac`) rather than
argon2/bcrypt: those are C extensions that must build/ship a wheel for the DGX's
ARM64 target and would add a pinned dependency, whereas PBKDF2-SHA256 is the
long-standing Django default, dependency-free, and CI-light (matching this repo's
"keep base deps light" stance in requirements.txt). The encoded string carries the
algorithm, iteration count and per-password random salt, so the parameters can be
raised later — or the scheme swapped for argon2 — without a data migration
(``verify_password`` reads the stored parameters).

Format: ``pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>`` (base64 without
padding). ``verify_password`` uses a constant-time comparison.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets

_ALGORITHM = "pbkdf2_sha256"
# OWASP-recommended floor for PBKDF2-HMAC-SHA256 (2023): 600k iterations.
_DEFAULT_ITERATIONS = 600_000
_SALT_BYTES = 16


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    # Restore the stripped ``=`` padding (base64 needs length % 4 == 0).
    padding = "=" * (-len(text) % 4)
    return base64.b64decode(text + padding)


def hash_password(password: str, *, iterations: int = _DEFAULT_ITERATIONS) -> str:
    """Hash ``password`` with a fresh random salt; return the encoded string.

    Raises ``ValueError`` on an empty password (a blank credential must never be
    hashed into a usable login).
    """

    if not password:
        raise ValueError("password must not be empty")
    salt = secrets.token_bytes(_SALT_BYTES)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{_ALGORITHM}${iterations}${_b64(salt)}${_b64(derived)}"


def verify_password(password: str, encoded: str | None) -> bool:
    """Constant-time check of ``password`` against a stored ``encoded`` hash.

    Returns ``False`` (never raises) for a missing/blank password, a ``None``/blank
    stored hash, or a malformed/unknown-scheme encoding — a login attempt must fail
    closed, not error out.
    """

    if not password or not encoded:
        return False
    try:
        algorithm, iter_str, salt_b64, hash_b64 = encoded.split("$")
    except ValueError:
        return False
    if algorithm != _ALGORITHM:
        return False
    try:
        iterations = int(iter_str)
        salt = _unb64(salt_b64)
        expected = _unb64(hash_b64)
    except (ValueError, binascii.Error):
        return False
    if iterations <= 0:
        return False
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(derived, expected)
