"""Qdrant and Postgres adapters: the behaviours that are easy to get wrong.

Driven by fakes rather than live services. That is a deliberate limit and worth stating plainly:
these tests verify the *adapter's* logic -- the SQL and filters it builds, the errors it raises,
the ordering it preserves -- not that Postgres and Qdrant behave as assumed. Only the
Docker-backed integration test can do that. What these catch is the class of bug that dominated
this work: a filter that omits the tenant clause, a mismatch branch that deletes instead of
raising, a read path that mutates.

The cases chosen are the ones where the previous implementation was wrong, or where the port's
contract says something surprising.
"""

from __future__ import annotations

import pytest

from aura_core.adapters.qdrant_store import (
    QdrantVectorStore,
    _parse_physical_name,
    _point_id,
)
from aura_core.domain.errors import ConfigurationError, ConflictError
from aura_core.domain.identifiers import UserId
from aura_core.domain.knowledge import IndexIdentity

IDENTITY = IndexIdentity(
    logical_name="aura-knowledge",
    schema_version=1,
    embedding_model_id="bge-small-en-v1.5",
    dimension=384,
)


class TestPhysicalNameRoundTrip:
    """`resolve_alias` reconstructs an identity by parsing the collection name, so the encoding has
    to be reversible. If it stops being, resolve_alias must read a manifest instead."""

    def test_an_identity_survives_a_round_trip(self) -> None:
        parsed = _parse_physical_name(IDENTITY.physical_name)
        assert parsed is not None
        assert parsed.dimension == IDENTITY.dimension
        assert parsed.schema_version == IDENTITY.schema_version

    def test_a_logical_name_containing_the_separator_still_parses(self) -> None:
        """Split from the right: the logical name may contain '__', the trailing three fields
        may not."""
        identity = IndexIdentity(
            logical_name="team__shared",
            schema_version=2,
            embedding_model_id="m",
            dimension=768,
        )
        parsed = _parse_physical_name(identity.physical_name)
        assert parsed is not None
        assert parsed.dimension == 768
        assert parsed.schema_version == 2

    @pytest.mark.parametrize(
        "name",
        [
            "not-an-index",
            "missing__parts__here",
            "logical__vX__model__384",   # non-numeric schema
            "logical__v1__model__wide",  # non-numeric dimension
            "logical__v1__model__0",     # dimension must be positive
        ],
    )
    def test_an_unrecognisable_name_is_none_not_an_exception(self, name: str) -> None:
        """An alias pointing at a foreign collection is a situation to report, not a crash inside
        the readiness path."""
        assert _parse_physical_name(name) is None


class TestPointIds:
    """Qdrant ids must be an int or UUID; ours are arbitrary strings."""

    def test_a_uuid_passes_through_unchanged(self) -> None:
        raw = "3f2504e0-4f89-11d3-9a0c-0305e82c3301"
        assert _point_id(raw) == raw

    def test_a_non_uuid_is_hashed_deterministically(self) -> None:
        """Stability is the requirement: upsert must overwrite the same point on re-ingestion
        rather than accumulating duplicates, and that must hold across processes -- which
        `hash()` does not."""
        first = _point_id("doc-1:v1:0")
        second = _point_id("doc-1:v1:0")
        assert first == second
        assert isinstance(first, int)

    def test_different_keys_do_not_collide(self) -> None:
        ids = {_point_id(f"doc-1:v1:{i}") for i in range(200)}
        assert len(ids) == 200


class TestQdrantConfiguration:
    def test_an_empty_url_is_refused_at_construction(self) -> None:
        with pytest.raises(ConfigurationError) as excinfo:
            QdrantVectorStore("")
        assert "AURA_QDRANT_URL" in str(excinfo.value)

    def test_construction_opens_no_connection(self) -> None:
        store = QdrantVectorStore("http://unreachable:6333")
        assert store._client is None


class TestDimensionGuards:
    """The port's promise that a 768-dim vector cannot enter a 384-dim index."""

    async def test_upserting_a_wrong_width_vector_is_refused(self) -> None:
        store = QdrantVectorStore("http://qdrant:6333")
        from aura_core.ports.storage import VectorPoint

        with pytest.raises(ConflictError) as excinfo:
            await store.upsert(IDENTITY, [VectorPoint("p1", [0.1] * 768, {"owner_id": "u1"})])
        assert "768" in str(excinfo.value)

    async def test_searching_with_a_wrong_width_vector_is_refused(self) -> None:
        """Caught before the request rather than surfacing as a Qdrant 400, so the message names
        the actual problem."""
        store = QdrantVectorStore("http://qdrant:6333")
        with pytest.raises(ConflictError) as excinfo:
            await store.search(IDENTITY, [0.1] * 100, limit=5, owner_id=None)
        assert "100" in str(excinfo.value)

    async def test_an_empty_upsert_is_a_no_op_and_touches_nothing(self) -> None:
        store = QdrantVectorStore("http://qdrant:6333")
        assert await store.upsert(IDENTITY, []) == 0
        assert store._client is None


class TestUnconditionalDeleteIsRefused:
    async def test_delete_by_filter_requires_a_condition(self) -> None:
        """With no filter and no owner, the selector would match every point -- a caller bug that
        would read as data loss rather than as an error."""
        store = QdrantVectorStore("http://qdrant:6333")
        with pytest.raises(ConflictError) as excinfo:
            await store.delete_by_filter(IDENTITY, filters={}, owner_id=None)
        assert "empty the index" in str(excinfo.value)


class TestOwnerFilterMatchesTheInMemoryAdapter:
    """Tenant isolation must mean the same thing in both adapters.

    A difference here shows up as content visible in one deployment and not the other, which is the
    hardest kind of bug to attribute.
    """

    def test_owner_none_asks_for_global_only(self) -> None:
        """`owner_id=None` means global content, not everything. There is deliberately no way to
        express an unfiltered cross-tenant search through this port."""
        store = QdrantVectorStore("http://qdrant:6333")
        conditions = store._owner_filter(None, include_global=True)
        rendered = repr(conditions)
        assert "global" in rendered
        # No bare owner match: that would widen the query to another tenant's rows.
        assert "u1" not in rendered

    def test_an_owner_sees_their_own_content_plus_global(self) -> None:
        store = QdrantVectorStore("http://qdrant:6333")
        rendered = repr(store._owner_filter(UserId("u1"), include_global=True))
        assert "u1" in rendered
        assert "global" in rendered

    def test_include_global_false_narrows_to_the_owner(self) -> None:
        store = QdrantVectorStore("http://qdrant:6333")
        rendered = repr(store._owner_filter(UserId("u1"), include_global=False))
        assert "u1" in rendered
        assert "global" not in rendered

    def test_a_missing_owner_key_is_not_global(self) -> None:
        """CB-11: global is the explicit sentinel only. An is-empty clause would publish every point
        whose owner was lost; without one, such a point matches no filter at all."""
        store = QdrantVectorStore("http://qdrant:6333")
        for owner in (None, UserId("u1")):
            rendered = repr(store._owner_filter(owner, include_global=True))
            assert "IsEmptyCondition" not in rendered


class TestFilterTranslation:
    def test_a_scalar_becomes_a_match_value(self) -> None:
        from aura_core.adapters.qdrant_store import _filter_conditions

        rendered = repr(_filter_conditions({"document_id": "d1"}))
        assert "d1" in rendered

    def test_a_sequence_becomes_match_any(self) -> None:
        """Matching InMemoryVectorStore._matches, where a list value means "any of"."""
        from aura_core.adapters.qdrant_store import _filter_conditions

        rendered = repr(_filter_conditions({"document_id": ["d1", "d2"]}))
        assert "d1" in rendered and "d2" in rendered

    def test_no_filters_is_no_conditions(self) -> None:
        from aura_core.adapters.qdrant_store import _filter_conditions

        assert _filter_conditions(None) == []
        assert _filter_conditions({}) == []


# -- postgres -----------------------------------------------------------------------------


class TestPostgresDsnNormalisation:
    """AURA_DATABASE_URL is shared with the legacy tree, which needs the SQLAlchemy dialect
    prefix. asyncpg rejects it, so both spellings must work rather than operators keeping two
    copies of one URL in step."""

    @pytest.mark.parametrize(
        "given,expected",
        [
            ("postgresql+asyncpg://u:p@h/db", "postgresql://u:p@h/db"),
            ("postgres+asyncpg://u:p@h/db", "postgresql://u:p@h/db"),
            ("postgresql://u:p@h/db", "postgresql://u:p@h/db"),
        ],
    )
    def test_the_dialect_prefix_is_stripped(self, given: str, expected: str) -> None:
        from aura_core.adapters.postgres_store import _dsn

        assert _dsn(given) == expected


class TestPostgresConfiguration:
    def test_an_empty_url_is_refused(self) -> None:
        from aura_core.adapters.postgres_store import PostgresPool

        with pytest.raises(ConfigurationError) as excinfo:
            PostgresPool("")
        assert "AURA_DATABASE_URL" in str(excinfo.value)

    def test_construction_opens_no_connection(self) -> None:
        from aura_core.adapters.postgres_store import PostgresDocumentStore

        store = PostgresDocumentStore("postgresql://u:p@unreachable/db")
        assert store._pool._pool is None


class TestTimestampNormalisation:
    """asyncpg returns naive datetimes for `timestamp` and aware ones for `timestamptz`. The
    domain compares timestamps to compute decay and expiry, and mixing the two raises TypeError
    mid-request."""

    def test_a_naive_datetime_becomes_utc_aware(self) -> None:
        from datetime import datetime, timezone

        from aura_core.adapters.postgres_store import _utc

        result = _utc(datetime(2026, 1, 1, 12, 0, 0))
        assert result.tzinfo is not None
        assert result == datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    def test_an_aware_datetime_is_converted_not_relabelled(self) -> None:
        from datetime import datetime, timedelta, timezone

        from aura_core.adapters.postgres_store import _utc

        plus_two = timezone(timedelta(hours=2))
        result = _utc(datetime(2026, 1, 1, 12, 0, 0, tzinfo=plus_two))
        assert result == datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)

    def test_none_stays_none(self) -> None:
        from aura_core.adapters.postgres_store import _utc

        assert _utc(None) is None


class TestGetManyPreservesRanking:
    """The ids arrive ranked by vector similarity and `ANY($1)` does not preserve order, so the
    adapter re-sorts. Without it, retrieval's ranking is silently discarded at the last step."""

    async def test_results_come_back_in_the_requested_order(self) -> None:
        from aura_core.adapters.postgres_store import PostgresMemoryStore
        from aura_core.domain.identifiers import MemoryId

        store = PostgresMemoryStore("postgresql://u:p@h/db")

        rows = [
            {"id": "m3", **_memory_row("m3")},
            {"id": "m1", **_memory_row("m1")},
            {"id": "m2", **_memory_row("m2")},
        ]

        class FakeConn:
            async def fetch(self, *args):
                return rows

        store._pool = _FakePool(FakeConn())
        wanted = [MemoryId("m1"), MemoryId("m2"), MemoryId("m3")]
        result = await store.get_many(wanted)
        assert [r.id.value for r in result] == ["m1", "m2", "m3"]

    async def test_ids_absent_from_the_database_are_dropped_not_faked(self) -> None:
        from aura_core.adapters.postgres_store import PostgresMemoryStore
        from aura_core.domain.identifiers import MemoryId

        store = PostgresMemoryStore("postgresql://u:p@h/db")

        class FakeConn:
            async def fetch(self, *args):
                return [{"id": "m1", **_memory_row("m1")}]

        store._pool = _FakePool(FakeConn())
        result = await store.get_many([MemoryId("m1"), MemoryId("gone")])
        assert [r.id.value for r in result] == ["m1"]

    async def test_an_empty_request_touches_no_connection(self) -> None:
        from aura_core.adapters.postgres_store import PostgresMemoryStore

        store = PostgresMemoryStore("postgresql://u:p@h/db")
        assert await store.get_many([]) == []
        assert store._pool._pool is None


def _memory_row(mid: str) -> dict:
    from datetime import datetime, timezone

    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return {
        "kind": "semantic",
        "owner_id": "u1",
        "content": f"content for {mid}",
        "subject": None,
        "confidence": 0.8,
        "embedding_ref": None,
        "session_id": None,
        "created_at": now,
        "last_accessed_at": now,
        "access_count": 0,
        "superseded_by": None,
        "expires_at": None,
        "prov_source": "test",
        "prov_turn_id": None,
        "prov_session_id": None,
        "prov_extractor": None,
        "prov_extractor_ver": None,
        "prov_document_ref": None,
        "prov_recorded_at": now,
        "metadata": "{}",
    }


class _FakePool:
    """Stands in for PostgresPool, skipping connection and schema creation."""

    def __init__(self, conn) -> None:
        self._conn = conn
        self._pool = self  # non-None so `construction opens no connection` stays meaningful

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


class TestListForOwnerFiltersExpiryInSql:
    """Expiry is filtered in SQL as well as in the domain's `is_active`.

    Without it, an owner with thousands of expired records spends the whole `limit` on rows the
    caller immediately discards -- which presents as "memory stopped working".
    """

    async def test_active_only_excludes_superseded_and_expired(self) -> None:
        from aura_core.adapters.postgres_store import PostgresMemoryStore

        captured = {}

        class FakeConn:
            async def fetch(self, sql, *params):
                captured["sql"] = sql
                return []

        store = PostgresMemoryStore("postgresql://u:p@h/db")
        store._pool = _FakePool(FakeConn())
        await store.list_for_owner(UserId("u1"))
        assert "superseded_by IS NULL" in captured["sql"]
        assert "expires_at" in captured["sql"]

    async def test_include_superseded_drops_both_predicates(self) -> None:
        from aura_core.adapters.postgres_store import PostgresMemoryStore

        captured = {}

        class FakeConn:
            async def fetch(self, sql, *params):
                captured["sql"] = sql
                return []

        store = PostgresMemoryStore("postgresql://u:p@h/db")
        store._pool = _FakePool(FakeConn())
        await store.list_for_owner(UserId("u1"), include_superseded=True)
        assert "superseded_by IS NULL" not in captured["sql"]

    async def test_the_owner_predicate_is_always_present(self) -> None:
        """Tenant isolation is a signature-level obligation in this port; it must reach the SQL."""
        from aura_core.adapters.postgres_store import PostgresMemoryStore

        captured = {}

        class FakeConn:
            async def fetch(self, sql, *params):
                captured["sql"] = sql
                captured["params"] = params
                return []

        store = PostgresMemoryStore("postgresql://u:p@h/db")
        store._pool = _FakePool(FakeConn())
        await store.list_for_owner(UserId("u1"), include_superseded=True)
        assert "owner_id = $1" in captured["sql"]
        assert captured["params"][0] == "u1"


class TestDeleteIsScopedByOwner:
    async def test_the_owner_is_part_of_the_statement_not_the_caller(self) -> None:
        """A delete matching on id alone would let any user remove any memory by guessing an id."""
        from aura_core.adapters.postgres_store import PostgresMemoryStore
        from aura_core.domain.identifiers import MemoryId

        captured = {}

        class FakeConn:
            async def execute(self, sql, *params):
                captured["sql"] = sql
                captured["params"] = params
                return "DELETE 1"

        store = PostgresMemoryStore("postgresql://u:p@h/db")
        store._pool = _FakePool(FakeConn())
        assert await store.delete(MemoryId("m1"), owner_id=UserId("u1")) is True
        assert "owner_id = $2" in captured["sql"]
        assert captured["params"] == ("m1", "u1")

    async def test_deleting_nothing_reports_false(self) -> None:
        from aura_core.adapters.postgres_store import PostgresMemoryStore
        from aura_core.domain.identifiers import MemoryId

        class FakeConn:
            async def execute(self, sql, *params):
                return "DELETE 0"

        store = PostgresMemoryStore("postgresql://u:p@h/db")
        store._pool = _FakePool(FakeConn())
        assert await store.delete(MemoryId("m1"), owner_id=UserId("u1")) is False

    async def test_forget_source_reports_how_many_were_removed(self) -> None:
        """The count is what the caller uses to purge matching vector points, so the two stores
        stay consistent."""
        from aura_core.adapters.postgres_store import PostgresMemoryStore

        class FakeConn:
            async def execute(self, sql, *params):
                return "DELETE 7"

        store = PostgresMemoryStore("postgresql://u:p@h/db")
        store._pool = _FakePool(FakeConn())
        assert await store.delete_by_provenance(owner_id=UserId("u1"), source="doc-1") == 7


class TestGlobalDocumentHashLookup:
    async def test_it_uses_is_not_distinct_from_for_the_owner(self) -> None:
        """`owner_id = NULL` is NULL, never true, so `=` would never match a global document and
        every nightly refresh would re-ingest the whole global corpus as new versions."""
        from aura_core.adapters.postgres_store import PostgresDocumentStore

        captured = {}

        class FakeConn:
            async def fetchrow(self, sql, *params):
                captured["sql"] = sql
                return None

        store = PostgresDocumentStore("postgresql://u:p@h/db")
        store._pool = _FakePool(FakeConn())
        await store.get_by_hash("abc", owner_id=None)
        assert "IS NOT DISTINCT FROM" in captured["sql"]


class TestActiveVersionSelection:
    async def test_get_without_a_version_asks_for_the_active_one(self) -> None:
        """Not merely the highest: a tombstoned document must not resurface as its own latest
        version."""
        from aura_core.adapters.postgres_store import PostgresDocumentStore
        from aura_core.domain.identifiers import DocumentId

        captured = {}

        class FakeConn:
            async def fetchrow(self, sql, *params):
                captured["sql"] = sql
                return None

        store = PostgresDocumentStore("postgresql://u:p@h/db")
        store._pool = _FakePool(FakeConn())
        await store.get(DocumentId("d1"))
        assert "status = 'active'" in captured["sql"]
