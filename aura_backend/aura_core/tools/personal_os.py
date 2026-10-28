"""Personal-OS propose tools.

These tools do not write Postgres or any other server store. They return a canonical
``{type, params}`` payload that the Flutter ``PendingActionsExecutor`` applies locally
after the user approves the card. Server-side they are side-effecting so a tainted
turn cannot emit them without a bound confirmation (``approval_ids``, ADR-006).
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping, Sequence

from ..domain.tools import ToolCategory, ToolSensitivity
from .base import param, tool

__all__ = [
    "PERSONAL_OS_TOOL_NAMES",
    "personal_os_tools",
    "parse_proposed_action",
    "actions_from_tool_results",
    "matching_personal_os_tool",
]

PERSONAL_OS_TOOL_NAMES = frozenset({
    "propose_task",
    "propose_event",
    "propose_expense",
    "propose_income",
    "propose_health_log",
})

_ACTION_TYPES = frozenset({
    "add_task",
    "add_event",
    "add_expense",
    "add_income",
    "log_health",
})

# Imperative CRUD, not questions. Gated on the matching propose_* tool being offered so a
# short "what's my leave balance?" never pays for a planning generation just because the
# default install registers personal-OS tools.
_INTENT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\b(?:add|create|make|set|new)\b.{0,48}"
            r"\b(?:a |an |my )?(?:task|to-?do|todo|reminder)s?\b",
            re.I,
        ),
        "propose_task",
    ),
    (re.compile(r"\bremind me\b", re.I), "propose_task"),
    (
        re.compile(
            r"\b(?:schedule|book)\b.{0,48}"
            r"\b(?:an? |my )?(?:event|meeting|appointment|call|lunch|dinner)\b",
            re.I,
        ),
        "propose_event",
    ),
    (
        re.compile(
            r"\b(?:add|create)\b.{0,48}\b(?:an? |my )?(?:event|appointment|meeting)\b",
            re.I,
        ),
        "propose_event",
    ),
    (re.compile(r"\b(?:on my calendar|to my calendar)\b", re.I), "propose_event"),
    (
        re.compile(
            r"\b(?:log|record|add|track)\b.{0,48}\b(?:an? )?(?:expense|purchase)\b",
            re.I,
        ),
        "propose_expense",
    ),
    (
        re.compile(r"\bi (?:spent|paid)\b.{0,32}(?:\d|\$|€|£|usd|egp|dollars?)", re.I),
        "propose_expense",
    ),
    (
        re.compile(r"\b(?:log|record|add)\b.{0,48}\b(?:an? )?income\b", re.I),
        "propose_income",
    ),
    (
        re.compile(
            r"\b(?:log|record|track)\b.{0,48}"
            r"\b(?:water|sleep|weight|calories|exercise|workout|health)\b",
            re.I,
        ),
        "propose_health_log",
    ),
    (
        re.compile(
            r"\b(?:i (?:drank|slept)|water intake|log (?:my )?weight)\b",
            re.I,
        ),
        "propose_health_log",
    ),
)


def matching_personal_os_tool(
    message: str, available_tools: Sequence[str]
) -> str | None:
    """Return the propose_* tool a short imperative should run, or ``None``.

    Used by :meth:`PlanningEngine.needs_plan` so "add a task to buy milk" still
    reaches execution after the v1 ``[ACTIONS]`` path was locked. Returns ``None``
    when none of the matching tools are in ``available_tools``.
    """
    offered = {name for name in available_tools if name in PERSONAL_OS_TOOL_NAMES}
    if not offered:
        return None
    text = message.strip()
    if not text:
        return None
    for pattern, tool in _INTENT_PATTERNS:
        if tool in offered and pattern.search(text):
            return tool
    return None


def _payload(action_type: str, params: Mapping[str, Any]) -> str:
    cleaned = {key: value for key, value in params.items() if value is not None}
    return json.dumps({"type": action_type, "params": cleaned}, ensure_ascii=False)


def parse_proposed_action(text: str) -> dict[str, Any] | None:
    """Extract ``{type, params}`` from a propose_* tool result string."""
    try:
        data = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    action_type = data.get("type")
    params = data.get("params")
    if action_type not in _ACTION_TYPES or not isinstance(params, dict):
        return None
    return {"type": action_type, "params": params}


def actions_from_tool_results(pairs: Sequence[tuple[str, str]]) -> list[dict[str, Any]]:
    """Map successful personal-OS tool results into SSE ``actions`` payloads."""
    actions: list[dict[str, Any]] = []
    for name, text in pairs:
        if name not in PERSONAL_OS_TOOL_NAMES:
            continue
        parsed = parse_proposed_action(text)
        if parsed is not None:
            actions.append(parsed)
    return actions


def personal_os_tools() -> list:
    """Task / calendar / finance / health proposals for the client executor."""

    @tool(
        "propose_task",
        "Propose creating a task on the user's device. Does not create it until the user "
        "approves. Use when the user asks to remember, track, or do something later.",
        param("title", "string", "Short task title.", max_length=200),
        param("priority", "string", "Priority: low, medium, or high.", required=False,
              enum=["low", "medium", "high"], default="medium"),
        param("category", "string", "Task category, e.g. personal or work.", required=False,
              max_length=64, default="personal"),
        param("dueDate", "string", "Due date as an ISO-8601 datetime, if known.",
              required=False, max_length=40),
        param("description", "string", "Optional longer description.", required=False,
              max_length=500),
        category=ToolCategory.PRODUCTIVITY,
        sensitivity=ToolSensitivity.PERSONAL,
        side_effecting=True,
        timeout_s=2.0,
    )
    async def propose_task(
        title: str,
        priority: str = "medium",
        category: str = "personal",
        dueDate: str | None = None,
        description: str | None = None,
    ) -> str:
        return _payload("add_task", {
            "title": title,
            "priority": priority,
            "category": category,
            "dueDate": dueDate,
            "description": description,
        })

    @tool(
        "propose_event",
        "Propose scheduling a calendar event on the user's device. Does not create it until "
        "the user approves.",
        param("title", "string", "Event title.", max_length=200),
        param("startTime", "string", "Start time as an ISO-8601 datetime.", max_length=40),
        param("endTime", "string", "End time as an ISO-8601 datetime. Omit to default to one hour.",
              required=False, max_length=40),
        param("description", "string", "Optional event notes.", required=False, max_length=500),
        category=ToolCategory.PRODUCTIVITY,
        sensitivity=ToolSensitivity.PERSONAL,
        side_effecting=True,
        timeout_s=2.0,
    )
    async def propose_event(
        title: str,
        startTime: str,
        endTime: str | None = None,
        description: str | None = None,
    ) -> str:
        return _payload("add_event", {
            "title": title,
            "startTime": startTime,
            "endTime": endTime,
            "description": description,
        })

    @tool(
        "propose_expense",
        "Propose logging an expense on the user's device. Does not record it until the user "
        "approves.",
        param("amount", "number", "Expense amount.", minimum=0),
        param("category", "string", "Expense category, e.g. Food or Transport.", required=False,
              max_length=64, default="Other"),
        param("description", "string", "What the expense was for.", required=False, max_length=300),
        category=ToolCategory.PRODUCTIVITY,
        sensitivity=ToolSensitivity.PERSONAL,
        side_effecting=True,
        timeout_s=2.0,
    )
    async def propose_expense(
        amount: float,
        category: str = "Other",
        description: str | None = None,
    ) -> str:
        return _payload("add_expense", {
            "amount": amount,
            "category": category,
            "description": description,
        })

    @tool(
        "propose_income",
        "Propose logging income on the user's device. Does not record it until the user "
        "approves.",
        param("amount", "number", "Income amount.", minimum=0),
        param("category", "string", "Income category, e.g. Salary.", required=False,
              max_length=64, default="Other"),
        param("description", "string", "What the income was for.", required=False, max_length=300),
        category=ToolCategory.PRODUCTIVITY,
        sensitivity=ToolSensitivity.PERSONAL,
        side_effecting=True,
        timeout_s=2.0,
    )
    async def propose_income(
        amount: float,
        category: str = "Other",
        description: str | None = None,
    ) -> str:
        return _payload("add_income", {
            "amount": amount,
            "category": category,
            "description": description,
        })

    @tool(
        "propose_health_log",
        "Propose logging a health metric on the user's device. Does not record it until the "
        "user approves. Include only the metrics the user stated.",
        param("waterIntakeMl", "integer", "Water intake in millilitres.", required=False,
              minimum=0),
        param("sleepMinutes", "integer", "Sleep duration in minutes.", required=False, minimum=0),
        param("caloriesConsumed", "integer", "Calories consumed.", required=False, minimum=0),
        param("exerciseMinutes", "integer", "Exercise duration in minutes.", required=False,
              minimum=0),
        param("weight", "number", "Body weight.", required=False, minimum=0),
        category=ToolCategory.PRODUCTIVITY,
        sensitivity=ToolSensitivity.PERSONAL,
        side_effecting=True,
        timeout_s=2.0,
    )
    async def propose_health_log(
        waterIntakeMl: int | None = None,
        sleepMinutes: int | None = None,
        caloriesConsumed: int | None = None,
        exerciseMinutes: int | None = None,
        weight: float | None = None,
    ) -> str:
        return _payload("log_health", {
            "waterIntakeMl": waterIntakeMl,
            "sleepMinutes": sleepMinutes,
            "caloriesConsumed": caloriesConsumed,
            "exerciseMinutes": exerciseMinutes,
            "weight": weight,
        })

    return [propose_task, propose_event, propose_expense, propose_income, propose_health_log]
