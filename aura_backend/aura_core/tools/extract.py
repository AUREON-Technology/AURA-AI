"""``extract_structured``: typed fields from text the person supplied (ADR-038).

The tool is a thin, careful shell around a :class:`~aura_core.ports.extraction.StructuredExtractor`.
It decides *whether* a call may happen; the extractor decides how it is made. In order:

1. **Validate** the arguments into an :class:`~aura_core.domain.extraction.ExtractionRequest`. A
   malformed spec is refused before anything else, so a bad call costs nothing and sends nothing.
2. **Data ceiling.** The call sends text to Cloudflare, so it runs only when the turn's data scope
   is ``internal`` or lower (ADR-030 §4). The scope rises when the turn drew on memory, documents,
   pinned documents, goals, an Engineer workspace, caller evidence, images or an earlier sensitive
   session, and the tool registry raises it again the moment *any* tool returns output. So the tool
   works on a turn that holds only the person's own words, and a turn that already ran a tool, or
   that this tool itself already served, is refused. A call with no scope at all counts as
   ``sensitive``, like every unclassified generation. There is no local fallback to offer instead.
3. **Daily cap.** A per-process count of calls, so one busy day cannot spend the Workers AI
   allowance the ``hosted3`` chat slot shares. Per process, not per deployment: the effective cap is
   this times the number of gateway workers.
4. **Call**, then return every answer as the extractor classified it, plus the names of the fields
   that were withheld for low confidence so the model cannot miss them.

Every outcome writes an ``extraction.third_party_call`` audit record: who, which model, how many
fields and characters, what came back by status. Never the text and never the answers.

The output is tagged ``untrusted_content``: it is derived from text an attacker may have written, so
the turn is tainted the way ``fetch_url`` taints it (ADR-006). The tool itself is read-only.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, time, timedelta, timezone
from typing import Any, Mapping

from ..domain.errors import (
    BudgetExceededError,
    ExtractionError,
    RateLimitedError,
    ToolExecutionError,
    ValidationError,
)
from ..domain.extraction import ExtractionRequest, FieldSpec, FieldStatus
from ..domain.llm import DataClassification
from ..domain.tools import ToolCategory, ToolSensitivity
from ..llm.sensitivity import current_data_scope
from ..observability.logging import get_logger
from ..ports.extraction import StructuredExtractor
from ..ports.runtime import AuditEvent, AuditSink, Clock, Tool, ToolContext
from .base import param, tool

__all__ = ["build_extract_tools", "DailyCallCap", "EXTRACT_TOOL_NAME", "EXTRACT_AUDIT_ACTION"]

EXTRACT_TOOL_NAME = "extract_structured"
EXTRACT_AUDIT_ACTION = "extraction.third_party_call"

logger = get_logger("tools.extract")

_DESCRIPTION = (
    "Read text you pass and fill in typed fields from it (for example an invoice number, a due "
    "date, a total, a list of names), returning each answer with a confidence and, where the value "
    "is printed in the text, its position. Fields: text, number, date (YYYY-MM-DD), boolean, enum "
    "(give \"enum\": [...]) and text_list. A field the text does not state comes back null. A value "
    "the service is not confident in is WITHHELD: its status is low_confidence, its value is null "
    "and the raw guess is shown only for the person to check -- never state a guess as a fact, "
    "and say which fields were withheld. The text is sent to an outside service (Cloudflare), so "
    "this works only on text the person typed in this conversation, once per turn, and is refused "
    "once the turn has used memory, documents or another tool."
)


class DailyCallCap:
    """A count of calls per UTC day, per process. ``acquire`` raises once the day's cap is spent.

    Counts every call that passes the earlier checks, including one the extractor then refuses
    from its own cooldown: the two cannot be told apart from here, and over-counting is the safe
    direction for a shared allowance.
    """

    def __init__(self, cap: int, clock: Clock) -> None:
        if cap < 1:
            raise ValueError("the daily cap must be at least 1")
        self._cap = cap
        self._clock = clock
        self._day: Any = None
        self._used = 0

    def _today(self) -> tuple[Any, datetime]:
        now = self._clock.now().astimezone(timezone.utc)
        reset = datetime.combine(now.date() + timedelta(days=1), time.min, tzinfo=timezone.utc)
        return now.date(), reset

    def acquire(self) -> None:
        day, reset = self._today()
        if day != self._day:
            self._day, self._used = day, 0
        if self._used >= self._cap:
            raise BudgetExceededError(
                "today's extraction allowance is spent",
                details={"reason": "daily_cap", "reset_at": reset.isoformat(), "cap": self._cap},
            )
        self._used += 1


def _request(text: Any, fields: Any, max_chars: int) -> ExtractionRequest:
    """Arguments -> a validated request, with errors the model can act on."""
    if not isinstance(text, str):
        raise ToolExecutionError("text must be a string", tool=EXTRACT_TOOL_NAME, retryable=False)
    if len(text) > max_chars:
        raise ToolExecutionError(
            f"text is {len(text)} characters; the limit is {max_chars}", tool=EXTRACT_TOOL_NAME, retryable=False
        )
    if not isinstance(fields, Mapping) or not fields:
        raise ToolExecutionError(
            "fields must be an object mapping each field name to its spec, for example "
            '{"due_date": {"type": "date"}}',
            tool=EXTRACT_TOOL_NAME, retryable=False,
        )
    try:
        specs = tuple(FieldSpec.from_mapping(str(name), raw) for name, raw in fields.items())
        return ExtractionRequest(text=text, fields=specs)
    except ValidationError as exc:
        raise ToolExecutionError(exc.message, tool=EXTRACT_TOOL_NAME, retryable=False) from None


def build_extract_tools(
    *,
    extractor: StructuredExtractor,
    audit: AuditSink,
    clock: Clock,
    min_confidence: float = 0.5,
    max_chars: int = 20_000,
    timeout_s: float = 30.0,
    daily_call_cap: int = 100,
) -> list[Tool]:
    cap = DailyCallCap(daily_call_cap, clock)

    async def record(
        context: ToolContext, outcome: str, reason: str, *, fields: int, chars: int,
        statuses: Mapping[str, int] | None = None,
    ) -> None:
        """Best effort, like the router's record for a hosted generation: a broken audit sink must
        not turn a refusal into a different failure."""
        try:
            await audit.record(
                AuditEvent(
                    action=EXTRACT_AUDIT_ACTION,
                    actor=str(context.user_id) or "system",
                    outcome=outcome,
                    resource=extractor.name,
                    correlation_id=context.correlation_id,
                    detail={
                        "reason": reason,
                        "model": extractor.model_id,
                        "fields": fields,
                        "input_chars": chars,
                        "statuses": dict(statuses) if statuses is not None else None,
                        "data_classification": DataClassification.INTERNAL.value,
                    },
                )
            )
        except Exception as exc:
            logger.warning("extraction audit record failed", extra={"error": type(exc).__name__})

    async def extract_structured(text: str, fields: dict[str, Any], context: ToolContext) -> str:
        request = _request(text, fields, max_chars)
        n_fields, n_chars = len(request.fields), request.character_count

        scope = current_data_scope()
        if scope is None or scope.level.rank > DataClassification.INTERNAL.rank:
            reasons = ", ".join(scope.reasons) if scope is not None and scope.reasons else "an earlier step"
            await record(context, "denied", "data_ceiling", fields=n_fields, chars=n_chars)
            raise ToolExecutionError(
                "extract_structured sends the text to an outside service, so it only runs when this "
                "conversation holds nothing but the person's own words. This turn also used: "
                f"{reasons}. Ask again in a new conversation, or read the values yourself.",
                tool=EXTRACT_TOOL_NAME, retryable=False, details={"reason": "data_ceiling"},
            )

        try:
            cap.acquire()
        except BudgetExceededError as exc:
            await record(context, "denied", "daily_cap", fields=n_fields, chars=n_chars)
            raise ToolExecutionError(
                "today's extraction allowance is spent; it resets at "
                f"{exc.details['reset_at']}",
                tool=EXTRACT_TOOL_NAME, retryable=False, details={"reason": "daily_cap"},
            ) from None

        try:
            result = await extractor.extract(request, min_confidence=min_confidence)
        except RateLimitedError as exc:
            await record(context, "failure", "rate_limited", fields=n_fields, chars=n_chars)
            raise ToolExecutionError(
                f"the extraction service is rate-limited; try again in about {exc.retry_after}s",
                tool=EXTRACT_TOOL_NAME, retryable=True, details={"reason": "rate_limited"},
            ) from None
        except ExtractionError as exc:
            await record(context, "failure", "upstream_error", fields=n_fields, chars=n_chars)
            raise ToolExecutionError(
                "the extraction service failed"
                + (f" (HTTP {exc.details['status']})" if "status" in exc.details else ""),
                tool=EXTRACT_TOOL_NAME, retryable=exc.retryable, details={"reason": "upstream_error"},
            ) from None

        statuses = Counter(f.status.value for f in result.fields.values())
        await record(context, "success", "ok", fields=n_fields, chars=n_chars, statuses=statuses)
        payload = result.as_dict()
        withheld = [name for name, f in result.fields.items() if f.status is FieldStatus.LOW_CONFIDENCE]
        payload["withheld"] = withheld
        if withheld:
            payload["note"] = (
                "The fields in 'withheld' were not answered because the service was not confident. "
                "Their 'guess' is for the person to check, not a fact."
            )
        return json.dumps(payload, ensure_ascii=False)

    return [
        tool(
            EXTRACT_TOOL_NAME,
            _DESCRIPTION,
            param(
                "text", "string",
                "The text to read: only what the person typed in this conversation.",
                max_length=max_chars,
            ),
            param(
                "fields", "object",
                'Map of field name to spec, e.g. {"due_date": {"type": "date", "instructions": '
                '"issue date plus net days"}, "total": {"type": "number", "decimals": 2}, "status": '
                '{"type": "enum", "enum": ["open", "paid"]}}. Types: text, number, date, boolean, '
                "enum, text_list. Keys per spec: type, instructions, enum, integer, decimals, unit. "
                "At most 20 fields.",
            ),
            category=ToolCategory.DATA,
            sensitivity=ToolSensitivity.PERSONAL,
            timeout_s=float(timeout_s) + 5.0,
            tags=("untrusted_content",),
        )(extract_structured)
    ]
