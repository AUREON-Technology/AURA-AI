"""In-memory :class:`~aura_core.ports.sharing.SharedConversationStore` (SaaS P1-F1)."""

from __future__ import annotations

from dataclasses import replace

from ..domain.sharing import SharedConversation

__all__ = ["InMemorySharedConversationStore"]


class InMemorySharedConversationStore:
    def __init__(self) -> None:
        self._rows: dict[str, SharedConversation] = {}

    async def put(self, conversation: SharedConversation) -> SharedConversation:
        for existing in self._rows.values():
            if (existing.workspace_id, existing.shared_by, existing.source_id) == (
                    conversation.workspace_id, conversation.shared_by, conversation.source_id):
                conversation = replace(conversation, id=existing.id)
                break
        self._rows[conversation.id] = conversation
        return conversation

    async def get(self, workspace_id: str, conversation_id: str) -> SharedConversation | None:
        row = self._rows.get(conversation_id)
        return row if row is not None and row.workspace_id == workspace_id else None

    async def list(self, workspace_id: str, limit: int) -> list[SharedConversation]:
        rows = [r for r in self._rows.values() if r.workspace_id == workspace_id]
        rows.sort(key=lambda r: r.created_at, reverse=True)
        return [replace(r, messages=()) for r in rows[:limit]]

    async def find_source(self, workspace_id: str, shared_by: str, source_id: str) -> str | None:
        return next((r.id for r in self._rows.values()
                     if (r.workspace_id, r.shared_by, r.source_id) == (workspace_id, shared_by, source_id)), None)

    async def count_by(self, workspace_id: str, shared_by: str) -> int:
        return sum(1 for r in self._rows.values() if (r.workspace_id, r.shared_by) == (workspace_id, shared_by))

    async def delete_by_sharer(self, shared_by: str) -> int:
        doomed = [k for k, r in self._rows.items() if r.shared_by == shared_by]
        for key in doomed:
            del self._rows[key]
        return len(doomed)

    async def delete(self, workspace_id: str, conversation_id: str) -> bool:
        if await self.get(workspace_id, conversation_id) is None:
            return False
        del self._rows[conversation_id]
        return True
