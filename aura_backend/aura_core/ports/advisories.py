"""Ports for the local advisory copy (``domain/advisories.py``).

``AdvisoryStore`` is what the dependency scan reads and the sync job writes. ``AdvisoryFeed`` is the
one place advisories come from. Both are small on purpose: the scan needs only "advisories for these
packages" and "how fresh is it".
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, AsyncIterator, Mapping, Protocol, Sequence, runtime_checkable

from ..domain.advisories import EcosystemStatus, StoredAdvisory

__all__ = ["AdvisoryStore", "AdvisoryFeed"]


@runtime_checkable
class AdvisoryStore(Protocol):
    async def upsert(self, items: Sequence[StoredAdvisory]) -> int:
        """Insert or replace; returns how many were written."""

    async def remove(self, ecosystem: str, advisory_ids: Sequence[str]) -> int:
        """Delete advisories (a withdrawn one must stop matching); returns how many rows went."""

    async def lookup(self, ecosystem: str, packages: Sequence[str]) -> list[Mapping[str, Any]]:
        """The stored payloads that affect any of ``packages`` (normalised names), one per advisory
        with the affected entries of every requested package merged."""

    async def status(self) -> list[EcosystemStatus]:
        """One row per ecosystem that has ever been synced or attempted."""

    async def record_sync(self, status: EcosystemStatus) -> None:
        """Save the outcome of a sync attempt for ``status.ecosystem``."""


@runtime_checkable
class AdvisoryFeed(Protocol):
    async def modified_since(self, ecosystem: str) -> list[tuple[datetime, str]]:
        """``(modified, advisory id)`` for every advisory of ``ecosystem``, newest first."""

    async def fetch(self, ecosystem: str, advisory_id: str) -> Mapping[str, Any] | None:
        """One advisory document, or ``None`` if the feed has no such file."""

    async def archive(self, ecosystem: str, *, batch: int = 500) -> AsyncIterator[list[Mapping[str, Any]]]:
        """Every advisory of ``ecosystem`` from the bulk archive, in batches (an async generator)."""
        yield []  # pragma: no cover
