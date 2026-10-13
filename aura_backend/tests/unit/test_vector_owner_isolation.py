"""CB-11 (SEC-09): "global" is an explicit sentinel, never the absence of an owner.

A point without an ``owner_id`` used to count as global, so a write path that forgot to set the
owner published one user's content to every user. Both vector adapters now refuse such a write,
and on read only the ``GLOBAL_OWNER`` sentinel is global: an owner-less point left over from
before the fix matches nobody.

The same scenarios run against the in-memory reference adapter and against ``QdrantVectorStore``
on qdrant-client's in-process mode (``location=":memory:"``), which evaluates the real filter
objects the adapter builds -- no server, no network.
"""

from __future__ import annotations

import pytest

from aura_core.adapters.memory_stores import InMemoryVectorStore
from aura_core.adapters.qdrant_store import QdrantVectorStore, _point_id
from aura_core.domain.errors import ValidationError
from aura_core.domain.identifiers import UserId
from aura_core.domain.knowledge import IndexIdentity
from aura_core.ports.storage import GLOBAL_OWNER, VectorPoint

IDENTITY = IndexIdentity(logical_name="isolation", schema_version=1, embedding_model_id="hash-embed-v1",
                         dimension=4)
VECTOR = (1.0, 0.0, 0.0, 0.0)


def _memory_store() -> InMemoryVectorStore:
    return InMemoryVectorStore()


def _qdrant_store() -> QdrantVectorStore:
    from qdrant_client import AsyncQdrantClient

    store = QdrantVectorStore("http://qdrant.invalid:6333")
    store._client = AsyncQdrantClient(location=":memory:")
    return store


async def _plant_ownerless(store, point_id: str) -> None:
    """Write a point with no owner the way pre-fix code could, bypassing the adapter's check."""
    if isinstance(store, InMemoryVectorStore):
        await store.ensure_index(IDENTITY)
        store._indexes[IDENTITY.physical_name][point_id] = VectorPoint(point_id, VECTOR, {"content": "lost owner"})
        return
    from qdrant_client import models as qmodels

    await store._get_client().upsert(
        collection_name=IDENTITY.physical_name, wait=True,
        points=[qmodels.PointStruct(id=_point_id(point_id), vector=list(VECTOR),
                                    payload={"_point_key": point_id, "content": "lost owner"})],
    )


@pytest.fixture(params=["memory", "qdrant"])
async def store(request):
    store = _memory_store() if request.param == "memory" else _qdrant_store()
    await store.ensure_index(IDENTITY)
    return store


class TestWrites:
    @pytest.mark.parametrize("payload", [{}, {"owner_id": None}, {"owner_id": ""}, {"owner_id": "  "}])
    async def test_a_point_without_an_owner_is_refused(self, store, payload) -> None:
        with pytest.raises(ValidationError, match="owner"):
            await store.upsert(IDENTITY, [VectorPoint("p", VECTOR, payload)])
        assert await store.count(IDENTITY) == 0

    async def test_one_ownerless_point_refuses_the_whole_batch(self, store) -> None:
        with pytest.raises(ValidationError):
            await store.upsert(IDENTITY, [VectorPoint("ok", VECTOR, {"owner_id": "alice"}),
                                          VectorPoint("bad", VECTOR, {})])
        assert await store.count(IDENTITY) == 0

    async def test_the_global_sentinel_and_user_ids_are_accepted(self, store) -> None:
        await store.upsert(IDENTITY, [VectorPoint("g", VECTOR, {"owner_id": GLOBAL_OWNER}),
                                      VectorPoint("a", VECTOR, {"owner_id": "alice"})])
        assert await store.count(IDENTITY) == 2


class TestReads:
    async def test_an_ownerless_point_is_visible_to_nobody(self, store) -> None:
        await store.upsert(IDENTITY, [VectorPoint("shared", VECTOR, {"owner_id": GLOBAL_OWNER})])
        await _plant_ownerless(store, "legacy")
        for owner in (None, UserId("alice"), UserId("bob")):
            hits = await store.search(IDENTITY, VECTOR, limit=10, owner_id=owner)
            assert [pid for pid, _s, _p in hits] == ["shared"], owner

    async def test_one_users_point_never_reaches_another(self, store) -> None:
        await store.upsert(IDENTITY, [VectorPoint("alice-doc", VECTOR, {"owner_id": "alice"}),
                                      VectorPoint("shared", VECTOR, {"owner_id": GLOBAL_OWNER})])
        bob = await store.search(IDENTITY, VECTOR, limit=10, owner_id=UserId("bob"))
        alice = await store.search(IDENTITY, VECTOR, limit=10, owner_id=UserId("alice"))
        assert {pid for pid, _s, _p in bob} == {"shared"}
        assert {pid for pid, _s, _p in alice} == {"alice-doc", "shared"}

    async def test_global_can_still_be_excluded(self, store) -> None:
        await store.upsert(IDENTITY, [VectorPoint("alice-doc", VECTOR, {"owner_id": "alice"}),
                                      VectorPoint("shared", VECTOR, {"owner_id": GLOBAL_OWNER})])
        hits = await store.search(IDENTITY, VECTOR, limit=10, owner_id=UserId("alice"), include_global=False)
        assert [pid for pid, _s, _p in hits] == ["alice-doc"]
