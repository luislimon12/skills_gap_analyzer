"""Rate limiting and anonymous sessions for the web app.

Cloudflare rate-limits at the edge as well; this is the in-app layer, keyed by
client IP and by anonymous session so one visitor can't exhaust the server.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections import defaultdict, deque

# action -> (max events, window in seconds)
DEFAULT_LIMITS = {
    "upload": (5, 3600),      # 5 resume uploads per hour
    "analysis": (30, 3600),   # 30 new analyses per hour
}


class RateLimiter:
    """Thread-safe sliding-window counter shared by all sessions in the process."""

    def __init__(self, limits: dict[str, tuple[int, int]] | None = None, clock=time.monotonic):
        self.limits = dict(limits or DEFAULT_LIMITS)
        self._clock = clock
        self._events: dict[tuple[str, str], deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, action: str, *keys: str) -> bool:
        """Record one event for every key, unless any key is over its limit."""
        limit, window = self.limits[action]
        now = self._clock()
        with self._lock:
            buckets = []
            for key in keys:
                if not key:
                    continue
                bucket = self._events[(action, key)]
                while bucket and now - bucket[0] >= window:
                    bucket.popleft()
                if len(bucket) >= limit:
                    return False
                buckets.append(bucket)
            for bucket in buckets:
                bucket.append(now)
            return True


def new_session_id() -> str:
    return secrets.token_urlsafe(24)


def client_ip(headers) -> str:
    """Visitor IP as seen by Cloudflare, falling back to the proxy chain."""
    if not headers:
        return ""
    for name in ("CF-Connecting-IP", "X-Real-IP"):
        value = headers.get(name)
        if value:
            return value.strip()
    forwarded = headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return ""
