"""Storage for chats shared with a team (SaaS P1-F1). Stdlib only (ADR-001)."""

from __future__ import annotations

from typing import Protocol

from ..domain.sharing import SharedConversation

__all__ = ["SharedConversationStore"]


class SharedConversationStore(Protocol):
    async def put(self, conversation: SharedConversation) -> SharedConversation:
        """Store it, replacing the row with the same ``(workspace_id, shared_by, source_id)``
        (which keeps its id). Returns what is stored."""
        ...

    async def get(self, workspace_id: str, conversation_id: str) -> SharedConversation | None: ...

    async def list(self, workspace_id: str, limit: int) -> list[SharedConversation]:
        """Newest first; ``messages`` may be empty (a listing carries metadata only)."""
        ...

    async def delete(self, workspace_id: str, conversation_id: str) -> bool: ...

    async def find_source(self, workspace_id: str, shared_by: str, source_id: str) -> str | None:
        """The id already shared for this source, if any."""
        ...

    async def count_by(self, workspace_id: str, shared_by: str) -> int: ...

    async def delete_by_sharer(self, shared_by: str) -> int:
        """Every share this person made, in every workspace (account erasure)."""
        ...
