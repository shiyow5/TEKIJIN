"""In-process de-dup guard for Slack's Events API ``event_id`` (#hand-off-chat).

Slack retries a delivery whenever the receiving endpoint doesn't ack (200)
within its budget, or errors — without a guard, a retry re-processes the same
event and inserts a duplicate chat message. Single-worker API (see
``AgentService``'s module docstring on the in-process session registry), so
in-process state is correct here the same way ``LoginRateLimiter`` and
``SlidingWindowLimiter`` already are — no shared/durable store needed.
"""

from __future__ import annotations

import threading
from collections import OrderedDict


class SeenEventIds:
    """Remembers up to ``max_size`` recently-seen ``event_id``s (oldest evicted
    first) — bounded so a long-running process can't leak memory over time."""

    def __init__(self, *, max_size: int = 2000) -> None:
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._max_size = max_size
        self._lock = threading.Lock()

    def seen_before(self, event_id: str) -> bool:
        """Record ``event_id`` and return whether it was already seen."""

        with self._lock:
            if event_id in self._seen:
                self._seen.move_to_end(event_id)
                return True
            self._seen[event_id] = None
            if len(self._seen) > self._max_size:
                self._seen.popitem(last=False)
            return False
