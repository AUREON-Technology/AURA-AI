"""Session checkpoint tools: ``save_session_checkpoint`` and ``recall_session_checkpoint``.

``memory/checkpoint.py`` can write a session's decisions, completed work, blockers and next steps
down, and ``EpisodicMemory.checkpoint()`` stores it -- but until these tools and
``/api/v3/sessions/{id}/checkpoint`` existed nothing called it (inventory row 26). The two tools
let a persona write or read the hand-over note for the conversation it is in, so a session picked
up days later starts from "what is decided, done, stuck and next" instead of re-deriving it from a
rolling summary.

Both are owner-scoped twice: ``EpisodicMemory`` namespaces every checkpoint key by the owner's id,
and the tools only ever pass ``context.user_id``. A session id is therefore never a capability --
naming someone else's session id reads an empty slot of *your own* namespace.
"""

from __future__ import annotations

from typing import Any

from ..domain.errors import ToolExecutionError
from ..domain.identifiers import SessionId
from ..domain.tools import ToolCategory, ToolSensitivity
from ..ports.runtime import Tool, ToolContext
from .base import param, tool

__all__ = ["build_session_tools", "SESSION_TOOL_NAMES"]

SESSION_TOOL_NAMES = ("save_session_checkpoint", "recall_session_checkpoint")


def _session(context: ToolContext, explicit: str | None, *, tool_name: str) -> SessionId:
    raw = (explicit or "").strip() or context.session_id
    if not raw:
        raise ToolExecutionError(
            "this turn is not part of a saved conversation (no session id), so there is no "
            "session to checkpoint",
            tool=tool_name,
        )
    try:
        return SessionId(raw)
    except ValueError as exc:
        raise ToolExecutionError("session_id is not a valid identifier", tool=tool_name) from exc


def build_session_tools(episodic: Any) -> list[Tool]:
    """The two checkpoint tools over an ``EpisodicMemory``."""

    @tool(
        "save_session_checkpoint",
        "Write down where this conversation stands -- what was decided, what is done, what is "
        "blocked and what comes next -- so it can be picked up later. Use at the end of a long "
        "piece of work or before the user leaves. Replaces the previous checkpoint of this "
        "conversation.",
        category=ToolCategory.MEMORY,
        sensitivity=ToolSensitivity.PERSONAL,
        # Writes durable state about the user's conversation, like `remember_fact`: in a turn
        # whose context was quarantined it waits for confirmation instead of running.
        side_effecting=True,
        timeout_s=90.0,
    )
    async def save_session_checkpoint(context: ToolContext) -> str:
        session = _session(context, None, tool_name="save_session_checkpoint")
        checkpoint = await episodic.checkpoint(session, context.user_id, save=True)
        if checkpoint.covers_messages == 0 and checkpoint.is_empty():
            return "Nothing to checkpoint: this conversation has no saved messages yet."
        return "Checkpoint saved.\n" + checkpoint.as_text()

    @tool(
        "recall_session_checkpoint",
        "Read the last saved checkpoint (decisions, completed work, blockers, next steps) of this "
        "conversation, or of another of the user's conversations by its session id. Use when "
        "resuming work to see where it stopped.",
        param(
            "session_id", "string",
            "Another conversation's session id. Omit for the current conversation.",
            required=False, max_length=128,
        ),
        category=ToolCategory.MEMORY,
        sensitivity=ToolSensitivity.PERSONAL,
        timeout_s=10.0,
    )
    async def recall_session_checkpoint(context: ToolContext, session_id: str | None = None) -> str:
        session = _session(context, session_id, tool_name="recall_session_checkpoint")
        checkpoint = await episodic.latest_checkpoint(session, context.user_id)
        if checkpoint is None:
            return "No checkpoint has been saved for this conversation yet."
        return checkpoint.as_text()

    return [save_session_checkpoint, recall_session_checkpoint]
