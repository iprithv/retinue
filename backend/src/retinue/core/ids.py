"""UUIDv7 (RFC 9562) generation — time-ordered, index-friendly (§17).

Stored as 16-byte BLOBs in SQLite and native uuid in Postgres. A per-process
monotonic guard makes ids generated within the same millisecond still sort in
generation order (the 12-bit rand_a field doubles as a sequence counter).
"""

import os
import threading
import time
import uuid

_lock = threading.Lock()
_last_ms = -1
_seq = 0

_MAX_SEQ = 0x0FFF  # 12-bit rand_a


def uuid7() -> uuid.UUID:
    global _last_ms, _seq
    with _lock:
        ms = time.time_ns() // 1_000_000
        if ms <= _last_ms:
            _seq += 1
            if _seq > _MAX_SEQ:
                # sequence exhausted within one ms: borrow the next ms
                _last_ms += 1
                _seq = 0
            ms = _last_ms
        else:
            _last_ms = ms
            _seq = 0
        rand_a = _seq
        rand_b = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)

    value = (
        ((ms & ((1 << 48) - 1)) << 80)
        | (0x7 << 76)
        | ((rand_a & 0x0FFF) << 64)
        | (0b10 << 62)
        | rand_b
    )
    return uuid.UUID(int=value)


def uuid7_hex() -> str:
    return uuid7().hex


def uuid_from_any(value: str | bytes | uuid.UUID) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, bytes):
        return uuid.UUID(bytes=value)
    return uuid.UUID(value)
