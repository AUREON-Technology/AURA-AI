"""Automatic lessons from Engineer turns (ADR-040, inventory row 60): the shapes only.

After an Engineer turn the kernel hands a :class:`LessonTurn` -- a plain record of what was asked,
what was answered and which tool steps ran or failed -- to a sink; ``learning/lessons.py`` derives
candidate lessons from it and ``learning/jobs.py`` stores them as memories of source
``auto_lesson``. They are written into a *pending* scope (:func:`pending_scope`) that no persona's
recall reads, and move into the engineer scope only when the owner approves them (ADR-008: the
system proposes, a person promotes). Stdlib only (ADR-001).
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "LESSON_SOURCE",
    "LESSON_SUBJECT",
    "ENGINEER_SCOPE",
    "pending_scope",
    "LessonStep",
    "LessonTurn",
]

#: ``Provenance.source`` of every automatically derived lesson. "Forget everything auto_lesson
#: wrote" is ``MemoryEngine.forget_source(source=LESSON_SOURCE)``.
LESSON_SOURCE = "auto_lesson"
#: The ``MemoryRecord.subject`` the review queries filter on.
LESSON_SUBJECT = "lesson"
#: The Engineer persona's memory scope (``AgentSpec.memory_scope``).
ENGINEER_SCOPE = "engineer"

_PENDING_SUFFIX = ":pending"


def pending_scope(scope: str) -> str:
    """The holding scope for lessons awaiting review. ``readable_scopes()`` of any persona is its
    own scope plus ``shared``, so a record here is read by nobody until it is approved."""
    return f"{scope}{_PENDING_SUFFIX}"


@dataclass(frozen=True, slots=True)
class LessonStep:
    """One tool step of the turn's plan, without its arguments or output -- those are the parts
    that can carry untrusted text, and a lesson never needs them."""

    tool: str
    description: str
    status: str
    error_kind: str | None = None
    attempts: int = 1


@dataclass(frozen=True, slots=True)
class LessonTurn:
    """What an Engineer turn did, for lesson extraction."""

    user_id: object  # a UserId; typed loosely so this module stays free of other domain imports
    agent_id: str
    message: str
    answer: str
    #: ``"success"`` or ``"failure"`` (planning failed, or a tool step failed).
    outcome: str = "success"
    session_id: str | None = None
    turn_id: str | None = None
    steps: tuple[LessonStep, ...] = ()
    revisions: int = 0
    #: A turn whose context held quarantined or untrusted content is never mined: its answer can
    #: carry text an attacker chose.
    tainted: bool = False
