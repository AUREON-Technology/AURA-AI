"""In-memory connector, twin and blueprint stores: the real substrate for the default test run (no infrastructure)."""

from __future__ import annotations

from typing import Sequence

from ..domain.ausm import ProjectTwin
from ..domain.blueprint import BlueprintState
from ..domain.compilation import ActivePointer, BlueprintRecord, plan_activation
from ..domain.connectors import ConnectorRecord, ConnectorState
from ..domain.errors import ConflictError, ValidationError
from ..domain.twin import TwinVersion

__all__ = ["InMemoryConnectorStore", "InMemoryTwinStore", "InMemoryBlueprintStore"]


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


class InMemoryTwinStore:
    """Append-only Project Twin versions (AP4). Twins are immutable, so they are kept as-is."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, int], tuple[TwinVersion, ProjectTwin]] = {}

    async def append(self, version: TwinVersion, twin: ProjectTwin) -> None:
        key = (version.project_id, version.version)
        if key in self._rows:
            raise ConflictError("twin version already exists", details={"version": version.version})
        if twin.version != version.version or twin.digest != version.digest:
            raise ValidationError("twin does not match its version record")
        self._rows[key] = (version, twin)

    async def latest(self, project_id: str) -> TwinVersion | None:
        versions = await self.versions(project_id, limit=1)
        return versions[0] if versions else None

    async def get(self, project_id: str, version: int) -> tuple[TwinVersion, ProjectTwin] | None:
        return self._rows.get((project_id, version))

    async def versions(self, project_id: str, *, limit: int = 50) -> Sequence[TwinVersion]:
        rows = sorted((v for (p, _), (v, _) in self._rows.items() if p == project_id), key=lambda v: -v.version)
        return rows[:limit]


class InMemoryBlueprintStore:
    """Compiled blueprint versions (AP6)."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, int], BlueprintRecord] = {}
        self._active: dict[str, ActivePointer] = {}

    async def append(self, record: BlueprintRecord) -> None:
        key = (record.project_id, record.version)
        if key in self._rows:
            raise ConflictError("blueprint version already exists", details={"version": record.version})
        self._rows[key] = record

    async def latest(self, project_id: str) -> BlueprintRecord | None:
        found = await self.versions(project_id, limit=1)
        return found[0] if found else None

    async def get(self, project_id: str, version: int) -> BlueprintRecord | None:
        return self._rows.get((project_id, version))

    async def versions(self, project_id: str, *, limit: int = 50) -> Sequence[BlueprintRecord]:
        rows = sorted((r for (p, _), r in self._rows.items() if p == project_id), key=lambda r: -r.version)
        return rows[:limit]

    async def transition(self, record: BlueprintRecord, *, expected: BlueprintState) -> None:
        key = (record.project_id, record.version)
        stored = self._rows.get(key)
        if stored is None or stored.state is not expected:
            raise ConflictError("blueprint changed state concurrently", details={"version": record.version})
        if stored.digest != record.digest:
            raise ConflictError("blueprint content cannot change", details={"version": record.version})
        self._rows[key] = record

    async def activate(self, project_id: str, version: int, *, by: str, at: str) -> ActivePointer:
        # No await between reading and writing: atomic on the event loop.
        target = self._rows.get((project_id, version))
        if target is None:
            raise ConflictError("unknown blueprint version", details={"version": version})
        pointer = self._active.get(project_id)
        current = self._rows.get((project_id, pointer.version)) if pointer else None
        live, retired, new_pointer = plan_activation(target, current, by=by, at=at)
        self._rows[(project_id, version)] = live
        if retired is not None:
            self._rows[(project_id, retired.version)] = retired
        self._active[project_id] = new_pointer
        return new_pointer

    async def active(self, project_id: str) -> ActivePointer | None:
        return self._active.get(project_id)
