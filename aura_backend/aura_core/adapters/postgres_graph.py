"""Postgres implementation of the ``GraphStore`` port.

A separate module from ``postgres_store.py`` so that file does not keep growing.
Selected when the document backend is postgres; shares ``PostgresPool``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from ..domain.errors import DependencyUnavailableError
from ..domain.identifiers import UserId
from ..domain.knowledge import Entity, GraphDelta, Relation
from .postgres_store import PostgresPool

logger = logging.getLogger(__name__)

__all__ = ["PostgresGraphStore"]

_MAX_DEPTH = 3

_SCHEMA = (
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
)


def _unavailable(message: str, **details: Any) -> DependencyUnavailableError:
    return DependencyUnavailableError(message, dependency="postgres", details=details or None)


def _utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _owner(owner_id: UserId | None) -> str:
    return owner_id.value if owner_id else ""


def _json_value(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        return json.loads(value)
    return value


def _deleted_count(result: Any) -> int:
    if not result:
        return 0
    parts = str(result).split()
    try:
        return int(parts[-1])
    except (TypeError, ValueError):
        return 0


class PostgresGraphStore:
    def __init__(self, url: str, *, pool: PostgresPool | None = None) -> None:
        self._pool = pool or PostgresPool(url)
        self._schema_ready = False

    async def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                for statement in _SCHEMA:
                    await conn.execute(statement)
        except Exception as exc:
            raise _unavailable(f"could not create the graph schema: {exc}") from exc
        self._schema_ready = True

    async def apply(self, delta: GraphDelta, *, owner_id: UserId | None) -> None:
        await self._ensure_schema()
        owner = _owner(owner_id)
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    for entity in delta.entities:
                        await self._upsert_entity(conn, owner, entity)
                    for relation in delta.relations:
                        await self._upsert_relation(conn, owner, relation)
        except Exception as exc:
            raise _unavailable(f"could not apply graph delta: {exc}") from exc

    async def _upsert_entity(self, conn: Any, owner: str, entity: Entity) -> None:
        row = await conn.fetchrow(
            "SELECT * FROM aura_graph_entities WHERE owner_id = $1 AND key = $2",
            owner,
            entity.key,
        )
        merged = self._entity(row).merged_with(entity) if row is not None else entity
        await conn.execute(
            """
            INSERT INTO aura_graph_entities (
                owner_id, key, name, type, aliases, attributes, mention_count,
                first_seen, last_seen, sources
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            ON CONFLICT (owner_id, key) DO UPDATE SET
                name = EXCLUDED.name,
                type = EXCLUDED.type,
                aliases = EXCLUDED.aliases,
                attributes = EXCLUDED.attributes,
                mention_count = EXCLUDED.mention_count,
                last_seen = EXCLUDED.last_seen,
                sources = EXCLUDED.sources
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
        )

    async def _upsert_relation(self, conn: Any, owner: str, relation: Relation) -> None:
        row = await conn.fetchrow(
            """
            SELECT * FROM aura_graph_relations
            WHERE owner_id = $1 AND subject_key = $2 AND predicate = $3 AND object_key = $4
            """,
            owner,
            relation.subject_key,
            relation.predicate,
            relation.object_key,
        )
        if row is not None and relation.confidence <= float(row["confidence"]):
            return
        await conn.execute(
            """
            INSERT INTO aura_graph_relations (
                owner_id, subject_key, predicate, object_key, confidence,
                sources, attributes, created_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            ON CONFLICT (owner_id, subject_key, predicate, object_key) DO UPDATE SET
                confidence = EXCLUDED.confidence,
                sources = EXCLUDED.sources,
                attributes = EXCLUDED.attributes
            """,
            owner,
            relation.subject_key,
            relation.predicate,
            relation.object_key,
            relation.confidence,
            json.dumps(list(relation.sources)),
            json.dumps(dict(relation.attributes)),
            relation.created_at,
        )

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
    ) -> Sequence[Entity]:
        await self._ensure_schema()
        pool = await self._pool._get_pool()
        needle = f"%{name.lower()}%" if name else None
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM aura_graph_entities
                    WHERE owner_id = $1
                      AND ($2::text IS NULL OR type = $2)
                      AND (
                        $3::text IS NULL
                        OR lower(name) LIKE $3
                        OR EXISTS (
                            SELECT 1 FROM jsonb_array_elements_text(aliases) AS alias
                            WHERE lower(alias) LIKE $3
                        )
                      )
                    ORDER BY mention_count DESC
                    LIMIT $4
                    """,
                    _owner(owner_id),
                    type,
                    needle,
                    limit,
                )
        except Exception as exc:
            raise _unavailable(f"could not find graph entities: {exc}") from exc
        return [self._entity(row) for row in rows]

    async def neighbours(
        self, key: str, *, owner_id: UserId | None, depth: int = 1, limit: int = 50
    ) -> tuple[Sequence[Entity], Sequence[Relation]]:
        """Bounded BFS. Depth is clamped to 3 regardless of the caller."""
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
                        WHERE owner_id = $1
                          AND (subject_key = ANY($2::text[]) OR object_key = ANY($2::text[]))
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
                    WHERE owner_id = $1 AND key = ANY($2::text[])
                    """,
                    owner,
                    list(seen),
                )
        except Exception as exc:
            raise _unavailable(f"could not traverse graph: {exc}") from exc
        entities = [self._entity(row) for row in entity_rows]
        return entities, edges[:limit]

    async def revert(self, source: str, *, owner_id: UserId | None) -> int:
        await self._ensure_schema()
        owner = _owner(owner_id)
        pool = await self._pool._get_pool()
        source_json = json.dumps([source])
        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    rel = await conn.execute(
                        """
                        DELETE FROM aura_graph_relations
                        WHERE owner_id = $1 AND sources @> $2::jsonb
                        """,
                        owner,
                        source_json,
                    )
                    ent = await conn.execute(
                        """
                        DELETE FROM aura_graph_entities
                        WHERE owner_id = $1 AND sources = $2::jsonb
                        """,
                        owner,
                        source_json,
                    )
        except Exception as exc:
            raise _unavailable(f"could not revert graph source: {exc}") from exc
        return _deleted_count(rel) + _deleted_count(ent)

    @staticmethod
    def _entity(row: Mapping[str, Any]) -> Entity:
        aliases = _json_value(row["aliases"], [])
        attributes = _json_value(row["attributes"], {})
        sources = _json_value(row["sources"], [])
        owner_raw = row["owner_id"]
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
        )
