"""The Qdrant cluster's own HTTP API, for the control center (ADR-037, stage 5).

``HttpQdrantAdmin`` implements ``VectorAdmin`` with the cluster key stored when Qdrant was
connected, sent as ``api-key`` (as every Qdrant example does). The cluster URL is the one named
then, or the gateway's ``AURA_QDRANT_URL``; ``platform_verify`` already refuses any other host,
so the key cannot be sent elsewhere.

Checked against Qdrant's v1.20 API reference (2026-11-22):

* ``GET /healthz``; ``GET /collections`` then ``GET /collections/{name}`` for ``result.status``,
  ``points_count``, ``indexed_vectors_count``, ``segments_count`` and
  ``config.params.vectors`` (``size``/``distance``, or a map of named vectors).
* ``POST /collections/{name}/snapshots`` (``result.name/creation_time/size``) and
  ``GET /collections/{name}/snapshots``.
* ``PUT /collections/{name}/snapshots/recover`` with ``location`` and ``priority``. Qdrant Cloud
  blocks recovery from a URL ("all outbound traffic is blocked") but allows a file URI, so the
  location is the snapshot's own file on the node: ``file:///qdrant/snapshots/<collection>/<name>``
  (the Docker image's snapshot directory, where Qdrant keeps each collection's snapshots). That
  path is the one part not proven against a Cloud cluster; GAP-067 records it.

Vendor messages are never passed through.
"""

from __future__ import annotations

import itertools
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Sequence
from urllib.parse import quote

from ..domain.errors import DependencyUnavailableError, NotFoundError, ValidationError
from ..ports.control_plane import VectorCollection, VectorSnapshot

__all__ = ["HttpQdrantAdmin", "InMemoryQdrant", "SNAPSHOT_DIR"]

#: Where the Qdrant image keeps snapshots; a collection's are in ``<dir>/<collection>/``.
SNAPSHOT_DIR = "/qdrant/snapshots"
_TIMEOUT_S = 30.0
#: Creating a snapshot copies the collection; give it longer than a read.
_SNAPSHOT_TIMEOUT_S = 300.0


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _int(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


class HttpQdrantAdmin:
    """``token_source`` returns the stored cluster key; ``url_source`` the cluster URL."""

    def __init__(self, *, token_source: Callable[[], Awaitable[str]], url_source: Callable[[], Awaitable[str]],
                 transport: Any = None) -> None:
        self._token_source = token_source
        self._url_source = url_source
        self._transport = transport

    async def _request(self, method: str, path: str, *, json: Any = None, params: Any = None,
                       timeout: float = _TIMEOUT_S) -> Any:
        import httpx

        base = (await self._url_source()).rstrip("/")
        if not base.startswith("https://") and not base.startswith("http://"):
            raise ValidationError("the Qdrant cluster URL is not set", details={"reason": "qdrant_url_missing"})
        try:
            async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as client:
                response = await client.request(
                    method, f"{base}{path}", json=json, params=params,
                    headers={"api-key": await self._token_source()},
                )
        except Exception as exc:
            raise DependencyUnavailableError(
                "the Qdrant cluster could not be reached", dependency="qdrant", details={"error": type(exc).__name__},
            ) from exc
        if response.status_code in (401, 403):
            raise ValidationError("Qdrant refused the cluster key", details={"reason": "qdrant_refused"})
        if response.status_code == 404:
            raise NotFoundError("the cluster has no such collection or snapshot")
        if response.status_code >= 400:
            raise DependencyUnavailableError(
                "Qdrant answered with an error", dependency="qdrant", details={"status": response.status_code},
            )
        try:
            return response.json()
        except ValueError:
            return None

    async def health(self) -> bool:
        try:
            await self._request("GET", "/healthz")
        except (DependencyUnavailableError, NotFoundError):
            return False
        return True

    async def collections(self) -> Sequence[VectorCollection]:
        listing = await self._request("GET", "/collections")
        names = [str(c.get("name")) for c in ((listing or {}).get("result") or {}).get("collections") or []
                 if isinstance(c, dict) and c.get("name")]
        out = []
        for name in sorted(names):
            info = ((await self._request("GET", f"/collections/{quote(name, safe='')}")) or {}).get("result") or {}
            vectors = ((info.get("config") or {}).get("params") or {}).get("vectors") or {}
            if isinstance(vectors, dict) and "size" not in vectors:
                # Named vectors: a map of name -> params. Show the first one's.
                first = next(iter(vectors.values()), None)
                vectors = first if isinstance(first, dict) else {}
            out.append(VectorCollection(
                name=name, status=str(info.get("status") or ""),
                points_count=_int(info.get("points_count")),
                indexed_vectors_count=_int(info.get("indexed_vectors_count")),
                segments_count=_int(info.get("segments_count")),
                vector_size=_int(vectors.get("size")) if isinstance(vectors, dict) else None,
                distance=str(vectors.get("distance") or "") if isinstance(vectors, dict) else "",
            ))
        return out

    async def snapshots(self, collection: str) -> Sequence[VectorSnapshot]:
        body = await self._request("GET", f"/collections/{quote(collection, safe='')}/snapshots")
        return [
            VectorSnapshot(name=str(s.get("name")), created_at=_parse_time(s.get("creation_time")), size=_int(s.get("size")))
            for s in ((body or {}).get("result") or []) if isinstance(s, dict) and s.get("name")
        ]

    async def create_snapshot(self, collection: str) -> VectorSnapshot:
        body = await self._request("POST", f"/collections/{quote(collection, safe='')}/snapshots",
                                   params={"wait": "true"}, timeout=_SNAPSHOT_TIMEOUT_S)
        result = (body or {}).get("result") or {}
        if not result.get("name"):
            raise DependencyUnavailableError("Qdrant did not return the snapshot", dependency="qdrant")
        return VectorSnapshot(name=str(result["name"]), created_at=_parse_time(result.get("creation_time")),
                              size=_int(result.get("size")))

    async def restore_snapshot(self, collection: str, snapshot: str) -> None:
        location = f"file://{SNAPSHOT_DIR}/{collection}/{snapshot}"
        await self._request(
            "PUT", f"/collections/{quote(collection, safe='')}/snapshots/recover",
            params={"wait": "true"}, json={"location": location, "priority": "snapshot"},
            timeout=_SNAPSHOT_TIMEOUT_S,
        )


class InMemoryQdrant:
    """A cluster with real state: collections, their points, and snapshots of those points."""

    def __init__(self) -> None:
        self.healthy = True
        self.points: dict[str, int] = {}
        self.snaps: dict[str, list[tuple[VectorSnapshot, int]]] = {}
        self.restored: list[tuple[str, str]] = []
        self._ids = itertools.count(1)

    def add_collection(self, name: str, points: int) -> None:
        self.points[name] = points
        self.snaps.setdefault(name, [])

    async def health(self) -> bool:
        return self.healthy

    async def collections(self) -> Sequence[VectorCollection]:
        return [VectorCollection(name=n, status="green", points_count=p, indexed_vectors_count=p, segments_count=2,
                                 vector_size=384, distance="Cosine") for n, p in sorted(self.points.items())]

    async def snapshots(self, collection: str) -> Sequence[VectorSnapshot]:
        if collection not in self.points:
            raise NotFoundError("the cluster has no such collection or snapshot")
        return [s for s, _ in self.snaps[collection]]

    async def create_snapshot(self, collection: str) -> VectorSnapshot:
        if collection not in self.points:
            raise NotFoundError("the cluster has no such collection or snapshot")
        snap = VectorSnapshot(name=f"{collection}-snap-{next(self._ids)}.snapshot",
                              created_at=datetime.now(timezone.utc), size=self.points[collection] * 100)
        self.snaps[collection].append((snap, self.points[collection]))
        return snap

    async def restore_snapshot(self, collection: str, snapshot: str) -> None:
        for snap, points in self.snaps.get(collection, []):
            if snap.name == snapshot:
                self.points[collection] = points
                self.restored.append((collection, snapshot))
                return
        raise NotFoundError("the cluster has no such collection or snapshot")
