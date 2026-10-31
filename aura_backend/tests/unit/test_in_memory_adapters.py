"""The in-memory storage adapters.

These are the substrate the whole suite runs on, so their semantics have to match what the
Postgres and Qdrant adapters will do -- a test that passes here and fails in production is worse
than no test. The properties that matter most are owner scoping (a read must never cross users)
and the alias/version invariants the index lifecycle depends on.
"""

from __future__ import annotations

import pytest

from aura_core.adapters import (
    InMemoryDocumentStore,
    InMemoryGraphStore,
    InMemoryMemoryStore,
    InMemoryVectorStore,
)
from aura_core.domain.errors import ConflictError
from aura_core.domain.identifiers import ChunkId, DocumentId, MemoryId, UserId
from aura_core.domain.knowledge import (
    Chunk,
    Document,
    DocumentStatus,
    Entity,
    GraphDelta,
    IndexIdentity,
    Relation,
)
from aura_core.domain.memory import MemoryKind, MemoryRecord, Provenance
from aura_core.ports.storage import VectorPoint


def _identity(version: int = 1, dimension: int = 4) -> IndexIdentity:
    return IndexIdentity(
        logical_name="test", schema_version=version,
        embedding_model_id="hash-embed-v1", dimension=dimension,
    )


def _point(pid: str, vector: tuple[float, ...], **payload) -> VectorPoint:
    return VectorPoint(id=pid, vector=vector, payload=payload)


class TestVectorStore:
    @pytest.fixture
    async def store(self) -> InMemoryVectorStore:
        store = InMemoryVectorStore()
        await store.ensure_index(_identity())
        return store

    async def test_upsert_and_search_returns_the_nearest_first(self, store) -> None:
        await store.upsert(_identity(), [
            _point("a", (1.0, 0.0, 0.0, 0.0), owner_id="u1"),
            _point("b", (0.0, 1.0, 0.0, 0.0), owner_id="u1"),
        ])
        hits = await store.search(
            _identity(), (0.9, 0.1, 0.0, 0.0), limit=2, owner_id=UserId("u1")
        )
        assert [pid for pid, _score, _payload in hits] == ["a", "b"]
        assert hits[0][1] > hits[1][1]

    async def test_upsert_replaces_a_point_with_the_same_id(self, store) -> None:
        await store.upsert(_identity(), [_point("a", (1.0, 0.0, 0.0, 0.0), owner_id="u1", v=1)])
        await store.upsert(_identity(), [_point("a", (0.0, 1.0, 0.0, 0.0), owner_id="u1", v=2)])

        assert await store.count(_identity()) == 1
        hits = await store.search(
            _identity(), (0.0, 1.0, 0.0, 0.0), limit=10, owner_id=UserId("u1")
        )
        assert hits[0][2]["v"] == 2

    async def test_search_is_owner_scoped(self, store) -> None:
        """The single most important property here: a read must never cross users."""
        await store.upsert(_identity(), [
            _point("alice", (1.0, 0.0, 0.0, 0.0), owner_id="alice"),
            _point("bob", (1.0, 0.0, 0.0, 0.0), owner_id="bob"),
        ])
        hits = await store.search(
            _identity(), (1.0, 0.0, 0.0, 0.0), limit=10, owner_id=UserId("alice")
        )
        assert [pid for pid, _s, _p in hits] == ["alice"]

    async def test_an_unscoped_search_sees_only_global_points(self, store) -> None:
        """Passing no owner must not mean 'search everything' -- that default would leak every
        user's documents into an administrative query."""
        await store.upsert(_identity(), [
            _point("private", (1.0, 0.0, 0.0, 0.0), owner_id="alice"),
            _point("shared", (1.0, 0.0, 0.0, 0.0), owner_id="global"),
        ])
        hits = await store.search(_identity(), (1.0, 0.0, 0.0, 0.0), limit=10, owner_id=None)
        assert [pid for pid, _s, _p in hits] == ["shared"]

    async def test_global_points_are_visible_to_a_scoped_search(self, store) -> None:
        await store.upsert(_identity(), [
            _point("mine", (1.0, 0.0, 0.0, 0.0), owner_id="alice"),
            _point("shared", (0.9, 0.1, 0.0, 0.0), owner_id="global"),
        ])
        hits = await store.search(
            _identity(), (1.0, 0.0, 0.0, 0.0), limit=10, owner_id=UserId("alice")
        )
        assert {pid for pid, _s, _p in hits} == {"mine", "shared"}

    async def test_global_content_can_be_excluded(self, store) -> None:
        await store.upsert(_identity(), [
            _point("mine", (1.0, 0.0, 0.0, 0.0), owner_id="alice"),
            _point("shared", (1.0, 0.0, 0.0, 0.0), owner_id="global"),
        ])
        hits = await store.search(
            _identity(), (1.0, 0.0, 0.0, 0.0), limit=10, owner_id=UserId("alice"),
            include_global=False,
        )
        assert [pid for pid, _s, _p in hits] == ["mine"]

    async def test_payload_filters_narrow_results(self, store) -> None:
        await store.upsert(_identity(), [
            _point("a", (1.0, 0.0, 0.0, 0.0), owner_id="u1", kind="doc"),
            _point("b", (1.0, 0.0, 0.0, 0.0), owner_id="u1", kind="memory"),
        ])
        hits = await store.search(
            _identity(), (1.0, 0.0, 0.0, 0.0), limit=10, owner_id=UserId("u1"),
            filters={"kind": "doc"},
        )
        assert [pid for pid, _s, _p in hits] == ["a"]

    async def test_a_list_filter_matches_any_member(self, store) -> None:
        await store.upsert(_identity(), [
            _point("a", (1.0, 0.0, 0.0, 0.0), owner_id="u1", kind="doc"),
            _point("b", (1.0, 0.0, 0.0, 0.0), owner_id="u1", kind="memory"),
            _point("c", (1.0, 0.0, 0.0, 0.0), owner_id="u1", kind="other"),
        ])
        hits = await store.search(
            _identity(), (1.0, 0.0, 0.0, 0.0), limit=10, owner_id=UserId("u1"),
            filters={"kind": ["doc", "memory"]},
        )
        assert {pid for pid, _s, _p in hits} == {"a", "b"}

    async def test_a_score_threshold_can_return_nothing(self, store) -> None:
        """An honest empty result beats returning the least-bad match."""
        await store.upsert(_identity(), [
            _point("a", (0.0, 1.0, 0.0, 0.0), owner_id="u1"),
        ])
        hits = await store.search(
            _identity(), (1.0, 0.0, 0.0, 0.0), limit=10, owner_id=UserId("u1"),
            score_threshold=0.5,
        )
        assert hits == []

    async def test_isolation_is_applied_before_scoring(self, store) -> None:
        """Filtering a scored list would let a cross-tenant document displace a legitimate one
        out of the top-k, silently shortening the result."""
        await store.upsert(_identity(), [
            _point("theirs", (1.0, 0.0, 0.0, 0.0), owner_id="bob"),
            _point("mine", (0.8, 0.2, 0.0, 0.0), owner_id="alice"),
        ])
        hits = await store.search(
            _identity(), (1.0, 0.0, 0.0, 0.0), limit=1, owner_id=UserId("alice")
        )
        assert [pid for pid, _s, _p in hits] == ["mine"]

    async def test_searching_an_absent_index_returns_nothing(self) -> None:
        store = InMemoryVectorStore()
        assert await store.search(
            _identity(), (1.0, 0.0, 0.0, 0.0), limit=10, owner_id=None
        ) == []

    async def test_deleting_points_reports_how_many_existed(self, store) -> None:
        await store.upsert(_identity(), [_point("a", (1.0, 0.0, 0.0, 0.0), owner_id="u1")])
        assert await store.delete(_identity(), point_ids=["a", "never-existed"]) == 1

    async def test_deleting_from_an_absent_index_is_zero_not_an_error(self) -> None:
        assert await InMemoryVectorStore().delete(_identity(), point_ids=["a"]) == 0

    async def test_delete_by_filter_is_owner_scoped(self, store) -> None:
        """Otherwise 'forget everything from source X' would delete another user's data."""
        await store.upsert(_identity(), [
            _point("a", (1.0, 0.0, 0.0, 0.0), owner_id="alice", source="s1"),
            _point("b", (1.0, 0.0, 0.0, 0.0), owner_id="bob", source="s1"),
        ])
        removed = await store.delete_by_filter(
            _identity(), filters={"source": "s1"}, owner_id=UserId("alice")
        )
        assert removed == 1
        assert await store.count(_identity()) == 1

    async def test_delete_by_filter_without_an_owner_removes_across_owners(self, store) -> None:
        await store.upsert(_identity(), [
            _point("a", (1.0, 0.0, 0.0, 0.0), owner_id="alice", source="s1"),
            _point("b", (1.0, 0.0, 0.0, 0.0), owner_id="bob", source="s1"),
        ])
        assert await store.delete_by_filter(
            _identity(), filters={"source": "s1"}, owner_id=None
        ) == 2

    async def test_delete_by_filter_on_an_absent_index_is_zero(self) -> None:
        assert await InMemoryVectorStore().delete_by_filter(
            _identity(), filters={}, owner_id=None
        ) == 0

    async def test_count_can_be_scoped_to_an_owner(self, store) -> None:
        await store.upsert(_identity(), [
            _point("a", (1.0, 0.0, 0.0, 0.0), owner_id="alice"),
            _point("b", (1.0, 0.0, 0.0, 0.0), owner_id="bob"),
        ])
        assert await store.count(_identity()) == 2
        assert await store.count(_identity(), owner_id=UserId("alice")) == 1

    async def test_counting_an_absent_index_is_zero(self) -> None:
        assert await InMemoryVectorStore().count(_identity()) == 0

    async def test_index_existence_is_reported(self, store) -> None:
        assert await store.index_exists(_identity()) is True
        assert await store.index_exists(_identity(version=99)) is False

    async def test_an_alias_resolves_to_its_identity(self, store) -> None:
        await store.set_alias("live", _identity(version=2))
        assert (await store.resolve_alias("live")).schema_version == 2

    async def test_an_unset_alias_resolves_to_nothing(self, store) -> None:
        assert await store.resolve_alias("never-set") is None

    async def test_repointing_an_alias_is_the_promotion_primitive(self, store) -> None:
        """O(1) and reversible -- the whole reason index identity is explicit (ADR-007)."""
        await store.set_alias("live", _identity(version=1))
        await store.set_alias("live", _identity(version=2))
        assert (await store.resolve_alias("live")).schema_version == 2
        await store.set_alias("live", _identity(version=1))
        assert (await store.resolve_alias("live")).schema_version == 1

    async def test_an_index_an_alias_points_at_cannot_be_dropped(self, store) -> None:
        """The failure mode this prevents is dropping the index production is serving from."""
        await store.set_alias("live", _identity())
        with pytest.raises(ConflictError) as excinfo:
            await store.drop_index(_identity())
        assert "live" in excinfo.value.details["aliases"]

    async def test_an_unreferenced_index_can_be_dropped(self, store) -> None:
        await store.ensure_index(_identity(version=2))
        await store.drop_index(_identity(version=2))
        assert await store.index_exists(_identity(version=2)) is False

    async def test_indexes_with_different_identities_do_not_share_points(self, store) -> None:
        """Vectors from one embedding model are meaningless when searched with another."""
        await store.ensure_index(_identity(version=2))
        await store.upsert(_identity(version=1), [_point("a", (1.0, 0.0, 0.0, 0.0), owner_id="u1")])

        assert await store.count(_identity(version=1)) == 1
        assert await store.count(_identity(version=2)) == 0

    async def test_index_names_are_listed(self, store) -> None:
        await store.ensure_index(_identity(version=2))
        assert len(store.index_names()) == 2


class TestDocumentStore:
    def _document(self, doc_id: str = "d1", *, version: int = 1, owner: str | None = "u1",
                  status: DocumentStatus = DocumentStatus.ACTIVE,
                  content: str | None = None) -> Document:
        return Document(
            id=DocumentId(doc_id), version=version, title="T",
            content=content if content is not None else f"body of {doc_id} v{version}",
            owner_id=UserId(owner) if owner else None, status=status,
        )

    def _chunk(self, doc_id: str, index: int, version: int = 1) -> Chunk:
        return Chunk(
            id=ChunkId(f"{doc_id}:v{version}:{index}"), document_id=DocumentId(doc_id),
            document_version=version, index=index, content=f"chunk {index}",
        )

    async def test_put_and_get(self) -> None:
        store = InMemoryDocumentStore()
        await store.put(self._document(), [self._chunk("d1", 0)])

        assert (await store.get(DocumentId("d1"))).title == "T"
        assert len(await store.chunks_for(DocumentId("d1"), 1)) == 1

    async def test_get_returns_the_latest_version_by_default(self) -> None:
        store = InMemoryDocumentStore()
        await store.put(self._document(version=1), [])
        await store.put(self._document(version=2), [])
        assert (await store.get(DocumentId("d1"))).version == 2

    async def test_a_specific_version_can_be_requested(self) -> None:
        store = InMemoryDocumentStore()
        await store.put(self._document(version=1), [])
        await store.put(self._document(version=2), [])
        assert (await store.get(DocumentId("d1"), version=1)).version == 1

    async def test_an_unknown_document_is_none(self) -> None:
        assert await InMemoryDocumentStore().get(DocumentId("nope")) is None

    async def test_lookup_by_content_hash_is_owner_scoped(self) -> None:
        """Deduplication must not tell one user that another user already has a document."""
        store = InMemoryDocumentStore()
        await store.put(self._document(owner="alice"), [])

        digest = self._document(owner="alice").content_hash
        assert await store.get_by_hash(digest, owner_id=UserId("alice")) is not None
        assert await store.get_by_hash(digest, owner_id=UserId("bob")) is None

    async def test_listing_active_documents_is_owner_scoped(self) -> None:
        store = InMemoryDocumentStore()
        await store.put(self._document("a", owner="alice"), [])
        await store.put(self._document("b", owner="bob"), [])

        assert [d.id.value for d in await store.list_active(owner_id=UserId("alice"))] == ["a"]

    async def test_superseded_documents_are_excluded_from_the_active_list(self) -> None:
        store = InMemoryDocumentStore()
        await store.put(self._document(version=1), [])
        await store.put(self._document(version=2), [])
        await store.supersede(DocumentId("d1"), old_version=1, new_version=2)

        active = await store.list_active(owner_id=UserId("u1"))
        assert [d.version for d in active] == [2]

    async def test_superseding_an_absent_version_is_a_no_op(self) -> None:
        await InMemoryDocumentStore().supersede(DocumentId("nope"), old_version=1, new_version=2)

    async def test_listing_supports_pagination(self) -> None:
        store = InMemoryDocumentStore()
        for i in range(5):
            await store.put(self._document(f"d{i}"), [])
        page = await store.list_active(owner_id=UserId("u1"), limit=2, offset=2)
        assert len(page) == 2

    async def test_chunks_stream_in_batches_for_a_rebuild(self) -> None:
        """A rebuild must not be bounded by available memory."""
        store = InMemoryDocumentStore()
        await store.put(self._document(), [self._chunk("d1", i) for i in range(7)])

        batches = [batch async for batch in store.iter_all_chunks(batch_size=3)]
        assert [len(b) for b in batches] == [3, 3, 1]

    async def test_streaming_skips_inactive_documents(self) -> None:
        store = InMemoryDocumentStore()
        await store.put(self._document("a"), [self._chunk("a", 0)])
        await store.put(
            self._document("b", status=DocumentStatus.TOMBSTONED), [self._chunk("b", 0)]
        )
        chunks = [c for batch in [b async for b in store.iter_all_chunks()] for c in batch]
        assert {c.document_id.value for c in chunks} == {"a"}

    async def test_streaming_an_empty_store_yields_nothing(self) -> None:
        assert [b async for b in InMemoryDocumentStore().iter_all_chunks()] == []

    async def test_tombstoning_is_owner_scoped_and_counts_chunks(self) -> None:
        store = InMemoryDocumentStore()
        await store.put(self._document(owner="alice"), [self._chunk("d1", 0), self._chunk("d1", 1)])

        assert await store.tombstone(DocumentId("d1"), owner_id=UserId("bob")) == 0
        assert await store.tombstone(DocumentId("d1"), owner_id=UserId("alice")) == 2
        assert await store.list_active(owner_id=UserId("alice")) == []

    async def test_every_corpus_change_advances_only_its_owners_revision(self) -> None:
        """What lets each gateway worker notice that another one changed an owner's corpus
        (F-14): put, supersede, tombstone and purge each move the revision, never backwards."""
        store = InMemoryDocumentStore()
        assert await store.corpus_revisions(["alice", "global"]) == {}

        seen = []
        await store.put(self._document("d1", owner="alice"), [self._chunk("d1", 0)])
        seen.append((await store.corpus_revisions(["alice"]))["alice"])
        await store.put(self._document("d1", version=2, owner="alice"), [self._chunk("d1", 0, 2)])
        await store.supersede(DocumentId("d1"), old_version=1, new_version=2)
        seen.append((await store.corpus_revisions(["alice"]))["alice"])
        await store.tombstone(DocumentId("d1"), owner_id=UserId("alice"))
        seen.append((await store.corpus_revisions(["alice"]))["alice"])
        await store.purge_owner(UserId("alice"))
        seen.append((await store.corpus_revisions(["alice"]))["alice"])

        assert seen == sorted(set(seen))  # strictly increasing
        await store.put(self._document("g1", owner=None), [])
        revisions = await store.corpus_revisions(["alice", "bob", "global"])
        assert set(revisions) == {"alice", "global"}
        assert revisions["alice"] == seen[-1]

    async def test_active_chunks_are_listed_per_owner_key_and_fetched_by_id(self) -> None:
        store = InMemoryDocumentStore()
        await store.put(self._document("a", owner="alice"), [self._chunk("a", 0), self._chunk("a", 1)])
        await store.put(self._document("g", owner=None), [self._chunk("g", 0)])
        await store.put(
            self._document("old", owner="alice", status=DocumentStatus.SUPERSEDED),
            [self._chunk("old", 0)],
        )

        assert set(await store.active_chunk_ids("alice")) == {"a:v1:0", "a:v1:1"}
        assert set(await store.active_chunk_ids("global")) == {"g:v1:0"}
        assert await store.active_chunk_ids("bob") == []
        fetched = await store.active_chunks(["a:v1:1", "old:v1:0", "missing:v1:0"])
        assert [c.id.value for c in fetched] == ["a:v1:1"]


class TestMemoryStore:
    def _record(self, memory_id: str = "m1", *, owner: str = "u1",
                kind: MemoryKind = MemoryKind.SEMANTIC, source: str = "turn:1") -> MemoryRecord:
        return MemoryRecord(
            id=MemoryId(memory_id), kind=kind, owner_id=UserId(owner),
            content=f"content of {memory_id}", provenance=Provenance(source=source),
        )

    async def test_put_and_get(self) -> None:
        store = InMemoryMemoryStore()
        await store.put(self._record())
        assert (await store.get(MemoryId("m1"))).content == "content of m1"

    async def test_an_unknown_record_is_none(self) -> None:
        assert await InMemoryMemoryStore().get(MemoryId("nope")) is None

    async def test_get_many_skips_what_is_absent(self) -> None:
        store = InMemoryMemoryStore()
        await store.put(self._record("m1"))
        found = await store.get_many([MemoryId("m1"), MemoryId("absent")])
        assert [r.id.value for r in found] == ["m1"]

    async def test_listing_is_owner_scoped(self) -> None:
        store = InMemoryMemoryStore()
        await store.put(self._record("a", owner="alice"))
        await store.put(self._record("b", owner="bob"))
        listed = await store.list_for_owner(UserId("alice"))
        assert [r.id.value for r in listed] == ["a"]

    async def test_listing_can_filter_by_kind(self) -> None:
        store = InMemoryMemoryStore()
        await store.put(self._record("a", kind=MemoryKind.SEMANTIC))
        await store.put(self._record("b", kind=MemoryKind.EPISODIC))
        listed = await store.list_for_owner(UserId("u1"), kinds=(MemoryKind.EPISODIC,))
        assert [r.id.value for r in listed] == ["b"]

    async def test_update_replaces_in_place(self) -> None:
        from dataclasses import replace

        store = InMemoryMemoryStore()
        await store.put(self._record())
        await store.update(replace(await store.get(MemoryId("m1")), confidence=0.95))
        assert (await store.get(MemoryId("m1"))).confidence == pytest.approx(0.95)

    async def test_touch_many_bumps_only_the_access_fields(self) -> None:
        """Reinforcement is a field-level update on the *current* row (F-16): it never writes
        back a snapshot, so whatever else changed since the caller read the record survives."""
        from dataclasses import replace
        from datetime import datetime, timezone

        store = InMemoryMemoryStore()
        await store.put(self._record("m1"))
        await store.put(self._record("m2"))
        await store.update(replace(await store.get(MemoryId("m1")),
                                   superseded_by=MemoryId("m9"), confidence=0.9))
        at = datetime(2030, 1, 1, tzinfo=timezone.utc)

        touched = await store.touch_many(
            [MemoryId("m1"), MemoryId("m2"), MemoryId("m1"), MemoryId("gone")],
            owner_id=UserId("u1"), at=at,
        )

        assert touched == 2  # duplicates collapse, absent ids are skipped, never created
        m1, m2 = await store.get(MemoryId("m1")), await store.get(MemoryId("m2"))
        assert (m1.access_count, m2.access_count) == (1, 1)
        assert m1.last_accessed_at == at
        assert m1.superseded_by == MemoryId("m9") and m1.confidence == pytest.approx(0.9)
        assert await store.get(MemoryId("gone")) is None

    async def test_touch_many_is_owner_scoped_and_never_moves_time_backwards(self) -> None:
        from datetime import datetime, timezone

        store = InMemoryMemoryStore()
        await store.put(self._record("a", owner="alice"))
        later = datetime(2030, 1, 1, tzinfo=timezone.utc)
        await store.touch_many([MemoryId("a")], owner_id=UserId("alice"), at=later)

        assert await store.touch_many([MemoryId("a")], owner_id=UserId("bob"), at=later) == 0
        await store.touch_many(
            [MemoryId("a")], owner_id=UserId("alice"), at=datetime(2001, 1, 1, tzinfo=timezone.utc)
        )
        record = await store.get(MemoryId("a"))
        assert record.access_count == 2
        assert record.last_accessed_at == later

    async def test_deletion_is_owner_scoped(self) -> None:
        """A memory id in a URL must not be a capability over another user's data."""
        store = InMemoryMemoryStore()
        await store.put(self._record(owner="alice"))

        assert await store.delete(MemoryId("m1"), owner_id=UserId("bob")) is False
        assert await store.delete(MemoryId("m1"), owner_id=UserId("alice")) is True
        assert await store.get(MemoryId("m1")) is None

    async def test_deleting_an_absent_record_is_false(self) -> None:
        assert await InMemoryMemoryStore().delete(MemoryId("nope"), owner_id=UserId("u1")) is False

    async def test_deletion_by_provenance_is_scoped_and_counted(self) -> None:
        """The primitive behind 'forget everything you learned from that document'."""
        store = InMemoryMemoryStore()
        await store.put(self._record("a", owner="alice", source="doc:1"))
        await store.put(self._record("b", owner="alice", source="doc:2"))
        await store.put(self._record("c", owner="bob", source="doc:1"))

        assert await store.delete_by_provenance(owner_id=UserId("alice"), source="doc:1") == 1
        assert await store.get(MemoryId("c")) is not None

    async def test_counting_is_owner_scoped_and_kind_aware(self) -> None:
        store = InMemoryMemoryStore()
        await store.put(self._record("a", kind=MemoryKind.SEMANTIC))
        await store.put(self._record("b", kind=MemoryKind.EPISODIC))
        await store.put(self._record("c", owner="bob"))

        assert await store.count(UserId("u1")) == 2
        assert await store.count(UserId("u1"), kind=MemoryKind.SEMANTIC) == 1


class TestGraphStore:
    def _entity(self, key: str = "sarah_chen", *, name: str = "Sarah Chen",
                type: str = "person", sources: tuple[str, ...] = ("doc:1",),
                **extra) -> Entity:
        return Entity(key=key, name=name, type=type, sources=sources, **extra)

    def _relation(self, subject: str, predicate: str, obj: str, *, confidence: float = 0.7,
                  sources: tuple[str, ...] = ("doc:1",)) -> Relation:
        return Relation(subject_key=subject, predicate=predicate, object_key=obj,
                        confidence=confidence, sources=sources)

    async def test_apply_and_lookup(self) -> None:
        store = InMemoryGraphStore()
        await store.apply(GraphDelta(entities=(self._entity(),)), owner_id=UserId("u1"))
        assert (await store.get_entity("sarah_chen", owner_id=UserId("u1"))).name == "Sarah Chen"

    async def test_the_graph_is_owner_scoped(self) -> None:
        store = InMemoryGraphStore()
        await store.apply(GraphDelta(entities=(self._entity(),)), owner_id=UserId("alice"))
        assert await store.get_entity("sarah_chen", owner_id=UserId("bob")) is None

    async def test_a_second_mention_merges_rather_than_overwrites(self) -> None:
        """Adding attributes and aliases, not replacing what was already known."""
        store = InMemoryGraphStore()
        await store.apply(
            GraphDelta(entities=(self._entity(attributes={"role": "director"}),)),
            owner_id=UserId("u1"),
        )
        await store.apply(
            GraphDelta(entities=(self._entity(sources=("doc:2",), aliases=("S. Chen",)),)),
            owner_id=UserId("u1"),
        )
        entity = await store.get_entity("sarah_chen", owner_id=UserId("u1"))

        assert entity.attributes["role"] == "director"
        assert "S. Chen" in entity.aliases
        assert set(entity.sources) == {"doc:1", "doc:2"}

    async def test_a_higher_confidence_relation_wins(self) -> None:
        store = InMemoryGraphStore()
        low = self._relation("a", "leads", "b", confidence=0.5)
        high = self._relation("a", "leads", "b", confidence=0.9)
        await store.apply(GraphDelta(relations=(low,)), owner_id=UserId("u1"))
        await store.apply(GraphDelta(relations=(high,)), owner_id=UserId("u1"))

        entities, edges = await store.neighbours("a", owner_id=UserId("u1"))
        assert edges[0].confidence == pytest.approx(0.9)

    async def test_a_lower_confidence_relation_does_not_overwrite(self) -> None:
        store = InMemoryGraphStore()
        await store.apply(
            GraphDelta(relations=(self._relation("a", "leads", "b", confidence=0.9),)),
            owner_id=UserId("u1"),
        )
        await store.apply(
            GraphDelta(relations=(self._relation("a", "leads", "b", confidence=0.2),)),
            owner_id=UserId("u1"),
        )
        _, edges = await store.neighbours("a", owner_id=UserId("u1"))
        assert edges[0].confidence == pytest.approx(0.9)

    async def test_entities_are_found_by_partial_name(self) -> None:
        store = InMemoryGraphStore()
        await store.apply(GraphDelta(entities=(self._entity(),)), owner_id=UserId("u1"))
        assert len(await store.find_entities(owner_id=UserId("u1"), name="chen")) == 1

    async def test_entities_are_found_by_alias(self) -> None:
        store = InMemoryGraphStore()
        await store.apply(
            GraphDelta(entities=(self._entity(aliases=("S. Chen",)),)), owner_id=UserId("u1")
        )
        assert len(await store.find_entities(owner_id=UserId("u1"), name="s. chen")) == 1

    async def test_entities_can_be_filtered_by_type(self) -> None:
        store = InMemoryGraphStore()
        await store.apply(GraphDelta(entities=(
            self._entity("a", name="A", type="person"),
            self._entity("b", name="B", type="organisation"),
        )), owner_id=UserId("u1"))
        found = await store.find_entities(owner_id=UserId("u1"), type="organisation")
        assert [e.key for e in found] == ["b"]

    async def test_results_are_ordered_by_mention_count(self) -> None:
        store = InMemoryGraphStore()
        await store.apply(GraphDelta(entities=(
            self._entity("rare", name="Rare", mention_count=1),
            self._entity("common", name="Common", mention_count=20),
        )), owner_id=UserId("u1"))
        assert (await store.find_entities(owner_id=UserId("u1")))[0].key == "common"

    async def test_traversal_follows_edges_in_both_directions(self) -> None:
        store = InMemoryGraphStore()
        await store.apply(GraphDelta(
            entities=(self._entity("a", name="A"), self._entity("b", name="B")),
            relations=(self._relation("a", "leads", "b"),),
        ), owner_id=UserId("u1"))

        from_subject, _ = await store.neighbours("a", owner_id=UserId("u1"))
        from_object, _ = await store.neighbours("b", owner_id=UserId("u1"))
        assert {e.key for e in from_subject} == {"a", "b"}
        assert {e.key for e in from_object} == {"a", "b"}

    async def test_traversal_depth_is_clamped(self) -> None:
        """An unbounded graph walk driven by model-chosen arguments is a denial-of-service
        primitive."""
        store = InMemoryGraphStore()
        entities = tuple(self._entity(f"n{i}", name=f"N{i}") for i in range(10))
        relations = tuple(self._relation(f"n{i}", "next", f"n{i + 1}") for i in range(9))
        await store.apply(GraphDelta(entities=entities, relations=relations),
                          owner_id=UserId("u1"))

        reachable, _ = await store.neighbours("n0", owner_id=UserId("u1"), depth=99)
        assert len(reachable) <= 4  # n0 plus at most three hops

    async def test_traversal_respects_the_edge_limit(self) -> None:
        store = InMemoryGraphStore()
        entities = tuple(self._entity(f"n{i}", name=f"N{i}") for i in range(12))
        relations = tuple(self._relation("n0", "links", f"n{i}") for i in range(1, 12))
        await store.apply(GraphDelta(entities=entities, relations=relations),
                          owner_id=UserId("u1"))

        _, edges = await store.neighbours("n0", owner_id=UserId("u1"), limit=3)
        assert len(edges) <= 3

    async def test_traversal_from_an_isolated_node_returns_only_itself(self) -> None:
        store = InMemoryGraphStore()
        await store.apply(GraphDelta(entities=(self._entity("lonely", name="L"),)),
                          owner_id=UserId("u1"))
        entities, edges = await store.neighbours("lonely", owner_id=UserId("u1"))
        assert [e.key for e in entities] == ["lonely"]
        assert edges == []

    async def test_reverting_a_source_removes_what_only_it_contributed(self) -> None:
        """Entities corroborated by another source survive; a shared fact is not one source's
        to retract."""
        store = InMemoryGraphStore()
        await store.apply(GraphDelta(
            entities=(self._entity("only", name="Only", sources=("doc:1",)),
                      self._entity("shared", name="Shared", sources=("doc:1",))),
            relations=(self._relation("only", "links", "shared", sources=("doc:1",)),),
        ), owner_id=UserId("u1"))
        await store.apply(
            GraphDelta(entities=(self._entity("shared", name="Shared", sources=("doc:2",)),)),
            owner_id=UserId("u1"),
        )

        removed = await store.revert("doc:1", owner_id=UserId("u1"))
        assert removed == 2  # the entity sourced only from doc:1, plus the relation
        assert await store.get_entity("only", owner_id=UserId("u1")) is None
        assert await store.get_entity("shared", owner_id=UserId("u1")) is not None

    async def test_reverting_is_owner_scoped(self) -> None:
        store = InMemoryGraphStore()
        await store.apply(GraphDelta(entities=(self._entity(),)), owner_id=UserId("alice"))
        assert await store.revert("doc:1", owner_id=UserId("bob")) == 0
        assert await store.get_entity("sarah_chen", owner_id=UserId("alice")) is not None

    async def test_size_reports_both_dimensions(self) -> None:
        store = InMemoryGraphStore()
        await store.apply(GraphDelta(
            entities=(self._entity("a", name="A"), self._entity("b", name="B")),
            relations=(self._relation("a", "links", "b"),),
        ), owner_id=UserId("u1"))
        assert store.size() == (2, 1)
