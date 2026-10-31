"""Postgres implementations of the ``DocumentStore`` and ``MemoryStore`` ports.

Documents are the source of truth; the vector index is derived and can always be rebuilt from
here (ADR-007). That asymmetry is what makes reindexing routine, and it is why this adapter --
not the vector store -- owns the schema for content, versions, and provenance.

Both stores share one connection pool and one ``ensure_schema`` call, because they are one
database and giving them separate pools would double the connection count for no benefit on a
box where Postgres is configured for a few hundred.

**Written with asyncpg directly, not the ORM.** The legacy tree already has SQLAlchemy models for
the v1/v2 tables, and `aura_core` must not import from it (ADR-002's one-directional rule, enforced
by the architecture tests). Sharing an ORM base across the boundary would create exactly the
coupling the strangler migration exists to avoid, and these tables are owned solely by v3 -- the
removal checklist in the migration guide requires that no two layers write the same tables.

**Schema is created idempotently on first use, not by Alembic.** The legacy Alembic chain manages
the v1/v2 tables; these are separate, versioned by ``schema_version`` in their own metadata row,
and created with ``IF NOT EXISTS`` so a fresh deployment needs no migration step before it can
serve. When these tables need their first real migration, that is the point to add an Alembic
branch for them rather than extending this function.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Mapping, Sequence

from ..domain.errors import ConfigurationError, DependencyUnavailableError, NotFoundError
from ..domain.identifiers import ChunkId, DocumentId, MemoryId, SessionId, TurnId, UserId
from ..domain.knowledge import Chunk, Document, DocumentStatus, SourceType, TrustTier
from ..domain.memory import MemoryKind, MemoryRecord, Provenance
from ..ports.storage import GLOBAL_OWNER
from .field_crypto import FieldCipher
from dataclasses import replace as _replace

logger = logging.getLogger(__name__)

__all__ = ["PostgresDocumentStore", "PostgresMemoryStore", "PostgresPool"]


def _unavailable(message: str, **details: Any) -> DependencyUnavailableError:
    return DependencyUnavailableError(message, dependency="postgres", details=details or None)


def _dsn(url: str) -> str:
    """Strip the SQLAlchemy dialect prefix asyncpg does not understand.

    ``AURA_DATABASE_URL`` is shared with the legacy tree, which needs
    ``postgresql+asyncpg://``. asyncpg's own ``connect`` rejects that, so accept both rather than
    requiring operators to keep two spellings of the same URL in step.
    """
    for prefix in ("postgresql+asyncpg://", "postgres+asyncpg://"):
        if url.startswith(prefix):
            return "postgresql://" + url[len(prefix):]
    return url


def _utc(value: datetime | None) -> datetime | None:
    """Normalise to timezone-aware UTC.

    asyncpg returns naive datetimes for ``timestamp`` columns and aware ones for ``timestamptz``.
    The domain compares timestamps to compute decay and expiry, and mixing the two raises
    TypeError mid-request, so everything crossing this boundary is made aware.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class PostgresPool:
    """Lazily-created asyncpg pool, shared by both stores.

    Lazy for the same reason as the other adapters: building the container happens during settings
    validation and in every test that constructs one, and neither should require a database.
    """

    def __init__(self, url: str, *, min_size: int = 2, max_size: int = 10) -> None:
        if not url:
            raise ConfigurationError(
                "postgres backends require AURA_DATABASE_URL",
                details={"setting": "AURA_DATABASE_URL"},
            )
        self._dsn = _dsn(url)
        self._min = min_size
        self._max = max_size
        self._pool: Any = None
        self._schema_ready = False
        # Guards pool *creation* only. N concurrent first callers must produce exactly one
        # asyncpg pool -- without this, each sees ``self._pool is None`` before any of them
        # finishes awaiting ``create_pool``, so each creates its own; only the last write wins,
        # and every other pool leaks its connections uncollected. Schema readiness below is
        # deliberately left outside the lock: `_ensure_schema`'s statements are all
        # ``IF NOT EXISTS``, so concurrent callers racing it converge rather than one failing.
        self._pool_lock = asyncio.Lock()

    async def acquire(self) -> Any:
        pool = await self._get_pool()
        return pool.acquire()

    async def _get_pool(self) -> Any:
        if self._pool is None:
            async with self._pool_lock:
                # Re-check inside the lock: a caller that waited for it may find the pool
                # another caller already built while it queued.
                if self._pool is None:
                    try:
                        import asyncpg
                    except ImportError as exc:  # pragma: no cover - dependency is declared
                        raise ConfigurationError(
                            "postgres backends require the asyncpg package",
                            details={"install": "pip install asyncpg"},
                        ) from exc
                    try:
                        self._pool = await asyncpg.create_pool(
                            self._dsn, min_size=self._min, max_size=self._max
                        )
                    except Exception as exc:
                        raise _unavailable(f"could not connect to Postgres: {exc}") from exc
        if not self._schema_ready:
            await self._ensure_schema()
            self._schema_ready = True
        return self._pool

    async def _ensure_schema(self) -> None:
        """Create the v3 tables if absent.

        Every statement is ``IF NOT EXISTS``, so concurrent workers racing at startup converge
        rather than one of them failing. The indexes are not optional extras: without
        ``aura_chunks(document_id, document_version)`` a rebuild degenerates into a sequential scan
        per document, and without the ``owner_id`` indexes tenant filtering becomes the dominant
        cost of every read.
        """
        statements = (
            """
            CREATE TABLE IF NOT EXISTS aura_documents (
                id                TEXT        NOT NULL,
                version           INTEGER     NOT NULL,
                title             TEXT        NOT NULL DEFAULT '',
                content           TEXT        NOT NULL,
                content_hash      TEXT        NOT NULL,
                source_type       TEXT        NOT NULL DEFAULT 'upload',
                source_uri        TEXT,
                owner_id          TEXT,
                trust_tier        INTEGER     NOT NULL DEFAULT 1,
                status            TEXT        NOT NULL DEFAULT 'active',
                language          TEXT,
                created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
                metadata          JSONB       NOT NULL DEFAULT '{}'::jsonb,
                PRIMARY KEY (id, version)
            )
            """,
            # Partial index: `get(version=None)` and `list_active` both ask only about active
            # versions, and a document accumulates superseded ones indefinitely.
            """
            CREATE INDEX IF NOT EXISTS ix_aura_documents_active
                ON aura_documents (owner_id, updated_at DESC)
                WHERE status = 'active'
            """,
            # Ingestion idempotency lookup. Scoped by owner because two users may legitimately
            # upload identical content and neither should see the other's document.
            """
            CREATE INDEX IF NOT EXISTS ix_aura_documents_hash
                ON aura_documents (content_hash, owner_id)
            """,
            # CAG pin flag (ADR-010). Added via ALTER so existing deployments pick it up
            # without a separate migration step.
            """
            ALTER TABLE aura_documents
                ADD COLUMN IF NOT EXISTS cag_pinned BOOLEAN NOT NULL DEFAULT false
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_aura_documents_cag_pinned
                ON aura_documents (owner_id)
                WHERE cag_pinned AND status = 'active'
            """,
            """
            CREATE TABLE IF NOT EXISTS aura_chunks (
                id                TEXT        PRIMARY KEY,
                document_id       TEXT        NOT NULL,
                document_version  INTEGER     NOT NULL,
                idx               INTEGER     NOT NULL,
                content           TEXT        NOT NULL,
                heading_path      JSONB       NOT NULL DEFAULT '[]'::jsonb,
                token_estimate    INTEGER     NOT NULL DEFAULT 0,
                owner_id          TEXT,
                trust_tier        INTEGER     NOT NULL DEFAULT 1,
                metadata          JSONB       NOT NULL DEFAULT '{}'::jsonb
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_aura_chunks_document
                ON aura_chunks (document_id, document_version, idx)
            """,
            # One row per owner key ('global' for owner-less content), advanced in the same
            # transaction as every change to that owner's active chunks. Each gateway worker
            # reads it before a lexical search to learn whether its in-process index for that
            # owner is stale (F-14) -- a primary-key read, so it is cheap enough for every query.
            """
            CREATE TABLE IF NOT EXISTS aura_corpus_revisions (
                owner_key   TEXT    PRIMARY KEY,
                revision    BIGINT  NOT NULL DEFAULT 0
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS aura_memories (
                id                  TEXT        PRIMARY KEY,
                kind                TEXT        NOT NULL,
                owner_id            TEXT        NOT NULL,
                content             TEXT        NOT NULL,
                subject             TEXT,
                confidence          DOUBLE PRECISION NOT NULL DEFAULT 0.7,
                embedding_ref       TEXT,
                session_id          TEXT,
                created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
                last_accessed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
                access_count        INTEGER     NOT NULL DEFAULT 0,
                superseded_by       TEXT,
                expires_at          TIMESTAMPTZ,
                prov_source         TEXT        NOT NULL,
                prov_turn_id        TEXT,
                prov_session_id     TEXT,
                prov_extractor      TEXT,
                prov_extractor_ver  TEXT,
                prov_document_ref   TEXT,
                prov_recorded_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
                metadata            JSONB       NOT NULL DEFAULT '{}'::jsonb
            )
            """,
            # The shape `list_for_owner` asks for: this owner, this kind, active first.
            """
            CREATE INDEX IF NOT EXISTS ix_aura_memories_owner_kind
                ON aura_memories (owner_id, kind, created_at DESC)
            """,
            # `delete_by_provenance` -- "forget everything you learned from X".
            """
            CREATE INDEX IF NOT EXISTS ix_aura_memories_provenance
                ON aura_memories (owner_id, prov_source)
            """,
        )
        try:
            async with self._pool.acquire() as conn:
                for statement in statements:
                    await conn.execute(statement)
        except Exception as exc:
            raise _unavailable(f"could not create the v3 schema: {exc}") from exc

    async def close(self, *, timeout_s: float | None = None) -> None:
        """Release the pool. Safe to call when it was never created, and a second time.

        asyncpg's ``Pool.close()`` waits for every acquired connection to be released. With
        ``timeout_s`` that wait is bounded, and a connection still busy afterwards -- a query
        outliving the shutdown drain -- is terminated instead, because a shutdown that never
        ends gets SIGKILLed and then nothing is closed cleanly.
        """
        pool, self._pool = self._pool, None
        self._schema_ready = False
        if pool is None:
            return
        if timeout_s is None:
            await pool.close()
            return
        try:
            await asyncio.wait_for(pool.close(), timeout=timeout_s)
        except asyncio.TimeoutError:
            logger.warning(
                "postgres pool did not close within %.1fs; terminating its connections", timeout_s
            )
            pool.terminate()

    async def ping(self) -> bool:
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return True
        except Exception as exc:
            logger.warning("postgres ping failed: %s", type(exc).__name__)
            return False


# -- documents ----------------------------------------------------------------------------

#: Advances one owner key's corpus revision; run inside the transaction that changed the corpus,
#: so a reader that sees the new revision also sees the change (see `corpus_revisions`).
_BUMP_REVISION = """
    INSERT INTO aura_corpus_revisions (owner_key, revision) VALUES ($1, 1)
    ON CONFLICT (owner_key) DO UPDATE SET revision = aura_corpus_revisions.revision + 1
"""


def _owner_key(owner_id: UserId | None) -> str:
    return owner_id.value if owner_id is not None else GLOBAL_OWNER


class PostgresDocumentStore:
    """Implements the ``DocumentStore`` port."""

    def __init__(self, url: str, *, pool: PostgresPool | None = None) -> None:
        self._pool = pool or PostgresPool(url)

    async def put(self, document: Document, chunks: Sequence[Chunk]) -> None:
        """Write a document version and its chunks atomically.

        One transaction, because a document whose chunks are missing is worse than no document: it
        is retrievable metadata with nothing behind it, and the rebuild path would treat it as
        legitimately empty rather than as a failure to repair.
        """
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(
                        """
                        INSERT INTO aura_documents (
                            id, version, title, content, content_hash, source_type, source_uri,
                            owner_id, trust_tier, status, language, cag_pinned,
                            created_at, updated_at, metadata
                        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
                        ON CONFLICT (id, version) DO UPDATE SET
                            title = EXCLUDED.title,
                            content = EXCLUDED.content,
                            content_hash = EXCLUDED.content_hash,
                            status = EXCLUDED.status,
                            cag_pinned = EXCLUDED.cag_pinned,
                            updated_at = EXCLUDED.updated_at,
                            metadata = EXCLUDED.metadata
                        """,
                        document.id.value,
                        document.version,
                        document.title,
                        document.content,
                        document.content_hash,
                        document.source_type.value,
                        document.source_uri,
                        document.owner_id.value if document.owner_id else None,
                        int(document.trust_tier),
                        document.status.value,
                        document.language,
                        bool(document.cag_pinned),
                        document.created_at,
                        document.updated_at,
                        json.dumps(dict(document.metadata)),
                    )
                    # Replace rather than merge: a re-put of the same version means the chunker ran
                    # again, and leaving orphans from the previous run would double-count content in
                    # retrieval.
                    await conn.execute(
                        "DELETE FROM aura_chunks WHERE document_id = $1 AND document_version = $2",
                        document.id.value,
                        document.version,
                    )
                    if chunks:
                        await conn.executemany(
                            """
                            INSERT INTO aura_chunks (
                                id, document_id, document_version, idx, content, heading_path,
                                token_estimate, owner_id, trust_tier, metadata
                            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                            ON CONFLICT (id) DO UPDATE SET
                                content = EXCLUDED.content,
                                heading_path = EXCLUDED.heading_path,
                                token_estimate = EXCLUDED.token_estimate,
                                metadata = EXCLUDED.metadata
                            """,
                            [
                                (
                                    chunk.id.value,
                                    chunk.document_id.value,
                                    chunk.document_version,
                                    chunk.index,
                                    chunk.content,
                                    json.dumps(list(chunk.heading_path)),
                                    chunk.token_estimate,
                                    chunk.owner_id.value if chunk.owner_id else None,
                                    int(chunk.trust_tier),
                                    json.dumps(dict(chunk.metadata)),
                                )
                                for chunk in chunks
                            ],
                        )
                    # Last, so the revision row is locked for as short a time as possible.
                    await conn.execute(_BUMP_REVISION, _owner_key(document.owner_id))
        except Exception as exc:
            raise _unavailable(
                f"could not write document {document.id.value!r}: {exc}",
                document=document.id.value,
            ) from exc

    @staticmethod
    def _document(row: Mapping[str, Any]) -> Document:
        return Document(
            id=DocumentId(row["id"]),
            version=row["version"],
            title=row["title"],
            content=row["content"],
            source_type=SourceType(row["source_type"]),
            source_uri=row["source_uri"],
            owner_id=UserId(row["owner_id"]) if row["owner_id"] else None,
            trust_tier=TrustTier(row["trust_tier"]),
            status=DocumentStatus(row["status"]),
            language=row["language"],
            cag_pinned=bool(row.get("cag_pinned", False)),
            created_at=_utc(row["created_at"]),
            updated_at=_utc(row["updated_at"]),
            metadata=json.loads(row["metadata"]) if isinstance(row["metadata"], str) else dict(row["metadata"] or {}),
        )

    @staticmethod
    def _chunk(row: Mapping[str, Any]) -> Chunk:
        heading = row["heading_path"]
        if isinstance(heading, str):
            heading = json.loads(heading)
        metadata = row["metadata"]
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        return Chunk(
            id=ChunkId(row["id"]),
            document_id=DocumentId(row["document_id"]),
            document_version=row["document_version"],
            index=row["idx"],
            content=row["content"],
            heading_path=tuple(heading or ()),
            token_estimate=row["token_estimate"],
            owner_id=UserId(row["owner_id"]) if row["owner_id"] else None,
            trust_tier=TrustTier(row["trust_tier"]),
            metadata=dict(metadata or {}),
        )

    async def get(self, document_id: DocumentId, *, version: int | None = None) -> Document | None:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                if version is not None:
                    row = await conn.fetchrow(
                        "SELECT * FROM aura_documents WHERE id = $1 AND version = $2",
                        document_id.value,
                        version,
                    )
                else:
                    # The active version, not merely the highest: a tombstoned document must not
                    # resurface as its own latest version.
                    row = await conn.fetchrow(
                        """
                        SELECT * FROM aura_documents
                        WHERE id = $1 AND status = 'active'
                        ORDER BY version DESC LIMIT 1
                        """,
                        document_id.value,
                    )
        except Exception as exc:
            raise _unavailable(f"could not read document {document_id.value!r}: {exc}") from exc
        return self._document(row) if row else None

    async def get_by_hash(self, content_hash: str, *, owner_id: UserId | None) -> Document | None:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                # `owner_id IS NOT DISTINCT FROM $2` rather than `=`: a global document has NULL
                # here, and `NULL = NULL` is NULL, so `=` would never match one and every nightly
                # refresh would re-ingest the whole global corpus as new versions.
                row = await conn.fetchrow(
                    """
                    SELECT * FROM aura_documents
                    WHERE content_hash = $1
                      AND owner_id IS NOT DISTINCT FROM $2
                      AND status = 'active'
                    ORDER BY version DESC LIMIT 1
                    """,
                    content_hash,
                    owner_id.value if owner_id else None,
                )
        except Exception as exc:
            raise _unavailable(f"could not look up content hash: {exc}") from exc
        return self._document(row) if row else None

    async def chunks_for(self, document_id: DocumentId, version: int) -> Sequence[Chunk]:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM aura_chunks
                    WHERE document_id = $1 AND document_version = $2
                    ORDER BY idx
                    """,
                    document_id.value,
                    version,
                )
        except Exception as exc:
            raise _unavailable(f"could not read chunks for {document_id.value!r}: {exc}") from exc
        return [self._chunk(row) for row in rows]

    async def list_active(
        self, *, owner_id: UserId | None, limit: int = 100, offset: int = 0
    ) -> Sequence[Document]:
        """Active documents for one owner, plus global ones.

        ``owner_id=None`` means global only, matching the vector store's rule -- there is no way to
        ask this port for a cross-tenant listing.
        """
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                if owner_id is None:
                    rows = await conn.fetch(
                        """
                        SELECT * FROM aura_documents
                        WHERE status = 'active' AND owner_id IS NULL
                        ORDER BY updated_at DESC LIMIT $1 OFFSET $2
                        """,
                        limit,
                        offset,
                    )
                else:
                    rows = await conn.fetch(
                        """
                        SELECT * FROM aura_documents
                        WHERE status = 'active' AND (owner_id = $1 OR owner_id IS NULL)
                        ORDER BY updated_at DESC LIMIT $2 OFFSET $3
                        """,
                        owner_id.value,
                        limit,
                        offset,
                    )
        except Exception as exc:
            raise _unavailable(f"could not list documents: {exc}") from exc
        return [self._document(row) for row in rows]

    async def set_cag_pin(
        self, document_id: DocumentId, *, owner_id: UserId | None, pinned: bool
    ) -> Document:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    row = await conn.fetchrow(
                        """
                        SELECT * FROM aura_documents
                        WHERE id = $1 AND status = 'active'
                        ORDER BY version DESC LIMIT 1
                        """,
                        document_id.value,
                    )
                    if row is None:
                        raise NotFoundError(
                            f"document {document_id.value!r} not found",
                            details={"document_id": document_id.value},
                        )
                    doc = self._document(row)
                    doc_owner = doc.owner_id.value if doc.owner_id else None
                    caller = owner_id.value if owner_id else None
                    if doc_owner != caller:
                        raise NotFoundError(
                            f"document {document_id.value!r} not found",
                            details={"document_id": document_id.value},
                        )
                    await conn.execute(
                        """
                        UPDATE aura_documents
                        SET cag_pinned = $1, updated_at = now()
                        WHERE id = $2 AND version = $3
                        """,
                        pinned,
                        document_id.value,
                        doc.version,
                    )
                    return _replace(doc, cag_pinned=pinned)
        except NotFoundError:
            raise
        except Exception as exc:
            raise _unavailable(
                f"could not set cag pin on {document_id.value!r}: {exc}"
            ) from exc

    async def list_cag_pinned(self, *, owner_id: UserId | None) -> Sequence[Document]:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                if owner_id is None:
                    rows = await conn.fetch(
                        """
                        SELECT DISTINCT ON (id) *
                        FROM aura_documents
                        WHERE status = 'active' AND cag_pinned AND owner_id IS NULL
                        ORDER BY id, version DESC
                        """
                    )
                else:
                    rows = await conn.fetch(
                        """
                        SELECT DISTINCT ON (id) *
                        FROM aura_documents
                        WHERE status = 'active' AND cag_pinned
                          AND (owner_id = $1 OR owner_id IS NULL)
                        ORDER BY id, version DESC
                        """,
                        owner_id.value,
                    )
        except Exception as exc:
            raise _unavailable(f"could not list cag-pinned documents: {exc}") from exc
        docs = [self._document(row) for row in rows]
        docs.sort(
            key=lambda d: (d.owner_id is not None, -int(d.trust_tier), d.id.value, -d.version)
        )
        return docs

    async def iter_all_chunks(self, *, batch_size: int = 500) -> AsyncIterator[list[Chunk]]:
        """Stream every chunk belonging to an active document version.

        A server-side cursor inside one transaction, so a rebuild of a corpus larger than RAM does
        not have to materialise it. The join is what keeps superseded and tombstoned versions out:
        without it a rebuild would index every historical version and retrieval would return
        content the user had already replaced.
        """
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    batch: list[Chunk] = []
                    async for row in conn.cursor(
                        """
                        SELECT c.* FROM aura_chunks c
                        JOIN aura_documents d
                          ON d.id = c.document_id AND d.version = c.document_version
                        WHERE d.status = 'active'
                        ORDER BY c.document_id, c.idx
                        """,
                        prefetch=batch_size,
                    ):
                        batch.append(self._chunk(row))
                        if len(batch) >= batch_size:
                            yield batch
                            batch = []
                    if batch:
                        yield batch
        except Exception as exc:
            raise _unavailable(f"could not stream chunks: {exc}") from exc

    async def supersede(
        self, document_id: DocumentId, *, old_version: int, new_version: int
    ) -> None:
        """Mark the old version superseded once the new one is durable.

        Scoped to the named version rather than "everything below new_version": the caller knows
        which version it replaced, and a broader update would silently re-mark versions that an
        earlier partial failure had left in some other state.
        """
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                # One statement, so the status change and the owner's revision bump are atomic
                # without an explicit transaction. Nothing superseded, nothing bumped.
                await conn.execute(
                    """
                    WITH superseded AS (
                        UPDATE aura_documents SET status = 'superseded', updated_at = now()
                        WHERE id = $1 AND version = $2 AND status = 'active'
                        RETURNING owner_id
                    )
                    INSERT INTO aura_corpus_revisions (owner_key, revision)
                    SELECT DISTINCT COALESCE(owner_id, $3), 1 FROM superseded
                    ON CONFLICT (owner_key) DO UPDATE
                        SET revision = aura_corpus_revisions.revision + 1
                    """,
                    document_id.value,
                    old_version,
                    GLOBAL_OWNER,
                )
        except Exception as exc:
            raise _unavailable(f"could not supersede {document_id.value!r}: {exc}") from exc

    async def purge_owner(self, owner_id: UserId) -> int:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(
                        """
                        DELETE FROM aura_chunks
                        WHERE owner_id = $1
                           OR document_id IN (SELECT id FROM aura_documents WHERE owner_id = $1)
                        """,
                        owner_id.value,
                    )
                    status = await conn.execute(
                        "DELETE FROM aura_documents WHERE owner_id = $1", owner_id.value
                    )
                    # Advanced, never deleted: a worker that last saw revision N must not see N
                    # again after the erasure and conclude nothing changed.
                    await conn.execute(_BUMP_REVISION, owner_id.value)
        except Exception as exc:
            raise _unavailable(f"could not purge documents of {owner_id.value!r}: {exc}") from exc
        return int(status.split()[-1]) if status else 0

    async def tombstone(self, document_id: DocumentId, *, owner_id: UserId | None) -> int:
        """Tombstone every version and report how many chunks must leave the indexes.

        The count is the caller's purge list, so it is computed inside the same transaction as the
        status change. Computing it afterwards would race a concurrent ingestion and leave
        retrievable chunks behind for a document the user asked to delete.

        The ``owner_id`` predicate is the authorisation check, in SQL rather than in the caller --
        a tombstone that forgot it would let one user delete another's document.
        """
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    if owner_id is None:
                        chunk_count = await conn.fetchval(
                            """
                            SELECT count(*) FROM aura_chunks c
                            JOIN aura_documents d
                              ON d.id = c.document_id AND d.version = c.document_version
                            WHERE c.document_id = $1 AND d.owner_id IS NULL
                            """,
                            document_id.value,
                        )
                        await conn.execute(
                            """
                            UPDATE aura_documents SET status = 'tombstoned', updated_at = now()
                            WHERE id = $1 AND owner_id IS NULL
                            """,
                            document_id.value,
                        )
                    else:
                        chunk_count = await conn.fetchval(
                            """
                            SELECT count(*) FROM aura_chunks c
                            JOIN aura_documents d
                              ON d.id = c.document_id AND d.version = c.document_version
                            WHERE c.document_id = $1 AND d.owner_id = $2
                            """,
                            document_id.value,
                            owner_id.value,
                        )
                        await conn.execute(
                            """
                            UPDATE aura_documents SET status = 'tombstoned', updated_at = now()
                            WHERE id = $1 AND owner_id = $2
                            """,
                            document_id.value,
                            owner_id.value,
                        )
                    await conn.execute(_BUMP_REVISION, _owner_key(owner_id))
        except Exception as exc:
            raise _unavailable(f"could not tombstone {document_id.value!r}: {exc}") from exc
        return int(chunk_count or 0)

    async def corpus_revisions(self, owner_keys: Sequence[str]) -> dict[str, int]:
        if not owner_keys:
            return {}
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT owner_key, revision FROM aura_corpus_revisions
                    WHERE owner_key = ANY($1::text[])
                    """,
                    list(owner_keys),
                )
        except Exception as exc:
            raise _unavailable(f"could not read corpus revisions: {exc}") from exc
        return {row["owner_key"]: int(row["revision"]) for row in rows}

    async def active_chunk_ids(self, owner_key: str) -> list[str]:
        """Ids only: a worker refreshing its lexical shard diffs these against what it holds
        and fetches content just for the difference (`active_chunks`). Branches on the global
        key rather than using ``IS NOT DISTINCT FROM`` so both forms can use the partial
        ``ix_aura_documents_active`` index."""
        pool = await self._pool._get_pool()
        owner_predicate = "d.owner_id IS NULL" if owner_key == GLOBAL_OWNER else "d.owner_id = $1"
        params = () if owner_key == GLOBAL_OWNER else (owner_key,)
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    f"""
                    SELECT c.id FROM aura_documents d
                    JOIN aura_chunks c
                      ON c.document_id = d.id AND c.document_version = d.version
                    WHERE d.status = 'active' AND {owner_predicate}
                    """,
                    *params,
                )
        except Exception as exc:
            raise _unavailable(f"could not list active chunks: {exc}") from exc
        return [row["id"] for row in rows]

    async def active_chunks(self, chunk_ids: Sequence[str]) -> list[Chunk]:
        if not chunk_ids:
            return []
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT c.* FROM aura_chunks c
                    JOIN aura_documents d
                      ON d.id = c.document_id AND d.version = c.document_version
                    WHERE c.id = ANY($1::text[]) AND d.status = 'active'
                    """,
                    list(chunk_ids),
                )
        except Exception as exc:
            raise _unavailable(f"could not read chunks: {exc}") from exc
        return [self._chunk(row) for row in rows]

    async def close(self) -> None:
        await self._pool.close()


# -- memories -----------------------------------------------------------------------------


class PostgresMemoryStore:
    """Implements the ``MemoryStore`` port.

    Vector similarity is not this store's job -- embeddings live in the vector store, referenced by
    ``embedding_ref``. What lives here are the relational questions memory actually asks: this
    owner's active semantic records, everything from one extractor version, everything derived from
    one document.

    **``content`` is encrypted at rest** when the container hands this store an enabled
    :class:`~aura_core.adapters.field_crypto.FieldCipher` (``AURA_DATA_ENCRYPTION_KEYS``; see
    ``docs/architecture/004-memory-taxonomy.md``, "Encryption at rest"). Every other column stays
    in the clear because a query needs it: ``owner_id``/``kind``/``subject``/``superseded_by``/
    ``expires_at`` are ``WHERE`` predicates, ``created_at`` is the sort key, ``prov_source`` is
    what ``delete_by_provenance`` matches. No statement here filters on ``content`` -- similarity
    search runs in the vector store against embeddings -- so encrypting it removes no query this
    adapter serves. Rows written before a key was configured are read as plaintext and sealed on
    their next write.
    """

    def __init__(
        self, url: str, *, pool: PostgresPool | None = None, cipher: FieldCipher | None = None,
    ) -> None:
        self._pool = pool or PostgresPool(url)
        self._cipher = cipher or FieldCipher.disabled()

    @staticmethod
    def encryption_context(memory_id: str, owner_id: str) -> str:
        """What a sealed ``content`` value is bound to: moving it to another row fails to decrypt."""
        return f"aura_memories:{memory_id}:{owner_id}"

    @staticmethod
    def _record(row: Mapping[str, Any], cipher: FieldCipher | None = None) -> MemoryRecord:
        metadata = row["metadata"]
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        # A passthrough cipher returns legacy plaintext unchanged and refuses (rather than
        # returns) a sealed value it has no key for.
        content = (cipher or FieldCipher.disabled()).decrypt(
            row["content"],
            context=PostgresMemoryStore.encryption_context(row["id"], row["owner_id"]),
        )
        return MemoryRecord(
            id=MemoryId(row["id"]),
            kind=MemoryKind(row["kind"]),
            owner_id=UserId(row["owner_id"]),
            content=content,
            provenance=Provenance(
                source=row["prov_source"],
                turn_id=TurnId(row["prov_turn_id"]) if row["prov_turn_id"] else None,
                session_id=SessionId(row["prov_session_id"]) if row["prov_session_id"] else None,
                extractor=row["prov_extractor"],
                extractor_version=row["prov_extractor_ver"],
                document_ref=row["prov_document_ref"],
                recorded_at=_utc(row["prov_recorded_at"]),
            ),
            subject=row["subject"],
            confidence=row["confidence"],
            embedding_ref=row["embedding_ref"],
            session_id=SessionId(row["session_id"]) if row["session_id"] else None,
            created_at=_utc(row["created_at"]),
            last_accessed_at=_utc(row["last_accessed_at"]),
            access_count=row["access_count"],
            superseded_by=MemoryId(row["superseded_by"]) if row["superseded_by"] else None,
            expires_at=_utc(row["expires_at"]),
            metadata=dict(metadata or {}),
        )

    @staticmethod
    def _params(record: MemoryRecord, cipher: FieldCipher | None = None) -> tuple:
        p = record.provenance
        content = (cipher or FieldCipher.disabled()).encrypt(
            record.content,
            context=PostgresMemoryStore.encryption_context(record.id.value, record.owner_id.value),
        )
        return (
            record.id.value,
            record.kind.value,
            record.owner_id.value,
            content,
            record.subject,
            record.confidence,
            record.embedding_ref,
            record.session_id.value if record.session_id else None,
            record.created_at,
            record.last_accessed_at,
            record.access_count,
            record.superseded_by.value if record.superseded_by else None,
            record.expires_at,
            p.source,
            p.turn_id.value if p.turn_id else None,
            p.session_id.value if p.session_id else None,
            p.extractor,
            p.extractor_version,
            p.document_ref,
            p.recorded_at,
            json.dumps(dict(record.metadata)),
        )

    _UPSERT = """
        INSERT INTO aura_memories (
            id, kind, owner_id, content, subject, confidence, embedding_ref, session_id,
            created_at, last_accessed_at, access_count, superseded_by, expires_at,
            prov_source, prov_turn_id, prov_session_id, prov_extractor, prov_extractor_ver,
            prov_document_ref, prov_recorded_at, metadata
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21)
        ON CONFLICT (id) DO UPDATE SET
            content = EXCLUDED.content,
            subject = EXCLUDED.subject,
            confidence = EXCLUDED.confidence,
            embedding_ref = EXCLUDED.embedding_ref,
            last_accessed_at = EXCLUDED.last_accessed_at,
            access_count = EXCLUDED.access_count,
            superseded_by = EXCLUDED.superseded_by,
            expires_at = EXCLUDED.expires_at,
            metadata = EXCLUDED.metadata
    """

    async def put(self, record: MemoryRecord) -> None:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                await conn.execute(self._UPSERT, *self._params(record, self._cipher))
        except Exception as exc:
            raise _unavailable(f"could not write memory {record.id.value!r}: {exc}") from exc

    async def update(self, record: MemoryRecord) -> None:
        """Same statement as ``put``.

        The port distinguishes them, but at this layer both are "make the row match this record".
        Two statements that must stay in step is a defect waiting to happen -- the supersede path
        writes through ``update`` and the extractor through ``put``, and a column present in one
        but not the other would silently drop data on whichever path was not exercised in testing.
        """
        await self.put(record)

    async def touch_many(
        self, memory_ids: Sequence[MemoryId], *, owner_id: UserId, at: datetime
    ) -> int:
        """One field-level ``UPDATE`` for a whole recall, never the snapshot UPSERT (F-16).

        Recall used to reinforce through ``update`` -- the full-row UPSERT above -- from the copy
        it had read, so a supersede, edit or delete landing in between was reverted (a deleted
        row was even re-inserted), and two overlapping recalls lost an increment. The increment
        is done by the database, so it is atomic under any concurrency; ``content`` is neither
        read nor re-encrypted; and it is one round trip and one pool connection where the
        per-record UPSERTs took up to ``limit`` of each. ``GREATEST`` keeps a replica with a
        clock behind another's from moving ``last_accessed_at`` backwards.
        """
        wanted = list(dict.fromkeys(mid.value for mid in memory_ids))
        if not wanted:
            return 0
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                status = await conn.execute(
                    """
                    UPDATE aura_memories
                    SET access_count = access_count + 1,
                        last_accessed_at = GREATEST(last_accessed_at, $3::timestamptz)
                    WHERE owner_id = $1 AND id = ANY($2::text[])
                    """,
                    owner_id.value,
                    wanted,
                    at,
                )
        except Exception as exc:
            raise _unavailable(f"could not reinforce memories: {exc}") from exc
        try:
            return int(status.rsplit(" ", 1)[-1])
        except ValueError:  # pragma: no cover - asyncpg always reports a count
            return 0

    async def get(self, memory_id: MemoryId) -> MemoryRecord | None:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM aura_memories WHERE id = $1", memory_id.value
                )
        except Exception as exc:
            raise _unavailable(f"could not read memory {memory_id.value!r}: {exc}") from exc
        return self._record(row, self._cipher) if row else None

    async def get_many(self, memory_ids: Sequence[MemoryId]) -> Sequence[MemoryRecord]:
        """One round trip after a vector search returns ids, not N.

        Returned in the caller's order rather than the database's: the ids arrive ranked by
        similarity, and `ANY($1)` does not preserve that. Re-sorting here keeps the ranking the
        retrieval step computed.
        """
        if not memory_ids:
            return []
        wanted = [mid.value for mid in memory_ids]
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT * FROM aura_memories WHERE id = ANY($1::text[])", wanted
                )
        except Exception as exc:
            raise _unavailable(f"could not batch-read memories: {exc}") from exc
        by_id = {row["id"]: self._record(row, self._cipher) for row in rows}
        return [by_id[mid] for mid in wanted if mid in by_id]

    async def list_for_owner(
        self,
        owner_id: UserId,
        *,
        kinds: Sequence[MemoryKind] | None = None,
        subject: str | None = None,
        include_superseded: bool = False,
        limit: int = 200,
        offset: int = 0,
    ) -> Sequence[MemoryRecord]:
        clauses = ["owner_id = $1"]
        params: list[Any] = [owner_id.value]

        if kinds:
            params.append([k.value for k in kinds])
            clauses.append(f"kind = ANY(${len(params)}::text[])")
        if subject is not None:
            params.append(subject)
            clauses.append(f"subject = ${len(params)}")
        if not include_superseded:
            # Expiry is filtered here as well as in the domain's `is_active`. Doing it in SQL means
            # an owner with thousands of expired records does not spend the whole `limit` on rows
            # the caller will immediately discard, which would look like "memory stopped working".
            clauses.append("superseded_by IS NULL")
            clauses.append("(expires_at IS NULL OR expires_at > now())")

        params.extend([limit, offset])
        sql = (
            f"SELECT * FROM aura_memories WHERE {' AND '.join(clauses)} "
            f"ORDER BY created_at DESC LIMIT ${len(params) - 1} OFFSET ${len(params)}"
        )
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(sql, *params)
        except Exception as exc:
            raise _unavailable(f"could not list memories: {exc}") from exc
        return [self._record(row, self._cipher) for row in rows]

    async def delete(self, memory_id: MemoryId, *, owner_id: UserId) -> bool:
        """Hard delete, scoped by owner.

        The owner predicate is the authorisation check and belongs in the statement: a delete that
        matched on id alone would let any user remove any memory by guessing an id.
        """
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                status = await conn.execute(
                    "DELETE FROM aura_memories WHERE id = $1 AND owner_id = $2",
                    memory_id.value,
                    owner_id.value,
                )
        except Exception as exc:
            raise _unavailable(f"could not delete memory {memory_id.value!r}: {exc}") from exc
        return status.rsplit(" ", 1)[-1] != "0"

    async def delete_by_provenance(self, *, owner_id: UserId, source: str) -> int:
        """"Forget everything you learned from X."

        Works only because every write carries provenance. The count is what the caller uses to
        purge the matching vector points, so the two stores stay consistent.
        """
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                status = await conn.execute(
                    "DELETE FROM aura_memories WHERE owner_id = $1 AND prov_source = $2",
                    owner_id.value,
                    source,
                )
        except Exception as exc:
            raise _unavailable(f"could not forget source {source!r}: {exc}") from exc
        try:
            return int(status.rsplit(" ", 1)[-1])
        except ValueError:  # pragma: no cover - asyncpg always reports a count
            return 0

    async def count(self, owner_id: UserId, *, kind: MemoryKind | None = None) -> int:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                if kind is None:
                    value = await conn.fetchval(
                        "SELECT count(*) FROM aura_memories WHERE owner_id = $1", owner_id.value
                    )
                else:
                    value = await conn.fetchval(
                        "SELECT count(*) FROM aura_memories WHERE owner_id = $1 AND kind = $2",
                        owner_id.value,
                        kind.value,
                    )
        except Exception as exc:
            raise _unavailable(f"could not count memories: {exc}") from exc
        return int(value or 0)

    async def close(self) -> None:
        await self._pool.close()
