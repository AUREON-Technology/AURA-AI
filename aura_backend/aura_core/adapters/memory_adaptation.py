"""In-memory connector store: the real substrate for the default test run (no infrastructure)."""

from __future__ import annotations

from typing import Sequence

from ..domain.connectors import ConnectorRecord, ConnectorState

__all__ = ["InMemoryConnectorStore"]


class InMemoryConnectorStore:
    def __init__(self) -> None:
        self._rows: dict[str, ConnectorRecord] = {}

    async def put(self, record: ConnectorRecord) -> None:
        self._rows[record.id] = record

    async def get(self, connector_id: str) -> ConnectorRecord | None:
        return self._rows.get(connector_id)

    async def list(self, *, state: ConnectorState | None = None) -> Sequence[ConnectorRecord]:
        rows = sorted(self._rows.values(), key=lambda r: r.id)
        return [r for r in rows if state is None or r.state is state]

    async def delete(self, connector_id: str) -> bool:
        return self._rows.pop(connector_id, None) is not None
