"""The context engine reads goal snapshots into the assembled prompt (Phase D2).

``GoalSnapshot`` carries only the legacy ``goals`` table's plaintext columns -- the backend never
sees ``title_enc``/``description_enc``, so these tests assert on what *is* readable (priority,
category, target date, progress) and that no title/description ever appears, structurally: the
type simply has no such field to leak.
"""

from __future__ import annotations

from datetime import datetime, timezone

from aura_core.context import ContextEngine, ContextRequest
from aura_core.domain.context import ContextSection
from aura_core.domain.goals import GoalSnapshot
from aura_core.prompts import build_default_registry


def _system_prompt() -> str:
    return (
        build_default_registry()
        .render(
            "system.orchestrator",
            {
                "fence_rules": "",
                "current_time": "2026-08-24T09:00+03:00",
                "capabilities": "calculator, current_datetime",
            },
        )
        .text
    )


def _goal(**overrides) -> GoalSnapshot:
    base = dict(
        id="11111111-1111-1111-1111-111111111111",
        category="fitness",
        status="active",
        priority="high",
        target_date=datetime(2026, 9, 1, tzinfo=timezone.utc),
        progress_pct=40,
    )
    base.update(overrides)
    return GoalSnapshot(**base)


class TestContextEngineReadsGoals:
    def test_active_goal_becomes_a_memory_section_item(self) -> None:
        assembled = ContextEngine().assemble(
            ContextRequest(
                system_prompt=_system_prompt(),
                user_message="What should I focus on today?",
                goals=(_goal(),),
            )
        )
        memory_items = assembled.bundle.by_section(ContextSection.MEMORY)
        assert any(item.provenance.startswith("goal:") for item in memory_items)
        goal_item = next(item for item in memory_items if item.provenance.startswith("goal:"))
        assert "priority=high" in goal_item.content
        assert "category=fitness" in goal_item.content
        assert "progress=40%" in goal_item.content
        assert "2026-09-01" in goal_item.content

    def test_no_goal_fields_beyond_the_snapshot_leak_into_content(self) -> None:
        """GoalSnapshot has no title/description field -- there is nothing to decrypt or leak."""
        assert not hasattr(GoalSnapshot, "title")
        assert not hasattr(GoalSnapshot, "description")
        assembled = ContextEngine().assemble(
            ContextRequest(
                system_prompt=_system_prompt(),
                user_message="hello",
                goals=(_goal(category=None, target_date=None),),
            )
        )
        goal_item = next(
            item for item in assembled.bundle.by_section(ContextSection.MEMORY)
            if item.provenance.startswith("goal:")
        )
        assert "category=" not in goal_item.content
        assert "target_date=" not in goal_item.content

    def test_no_goals_means_no_goal_items(self) -> None:
        assembled = ContextEngine().assemble(
            ContextRequest(system_prompt=_system_prompt(), user_message="hello")
        )
        assert not any(
            item.provenance.startswith("goal:")
            for item in assembled.bundle.by_section(ContextSection.MEMORY)
        )

    def test_multiple_goals_each_produce_a_distinct_item(self) -> None:
        goals = (
            _goal(id="aaaaaaaa-1111-1111-1111-111111111111", priority="high"),
            _goal(id="bbbbbbbb-2222-2222-2222-222222222222", priority="low", category="career"),
        )
        assembled = ContextEngine().assemble(
            ContextRequest(
                system_prompt=_system_prompt(), user_message="hello", goals=goals,
                max_prompt_tokens=0, context_window=32768,
            )
        )
        goal_items = [
            item for item in assembled.bundle.by_section(ContextSection.MEMORY)
            if item.provenance.startswith("goal:")
        ]
        assert len(goal_items) == 2
        assert {item.item_id for item in goal_items} == {
            f"goal:{g.id}" for g in goals
        }
