"""The query paths of the Qdrant and Postgres adapters, against fakes.

The companion file (``test_storage_adapters.py``) covers pure helpers and guard clauses. This one
drives the methods that actually talk to a backend, so the claims made in those adapters' docstrings
are checked rather than asserted:

* ``ensure_index`` creates when absent and **raises** on a mismatch instead of recreating -- the
  legacy behaviour that let a config change, or a bare search, destroy the corpus.
* ``set_alias`` issues **one** atomic call carrying both the delete and the create, so no reader
  ever observes an alias resolving to nothing.
* ``drop_index`` refuses while an alias points at the collection.
* Tenant isolation reaches the query, and the owner predicate reaches the SQL.
* ``put`` replaces a version's chunks inside one transaction rather than accumulating orphans.

Fakes record the calls, so a test can assert on the wire behaviour and not merely on the return
value. What they cannot verify is that Qdrant and Postgres interpret those calls as assumed -- only
the Docker-backed integration test does that, and it is stated here so the coverage number is not
mistaken for more than it is.
"""

from __future__ import annotations

import pytest

from aura_core.adapters.postgres_store import PostgresDocumentStore, PostgresMemoryStore
from aura_core.adapters.qdrant_store import QdrantVectorStore
from aura_core.domain.errors import ConflictError, DependencyUnavailableError
from aura_core.domain.identifiers import ChunkId, DocumentId, UserId
from aura_core.domain.knowledge import Chunk, Document, IndexIdentity
from aura_core.ports.storage import VectorPoint

IDENTITY = IndexIdentity(
    logical_name="aura-knowledge",
    schema_version=1,
    embedding_model_id="bge-small",
    dimension=4,
)


# -- qdrant fakes -------------------------------------------------------------------------


class FakeCollectionInfo:
    def __init__(self, size: int, distance: str = "Cosine") -> None:
        class Vectors:
            pass

        vectors = Vectors()
        vectors.size = size
        vectors.distance = distance

        class Params:
            pass

        params = Params()
        params.vectors = vectors

        class Config:
            pass

        config = Config()
        config.params = params
        self.config = config


class FakeAliasDescription:
    def __init__(self, alias_name: str, collection_name: str) -> None:
        self.alias_name = alias_name
        self.collection_name = collection_name


class FakeAliasResponse:
    def __init__(self, aliases) -> None:
        self.aliases = aliases


class FakeScored:
    def __init__(self, id, score, payload) -> None:
        self.id = id
        self.score = score
        self.payload = payload


class FakeQueryResponse:
    def __init__(self, points) -> None:
        self.points = points


class FakeQdrant:
    def __init__(self, *, collections=(), aliases=(), fail: bool = False) -> None:
        self.collections = {name: FakeCollectionInfo(4) for name in collections}
        self.aliases = list(aliases)
        self.fail = fail
        self.calls: list[tuple] = []
        self.search_result: list = []
        self.count_value = 0

    def _check(self):
        if self.fail:
            raise ConnectionError("qdrant is down")

    async def collection_exists(self, name):
        self._check()
        return name in self.collections

    async def get_collection(self, name):
        self._check()
        return self.collections[name]

    async def create_collection(self, collection_name, vectors_config):
        self._check()
        self.calls.append(("create_collection", collection_name, vectors_config.size))
        self.collections[collection_name] = FakeCollectionInfo(vectors_config.size)

    async def create_payload_index(self, collection_name, field_name, field_schema):
        self._check()
        self.calls.append(("create_payload_index", collection_name, field_name))

    async def delete_collection(self, name):
        self._check()
        self.calls.append(("delete_collection", name))
        self.collections.pop(name, None)

    async def get_aliases(self):
        self._check()
        return FakeAliasResponse(self.aliases)

    async def update_collection_aliases(self, change_aliases_operations):
        self._check()
        self.calls.append(("update_aliases", tuple(type(op).__name__ for op in change_aliases_operations)))
        for op in change_aliases_operations:
            create = getattr(op, "create_alias", None)
            delete = getattr(op, "delete_alias", None)
            if delete is not None:
                self.aliases = [a for a in self.aliases if a.alias_name != delete.alias_name]
            if create is not None:
                self.aliases.append(
                    FakeAliasDescription(create.alias_name, create.collection_name)
                )

    async def upsert(self, collection_name, points, wait=True):
        self._check()
        self.calls.append(("upsert", collection_name, tuple(p.payload.get("_point_key") for p in points)))

    async def delete(self, collection_name, points_selector, wait=True):
        self._check()
        self.calls.append(("delete", collection_name, type(points_selector).__name__))

    async def query_points(self, collection_name, query, limit, query_filter, score_threshold, with_payload):
        self._check()
        self.calls.append(("query_points", collection_name, limit, repr(query_filter)))
        return FakeQueryResponse(self.search_result)

    async def count(self, collection_name, count_filter=None, exact=True):
        self._check()
        self.calls.append(("count", collection_name, repr(count_filter)))

        class R:
            pass

        r = R()
        r.count = self.count_value
        return r

    async def get_collections(self):
        self._check()
        return object()

    async def close(self):
        self.calls.append(("close",))


def _store(**kwargs) -> tuple[QdrantVectorStore, FakeQdrant]:
    store = QdrantVectorStore("http://qdrant:6333")
    fake = FakeQdrant(**kwargs)
    store._client = fake
    return store, fake


# -- ensure_index -------------------------------------------------------------------------


class TestEnsureIndex:
    async def test_it_creates_the_collection_when_absent(self) -> None:
        store, fake = _store()
        await store.ensure_index(IDENTITY)
        created = [c for c in fake.calls if c[0] == "create_collection"]
        assert created == [("create_collection", IDENTITY.physical_name, 4)]

    async def test_it_creates_payload_indexes_for_the_filtered_fields(self) -> None:
        """Without them Qdrant scans every candidate's payload, so tenant filtering becomes the
        dominant cost as the collection grows."""
        store, fake = _store()
        await store.ensure_index(IDENTITY)
        fields = {c[2] for c in fake.calls if c[0] == "create_payload_index"}
        assert fields == {"owner_id", "document_id"}

    async def test_an_existing_matching_collection_is_left_alone(self) -> None:
        store, fake = _store(collections=[IDENTITY.physical_name])
        await store.ensure_index(IDENTITY)
        assert not [c for c in fake.calls if c[0] == "create_collection"]
        assert not [c for c in fake.calls if c[0] == "delete_collection"]

    async def test_a_dimension_mismatch_raises_and_deletes_nothing(self) -> None:
        """The defect this replaces: the legacy path deleted and recreated on mismatch, so a
        config change silently destroyed the corpus."""
        store, fake = _store(collections=[IDENTITY.physical_name])
        fake.collections[IDENTITY.physical_name] = FakeCollectionInfo(768)
        with pytest.raises(ConflictError) as excinfo:
            await store.ensure_index(IDENTITY)
        assert "768" in str(excinfo.value)
        assert not [c for c in fake.calls if c[0] == "delete_collection"]

    async def test_a_distance_mismatch_raises(self) -> None:
        """Scores from two metrics are not comparable, so this must be a new index."""
        store, fake = _store(collections=[IDENTITY.physical_name])
        fake.collections[IDENTITY.physical_name] = FakeCollectionInfo(4, distance="Euclid")
        with pytest.raises(ConflictError):
            await store.ensure_index(IDENTITY)

    async def test_a_concurrent_creator_is_not_an_error(self) -> None:
        """Two workers starting together both find the collection absent and both try."""
        store, fake = _store()

        original = fake.create_collection

        async def racing(collection_name, vectors_config):
            # Simulate another worker winning: the collection appears, then our create fails.
            fake.collections[collection_name] = FakeCollectionInfo(4)
            raise RuntimeError("already exists")

        fake.create_collection = racing
        await store.ensure_index(IDENTITY)  # must not raise

    async def test_a_real_failure_still_surfaces(self) -> None:
        store, fake = _store()

        async def broken(collection_name, vectors_config):
            raise RuntimeError("disk full")

        fake.create_collection = broken
        with pytest.raises(DependencyUnavailableError):
            await store.ensure_index(IDENTITY)


# -- aliases ------------------------------------------------------------------------------


class TestAliases:
    async def test_promotion_is_a_single_atomic_call(self) -> None:
        """Two calls would leave a window where the alias resolves to nothing, and the pipeline
        would report that as an honest empty result rather than as an outage."""
        store, fake = _store(collections=[IDENTITY.physical_name])
        await store.set_alias("knowledge", IDENTITY)
        updates = [c for c in fake.calls if c[0] == "update_aliases"]
        assert len(updates) == 1

    async def test_repointing_carries_the_delete_and_the_create_together(self) -> None:
        old = IndexIdentity(
            logical_name="aura-knowledge",
            schema_version=1,
            embedding_model_id="bge-small",
            dimension=4,
        )
        new = old.for_model("bge-base", 4)
        store, fake = _store(
            collections=[old.physical_name, new.physical_name],
            aliases=[FakeAliasDescription("knowledge", old.physical_name)],
        )
        await store.set_alias("knowledge", new)
        updates = [c for c in fake.calls if c[0] == "update_aliases"]
        assert len(updates) == 1
        assert "DeleteAliasOperation" in updates[0][1]
        assert "CreateAliasOperation" in updates[0][1]

    async def test_pointing_an_alias_at_a_missing_index_is_refused(self) -> None:
        """Promoting a missing index would make every search return nothing -- reported as an
        empty corpus rather than as a failed promotion."""
        store, _ = _store()
        with pytest.raises(ConflictError) as excinfo:
            await store.set_alias("knowledge", IDENTITY)
        assert "does not exist" in str(excinfo.value)

    async def test_resolve_alias_round_trips_to_an_identity(self) -> None:
        store, _ = _store(
            collections=[IDENTITY.physical_name],
            aliases=[FakeAliasDescription("knowledge", IDENTITY.physical_name)],
        )
        resolved = await store.resolve_alias("knowledge")
        assert resolved is not None
        assert resolved.dimension == 4

    async def test_an_unknown_alias_resolves_to_none(self) -> None:
        store, _ = _store()
        assert await store.resolve_alias("nope") is None

    async def test_resolved_identity_matches_the_raw_embedder_id_that_produced_it(self) -> None:
        """A real HuggingFace-style model id (uppercase, '/', '.') must still match after the
        lossy physical-name round trip -- this is exactly what production's sentence-transformers
        embedder uses (``BAAI/bge-small-en-v1.5``), and what ``HashEmbeddingProvider``'s plain
        ``local-embeddings`` id -- already its own slug -- never exercised. Before the fix, this
        assertion failed for every real model id: resolve_for_read would raise IndexNotReadyError
        on every query, forever, and retrieval would silently return nothing."""
        identity = IndexIdentity(
            logical_name="aura-knowledge", schema_version=1,
            embedding_model_id="BAAI/bge-small-en-v1.5", dimension=384,
        )
        store, _ = _store(
            collections=[identity.physical_name],
            aliases=[FakeAliasDescription("knowledge", identity.physical_name)],
        )
        resolved = await store.resolve_alias("knowledge")
        assert resolved is not None
        assert resolved.matches_embedding("BAAI/bge-small-en-v1.5", 384)

    async def test_rollback_is_the_same_operation_as_promotion(self) -> None:
        """ADR-007's claim, exercised: pointing back at the previous generation is one alias
        update, no data movement."""
        old = IDENTITY
        new = IDENTITY.for_model("bge-base", 4)
        store, fake = _store(
            collections=[old.physical_name, new.physical_name],
            aliases=[FakeAliasDescription("knowledge", old.physical_name)],
        )
        await store.set_alias("knowledge", new)
        await store.set_alias("knowledge", old)
        assert fake.aliases[-1].collection_name == old.physical_name


class TestDropIndex:
    async def test_it_refuses_while_an_alias_points_at_it(self) -> None:
        """The guard between retiring a superseded generation and deleting the one serving
        traffic."""
        store, fake = _store(
            collections=[IDENTITY.physical_name],
            aliases=[FakeAliasDescription("knowledge", IDENTITY.physical_name)],
        )
        with pytest.raises(ConflictError) as excinfo:
            await store.drop_index(IDENTITY)
        assert "knowledge" in str(excinfo.value)
        assert not [c for c in fake.calls if c[0] == "delete_collection"]

    async def test_it_drops_an_unaliased_index(self) -> None:
        store, fake = _store(collections=[IDENTITY.physical_name])
        await store.drop_index(IDENTITY)
        assert ("delete_collection", IDENTITY.physical_name) in fake.calls


# -- reads and writes ---------------------------------------------------------------------


class TestUpsertAndSearch:
    async def test_the_callers_point_id_survives_the_round_trip(self) -> None:
        """Qdrant ids must be int or UUID, so non-UUID keys are hashed. Search must still return
        the id the caller gave us, not the hash."""
        store, fake = _store(collections=[IDENTITY.physical_name])
        await store.upsert(IDENTITY, [VectorPoint("doc-1:v1:0", [0.1, 0.2, 0.3, 0.4], {})])
        assert ("upsert", IDENTITY.physical_name, ("doc-1:v1:0",)) in fake.calls

        fake.search_result = [FakeScored(12345, 0.9, {"_point_key": "doc-1:v1:0", "owner_id": "u1"})]
        results = await store.search(IDENTITY, [0.1, 0.2, 0.3, 0.4], limit=5, owner_id=UserId("u1"))
        assert results[0][0] == "doc-1:v1:0"

    async def test_the_internal_key_is_stripped_from_the_returned_payload(self) -> None:
        store, fake = _store(collections=[IDENTITY.physical_name])
        fake.search_result = [FakeScored(1, 0.9, {"_point_key": "k", "title": "t"})]
        results = await store.search(IDENTITY, [0.0] * 4, limit=1, owner_id=None)
        assert "_point_key" not in results[0][2]
        assert results[0][2]["title"] == "t"

    async def test_the_owner_filter_reaches_the_query_not_the_result(self) -> None:
        """Post-filtering a top-k result would let another tenant's document displace a legitimate
        one out of the k -- a correctness bug that reads as a relevance problem."""
        store, fake = _store(collections=[IDENTITY.physical_name])
        await store.search(IDENTITY, [0.0] * 4, limit=5, owner_id=UserId("u1"))
        query_calls = [c for c in fake.calls if c[0] == "query_points"]
        assert "u1" in query_calls[0][3]

    async def test_a_score_threshold_is_passed_through(self) -> None:
        store, fake = _store(collections=[IDENTITY.physical_name])
        fake.search_result = []
        results = await store.search(
            IDENTITY, [0.0] * 4, limit=5, owner_id=None, score_threshold=0.8
        )
        assert results == []

    async def test_a_backend_failure_is_a_dependency_error(self) -> None:
        store, _ = _store(collections=[IDENTITY.physical_name], fail=True)
        with pytest.raises(DependencyUnavailableError):
            await store.search(IDENTITY, [0.0] * 4, limit=1, owner_id=None)


class TestDeleteByFilter:
    async def test_it_counts_before_deleting(self) -> None:
        """Qdrant does not report how many points a filtered delete removed, so the count is taken
        first -- the caller uses it to keep the document store and the index consistent."""
        store, fake = _store(collections=[IDENTITY.physical_name])
        fake.count_value = 3
        removed = await store.delete_by_filter(
            IDENTITY, filters={"document_id": "d1"}, owner_id=UserId("u1")
        )
        assert removed == 3
        order = [c[0] for c in fake.calls if c[0] in ("count", "delete")]
        assert order == ["count", "delete"]

    async def test_an_owner_only_call_is_allowed(self) -> None:
        """One condition is enough; it is the *unconditional* call that is refused."""
        store, fake = _store(collections=[IDENTITY.physical_name])
        fake.count_value = 1
        assert await store.delete_by_filter(IDENTITY, filters={}, owner_id=UserId("u1")) == 1


class TestCount:
    async def test_counting_without_an_owner_counts_everything(self) -> None:
        store, fake = _store(collections=[IDENTITY.physical_name])
        fake.count_value = 42
        assert await store.count(IDENTITY) == 42

    async def test_counting_with_an_owner_filters(self) -> None:
        store, fake = _store(collections=[IDENTITY.physical_name])
        fake.count_value = 7
        assert await store.count(IDENTITY, owner_id=UserId("u1")) == 7
        count_calls = [c for c in fake.calls if c[0] == "count"]
        assert "u1" in count_calls[0][2]


class TestPing:
    async def test_it_answers_false_when_unreachable(self) -> None:
        store, _ = _store(fail=True)
        assert await store.ping() is False

    async def test_it_answers_true_when_reachable(self) -> None:
        store, _ = _store()
        assert await store.ping() is True


# -- postgres -----------------------------------------------------------------------------


class FakeTransaction:
    def __init__(self, conn) -> None:
        self.conn = conn

    async def __aenter__(self):
        self.conn.calls.append(("BEGIN",))
        return self

    async def __aexit__(self, *exc):
        self.conn.calls.append(("COMMIT" if exc[0] is None else "ROLLBACK",))
        return False


class FakePgConn:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.rows: list = []
        self.row = None
        self.value = 0

    def transaction(self):
        return FakeTransaction(self)

    async def execute(self, sql, *params):
        # Normalised whitespace, not truncated: assertions look for predicates such as
        # `owner_id IS NULL` that appear past any fixed cut-off.
        self.calls.append(("execute", " ".join(sql.split()), params))
        return "DELETE 1"

    async def executemany(self, sql, args):
        self.calls.append(("executemany", " ".join(sql.split()), len(args)))

    async def fetch(self, sql, *params):
        self.calls.append(("fetch", " ".join(sql.split()), params))
        return self.rows

    async def fetchrow(self, sql, *params):
        self.calls.append(("fetchrow", " ".join(sql.split()), params))
        return self.row

    async def fetchval(self, sql, *params):
        self.calls.append(("fetchval", " ".join(sql.split()), params))
        return self.value


class FakePgPool:
    def __init__(self, conn: FakePgConn) -> None:
        self._conn = conn
        self._pool = self

    async def _get_pool(self):
        return self

    def acquire(self):
        conn = self._conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


def _doc_store() -> tuple[PostgresDocumentStore, FakePgConn]:
    conn = FakePgConn()
    store = PostgresDocumentStore("postgresql://u:p@h/db")
    store._pool = FakePgPool(conn)
    return store, conn


class TestDocumentPut:
    def _document(self) -> tuple[Document, list[Chunk]]:
        doc = Document(id=DocumentId("d1"), version=2, title="T", content="body")
        chunks = [
            Chunk(
                id=ChunkId("d1:v2:0"),
                document_id=DocumentId("d1"),
                document_version=2,
                index=0,
                content="body",
            )
        ]
        return doc, chunks

    async def test_the_document_and_chunks_are_written_in_one_transaction(self) -> None:
        """A document whose chunks are missing is worse than no document: retrievable metadata
        with nothing behind it, which a rebuild treats as legitimately empty."""
        store, conn = _doc_store()
        doc, chunks = self._document()
        await store.put(doc, chunks)
        kinds = [c[0] for c in conn.calls]
        assert kinds[0] == "BEGIN"
        assert kinds[-1] == "COMMIT"

    async def test_existing_chunks_for_the_version_are_replaced_not_merged(self) -> None:
        """A re-put means the chunker ran again; leaving the previous run's chunks would
        double-count content in retrieval."""
        store, conn = _doc_store()
        doc, chunks = self._document()
        await store.put(doc, chunks)
        deletes = [c for c in conn.calls if c[0] == "execute" and "DELETE FROM aura_chunks" in c[1]]
        assert len(deletes) == 1
        assert deletes[0][2] == ("d1", 2)

    async def test_the_content_hash_is_persisted_for_idempotency_lookups(self) -> None:
        store, conn = _doc_store()
        doc, chunks = self._document()
        await store.put(doc, chunks)
        insert = [c for c in conn.calls if c[0] == "execute" and "INSERT INTO aura_documents" in c[1]]
        assert doc.content_hash in insert[0][2]

    async def test_a_failure_rolls_back(self) -> None:
        store, conn = _doc_store()
        doc, chunks = self._document()

        async def broken(sql, args):
            raise RuntimeError("constraint violation")

        conn.executemany = broken
        with pytest.raises(DependencyUnavailableError):
            await store.put(doc, chunks)
        assert ("ROLLBACK",) in conn.calls


class TestTombstoneIsScopedByOwner:
    async def test_the_owner_predicate_is_in_the_sql(self) -> None:
        """A tombstone that forgot it would let one user delete another's document."""
        store, conn = _doc_store()
        conn.value = 4
        removed = await store.tombstone(DocumentId("d1"), owner_id=UserId("u1"))
        assert removed == 4
        updates = [c for c in conn.calls if c[0] == "execute" and "tombstoned" in c[1]]
        assert updates[0][2] == ("d1", "u1")

    async def test_the_chunk_count_is_taken_inside_the_transaction(self) -> None:
        """Counting afterwards would race a concurrent ingestion and leave retrievable chunks
        behind for a document the user asked to delete."""
        store, conn = _doc_store()
        conn.value = 2
        await store.tombstone(DocumentId("d1"), owner_id=UserId("u1"))
        kinds = [c[0] for c in conn.calls]
        assert kinds[0] == "BEGIN"
        assert "fetchval" in kinds
        assert kinds.index("fetchval") < kinds.index("COMMIT")

    async def test_a_global_document_uses_an_is_null_predicate(self) -> None:
        store, conn = _doc_store()
        await store.tombstone(DocumentId("d1"), owner_id=None)
        updates = [c for c in conn.calls if c[0] == "execute" and "tombstoned" in c[1]]
        assert "owner_id IS NULL" in updates[0][1]


class TestIterAllChunksExcludesInactiveVersions:
    async def test_the_query_joins_on_active_documents(self) -> None:
        """Without the join a rebuild would index every historical version, and retrieval would
        return content the user had already replaced."""
        store, conn = _doc_store()

        class CursorConn(FakePgConn):
            def cursor(self, sql, prefetch=None):
                self.calls.append(("cursor", " ".join(sql.split())))

                class _Cursor:
                    def __aiter__(self_inner):
                        async def gen():
                            return
                            yield

                        return gen()

                return _Cursor()

        conn2 = CursorConn()
        store._pool = FakePgPool(conn2)
        batches = [b async for b in store.iter_all_chunks()]
        assert batches == []
        cursor_calls = [c for c in conn2.calls if c[0] == "cursor"]
        assert "d.status = 'active'" in cursor_calls[0][1]
        assert "JOIN aura_documents" in cursor_calls[0][1]


class TestMemoryPutAndUpdateShareOneStatement:
    async def test_update_writes_the_same_statement_as_put(self) -> None:
        """Two statements that must stay in step is a defect waiting to happen: a column present in
        one but not the other silently drops data on whichever path testing missed."""
        from aura_core.domain.identifiers import MemoryId
        from aura_core.domain.memory import MemoryKind, MemoryRecord, Provenance

        record = MemoryRecord(
            id=MemoryId("m1"),
            kind=MemoryKind.SEMANTIC,
            owner_id=UserId("u1"),
            content="fact",
            provenance=Provenance(source="test"),
        )
        conn = FakePgConn()
        store = PostgresMemoryStore("postgresql://u:p@h/db")
        store._pool = FakePgPool(conn)

        await store.put(record)
        await store.update(record)
        statements = [c[1] for c in conn.calls if c[0] == "execute"]
        assert len(statements) == 2
        assert statements[0] == statements[1]


class TestMemoryCount:
    async def test_counting_without_a_kind(self) -> None:
        conn = FakePgConn()
        conn.value = 11
        store = PostgresMemoryStore("postgresql://u:p@h/db")
        store._pool = FakePgPool(conn)
        assert await store.count(UserId("u1")) == 11

    async def test_counting_with_a_kind_filters(self) -> None:
        from aura_core.domain.memory import MemoryKind

        conn = FakePgConn()
        conn.value = 3
        store = PostgresMemoryStore("postgresql://u:p@h/db")
        store._pool = FakePgPool(conn)
        assert await store.count(UserId("u1"), kind=MemoryKind.SEMANTIC) == 3
        assert conn.calls[-1][2] == ("u1", "semantic")


# -- row mapping --------------------------------------------------------------------------


class TestRowMapping:
    """Reconstructing domain objects from database rows.

    Worth covering directly because a wrong mapping here is silent data corruption rather than an
    error: the read succeeds, the object is well-formed, and the value is wrong. The JSONB columns
    are the specific hazard -- asyncpg hands them back as `str` or as an already-decoded object
    depending on codec configuration, so both shapes have to work.
    """

    @staticmethod
    def _doc_row(**overrides):
        from datetime import datetime, timezone

        now = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)
        row = {
            "id": "d1",
            "version": 3,
            "title": "Handbook",
            "content": "body text",
            "content_hash": "abc",
            "source_type": "drive",
            "source_uri": "drive://x",
            "owner_id": "u1",
            "trust_tier": 2,
            "status": "active",
            "language": "en",
            "created_at": now,
            "updated_at": now,
            "metadata": '{"pages": 4}',
        }
        row.update(overrides)
        return row

    def test_a_document_row_maps_every_field(self) -> None:
        from aura_core.domain.knowledge import DocumentStatus, SourceType, TrustTier

        doc = PostgresDocumentStore._document(self._doc_row())
        assert doc.id.value == "d1"
        assert doc.version == 3
        assert doc.source_type is SourceType.DRIVE
        assert doc.trust_tier is TrustTier.VERIFIED
        assert doc.status is DocumentStatus.ACTIVE
        assert doc.owner_id.value == "u1"
        assert doc.metadata == {"pages": 4}

    def test_a_null_owner_becomes_a_global_document(self) -> None:
        """`owner_id IS NULL` is how a global document is stored, and `is_global` is what the
        retrieval filters key off."""
        doc = PostgresDocumentStore._document(self._doc_row(owner_id=None))
        assert doc.owner_id is None
        assert doc.is_global is True

    def test_jsonb_metadata_works_decoded_or_as_text(self) -> None:
        decoded = PostgresDocumentStore._document(self._doc_row(metadata={"pages": 4}))
        as_text = PostgresDocumentStore._document(self._doc_row(metadata='{"pages": 4}'))
        assert decoded.metadata == as_text.metadata == {"pages": 4}

    def test_a_naive_timestamp_is_made_aware(self) -> None:
        """The domain compares these to compute decay and expiry; mixing naive and aware raises
        TypeError mid-request."""
        from datetime import datetime

        doc = PostgresDocumentStore._document(
            self._doc_row(created_at=datetime(2026, 3, 1, 9, 0))
        )
        assert doc.created_at.tzinfo is not None

    @staticmethod
    def _chunk_row(**overrides):
        row = {
            "id": "d1:v3:2",
            "document_id": "d1",
            "document_version": 3,
            "idx": 2,
            "content": "fragment",
            "heading_path": '["Chapter 1", "Section A"]',
            "token_estimate": 42,
            "owner_id": "u1",
            "trust_tier": 1,
            "metadata": "{}",
        }
        row.update(overrides)
        return row

    def test_a_chunk_row_maps_the_heading_path_to_a_tuple(self) -> None:
        """The domain declares it as a tuple, and Chunk is frozen with slots -- a list would make
        the record unhashable in the places that key on it."""
        chunk = PostgresDocumentStore._chunk(self._chunk_row())
        assert chunk.heading_path == ("Chapter 1", "Section A")
        assert isinstance(chunk.heading_path, tuple)

    def test_the_idx_column_maps_to_the_index_field(self) -> None:
        """The column is `idx` because `index` is reserved; a mismatch here silently reorders
        citations."""
        chunk = PostgresDocumentStore._chunk(self._chunk_row(idx=7))
        assert chunk.index == 7

    def test_an_empty_heading_path_is_an_empty_tuple(self) -> None:
        chunk = PostgresDocumentStore._chunk(self._chunk_row(heading_path="[]"))
        assert chunk.heading_path == ()

    def test_a_chunk_renders_its_citation(self) -> None:
        """End-to-end check that the mapping preserves enough provenance to cite without a second
        lookup, which is what makes groundedness measurable."""
        chunk = PostgresDocumentStore._chunk(self._chunk_row())
        assert "d1@v3" in chunk.citation()

    def test_a_memory_row_reconstructs_its_provenance(self) -> None:
        """Provenance is what makes 'forget everything from X' and extractor rollback possible, so
        every field has to survive the round trip."""
        from datetime import datetime, timezone

        now = datetime(2026, 3, 1, tzinfo=timezone.utc)
        row = {
            "id": "m1",
            "kind": "semantic",
            "owner_id": "u1",
            "content": "the user studies CS",
            "subject": "study",
            "confidence": 0.9,
            "embedding_ref": "vec-1",
            "session_id": "s1",
            "created_at": now,
            "last_accessed_at": now,
            "access_count": 3,
            "superseded_by": None,
            "expires_at": None,
            "prov_source": "conversation",
            "prov_turn_id": "t1",
            "prov_session_id": "s1",
            "prov_extractor": "fact-extractor",
            "prov_extractor_ver": "2.1",
            "prov_document_ref": "d1",
            "prov_recorded_at": now,
            "metadata": "{}",
        }
        record = PostgresMemoryStore._record(row)
        assert record.provenance.source == "conversation"
        assert record.provenance.extractor_version == "2.1"
        assert record.provenance.turn_id.value == "t1"
        assert record.subject == "study"
        assert record.access_count == 3
        assert record.is_active is True

    def test_a_superseded_memory_is_not_active(self) -> None:
        from datetime import datetime, timezone

        now = datetime(2026, 3, 1, tzinfo=timezone.utc)
        row = {
            "id": "m1", "kind": "semantic", "owner_id": "u1", "content": "old fact",
            "subject": None, "confidence": 0.5, "embedding_ref": None, "session_id": None,
            "created_at": now, "last_accessed_at": now, "access_count": 0,
            "superseded_by": "m2", "expires_at": None,
            "prov_source": "test", "prov_turn_id": None, "prov_session_id": None,
            "prov_extractor": None, "prov_extractor_ver": None, "prov_document_ref": None,
            "prov_recorded_at": now, "metadata": "{}",
        }
        record = PostgresMemoryStore._record(row)
        assert record.superseded_by.value == "m2"
        assert record.is_active is False

    def test_the_params_tuple_matches_the_upsert_placeholder_count(self) -> None:
        """The statement has 21 placeholders. A mismatch is a runtime error on every write, and the
        two live far enough apart in the file to drift."""
        import re

        from aura_core.domain.identifiers import MemoryId
        from aura_core.domain.memory import MemoryKind, MemoryRecord, Provenance

        record = MemoryRecord(
            id=MemoryId("m1"),
            kind=MemoryKind.SEMANTIC,
            owner_id=UserId("u1"),
            content="fact",
            provenance=Provenance(source="test"),
        )
        placeholders = {int(n) for n in re.findall(r"\$(\d+)", PostgresMemoryStore._UPSERT)}
        assert len(PostgresMemoryStore._params(record)) == max(placeholders)
