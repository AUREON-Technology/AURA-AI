"""In-memory :class:`~aura_core.ports.model_settings.ModelSettingsStore` (a real implementation:
the default test run and a single-process deployment use it)."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from ..domain.identifiers import UserId
from ..domain.model_settings import ModelSettings

__all__ = ["InMemoryModelSettingsStore"]


class InMemoryModelSettingsStore:
    def __init__(self) -> None:
        self._rows: dict[str, ModelSettings] = {}

    async def get(self, owner_id: UserId) -> ModelSettings | None:
        return self._rows.get(owner_id.value)

    async def put(self, owner_id: UserId, settings: ModelSettings, *, at: datetime) -> ModelSettings:
        stored = replace(settings, updated_at=at)
        self._rows[owner_id.value] = stored
        return stored

    async def delete(self, owner_id: UserId) -> bool:
        return self._rows.pop(owner_id.value, None) is not None
