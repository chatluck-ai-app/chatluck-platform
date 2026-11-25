"""Simple in-memory token-bucket rate limiter, per user id."""
from __future__ import annotations

import time


class RateLimiter:
    def __init__(self, per_minute: int, clock=time.monotonic):
        self.per_minute = per_minute
        self.clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, ts)

    def allow(self, key: str) -> bool:
        now = self.clock()
        tokens, ts = self._buckets.get(key, (float(self.per_minute), now))
        tokens = min(
            float(self.per_minute), tokens + (now - ts) * (self.per_minute / 60.0)
        )
        if tokens < 1.0:
            self._buckets[key] = (tokens, now)
            return False
        self._buckets[key] = (tokens - 1.0, now)
        return True
