"""F7 / INT-114: a live-voice turn is a turn -- metered, rate limited and scoped like ``/chat``.

The socket used to rate-limit only at connect and on ``confirm``, never admitted a committed
utterance against the workspace's plan, and built its ``TurnRequest`` without the team scope or
the ``use_memory`` gate ``to_turn`` applies. Real app, in-memory stack, echo provider; only the
token check is stood in for.
"""

from __future__ import annotations

import math
import struct
from dataclasses import replace
from types import MappingProxyType
from typing import Any

import pytest

from aura_core.adapters.in_memory_speech import InMemoryStt, InMemoryTts
from aura_core.api import Principal, build_app
from aura_core.domain.errors import AuthenticationError
from aura_core.domain.identifiers import UserId
from aura_core.domain.plans import DEFAULT_CATALOGUE
from aura_core.domain.tenancy import WorkspaceContext, WorkspaceKind, WorkspaceRole
from aura_core.security.rate_limit import RateLimitPolicy, SlidingWindowRateLimiter
from aura_core.voice import LiveVoiceConfig

pytest.importorskip("fastapi")


def _loud(ms: int = 200, sample_rate: int = 16_000) -> list[bytes]:
    n = int(sample_rate * ms / 1000)
    pcm = b"".join(
        struct.pack("<h", int(0.5 * 32767 * math.sin(2 * math.pi * 440 * i / sample_rate)))
        for i in range(n)
    )
    return [pcm[i : i + 640] for i in range(0, len(pcm), 640)]


def _app(container, *, principal: Principal | None = None, rate_limiter: Any = None):
    container.stt_engine = InMemoryStt(default="hello")
    container.tts_engine = InMemoryTts()
    container.live_voice = {
        "enabled": True,
        "config": LiveVoiceConfig(min_speech_ms=40, silence_hangover_ms=40,
                                  english_only_stt=True, hold_tts_until_done=True),
        "stt": container.stt_engine, "tts": container.tts_engine, "nlp": container.nlp,
        "max_sessions": 4, "kernel": container.kernel, "cache": container.cache_backend,
        "safety_guards_enabled": False,
    }
    who = principal or Principal(user_id=UserId("talker"), role="User")

    async def authenticator(request):
        if request.headers.get("authorization", "") != "Bearer good":
            raise AuthenticationError("unknown")
        return who

    return build_app(container, authenticator=authenticator, rate_limiter=rate_limiter)


def _open(ws) -> None:
    ws.send_json({"type": "auth", "token": "good"})
    ws.send_json({"type": "start", "session_id": "voice-1", "language": "en"})
    assert ws.receive_json()["type"] == "ready"
    assert ws.receive_json()["type"] == "state"


def _say_and_commit(ws) -> None:
    for frame in _loud():
        ws.send_bytes(frame)
    ws.send_json({"type": "commit"})


def _until_turn_ends(ws) -> list[dict]:
    """JSON frames up to the turn's ``done`` or ``error`` frame (audio frames skipped)."""
    import json

    frames: list[dict] = []
    for _ in range(500):
        message = ws.receive()
        if message.get("text") is None:
            continue
        frame = json.loads(message["text"])
        frames.append(frame)
        if frame.get("type") in ("done", "error"):
            return frames
    raise AssertionError(f"turn never ended: {frames[-5:]}")


async def _used(container, person: str) -> int:
    ws = (await container.tenancy.ensure_personal(person)).id
    return (await container.plans.usage(ws))["messages_used"]


async def test_each_committed_turn_is_metered_and_refused_past_the_plan(container) -> None:
    free = replace(DEFAULT_CATALOGUE["free"], messages_per_month=1)
    container.plans.catalogue = MappingProxyType({**DEFAULT_CATALOGUE, "free": free})
    await container.startup()
    from fastapi.testclient import TestClient

    with TestClient(_app(container)) as client:
        with client.websocket_connect("/api/v3/voice/live") as ws:
            _open(ws)
            _say_and_commit(ws)
            first = _until_turn_ends(ws)
            assert first[-1]["type"] == "done"
            assert any(f["type"] == "token" for f in first)

            _say_and_commit(ws)
            second = _until_turn_ends(ws)
            refused = second[-1]
            assert refused["type"] == "error"
            assert refused["error"] == "plan_limit"
            assert refused["details"]["limit"] == "messages" and refused["details"]["allowed"] == 1
            assert not any(f["type"] == "token" for f in second), "the refused turn still ran"
            ws.send_json({"type": "stop"})
    assert await _used(container, "talker") == 1


async def test_a_turn_that_fails_before_any_token_gives_the_message_back(container, monkeypatch) -> None:
    async def failing(request):
        yield {"type": "error", "error": "provider_unavailable", "message": "down"}

    monkeypatch.setattr(container.kernel, "stream", failing)
    await container.startup()
    from fastapi.testclient import TestClient

    with TestClient(_app(container)) as client:
        with client.websocket_connect("/api/v3/voice/live") as ws:
            _open(ws)
            _say_and_commit(ws)
            assert _until_turn_ends(ws)[-1]["error"] == "provider_unavailable"
            ws.send_json({"type": "stop"})
    assert await _used(container, "talker") == 0


async def test_a_turn_that_fails_after_a_token_still_counts(container, monkeypatch) -> None:
    async def token_then_error(request):
        yield {"type": "token", "text": "part"}
        yield {"type": "error", "error": "provider_unavailable", "message": "down"}

    monkeypatch.setattr(container.kernel, "stream", token_then_error)
    await container.startup()
    from fastapi.testclient import TestClient

    with TestClient(_app(container)) as client:
        with client.websocket_connect("/api/v3/voice/live") as ws:
            _open(ws)
            _say_and_commit(ws)
            _until_turn_ends(ws)
            ws.send_json({"type": "stop"})
    assert await _used(container, "talker") == 1


async def test_each_committed_turn_is_rate_limited(container) -> None:
    """Connect costs one voice_live charge and every committed turn another: with room for two,
    the second turn is refused with the same rate_limited shape the HTTP routes use."""
    from aura_core.security.rate_limit import ROUTE_COSTS

    limiter = SlidingWindowRateLimiter(
        container.cache_backend,
        policies=(RateLimitPolicy(scope="user", limit=2 * ROUTE_COSTS["voice_live"], window_s=3600),),
    )
    await container.startup()
    from fastapi.testclient import TestClient

    with TestClient(_app(container, rate_limiter=limiter)) as client:
        with client.websocket_connect("/api/v3/voice/live") as ws:
            _open(ws)
            _say_and_commit(ws)
            assert _until_turn_ends(ws)[-1]["type"] == "done"
            _say_and_commit(ws)
            refused = _until_turn_ends(ws)
            assert refused[-1]["error"] == "rate_limited"
            assert refused[-1]["details"]["retry_after"] >= 0
            assert not any(f["type"] == "token" for f in refused)
            ws.send_json({"type": "stop"})
    # A rate-limited turn never reached the plan meter.
    assert await _used(container, "talker") == 1


async def test_a_team_turn_uses_the_team_corpus_and_no_personal_memory(container, monkeypatch) -> None:
    """The same scope ``to_turn`` gives a typed turn (ADR-034): knowledge from the team, never
    the person's private memory, and the origin from the signed token."""
    seen: list[Any] = []
    original = container.kernel.stream

    def spy(request):
        seen.append(request)
        return original(request)

    monkeypatch.setattr(container.kernel, "stream", spy)
    team = await container.tenancy.create_team("tess", "Tess Co")
    principal = Principal(
        user_id=UserId("tess"), role="User",
        workspace=WorkspaceContext(team.id, WorkspaceKind.TEAM, WorkspaceRole.OWNER),
    )
    await container.startup()
    from fastapi.testclient import TestClient

    with TestClient(_app(container, principal=principal)) as client:
        with client.websocket_connect("/api/v3/voice/live") as ws:
            _open(ws)
            _say_and_commit(ws)
            _until_turn_ends(ws)
            ws.send_json({"type": "stop"})
    assert seen, "no turn ran"
    turn = seen[0]
    assert turn.knowledge_owner == principal.knowledge_owner != turn.user_id
    assert turn.use_memory is False
    assert turn.origin == "remote"
    assert turn.route == "voice_live"


async def test_a_refused_confirmation_keeps_the_calls_held() -> None:
    """A confirmation is a turn: refused by admission it does not run, and the held calls stay
    held so the person can confirm once the limit clears."""
    from collections.abc import AsyncIterator, Mapping

    from aura_core.domain.identifiers import SessionId
    from aura_core.nlp import NlpEngine
    from aura_core.voice import LiveVoiceSession
    from aura_core.voice.session import SessionState, TurnRefused

    class Holding:
        def __init__(self) -> None:
            self.requests: list[Any] = []

        async def stream(self, request: Any) -> AsyncIterator[Mapping[str, Any]]:
            self.requests.append(request)
            yield {"type": "done", "needs_confirmation": ["propose_task"], "pending_tool_calls": [
                {"call_id": "c1", "tool": "propose_task", "arguments": {}, "approval_id": "apr.1"},
            ]}

    refuse = False

    async def admit(turn):
        if refuse:
            raise TurnRefused({"error": "rate_limited", "message": "slow down", "details": {}})
        return None, turn

    streamer = Holding()
    session = LiveVoiceSession(
        stt=InMemoryStt(default="x"), tts=InMemoryTts(), turn_streamer=streamer,
        user_id=UserId("u1"), session_id=SessionId("s1"),
        nlp=NlpEngine(extract_entities=False), config=LiveVoiceConfig(), admit=admit,
    )
    await session.start()
    await session._run_kernel_turn(message="make a task")
    assert session.state is SessionState.AWAITING_CONFIRMATION
    refuse = True
    await session.confirm(None)
    assert len(streamer.requests) == 1, "the refused confirmation ran"
    assert session.state is SessionState.AWAITING_CONFIRMATION
    events = []
    while not session._outbound.empty():
        events.append(await session._outbound.get())
    assert {"type": "error", "error": "rate_limited", "message": "slow down", "details": {}} in events
    refuse = False
    await session.confirm(None)
    assert streamer.requests[-1].approval_ids == ("apr.1",)
    await session.close()
