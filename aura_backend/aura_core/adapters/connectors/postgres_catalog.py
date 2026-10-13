"""PostgreSQL discovery: catalog capture and the deterministic catalog -> artifacts mapping (AP3).

Two halves, deliberately separable:

* :func:`capture_catalog` runs the catalog queries and a bounded profile against a live connection
  and returns a **catalog snapshot** -- plain JSON-able rows. It is the only part that talks to
  the database.
* :func:`artifacts_from_catalog` turns a snapshot into a :class:`DiscoveryReport`. It is pure, so
  golden tests replay recorded snapshots of the reference pilots with no database at all, and
  the opt-in live test proves a fresh capture equals the recorded one.

What the capture reads, and what it does not:

* Structure from ``pg_catalog`` -- relations, columns, keys, checks, enum labels, table grants and
  comments. ``pg_class`` is visible to every role, so a read-only role sees the whole shape even
  of tables it may not read; those are marked ``readable: false`` and not profiled.
* Profiles from a bounded sample (``LIMIT sample_rows``) computed **in SQL**: counts, distinct
  counts and pattern-match counts. Row values never cross into the process -- unless a Super
  Admin opts in for one run (``DiscoveryBudget.sample_values``), and then only the distinct values
  of low-cardinality text columns.
* Everything runs in one read-only transaction with a ``statement_timeout``; each table's profile
  runs under its own savepoint, so a permission refusal on one table is recorded and the rest
  continue.
* Owner grants are excluded (they are implicit and name whoever created the schema, which would
  make the snapshot depend on the environment rather than the system).
"""

from __future__ import annotations

import time
from typing import Any, Mapping, Sequence

from ...domain.discovery import (
    ArtifactKind,
    DiscoveryBudget,
    DiscoveryIssue,
    DiscoveryReport,
    RawArtifact,
    quote_identifier as q,
)
from ...domain.untrusted import UntrustedText

__all__ = [
    "capture_catalog",
    "artifacts_from_catalog",
    "CATALOG_EXTRACTOR",
    "PROFILE_EXTRACTOR",
]

CATALOG_EXTRACTOR = "pg_catalog"
PROFILE_EXTRACTOR = "pg_profile"

_SCHEMA_FILTER = """
    n.nspname NOT IN ('pg_catalog', 'information_schema')
    AND n.nspname NOT LIKE 'pg\\_%'
    AND ($1::text[] IS NULL OR n.nspname = ANY($1::text[]))
"""
_RELKINDS = "('r', 'p', 'v', 'm', 'f')"

RELATIONS_SQL = f"""
SELECT n.nspname AS schema, c.relname AS name, c.relkind::text AS kind,
       obj_description(c.oid, 'pg_class') AS comment,
       has_table_privilege(c.oid, 'SELECT') AS readable
  FROM pg_catalog.pg_class c
  JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relkind IN {_RELKINDS} AND NOT c.relispartition AND {_SCHEMA_FILTER}
 ORDER BY 1, 2
 LIMIT $2
"""

COLUMNS_SQL = f"""
SELECT n.nspname AS schema, c.relname AS "table", a.attname AS name, a.attnum AS position,
       format_type(a.atttypid, a.atttypmod) AS data_type, a.attnotnull AS not_null,
       col_description(c.oid, a.attnum) AS comment, t.typtype = 'e' AS is_enum,
       tn.nspname AS type_schema, t.typname AS type_name, a.atthasdef AS has_default
  FROM pg_catalog.pg_attribute a
  JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
  JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
  JOIN pg_catalog.pg_type t ON t.oid = a.atttypid
  JOIN pg_catalog.pg_namespace tn ON tn.oid = t.typnamespace
 WHERE a.attnum > 0 AND NOT a.attisdropped
   AND c.relkind IN {_RELKINDS} AND NOT c.relispartition AND {_SCHEMA_FILTER}
 ORDER BY 1, 2, 4
 LIMIT $2
"""

CONSTRAINTS_SQL = f"""
SELECT n.nspname AS schema, c.relname AS "table", con.conname AS name, con.contype::text AS type,
       ARRAY(SELECT a.attname FROM unnest(con.conkey) WITH ORDINALITY k(attnum, ord)
               JOIN pg_catalog.pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum
              ORDER BY k.ord) AS columns,
       rn.nspname AS ref_schema, rc.relname AS ref_table,
       ARRAY(SELECT a.attname FROM unnest(con.confkey) WITH ORDINALITY k(attnum, ord)
               JOIN pg_catalog.pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.attnum
              ORDER BY k.ord) AS ref_columns,
       CASE WHEN con.contype = 'c' THEN pg_get_constraintdef(con.oid) END AS definition,
       obj_description(con.oid, 'pg_constraint') AS comment
  FROM pg_catalog.pg_constraint con
  JOIN pg_catalog.pg_class c ON c.oid = con.conrelid
  JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
  LEFT JOIN pg_catalog.pg_class rc ON rc.oid = con.confrelid
  LEFT JOIN pg_catalog.pg_namespace rn ON rn.oid = rc.relnamespace
 WHERE con.contype IN ('p', 'f', 'u', 'c') AND {_SCHEMA_FILTER}
 ORDER BY 1, 2, 3
 LIMIT $2
"""

ENUMS_SQL = f"""
SELECT n.nspname AS schema, t.typname AS name,
       array_agg(e.enumlabel ORDER BY e.enumsortorder) AS labels
  FROM pg_catalog.pg_type t
  JOIN pg_catalog.pg_enum e ON e.enumtypid = t.oid
  JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace
 WHERE {_SCHEMA_FILTER}
 GROUP BY 1, 2
 ORDER BY 1, 2
 LIMIT $2
"""

GRANTS_SQL = f"""
SELECT n.nspname AS schema, c.relname AS "table",
       CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END AS grantee,
       a.privilege_type AS privilege
  FROM pg_catalog.pg_class c
  JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace,
       LATERAL aclexplode(c.relacl) a
 WHERE c.relkind IN {_RELKINDS} AND NOT c.relispartition AND a.grantee <> c.relowner
   AND {_SCHEMA_FILTER}
 ORDER BY 1, 2, 3, 4
 LIMIT $2
"""

_TEXTUAL = ("text", "character varying", "character", "citext", "name", "uuid")
#: Pattern name -> POSIX regex, evaluated in SQL on ``col::text``. Order is precedence.
_PATTERNS = (
    ("uuid", r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$"),
    ("email", r"^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$"),
    ("url", r"^https?://"),
    ("iso_date", r"^[0-9]{4}-[0-9]{2}-[0-9]{2}"),
    ("numeric_text", r"^-?[0-9]+(\.[0-9]+)?$"),
)
_CATEGORICAL_MAX_DISTINCT = 20
_DOMINANT = 0.9
_MAX_VALUE_CHARS = 64


def _sql_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _is_textual(data_type: str) -> bool:
    return data_type.startswith(_TEXTUAL)


# ------------------------------------------------------------------------------------- capture


async def capture_catalog(
    conn: Any,
    *,
    budget: DiscoveryBudget,
    schemas: Sequence[str] | None = None,
    timeout_s: float = 15.0,
    asyncpg_module: Any = None,
) -> dict[str, Any]:
    """Run the catalog queries and the bounded profile on ``conn``; return a snapshot.

    The caller opens the connection; this function opens the read-only transaction.
    """
    import asyncpg as _asyncpg  # lazy: vendor driver stays out of import time

    asyncpg = asyncpg_module or _asyncpg
    deadline = time.monotonic() + budget.max_seconds
    limit = budget.max_objects + 1
    schema_arg = list(schemas) if schemas else None
    snapshot: dict[str, Any] = {"issues": [], "truncated": False}
    async with conn.transaction(readonly=True, isolation="repeatable_read"):
        await conn.execute(f"SET LOCAL statement_timeout = {int(timeout_s * 1000)}")
        for key, sql in (
            ("relations", RELATIONS_SQL),
            ("columns", COLUMNS_SQL),
            ("constraints", CONSTRAINTS_SQL),
            ("enums", ENUMS_SQL),
            ("grants", GRANTS_SQL),
        ):
            rows = [dict(r) for r in await conn.fetch(sql, schema_arg, limit)]
            if len(rows) == limit:
                rows = rows[:-1]
                snapshot["truncated"] = True
                snapshot["issues"].append({"code": "budget_exhausted", "locator": key})
            snapshot[key] = [_jsonable(r) for r in rows]
        snapshot["profiles"] = await _profile(conn, asyncpg, snapshot, budget, deadline)
    return snapshot


async def _profile(conn: Any, asyncpg: Any, snapshot: dict[str, Any], budget: DiscoveryBudget, deadline: float) -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []
    if budget.sample_rows == 0 or budget.max_profiled_columns == 0:
        return profiles
    columns_by_table: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for col in snapshot["columns"]:
        columns_by_table.setdefault((col["schema"], col["table"]), []).append(col)
    remaining = budget.max_profiled_columns
    for rel in snapshot["relations"]:
        if rel["kind"] not in ("r", "p", "m"):
            continue
        locator = f"table:{q(rel['schema'])}.{q(rel['name'])}"
        if not rel["readable"]:
            snapshot["issues"].append({"code": "not_readable", "locator": locator})
            continue
        if remaining <= 0 or time.monotonic() > deadline:
            snapshot["truncated"] = True
            snapshot["issues"].append({"code": "profile_budget_exhausted", "locator": locator})
            continue
        cols = columns_by_table.get((rel["schema"], rel["name"]), [])[:remaining]
        remaining -= len(cols)
        try:
            async with conn.transaction():  # savepoint: one refusal must not abort the run
                profiles.extend(await _profile_table(conn, rel, cols, budget))
        except asyncpg.InsufficientPrivilegeError:
            snapshot["issues"].append({"code": "permission_denied", "locator": locator})
        except asyncpg.QueryCanceledError:
            snapshot["issues"].append({"code": "profile_timeout", "locator": locator})
    return profiles


async def _profile_table(conn: Any, rel: Mapping[str, Any], cols: list[dict[str, Any]], budget: DiscoveryBudget) -> list[dict[str, Any]]:
    if not cols:
        return []
    exprs = ["count(*) AS n"]
    for i, col in enumerate(cols):
        ident = _sql_ident(col["name"])
        exprs.append(f"count({ident}) AS nn_{i}")
        exprs.append(f"count(DISTINCT {ident}::text) AS d_{i}")
        if _is_textual(col["data_type"]):
            for p, (pname, regex) in enumerate(_PATTERNS):
                literal = regex.replace("'", "''")
                exprs.append(f"count(*) FILTER (WHERE {ident}::text ~ '{literal}') AS p_{i}_{p}")
    source = f"{_sql_ident(rel['schema'])}.{_sql_ident(rel['name'])}"
    select_cols = ", ".join(_sql_ident(c["name"]) for c in cols)
    sql = f"SELECT {', '.join(exprs)} FROM (SELECT {select_cols} FROM {source} LIMIT $1) s"
    row = await conn.fetchrow(sql, budget.sample_rows)
    out: list[dict[str, Any]] = []
    for i, col in enumerate(cols):
        non_null, distinct = int(row[f"nn_{i}"]), int(row[f"d_{i}"])
        patterns = {}
        if _is_textual(col["data_type"]):
            patterns = {pname: int(row[f"p_{i}_{p}"]) for p, (pname, _) in enumerate(_PATTERNS)}
        profile = {
            "schema": rel["schema"], "table": rel["name"], "column": col["name"],
            "data_type": col["data_type"], "sampled_rows": int(row["n"]),
            "non_null": non_null, "distinct": distinct, "patterns": patterns, "values": None,
        }
        if budget.sample_values and _is_textual(col["data_type"]) and 0 < distinct <= _CATEGORICAL_MAX_DISTINCT:
            ident = _sql_ident(col["name"])
            values = await conn.fetch(
                f"SELECT DISTINCT left({ident}::text, {_MAX_VALUE_CHARS}) AS v FROM "
                f"(SELECT {ident} FROM {source} LIMIT $1) s WHERE {ident} IS NOT NULL ORDER BY 1",
                budget.sample_rows,
            )
            profile["values"] = [r["v"] for r in values]
        out.append(profile)
    return out


def _jsonable(row: Mapping[str, Any]) -> dict[str, Any]:
    return {k: (list(v) if isinstance(v, (list, tuple)) else v) for k, v in row.items()}


# ------------------------------------------------------------------------------------- mapping


def _text(value: Any) -> UntrustedText:
    return UntrustedText.of(str(value))


def _optional_text(value: Any) -> UntrustedText | None:
    return UntrustedText.of(str(value)) if value else None


def _relation_locator(schema: str, name: str, kind: str) -> str:
    prefix = "view" if kind in ("v", "m") else "table"
    return f"{prefix}:{q(schema)}.{q(name)}"


def _classify(profile: Mapping[str, Any]) -> tuple[str | None, bool]:
    """(dominant pattern, unique in sample)."""
    non_null, distinct, sampled = profile["non_null"], profile["distinct"], profile["sampled_rows"]
    unique = sampled > 0 and non_null == sampled and distinct == non_null
    for pname, _ in _PATTERNS:
        count = profile["patterns"].get(pname, 0)
        if non_null and count >= _DOMINANT * non_null:
            return pname, unique
    if (
        _is_textual(profile["data_type"])
        and non_null >= 10
        and 0 < distinct <= _CATEGORICAL_MAX_DISTINCT
        and distinct <= 0.5 * non_null
    ):
        return "categorical", unique
    return None, unique


def artifacts_from_catalog(
    source_id: str, snapshot: Mapping[str, Any], budget: DiscoveryBudget, *, extractor: str = "postgres",
    catalog_extractor: str = CATALOG_EXTRACTOR, profile_extractor: str = PROFILE_EXTRACTOR,
) -> DiscoveryReport:
    """The deterministic mapping from a catalog snapshot to raw artifacts.

    The extractor names are what provenance cites; another source that produces the same snapshot
    shape (the Files connector, AP10) passes its own, so its artifacts never claim ``pg_catalog``.
    """
    artifacts: list[RawArtifact] = []
    issues = [DiscoveryIssue(i["code"], i.get("locator", "")) for i in snapshot.get("issues", [])]
    relation_locators: dict[tuple[str, str], str] = {}

    for rel in snapshot.get("relations", []):
        locator = _relation_locator(rel["schema"], rel["name"], rel["kind"])
        relation_locators[(rel["schema"], rel["name"])] = locator
        artifacts.append(RawArtifact(
            kind=ArtifactKind.VIEW if rel["kind"] in ("v", "m") else ArtifactKind.TABLE,
            locator=locator, name=_text(rel["name"]), extractor=catalog_extractor,
            description=_optional_text(rel.get("comment")),
            attributes={"schema": rel["schema"], "relkind": rel["kind"], "readable": bool(rel["readable"])},
        ))

    for col in snapshot.get("columns", []):
        parent = relation_locators.get((col["schema"], col["table"]))
        if parent is None:
            continue  # its relation was cut by the budget
        attrs: dict[str, Any] = {
            "data_type": col["data_type"], "nullable": not col["not_null"],
            "position": int(col["position"]), "has_default": bool(col["has_default"]),
        }
        if col.get("is_enum"):
            attrs["enum_type"] = f"enum:{q(col['type_schema'])}.{q(col['type_name'])}"
        artifacts.append(RawArtifact(
            kind=ArtifactKind.COLUMN,
            locator=f"column:{q(col['schema'])}.{q(col['table'])}.{q(col['name'])}",
            name=_text(col["name"]), extractor=catalog_extractor,
            description=_optional_text(col.get("comment")), parent=parent, attributes=attrs,
        ))

    kinds = {"p": (ArtifactKind.PRIMARY_KEY, "pk"), "u": (ArtifactKind.UNIQUE, "unique"),
             "f": (ArtifactKind.FOREIGN_KEY, "fk"), "c": (ArtifactKind.CHECK, "check")}
    for con in snapshot.get("constraints", []):
        parent = relation_locators.get((con["schema"], con["table"]))
        if parent is None:
            continue
        kind, prefix = kinds[con["type"]]
        attrs = {"columns": tuple(con.get("columns") or ())}
        if con["type"] == "f":
            attrs["ref_table"] = relation_locators.get(
                (con["ref_schema"], con["ref_table"]), f"table:{q(con['ref_schema'])}.{q(con['ref_table'])}"
            )
            attrs["ref_columns"] = tuple(con.get("ref_columns") or ())
        if con["type"] == "c" and con.get("definition"):
            definition = str(con["definition"])
            if len(definition) > 512:
                definition = definition[:509] + "..."
                issues.append(DiscoveryIssue("check_definition_truncated", f"check:{q(con['schema'])}.{q(con['table'])}.{q(con['name'])}"))
            attrs["definition"] = definition
        artifacts.append(RawArtifact(
            kind=kind, locator=f"{prefix}:{q(con['schema'])}.{q(con['table'])}.{q(con['name'])}",
            name=_text(con["name"]), extractor=catalog_extractor,
            description=_optional_text(con.get("comment")), parent=parent, attributes=attrs,
        ))

    for enum_row in snapshot.get("enums", []):
        artifacts.append(RawArtifact(
            kind=ArtifactKind.ENUM_TYPE, locator=f"enum:{q(enum_row['schema'])}.{q(enum_row['name'])}",
            name=_text(enum_row["name"]), extractor=catalog_extractor,
            attributes={"labels": tuple(enum_row["labels"])},
        ))

    grants: dict[tuple[str, str, str], set[str]] = {}
    for grant in snapshot.get("grants", []):
        grants.setdefault((grant["grantee"], grant["schema"], grant["table"]), set()).add(grant["privilege"])
    for grantee in sorted({g for g, _, _ in grants}):
        artifacts.append(RawArtifact(
            kind=ArtifactKind.ROLE, locator=f"role:{q(grantee)}", name=_text(grantee),
            extractor=catalog_extractor,
        ))
    for (grantee, schema, table), privileges in sorted(grants.items()):
        parent = relation_locators.get((schema, table))
        if parent is None:
            continue
        artifacts.append(RawArtifact(
            kind=ArtifactKind.GRANT, locator=f"grant:{q(grantee)}@{q(schema)}.{q(table)}",
            name=_text(f"{grantee} on {schema}.{table}"), extractor=catalog_extractor, parent=parent,
            attributes={"role": f"role:{q(grantee)}", "privileges": tuple(sorted(privileges))},
        ))

    for prof in snapshot.get("profiles", []):
        column = f"column:{q(prof['schema'])}.{q(prof['table'])}.{q(prof['column'])}"
        pattern, unique = _classify(prof)
        sampled, non_null, distinct = prof["sampled_rows"], prof["non_null"], prof["distinct"]
        attrs = {
            "sampled_rows": sampled, "non_null": non_null, "distinct": distinct,
            "null_ratio": round(1 - non_null / sampled, 4) if sampled else 0.0,
            "distinct_ratio": round(distinct / non_null, 4) if non_null else 0.0,
            "pattern": pattern, "unique_in_sample": unique,
        }
        if prof.get("values") is not None:
            attrs["values"] = tuple(prof["values"])
        artifacts.append(RawArtifact(
            kind=ArtifactKind.COLUMN_PROFILE,
            locator=f"profile:{q(prof['schema'])}.{q(prof['table'])}.{q(prof['column'])}",
            name=_text(prof["column"]), extractor=profile_extractor, parent=column, attributes=attrs,
        ))

    truncated = bool(snapshot.get("truncated"))
    if len(artifacts) > budget.max_objects:
        artifacts = sorted(artifacts, key=lambda a: a.locator)[: budget.max_objects]
        truncated = True
        issues.append(DiscoveryIssue("budget_exhausted", "artifacts"))
    return DiscoveryReport.build(source_id, extractor, artifacts, issues=issues, truncated=truncated)
