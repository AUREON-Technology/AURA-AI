import json
import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

#: Output cap for both analyses. Sized to the deployed CPU model (CLAUDE.md: 5-10 tok/s), so a
#: worst-case generation finishes well inside the router's request timeout instead of being
#: cancelled mid-JSON: 400 tokens is ~80 s at 5 tok/s against a 180 s deadline. Both answers are
#: a handful of short fields; the email schema's longest realistic reply is ~250 tokens.
ANALYSIS_MAX_OUTPUT_TOKENS = 400


class AnalysisUnavailable(Exception):
    """The model could not produce a usable analysis. The route turns this into a 503.

    It used to return a placeholder dictionary with HTTP 200, so the client rendered "Email
    analysis unavailable" as if it were the analysis, and nothing counted the failure."""


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
        """Analyzes email subject and body, returning structured JSON."""
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
        return await self._generate(
            router,
            prompt,
            "You extract structured metadata from email. Reply with JSON only, matching "
            "the keys the prompt describes.",
        )

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
        return await self._generate(
            router,
            prompt,
            "You break issues into subtasks and estimate complexity. Reply with JSON "
            "only, matching the keys the prompt describes.",
        )


automation_service = AutomationService()
