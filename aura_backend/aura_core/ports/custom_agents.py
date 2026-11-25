"""The store a person's custom agents live in (``aura_core/domain/custom_agents.py``).

Owner-scoped by construction: every method that reads or writes one agent takes the ``owner_id`` it
acts for, and an id that exists under another owner is indistinguishable from one that does not
exist (``get`` returns ``None``, ``update`` returns ``None``, ``delete`` returns ``False``). The
limit and the name rule are enforced by the store, atomically with the write, so two requests
racing for the last slot cannot both get it.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.custom_agents import CustomAgent
from ..domain.identifiers import UserId

__all__ = ["CustomAgentStore"]


@runtime_checkable
class CustomAgentStore(Protocol):
    async def list_for_owner(self, owner_id: UserId) -> list[CustomAgent]:
        """The owner's agents, oldest first."""
        ...

    async def get(self, owner_id: UserId, agent_id: str) -> CustomAgent | None:
        ...

    async def create(self, agent: CustomAgent, *, limit: int) -> CustomAgent:
        """Store ``agent``. Raises ``CustomAgentConflict`` (``limit`` / ``name``)."""
        ...

    async def update(self, agent: CustomAgent) -> CustomAgent | None:
        """Replace the owner's agent with the same id. ``None`` if the owner has none. Raises
        ``CustomAgentConflict`` (``name``) if the new name belongs to another of the owner's."""
        ...

    async def delete(self, owner_id: UserId, agent_id: str) -> bool:
        ...

    async def delete_for_owner(self, owner_id: UserId) -> int:
        """Remove every agent the owner has (account erasure); returns how many."""
        ...
