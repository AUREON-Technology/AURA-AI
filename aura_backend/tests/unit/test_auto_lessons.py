"""Automatic lessons from Engineer turns (ADR-040, inventory row 60).

Layers: the pure extractor, the job against the real in-memory memory engine (what is written,
where, and what is refused), the review path (approve promotes, reject deletes, nobody else can),
and the whole thing through a real Engineer kernel turn in a container with the setting on.
"""

from __future__ import annotations

import dataclasses

import pytest

from aura_core.config import load_settings
from aura_core.container import Container
from aura_core.domain.identifiers import MemoryId, PlanId, SessionId, StepId, UserId
from aura_core.domain.lessons import (
    ENGINEER_SCOPE,
    LESSON_SOURCE,
    LessonStep,
    LessonTurn,
    pending_scope,
)
from aura_core.domain.memory import MemoryKind, MemoryQuery, readable_scopes
from aura_core.domain.planning import Goal, Plan, PlanStep, StepKind
from aura_core.domain.tools import ToolSensitivity
from aura_core.kernel import TurnRequest
from aura_core.learning.jobs import run_engineer_lesson_extraction
from aura_core.learning.lessons import extract_lessons
from aura_core.memory.checkpoint import SessionCheckpoint
from aura_core.tools import param, tool

ALICE, BOB = UserId("alice-lessons"), UserId("bob-lessons")


def _turn(**overrides) -> LessonTurn:
    base = dict(
        user_id=ALICE, agent_id="engineer", session_id="s-1", turn_id="t-1",
        message="We always use Alembic for schema changes. Please add the billing column.",
        answer="Done. We decided to keep the billing column nullable until the backfill finishes.",
    )
    base.update(overrides)
    return LessonTurn(**base)


FAILED_THEN_FIXED = (
    LessonStep(tool="run_command", description="run the migration", status="failed", error_kind="tool_error"),
    LessonStep(tool="run_command", description="run the migration again", status="succeeded", attempts=2),
)


class TestExtraction:
    def test_decisions_conventions_and_failures_are_found_and_ranked(self) -> None:
        lessons = extract_lessons(_turn(steps=(
            LessonStep(tool="git_push", description="push the branch", status="failed", error_kind="auth"),
        )))
        kinds = [lesson.kind for lesson in lessons]
        assert kinds[0] == "failure", "what went wrong comes first"
        assert "convention" in kinds and "decision" in kinds
        texts = " | ".join(lesson.text for lesson in lessons)
        assert "git_push failed with auth" in texts
        assert "We always use Alembic for schema changes" in texts
        assert "keep the billing column nullable" in texts

    def test_a_failure_that_replanning_fixed_is_a_correction(self) -> None:
        lessons = extract_lessons(_turn(message="Run the migration.", answer="ok", steps=FAILED_THEN_FIXED,
                                        revisions=1))
        assert any(lesson.kind == "correction" and "run_command" in lesson.text for lesson in lessons)

    def test_a_failure_from_an_earlier_pass_has_no_tool_name_and_is_still_a_lesson(self) -> None:
        lessons = extract_lessons(_turn(message="Check the repo.", answer="ok", revisions=1, steps=(
            LessonStep(tool="", description="check the repo state", status="failed", error_kind="tool_execution_error"),
            LessonStep(tool="calculator", description="add", status="succeeded"),
        )))
        assert lessons[0].kind == "correction"
        assert lessons[0].text.startswith("When a step failed with tool_execution_error (check the repo state)")

    def test_questions_and_plain_requests_are_not_conventions(self) -> None:
        lessons = extract_lessons(_turn(message="Should we always use Alembic? Add a column please.", answer="ok"))
        assert not any(lesson.kind == "convention" for lesson in lessons)

    def test_a_tainted_turn_yields_nothing(self) -> None:
        assert extract_lessons(_turn(tainted=True)) == []

    def test_the_saved_checkpoints_decisions_are_used(self) -> None:
        checkpoint = SessionCheckpoint(decisions=("Use the staging database for load tests only",))
        lessons = extract_lessons(_turn(message="hello there", answer="hi"), checkpoint)
        assert any("staging database for load tests only" in lesson.text for lesson in lessons)

    def test_bounded_deduplicated_and_short(self) -> None:
        message = " ".join(f"We always use tool number {i} for task {i}." for i in range(20))
        lessons = extract_lessons(_turn(message=message, answer=""))
        assert len(lessons) == 5 and len({lesson.text for lesson in lessons}) == 5
        assert all(len(lesson.text) <= 300 for lesson in lessons)

    def test_step_output_and_arguments_can_never_reach_a_lesson(self) -> None:
        """The record has no field for them, so there is nothing to leak: a step carries a
        description, a status and an error kind, nothing a tool printed."""
        assert {f.name for f in dataclasses.fields(LessonStep)} == {
            "tool", "description", "status", "error_kind", "attempts"}


@pytest.fixture
def memory(container):
    return container.memory


async def _pending(memory, owner=ALICE):
    return await memory.list_pending_lessons(owner, scope=ENGINEER_SCOPE)


class TestJob:
    async def test_disabled_and_ineligible_turns_do_nothing(self, memory) -> None:
        assert (await run_engineer_lesson_extraction(memory, _turn(), enabled=False))["reason"] == "lessons_disabled"
        assert (await run_engineer_lesson_extraction(None, _turn(), enabled=True))["status"] == "skipped"
        assert (await run_engineer_lesson_extraction(memory, _turn(tainted=True), enabled=True))["reason"] == "tainted_turn"
        assert (await run_engineer_lesson_extraction(memory, _turn(agent_id="orchestrator"), enabled=True))["reason"] == "not_engineer"
        assert await _pending(memory) == []

    async def test_lessons_land_pending_with_provenance_and_stay_out_of_every_recall(self, memory) -> None:
        report = await run_engineer_lesson_extraction(memory, _turn(), enabled=True)
        assert report["stored"] == report["candidates"] >= 2 and report["errors"] == 0

        pending = await _pending(memory)
        assert pending, "lessons were queued for review"
        for record in pending:
            assert record.provenance.source == LESSON_SOURCE
            assert record.scope == pending_scope(ENGINEER_SCOPE) and record.kind is MemoryKind.PROCEDURAL
            assert record.metadata["review"] == "pending" and record.expires_at is not None
            assert record.session_id is None, "a lesson is not one conversation's memory"
            assert record.metadata["source_session"] == "s-1"

        # Not in the engineer's own recall, not in shared, not for anyone else.
        for scopes in (readable_scopes(ENGINEER_SCOPE), readable_scopes("shared"), None):
            for kinds in ((MemoryKind.PROCEDURAL,), (MemoryKind.PROCEDURAL, MemoryKind.SEMANTIC)):
                hits = await memory.recall(MemoryQuery(
                    owner_id=ALICE, text="We always use Alembic for schema changes",
                    kinds=kinds, scopes=scopes if scopes is None else scopes, min_score=0.0,
                ))
                if scopes is None:
                    assert hits, "the owner's own unscoped view does see them (that is the review surface)"
                else:
                    assert hits == [], f"a pending lesson was recallable through scopes={scopes}"
        assert await _pending(memory, BOB) == []

    async def test_the_same_lesson_twice_is_a_duplicate_not_a_second_record(self, memory) -> None:
        first = await run_engineer_lesson_extraction(memory, _turn(), enabled=True)
        second = await run_engineer_lesson_extraction(memory, _turn(turn_id="t-2"), enabled=True)
        assert first["stored"] > 0 and second["stored"] == 0 and second["duplicates"] == second["candidates"]
        assert len(await _pending(memory)) == first["stored"]

    async def test_an_approved_lesson_is_still_a_duplicate_of_itself(self, memory) -> None:
        await run_engineer_lesson_extraction(memory, _turn(), enabled=True)
        for record in await _pending(memory):
            await memory.approve_lesson(record.id, owner_id=ALICE, scope=ENGINEER_SCOPE, approved_by="alice")
        again = await run_engineer_lesson_extraction(memory, _turn(turn_id="t-3"), enabled=True)
        assert again["stored"] == 0 and again["duplicates"] == again["candidates"]
        assert await _pending(memory) == []

    async def test_a_candidate_containing_a_secret_is_dropped_not_redacted(self, memory) -> None:
        fake_key = "sk-" + "A1b2C3d4" * 4  # built at runtime: no credential-looking literal in source
        report = await run_engineer_lesson_extraction(
            memory, _turn(message=f"We always export OPENAI_API_KEY={fake_key} before running the suite.",
                          answer="Noted."),
            enabled=True,
        )
        assert report["rejected_secret"] >= 1
        stored = " ".join(r.content for r in await _pending(memory))
        assert fake_key not in stored and "OPENAI_API_KEY" not in stored

    async def test_the_injection_screen_drops_what_it_rejects(self, memory) -> None:
        async def screen(text: str) -> bool:
            return "Alembic" not in text

        report = await run_engineer_lesson_extraction(memory, _turn(), enabled=True, screen=screen)
        assert report["rejected_unsafe"] >= 1
        assert not any("Alembic" in r.content for r in await _pending(memory))

    async def test_one_candidate_failing_does_not_stop_the_rest(self, memory) -> None:
        calls = {"n": 0}
        real_remember = memory.remember

        async def flaky(**kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("store hiccup")
            return await real_remember(**kwargs)

        memory.remember = flaky  # the one adapter-boundary failure we need to simulate
        try:
            report = await run_engineer_lesson_extraction(memory, _turn(), enabled=True)
        finally:
            memory.remember = real_remember
        assert report["errors"] == 1 and report["stored"] >= 1


class TestReview:
    async def _queue(self, memory):
        await run_engineer_lesson_extraction(memory, _turn(), enabled=True)
        return await _pending(memory)

    async def test_approval_promotes_into_the_engineers_recall_and_names_the_approver(self, memory) -> None:
        pending = await self._queue(memory)
        target = next(r for r in pending if "Alembic" in r.content)
        promoted = await memory.approve_lesson(
            target.id, owner_id=ALICE, scope=ENGINEER_SCOPE, approved_by="alice@example.com")
        assert promoted.scope == ENGINEER_SCOPE and promoted.expires_at is None
        assert promoted.metadata["review"] == "approved"
        assert promoted.metadata["approved_by"] == "alice@example.com" and promoted.metadata["approved_at"]
        assert promoted.confidence >= 0.7

        query = dict(owner_id=ALICE, text="We always use Alembic for schema changes",
                     kinds=(MemoryKind.PROCEDURAL,), min_score=0.0)
        engineer = await memory.recall(MemoryQuery(scopes=readable_scopes(ENGINEER_SCOPE), **query))
        assert any(h.record.id == target.id for h in engineer), "approved: the Engineer now recalls it"
        other_persona = await memory.recall(MemoryQuery(scopes=readable_scopes("finance"), **query))
        assert not any(h.record.id == target.id for h in other_persona), "and it stays the Engineer's"
        assert target.id not in {r.id for r in await _pending(memory)}

    async def test_an_approved_lesson_is_recalled_in_a_different_conversation(self, memory) -> None:
        """Found by running the real path: a lesson stamped with its source session was filtered out
        of every other session's recall, so approving it changed nothing for the next conversation."""
        pending = await self._queue(memory)
        target = next(r for r in pending if "Alembic" in r.content)
        await memory.approve_lesson(target.id, owner_id=ALICE, scope=ENGINEER_SCOPE, approved_by="alice")
        hits = await memory.recall(MemoryQuery(
            owner_id=ALICE, text="We always use Alembic for schema changes.", session_id=SessionId("a-later-chat"),
            kinds=(MemoryKind.SEMANTIC, MemoryKind.PROCEDURAL, MemoryKind.ENTITY),
            scopes=readable_scopes(ENGINEER_SCOPE),
        ))
        assert any(h.record.id == target.id for h in hits)

    async def test_rejection_deletes_it(self, memory) -> None:
        target = (await self._queue(memory))[0]
        assert await memory.reject_lesson(target.id, owner_id=ALICE, scope=ENGINEER_SCOPE) is True
        assert target.id not in {r.id for r in await _pending(memory)}
        assert await memory.reject_lesson(target.id, owner_id=ALICE, scope=ENGINEER_SCOPE) is False

    async def test_only_the_owner_can_review_and_only_pending_auto_lessons(self, memory) -> None:
        target = (await self._queue(memory))[0]
        assert await memory.approve_lesson(target.id, owner_id=BOB, scope=ENGINEER_SCOPE, approved_by="bob") is None
        assert await memory.reject_lesson(target.id, owner_id=BOB, scope=ENGINEER_SCOPE) is False

        ordinary = await memory.remember(owner_id=ALICE, content="Alice prefers tabs over spaces.",
                                         kind=MemoryKind.PROCEDURAL, source="api:v3:alice")
        assert await memory.approve_lesson(ordinary.id, owner_id=ALICE, scope=ENGINEER_SCOPE, approved_by="a") is None
        assert await memory.reject_lesson(ordinary.id, owner_id=ALICE, scope=ENGINEER_SCOPE) is False
        assert await memory.approve_lesson(MemoryId("nope"), owner_id=ALICE, scope=ENGINEER_SCOPE, approved_by="a") is None
        assert target.id in {r.id for r in await _pending(memory)}

    async def test_forgetting_the_source_removes_every_lesson(self, memory) -> None:
        await self._queue(memory)
        assert await memory.forget_source(owner_id=ALICE, source=LESSON_SOURCE) > 0
        assert await _pending(memory) == []


# -- through a real Engineer turn ----------------------------------------------------------------


def _container(enabled: bool) -> Container:
    base = load_settings(refresh=True, validate=False)
    return Container(dataclasses.replace(
        base, learning=dataclasses.replace(base.learning, auto_lessons_enabled=enabled),
        # The CPU default caps the whole prompt at 400 tokens, which the Engineer's own system prompt
        # nearly fills; a hosted slot raises it, and so does this test, so memory has room to appear.
        llm=dataclasses.replace(base.llm, max_prompt_tokens=4000),
    ))


class _FixedPlanner:
    def __init__(self, plan: Plan) -> None:
        self._plan = plan

    def needs_plan(self, message, *, available_tools=(), force=None, previous_reply=None) -> bool:  # noqa: ANN001
        return True

    def goal_from_message(self, message, *, criteria=()):  # noqa: ANN001
        return Goal(description=message)

    async def plan(self, goal, available_tools=()):  # noqa: ANN001
        return self._plan

    async def replan(self, *a, **k):  # pragma: no cover - the loop may ask; no revision is the answer
        return None

    def can_replan(self, *a, **k) -> bool:
        return False


async def _engineer_turn(container, message: str, *, tainted_tool: bool = False):
    from datetime import datetime, timezone

    from aura_core.domain.engineering import ENGINEER_CONSENT_NOTICE_VERSION
    from aura_core.domain.errors import ToolExecutionError

    await container.startup()
    await container.engineer_consent.accept(ALICE, ENGINEER_CONSENT_NOTICE_VERSION, at=datetime.now(timezone.utc))

    @tool("git_status", "Show git status.", param("target", "string", "t"), sensitivity=ToolSensitivity.PUBLIC)
    async def git_status(target: str) -> str:
        raise ToolExecutionError("not a git repository", tool="git_status")

    @tool("calculator", "Add.", param("target", "string", "t"), sensitivity=ToolSensitivity.PUBLIC)
    async def calculator(target: str) -> str:
        return "4"

    container.tools.register(git_status, overwrite=True)
    container.tools.register(calculator, overwrite=True)
    plan = Plan(id=PlanId.new(), goal=Goal(description="g"), steps=(
        PlanStep(id=StepId("s1"), kind=StepKind.TOOL, tool="git_status", description="check the repo state",
                 arguments={"target": "."}),
        PlanStep(id=StepId("s2"), kind=StepKind.TOOL, tool="calculator", description="add the numbers",
                 arguments={"target": "2+2"}),
    ))
    container.kernel._deps.planner = _FixedPlanner(plan)

    async def unplannable(goal, available_tools=(), max_steps=None):  # noqa: ANN001
        # The agent loop replans with the real engine. Make its model answer unusable on purpose
        # (the fallback is one respond step carrying the goal, "Failed: ..." lines included), so
        # the result does not depend on how much of its own prompt the echo provider repeats.
        step = PlanStep(id=StepId("s1"), kind=StepKind.RESPOND, description=goal.description)
        return Plan(id=PlanId.new(), goal=goal, steps=(step,))

    container.planner.plan = unplannable
    result = await container.kernel.run(TurnRequest(
        message=message, user_id=ALICE, session_id=SessionId("eng-session"), agent="engineer",
        capabilities=("engineer_agent",), plan=True,
    ))
    await container.kernel.wait_for_lessons()
    return result


class TestThroughTheKernel:
    MESSAGE = "We always use Alembic for schema changes. Never edit the database by hand. Check the repo."

    async def test_an_engineer_turn_queues_lessons_for_review_and_nothing_trusted(self) -> None:
        container = _container(True)
        result = await _engineer_turn(container, self.MESSAGE)
        assert result.telemetry.outcome.value == "success" and result.resolved_agent == "engineer"

        pending = await container.memory.list_pending_lessons(ALICE, scope=ENGINEER_SCOPE)
        texts = [r.content for r in pending]
        # The agent loop replanned around the failing step; the correction is still learned.
        assert any("failed with tool_execution_error (check the repo state)" in t for t in texts), texts
        assert any("always use Alembic" in t for t in texts), texts
        assert all(r.provenance.source == LESSON_SOURCE for r in pending)
        assert not any("not a git repository" in t for t in texts), "tool output must never become a lesson"

        # The Engineer's next turn recalls none of it until approved.
        hits = await container.memory.recall(MemoryQuery(
            owner_id=ALICE, text="always use Alembic for schema changes", min_score=0.0,
            kinds=(MemoryKind.PROCEDURAL, MemoryKind.SEMANTIC), scopes=readable_scopes(ENGINEER_SCOPE),
        ))
        assert hits == []

    async def test_the_engineers_next_conversation_sees_a_lesson_only_after_approval(self) -> None:
        container = _container(True)
        await _engineer_turn(container, self.MESSAGE)
        pending = await container.memory.list_pending_lessons(ALICE, scope=ENGINEER_SCOPE)
        target = next(r for r in pending if "always use Alembic" in r.content)
        question = "We always use Alembic for schema changes."
        echo = container.providers[0]

        async def lesson_in_context() -> bool:
            before = len(echo.calls)
            await container.kernel.run(TurnRequest(
                message=question, user_id=ALICE, session_id=SessionId("a-different-chat"), agent="engineer",
                capabilities=("engineer_agent",), plan=False,
            ))
            return any(
                "always use Alembic for schema changes" in m.content and "<<<AURA-DATA" in m.content
                for call in echo.calls[before:] for m in call.conversation if m.role.value == "system"
            )

        assert await lesson_in_context() is False, "pending: not part of the Engineer's memory"
        await container.memory.approve_lesson(target.id, owner_id=ALICE, scope=ENGINEER_SCOPE, approved_by="alice")
        assert await lesson_in_context() is True, "approved: recalled, fenced as data, in a conversation it did not come from"

    async def test_off_by_default_nothing_is_written(self) -> None:
        container = _container(False)
        assert container.lesson_sink is None
        await _engineer_turn(container, self.MESSAGE)
        assert await container.memory.list_pending_lessons(ALICE, scope=ENGINEER_SCOPE) == []

    async def test_a_non_engineer_turn_is_never_mined(self) -> None:
        container = _container(True)
        await container.startup()
        await container.kernel.run(TurnRequest(message=self.MESSAGE, user_id=ALICE, session_id=SessionId("o-1")))
        await container.kernel.wait_for_lessons()
        assert await container.memory.list_pending_lessons(ALICE, scope=ENGINEER_SCOPE) == []

    async def test_the_streaming_path_extracts_too(self) -> None:
        container = _container(True)
        await container.startup()
        from datetime import datetime, timezone

        from aura_core.domain.engineering import ENGINEER_CONSENT_NOTICE_VERSION

        await container.engineer_consent.accept(ALICE, ENGINEER_CONSENT_NOTICE_VERSION, at=datetime.now(timezone.utc))
        frames = [f async for f in container.kernel.stream(TurnRequest(
            message=self.MESSAGE, user_id=ALICE, session_id=SessionId("eng-stream"), agent="engineer",
            capabilities=("engineer_agent",),
        ))]
        assert frames[-1]["type"] == "done"
        await container.kernel.wait_for_lessons()
        pending = await container.memory.list_pending_lessons(ALICE, scope=ENGINEER_SCOPE)
        assert any("always use Alembic" in r.content for r in pending)


class TestRoutes:
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

    async def test_list_approve_reject_through_http(self, client, container) -> None:
        await run_engineer_lesson_extraction(container.memory, _turn(), enabled=True)
        listed = client.get("/api/v3/memory/lessons", headers=self._auth(ALICE)).json()["lessons"]
        assert len(listed) >= 2 and all(item["content"] for item in listed)

        keep = next(item for item in listed if "Alembic" in item["content"])
        drop = next(item for item in listed if item["id"] != keep["id"])
        approved = client.post(f"/api/v3/memory/lessons/{keep['id']}/approve", headers=self._auth(ALICE))
        assert approved.status_code == 200 and approved.json()["approved"] is True
        rejected = client.post(f"/api/v3/memory/lessons/{drop['id']}/reject", headers=self._auth(ALICE))
        assert rejected.status_code == 200

        remaining = {i["id"] for i in client.get("/api/v3/memory/lessons", headers=self._auth(ALICE)).json()["lessons"]}
        assert keep["id"] not in remaining and drop["id"] not in remaining

        stored = await container.memory._store.get(MemoryId(keep["id"]))
        assert stored.scope == ENGINEER_SCOPE and stored.metadata["approved_by"] == "alice-lessons@example.com"

        again = client.post(f"/api/v3/memory/lessons/{keep['id']}/approve", headers=self._auth(ALICE))
        assert again.status_code == 404, "approving something no longer pending is a 404"

    def test_unauthenticated_is_refused(self, client) -> None:
        assert client.get("/api/v3/memory/lessons").status_code in (401, 403)
