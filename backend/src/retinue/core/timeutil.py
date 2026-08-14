"""Time helpers. All persisted timestamps are integer epoch-milliseconds UTC (§17)."""

import time


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def ms_after(seconds: float) -> int:
    return now_ms() + int(seconds * 1000)
