"""Splits a raw token stream (which may contain a trailing `[ACTIONS]<json>`
marker anywhere, split across arbitrary chunk boundaries) into a sequence
of ("token", text) / ("tool_calls", actions) events. Extracted out of
api/v2/chat.py's SSE generator into a standalone class specifically so the
chunk-boundary edge case (the marker split across two `chunk`s from the
upstream Gemini/Ollama stream) can be unit tested without spinning up a
FastAPI app or a real model — see test_sse_marker_splitter.py.
"""
import json
from typing import Iterator, Tuple

MARKER = "[ACTIONS]"


class MarkerStreamSplitter:
    def __init__(self, marker: str = MARKER):
        self.marker = marker
        self._buffer = ""
        self._marker_found = False

    def feed(self, chunk: str) -> Iterator[Tuple[str, dict]]:
        """Call once per chunk as it arrives. Yields zero or more
        ("token", {"text": ...}) events. Never yields the tool_calls event
        here — that only happens once the full stream has ended, since the
        actions JSON may itself still be streaming in across more chunks."""
        self._buffer += chunk

        if self._marker_found:
            return  # accumulating the actions payload; nothing to emit yet

        if self.marker in self._buffer:
            visible, _, rest = self._buffer.partition(self.marker)
            if visible:
                yield ("token", {"text": visible})
            self._buffer = rest
            self._marker_found = True
            return

        # No marker yet: emit everything except a trailing suffix that
        # could be the start of a marker split across the next chunk
        # boundary (e.g. this chunk ends in "...[ACTIO").
        hold = 0
        max_check = min(len(self.marker) - 1, len(self._buffer))
        for i in range(max_check, 0, -1):
            if self.marker.startswith(self._buffer[-i:]):
                hold = i
                break
        safe_len = len(self._buffer) - hold
        if safe_len > 0:
            yield ("token", {"text": self._buffer[:safe_len]})
            self._buffer = self._buffer[safe_len:]

    def finish(self) -> Iterator[Tuple[str, dict]]:
        """Call once after the source stream ends. Yields the final
        tool_calls/error event if a marker was found, or flushes any
        held-back tail text if it never was."""
        if self._marker_found:
            actions_text = self._buffer.strip()
            if actions_text:
                try:
                    actions = json.loads(actions_text)
                    yield ("tool_calls", {"actions": actions})
                except Exception as parse_err:
                    yield ("error", {"message": f"Failed to parse proposed actions: {parse_err}"})
        elif self._buffer:
            yield ("token", {"text": self._buffer})
