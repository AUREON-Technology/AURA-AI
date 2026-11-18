import json
import logging
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

logger = logging.getLogger(__name__)

#: Output cap for both analyses. Sized to the deployed CPU model (CLAUDE.md: 5-10 tok/s), so a
#: worst-case generation finishes well inside the router's request timeout instead of being
#: cancelled mid-JSON: 400 tokens is ~80 s at 5 tok/s against a 180 s deadline. Both answers are
#: a handful of short fields; the email schema's longest realistic reply is ~250 tokens.
ANALYSIS_MAX_OUTPUT_TOKENS = 400


class AnalysisUnavailable(Exception):
    """The model could not produce a usable analysis. The route turns this into a 503.

    It used to return a placeholder dictionary with HTTP 200, so the client rendered "Email
    analysis unavailable" as if it were the analysis, and nothing counted the failure.

    ``str(exc)`` is the reason slug; ``message`` and ``retryable`` are set when the reason needs
    explaining to the person (``no_local_provider``)."""

    def __init__(self, reason: str, *, message: str = "", retryable: bool = True) -> None:
        super().__init__(reason)
        self.message = message
        self.retryable = retryable


def _parse_json_object(text: str) -> dict | None:
    """The first JSON object in ``text``. Tolerates a code fence or prose around it, which a
    prompt-led model (no JSON mode) produces regularly."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        value = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


# -- validated output (INT-018) ---------------------------------------------------------------
#
# The model's JSON used to reach the client verbatim, and the client hard-casts it: a "true"
# string where a bool belongs, a list of strings where objects belong, or a numeric date was a
# TypeError on the device. These models are the contract instead. Optional fields that come back
# malformed are coerced or dropped to their documented default -- a bad urgency should not cost
# the user the whole analysis -- but an answer without a string ``summary`` is not an analysis at
# all, and becomes ``AnalysisUnavailable("invalid_output")`` (a 503 the client can retry).

EMAIL_CATEGORIES = ("Academic", "Tasks", "Finance", "Health", "Social", "Spam", "General")
_LEVELS = ("low", "medium", "high")


def _optional_text(value: Any) -> str | None:
    if value is None or isinstance(value, (dict, list, bool)):
        return None
    text = str(value).strip()
    return text or None


def _level(value: Any, default: str) -> str:
    text = value.strip().lower() if isinstance(value, str) else ""
    return text if text in _LEVELS else default


def _iso_date(value: Any) -> str | None:
    """``YYYY-MM-DD`` or None. Numbers are refused: 20261120 or an epoch is a guess, not a date."""
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()[:10]).isoformat()
    except ValueError:
        return None


def _iso_datetime(value: Any) -> str | None:
    """An ISO local datetime string, as the prompt asks for, or None."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return value.strip()


class _Output(BaseModel):
    # Unknown keys are dropped rather than passed on: the response is the contract, not whatever
    # else the model chose to add.
    model_config = ConfigDict(extra="ignore")

    @field_validator("summary", mode="before", check_fields=False)
    @classmethod
    def _summary_is_text(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("summary must be a string")
        return value.strip()


class EmailActionItem(BaseModel):
    title: str
    due_date: str | None = None


class EmailAnalysis(_Output):
    category: Literal[EMAIL_CATEGORIES] = "General"  # type: ignore[valid-type]
    summary: str
    urgency: Literal["low", "medium", "high"] = "low"
    action_items: list[EmailActionItem] = []
    should_create_event: bool = False
    event_title: str | None = None
    event_start: str | None = None
    event_end: str | None = None

    @field_validator("category", mode="before")
    @classmethod
    def _category(cls, value: Any) -> str:
        text = value.strip().lower() if isinstance(value, str) else ""
        return next((c for c in EMAIL_CATEGORIES if c.lower() == text), "General")

    @field_validator("urgency", mode="before")
    @classmethod
    def _urgency(cls, value: Any) -> str:
        return _level(value, "low")

    @field_validator("action_items", mode="before")
    @classmethod
    def _action_items(cls, value: Any) -> list[dict]:
        if not isinstance(value, list):
            return []
        items: list[dict] = []
        for item in value:
            if isinstance(item, str):
                title, due = _optional_text(item), None
            elif isinstance(item, dict):
                title, due = _optional_text(item.get("title")), _iso_date(item.get("due_date"))
            else:
                continue
            if title:
                items.append({"title": title, "due_date": due})
        return items

    @field_validator("should_create_event", mode="before")
    @classmethod
    def _flag(cls, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("true", "yes", "1")
        return False

    @field_validator("event_title", mode="before")
    @classmethod
    def _event_title(cls, value: Any) -> str | None:
        return _optional_text(value)

    @field_validator("event_start", "event_end", mode="before")
    @classmethod
    def _event_time(cls, value: Any) -> str | None:
        return _iso_datetime(value)


class IssueAnalysis(_Output):
    summary: str
    subtasks: list[str] = []
    complexity: Literal["low", "medium", "high"] = "medium"
    suggested_priority: Literal["low", "medium", "high"] = "medium"

    @field_validator("subtasks", mode="before")
    @classmethod
    def _subtasks(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return []
        return [t for t in (_optional_text(v) for v in value) if t]

    @field_validator("complexity", "suggested_priority", mode="before")
    @classmethod
    def _levels(cls, value: Any) -> str:
        return _level(value, "medium")


def _validated(model: type[_Output], data: dict) -> dict:
    try:
        return model.model_validate(data).model_dump()
    except ValidationError as exc:
        # Field names only: the values are model output derived from user data.
        logger.warning(
            "analysis output failed validation: %s",
            sorted({".".join(str(p) for p in e["loc"]) for e in exc.errors()}),
        )
        raise AnalysisUnavailable("invalid_output") from exc


class AutomationService:
    """Structured email and issue analysis through the gateway's ``ProviderRouter`` (F-23).

    This used to call ``ollama_service.generate_json`` -- a synchronous ``httpx.Client`` post
    with a 60 s timeout -- straight from ``async def`` route handlers. One analysis froze that
    uvicorn worker's event loop, live SSE chat streams included, for up to a minute. It also
    bypassed the router entirely: no admission control, no circuit breakers, no metrics, and no
    sensitivity routing. Through the router the call is awaited, admitted like any other
    generation, and -- being unclassified, hence SENSITIVE (``llm/sensitivity.py``) -- can only
    go to a local provider, which is what "running entirely on our own inference host" meant.
    """

    async def _generate(self, router: Any, prompt: str, system_instruction: str) -> dict:
        from aura_core.domain import CompletionRequest, Conversation, Message, SamplingParams
        from aura_core.domain.errors import CapabilityUnavailableError
        from services.budget import increment_and_check_inference_budget

        # The budget check stays: it guards model concurrency, the scarcest resource on the box.
        increment_and_check_inference_budget()
        try:
            response = await router.complete(
                CompletionRequest(
                    conversation=Conversation.of(
                        [Message.system(system_instruction), Message.user(prompt)]
                    ),
                    sampling=SamplingParams(
                        temperature=0.0, max_output_tokens=ANALYSIS_MAX_OUTPUT_TOKENS
                    ),
                )
            )
        except CapabilityUnavailableError as exc:
            # INT-115: the email or issue is third-party text, as sensitive as a retrieved
            # document, so it stays unclassified (SENSITIVE) and only a self-hosted provider may
            # read it. A deployment with only hosted providers refuses it for content; retrying
            # sends the same text to the same refusal, so say what is missing instead.
            logger.warning("analysis refused: no provider may receive this content")
            raise AnalysisUnavailable(
                "no_local_provider",
                message="This analysis needs a self-hosted model, and none is available on this "
                        "server right now.",
                retryable=False,
            ) from exc
        except Exception as exc:
            # Log the type only: the message can carry provider detail, and the prompt is user
            # data either way.
            logger.warning("analysis generation failed: %s", type(exc).__name__)
            raise AnalysisUnavailable("generation_failed") from exc
        data = _parse_json_object(response.text or "")
        if data is None:
            logger.warning(
                "analysis returned unparseable output (%d chars)", len(response.text or "")
            )
            raise AnalysisUnavailable("unparseable_output")
        return data

    async def analyze_email(self, router: Any, subject: str, sender: str, body: str) -> dict:
        """Analyzes email subject and body, returning an :class:`EmailAnalysis` as a dict."""
        prompt = f"""
        Analyze the following email and extract structured metadata and action items.
        Sender: {sender}
        Subject: {subject}
        Body:
        {body}

        Today's Date: {datetime.now().strftime('%Y-%m-%d %A')}

        Respond ONLY with a JSON object containing the following keys:
        - "category": String. Must be one of: "Academic", "Tasks", "Finance", "Health", "Social", "Spam", "General".
        - "summary": String. A brief 1-2 sentence summary of the email.
        - "urgency": String. "high", "medium", or "low".
        - "action_items": List of objects. Each object should have:
            - "title": String. The specific task title.
            - "due_date": String or null. Format YYYY-MM-DD if a deadline is mentioned in the text (infer relative to Today's Date).
        - "should_create_event": Boolean. True if the email specifies a specific meeting, calendar appointment, class, or deadline event.
        - "event_title": String or null. Title for calendar event.
        - "event_start": String or null. Start datetime in format YYYY-MM-DDTHH:MM:SS (local time).
        - "event_end": String or null. End datetime in format YYYY-MM-DDTHH:MM:SS (local time).
        """
        data = await self._generate(
            router,
            prompt,
            "You extract structured metadata from email. Reply with JSON only, matching "
            "the keys the prompt describes.",
        )
        return _validated(EmailAnalysis, data)

    async def analyze_github_issue(self, router: Any, title: str, body: str) -> dict:
        """Analyzes a GitHub issue and extracts checkable subtask steps and metadata."""
        prompt = f"""
        Analyze the following GitHub issue and extract actionable coding subtasks and metadata.
        Issue Title: {title}
        Issue Body:
        {body}

        Respond ONLY with a JSON object containing the following keys:
        - "summary": String. A brief description of the issue.
        - "subtasks": List of strings. Actionable coding checklist items required to resolve this issue.
        - "complexity": String. "low", "medium", or "high".
        - "suggested_priority": String. "low", "medium", or "high".
        """
        data = await self._generate(
            router,
            prompt,
            "You break issues into subtasks and estimate complexity. Reply with JSON "
            "only, matching the keys the prompt describes.",
        )
        return _validated(IssueAnalysis, data)


automation_service = AutomationService()
