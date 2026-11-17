"""Port for prompt-version pins (TD-11, INT-004). Stdlib only (ADR-001).

A pin is the operator's rollback lever for a bad prompt change. It is shared state: the gateway
runs several workers, each with its own :class:`~aura_core.prompts.PromptRegistry`, and a pin
must reach all of them and survive a restart. The registry keeps a local copy for the hot path
and refreshes it from this store (see ``PromptRegistry.refresh_pins``).
"""

from __future__ import annotations

from typing import Mapping, Protocol

__all__ = ["PromptPinStore"]


class PromptPinStore(Protocol):
    async def load_all(self) -> Mapping[str, str]:
        """Every pin, as ``{template_id: version}``."""

    async def set(self, template_id: str, version: str, *, pinned_by: str | None = None) -> None:
        """Pin ``template_id`` to ``version``, replacing any earlier pin."""

    async def delete(self, template_id: str) -> bool:
        """Remove the pin. ``True`` if there was one."""
