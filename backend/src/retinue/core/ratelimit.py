"""In-process token buckets per user+IP+route class (§16). Redis lands at T3."""

import time


class RateLimiter:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        # key -> (tokens, last_refill_monotonic)
        self._buckets: dict[str, tuple[float, float]] = {}
        self._last_sweep = time.monotonic()

    def check(self, key: str, rate_per_min: float, burst: float) -> float | None:
        """Consume one token. Returns None if allowed, else seconds to wait."""
        if not self.enabled or rate_per_min <= 0:
            return None
        now = time.monotonic()
        refill_per_s = rate_per_min / 60.0
        tokens, last = self._buckets.get(key, (burst, now))
        tokens = min(burst, tokens + (now - last) * refill_per_s)
        if tokens >= 1.0:
            self._buckets[key] = (tokens - 1.0, now)
            self._maybe_sweep(now, burst_hint=burst)
            return None
        self._buckets[key] = (tokens, now)
        return (1.0 - tokens) / refill_per_s

    def _maybe_sweep(self, now: float, burst_hint: float) -> None:
        # Drop buckets idle long enough to be full again; bounds memory.
        if len(self._buckets) < 10_000 or now - self._last_sweep < 60:
            return
        self._last_sweep = now
        stale = [k for k, (_, last) in self._buckets.items() if now - last > 600]
        for k in stale:
            del self._buckets[k]
