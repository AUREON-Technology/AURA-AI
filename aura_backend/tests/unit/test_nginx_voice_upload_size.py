"""nginx lets a full-size voice upload reach the gateway.

`/api/v3/voice/transcribe` accepts audio up to `MAX_AUDIO_BYTES` (25 MiB) plus the multipart
envelope, but both VPS server blocks cap request bodies at 20m, so nginx answered 413 to a 20-25 MB
recording the gateway would have taken. Each block now has an exact-match location for that one
route with a larger cap, and otherwise proxies exactly as its general `/api/` location does: the
larger cap must not come with a different upstream, rate limit or timeout.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_TEMPLATE = _ROOT / "deploy" / "nginx" / "templates" / "aura.conf.template"
_HOST_PROXY = _ROOT / "deploy" / "nginx" / "host-proxy.conf.example"
_ENVELOPE = 1024 * 1024


def _blocks(text: str, header: str) -> list[str]:
    blocks = []
    for match in re.finditer(re.escape(header) + r" \{", text):
        depth, i = 0, match.end() - 1
        while True:
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            if depth == 0:
                break
            i += 1
        blocks.append(text[match.end():i])
    return blocks


def _directives(block: str) -> list[str]:
    lines = [line.split("#", 1)[0].strip() for line in block.splitlines()]
    return sorted(line for line in lines if line and not line.startswith("client_max_body_size"))


def _max_audio_bytes() -> int:
    source = (_ROOT / "aura_backend" / "aura_core" / "api" / "v3.py").read_text(encoding="utf-8")
    match = re.search(r"MAX_AUDIO_BYTES\s*=\s*(\d+)\s*\*\s*1024\s*\*\s*1024", source)
    assert match, "MAX_AUDIO_BYTES moved; point this test at it"
    return int(match.group(1)) * 1024 * 1024


def _cap_bytes(block: str) -> int:
    caps = re.findall(r"client_max_body_size\s+(\d+)m;", block)
    assert len(caps) == 1, "the transcribe location must set its own body cap"
    return int(caps[0]) * 1024 * 1024


@pytest.mark.parametrize("route", ["/api/v3/voice/transcribe", "/api/v3/voice/task"])
@pytest.mark.parametrize(("path", "count", "twin"), [
    (_TEMPLATE, 2, "location /api/"),
    (_HOST_PROXY, 1, "location /"),
])
def test_every_server_block_lets_a_full_size_recording_through(
    path: Path, count: int, twin: str, route: str
) -> None:
    text = path.read_text(encoding="utf-8")
    transcribe = _blocks(text, f"location = {route}")
    # The HTTPS server blocks are the last ones; host-proxy also has a port-80 `location /`.
    general = _blocks(text, twin)[-count:]
    assert len(transcribe) == count, f"{path.name}: expected {count} transcribe locations"
    assert len(general) == count, f"{path.name}: no `{twin}` to compare with"
    needed = _max_audio_bytes() + _ENVELOPE
    for block, base in zip(transcribe, general):
        assert _cap_bytes(block) >= needed, f"{path.name}: cap below {math.ceil(needed / 2**20)} MiB"
        assert _directives(block) == _directives(base), (
            f"{path.name}: the transcribe location must proxy exactly like `{twin}`"
        )


def test_the_server_default_stays_at_the_document_ceiling() -> None:
    """Only this route is widened; every other body keeps the 20m server cap."""
    for path in (_TEMPLATE, _HOST_PROXY):
        server_level = re.findall(r"^    client_max_body_size\s+(\d+)m;", path.read_text(encoding="utf-8"), re.M)
        assert server_level and set(server_level) == {"20"}, (path.name, server_level)
