"""In-memory :class:`~aura_core.ports.prompts.PromptPinStore` (INT-004).

The real substrate for tests and a single-process local run; production uses
``postgres_prompt_pins.py``. Shared by reference, two registries on one instance behave like two
gateway workers on one Postgres table.
"""

from __future__ import annotations

from typing import Mapping

__all__ = ["InMemoryPromptPinStore"]


class InMemoryPromptPinStore:
    def __init__(self) -> None:
        self._pins: dict[str, str] = {}

    async def load_all(self) -> Mapping[str, str]:
        return dict(self._pins)

    async def set(self, template_id: str, version: str, *, pinned_by: str | None = None) -> None:
        self._pins[template_id] = version

    async def delete(self, template_id: str) -> bool:
        return self._pins.pop(template_id, None) is not None
