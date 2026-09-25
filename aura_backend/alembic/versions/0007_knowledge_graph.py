"""knowledge graph: provenance, aliases, vocabulary, confidence and validity

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-25

Roadmap Phase 11 / GAP-023. Evolves the two graph tables ``PostgresGraphStore`` has been creating
for itself (``aura_graph_entities``, ``aura_graph_relations``) and adds what the knowledge-graph
pipeline needs so that no fact enters long-term knowledge without source, provenance and
confidence, and so deleting or superseding a document can remove or expire what it asserted:

* ``confidence`` on entities, and ``valid_from``/``valid_to`` on entities and relations (an
  expired fact is kept for history and invisible to retrieval);
* ``aura_graph_entity_provenance`` / ``aura_graph_relation_provenance``: one row per source per
  fact, with ``document_id``/``document_version``, extractor, confidence and ``retired_at``;
* ``aura_graph_entity_aliases``: every normalised surface form of an entity;
* ``aura_graph_entity_types`` / ``aura_graph_relation_types``: the allowed vocabulary, seeded from
  ``aura_core.domain.graph.DEFAULT_GRAPH_SCHEMA``;
* a backfill of provenance and aliases from the pre-existing ``sources``/``aliases`` JSONB.

Written as raw idempotent SQL rather than ``op.create_table`` and kept textually in step with
``aura_core/adapters/postgres_graph.py``'s ``GRAPH_SCHEMA`` (pinned by
``tests/unit/test_graph_schema_migration.py``): the adapter creates the same objects on first use,
so a gateway that wrote graph rows before this migration ran must not make ``alembic upgrade
head`` fail, and a fresh database must end up identical either way.

Every table is owner-scoped with ``owner_id`` TEXT (the v3 ``UserId`` value, ``''`` for global),
the same convention as ``aura_documents.owner_id``.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: (name, literal, description) -- must equal DEFAULT_GRAPH_SCHEMA.entity_types.
ENTITY_TYPES = (
    ('person', False, 'A named individual.'),
    ('organisation', False, 'A company, institution or team.'),
    ('place', False, 'A location.'),
    ('project', False, 'A named initiative or codebase.'),
    ('product', False, 'A product, service or system.'),
    ('event', False, 'A named occurrence.'),
    ('concept', False, 'A named thing of no more specific type.'),
    ('date', True, 'A calendar date.'),
    ('money', True, 'A monetary amount.'),
    ('email', True, 'An email address.'),
    ('url', True, 'A web address.'),
)

#: (predicate, subject types, object types, description) -- must equal
#: DEFAULT_GRAPH_SCHEMA.relation_types.
RELATION_TYPES = (
    ('works_at', ('concept', 'person'), ('concept', 'organisation', 'place', 'project'), 'Employment or affiliation.'),
    ('leads', ('concept', 'person'), ('concept', 'organisation', 'place', 'project'), 'Heads an organisation, team or project.'),
    ('reports_to', ('concept', 'person'), ('concept', 'person'), 'Line-management relation between people.'),
    ('manages', ('concept', 'organisation', 'person'), ('concept', 'event', 'organisation', 'person', 'place', 'product', 'project'), 'Owns, manages or operates something.'),
    ('located_in', ('concept', 'event', 'organisation', 'person', 'place', 'product', 'project'), ('concept', 'place'), 'Physical or organisational location.'),
    ('acquired', ('concept', 'organisation', 'person'), ('concept', 'organisation', 'product', 'project'), 'Acquisition or purchase.'),
    ('depends_on', ('concept', 'organisation', 'product', 'project'), ('concept', 'organisation', 'product', 'project'), 'A technical or operational dependency.'),
    ('part_of', ('concept', 'event', 'organisation', 'person', 'place', 'product', 'project'), ('concept', 'event', 'organisation', 'person', 'place', 'product', 'project'), 'Membership or containment.'),
    ('founded', ('concept', 'organisation', 'person'), ('concept', 'organisation', 'product', 'project'), 'Founded or created.'),
)

STATEMENTS = (
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


def _text(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _array(values: Sequence[str]) -> str:
    return "ARRAY[" + ", ".join(_text(v) for v in sorted(values)) + "]::text[]"


def seed_statements() -> tuple[str, ...]:
    entity_rows = ", ".join(
        f"({_text(name)}, {'true' if literal else 'false'}, {_text(description)})"
        for name, literal, description in ENTITY_TYPES
    )
    relation_rows = ", ".join(
        f"({_text(predicate)}, {_array(subjects)}, {_array(objects)}, {_text(description)})"
        for predicate, subjects, objects, description in RELATION_TYPES
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


def upgrade() -> None:
    for statement in (*STATEMENTS, *seed_statements()):
        op.execute(statement)


def downgrade() -> None:
    # Drops only what this revision added. The two fact tables predate it (the adapter created
    # them before any migration knew about them), so they stay, minus the new columns.
    for table in (
        "aura_graph_relation_provenance",
        "aura_graph_entity_provenance",
        "aura_graph_entity_aliases",
        "aura_graph_relation_types",
        "aura_graph_entity_types",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.execute("DROP INDEX IF EXISTS ix_aura_graph_entities_current")
    op.execute("DROP INDEX IF EXISTS ix_aura_graph_relations_current")
    for table, columns in (
        ("aura_graph_entities", ("confidence", "valid_from", "valid_to")),
        ("aura_graph_relations", ("valid_from", "valid_to")),
    ):
        for column in columns:
            op.execute(f"ALTER TABLE IF EXISTS {table} DROP COLUMN IF EXISTS {column}")
