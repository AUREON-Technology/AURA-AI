"""Where a person's standing model preferences (``domain/model_settings.py``) are kept."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from ..domain.identifiers import UserId
from ..domain.model_settings import ModelSettings

__all__ = ["ModelSettingsStore"]


@runtime_checkable
class ModelSettingsStore(Protocol):
    """Every statement is scoped to the owner."""

    async def get(self, owner_id: UserId) -> ModelSettings | None:
        ...

    async def put(self, owner_id: UserId, settings: ModelSettings, *, at: datetime) -> ModelSettings:
        """Replace the owner's settings; returns what was stored (with ``updated_at``)."""
        ...

    async def delete(self, owner_id: UserId) -> bool:
        """Remove them; ``True`` if there were any. Also what account erasure calls."""
        ...
