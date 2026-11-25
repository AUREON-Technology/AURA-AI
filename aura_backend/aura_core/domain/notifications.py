"""In-app notifications: a person's inbox of things the system wants them to see.

**A notification is a kind and its parameters, never prose.** The client renders and translates it
(``kind`` plus ``params``), so the server holds no user-visible text, and a notification can
never carry a person's own content: only identifiers, counts, plan names and dates that the server
already knows. In particular no credential travels in one -- an approval notification names the tool
and the conversation, not the ``approval_id`` that would release the call (read ``GET /approvals``
for that).

A notification may carry a ``dedupe_key``: the same key for the same person is stored once, so an
event that is reported twice (a retried billing cycle, a turn held twice) is one notification.

Stdlib only (ADR-001).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from .errors import ValidationError
from .identifiers import UserId

__all__ = [
    "KINDS", "MAX_PARAMS_BYTES", "NotificationKind", "Notification", "NotificationSettings",
]


class NotificationKind:
    #: A side-effecting call is waiting for the person's approval. params: tool, tools, count, session_id.
    APPROVAL_PENDING = "approval_pending"
    #: An agent run ended without finishing. params: run_id, agent_id, status.
    RUN_FAILED = "run_failed"
    #: A subscription payment was applied. params: workspace_id, plan_id, amount_piastres, period_end.
    PAYMENT_RECEIVED = "payment_received"
    #: A renewal is coming up. params: workspace_id, amount_piastres, expires_at.
    RENEWAL_DUE = "renewal_due"
    #: A payment was missed. params: workspace_id, grace_until.
    PAYMENT_PAST_DUE = "payment_past_due"
    #: The plan's message or storage allowance is used up. params: limit, plan, resets_at.
    USAGE_LIMIT_REACHED = "usage_limit_reached"


#: Every kind a notification may have; the settings route accepts only these.
KINDS = (
    NotificationKind.APPROVAL_PENDING, NotificationKind.RUN_FAILED, NotificationKind.PAYMENT_RECEIVED,
    NotificationKind.RENEWAL_DUE, NotificationKind.PAYMENT_PAST_DUE, NotificationKind.USAGE_LIMIT_REACHED,
)
#: A notification's parameters are identifiers and short values; this is a generous ceiling.
MAX_PARAMS_BYTES = 2048


@dataclass(frozen=True, slots=True)
class Notification:
    id: str
    owner_id: UserId
    kind: str
    created_at: datetime
    params: Mapping[str, Any] = field(default_factory=dict)
    read_at: datetime | None = None
    dedupe_key: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValidationError(f"unknown notification kind {self.kind!r}", details={"field": "kind"})
        try:
            size = len(json.dumps(dict(self.params), sort_keys=True).encode("utf-8"))
        except (TypeError, ValueError) as exc:
            raise ValidationError("notification params must be JSON values") from exc
        if size > MAX_PARAMS_BYTES:
            raise ValidationError("notification params are too large", details={"limit_bytes": MAX_PARAMS_BYTES})

    @property
    def is_read(self) -> bool:
        return self.read_at is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "params": dict(self.params),
            "read": self.is_read,
            "created_at": self.created_at.isoformat(),
            "read_at": self.read_at.isoformat() if self.read_at else None,
        }


@dataclass(frozen=True, slots=True)
class NotificationSettings:
    """Which kinds the person does not want in their inbox. Everything is on by default, so a new
    kind reaches people until they turn it off."""

    disabled_kinds: frozenset[str] = frozenset()
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        unknown = sorted(set(self.disabled_kinds) - set(KINDS))
        if unknown:
            raise ValidationError(
                f"unknown notification kind(s): {', '.join(unknown)}", details={"field": "disabled_kinds"},
            )

    def enabled(self, kind: str) -> bool:
        return kind not in self.disabled_kinds

    def as_dict(self) -> dict[str, Any]:
        return {
            "kinds": {kind: self.enabled(kind) for kind in KINDS},
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
