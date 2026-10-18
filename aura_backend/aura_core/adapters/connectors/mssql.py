"""SQL Server connector (AP10): reachability, privilege inspection and discovery.

Discovery only, behind the same port as the PostgreSQL and MySQL connectors
(``DiscoveryConnector``); templated reads need a T-SQL dialect behind ``render_sql`` and are a
later step, so the compiler drops any capability on a SQL Server table with a recorded reason.

**Capture** (:func:`capture_mssql`) reads the ``sys`` catalog views of one database (the endpoint's
path), every user schema: tables and views, columns, primary/unique keys, foreign keys, CHECK
constraints, ``MS_Description`` extended properties as comments, and object/schema/database
``GRANT``\\ s, plus a bounded profile computed by the server. SQL Server has no read-only session,
so the whole capture runs inside one transaction that is always rolled back; the statements are
constants, and the account itself is what activation vouches for (below). Metadata visibility
applies: without ``VIEW DEFINITION`` a CHECK definition reads as NULL and other principals'
grants are invisible, so each is an issue (``check_definition_hidden``, ``grants_not_visible``),
not a failure. The connector's own user is excluded from the grants, as owner grants are on
PostgreSQL.

**Mapping** (:func:`snapshot_from_mssql`) is pure and produces the PostgreSQL snapshot shape, so
``artifacts_from_catalog`` and everything after it are shared. Types take the twin's canonical
names (``nvarchar(n)`` -> ``character varying(n)``, ``bit`` -> ``boolean``, ``datetime2`` ->
``timestamp without time zone``, ``datetimeoffset`` -> ``timestamp with time zone``,
``uniqueidentifier`` -> ``uuid``). SQL Server stores ``col IN ('a', 'b')`` as the OR chain
``([col]='b' OR [col]='a')``; :func:`normalize_check` turns exactly that shape back into
``CHECK (col IN ('a', 'b'))`` (declaration order restored), so it reads as a closed value set as
on PostgreSQL. Any other boolean structure is left alone, and stays not-a-value-set.

**Privileges** (:func:`privilege_findings`): the account's *effective* server and database
permissions (``fn_my_permissions``, which follows role membership, so ``db_datawriter`` or
``sysadmin`` is found) plus object- and schema-level write, DDL and EXECUTE paths
(``HAS_PERMS_BY_NAME``) and any ``WITH GRANT OPTION``. Only ``CONNECT``, ``SELECT``, ``SHOWPLAN``
and the ``VIEW ...`` family are clean; anything else is a finding, and activation refuses any
finding (``ConnectorHealth.write_capable``). EXECUTE is one: a procedure runs with its owner's
rights through ownership chaining, so it is a write path whatever the account's table grants say.

**TLS**: python-tds only encrypts with a CA to verify against, so the modes are ``verify-full``
(the default), ``verify-ca`` (chain only, no host name check) and ``disable``. The CA is the
``ca_file`` option, or the system bundle. A self-signed server certificate is trusted by giving
that certificate as ``ca_file`` -- there is deliberately no "encrypt but trust anything" mode.
python-tds's own host-name check is replaced (:func:`_validate_host`), because it is the only
check that runs before the password is sent and as shipped it cannot read a subjectAltName.

python-tds is pure Python and synchronous; each call runs in a worker thread, so nothing here
holds the event loop.
"""

from __future__ import annotations

import asyncio
import re
import ssl
import time
from typing import Any, Mapping
from urllib.parse import unquote, urlsplit

from ...domain.connectors import CallerIdentity, ConnectorDescriptor, ConnectorHealth, Credential, HealthStatus
from ...domain.discovery import DiscoveryBudget, DiscoveryReport, quote_identifier as q
from ...domain.errors import DependencyUnavailableError, ToolExecutionError
from ...domain.templates import ReadTemplate
from ...observability.logging import get_logger
from .postgres_catalog import _CATEGORICAL_MAX_DISTINCT, _MAX_VALUE_CHARS, artifacts_from_catalog

__all__ = [
    "SqlServerConnectorFactory", "SqlServerConnector", "capture_mssql", "snapshot_from_mssql",
    "privilege_findings", "canonical_type", "normalize_check", "host_matches", "CATALOG_EXTRACTOR",
    "PROFILE_EXTRACTOR",
]

logger = get_logger("adapters.connectors.mssql")

CATALOG_EXTRACTOR = "mssql_catalog"
PROFILE_EXTRACTOR = "mssql_profile"
_TLS_MODES = {"disable", "verify-ca", "verify-full"}
_SCHEMES = {"mssql", "sqlserver"}

RELATIONS_SQL = """
SELECT TOP (%s) s.name AS [schema], o.name AS name, RTRIM(o.type) AS type,
       CAST(ep.value AS NVARCHAR(4000)) AS comment,
       HAS_PERMS_BY_NAME(QUOTENAME(s.name) + '.' + QUOTENAME(o.name), 'OBJECT', 'SELECT') AS readable
  FROM sys.objects o
  JOIN sys.schemas s ON s.schema_id = o.schema_id
  LEFT JOIN sys.extended_properties ep
    ON ep.class = 1 AND ep.major_id = o.object_id AND ep.minor_id = 0 AND ep.name = 'MS_Description'
 WHERE o.type IN ('U', 'V') AND o.is_ms_shipped = 0
 ORDER BY s.name, o.name
"""

COLUMNS_SQL = """
SELECT TOP (%s) s.name AS [schema], o.name AS [table], c.name AS name, c.column_id AS position,
       t.name AS type_name, TYPE_NAME(t.system_type_id) AS base_type, t.is_user_defined AS is_user_defined,
       c.max_length AS max_length, c.precision AS precision, c.scale AS scale,
       c.is_nullable AS is_nullable, c.is_identity AS is_identity, c.is_computed AS is_computed,
       CASE WHEN c.default_object_id <> 0 THEN 1 ELSE 0 END AS has_default,
       CAST(ep.value AS NVARCHAR(4000)) AS comment
  FROM sys.columns c
  JOIN sys.objects o ON o.object_id = c.object_id
  JOIN sys.schemas s ON s.schema_id = o.schema_id
  JOIN sys.types t ON t.user_type_id = c.user_type_id
  LEFT JOIN sys.extended_properties ep
    ON ep.class = 1 AND ep.major_id = c.object_id AND ep.minor_id = c.column_id AND ep.name = 'MS_Description'
 WHERE o.type IN ('U', 'V') AND o.is_ms_shipped = 0
 ORDER BY s.name, o.name, c.column_id
"""

KEYS_SQL = """
SELECT TOP (%s) s.name AS [schema], o.name AS [table], kc.name AS name, RTRIM(kc.type) AS type,
       col.name AS column_name, ic.key_ordinal AS ordinal
  FROM sys.key_constraints kc
  JOIN sys.objects o ON o.object_id = kc.parent_object_id
  JOIN sys.schemas s ON s.schema_id = o.schema_id
  JOIN sys.index_columns ic ON ic.object_id = kc.parent_object_id AND ic.index_id = kc.unique_index_id
  JOIN sys.columns col ON col.object_id = ic.object_id AND col.column_id = ic.column_id
 WHERE o.is_ms_shipped = 0 AND ic.key_ordinal > 0
 ORDER BY s.name, o.name, kc.name, ic.key_ordinal
"""

FOREIGN_KEYS_SQL = """
SELECT TOP (%s) s.name AS [schema], o.name AS [table], fk.name AS name, pc.name AS column_name,
       rs.name AS ref_schema, ro.name AS ref_table, rc.name AS ref_column, fkc.constraint_column_id AS ordinal
  FROM sys.foreign_keys fk
  JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id = fk.object_id
  JOIN sys.objects o ON o.object_id = fk.parent_object_id
  JOIN sys.schemas s ON s.schema_id = o.schema_id
  JOIN sys.columns pc ON pc.object_id = fkc.parent_object_id AND pc.column_id = fkc.parent_column_id
  JOIN sys.objects ro ON ro.object_id = fkc.referenced_object_id
  JOIN sys.schemas rs ON rs.schema_id = ro.schema_id
  JOIN sys.columns rc ON rc.object_id = fkc.referenced_object_id AND rc.column_id = fkc.referenced_column_id
 WHERE o.is_ms_shipped = 0
 ORDER BY s.name, o.name, fk.name, fkc.constraint_column_id
"""

CHECKS_SQL = """
SELECT TOP (%s) s.name AS [schema], o.name AS [table], cc.name AS name, cc.definition AS definition
  FROM sys.check_constraints cc
  JOIN sys.objects o ON o.object_id = cc.parent_object_id
  JOIN sys.schemas s ON s.schema_id = o.schema_id
 WHERE o.is_ms_shipped = 0
 ORDER BY s.name, o.name, cc.name
"""

#: Table-affecting grants at object (class 1), schema (3) and database (0) scope.
GRANTS_SQL = """
SELECT pr.name AS grantee, dp.class AS class, dp.permission_name AS privilege,
       os.name AS [schema], o.name AS [table], ss.name AS grant_schema
  FROM sys.database_permissions dp
  JOIN sys.database_principals pr ON pr.principal_id = dp.grantee_principal_id
  LEFT JOIN sys.objects o ON dp.class = 1 AND o.object_id = dp.major_id
  LEFT JOIN sys.schemas os ON os.schema_id = o.schema_id
  LEFT JOIN sys.schemas ss ON dp.class = 3 AND ss.schema_id = dp.major_id
 WHERE dp.class IN (0, 1, 3) AND dp.minor_id = 0 AND dp.state IN ('G', 'W')
   AND dp.permission_name IN ('SELECT', 'INSERT', 'UPDATE', 'DELETE', 'REFERENCES')
   AND (dp.class <> 1 OR (o.type IN ('U', 'V') AND o.is_ms_shipped = 0))
 ORDER BY pr.name, dp.class, os.name, o.name, ss.name, dp.permission_name
"""

ACCOUNT_SQL = """
SELECT USER_NAME() AS account,
       HAS_PERMS_BY_NAME(NULL, 'DATABASE', 'VIEW DEFINITION') AS view_definition
"""

#: Effective permissions (role membership included) plus every object/schema write path. Each
#: fixed database role owns an empty schema of its own name (``db_datareader`` included), which
#: its members therefore "own"; those schemas are skipped, as they hold no objects.
PRIVILEGE_INSPECTION_SQL = """
SELECT 'server' AS scope, permission_name COLLATE DATABASE_DEFAULT AS permission
  FROM fn_my_permissions(NULL, 'SERVER')
UNION ALL
SELECT 'database', permission_name COLLATE DATABASE_DEFAULT FROM fn_my_permissions(NULL, 'DATABASE')
UNION ALL
SELECT DISTINCT 'object', p.permission COLLATE DATABASE_DEFAULT
  FROM sys.objects o
  JOIN sys.schemas s ON s.schema_id = o.schema_id
 CROSS APPLY (VALUES ('INSERT'), ('UPDATE'), ('DELETE'), ('ALTER'), ('EXECUTE')) AS p(permission)
 WHERE o.type IN ('U', 'V', 'P', 'PC', 'X') AND o.is_ms_shipped = 0
   AND HAS_PERMS_BY_NAME(QUOTENAME(s.name) + '.' + QUOTENAME(o.name), 'OBJECT', p.permission) = 1
UNION ALL
SELECT DISTINCT 'schema', p.permission COLLATE DATABASE_DEFAULT
  FROM sys.schemas s
  LEFT JOIN sys.database_principals owner ON owner.principal_id = s.principal_id
 CROSS APPLY (VALUES ('INSERT'), ('UPDATE'), ('DELETE'), ('ALTER'), ('EXECUTE')) AS p(permission)
 WHERE s.name NOT IN ('sys', 'INFORMATION_SCHEMA')
   AND COALESCE(owner.is_fixed_role, 0) = 0
   AND HAS_PERMS_BY_NAME(QUOTENAME(s.name), 'SCHEMA', p.permission) = 1
UNION ALL
SELECT TOP (1) 'grant_option', dp.permission_name COLLATE DATABASE_DEFAULT
  FROM sys.database_permissions dp
 WHERE dp.state = 'W'
   AND (dp.grantee_principal_id = DATABASE_PRINCIPAL_ID() OR IS_ROLEMEMBER(USER_NAME(dp.grantee_principal_id)) = 1)
"""

BEGIN_SQL = "BEGIN TRANSACTION"
ROLLBACK_SQL = "IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION"

# ------------------------------------------------------------------------------- pure mapping

_SIMPLE = {
    "int": "integer", "bigint": "bigint", "smallint": "smallint", "tinyint": "smallint", "bit": "boolean",
    "real": "real", "float": "double precision", "money": "numeric(19,4)", "smallmoney": "numeric(10,4)",
    "date": "date", "datetime": "timestamp without time zone", "smalldatetime": "timestamp without time zone",
    "datetime2": "timestamp without time zone", "datetimeoffset": "timestamp with time zone",
    "time": "time without time zone", "uniqueidentifier": "uuid", "xml": "xml", "text": "text",
    "ntext": "text", "image": "bytea", "binary": "bytea", "varbinary": "bytea", "timestamp": "bytea",
    "rowversion": "bytea", "sql_variant": "text", "hierarchyid": "text", "geography": "text",
    "geometry": "text", "sysname": "character varying(128)",
}
_UDT = {"integer": "int4", "bigint": "int8", "smallint": "int2", "boolean": "bool", "real": "float4",
        "double precision": "float8"}
#: Types ``CAST(... AS NVARCHAR(4000))`` cannot take, or that are not worth profiling.
_UNPROFILED = {"text", "ntext", "image", "geography", "geometry", "hierarchyid", "xml", "sql_variant",
               "timestamp", "rowversion", "binary", "varbinary"}


def canonical_type(base_type: str, max_length: int, precision: int, scale: int) -> str:
    """A SQL Server column type under the twin's canonical (PostgreSQL ``format_type``) name.

    ``max_length`` is in bytes as ``sys.columns`` reports it (-1 for ``(max)``); ``nchar``/
    ``nvarchar`` take two bytes per character.
    """
    base = base_type.lower()
    if base in ("decimal", "numeric"):
        return f"numeric({precision},{scale})"
    if base in ("varchar", "nvarchar", "char", "nchar"):
        if max_length == -1:
            return "text"
        length = max_length // 2 if base.startswith("n") else max_length
        return f"character varying({length})" if "var" in base else f"character({length})"
    return _SIMPLE.get(base, base)


_EQUALITY = re.compile(r"^\[((?:[^\]]|\]\])+)\]\s*=\s*N?'((?:[^']|'')*)'$")


def _strip_parens(text: str) -> str:
    text = text.strip()
    while text.startswith("(") and text.endswith(")") and _balanced(text[1:-1]):
        text = text[1:-1].strip()
    return text


def _balanced(text: str) -> bool:
    depth, quoted = 0, False
    for ch in text:
        if ch == "'":
            quoted = not quoted
        elif not quoted and ch == "(":
            depth += 1
        elif not quoted and ch == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0 and not quoted


def _split_top_level_or(text: str) -> list[str]:
    parts, depth, quoted, start, i = [], 0, False, 0, 0
    upper = text.upper()
    while i < len(text):
        ch = text[i]
        if ch == "'":
            quoted = not quoted
        elif not quoted and ch == "(":
            depth += 1
        elif not quoted and ch == ")":
            depth -= 1
        elif not quoted and depth == 0 and upper.startswith(" OR ", i):
            parts.append(text[start:i])
            start = i + 4
            i += 4
            continue
        i += 1
    parts.append(text[start:])
    return parts


def normalize_check(definition: str) -> str:
    """A ``sys.check_constraints`` definition as a PostgreSQL-shaped ``CHECK (...)``.

    Exactly one shape is rewritten: an OR chain of equalities of a single column against string
    literals -- how SQL Server stores ``col IN ('a', 'b')`` -- becomes ``col IN ('a', 'b')``, in
    declaration order (the stored chain is reversed). Everything else keeps its text with the
    identifier brackets removed, so a genuine ``a = 'x' OR b = 'y'`` stays boolean structure that
    the normalizer does not read as a value set.
    """
    body = _strip_parens(definition)
    parts = [_strip_parens(p) for p in _split_top_level_or(body)]
    matches = [_EQUALITY.match(p) for p in parts]
    if len(parts) > 1 and all(matches) and len({m.group(1) for m in matches if m}) == 1:
        column = matches[0].group(1).replace("]]", "]")  # type: ignore[union-attr]
        values = ", ".join(f"'{m.group(2)}'" for m in reversed(matches) if m)
        return f"CHECK ({q(column)} IN ({values}))"
    return f"CHECK ({_unbracket(definition)[0]})"


def _unbracket(text: str) -> tuple[str, set[str]]:
    """``text`` with ``[identifier]`` quoting replaced by PostgreSQL's outside string literals (a
    LIKE pattern's ``[0-9]`` is left alone), and the identifiers that were bracketed."""
    out: list[str] = []
    names: set[str] = set()
    i, quoted = 0, False
    while i < len(text):
        ch = text[i]
        if ch == "'":
            quoted = not quoted  # '' inside a literal toggles twice: still inside
        elif ch == "[" and not quoted:
            end = i + 1
            while end < len(text) and not (text[end] == "]" and text[end + 1: end + 2] != "]"):
                end += 2 if text[end] == "]" else 1
            name = text[i + 1: end].replace("]]", "]")
            names.add(name)
            out.append(q(name))  # PostgreSQL's own quoting: bare when simple
            i = end + 1
            continue
        out.append(ch)
        i += 1
    return "".join(out), names


_TABLE_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "REFERENCES")


def snapshot_from_mssql(rows: Mapping[str, Any]) -> dict[str, Any]:
    """Raw catalog rows (as :func:`capture_mssql` records them) -> the snapshot."""
    own = {str(g).lower() for g in rows.get("own_grantees", ())}
    relations = [{
        "schema": r["schema"], "name": r["name"], "kind": "v" if r["type"] == "V" else "r",
        "comment": r["comment"] or None, "readable": bool(r["readable"]),
    } for r in rows["relations"]]
    views = {(r["schema"], r["name"]) for r in relations if r["kind"] == "v"}
    columns = []
    for c in rows["columns"]:
        data_type = canonical_type(c["base_type"], int(c["max_length"]), int(c["precision"]), int(c["scale"]))
        user_defined = bool(c["is_user_defined"])
        columns.append({
            "schema": c["schema"], "table": c["table"], "name": c["name"], "position": int(c["position"]),
            "data_type": data_type, "not_null": not c["is_nullable"], "comment": c["comment"] or None,
            "is_enum": False, "type_schema": "sys",
            "type_name": c["type_name"] if user_defined else _UDT.get(data_type, c["type_name"]),
            # IDENTITY is reported as no default, as the PostgreSQL catalog reports identity.
            "has_default": bool(c["has_default"]) and not c["is_identity"]
                           and (c["schema"], c["table"]) not in views,
        })
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}

    def entry(schema: str, table: str, name: str, kind: str) -> dict[str, Any]:
        return grouped.setdefault((schema, table, name), {
            "schema": schema, "table": table, "name": name, "type": kind, "columns": [],
            "ref_schema": None, "ref_table": None, "ref_columns": [], "definition": None, "comment": None,
        })

    for k in rows["keys"]:
        entry(k["schema"], k["table"], k["name"], "p" if k["type"] == "PK" else "u")["columns"].append(k["column_name"])
    for f in rows["foreign_keys"]:
        e = entry(f["schema"], f["table"], f["name"], "f")
        e["columns"].append(f["column_name"])
        e["ref_schema"], e["ref_table"] = f["ref_schema"], f["ref_table"]
        e["ref_columns"].append(f["ref_column"])
    column_names: dict[tuple[str, str], set[str]] = {}
    for c in rows["columns"]:
        column_names.setdefault((c["schema"], c["table"]), set()).add(c["name"])
    for ch in rows["checks"]:
        e = entry(ch["schema"], ch["table"], ch["name"], "c")
        if ch["definition"] is not None:
            e["definition"] = normalize_check(ch["definition"])
            names = column_names.get((ch["schema"], ch["table"]), set())
            e["columns"] = sorted(names & _unbracket(ch["definition"])[1])
    grants: set[tuple[str, str, str, str]] = set()
    for g in rows.get("grants", ()):
        if str(g["grantee"]).lower() in own or g["privilege"] not in _TABLE_PRIVILEGES:
            continue
        if g["class"] == 1:
            targets = [(g["schema"], g["table"])] if g["table"] is not None else []
        elif g["class"] == 3:
            targets = [(r["schema"], r["name"]) for r in relations if r["schema"] == g["grant_schema"]]
        else:
            targets = [(r["schema"], r["name"]) for r in relations]
        known = {(r["schema"], r["name"]) for r in relations}
        grants.update((g["grantee"], s, t, g["privilege"]) for s, t in targets if (s, t) in known)
    return {
        "relations": relations,
        "columns": columns,
        "constraints": [grouped[k] for k in sorted(grouped)],
        "enums": [],
        "grants": [{"grantee": g, "schema": s, "table": t, "privilege": p} for g, s, t, p in sorted(grants)],
        "profiles": list(rows.get("profiles", ())),
        "issues": list(rows.get("issues", ())),
        "truncated": bool(rows.get("truncated")),
    }


_CLEAN_PERMISSIONS = {"CONNECT", "CONNECT SQL", "CONNECT ANY DATABASE", "SELECT", "SHOWPLAN",
                      "SELECT ALL USER SECURABLES"}


def privilege_findings(rows: list[Mapping[str, Any]]) -> tuple[str, ...]:
    """Rows of ``PRIVILEGE_INSPECTION_SQL`` -> findings; empty means the account can only read."""
    findings: set[str] = set()
    for row in rows:
        scope, permission = row["scope"], str(row["permission"]).upper()
        if scope == "grant_option":
            findings.add("grant_option")
        elif permission in _CLEAN_PERMISSIONS or permission.startswith("VIEW "):
            continue
        elif permission in ("INSERT", "UPDATE", "DELETE"):
            findings.add("table_write")
        elif permission == "EXECUTE":
            findings.add("procedure_execute")
        elif permission.startswith(("CREATE ", "ALTER", "REFERENCES")):
            findings.add("ddl")
        else:
            findings.add("admin")  # CONTROL, TAKE OWNERSHIP, IMPERSONATE, CONTROL SERVER, ...
    return tuple(sorted(findings))


# ------------------------------------------------------------------------------- capture


def _mssql_ident(name: str) -> str:
    return "[" + name.replace("]", "]]") + "]"


_TEXTUAL = ("text", "character varying", "character")
_HEX = "[0-9A-Fa-f]"
_DIGIT = "[0-9]"
#: Not a space-class character: [:space:] is space, \t, \n, \v, \f, \r.
_WHITESPACE = " \t\n\x0b\x0c\r"
#: The PostgreSQL connector's pattern rules (``postgres_catalog._PATTERNS``) without regular
#: expressions, which T-SQL lacks: each is an exact LIKE-based equivalent over a binary collation
#: (so case and code points compare as the regex does). ``{v}`` is the value expression; the
#: patterns are bound parameters.
_LIKE_PATTERNS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("uuid", "{v} LIKE %s", (f"{_HEX * 8}-{_HEX * 4}-{_HEX * 4}-{_HEX * 4}-{_HEX * 12}",)),
    ("email", "{v} LIKE %s AND {v} NOT LIKE %s AND {v} NOT LIKE %s",
     ("_%@_%._%", "%@%@%", f"%[{_WHITESPACE}]%")),
    ("url", "({v} LIKE %s OR {v} LIKE %s)", ("http://%", "https://%")),
    ("iso_date", "{v} LIKE %s", (f"{_DIGIT * 4}-{_DIGIT * 2}-{_DIGIT * 2}%",)),
    ("numeric_text",
     "({v} LIKE %s OR {v} LIKE %s) AND SUBSTRING({v}, 2, 4000) NOT LIKE %s AND {v} NOT LIKE %s AND {v} NOT LIKE %s",
     ("[0-9]%", "-[0-9]%", "%[^0-9.]%", "%.%.%", "%.")),
)


def _profile_table(cur: Any, rel: Mapping[str, Any], cols: list[Mapping[str, Any]],
                   budget: DiscoveryBudget) -> list[dict[str, Any]]:
    """Aggregates over a bounded sample, computed by the server: no row value leaves it."""
    if not cols:
        return []
    exprs = ["COUNT(*) AS n"]
    params: list[Any] = []
    for i, col in enumerate(cols):
        ident = _mssql_ident(col["name"])
        text = f"CAST({ident} AS NVARCHAR(4000)) COLLATE Latin1_General_BIN2"
        exprs.append(f"COUNT({ident}) AS nn_{i}")
        exprs.append(f"COUNT(DISTINCT {text}) AS d_{i}")
        if col["data_type"].startswith(_TEXTUAL):
            for p, (_name, condition, patterns) in enumerate(_LIKE_PATTERNS):
                exprs.append(f"SUM(CASE WHEN {condition.format(v=text)} THEN 1 ELSE 0 END) AS p_{i}_{p}")
                params.extend(patterns)
    source = f"{_mssql_ident(rel['schema'])}.{_mssql_ident(rel['name'])}"
    select_cols = ", ".join(_mssql_ident(c["name"]) for c in cols)
    # Placeholders bind in text order: the pattern parameters (select list) before the sample size.
    cur.execute(f"SELECT {', '.join(exprs)} FROM (SELECT TOP (%s) {select_cols} FROM {source}) s",
                (*params, budget.sample_rows))
    row = cur.fetchone()
    out = []
    for i, col in enumerate(cols):
        textual = col["data_type"].startswith(_TEXTUAL)
        patterns_found = ({name: int(row[f"p_{i}_{p}"] or 0) for p, (name, _c, _p) in enumerate(_LIKE_PATTERNS)}
                          if textual else {})
        distinct = int(row[f"d_{i}"])
        profile = {
            "schema": rel["schema"], "table": rel["name"], "column": col["name"], "data_type": col["data_type"],
            "sampled_rows": int(row["n"]), "non_null": int(row[f"nn_{i}"]), "distinct": distinct,
            "patterns": patterns_found, "values": None,
        }
        if budget.sample_values and textual and 0 < distinct <= _CATEGORICAL_MAX_DISTINCT:
            ident = _mssql_ident(col["name"])
            cur.execute(
                f"SELECT DISTINCT LEFT(CAST({ident} AS NVARCHAR(4000)), {int(_MAX_VALUE_CHARS)}) "
                f"COLLATE Latin1_General_BIN2 AS v FROM (SELECT TOP (%s) {ident} FROM {source}) s "
                f"WHERE {ident} IS NOT NULL ORDER BY 1",
                (budget.sample_rows,),
            )
            profile["values"] = [r["v"] for r in cur.fetchall()]
        out.append(profile)
    return out


def capture_mssql(conn: Any, *, budget: DiscoveryBudget, timeout_s: float = 15.0,
                  pytds_module: Any = None) -> dict[str, Any]:
    """Run the catalog queries and the bounded profile on an open python-tds connection
    (``as_dict=True``). Synchronous by design; the connector calls it in a worker thread."""
    import pytds as _pytds

    pytds = pytds_module or _pytds
    deadline = time.monotonic() + budget.max_seconds
    limit = budget.max_objects + 1
    rows: dict[str, Any] = {"issues": [], "truncated": False}
    cur = conn.cursor()
    try:
        # A SET inside sp_executesql (how a parameterized statement is sent) reverts when that batch
        # ends, so the lock timeout is sent as a plain batch, from an int().
        cur.execute(f"SET LOCK_TIMEOUT {int(timeout_s * 1000)}")
        cur.execute(BEGIN_SQL)
        for key, sql in (("relations", RELATIONS_SQL), ("columns", COLUMNS_SQL), ("keys", KEYS_SQL),
                         ("foreign_keys", FOREIGN_KEYS_SQL), ("checks", CHECKS_SQL)):
            cur.execute(sql, (limit,))
            found = list(cur.fetchall())
            if len(found) == limit:
                found = found[:-1]
                rows["truncated"] = True
                rows["issues"].append({"code": "budget_exhausted", "locator": key})
            rows[key] = found
        cur.execute(ACCOUNT_SQL)
        account = cur.fetchone()
        rows["own_grantees"] = [account["account"]]
        if any(ch["definition"] is None for ch in rows["checks"]):
            rows["issues"].append({"code": "check_definition_hidden", "locator": "sys.check_constraints"})
        if account["view_definition"]:
            cur.execute(GRANTS_SQL)
            rows["grants"] = list(cur.fetchall())
        else:
            rows["grants"] = []
            rows["issues"].append({"code": "grants_not_visible", "locator": "sys.database_permissions"})
        rows["profiles"] = _profile(cur, pytds, rows, budget, deadline)
    finally:
        cur.execute(ROLLBACK_SQL)
        cur.close()
    return rows


def _profile(cur: Any, pytds: Any, rows: dict[str, Any], budget: DiscoveryBudget,
             deadline: float) -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []
    if budget.sample_rows == 0 or budget.max_profiled_columns == 0:
        return profiles
    snapshot = snapshot_from_mssql({**rows, "profiles": [], "issues": []})
    base_types = {(c["schema"], c["table"], c["name"]): str(c["base_type"]).lower() for c in rows["columns"]}
    by_table: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for col in snapshot["columns"]:
        base = base_types[(col["schema"], col["table"], col["name"])]
        if base in _UNPROFILED or (base in ("varchar", "nvarchar") and col["data_type"] == "text"):
            continue  # not castable, or (max): not worth a sample's distinct count
        by_table.setdefault((col["schema"], col["table"]), []).append(col)
    remaining = budget.max_profiled_columns
    for rel in snapshot["relations"]:
        if rel["kind"] != "r":
            continue
        locator = f"table:{q(rel['schema'])}.{q(rel['name'])}"
        if not rel["readable"]:
            rows["issues"].append({"code": "not_readable", "locator": locator})
            continue
        if remaining <= 0 or time.monotonic() > deadline:
            rows["truncated"] = True
            rows["issues"].append({"code": "profile_budget_exhausted", "locator": locator})
            continue
        cols = by_table.get((rel["schema"], rel["name"]), [])[:remaining]
        remaining -= len(cols)
        try:
            profiles.extend(_profile_table(cur, rel, cols, budget))
        except pytds.OperationalError as exc:
            code = getattr(exc, "msg_no", None)
            rows["issues"].append({"code": "profile_timeout" if code == 1222 else "permission_denied",
                                   "locator": locator})
        except pytds.ProgrammingError:
            rows["issues"].append({"code": "permission_denied", "locator": locator})
    return profiles


# ------------------------------------------------------------------------------- connector


def _target(endpoint: str) -> tuple[str, int, str] | None:
    try:
        parts = urlsplit(endpoint)
        port = parts.port or 1433
    except ValueError:
        return None
    database = unquote(parts.path.lstrip("/"))
    if parts.scheme not in _SCHEMES or not parts.hostname or not database or "/" in database:
        return None
    return parts.hostname, port, database


def host_matches(certificate: Any, host: str) -> bool:
    """Whether a ``cryptography`` certificate names ``host`` (RFC 6125): subjectAltName DNS names
    (one leftmost ``*`` label at most) or IP addresses, and the subject CN only when there is no
    subjectAltName at all."""
    import ipaddress

    from cryptography import x509

    try:
        address: Any = ipaddress.ip_address(host)
    except ValueError:
        address = None
    try:
        san = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        san = None
    if san is not None:
        if address is not None:
            return address in san.get_values_for_type(x509.IPAddress)
        return any(_dns_matches(name, host) for name in san.get_values_for_type(x509.DNSName))
    if address is not None:
        return False
    return any(_dns_matches(str(attr.value), host)
               for attr in certificate.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME))


def _dns_matches(pattern: str, host: str) -> bool:
    pattern, host = pattern.lower().rstrip("."), host.lower().rstrip(".")
    if pattern.startswith("*."):
        label, dot, rest = host.partition(".")
        # A wildcard stands for exactly one whole label, never for a public suffix like *.com.
        return bool(label) and bool(dot) and rest == pattern[2:] and "." in rest
    return "*" not in pattern and pattern == host


def _validate_host(cert: Any, name: bytes) -> bool:
    """Drop-in for ``pytds.tls.validate_host``: python-tds calls it inside the TLS handshake,
    before the login (and password) is sent, which makes it the only place a host-name check
    protects anything. The library's own one compares the CN byte-for-byte, mis-parses the SAN
    and calls ``X509.get_extension``, which pyOpenSSL has removed -- so any certificate naming
    the host only in its SAN would raise. Fails closed on anything unexpected."""
    try:
        return host_matches(cert.to_cryptography(), name.decode("ascii"))
    except Exception:  # noqa: BLE001 -- an unparseable certificate is a mismatch, not a crash
        return False


def _install_host_check() -> None:
    import pytds.tls

    pytds.tls.validate_host = _validate_host


def _system_ca_file() -> str | None:
    paths = ssl.get_default_verify_paths()
    return paths.cafile or paths.openssl_cafile or None


class SqlServerConnectorFactory:
    async def open(self, descriptor: ConnectorDescriptor, credential: Credential | None) -> "SqlServerConnector":
        return SqlServerConnector(descriptor, credential)


class SqlServerConnector:
    def __init__(self, descriptor: ConnectorDescriptor, credential: Credential | None) -> None:
        self.descriptor = descriptor
        self._credential = credential

    def _preflight(self) -> tuple[tuple[str, int, str] | None, str | None]:
        target = _target(self.descriptor.endpoint)
        if target is None:
            return None, "invalid_endpoint"
        user = self.descriptor.options.get("user")
        tls = str(self.descriptor.options.get("tls", "verify-full"))
        ca_file = self.descriptor.options.get("ca_file")
        if not isinstance(user, str) or not user or tls not in _TLS_MODES:
            return None, "invalid_options"
        if ca_file is not None and not isinstance(ca_file, str):
            return None, "invalid_options"
        if tls != "disable" and not (ca_file or _system_ca_file()):
            return None, "no_ca_file"
        return target, None

    def _connect_sync(self, target: tuple[str, int, str]) -> tuple[Any, str | None]:
        import pytds

        host, port, database = target
        tls = str(self.descriptor.options.get("tls", "verify-full"))
        timeout = max(1.0, float(self.descriptor.timeout_s))
        tls_args: dict[str, Any] = {}
        if tls != "disable":
            tls_args = {"cafile": self.descriptor.options.get("ca_file") or _system_ca_file(),
                        "validate_host": tls == "verify-full"}
            if tls == "verify-full":
                _install_host_check()
        try:
            conn = pytds.connect(
                host, database=database, user=self.descriptor.options["user"],
                password=self._credential.reveal() if self._credential else "", port=port,
                login_timeout=timeout, timeout=timeout + 5, as_dict=True, autocommit=True,
                appname="aura-connector", **tls_args,
            )
        except pytds.OperationalError as exc:
            code = getattr(exc, "msg_no", None)
            if code == 18456:  # also an unknown or forbidden database: SQL Server does not say which
                return None, "auth_failed"
            logger.info("sql server connector refused", extra={"connector": self.descriptor.id, "code": code})
            return None, "connect_failed"
        except OSError:
            return None, "unreachable"
        except pytds.Error as exc:
            if "Certificate does not match host name" in str(exc):
                return None, "tls_failed"
            logger.info("sql server connector refused", extra={"connector": self.descriptor.id})
            return None, "connect_failed"
        except Exception as exc:  # pyOpenSSL's errors, without importing it where it is absent
            if type(exc).__module__.startswith("OpenSSL"):
                return None, "tls_failed"
            raise
        return conn, None

    def _health_sync(self, target: tuple[str, int, str]) -> tuple[str, tuple[str, ...] | None]:
        import pytds

        conn, reason = self._connect_sync(target)
        if conn is None:
            return reason or "connect_failed", None
        try:
            cur = conn.cursor()
            cur.execute(PRIVILEGE_INSPECTION_SQL)
            found = list(cur.fetchall())
        except pytds.Error:
            return "inspection_failed", None
        finally:
            conn.close()
        return "ok", privilege_findings(found)

    async def health(self) -> ConnectorHealth:
        started = time.monotonic()
        target, reason = self._preflight()
        findings = None
        if target is not None:
            reason, findings = await asyncio.to_thread(self._health_sync, target)
        latency = round((time.monotonic() - started) * 1000.0, 1)
        if findings is None:
            return ConnectorHealth(HealthStatus.UNHEALTHY, reason or "connect_failed", latency_ms=latency)
        return ConnectorHealth(HealthStatus.HEALTHY, "ok", write_capable=bool(findings), findings=findings,
                               latency_ms=latency)

    def _discover_sync(self, target: tuple[str, int, str], budget: DiscoveryBudget) -> dict[str, Any]:
        import pytds

        conn, reason = self._connect_sync(target)
        if conn is None:
            raise DependencyUnavailableError(
                "the connected database could not be reached for discovery", dependency="connector",
                details={"connector": self.descriptor.id, "reason": reason})
        try:
            return capture_mssql(conn, budget=budget, timeout_s=self.descriptor.timeout_s)
        except pytds.Error as exc:
            code = getattr(exc, "msg_no", None)
            logger.info("sql server discovery failed", extra={"connector": self.descriptor.id, "code": code})
            raise DependencyUnavailableError("catalog discovery failed", dependency="connector",
                                             details={"connector": self.descriptor.id, "code": code}) from None
        finally:
            conn.close()

    async def discover(self, budget: DiscoveryBudget) -> DiscoveryReport:
        target, reason = self._preflight()
        if target is None:
            raise DependencyUnavailableError("the connector is misconfigured", dependency="connector",
                                             details={"connector": self.descriptor.id, "reason": reason})
        rows = await asyncio.to_thread(self._discover_sync, target, budget)
        return artifacts_from_catalog(self.descriptor.id, snapshot_from_mssql(rows), budget, extractor="mssql",
                                      catalog_extractor=CATALOG_EXTRACTOR, profile_extractor=PROFILE_EXTRACTOR)

    async def read(self, template: ReadTemplate, arguments: Mapping[str, Any],
                   identity: CallerIdentity | None = None) -> list[dict[str, Any]]:
        raise ToolExecutionError("templated reads are not available for SQL Server yet", tool=template.id)

    async def aclose(self) -> None:
        return None
