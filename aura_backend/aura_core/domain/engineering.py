"""Server-authored engineer-agent run records.

This is deliberately *not* one of the synced, client-encrypted tables
(``tasks``/``calendar_events``/...): the server itself is the one that learns a PR's URL, a
commit's SHA, or a sandbox command's stdout, so a plaintext, server-readable row is the honest
shape.

An :class:`EngineerRun` *is* synced: it is the only durable record of what
an engineer-agent tool call did once its sandbox container or GitHub-side state stops being
directly queryable, so a client history view needs it delivered via the same pull-only sync path
covered in ``infrastructure/db/models.py``'s ``SYNCABLE_MODELS``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .identifiers import EngineerRunId, UserId, WorkspaceId

__all__ = [
    "EngineerRunStatus", "EngineerRun", "WorkspaceStatus", "AgentWorkspace",
    "ENGINEER_CONSENT_NOTICE_VERSION", "EngineerConsent",
]


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


class WorkspaceStatus:
    """Plain string constants, for the same reason :class:`EngineerRunStatus` is: the value is
    stored verbatim in a column the Flutter client reads back, and the client shares no enum type
    with the backend.

    The three states are a memory story, not bookkeeping. A workspace's real content is the
    checkout on disk, so it does not need a running container between turns:

    * ``HOT``     -- container running. The only state that costs RAM.
    * ``WARM``    -- container stopped, checkout intact. Costs disk only; restarting is fast
      because the container still exists.
    * ``EXPIRED`` -- past its idle TTL and reaped: container and directory both gone. Terminal;
      the row is deleted rather than left in this state, which exists so a reaper mid-pass and a
      caller reading concurrently agree on what is happening.
    """

    HOT = "hot"
    WARM = "warm"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class AgentWorkspace:
    """One durable engineer-agent workspace: a real git checkout plus the container that works
    on it, scoped to a single ``(user_id, repo)`` pair.

    Scoped per repository rather than per user on purpose. Two repositories a user is working on
    get two containers and two filesystems, so a hostile repository cannot read the other's
    checkout -- isolation by construction rather than by convention. Per *conversation* was the
    other candidate and would re-clone for every new thread about the same repository, which is
    the cost this whole feature exists to remove.

    Unlike :class:`EngineerRun`, this is mutable live state, not an append-only ledger:
    ``last_active_at`` is rewritten on every tool call and is what the TTL measures, so a
    workspace someone is actively using is never reaped out from under them.

    ``container_id`` is nullable because the row and the container are created in that order, and
    because a reaped or externally-removed container leaves a row whose checkout is still on
    disk. Reconciliation (``Container.startup``) treats a row with no live container and a
    container with no row as the same class of problem: state that drifted, to be resolved
    towards the store rather than guessed at.
    """

    id: WorkspaceId
    user_id: UserId
    repo: str
    workspace_path: str
    status: str
    created_at: datetime
    last_active_at: datetime
    container_id: str | None = None
    branch: str | None = None
    #: Which sandbox runner holds the files: ``"docker"`` or ``"railway"``.
    provider: str = "docker"

    def is_expired(self, *, now: datetime, ttl_hours: float) -> bool:
        """True when nothing has touched this workspace for ``ttl_hours``.

        Idle time is measured from ``last_active_at``, never from ``created_at``: the promise is
        "expires after 3 days of inactivity", so a workspace used daily for a month is not stale
        and a workspace created an hour ago and abandoned is on its way out.
        """
        return (now - self.last_active_at).total_seconds() >= ttl_hours * 3600.0


#: The version of the notice a person agrees to before the Engineer sends their code to Anthropic
#: (module readiness D-03, WP1). The client shows the text that belongs to this version and sends
#: it back; changing the notice means a new version, and everyone is asked again.
ENGINEER_CONSENT_NOTICE_VERSION = "2026-11-17"


@dataclass(frozen=True, slots=True)
class EngineerConsent:
    """One person's answer to the Engineer's Anthropic notice (D-03).

    Server-held on purpose: a client-side flag would let any client (or a stale install) send
    code to a third party without the person ever having seen the notice. ``withdrawn_at`` set
    means withdrawn; the row is kept so a withdrawal is itself on record.
    """

    user_id: UserId
    notice_version: str
    accepted_at: datetime
    withdrawn_at: datetime | None = None

    def is_current(self, version: str = ENGINEER_CONSENT_NOTICE_VERSION) -> bool:
        return self.withdrawn_at is None and self.notice_version == version
