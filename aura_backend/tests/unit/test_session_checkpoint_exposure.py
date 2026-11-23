"""Row 26: the session checkpoint capability is reachable -- tools, API route, automatic checkpoints.

Everything goes through the real wiring: the tool registry and policy of the container, the real
ASGI app for the route, and a real kernel turn for the automatic checkpoint. The model is the echo
provider, whose reply is not JSON, so checkpoints here are the extractive ones -- the model-written
path is covered in ``test_session_checkpoint.py``.
"""

from __future__ import annotations

import dataclasses

import pytest

from aura_core.agents.registry import build_default_agent_registry
from aura_core.container import Container
from aura_core.config import load_settings
from aura_core.domain import ToolCall, ToolCallId
from aura_core.domain.identifiers import SessionId, UserId
from aura_core.kernel import TurnRequest
from aura_core.memory.session import EpisodicMemory
from aura_core.ports.runtime import ToolContext
from aura_core.tools.session_tools import SESSION_TOOL_NAMES

ALICE, BOB = UserId("alice-ckpt"), UserId("bob-ckpt")
SESSION = SessionId("ckpt-session-1")

CONVO = (
    ("We need to migrate the billing tables.",
     "We decided to use Alembic for the migration. The schema change is done and merged."),
    ("The deploy is failing with a timeout error. Is the proxy ok?",
     "Blocked: the staging database is unreachable. Next, we need to rerun the load test."),
)


def _ctx(user: UserId = ALICE, session: str | None = "ckpt-session-1", **extra) -> ToolContext:
    return ToolContext(
        user_id=user, correlation_id="c-ckpt", deadline_s=30.0, agent_id="orchestrator",
        session_id=session, **extra,
    )


async def _seed(container, user: UserId = ALICE, session: SessionId = SESSION) -> None:
    for message, answer in CONVO:
        await container.episodic.append(session, user, user_message=message, assistant_message=answer)


async def _call(container, name: str, arguments=None, *, context: ToolContext):
    return await container.tools.execute(
        ToolCall(id=ToolCallId("t1"), name=name, arguments=arguments or {}), context,
    )


class TestToolsThroughTheRegistry:
    def test_both_tools_are_registered_and_the_engineer_may_use_them(self, container) -> None:
        for name in SESSION_TOOL_NAMES:
            assert container.tools.spec(name) is not None
        engineer = build_default_agent_registry().get("engineer")
        assert set(SESSION_TOOL_NAMES) <= engineer.allowed_tools

    async def test_save_then_recall_round_trips_through_the_real_policy(self, container) -> None:
        await _seed(container)
        saved, report = await _call(container, "save_session_checkpoint", context=_ctx())
        assert saved.ok and report.allowed, saved.content
        assert "Alembic" in saved.content and "staging database is unreachable" in saved.content

        recalled, _ = await _call(container, "recall_session_checkpoint", context=_ctx())
        assert recalled.ok
        assert "Alembic" in recalled.content and "rerun the load test" in recalled.content

    async def test_recall_before_any_save_says_so(self, container) -> None:
        await _seed(container)
        result, _ = await _call(container, "recall_session_checkpoint", context=_ctx())
        assert result.ok and "No checkpoint" in result.content

    async def test_a_turn_without_a_session_cannot_checkpoint(self, container) -> None:
        result, _ = await _call(container, "save_session_checkpoint", context=_ctx(session=None))
        assert not result.ok and "no session" in result.content

    async def test_an_empty_session_saves_nothing(self, container) -> None:
        result, _ = await _call(container, "save_session_checkpoint", context=_ctx(session="never-used"))
        assert result.ok and "Nothing to checkpoint" in result.content
        assert await container.episodic.latest_checkpoint(SessionId("never-used"), ALICE) is None

    async def test_a_session_id_is_not_a_capability(self, container) -> None:
        """Bob names Alice's session id, as the argument and as his own context's session."""
        await _seed(container)
        await container.episodic.checkpoint(SESSION, ALICE, save=True)

        by_argument, _ = await _call(
            container, "recall_session_checkpoint", {"session_id": SESSION.value}, context=_ctx(BOB),
        )
        by_context, _ = await _call(container, "recall_session_checkpoint", context=_ctx(BOB))
        for result in (by_argument, by_context):
            assert result.ok and "No checkpoint" in result.content and "Alembic" not in result.content

        saved, _ = await _call(container, "save_session_checkpoint", context=_ctx(BOB))
        assert "Alembic" not in saved.content
        still_alice = await container.episodic.latest_checkpoint(SESSION, ALICE)
        assert still_alice is not None and any("Alembic" in d for d in still_alice.decisions)

    async def test_saving_in_a_quarantined_turn_waits_for_confirmation(self, container) -> None:
        """It is side-effecting (durable state, like ``remember_fact``): ADR-006 applies."""
        await _seed(container)
        result, report = await _call(
            container, "save_session_checkpoint", context=_ctx(tainted=True),
        )
        assert not result.ok and report.needs_confirmation
        assert await container.episodic.latest_checkpoint(SESSION, ALICE) is None


class TestAutoCheckpoint:
    async def test_every_nth_turn_writes_one_in_the_background(self, cache) -> None:
        memory = EpisodicMemory(cache, auto_checkpoint_every=2)
        await memory.append(SESSION, ALICE, user_message=CONVO[0][0], assistant_message=CONVO[0][1])
        await memory.drain_background()
        assert await memory.latest_checkpoint(SESSION, ALICE) is None, "turn 1 is not a multiple of 2"

        await memory.append(SESSION, ALICE, user_message=CONVO[1][0], assistant_message=CONVO[1][1])
        await memory.drain_background()
        saved = await memory.latest_checkpoint(SESSION, ALICE)
        assert saved is not None and saved.covers_messages == 4
        assert any("Alembic" in d for d in saved.decisions)

    async def test_off_by_default(self, cache) -> None:
        memory = EpisodicMemory(cache)
        for message, answer in CONVO * 3:
            await memory.append(SESSION, ALICE, user_message=message, assistant_message=answer)
        await memory.drain_background()
        assert await memory.latest_checkpoint(SESSION, ALICE) is None

    async def test_a_failing_checkpointer_never_fails_the_append(self, cache) -> None:
        async def broken(transcript: str, previous: str) -> str:
            raise RuntimeError("model is down")

        memory = EpisodicMemory(cache, checkpointer=broken, auto_checkpoint_every=1)
        state = await memory.append(SESSION, ALICE, user_message=CONVO[0][0], assistant_message=CONVO[0][1])
        await memory.drain_background()
        assert state.turn_count == 1
        saved = await memory.latest_checkpoint(SESSION, ALICE)
        assert saved is not None and saved.source == "extractive"

    async def test_the_turn_returns_before_the_checkpoint_model_does(self, cache) -> None:
        import asyncio

        release = asyncio.Event()

        async def slow(transcript: str, previous: str) -> str:
            await release.wait()
            return '{"decisions": ["slow one"], "completed": [], "blockers": [], "next_steps": []}'

        memory = EpisodicMemory(cache, checkpointer=slow, auto_checkpoint_every=1)
        await memory.append(SESSION, ALICE, user_message=CONVO[0][0], assistant_message=CONVO[0][1])
        assert await memory.latest_checkpoint(SESSION, ALICE) is None  # still running, not awaited
        release.set()
        await memory.drain_background()
        saved = await memory.latest_checkpoint(SESSION, ALICE)
        assert saved is not None and saved.decisions == ("slow one",)

    async def test_through_a_real_kernel_turn(self) -> None:
        """Settings -> container -> kernel.run -> EpisodicMemory.append -> checkpoint."""
        base = load_settings(refresh=True, validate=False)
        settings = dataclasses.replace(
            base, memory=dataclasses.replace(base.memory, auto_checkpoint_every_turns=2)
        )
        container = Container(settings)
        await container.startup()
        for message in ("We decided to use Alembic for the migration.", "The schema change is done and merged."):
            result = await container.kernel.run(TurnRequest(message=message, user_id=ALICE, session_id=SESSION))
            assert result.telemetry.outcome.value == "success"
        await container.episodic.drain_background()
        saved = await container.episodic.latest_checkpoint(SESSION, ALICE)
        assert saved is not None and saved.covers_messages == 4


class TestFreshCheckpoint:
    async def test_a_recent_saved_checkpoint_is_reused_without_a_new_model_call(self, cache) -> None:
        calls = 0

        async def counting(transcript: str, previous: str) -> str:
            nonlocal calls
            calls += 1
            return '{"decisions": ["d"], "completed": [], "blockers": [], "next_steps": []}'

        memory = EpisodicMemory(cache, checkpointer=counting)
        await memory.append(SESSION, ALICE, user_message=CONVO[0][0], assistant_message=CONVO[0][1])
        first = await memory.fresh_checkpoint(SESSION, ALICE)
        second = await memory.fresh_checkpoint(SESSION, ALICE)
        assert calls == 1 and first is not None and second.decisions == first.decisions

    async def test_an_empty_session_yields_none(self, cache) -> None:
        assert await EpisodicMemory(cache).fresh_checkpoint(SessionId("nothing"), ALICE) is None


class TestRoute:
    @pytest.fixture
    def client(self, container):
        from fastapi.testclient import TestClient

        from aura_core.api import Principal, build_app
        from aura_core.domain.errors import AuthenticationError

        async def authenticator(request):
            header = request.headers.get("authorization", "")
            if not header.startswith("Bearer ") or header[7:] not in (ALICE.value, BOB.value):
                raise AuthenticationError("unknown principal")
            return Principal(user_id=UserId(header[7:]), email=f"{header[7:]}@example.com", email_verified=True)

        with TestClient(build_app(container, authenticator=authenticator)) as c:
            yield c

    @staticmethod
    def _auth(user: UserId) -> dict[str, str]:
        return {"Authorization": f"Bearer {user.value}"}

    async def test_post_writes_and_get_reads_it_back(self, client, container) -> None:
        await _seed(container)
        assert client.get(f"/api/v3/sessions/{SESSION.value}/checkpoint", headers=self._auth(ALICE)).status_code == 404

        written = client.post(f"/api/v3/sessions/{SESSION.value}/checkpoint", headers=self._auth(ALICE))
        assert written.status_code == 200, written.text
        body = written.json()
        assert body["session_id"] == SESSION.value and body["checkpoint"]["source"] == "extractive"
        assert any("Alembic" in d for d in body["checkpoint"]["decisions"])

        read = client.get(f"/api/v3/sessions/{SESSION.value}/checkpoint", headers=self._auth(ALICE))
        assert read.status_code == 200 and read.json()["checkpoint"] == body["checkpoint"]

    def test_an_empty_session_is_a_404_not_an_empty_checkpoint(self, client) -> None:
        response = client.post("/api/v3/sessions/nothing-here/checkpoint", headers=self._auth(ALICE))
        assert response.status_code == 404

    def test_unauthenticated_is_refused(self, client) -> None:
        assert client.get(f"/api/v3/sessions/{SESSION.value}/checkpoint").status_code in (401, 403)

    def test_a_malformed_session_id_is_a_422(self, client) -> None:
        response = client.get("/api/v3/sessions/bad id with spaces!/checkpoint", headers=self._auth(ALICE))
        assert response.status_code in (404, 422)
