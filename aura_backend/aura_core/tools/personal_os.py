"""Personal-OS propose tools.

These tools do not write Postgres or any other server store. They return a canonical
``{type, params}`` payload that the Flutter ``PendingActionsExecutor`` applies locally
after the user approves the card. Server-side they are side-effecting so a tainted
turn cannot emit them without a bound confirmation (``approval_ids``, ADR-006).
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime
from typing import Any, Mapping, Sequence

from ..domain.errors import ToolExecutionError
from ..domain.text import fold_arabic
from ..domain.tools import ToolCategory, ToolSensitivity
from .base import param, tool

__all__ = [
    "PERSONAL_OS_TOOL_NAMES",
    "personal_os_tools",
    "parse_proposed_action",
    "actions_from_tool_results",
    "matching_personal_os_tool",
    "matching_record_tool",
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
    # Arabic (audit A2): matched on folded text -- no diacritics, bare alefs, final ة as ه --
    # so "ذكّرني" and "ذكرني", "أضف" and "اضف" are the same request.
    (re.compile(r"ذكرني|نبهني"), "propose_task"),
    (
        re.compile(r"(?:اضف|ضيف|اضيف|انشئ|اعمل|سجل)\S*\s.{0,40}(?:مهمه|مهام|تذكير)"),
        "propose_task",
    ),
    (re.compile(r"مهمه\s+جديده"), "propose_task"),
    (
        re.compile(r"(?:احجز|حدد|اضف|ضيف|جدول|سجل)\S*\s.{0,40}(?:موعد|اجتماع|مقابله|حدث)"),
        "propose_event",
    ),
    (
        re.compile(r"(?:صرفت|دفعت|انفقت)\s.{0,32}(?:\d|[٠-٩]|جنيه|دولار|ريال|درهم|يورو)"),
        "propose_expense",
    ),
    (
        re.compile(r"(?:سجل|اضف|ضيف)\S*\s.{0,40}(?:مصروف|مصاريف|نفقه|مشتريات)"),
        "propose_expense",
    ),
    (re.compile(r"(?:سجل|اضف|ضيف)\S*\s.{0,40}(?:دخل|راتب|ايراد)"), "propose_income"),
    (
        re.compile(r"(?:سجل|تتبع)\S*\s.{0,40}(?:ماء|مياه|نوم|وزن|سعرات|تمرين|رياضه)"),
        "propose_health_log",
    ),
    (re.compile(r"(?:^|\s)(?:شربت|نمت)(?=\s|$)"), "propose_health_log"),
)

#: The stored-record tools (``tools/records.py``) that serve the same request as each propose
#: tool: a persona granted ``create_task`` but not ``propose_task`` still needs the plan.
_WRITE_EQUIVALENTS: Mapping[str, tuple[str, ...]] = {
    "propose_task": ("create_task",),
    "propose_event": ("create_calendar_event",),
    "propose_expense": ("create_finance_record",),
    "propose_income": ("create_finance_record",),
    "propose_health_log": ("create_health_record",),
}

#: Questions about the person's own records (audit A2): "how much did I spend on food this
#: month?" never reached ``list_finance_records``, because the gate knew only imperatives. Each
#: fires only when its ``list_*`` tool is offered, so a persona without records pays nothing.
_READ_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\bhow much (?:did|have|do) i (?:spend|spent|pay|paid|earn|earned|make|made)\b"
            r"|\bwhat (?:did|have) i (?:spend|spent|pay|paid|buy|bought)\b"
            r"|\b(?:my|show|list)\b.{0,24}\b(?:expenses|spending|transactions|income|earnings)\b",
            re.I,
        ),
        "list_finance_records",
    ),
    (re.compile(r"كم\s+(?:صرفت|دفعت|انفقت|كسبت)|مصاريفي|مصروفاتي|نفقاتي|دخلي"), "list_finance_records"),
    (
        re.compile(
            r"\b(?:show|list|what are|what's|whats|do i have|any)\b.{0,24}"
            r"\b(?:tasks|to-?dos|reminders)\b",
            re.I,
        ),
        "list_tasks",
    ),
    (re.compile(r"مهامي|مهماتي|تذكيراتي|(?:اعرض|ما هي|ماهي|عندي)\s.{0,16}مهام"), "list_tasks"),
    (
        re.compile(
            r"\b(?:on my calendar|my schedule|my meetings|my events|my appointments)\b"
            r"|\bdo i have (?:any )?(?:meetings|events|appointments)\b",
            re.I,
        ),
        "list_calendar_events",
    ),
    (re.compile(r"مواعيدي|اجتماعاتي|جدولي|تقويمي"), "list_calendar_events"),
    (
        re.compile(
            r"\bhow (?:much|long|many hours) did i (?:sleep|drink)\b"
            r"|\bmy (?:health|sleep|weight|water) (?:log|logs|records|history)\b",
            re.I,
        ),
        "list_health_records",
    ),
    (re.compile(r"كم\s+(?:نمت|شربت)"), "list_health_records"),
)


def matching_personal_os_tool(
    message: str, available_tools: Sequence[str]
) -> str | None:
    """Return the propose_* tool a short imperative should run, or ``None``.

    Used by :meth:`PlanningEngine.needs_plan` so "add a task to buy milk" still
    reaches execution after the v1 ``[ACTIONS]`` path was locked. Returns ``None``
    when none of the matching tools are in ``available_tools``.
    """
    names = set(available_tools)
    text = fold_arabic(message.strip())
    if not text:
        return None
    for pattern, candidate in _INTENT_PATTERNS:
        serves = candidate in names or any(
            e in names for e in _WRITE_EQUIVALENTS.get(candidate, ())
        )
        if serves and pattern.search(text):
            return candidate
    return None


def matching_record_tool(message: str, available_tools: Sequence[str]) -> str | None:
    """The ``list_*`` record tool a question about the person's own records needs, or ``None``.

    Gated like :func:`matching_personal_os_tool`: only a tool actually offered can match, so
    ordinary questions on a persona without record tools never pay for a planning generation.
    """
    names = set(available_tools)
    text = fold_arabic(message.strip())
    if not text:
        return None
    for pattern, candidate in _READ_PATTERNS:
        if candidate in names and pattern.search(text):
            return candidate
    return None


_TOOL_FOR_ACTION = {
    "add_task": "propose_task", "add_event": "propose_event", "add_expense": "propose_expense",
    "add_income": "propose_income", "log_health": "propose_health_log",
}


def _clean_text(value: str) -> str:
    """No NUL bytes and no lone surrogates: either one makes the payload unstorable or unencodable
    somewhere between here and the user's device."""
    return value.replace("\x00", "").encode("utf-8", "replace").decode("utf-8")


def _payload(action_type: str, params: Mapping[str, Any]) -> str:
    cleaned: dict[str, Any] = {}
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, str):
            value = _clean_text(value)
        elif isinstance(value, float) and not math.isfinite(value):
            # json.dumps writes NaN/Infinity, which is not JSON: the app's decoder would refuse
            # the whole approval card.
            raise ToolExecutionError(f"{key} must be a finite number", tool=_TOOL_FOR_ACTION[action_type])
        cleaned[key] = value
    return json.dumps({"type": action_type, "params": cleaned}, ensure_ascii=False)


def parse_iso(value: str, field: str, tool_name: str) -> datetime:
    """``value`` as a datetime, or a :class:`ToolExecutionError` the model can correct itself from.

    A proposal carries its dates as the string the model wrote, and the app's executor parses it
    only after the user taps Approve -- a typo should fail here, where the model can retry, not there.
    """
    text = value.strip() if isinstance(value, str) else ""
    if text[-1:] in ("Z", "z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        raise ToolExecutionError(
            f"{field} must be an ISO 8601 date or datetime such as '2026-09-01T14:30:00'; "
            f"got {str(value)[:60]!r}", tool=tool_name,
        ) from None


def require_title(value: str, tool_name: str) -> str:
    title = value.replace("\x00", "").strip() if isinstance(value, str) else ""
    if not title:
        raise ToolExecutionError("title must not be empty", tool=tool_name)
    return title


def _later(start: datetime, end: datetime) -> bool:
    """Whether ``end`` is not before ``start`` (comparing a naive and an aware one is not possible,
    and not an error worth raising: they are then taken as unordered)."""
    try:
        return end >= start
    except TypeError:
        return True


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
        title = require_title(title, "propose_task")
        if dueDate:
            parse_iso(dueDate, "dueDate", "propose_task")
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
        title = require_title(title, "propose_event")
        start = parse_iso(startTime, "startTime", "propose_event")
        if endTime and not _later(start, parse_iso(endTime, "endTime", "propose_event")):
            raise ToolExecutionError("endTime must not be before startTime", tool="propose_event")
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
        if all(v is None for v in (waterIntakeMl, sleepMinutes, caloriesConsumed, exerciseMinutes, weight)):
            raise ToolExecutionError(
                "give at least one metric (waterIntakeMl, sleepMinutes, caloriesConsumed, "
                "exerciseMinutes or weight)", tool="propose_health_log",
            )
        return _payload("log_health", {
            "waterIntakeMl": waterIntakeMl,
            "sleepMinutes": sleepMinutes,
            "caloriesConsumed": caloriesConsumed,
            "exerciseMinutes": exerciseMinutes,
            "weight": weight,
        })

    return [propose_task, propose_event, propose_expense, propose_income, propose_health_log]
