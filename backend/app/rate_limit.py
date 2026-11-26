"""Simple in-memory rate limiting: per-key token bucket + daily counter."""
from __future__ import annotations

import time


class RateLimiter:
    def __init__(self, per_minute: float, clock=time.monotonic, burst: float | None = None):
        self.per_minute = per_minute
        # Burst capacity: at least one request, so sub-1-per-minute rates
        # (e.g. hourly caps) do not reject the very first call.
        self.burst = max(1.0, float(burst) if burst is not None else float(per_minute))
        self.clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, ts)

    def allow(self, key: str) -> bool:
        now = self.clock()
        tokens, ts = self._buckets.get(key, (self.burst, now))
        tokens = min(
            self.burst, tokens + (now - ts) * (self.per_minute / 60.0)
        )
        if tokens < 1.0:
            self._buckets[key] = (tokens, now)
            return False
        self._buckets[key] = (tokens - 1.0, now)
        return True


class DailyCounter:
    """Rolling per-day counter (UTC). Resets on restart; that is acceptable."""

    def __init__(self, per_day: int, clock=None):
        self.per_day = per_day
        self._clock = clock or (lambda: __import__("time").time())
        self._day = ""
        self._count = 0

    def allow(self) -> bool:
        import time as _t

        day = _t.strftime("%Y-%m-%d", _t.gmtime(self._clock()))
        if day != self._day:
            self._day, self._count = day, 0
        if self._count >= self.per_day:
            return False
        self._count += 1
        return True
