"""A tiny in-process sliding-window rate limiter for write endpoints (#263).

Counts EVERY allowed event per key within a rolling window (unlike
``LoginRateLimiter``, which counts only failures). Used to throttle
``POST /feedback`` per actor so an authenticated user cannot flood the
append-only learning signal. In-process and lock-guarded — correct for the
single-worker API where several graph runs share one process across threads; a
durable/shared limiter for multi-worker is a separate concern (see #76).
"""

from __future__ import annotations

import datetime as dt
import threading
from collections import defaultdict, deque


class SlidingWindowLimiter:
    """Allow at most ``max_events`` per key within ``window_seconds`` (rolling)."""

    def __init__(self, *, max_events: int, window_seconds: float) -> None:
        self.max_events = max_events
        self._window = dt.timedelta(seconds=window_seconds)
        self._events: dict[str, deque[dt.datetime]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, *, now: dt.datetime | None = None) -> bool:
        """Record an event for ``key`` and return ``True`` if it is within the limit.

        A refused event (return ``False``) is NOT recorded, so a client that backs
        off after a 429 recovers as soon as the window slides, rather than being
        penalised for the rejected attempts.
        """

        now = now or dt.datetime.now(dt.UTC)
        cutoff = now - self._window
        with self._lock:
            bucket = self._events[key]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self.max_events:
                return False
            bucket.append(now)
            return True
