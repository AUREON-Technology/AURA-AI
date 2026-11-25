"""KAN-110: a streamed turn's ``done`` frame no longer waits for its bookkeeping.

The session append (which can run a whole summarising generation), the telemetry sink write
and the session's clean mark used to be awaited before ``done`` -- so the person watched a
finished answer with the turn still "running". They now run after ``done``, and the session's
next turn waits for them before it reads history, so a quick follow-up still sees the exchange.
A SENSITIVE mark is still written before ``done`` (F32).

The slow store here is the real in-memory session memory behind a gate at its port boundary:
its ``append`` waits for the test to release it, standing in for a 5 s summary without a sleep.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace

from aura_core.domain.caller_context import CallerContext
from aura_core.domain.identifiers import SessionId, UserId
from aura_core.kernel import TurnRequest

USER = UserId("post-done-user")
SESSION = SessionId("post-done-session")


class GatedEpisodic:
    """The container's session memory, with its first ``append`` held until ``release()``."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.gate = asyncio.Event()
        self.appends_started = 0
        self.appends_landed = 0

    def release(self) -> None:
        self.gate.set()

    async def append(self, *args, **kwargs):
        self.appends_started += 1
        if self.appends_started == 1:
            await self.gate.wait()  # the first turn's slow summary, before anything is written
        result = await self._inner.append(*args, **kwargs)
        self.appends_landed += 1
        return result

    async def history(self, *args, **kwargs):
        return await self._inner.history(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _turn(message: str) -> TurnRequest:
    return TurnRequest(
        message=message, user_id=USER, session_id=SESSION,
        use_tools=False, use_knowledge=False, use_memory=False,
    )


async def _timed_frames(stream) -> list[tuple[float, dict]]:
    return [(time.perf_counter(), frame) async for frame in stream]


class TestDoneIsNotHeldBack:
    async def test_done_arrives_promptly_while_the_append_is_still_running(
        self, container
    ) -> None:
        await container.startup()
        gated = GatedEpisodic(container.kernel._deps.episodic)
        container.kernel._deps.episodic = gated

        frames = await asyncio.wait_for(
            _timed_frames(container.kernel.stream(_turn("first question"))), timeout=5.0
        )
        tokens = [t for t, f in frames if f["type"] == "token"]
        done_at = next(t for t, f in frames if f["type"] == "done")
        assert tokens
        assert done_at - tokens[-1] < 0.1
        assert gated.appends_landed == 0  # still "summarising" when done went out

        gated.release()
        await container.kernel.wait_for_post_turn()
        assert gated.appends_landed == 1

    async def test_the_next_turn_still_sees_the_previous_exchange(self, container) -> None:
        await container.startup()
        gated = GatedEpisodic(container.kernel._deps.episodic)
        container.kernel._deps.episodic = gated

        [f async for f in container.kernel.stream(_turn("my cat is called Miso"))]
        assert gated.appends_landed == 0

        # The follow-up starts while the first turn's append is still held. Its own append is
        # not gated, so if it read history now it would finish -- without the first exchange.
        follow_up = asyncio.ensure_future(
            container.kernel.run(_turn("what is my cat called?"))
        )
        await asyncio.sleep(0.05)
        assert not follow_up.done()  # it is waiting for the previous turn's append

        gated.release()
        await asyncio.wait_for(follow_up, timeout=5.0)
        await container.kernel.wait_for_post_turn()

        # What the model was shown for the follow-up included the first exchange.
        echo = next(p for p in container.providers if hasattr(p, "calls"))
        shown = " | ".join(m.content for m in echo.calls[-1].conversation)
        assert "my cat is called Miso" in shown

        history, _ = await gated.history(SESSION, USER)
        contents = [m.content for m in history]
        assert "my cat is called Miso" in contents
        assert "what is my cat called?" in contents
        assert contents.index("my cat is called Miso") < contents.index("what is my cat called?")

    async def test_telemetry_still_lands_after_done(self, container) -> None:
        await container.startup()
        before = len(container.telemetry_sink)
        [f async for f in container.kernel.stream(_turn("hello"))]
        await container.kernel.wait_for_post_turn()
        assert len(container.telemetry_sink) == before + 1

    async def test_a_reader_that_stops_at_done_does_not_lose_the_append(self, container) -> None:
        await container.startup()
        async for frame in container.kernel.stream(_turn("remember this exchange")):
            if frame["type"] == "done":
                break
        await container.kernel.wait_for_post_turn()
        history, _ = await container.episodic.history(SESSION, USER)
        assert "remember this exchange" in [m.content for m in history]


class TestSessionMarks:
    """F32 is unchanged: sensitive is marked before ``done``; clean may land after it."""

    async def test_a_sensitive_mark_is_written_before_done(self, container, monkeypatch) -> None:
        await container.startup()
        monkeypatch.setattr(type(container.kernel), "_restricts_data", lambda self: True)
        kernel = container.kernel
        # Caller evidence makes the turn SENSITIVE from its first step (AR-5).
        request = replace(
            _turn("hello"), caller_context=(CallerContext.of(id="doc-1", text="private notes"),)
        )
        marks = kernel._deps.session_marks
        assert marks is not None
        sensitive_key = kernel._session_sensitive_key(request)

        seen_at_done: dict[str, bool] = {}
        async for frame in kernel.stream(request):
            if frame["type"] == "done":
                seen_at_done["sensitive"] = sensitive_key in await marks.get_many([sensitive_key])
        assert seen_at_done == {"sensitive": True}
        assert await kernel._session_was_clean(request) is False

    async def test_a_clean_mark_lands_after_done(self, container, monkeypatch) -> None:
        await container.startup()
        monkeypatch.setattr(type(container.kernel), "_restricts_data", lambda self: True)
        kernel = container.kernel
        request = _turn("hello")
        [f async for f in kernel.stream(request)]
        await kernel.wait_for_post_turn()
        assert await kernel._session_was_clean(request) is True
