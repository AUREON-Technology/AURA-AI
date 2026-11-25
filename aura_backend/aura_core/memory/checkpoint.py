"""Structured session checkpoints: decisions, completed work, blockers and next steps.

The rolling conversation summary (``memory/session.py``) is prose: it preserves facts, but "what did
we decide, what is done, what is stuck, what comes next" has to be re-derived from it every time a
session is picked up again. A checkpoint is that answer, written down once, in four lists.

Two ways to produce one, both returning the same :class:`SessionCheckpoint`:

* **Model-written** -- a deterministic (temperature 0) generation asked for JSON. Its output is
  parsed defensively: code fences, prose around the JSON, a field that is a string instead of a
  list, over-long items and non-string items are all tolerated, and anything unparseable falls
  through to the extractive path rather than failing.
* **Extractive** -- no model: sentences that read as a decision, a completed item, a blocker or a
  next step are picked by keyword (English and Arabic). Crude by design, and always marked
  ``source="extractive"`` so it is never mistaken for a model's judgement.

Stdlib only; the model call is injected, as the summariser's is.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Sequence

__all__ = [
    "SessionCheckpoint",
    "parse_checkpoint_json",
    "extract_checkpoint",
    "build_session_checkpointer",
    "CHECKPOINT_PROMPT",
]

#: Items kept per list and characters per item. A checkpoint is read at the start of a session, so
#: it has to stay short enough to be worth reading.
MAX_ITEMS = 12
MAX_ITEM_CHARS = 300

CHECKPOINT_PROMPT = """You are writing a hand-over note for a work session so it can be continued later.
Read the conversation (and the earlier summary, if any) and answer with ONE JSON object and nothing else:

{{"decisions": [...], "completed": [...], "blockers": [...], "next_steps": [...]}}

- "decisions": choices that were made and the reason in a few words.
- "completed": things that are finished (done, merged, fixed, delivered).
- "blockers": things that are stuck, failing, unknown or waiting on someone.
- "next_steps": concrete actions still to do, most important first.

Use short plain sentences in the language of the conversation. Only include what the conversation
actually says -- never invent. Use an empty list when there is nothing for a field.

Earlier summary:
{previous_summary}

Conversation:
{conversation}
"""


@dataclass(frozen=True, slots=True)
class SessionCheckpoint:
    decisions: tuple[str, ...] = ()
    completed: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    next_steps: tuple[str, ...] = ()
    #: ``"model"`` or ``"extractive"``: how the lists were produced.
    source: str = "extractive"
    #: How many messages of the session the checkpoint was written from.
    covers_messages: int = 0
    created_at: float = 0.0

    def is_empty(self) -> bool:
        return not (self.decisions or self.completed or self.blockers or self.next_steps)

    def as_dict(self) -> dict[str, Any]:
        return {
            "decisions": list(self.decisions),
            "completed": list(self.completed),
            "blockers": list(self.blockers),
            "next_steps": list(self.next_steps),
            "source": self.source,
            "covers_messages": self.covers_messages,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SessionCheckpoint":
        def items(key: str) -> tuple[str, ...]:
            return _clean_items(data.get(key))

        try:
            covers = int(data.get("covers_messages", 0))
            created = float(data.get("created_at", 0.0))
        except (TypeError, ValueError):
            covers, created = 0, 0.0
        source = data.get("source")
        return cls(
            decisions=items("decisions"),
            completed=items("completed"),
            blockers=items("blockers"),
            next_steps=items("next_steps"),
            source=source if source in ("model", "extractive") else "extractive",
            covers_messages=covers,
            created_at=created,
        )

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False)

    def as_text(self) -> str:
        """A short Markdown block for putting in front of a model or a person."""
        if self.is_empty():
            return "No checkpoint content: nothing decided, finished, blocked or planned was found."
        sections = (
            ("Decisions", self.decisions),
            ("Completed", self.completed),
            ("Blockers", self.blockers),
            ("Next steps", self.next_steps),
        )
        lines = [f"## Session checkpoint ({self.source}, {self.covers_messages} messages)"]
        for title, entries in sections:
            if entries:
                lines.append(f"\n### {title}")
                lines.extend(f"- {e}" for e in entries)
        return "\n".join(lines)


def _clean_items(value: Any) -> tuple[str, ...]:
    """A list of short non-empty strings, whatever shape ``value`` arrived in."""
    if value is None:
        return ()
    if isinstance(value, str):
        value = [part for part in re.split(r"\n+|(?<=[.!?؟])\s+", value) if part.strip()]
    if not isinstance(value, (list, tuple)):
        return ()
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        if isinstance(item, Mapping):  # a model sometimes returns {"text": ...} or {"step": ...}
            item = next((v for v in item.values() if isinstance(v, str)), "")
        if not isinstance(item, str):
            continue
        text = " ".join(item.replace("\x00", " ").split()).lstrip("-*•· ").strip()
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        out.append(text[:MAX_ITEM_CHARS])
        if len(out) >= MAX_ITEMS:
            break
    return tuple(out)


def parse_checkpoint_json(raw: str, *, covers_messages: int = 0, now: float | None = None) -> SessionCheckpoint | None:
    """Parse a model's reply into a checkpoint, or ``None`` when no usable JSON object is in it."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S | re.I)
    if fence:
        text = fence.group(1).strip()
    candidates = [text]
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (ValueError, RecursionError):
            continue
        if isinstance(data, Mapping) and any(k in data for k in ("decisions", "completed", "blockers", "next_steps")):
            return SessionCheckpoint(
                decisions=_clean_items(data.get("decisions")),
                completed=_clean_items(data.get("completed")),
                blockers=_clean_items(data.get("blockers")),
                next_steps=_clean_items(data.get("next_steps")),
                source="model",
                covers_messages=covers_messages,
                created_at=time.time() if now is None else now,
            )
    return None


_DECISION = re.compile(
    r"\b(?:we(?:'ll| will| decided| agreed| chose)|decided|agreed|chose|going with|let'?s (?:use|go with)|will use)\b"
    r"|(?:قررنا|قررت|اتفقنا|هنستخدم|هنعتمد|اخترنا|الاتفاق)",
    re.I,
)
_DONE = re.compile(
    r"\b(?:done|completed|finished|fixed|merged|deployed|implemented|added|resolved|passes|passed)\b"
    r"|(?:تم|خلصت|خلصنا|اتصلح|اتنفذ|اكتمل|انتهينا|اشتغل|عدّت|عدت)",
    re.I,
)
_BLOCK = re.compile(
    r"\b(?:blocked|blocker|stuck|failing|fails|failed|error|cannot|can't|unable|waiting (?:on|for)|unknown|broken|not working)\b"
    r"|(?:مشكلة|معطل|عالق|فشل|بيفشل|مش شغال|خطأ|مستني|مش قادر|ناقص)",
    re.I,
)
_NEXT = re.compile(
    r"\b(?:next|todo|to do|still (?:need|have)|remaining|need to|needs to|should|must|will (?:need|do)|follow[- ]?up)\b"
    r"|(?:الخطوة الجاية|الخطوة التالية|لسه|محتاج|لازم|هنعمل|هنكمل|المتبقي|بعد كده)",
    re.I,
)
_SENTENCE = re.compile(r"(?<=[.!?؟\n])\s+")


def extract_checkpoint(
    messages: Sequence[Any], previous_summary: str = "", *, now: float | None = None
) -> SessionCheckpoint:
    """A checkpoint picked out of the conversation by keyword. No model, no network.

    Order of precedence per sentence: blocker, then next step, then decision, then completed. A
    sentence lands in at most one list. Questions are never picked: "should we use Redis?" is not a
    decision. Later sentences win ties by being kept when a list overflows.
    """
    lists: dict[str, list[str]] = {"decisions": [], "completed": [], "blockers": [], "next_steps": []}
    texts = [previous_summary or ""]
    texts.extend(str(getattr(m, "content", m) or "") for m in messages)
    for block in texts:
        for sentence in _SENTENCE.split(block):
            s = " ".join(sentence.split())
            if len(s) < 8 or s.endswith(("?", "؟")):
                continue
            if _BLOCK.search(s):
                lists["blockers"].append(s)
            elif _NEXT.search(s):
                lists["next_steps"].append(s)
            elif _DECISION.search(s):
                lists["decisions"].append(s)
            elif _DONE.search(s):
                lists["completed"].append(s)

    def tail(entries: list[str]) -> tuple[str, ...]:
        return _clean_items(list(reversed(entries[-MAX_ITEMS * 2 :])))[:MAX_ITEMS][::-1] if entries else ()

    return SessionCheckpoint(
        decisions=tail(lists["decisions"]),
        completed=tail(lists["completed"]),
        blockers=tail(lists["blockers"]),
        next_steps=tail(lists["next_steps"]),
        source="extractive",
        covers_messages=len(messages),
        created_at=time.time() if now is None else now,
    )


def build_session_checkpointer(router: Any, *, model_policy: Any = None) -> Callable[[str, str], Awaitable[str]]:
    """A closure ``(transcript, previous_summary) -> raw model text`` backed by the LLM router.

    Temperature 0: a checkpoint is a record, and sampling would make it depend on when it was
    written. The reply is bounded so it finishes inside the provider timeout on a CPU-hosted model.
    """
    from ..domain.llm import CompletionRequest, SamplingParams
    from ..domain.messages import Conversation as _Conversation, Message as _Message

    async def checkpoint(transcript: str, previous: str) -> str:
        prompt = CHECKPOINT_PROMPT.format(
            conversation=transcript[-12_000:], previous_summary=(previous or "(none)")[-2_000:]
        )
        request = CompletionRequest(
            conversation=_Conversation.of([_Message.user(prompt)]),
            sampling=SamplingParams(temperature=0.0, max_output_tokens=400),
            policy=model_policy,
        )
        response = await router.complete(request)
        return response.text.strip()

    return checkpoint
