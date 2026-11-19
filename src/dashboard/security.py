"""Rate limiting and anonymous sessions for the web app.

Cloudflare rate-limits at the edge as well; this is the in-app layer, keyed by
client IP and by anonymous session so one visitor can't exhaust the server.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections import deque

# action -> (max events, window in seconds)
DEFAULT_LIMITS = {
    "upload": (5, 3600),      # 5 resume uploads per hour
    "analysis": (30, 3600),   # 30 new analyses per hour
}
_SWEEP_EVERY = 256


class RateLimiter:
    """Thread-safe sliding-window counter shared by all sessions in the process."""

    def __init__(self, limits: dict[str, tuple[int, int]] | None = None, clock=time.monotonic):
        self.limits = dict(limits or DEFAULT_LIMITS)
        self._clock = clock
        self._events: dict[tuple[str, str], deque] = {}
        self._lock = threading.Lock()
        self._calls = 0

    def _sweep(self, now: float) -> None:
        """Drop buckets whose events have all expired (visitors who left)."""
        for key in list(self._events):
            window = self.limits[key[0]][1]
            bucket = self._events[key]
            while bucket and now - bucket[0] >= window:
                bucket.popleft()
            if not bucket:
                del self._events[key]

    def allow(self, action: str, *keys: str) -> bool:
        """Record one event for every key, unless any key is over its limit. Empty keys are ignored."""
        limit, window = self.limits[action]
        now = self._clock()
        with self._lock:
            self._calls += 1
            if self._calls % _SWEEP_EVERY == 0:
                self._sweep(now)
            buckets = []
            for key in keys:
                if not key:
                    continue
                bucket = self._events.get((action, key)) or deque()
                while bucket and now - bucket[0] >= window:
                    bucket.popleft()
                if len(bucket) >= limit:
                    return False
                buckets.append(((action, key), bucket))
            for bucket_key, bucket in buckets:
                bucket.append(now)
                self._events[bucket_key] = bucket
            return True

    def bucket_count(self) -> int:
        return len(self._events)


def rate_limit_keys(ip: str, session_id: str) -> tuple[str, ...]:
    """Keys for one visitor. A missing IP adds no key, so IP-less visitors
    don't all share (and exhaust) one bucket."""
    keys = [f"session:{session_id}"] if session_id else []
    if ip:
        keys.insert(0, f"ip:{ip}")
    return tuple(keys)


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
