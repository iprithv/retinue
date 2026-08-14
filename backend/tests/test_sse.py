"""SSE encoder (§19) — exact wire format, property-based roundtrip, ring buffer."""

from hypothesis import given
from hypothesis import strategies as st

from retinue.core.sse import HEARTBEAT, RingBuffer, encode_sse
from support import parse_sse


def test_wire_format_exact():
    frame = encode_sse(7, "delta", {"index": 0, "text": "hi"})
    assert frame == b'id: 7\nevent: delta\ndata: {"index":0,"text":"hi"}\n\n'


def test_idless_frame():
    frame = encode_sse(None, "resync_required", {})
    assert frame == b"event: resync_required\ndata: {}\n\n"


def test_heartbeat_is_comment():
    assert HEARTBEAT == b": ping\n\n"
    assert parse_sse(HEARTBEAT) == []


# hypothesis: whatever we encode, a spec-compliant parser reads back identically
_text = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)),  # any unicode minus surrogates
    max_size=200,
)


@given(
    event_id=st.integers(min_value=1, max_value=2**53),
    event=st.sampled_from(["message_start", "block_start", "delta", "usage", "message_end"]),
    data=st.dictionaries(
        st.sampled_from(["text", "index", "note"]),
        st.one_of(_text, st.integers(-1000, 1000), st.none()),
        max_size=3,
    ),
)
def test_encode_parse_roundtrip(event_id, event, data):
    parsed = parse_sse(encode_sse(event_id, event, data))
    assert len(parsed) == 1
    assert parsed[0].id == event_id
    assert parsed[0].event == event
    assert parsed[0].data == data


class TestRingBuffer:
    def test_replay_after(self):
        ring = RingBuffer(size=10)
        for i in range(1, 6):
            ring.push(i, f"frame-{i}".encode())
        frames, missed = ring.replay_after(2)
        assert frames == [b"frame-3", b"frame-4", b"frame-5"]
        assert missed is False

    def test_eviction_detected(self):
        ring = RingBuffer(size=3)
        for i in range(1, 8):
            ring.push(i, f"frame-{i}".encode())
        frames, missed = ring.replay_after(1)
        assert missed is True
        assert frames == [b"frame-5", b"frame-6", b"frame-7"]

    def test_replay_from_zero(self):
        ring = RingBuffer(size=10)
        ring.push(1, b"a")
        frames, missed = ring.replay_after(0)
        assert frames == [b"a"]
        assert missed is False

    def test_empty_with_last_id(self):
        ring = RingBuffer()
        frames, missed = ring.replay_after(5)
        assert frames == []
        assert missed is True

    def test_last_id(self):
        ring = RingBuffer()
        assert ring.last_id == 0
        ring.push(9, b"x")
        assert ring.last_id == 9
