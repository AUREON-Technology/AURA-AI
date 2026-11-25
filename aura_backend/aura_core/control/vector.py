"""Qdrant Cloud from the control center (ADR-037, stage 5): status and snapshots.

Engine layer: it reaches the cluster only through the ``VectorAdmin`` port, with the cluster key
stored when Qdrant was connected. The cluster is the one named then (or the gateway's own
``AURA_QDRANT_URL``), so no setting here points the key anywhere else. No resizing: that is a
Qdrant Cloud console decision with a bill attached (decision 11).

A restore replaces a collection's contents, so before it runs, a snapshot of the current state is
taken: the restore can itself be undone by restoring that one. Both are journaled.
"""

from __future__ import annotations

import re
from typing import Any

from ..domain.control import Platform
from ..domain.errors import NotFoundError, ValidationError
from ..ports.control_plane import ControlStore, VectorAdmin, VectorCollection, VectorSnapshot
from .railway import change_json
from .service import ControlService, _confirm

__all__ = ["VectorService"]

_NAME = re.compile(r"^[A-Za-z0-9_.:-]{1,255}$")


def _refuse(message: str, reason: str) -> ValidationError:
    return ValidationError(message, details={"reason": reason})


def _collection_json(c: VectorCollection) -> dict[str, Any]:
    return {
        "name": c.name, "status": c.status, "points_count": c.points_count,
        "indexed_vectors_count": c.indexed_vectors_count, "segments_count": c.segments_count,
        "vector_size": c.vector_size, "distance": c.distance,
    }


def _snapshot_json(s: VectorSnapshot) -> dict[str, Any]:
    return {"name": s.name, "created_at": s.created_at.isoformat() if s.created_at else None, "size": s.size}


class VectorService:
    def __init__(self, *, control: ControlService, api: VectorAdmin, store: ControlStore, default_url: str) -> None:
        self._control = control
        self._api = api
        self._store = store
        self._default_url = default_url

    async def _cluster(self) -> str:
        credential = await self._store.get_credential(Platform.QDRANT)
        if credential is None:
            raise NotFoundError("Qdrant is not connected; connect it in Settings")
        return credential.target or self._default_url

    async def overview(self) -> dict[str, Any]:
        cluster = await self._cluster()
        healthy = await self._api.health()
        collections = await self._api.collections() if healthy else []
        return {
            "cluster": cluster,
            "healthy": healthy,
            "collections": [_collection_json(c) for c in collections],
        }

    async def _collection(self, name: str) -> VectorCollection:
        if not _NAME.match(name or ""):
            raise _refuse("that is not a collection name", "bad_name")
        await self._cluster()
        for collection in await self._api.collections():
            if collection.name == name:
                return collection
        raise NotFoundError("the cluster has no such collection")

    async def snapshots(self, collection: str) -> list[dict[str, Any]]:
        await self._collection(collection)
        found = await self._api.snapshots(collection)
        return [_snapshot_json(s) for s in sorted(found, key=lambda s: (s.created_at is None, s.created_at), reverse=True)]

    async def create_snapshot(self, collection: str, *, actor: str, confirm: str | None) -> dict[str, Any]:
        await self._collection(collection)
        _confirm(collection, confirm)
        await self._control.check_write_cap(actor)
        snapshot = await self._api.create_snapshot(collection)
        record = await self._control.journal(
            actor=actor, platform=Platform.QDRANT, target=collection, action="vector_snapshot",
            detail={"service": collection, "snapshot": snapshot.name},
        )
        return {"snapshot": _snapshot_json(snapshot), "change": change_json(record)}

    async def restore(self, collection: str, snapshot: str, *, actor: str, confirm: str | None) -> dict[str, Any]:
        await self._collection(collection)
        _confirm(collection, confirm)
        if snapshot not in {s.name for s in await self._api.snapshots(collection)}:
            raise NotFoundError("that snapshot is not on the cluster")
        await self._control.check_write_cap(actor)
        # The restore cannot be undone by Qdrant: keep the state it replaces.
        safety = await self._api.create_snapshot(collection)
        await self._api.restore_snapshot(collection, snapshot)
        record = await self._control.journal(
            actor=actor, platform=Platform.QDRANT, target=collection, action="vector_restore",
            detail={"service": collection, "snapshot": snapshot, "safety_snapshot": safety.name},
        )
        return {"safety_snapshot": safety.name, "change": change_json(record)}
