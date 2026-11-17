"""Sharing a chat with the team (SaaS P1-F1, spec §5.2).

* Team workspaces only; any member may share their own chat, and every member may read.
* Unsharing: the sharer, or the team's Owner or an Admin.
* A share outlives its sharer's membership (it was given to the team), but a person who has left
  can no longer read or remove it: every call resolves membership first, so a stranger and a
  former member get the same 403 as for a workspace that does not exist.
* Audited without content.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any

from ..domain.errors import AuthorizationError, NotFoundError, ValidationError
from ..domain.sharing import MAX_TITLE_CHARS, SharedConversation, parse_shared_messages
from ..domain.tenancy import WorkspaceKind, WorkspaceRole
from ..ports.runtime import AuditEvent

__all__ = ["SharingService"]

LIST_LIMIT = 200


@dataclass
class SharingService:
    store: Any
    tenancy: Any
    clock: Any
    audit: Any

    async def share(self, actor: str, workspace_id: str, source_id: str, title: str, messages: Any) -> SharedConversation:
        workspace, _membership = await self.tenancy.resolve(actor, workspace_id)
        if workspace.kind is not WorkspaceKind.TEAM:
            raise ValidationError("chats are shared with a team; this is a personal workspace")
        source = str(source_id or "").strip()
        if not source or len(source) > 200:
            raise ValidationError("source_id is required (at most 200 characters)")
        clean_title = " ".join(str(title or "").split())[:MAX_TITLE_CHARS] or "Shared chat"
        stored = await self.store.put(SharedConversation(
            id=secrets.token_hex(12), workspace_id=workspace_id, shared_by=actor, source_id=source,
            title=clean_title, messages=parse_shared_messages(messages), created_at=self.clock.now(),
        ))
        await self._audit("workspace.share", actor, workspace_id, {"shared_id": stored.id, "messages": len(stored.messages)})
        return stored

    async def list(self, actor: str, workspace_id: str) -> list[SharedConversation]:
        await self.tenancy.resolve(actor, workspace_id)
        return await self.store.list(workspace_id, LIST_LIMIT)

    async def get(self, actor: str, workspace_id: str, conversation_id: str) -> SharedConversation:
        await self.tenancy.resolve(actor, workspace_id)
        found = await self.store.get(workspace_id, conversation_id)
        if found is None:
            raise NotFoundError("no such shared chat")
        return found

    async def unshare(self, actor: str, workspace_id: str, conversation_id: str) -> None:
        _workspace, membership = await self.tenancy.resolve(actor, workspace_id)
        found = await self.store.get(workspace_id, conversation_id)
        if found is None:
            raise NotFoundError("no such shared chat")
        if found.shared_by != actor and membership.role not in (WorkspaceRole.OWNER, WorkspaceRole.ADMIN):
            raise AuthorizationError("only the person who shared it, the Owner or an Admin can unshare it")
        await self.store.delete(workspace_id, conversation_id)
        await self._audit("workspace.unshare", actor, workspace_id, {"shared_id": conversation_id})

    async def _audit(self, action: str, actor: str, workspace_id: str, detail: dict) -> None:
        await self.audit.record(AuditEvent(
            action=action, actor=actor, outcome="ok", at=self.clock.now(),
            resource=f"workspace:{workspace_id}", detail=detail,
        ))
