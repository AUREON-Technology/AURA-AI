"""Postgres implementation of the ``GraphStore`` port.

A separate module from ``postgres_store.py`` so that file does not keep growing.
Selected when the document backend is postgres; shares ``PostgresPool``.

Tables (all owner-scoped; ``owner_id = ''`` is the global partition):

* ``aura_graph_entities`` / ``aura_graph_relations`` -- the facts, with ``confidence`` and a
  ``valid_from``/``valid_to`` validity interval. ``sources``/``aliases`` JSONB are denormalised
  mirrors kept for cheap reads; the tables below are the truth.
* ``aura_graph_entity_provenance`` / ``aura_graph_relation_provenance`` -- one row per source
  that asserted a fact, with ``document_id``/``document_version`` parsed from the source id, the
  extractor, its confidence, and ``retired_at`` once that document version was superseded.
* ``aura_graph_entity_aliases`` -- every surface form, normalised, for search.
* ``aura_graph_entity_types`` / ``aura_graph_relation_types`` -- the allowed vocabulary, seeded
  from :data:`~aura_core.domain.graph.DEFAULT_GRAPH_SCHEMA`.

Confidence, live sources and validity are re-derived after every change with
:func:`~aura_core.domain.graph.summarise_provenance` -- the function the in-memory adapter uses --
so both stores compute identical beliefs from identical evidence.

The DDL below is kept textually in step with ``alembic/versions/0007_knowledge_graph.py``
(``tests/unit/test_graph_schema_migration.py`` checks it): like every ``aura_*`` table, the adapter
creates what it needs on first use, so a gateway that ran before the migration must not make
``alembic upgrade head`` fail, and vice versa.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from ..domain.errors import DependencyUnavailableError
from ..domain.graph import (
    DEFAULT_GRAPH_SCHEMA,
    FactProvenance,
    GraphSchema,
    normalize_alias,
    parse_source,
    summarise_provenance,
)
from ..domain.identifiers import UserId
from ..domain.knowledge import Entity, GraphDelta, Relation
from .postgres_store import PostgresPool

logger = logging.getLogger(__name__)

__all__ = ["PostgresGraphStore", "GRAPH_SCHEMA", "graph_type_seed_statements"]

_MAX_DEPTH = 3

#: Static DDL, in order. Mirrored by migration 0007.
GRAPH_SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS aura_graph_entities (
        owner_id      TEXT        NOT NULL DEFAULT '',
        key           TEXT        NOT NULL,
        name          TEXT        NOT NULL,
        type          TEXT        NOT NULL,
        aliases       JSONB       NOT NULL DEFAULT '[]'::jsonb,
        attributes    JSONB       NOT NULL DEFAULT '{}'::jsonb,
        mention_count INTEGER     NOT NULL DEFAULT 1,
        first_seen    TIMESTAMPTZ NOT NULL DEFAULT now(),
        last_seen     TIMESTAMPTZ NOT NULL DEFAULT now(),
        sources       JSONB       NOT NULL DEFAULT '[]'::jsonb,
        PRIMARY KEY (owner_id, key)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_aura_graph_entities_owner
        ON aura_graph_entities (owner_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS aura_graph_relations (
        owner_id     TEXT             NOT NULL DEFAULT '',
        subject_key  TEXT             NOT NULL,
        predicate    TEXT             NOT NULL,
        object_key   TEXT             NOT NULL,
        confidence   DOUBLE PRECISION NOT NULL DEFAULT 0.5,
        sources      JSONB            NOT NULL DEFAULT '[]'::jsonb,
        attributes   JSONB            NOT NULL DEFAULT '{}'::jsonb,
        created_at   TIMESTAMPTZ      NOT NULL DEFAULT now(),
        PRIMARY KEY (owner_id, subject_key, predicate, object_key)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_aura_graph_relations_owner
        ON aura_graph_relations (owner_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_aura_graph_relations_subject
        ON aura_graph_relations (owner_id, subject_key)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_aura_graph_relations_object
        ON aura_graph_relations (owner_id, object_key)
    """,
    # -- Phase 11: confidence and validity on the facts ----------------------------------
    """
    ALTER TABLE aura_graph_entities
        ADD COLUMN IF NOT EXISTS confidence DOUBLE PRECISION NOT NULL DEFAULT 0.5
    """,
    """
    ALTER TABLE aura_graph_entities ADD COLUMN IF NOT EXISTS valid_from TIMESTAMPTZ
    """,
    """
    ALTER TABLE aura_graph_entities ADD COLUMN IF NOT EXISTS valid_to TIMESTAMPTZ
    """,
    """
    UPDATE aura_graph_entities SET valid_from = first_seen WHERE valid_from IS NULL
    """,
    """
    ALTER TABLE aura_graph_relations ADD COLUMN IF NOT EXISTS valid_from TIMESTAMPTZ
    """,
    """
    ALTER TABLE aura_graph_relations ADD COLUMN IF NOT EXISTS valid_to TIMESTAMPTZ
    """,
    """
    UPDATE aura_graph_relations SET valid_from = created_at WHERE valid_from IS NULL
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_aura_graph_entities_current
        ON aura_graph_entities (owner_id, type) WHERE valid_to IS NULL
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_aura_graph_relations_current
        ON aura_graph_relations (owner_id) WHERE valid_to IS NULL
    """,
    # -- vocabulary ----------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS aura_graph_entity_types (
        name        TEXT    PRIMARY KEY,
        literal     BOOLEAN NOT NULL DEFAULT false,
        description TEXT    NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS aura_graph_relation_types (
        predicate     TEXT   PRIMARY KEY,
        subject_types TEXT[] NOT NULL,
        object_types  TEXT[] NOT NULL,
        description   TEXT   NOT NULL DEFAULT ''
    )
    """,
    # -- aliases -------------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS aura_graph_entity_aliases (
        owner_id    TEXT NOT NULL,
        entity_key  TEXT NOT NULL,
        alias       TEXT NOT NULL,
        alias_norm  TEXT NOT NULL,
        PRIMARY KEY (owner_id, entity_key, alias_norm),
        FOREIGN KEY (owner_id, entity_key)
            REFERENCES aura_graph_entities (owner_id, key) ON DELETE CASCADE
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_aura_graph_entity_aliases_norm
        ON aura_graph_entity_aliases (owner_id, alias_norm)
    """,
    # -- provenance ----------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS aura_graph_entity_provenance (
        owner_id          TEXT             NOT NULL,
        entity_key        TEXT             NOT NULL,
        source_id         TEXT             NOT NULL,
        document_id       TEXT,
        document_version  INTEGER,
        extractor         TEXT             NOT NULL DEFAULT '',
        confidence        DOUBLE PRECISION NOT NULL
            CHECK (confidence >= 0 AND confidence <= 1),
        recorded_at       TIMESTAMPTZ      NOT NULL DEFAULT now(),
        retired_at        TIMESTAMPTZ,
        PRIMARY KEY (owner_id, entity_key, source_id),
        FOREIGN KEY (owner_id, entity_key)
            REFERENCES aura_graph_entities (owner_id, key) ON DELETE CASCADE
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_aura_graph_entity_provenance_document
        ON aura_graph_entity_provenance (owner_id, document_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS aura_graph_relation_provenance (
        owner_id          TEXT             NOT NULL,
        subject_key       TEXT             NOT NULL,
        predicate         TEXT             NOT NULL,
        object_key        TEXT             NOT NULL,
        source_id         TEXT             NOT NULL,
        document_id       TEXT,
        document_version  INTEGER,
        extractor         TEXT             NOT NULL DEFAULT '',
        confidence        DOUBLE PRECISION NOT NULL
            CHECK (confidence >= 0 AND confidence <= 1),
        recorded_at       TIMESTAMPTZ      NOT NULL DEFAULT now(),
        retired_at        TIMESTAMPTZ,
        PRIMARY KEY (owner_id, subject_key, predicate, object_key, source_id),
        FOREIGN KEY (owner_id, subject_key, predicate, object_key)
            REFERENCES aura_graph_relations (owner_id, subject_key, predicate, object_key)
            ON DELETE CASCADE
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_aura_graph_relation_provenance_document
        ON aura_graph_relation_provenance (owner_id, document_id)
    """,
    # -- backfill from the pre-Phase-11 JSONB columns (idempotent) ------------------------
    """
    INSERT INTO aura_graph_entity_provenance (
        owner_id, entity_key, source_id, document_id, document_version, extractor,
        confidence, recorded_at
    )
    SELECT e.owner_id, e.key, s.source,
           CASE WHEN s.source ~ '^.+@v[0-9]+$'
                THEN regexp_replace(s.source, '@v[0-9]+$', '') END,
           CASE WHEN s.source ~ '^.+@v[0-9]+$'
                THEN substring(s.source from '@v([0-9]+)$')::integer END,
           COALESCE(e.attributes->>'extractor', ''),
           LEAST(GREATEST(e.confidence, 0), 1),
           e.first_seen
    FROM aura_graph_entities e
    CROSS JOIN LATERAL jsonb_array_elements_text(
        CASE WHEN jsonb_array_length(e.sources) = 0 THEN '[""]'::jsonb ELSE e.sources END
    ) AS s(source)
    WHERE NOT EXISTS (
        SELECT 1 FROM aura_graph_entity_provenance p
        WHERE p.owner_id = e.owner_id AND p.entity_key = e.key
    )
    ON CONFLICT DO NOTHING
    """,
    """
    INSERT INTO aura_graph_relation_provenance (
        owner_id, subject_key, predicate, object_key, source_id, document_id,
        document_version, extractor, confidence, recorded_at
    )
    SELECT r.owner_id, r.subject_key, r.predicate, r.object_key, s.source,
           CASE WHEN s.source ~ '^.+@v[0-9]+$'
                THEN regexp_replace(s.source, '@v[0-9]+$', '') END,
           CASE WHEN s.source ~ '^.+@v[0-9]+$'
                THEN substring(s.source from '@v([0-9]+)$')::integer END,
           COALESCE(r.attributes->>'extractor', ''),
           LEAST(GREATEST(r.confidence, 0), 1),
           r.created_at
    FROM aura_graph_relations r
    CROSS JOIN LATERAL jsonb_array_elements_text(
        CASE WHEN jsonb_array_length(r.sources) = 0 THEN '[""]'::jsonb ELSE r.sources END
    ) AS s(source)
    WHERE NOT EXISTS (
        SELECT 1 FROM aura_graph_relation_provenance p
        WHERE p.owner_id = r.owner_id AND p.subject_key = r.subject_key
          AND p.predicate = r.predicate AND p.object_key = r.object_key
    )
    ON CONFLICT DO NOTHING
    """,
    """
    INSERT INTO aura_graph_entity_aliases (owner_id, entity_key, alias, alias_norm)
    SELECT e.owner_id, e.key, a.alias, lower(regexp_replace(btrim(a.alias), '\\s+', ' ', 'g'))
    FROM aura_graph_entities e
    CROSS JOIN LATERAL (
        SELECT e.name AS alias
        UNION
        SELECT jsonb_array_elements_text(e.aliases)
    ) AS a
    WHERE NOT EXISTS (
        SELECT 1 FROM aura_graph_entity_aliases x
        WHERE x.owner_id = e.owner_id AND x.entity_key = e.key
    )
    ON CONFLICT DO NOTHING
    """,
)


def _sql_text(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _sql_array(values: Sequence[str]) -> str:
    return "ARRAY[" + ", ".join(_sql_text(v) for v in sorted(values)) + "]::text[]"


def graph_type_seed_statements(schema: GraphSchema = DEFAULT_GRAPH_SCHEMA) -> tuple[str, ...]:
    """Upserts that make the vocabulary tables match ``schema``. Values are code constants."""
    entity_rows = ", ".join(
        f"({_sql_text(t.name)}, {'true' if t.literal else 'false'}, {_sql_text(t.description)})"
        for t in schema.entity_types
    )
    relation_rows = ", ".join(
        f"({_sql_text(r.predicate)}, {_sql_array(r.subject_types)}, "
        f"{_sql_array(r.object_types)}, {_sql_text(r.description)})"
        for r in schema.relation_types
    )
    return (
        "INSERT INTO aura_graph_entity_types (name, literal, description) VALUES "
        f"{entity_rows} ON CONFLICT (name) DO UPDATE SET literal = EXCLUDED.literal, "
        "description = EXCLUDED.description",
        "INSERT INTO aura_graph_relation_types (predicate, subject_types, object_types, "
        f"description) VALUES {relation_rows} ON CONFLICT (predicate) DO UPDATE SET "
        "subject_types = EXCLUDED.subject_types, object_types = EXCLUDED.object_types, "
        "description = EXCLUDED.description",
    )


def _unavailable(message: str, **details: Any) -> DependencyUnavailableError:
    return DependencyUnavailableError(message, dependency="postgres", details=details or None)


def _utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _utc_or_none(value: datetime | None) -> datetime | None:
    return _utc(value) if value is not None else None


def _owner(owner_id: UserId | None) -> str:
    return owner_id.value if owner_id else ""


def _json_value(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        return json.loads(value)
    return value


def _get(row: Mapping[str, Any], name: str, default: Any = None) -> Any:
    try:
        return row[name]
    except (KeyError, IndexError):
        return default


def _like(needle: str) -> str:
    escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class PostgresGraphStore:
    def __init__(self, url: str, *, pool: PostgresPool | None = None) -> None:
        self._pool = pool or PostgresPool(url)
        self._schema_ready = False
        # The schema includes ALTERs and backfills, which -- unlike bare CREATE ... IF NOT
        # EXISTS -- can conflict when two first callers run them concurrently.
        self._schema_lock = asyncio.Lock()

    async def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        async with self._schema_lock:
            if self._schema_ready:
                return
            pool = await self._pool._get_pool()
            try:
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        for statement in (*GRAPH_SCHEMA, *graph_type_seed_statements()):
                            await conn.execute(statement)
            except Exception as exc:
                raise _unavailable(f"could not create the graph schema: {exc}") from exc
            self._schema_ready = True

    # -- writes --------------------------------------------------------------------------

    async def apply(self, delta: GraphDelta, *, owner_id: UserId | None) -> None:
        await self._ensure_schema()
        owner = _owner(owner_id)
        now = datetime.now(timezone.utc)
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    for entity in delta.entities:
                        await self._upsert_entity(conn, owner, entity, now)
                    for relation in delta.relations:
                        await self._upsert_relation(conn, owner, relation, now)
        except Exception as exc:
            raise _unavailable(f"could not apply graph delta: {exc}") from exc

    async def _upsert_entity(self, conn: Any, owner: str, entity: Entity, now: datetime) -> None:
        row = await conn.fetchrow(
            "SELECT * FROM aura_graph_entities WHERE owner_id = $1 AND key = $2 FOR UPDATE",
            owner,
            entity.key,
        )
        existing = self._entity(row) if row is not None else None
        merged = existing.merged_with(entity) if existing is not None else entity
        valid_from = (
            existing.valid_from
            if existing is not None and existing.is_current and existing.valid_from is not None
            else now
        )
        await conn.execute(
            """
            INSERT INTO aura_graph_entities (
                owner_id, key, name, type, aliases, attributes, mention_count,
                first_seen, last_seen, sources, confidence, valid_from, valid_to
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,NULL)
            ON CONFLICT (owner_id, key) DO UPDATE SET
                name = EXCLUDED.name,
                type = EXCLUDED.type,
                aliases = EXCLUDED.aliases,
                attributes = EXCLUDED.attributes,
                mention_count = EXCLUDED.mention_count,
                last_seen = EXCLUDED.last_seen,
                sources = EXCLUDED.sources,
                valid_from = EXCLUDED.valid_from,
                valid_to = NULL
            """,
            owner,
            merged.key,
            merged.name,
            merged.type,
            json.dumps(list(merged.aliases)),
            json.dumps(dict(merged.attributes)),
            merged.mention_count,
            merged.first_seen,
            merged.last_seen,
            json.dumps(list(merged.sources)),
            merged.confidence,
            valid_from,
        )
        for alias in dict.fromkeys((merged.name, *merged.aliases)):
            await conn.execute(
                """
                INSERT INTO aura_graph_entity_aliases (owner_id, entity_key, alias, alias_norm)
                VALUES ($1,$2,$3,$4) ON CONFLICT DO NOTHING
                """,
                owner,
                merged.key,
                alias,
                normalize_alias(alias),
            )
        extractor = str(entity.attributes.get("extractor", ""))
        for source in entity.sources or ("",):
            document_id, version = parse_source(source)
            await conn.execute(
                """
                INSERT INTO aura_graph_entity_provenance AS p (
                    owner_id, entity_key, source_id, document_id, document_version,
                    extractor, confidence, recorded_at, retired_at
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,NULL)
                ON CONFLICT (owner_id, entity_key, source_id) DO UPDATE SET
                    confidence = GREATEST(p.confidence, EXCLUDED.confidence),
                    extractor = CASE WHEN EXCLUDED.extractor <> '' THEN EXCLUDED.extractor
                                     ELSE p.extractor END,
                    recorded_at = CASE WHEN p.retired_at IS NULL THEN p.recorded_at
                                       ELSE EXCLUDED.recorded_at END,
                    retired_at = NULL
                """,
                owner,
                merged.key,
                source,
                document_id,
                version,
                extractor,
                entity.confidence,
                now,
            )
        await self._refresh_entity(conn, owner, merged.key, now)

    async def _upsert_relation(
        self, conn: Any, owner: str, relation: Relation, now: datetime
    ) -> None:
        row = await conn.fetchrow(
            """
            SELECT * FROM aura_graph_relations
            WHERE owner_id = $1 AND subject_key = $2 AND predicate = $3 AND object_key = $4
            FOR UPDATE
            """,
            owner,
            relation.subject_key,
            relation.predicate,
            relation.object_key,
        )
        existing = self._relation(row) if row is not None else None
        attributes = dict(existing.attributes) if existing is not None else {}
        attributes.update(relation.attributes)
        valid_from = (
            existing.valid_from
            if existing is not None and existing.is_current and existing.valid_from is not None
            else now
        )
        await conn.execute(
            """
            INSERT INTO aura_graph_relations (
                owner_id, subject_key, predicate, object_key, confidence,
                sources, attributes, created_at, valid_from, valid_to
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,NULL)
            ON CONFLICT (owner_id, subject_key, predicate, object_key) DO UPDATE SET
                attributes = EXCLUDED.attributes,
                valid_from = EXCLUDED.valid_from,
                valid_to = NULL
            """,
            owner,
            relation.subject_key,
            relation.predicate,
            relation.object_key,
            relation.confidence,
            json.dumps(list(relation.sources)),
            json.dumps(attributes),
            relation.created_at,
            valid_from,
        )
        extractor = str(relation.attributes.get("extractor", ""))
        for source in relation.sources or ("",):
            document_id, version = parse_source(source)
            await conn.execute(
                """
                INSERT INTO aura_graph_relation_provenance AS p (
                    owner_id, subject_key, predicate, object_key, source_id, document_id,
                    document_version, extractor, confidence, recorded_at, retired_at
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,NULL)
                ON CONFLICT (owner_id, subject_key, predicate, object_key, source_id)
                DO UPDATE SET
                    confidence = GREATEST(p.confidence, EXCLUDED.confidence),
                    extractor = CASE WHEN EXCLUDED.extractor <> '' THEN EXCLUDED.extractor
                                     ELSE p.extractor END,
                    recorded_at = CASE WHEN p.retired_at IS NULL THEN p.recorded_at
                                       ELSE EXCLUDED.recorded_at END,
                    retired_at = NULL
                """,
                owner,
                relation.subject_key,
                relation.predicate,
                relation.object_key,
                source,
                document_id,
                version,
                extractor,
                relation.confidence,
                now,
            )
        await self._refresh_relation(conn, owner, relation.key, now)

    async def _refresh_entity(self, conn: Any, owner: str, key: str, now: datetime) -> bool:
        """Re-derive confidence, sources and validity. True when the entity just expired."""
        rows = await conn.fetch(
            "SELECT * FROM aura_graph_entity_provenance WHERE owner_id = $1 AND entity_key = $2",
            owner,
            key,
        )
        confidence, live, every = summarise_provenance(self._provenance(r) for r in rows)
        if confidence is not None:
            await conn.execute(
                """
                UPDATE aura_graph_entities SET confidence = $3, sources = $4, valid_to = NULL
                WHERE owner_id = $1 AND key = $2
                """,
                owner, key, confidence, json.dumps(list(live)),
            )
            return False
        was_current = await conn.fetchval(
            "SELECT valid_to IS NULL FROM aura_graph_entities WHERE owner_id = $1 AND key = $2",
            owner, key,
        )
        await conn.execute(
            """
            UPDATE aura_graph_entities SET sources = $3, valid_to = COALESCE(valid_to, $4)
            WHERE owner_id = $1 AND key = $2
            """,
            owner, key, json.dumps(list(every)), now,
        )
        return bool(was_current)

    async def _refresh_relation(
        self, conn: Any, owner: str, key: tuple[str, str, str], now: datetime
    ) -> bool:
        """Re-derive confidence, sources and validity. True when the relation just expired."""
        rows = await conn.fetch(
            """
            SELECT * FROM aura_graph_relation_provenance
            WHERE owner_id = $1 AND subject_key = $2 AND predicate = $3 AND object_key = $4
            """,
            owner, *key,
        )
        confidence, live, every = summarise_provenance(self._provenance(r) for r in rows)
        if confidence is not None:
            await conn.execute(
                """
                UPDATE aura_graph_relations SET confidence = $5, sources = $6, valid_to = NULL
                WHERE owner_id = $1 AND subject_key = $2 AND predicate = $3 AND object_key = $4
                """,
                owner, *key, confidence, json.dumps(list(live)),
            )
            return False
        was_current = await conn.fetchval(
            """
            SELECT valid_to IS NULL FROM aura_graph_relations
            WHERE owner_id = $1 AND subject_key = $2 AND predicate = $3 AND object_key = $4
            """,
            owner, *key,
        )
        await conn.execute(
            """
            UPDATE aura_graph_relations SET sources = $5, valid_to = COALESCE(valid_to, $6)
            WHERE owner_id = $1 AND subject_key = $2 AND predicate = $3 AND object_key = $4
            """,
            owner, *key, json.dumps(list(every)), now,
        )
        return bool(was_current)

    async def retire_document(
        self, document_id: str, *, owner_id: UserId | None, keep_version: int | None = None,
        at: datetime | None = None,
    ) -> int:
        await self._ensure_schema()
        owner = _owner(owner_id)
        when = _utc(at)
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    entity_rows = await conn.fetch(
                        """
                        UPDATE aura_graph_entity_provenance SET retired_at = $4
                        WHERE owner_id = $1 AND document_id = $2
                          AND document_version IS DISTINCT FROM $3::integer
                          AND retired_at IS NULL
                        RETURNING entity_key
                        """,
                        owner, document_id, keep_version, when,
                    )
                    relation_rows = await conn.fetch(
                        """
                        UPDATE aura_graph_relation_provenance SET retired_at = $4
                        WHERE owner_id = $1 AND document_id = $2
                          AND document_version IS DISTINCT FROM $3::integer
                          AND retired_at IS NULL
                        RETURNING subject_key, predicate, object_key
                        """,
                        owner, document_id, keep_version, when,
                    )
                    expired = 0
                    for key in dict.fromkeys(r["entity_key"] for r in entity_rows):
                        expired += await self._refresh_entity(conn, owner, key, when)
                    for rkey in dict.fromkeys(
                        (r["subject_key"], r["predicate"], r["object_key"]) for r in relation_rows
                    ):
                        expired += await self._refresh_relation(conn, owner, rkey, when)
        except Exception as exc:
            raise _unavailable(f"could not retire graph document: {exc}") from exc
        return expired

    async def purge_document(self, document_id: str, *, owner_id: UserId | None) -> int:
        return await self._remove_provenance(owner_id, "document_id", document_id)

    async def revert(self, source: str, *, owner_id: UserId | None) -> int:
        return await self._remove_provenance(owner_id, "source_id", source)

    async def _remove_provenance(self, owner_id: UserId | None, column: str, value: str) -> int:
        assert column in ("document_id", "source_id")  # interpolated below; never user input
        await self._ensure_schema()
        owner = _owner(owner_id)
        now = datetime.now(timezone.utc)
        pool = await self._pool._get_pool()
        removed = 0
        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    relation_rows = await conn.fetch(
                        f"""
                        DELETE FROM aura_graph_relation_provenance
                        WHERE owner_id = $1 AND {column} = $2
                        RETURNING subject_key, predicate, object_key
                        """,
                        owner, value,
                    )
                    for rkey in dict.fromkeys(
                        (r["subject_key"], r["predicate"], r["object_key"]) for r in relation_rows
                    ):
                        remaining = await conn.fetchval(
                            """
                            SELECT count(*) FROM aura_graph_relation_provenance
                            WHERE owner_id = $1 AND subject_key = $2 AND predicate = $3
                              AND object_key = $4
                            """,
                            owner, *rkey,
                        )
                        if remaining:
                            await self._refresh_relation(conn, owner, rkey, now)
                            continue
                        await conn.execute(
                            """
                            DELETE FROM aura_graph_relations
                            WHERE owner_id = $1 AND subject_key = $2 AND predicate = $3
                              AND object_key = $4
                            """,
                            owner, *rkey,
                        )
                        removed += 1
                    entity_rows = await conn.fetch(
                        f"""
                        DELETE FROM aura_graph_entity_provenance
                        WHERE owner_id = $1 AND {column} = $2
                        RETURNING entity_key
                        """,
                        owner, value,
                    )
                    for key in dict.fromkeys(r["entity_key"] for r in entity_rows):
                        remaining = await conn.fetchval(
                            """
                            SELECT count(*) FROM aura_graph_entity_provenance
                            WHERE owner_id = $1 AND entity_key = $2
                            """,
                            owner, key,
                        )
                        if remaining:
                            await self._refresh_entity(conn, owner, key, now)
                            continue
                        # Aliases go with it (ON DELETE CASCADE).
                        await conn.execute(
                            "DELETE FROM aura_graph_entities WHERE owner_id = $1 AND key = $2",
                            owner, key,
                        )
                        removed += 1
        except Exception as exc:
            raise _unavailable(f"could not remove graph provenance: {exc}") from exc
        return removed

    # -- reads ---------------------------------------------------------------------------

    async def get_entity(self, key: str, *, owner_id: UserId | None) -> Entity | None:
        await self._ensure_schema()
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM aura_graph_entities WHERE owner_id = $1 AND key = $2",
                    _owner(owner_id),
                    key,
                )
        except Exception as exc:
            raise _unavailable(f"could not get graph entity: {exc}") from exc
        return self._entity(row) if row is not None else None

    async def find_entities(
        self,
        *,
        owner_id: UserId | None,
        name: str | None = None,
        type: str | None = None,
        limit: int = 50,
        include_expired: bool = False,
    ) -> Sequence[Entity]:
        await self._ensure_schema()
        pool = await self._pool._get_pool()
        needle = _like(normalize_alias(name)) if name else None
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM aura_graph_entities e
                    WHERE e.owner_id = $1
                      AND ($5::boolean OR e.valid_to IS NULL)
                      AND ($2::text IS NULL OR e.type = $2)
                      AND (
                        $3::text IS NULL
                        OR lower(e.name) LIKE $3 ESCAPE '\\'
                        OR EXISTS (
                            SELECT 1 FROM aura_graph_entity_aliases a
                            WHERE a.owner_id = e.owner_id AND a.entity_key = e.key
                              AND a.alias_norm LIKE $3 ESCAPE '\\'
                        )
                      )
                    ORDER BY e.mention_count DESC, e.key
                    LIMIT $4
                    """,
                    _owner(owner_id),
                    type,
                    needle,
                    limit,
                    include_expired,
                )
        except Exception as exc:
            raise _unavailable(f"could not find graph entities: {exc}") from exc
        return [self._entity(row) for row in rows]

    async def list_relations(
        self,
        *,
        owner_id: UserId | None,
        entity_keys: Sequence[str] | None = None,
        limit: int = 100,
        include_expired: bool = False,
    ) -> Sequence[Relation]:
        await self._ensure_schema()
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM aura_graph_relations
                    WHERE owner_id = $1
                      AND ($3::boolean OR valid_to IS NULL)
                      AND ($2::text[] IS NULL
                           OR subject_key = ANY($2::text[]) OR object_key = ANY($2::text[]))
                    ORDER BY confidence DESC, subject_key, predicate, object_key
                    LIMIT $4
                    """,
                    _owner(owner_id),
                    list(entity_keys) if entity_keys is not None else None,
                    include_expired,
                    limit,
                )
        except Exception as exc:
            raise _unavailable(f"could not list graph relations: {exc}") from exc
        return [self._relation(row) for row in rows]

    async def provenance(
        self,
        *,
        owner_id: UserId | None,
        entity_key: str | None = None,
        relation_key: tuple[str, str, str] | None = None,
    ) -> Sequence[FactProvenance]:
        await self._ensure_schema()
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                if entity_key is not None:
                    rows = await conn.fetch(
                        """
                        SELECT * FROM aura_graph_entity_provenance
                        WHERE owner_id = $1 AND entity_key = $2
                        ORDER BY COALESCE(document_version, 0), source_id
                        """,
                        _owner(owner_id), entity_key,
                    )
                elif relation_key is not None:
                    rows = await conn.fetch(
                        """
                        SELECT * FROM aura_graph_relation_provenance
                        WHERE owner_id = $1 AND subject_key = $2 AND predicate = $3
                          AND object_key = $4
                        ORDER BY COALESCE(document_version, 0), source_id
                        """,
                        _owner(owner_id), *relation_key,
                    )
                else:
                    return []
        except Exception as exc:
            raise _unavailable(f"could not read graph provenance: {exc}") from exc
        return [self._provenance(row) for row in rows]

    async def neighbours(
        self, key: str, *, owner_id: UserId | None, depth: int = 1, limit: int = 50
    ) -> tuple[Sequence[Entity], Sequence[Relation]]:
        """Bounded BFS over current facts. Depth is clamped to 3 regardless of the caller."""
        await self._ensure_schema()
        owner = _owner(owner_id)
        depth = max(1, min(depth, _MAX_DEPTH))
        pool = await self._pool._get_pool()
        frontier = {key}
        seen = {key}
        edges: list[Relation] = []
        try:
            async with pool.acquire() as conn:
                for _ in range(depth):
                    if not frontier or len(edges) >= limit:
                        break
                    rows = await conn.fetch(
                        """
                        SELECT * FROM aura_graph_relations
                        WHERE owner_id = $1 AND valid_to IS NULL
                          AND (subject_key = ANY($2::text[]) OR object_key = ANY($2::text[]))
                        ORDER BY subject_key, predicate, object_key
                        """,
                        owner,
                        list(frontier),
                    )
                    next_frontier: set[str] = set()
                    for row in rows:
                        relation = self._relation(row)
                        if relation.subject_key in frontier and relation.object_key not in seen:
                            edges.append(relation)
                            next_frontier.add(relation.object_key)
                        elif relation.object_key in frontier and relation.subject_key not in seen:
                            edges.append(relation)
                            next_frontier.add(relation.subject_key)
                        if len(edges) >= limit:
                            break
                    seen |= next_frontier
                    frontier = next_frontier
                entity_rows = await conn.fetch(
                    """
                    SELECT * FROM aura_graph_entities
                    WHERE owner_id = $1 AND valid_to IS NULL AND key = ANY($2::text[])
                    """,
                    owner,
                    list(seen),
                )
        except Exception as exc:
            raise _unavailable(f"could not traverse graph: {exc}") from exc
        entities = [self._entity(row) for row in entity_rows]
        return entities, edges[:limit]

    # -- row mapping ---------------------------------------------------------------------

    @staticmethod
    def _entity(row: Mapping[str, Any]) -> Entity:
        aliases = _json_value(row["aliases"], [])
        attributes = _json_value(row["attributes"], {})
        sources = _json_value(row["sources"], [])
        owner_raw = row["owner_id"]
        confidence = _get(row, "confidence", 0.5)
        return Entity(
            key=row["key"],
            name=row["name"],
            type=row["type"],
            owner_id=UserId(owner_raw) if owner_raw else None,
            aliases=tuple(aliases),
            attributes=dict(attributes or {}),
            mention_count=int(row["mention_count"]),
            first_seen=_utc(row["first_seen"]),
            last_seen=_utc(row["last_seen"]),
            sources=tuple(sources),
            confidence=float(confidence if confidence is not None else 0.5),
            valid_from=_utc_or_none(_get(row, "valid_from")),
            valid_to=_utc_or_none(_get(row, "valid_to")),
        )

    @staticmethod
    def _relation(row: Mapping[str, Any]) -> Relation:
        sources = _json_value(row["sources"], [])
        attributes = _json_value(row["attributes"], {})
        owner_raw = row["owner_id"]
        return Relation(
            subject_key=row["subject_key"],
            predicate=row["predicate"],
            object_key=row["object_key"],
            owner_id=UserId(owner_raw) if owner_raw else None,
            confidence=float(row["confidence"]),
            sources=tuple(sources),
            attributes=dict(attributes or {}),
            created_at=_utc(row["created_at"]),
            valid_from=_utc_or_none(_get(row, "valid_from")),
            valid_to=_utc_or_none(_get(row, "valid_to")),
        )

    @staticmethod
    def _provenance(row: Mapping[str, Any]) -> FactProvenance:
        version = row["document_version"]
        return FactProvenance(
            source_id=row["source_id"],
            document_id=row["document_id"],
            document_version=int(version) if version is not None else None,
            extractor=row["extractor"] or "",
            confidence=float(row["confidence"]),
            recorded_at=_utc_or_none(row["recorded_at"]),
            retired_at=_utc_or_none(row["retired_at"]),
        )
