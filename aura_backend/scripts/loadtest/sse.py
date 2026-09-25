"""Incremental parser for the gateway's typed server-sent events.

``/api/v3/chat/stream`` emits ``event: <type>\\ndata: <json>\\n\\n`` frames (``token``,
``citations``, ``step``, ``actions``, ``tool_calls``, ``revision``, ``done``, ``error``). Chunks
arrive split at arbitrary byte boundaries -- mid-line, mid-frame, mid-UTF-8 sequence when read as
bytes -- so the parser buffers until a blank line closes a frame, the same contract the Flutter
client's ``SseFrameParser`` implements.
"""

from __future__ import annotations

import json
from typing import Any, Iterator


class SseParser:
    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, text: str) -> Iterator[dict[str, Any]]:
        """Yield every complete frame in ``text`` (plus whatever was buffered before it).

        Each frame is returned as the decoded JSON ``data`` payload, with ``type`` defaulted from
        the ``event:`` line when the payload itself lacks one. A frame whose data is not JSON is
        returned as ``{"type": <event>, "raw": <data>}`` rather than dropped -- the harness counts
        it, and a silent drop would hide a protocol regression.
        """
        self._buffer += text.replace("\r\n", "\n").replace("\r", "\n")
        while "\n\n" in self._buffer:
            block, self._buffer = self._buffer.split("\n\n", 1)
            frame = self._parse_block(block)
            if frame is not None:
                yield frame

    def flush(self) -> Iterator[dict[str, Any]]:
        """Parse a trailing frame the server closed without the final blank line."""
        block, self._buffer = self._buffer, ""
        if block.strip():
            frame = self._parse_block(block)
            if frame is not None:
                yield frame

    @staticmethod
    def _parse_block(block: str) -> dict[str, Any] | None:
        event = "message"
        data_lines: list[str] = []
        for line in block.split("\n"):
            if not line or line.startswith(":"):
                continue
            name, _, value = line.partition(":")
            if value.startswith(" "):
                value = value[1:]
            if name == "event":
                event = value.strip() or "message"
            elif name == "data":
                data_lines.append(value)
        if not data_lines:
            return None
        data = "\n".join(data_lines)
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            return {"type": event, "raw": data}
        if not isinstance(payload, dict):
            return {"type": event, "raw": data}
        payload.setdefault("type", event)
        return payload
