"""Token counting (D20) that never blocks the token path.

tiktoken's encoding file is fetched once over the network (cached under the
Retinue home dir). That download must never sit between a chat request and its
first token — counts fall back to a chars/4 heuristic until a background
thread finishes loading the encoder, after which counts are exact. The §31.2
allocator only needs consistency, so the temporary heuristic is safe.
"""

import threading

import structlog

log = structlog.get_logger("retinue.tokens")


class TokenCounter:
    # per-message overhead approximation for chat-format wrapping
    MESSAGE_OVERHEAD = 4

    def __init__(self, encoding_name: str = "o200k_base", force_fallback: bool = False) -> None:
        self._encoding_name = encoding_name
        self._encoder: object | None = None
        self._loading = force_fallback  # force_fallback: pretend load already ran
        self._lock = threading.Lock()

    def _load_in_background(self) -> None:
        with self._lock:
            if self._loading:
                return
            self._loading = True
        threading.Thread(target=self._load, name="tiktoken-load", daemon=True).start()

    def _load(self) -> None:
        try:
            import tiktoken

            self._encoder = tiktoken.get_encoding(self._encoding_name)
            log.debug("tiktoken_ready", encoding=self._encoding_name)
        except Exception as exc:  # offline first run: heuristic keeps working
            log.warning("tiktoken_unavailable_fallback", error=str(exc))

    def warm(self) -> None:
        """Kick off the encoder load without waiting (called at startup)."""
        self._load_in_background()

    def count(self, text: str) -> int:
        if not text:
            return 0
        encoder = self._encoder
        if encoder is not None:
            return len(encoder.encode(text, disallowed_special=()))  # type: ignore[attr-defined]
        self._load_in_background()
        return max(1, len(text) // 4)

    def count_message(self, role: str, text: str) -> int:
        return self.MESSAGE_OVERHEAD + self.count(role) + self.count(text)
