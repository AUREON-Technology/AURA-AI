"""Derive candidate lessons from an Engineer turn (ADR-038, inventory row 60).

What a lesson is: one short sentence an Engineer would want in front of it next time -- *a decision
that was made*, *a convention the user stated*, *a step that failed and what it failed on*. What
this module will and will not read:

* **Reads** the user's own message, the assistant's answer, the turn's checkpoint (when a session
  has one), and the *shape* of the plan -- tool names, step descriptions, statuses, error kinds.
* **Never reads** tool output, retrieved documents or step arguments. Those carry third-party text,
  and a lesson that quoted them would be a way to write an attacker's sentence into the Engineer's
  long-term memory. A turn whose context was tainted is not mined at all
  (:attr:`~aura_core.domain.lessons.LessonTurn.tainted`).

Extraction is by keyword and plan structure, no model: it runs after every Engineer turn when
enabled, on a path that must never cost one. It is deliberately modest -- a handful of short,
templated sentences -- because the person reviewing them (``/api/v3/memory/lessons``) has to be
able to say yes or no to each in a glance. Stdlib and domain only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..domain.lessons import LessonTurn
from ..memory.checkpoint import extract_checkpoint
from ..domain.messages import Message

__all__ = ["LessonCandidate", "extract_lessons", "normalise_lesson"]

MAX_LESSONS_PER_TURN = 5
MIN_CHARS = 20
MAX_CHARS = 300

# What a user says when they are stating a rule rather than asking for something.
_CONVENTION = re.compile(
    r"\b(?:always|never|we (?:always |never )?(?:use|prefer|follow|avoid|keep)|our convention|"
    r"the convention is|by convention|make sure (?:to|you)|do not|don't|must (?:not )?be|"
    r"has to be|is required to|should always|should never)\b"
    r"|(?:دايما|دائما|ابدا|أبدا|اتفقنا ان|لازم دايما|ممنوع)",
    re.I,
)
_SENTENCE = re.compile(r"(?<=[.!?؟\n])\s+")


@dataclass(frozen=True, slots=True)
class LessonCandidate:
    #: ``decision`` | ``convention`` | ``failure`` | ``correction``
    kind: str
    text: str


def normalise_lesson(text: str) -> str:
    """The form two lessons are compared in: lower-cased, punctuation-free, single-spaced."""
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


def _clean(text: str) -> str:
    text = " ".join(str(text).replace("\x00", " ").split()).strip(" -*•·")
    return text if len(text) <= MAX_CHARS else text[: MAX_CHARS - 3].rstrip() + "..."


def extract_lessons(
    turn: LessonTurn, checkpoint: Any = None, *, limit: int = MAX_LESSONS_PER_TURN
) -> list[LessonCandidate]:
    """Candidate lessons, most valuable first, de-duplicated, at most ``limit``.

    Order of value: a failure the turn ran into, a correction that fixed one, a stated convention,
    a decision. A tainted turn yields nothing.
    """
    if turn.tainted:
        return []
    found: list[LessonCandidate] = []

    # 1. The plan's own record of what failed, and what was retried into working. A step from an
    #    earlier pass of the agent loop has no tool name (only the replanned goal remembers it).
    failed = [s for s in turn.steps if s.status == "failed"]
    succeeded_tools = {s.tool for s in turn.steps if s.status == "succeeded"}
    for step in failed:
        reason = step.error_kind or "an error"
        who = step.tool or "a step"
        fixed = turn.revisions > 0 and (step.tool in succeeded_tools if step.tool else bool(succeeded_tools))
        if fixed:
            found.append(LessonCandidate(
                "correction",
                f"When {who} failed with {reason} ({step.description}), re-planning got it through.",
            ))
        else:
            found.append(LessonCandidate(
                "failure", f"{who} failed with {reason} while trying to: {step.description}.",
            ))
    for step in turn.steps:
        if step.status == "succeeded" and step.attempts > 1:
            found.append(LessonCandidate(
                "correction",
                f"{step.tool} needed {step.attempts} attempts to: {step.description}.",
            ))

    # 2. Conventions the user stated (their own words only).
    for sentence in _SENTENCE.split(turn.message or ""):
        s = _clean(sentence)
        if MIN_CHARS <= len(s) and not s.endswith(("?", "؟")) and _CONVENTION.search(s):
            found.append(LessonCandidate("convention", s))

    # 3. Decisions: the session's saved checkpoint, plus whatever this exchange itself settled.
    decisions: list[str] = list(getattr(checkpoint, "decisions", ()) or ())
    fresh = extract_checkpoint(
        [Message.user(turn.message or ""), Message.assistant(turn.answer or "")]
    )
    decisions.extend(fresh.decisions)
    for decision in decisions:
        d = _clean(decision)
        if len(d) >= MIN_CHARS:
            found.append(LessonCandidate("decision", f"Decision: {d}"))

    out: list[LessonCandidate] = []
    seen: set[str] = set()
    for candidate in found:
        text = _clean(candidate.text)
        key = normalise_lesson(text)
        if len(text) < MIN_CHARS or key in seen:
            continue
        seen.add(key)
        out.append(LessonCandidate(candidate.kind, text))
        if len(out) >= limit:
            break
    return out
