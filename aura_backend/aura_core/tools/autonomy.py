"""Per-user review profile storage (ADR-041): how much a user wants to be asked before the agent
acts. The profile itself is applied by :class:`~aura_core.tools.policy.ToolPolicy`; this module
only remembers it.

**Where it lives, and what that costs.** There is no per-user settings table in this codebase and
a schema change is out of scope for this feature (Alembic is the only schema authority), so the
profile is kept in the ``Cache`` port under ``autonomy:profile:<user>`` with a very long TTL.
The port is documented as best-effort -- a Redis outage or an eviction reads as "no value" -- so
the failure mode is chosen to be safe in one direction only: an unreadable profile falls back to
the deployment's default (``standard`` unless the operator set ``AURA_AUTONOMY_DEFAULT``), which
for a user who chose ``strict`` means *losing* that choice until they set it again. It can never
turn into ``autonomous``: that value is only honoured when it is read back intact *and* the
deployment enabled it (``ToolPolicy.effective_autonomy``). Moving this to a durable row is the
one thing the profile still needs (ADR-041, "What is not built").
"""

from __future__ import annotations

import json
from typing import Any

from ..domain.identifiers import UserId
from .policy import AUTONOMY_PROFILES, DEFAULT_AUTONOMY

__all__ = ["AutonomyStore", "AUTONOMY_PROFILES", "DEFAULT_AUTONOMY"]

#: Ten years: "no expiry" on a cache that insists on a TTL.
_TTL_S = 10 * 365 * 24 * 3600
_KEY = "autonomy:profile:"


class AutonomyStore:
    def __init__(self, cache: Any, *, default: str = DEFAULT_AUTONOMY) -> None:
        if default not in AUTONOMY_PROFILES:
            raise ValueError(f"unknown autonomy profile {default!r}")
        self._cache = cache
        self._default = default

    @property
    def default(self) -> str:
        return self._default

    async def get(self, user_id: UserId) -> str:
        """The user's profile, or the deployment default when none is stored or it cannot be read."""
        return (await self.get_record(user_id))["profile"]

    async def get_record(self, user_id: UserId) -> dict[str, Any]:
        """``{"profile", "source", "updated_at"}``; ``source`` is ``user`` or ``default``."""
        try:
            raw = await self._cache.get(_KEY + user_id.value)
        except Exception:  # a cache outage must not fail a turn
            raw = None
        if raw:
            try:
                value = json.loads(raw)
                profile = value.get("profile")
                if profile in AUTONOMY_PROFILES:
                    return {"profile": profile, "source": "user", "updated_at": value.get("updated_at")}
            except (ValueError, AttributeError):
                pass
        return {"profile": self._default, "source": "default", "updated_at": None}

    async def put(self, user_id: UserId, profile: str, *, at: str) -> None:
        if profile not in AUTONOMY_PROFILES:
            raise ValueError(f"unknown autonomy profile {profile!r}")
        await self._cache.set(
            _KEY + user_id.value,
            json.dumps({"profile": profile, "updated_at": at}).encode(),
            ttl_s=_TTL_S,
        )

    async def delete(self, user_id: UserId) -> None:
        await self._cache.delete(_KEY + user_id.value)
