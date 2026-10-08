"""Personal-OS propose tools: canonical client executor payloads, no server writes."""

from __future__ import annotations

import json

import pytest

from aura_core.domain.tools import ToolSensitivity
from aura_core.ports.runtime import ToolContext
from aura_core.tools.personal_os import (
    PERSONAL_OS_TOOL_NAMES,
    actions_from_tool_results,
    matching_personal_os_tool,
    parse_proposed_action,
    personal_os_tools,
)


@pytest.fixture
def ctx(user) -> ToolContext:
    return ToolContext(user_id=user, correlation_id="corr-1", deadline_s=30.0)


def _tools() -> dict:
    return {t.spec.name: t for t in personal_os_tools()}


async def test_propose_task_payload_matches_client_executor(ctx: ToolContext) -> None:
    tools = _tools()
    raw = await tools["propose_task"].execute(
        {"title": "Buy milk", "priority": "high", "category": "personal",
         "dueDate": "2026-08-22T10:00:00"},
        ctx,
    )
    payload = json.loads(raw)
    assert payload == {
        "type": "add_task",
        "params": {
            "title": "Buy milk",
            "priority": "high",
            "category": "personal",
            "dueDate": "2026-08-22T10:00:00",
        },
    }


async def test_propose_event_expense_income_and_health(ctx: ToolContext) -> None:
    tools = _tools()
    event = json.loads(await tools["propose_event"].execute(
        {"title": "Dentist", "startTime": "2026-08-22T09:00:00",
         "endTime": "2026-08-22T10:00:00"},
        ctx,
    ))
    assert event["type"] == "add_event"
    assert event["params"]["title"] == "Dentist"

    expense = json.loads(await tools["propose_expense"].execute(
        {"amount": 12.5, "category": "Food"}, ctx,
    ))
    assert expense == {"type": "add_expense", "params": {"amount": 12.5, "category": "Food"}}

    income = json.loads(await tools["propose_income"].execute(
        {"amount": 100, "category": "Salary"}, ctx,
    ))
    assert income["type"] == "add_income"

    health = json.loads(await tools["propose_health_log"].execute(
        {"waterIntakeMl": 500, "weight": 72.5}, ctx,
    ))
    assert health["type"] == "log_health"
    assert health["params"]["waterIntakeMl"] == 500
    assert health["params"]["weight"] == 72.5


def test_personal_os_tools_are_side_effecting_and_personal() -> None:
    for tool in personal_os_tools():
        assert tool.spec.name in PERSONAL_OS_TOOL_NAMES
        assert tool.spec.side_effecting is True
        assert tool.spec.sensitivity is ToolSensitivity.PERSONAL


def test_actions_from_tool_results_ignores_other_tools() -> None:
    pairs = [
        ("calculator", "2+2 = 4"),
        ("propose_task", json.dumps({"type": "add_task", "params": {"title": "X"}})),
        ("propose_expense", "not-json"),
    ]
    actions = actions_from_tool_results(pairs)
    assert actions == [{"type": "add_task", "params": {"title": "X"}}]


def test_parse_proposed_action_rejects_unknown_types() -> None:
    assert parse_proposed_action('{"type": "wire_transfer", "params": {}}') is None
    assert parse_proposed_action("[]") is None
    assert parse_proposed_action("{") is None


def test_matching_personal_os_tool_is_gated_on_available_tools() -> None:
    assert matching_personal_os_tool("add a task to buy milk", ["propose_task"]) == "propose_task"
    assert matching_personal_os_tool("add a task to buy milk", ["calculator"]) is None
    assert matching_personal_os_tool("what is my leave balance?", list(PERSONAL_OS_TOOL_NAMES)) is None
    assert matching_personal_os_tool("schedule a meeting at 3", ["propose_event"]) == "propose_event"
    assert matching_personal_os_tool("I spent $12 on coffee", ["propose_expense"]) == "propose_expense"
