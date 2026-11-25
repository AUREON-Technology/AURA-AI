"""In-memory :class:`~aura_core.ports.custom_agents.CustomAgentStore`.

The real substrate for tests and a single-process local run; production uses
``postgres_custom_agents.py``. The two follow the same rules, which
``tests/unit/test_custom_agents.py`` and ``tests/load/test_custom_agents_postgres.py`` pin
against each other: names are unique per owner ignoring case, and the limit is checked at write.
"""

from __future__ import annotations

from ..domain.custom_agents import CustomAgent, CustomAgentConflict
from ..domain.identifiers import UserId

__all__ = ["InMemoryCustomAgentStore"]


class InMemoryCustomAgentStore:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], CustomAgent] = {}

    def _owned(self, owner_id: UserId) -> list[CustomAgent]:
        return sorted(
            (a for (owner, _), a in self._rows.items() if owner == owner_id.value),
            key=lambda a: (a.created_at, a.id),
        )

    async def list_for_owner(self, owner_id: UserId) -> list[CustomAgent]:
        return self._owned(owner_id)

    async def get(self, owner_id: UserId, agent_id: str) -> CustomAgent | None:
        return self._rows.get((owner_id.value, agent_id))

    async def create(self, agent: CustomAgent, *, limit: int) -> CustomAgent:
        owned = self._owned(agent.owner_id)
        if len(owned) >= limit:
            raise CustomAgentConflict("limit", f"at most {limit} custom agents")
        if any(a.name.casefold() == agent.name.casefold() for a in owned):
            raise CustomAgentConflict("name", "you already have an agent with this name")
        self._rows[(agent.owner_id.value, agent.id)] = agent
        return agent

    async def update(self, agent: CustomAgent) -> CustomAgent | None:
        key = (agent.owner_id.value, agent.id)
        if key not in self._rows:
            return None
        if any(a.id != agent.id and a.name.casefold() == agent.name.casefold() for a in self._owned(agent.owner_id)):
            raise CustomAgentConflict("name", "you already have an agent with this name")
        self._rows[key] = agent
        return agent

    async def delete(self, owner_id: UserId, agent_id: str) -> bool:
        return self._rows.pop((owner_id.value, agent_id), None) is not None

    async def delete_for_owner(self, owner_id: UserId) -> int:
        doomed = [key for key in self._rows if key[0] == owner_id.value]
        for key in doomed:
            del self._rows[key]
        return len(doomed)
