"""The chat stream sends SSE comments while it is quiet (F-22).

nginx drops a proxied response after 300 s with no bytes, and planning plus time-to-first-token
on the CPU stack can be silent for longer, so a long turn was cut mid-way with no error frame.
"""

from __future__ import annotations

import asyncio

from aura_core.api.v3 import with_keepalive


async def _slow_frames():
    await asyncio.sleep(0.25)
    yield b"event: token\ndata: {}\n\n"
    yield b"event: done\ndata: {}\n\n"


async def test_a_quiet_stream_gets_keepalives_then_its_frames_in_order() -> None:
    out = [chunk async for chunk in with_keepalive(_slow_frames(), interval_s=0.05)]

    keepalives = [c for c in out if c.startswith(b":")]
    frames = [c for c in out if not c.startswith(b":")]
    assert len(keepalives) >= 2
    assert frames == [b"event: token\ndata: {}\n\n", b"event: done\ndata: {}\n\n"]


async def test_a_busy_stream_gets_none() -> None:
    async def fast():
        for i in range(5):
            yield f"event: token\ndata: {i}\n\n".encode()

    out = [chunk async for chunk in with_keepalive(fast(), interval_s=5.0)]
    assert not [c for c in out if c.startswith(b":")]
    assert len(out) == 5


async def test_closing_the_stream_stops_the_producer() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def forever():
        started.set()
        try:
            await asyncio.sleep(3600)
            yield b""
        except asyncio.CancelledError:
            cancelled.set()
            raise

    stream = with_keepalive(forever(), interval_s=0.01)
    assert (await stream.__anext__()).startswith(b":")
    await stream.aclose()
    await asyncio.wait_for(cancelled.wait(), timeout=1.0)
