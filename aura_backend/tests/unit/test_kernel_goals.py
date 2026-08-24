"""The kernel fetches active goals alongside memory/retrieval/history and threads them into
context assembly (Phase D2)."""

from __future__ import annotations

from datetime import datetime, timezone

from aura_core.domain.goals import GoalSnapshot
from aura_core.domain.identifiers import UserId
from aura_core.kernel import TurnRequest


def _request(message: str = "hello", **overrides):
    base = dict(message=message, user_id=UserId("test-user"))
    base.update(overrides)
    return TurnRequest(**base)


def _goal(**overrides) -> GoalSnapshot:
    base = dict(
        id="11111111-1111-1111-1111-111111111111",
        category="fitness",
        status="active",
        priority="high",
        target_date=datetime(2026, 9, 1, tzinfo=timezone.utc),
        progress_pct=10,
    )
    base.update(overrides)
    return GoalSnapshot(**base)


class TestGoalsFlowIntoContext:
    async def test_gather_context_returns_seeded_active_goals(self, container, user) -> None:
        await container.startup()
        container.goals.seed(user, [_goal()])

        _memories, _retrieval, _history, _summary, goals = await container.kernel._gather_context(
            _request(user_id=user), "hello"
        )
        assert len(goals) == 1
        assert goals[0].category == "fitness" and goals[0].priority == "high"

    async def test_archived_goals_are_excluded(self, container, user) -> None:
        await container.startup()
        container.goals.seed(user, [_goal(status="achieved")])

        _memories, _retrieval, _history, _summary, goals = await container.kernel._gather_context(
            _request(user_id=user), "hello"
        )
        assert len(goals) == 0

    async def test_a_turn_with_no_goals_backend_degrades_to_empty(self, container, user) -> None:
        """``deps.goals is None`` (a deployment that opts out) must not fail the turn."""
        await container.startup()
        container.kernel._deps.goals = None

        _memories, _retrieval, _history, _summary, goals = await container.kernel._gather_context(
            _request(user_id=user), "hello"
        )
        assert len(goals) == 0

    async def test_a_full_turn_succeeds_with_active_goals_present(self, container, user) -> None:
        """End-to-end: seeding a goal must not break the ordinary turn pipeline."""
        await container.startup()
        container.goals.seed(user, [_goal()])

        result = await container.kernel.run(_request(user_id=user))
        assert result.text
