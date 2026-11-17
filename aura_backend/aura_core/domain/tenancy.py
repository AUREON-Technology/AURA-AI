"""Workspaces: who shares what (SaaS phase 1, ADR-034).

Every person has one **personal** workspace; a **team** workspace adds Owner / Admin / Member
roles and invites. These roles are a customer's, not the operator's: nothing here maps onto the
platform roles in ``services/rbac.py`` and nothing grants a console route or an ``admin.*``
capability.

Scoping reuses the ports' existing ``owner_id`` obligation:

* in a personal workspace the scope is the person's own subject, exactly as before, so data
  that predates workspaces needs no rewrite;
* in a team the scope of *shareable* data (knowledge, graph) is ``workspace:<id>``, a reserved
  principal (``identifiers.RESERVED_PRINCIPAL_PREFIXES``) no person's subject can equal.

Personal-life data (memory, Personal-OS records, feedback, runs, data keys) never takes a team
scope. An invite is a one-time token ``auraiv_<invite_id>_<secret>``; only its SHA-256 is stored.
Stdlib only (ADR-001).
"""

from __future__ import annotations

import hashlib
import re
import secrets
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from .errors import ValidationError

__all__ = [
    "WorkspaceKind",
    "WorkspaceRole",
    "Workspace",
    "Membership",
    "Invite",
    "WorkspaceContext",
    "INVITE_TTL",
    "MAX_WORKSPACE_NAME",
    "INVITE_TOKEN_PREFIX",
    "new_workspace_id",
    "workspace_scope",
    "new_invite_token",
    "parse_invite_token",
    "invite_digest",
    "can",
    "clean_workspace_name",
]

INVITE_TTL = timedelta(days=7)
MAX_WORKSPACE_NAME = 80
INVITE_TOKEN_PREFIX = "auraiv_"
_WORKSPACE_ID = re.compile(r"^[0-9a-f]{32}$")
_INVITE_ID = re.compile(r"^[0-9a-f]{16}$")
_INVITE_TOKEN = re.compile(r"^auraiv_([0-9a-f]{16})_([A-Za-z0-9_\-]{20,})$")


class WorkspaceKind(str, Enum):
    PERSONAL = "personal"
    TEAM = "team"


class WorkspaceRole(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


@dataclass(frozen=True, slots=True)
class Workspace:
    id: str
    kind: WorkspaceKind
    name: str
    created_by: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Membership:
    workspace_id: str
    user_id: str
    role: WorkspaceRole
    joined_at: datetime
    invited_by: str | None = None


@dataclass(frozen=True, slots=True)
class Invite:
    invite_id: str
    workspace_id: str
    email: str
    role: WorkspaceRole
    digest: str
    created_by: str
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None = None
    accepted_by: str | None = None

    def is_open(self, now: datetime) -> bool:
        return self.accepted_at is None and now < self.expires_at


@dataclass(frozen=True, slots=True)
class WorkspaceContext:
    """The workspace a request acts in, after its membership was checked against the store."""

    workspace_id: str
    kind: WorkspaceKind
    role: WorkspaceRole


def new_workspace_id() -> str:
    return secrets.token_hex(16)


def is_workspace_id(value: str) -> bool:
    return isinstance(value, str) and bool(_WORKSPACE_ID.match(value))


def workspace_scope(workspace_id: str) -> str:
    if not is_workspace_id(workspace_id):
        raise ValidationError("a workspace id is 32 lowercase hex characters")
    return f"workspace:{workspace_id}"


def new_invite_token() -> tuple[str, str]:
    invite_id = secrets.token_hex(8)
    return invite_id, f"{INVITE_TOKEN_PREFIX}{invite_id}_{secrets.token_urlsafe(32)}"


def parse_invite_token(token: str) -> str:
    match = _INVITE_TOKEN.match(token) if isinstance(token, str) else None
    if match is None:
        raise ValidationError("not a valid invite token")
    return match.group(1)


def is_invite_id(value: str) -> bool:
    return isinstance(value, str) and bool(_INVITE_ID.match(value))


def invite_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


_OWNER_ACTIONS = frozenset(
    {"view", "invite", "remove_member", "change_role", "transfer", "delete", "manage_knowledge"}
)
_ADMIN_ACTIONS = frozenset({"view", "invite", "manage_knowledge"})
_ADMIN_ON_MEMBERS = frozenset({"remove_member", "change_role"})


def can(role: WorkspaceRole, action: str, *, target_role: WorkspaceRole | None = None) -> bool:
    """Whether ``role`` may perform ``action`` (on a member holding ``target_role``)."""
    if role is WorkspaceRole.OWNER:
        return action in _OWNER_ACTIONS
    if role is WorkspaceRole.ADMIN:
        if action in _ADMIN_ACTIONS:
            return True
        return action in _ADMIN_ON_MEMBERS and target_role is WorkspaceRole.MEMBER
    return action == "view"


def clean_workspace_name(name: str) -> str:
    if not isinstance(name, str):
        raise ValidationError("a workspace name is text")
    text = unicodedata.normalize("NFKC", name)
    if any(not ch.isprintable() for ch in text):
        raise ValidationError("a workspace name cannot contain control or formatting characters")
    text = text.strip()
    if not text or len(text) > MAX_WORKSPACE_NAME:
        raise ValidationError(f"a workspace name is 1-{MAX_WORKSPACE_NAME} characters")
    return text
