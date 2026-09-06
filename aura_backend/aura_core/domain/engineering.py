"""Server-authored engineer-agent run records.

Like ``domain/personal_os.py``'s ``PersonalOsRecord``, this is deliberately *not* one of the
zero-knowledge-encrypted legacy tables (``tasks``/``calendar_events``/...): the server itself is
the one that learns a PR's URL, a commit's SHA, or a sandbox command's stdout -- there is no
client key to encrypt with, so a plaintext, server-readable row is the honest shape, not a
weakening of the client-encrypted tables' guarantee.

Unlike ``PersonalOsRecord`` (as of this writing, not yet synced to the client -- see that
module's docstring), an :class:`EngineerRun` *is* synced: it is the only durable record of what
an engineer-agent tool call did once its sandbox container or GitHub-side state stops being
directly queryable, so a client history view needs it delivered via the same pull-only sync path
covered in ``infrastructure/db/models.py``'s ``SYNCABLE_MODELS``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .identifiers import EngineerRunId, UserId

__all__ = ["EngineerRunStatus", "EngineerRun"]


class EngineerRunStatus:
    """Plain string constants, not an enum -- this value is stored verbatim in a synced column
    and read back by the Flutter client, which has no shared enum type with the backend."""

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class EngineerRun:
    """One engineer-agent tool call's outcome: which tool ran, for which turn, and what it did.

    ``pr_url``/``branch``/``commit_sha`` are populated only for the GitHub write tools
    (``tools/coding.py::build_github_tools``); ``sandbox_transcript`` only for the sandbox tools
    (command output or a diff body). A row is written once per tool call, not updated in place --
    a call that first appears ``pending`` (awaiting confirmation) and later actually runs becomes
    a second row with the same ``call_id`` and a terminal status, so the pending attempt is never
    silently overwritten and a history view can show both if useful.
    """

    id: EngineerRunId
    user_id: UserId
    turn_id: str
    correlation_id: str
    persona: str
    tool: str
    call_id: str
    status: str
    created_at: datetime
    updated_at: datetime
    pr_url: str | None = None
    branch: str | None = None
    commit_sha: str | None = None
    sandbox_transcript: str | None = None
