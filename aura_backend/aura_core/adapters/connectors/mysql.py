"""MySQL connector (AP10): reachability, privilege inspection and discovery.

Discovery only -- the same port as the PostgreSQL connector (``DiscoveryConnector``), not yet
templated reads: a MySQL read needs its own SQL dialect behind ``render_sql``, which is a later
step. Until then the compiler drops any capability on a MySQL table with a recorded reason.

**Capture** (:func:`capture_mysql`) runs inside a ``READ ONLY`` transaction with
``MAX_EXECUTION_TIME`` set, reading ``information_schema`` for one database (the endpoint's path):
tables and views, columns, primary/unique/foreign keys, CHECK constraints (MySQL >= 8.0.16), and a
bounded profile computed in SQL. Grants of *other* accounts and roles live in the ``mysql.db``
and ``mysql.tables_priv`` grant tables, which a read-only account can read only if granted
``SELECT`` on them; without it the grants are an issue (``grants_not_visible``), not a failure.
The connector's own account is excluded from the grants, as owner grants are on PostgreSQL.

**Mapping** (:func:`snapshot_from_mysql`) is pure and produces the PostgreSQL connector's snapshot
shape, so ``artifacts_from_catalog`` and everything after it are shared. Column types are
translated to the canonical names the twin already uses (``int`` -> ``integer``, ``varchar(n)``
-> ``character varying(n)``, ``tinyint(1)`` -> ``boolean``, ``datetime`` -> ``timestamp without
time zone``, ``timestamp`` -> ``timestamp with time zone``); the engine's own type stays as
``type_name``. A column ``ENUM(...)`` becomes an enum type named ``<table>_<column>``. CHECK
clauses lose MySQL's charset introducers and backticks, so ``status IN (...)`` reads as a closed
value set exactly as on PostgreSQL.

**TLS**: ``tls`` is ``require`` (the default: encrypted, certificate not verified, as libpq's
``sslmode=require``), ``verify-full`` (chain and host name, against the ``ca_file`` option or the
system bundle) or ``disable``. A failed verification is ``tls_failed``, not ``unreachable``.

**Privileges** (:func:`privilege_findings`): ``SHOW GRANTS`` for the current account is parsed;
anything beyond ``SELECT``/``SHOW VIEW``/``SHOW DATABASES``/``USAGE`` is a finding, and so is a
granted role (its privileges cannot be vouched for without activating it) and ``WITH GRANT
OPTION``. Activation refuses any finding (``ConnectorHealth.write_capable``). Every session is
also opened ``READ ONLY``, so a mistake in this module could not write either.

PyMySQL is pure Python and synchronous; each call runs in a worker thread, so nothing here holds
the event loop.
"""

from __future__ import annotations

import asyncio
import re
import ssl
import time
from typing import Any, Mapping, Sequence
from urllib.parse import unquote, urlsplit

from ...domain.connectors import ConnectorDescriptor, ConnectorHealth, Credential, HealthStatus
from ...domain.discovery import DiscoveryBudget, DiscoveryReport, quote_identifier as q
from ...domain.errors import DependencyUnavailableError, ToolExecutionError
from ...domain.templates import ReadTemplate
from ...observability.logging import get_logger
from .postgres_catalog import _CATEGORICAL_MAX_DISTINCT, _MAX_VALUE_CHARS, _PATTERNS, artifacts_from_catalog

__all__ = [
    "MySqlConnectorFactory", "MySqlConnector", "capture_mysql", "snapshot_from_mysql", "privilege_findings",
    "canonical_type", "normalize_check", "CATALOG_EXTRACTOR", "PROFILE_EXTRACTOR",
]

logger = get_logger("adapters.connectors.mysql")

CATALOG_EXTRACTOR = "mysql_catalog"
PROFILE_EXTRACTOR = "mysql_profile"
_TLS_MODES = {"disable", "require", "verify-full"}

RELATIONS_SQL = """
SELECT TABLE_SCHEMA AS `schema`, TABLE_NAME AS name, TABLE_TYPE AS table_type, TABLE_COMMENT AS comment
  FROM information_schema.TABLES
 WHERE TABLE_SCHEMA = %s AND TABLE_TYPE IN ('BASE TABLE', 'VIEW')
 ORDER BY TABLE_NAME
 LIMIT %s
"""

COLUMNS_SQL = """
SELECT TABLE_SCHEMA AS `schema`, TABLE_NAME AS `table`, COLUMN_NAME AS name, ORDINAL_POSITION AS position,
       DATA_TYPE AS data_type, COLUMN_TYPE AS column_type, IS_NULLABLE AS is_nullable,
       COLUMN_DEFAULT AS column_default, EXTRA AS extra, COLUMN_COMMENT AS comment
  FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA = %s
 ORDER BY TABLE_NAME, ORDINAL_POSITION
 LIMIT %s
"""

KEYS_SQL = """
SELECT tc.TABLE_SCHEMA AS `schema`, tc.TABLE_NAME AS `table`, tc.CONSTRAINT_NAME AS name,
       tc.CONSTRAINT_TYPE AS constraint_type, k.COLUMN_NAME AS column_name, k.ORDINAL_POSITION AS ordinal,
       k.REFERENCED_TABLE_SCHEMA AS ref_schema, k.REFERENCED_TABLE_NAME AS ref_table,
       k.REFERENCED_COLUMN_NAME AS ref_column
  FROM information_schema.TABLE_CONSTRAINTS tc
  JOIN information_schema.KEY_COLUMN_USAGE k
    ON k.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA AND k.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
   AND k.TABLE_NAME = tc.TABLE_NAME
 WHERE tc.TABLE_SCHEMA = %s AND tc.CONSTRAINT_TYPE IN ('PRIMARY KEY', 'UNIQUE', 'FOREIGN KEY')
 ORDER BY tc.TABLE_NAME, tc.CONSTRAINT_NAME, k.ORDINAL_POSITION
 LIMIT %s
"""

CHECKS_SQL = """
SELECT tc.TABLE_SCHEMA AS `schema`, tc.TABLE_NAME AS `table`, tc.CONSTRAINT_NAME AS name,
       cc.CHECK_CLAUSE AS clause
  FROM information_schema.TABLE_CONSTRAINTS tc
  JOIN information_schema.CHECK_CONSTRAINTS cc
    ON cc.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
 WHERE tc.TABLE_SCHEMA = %s AND tc.CONSTRAINT_TYPE = 'CHECK'
 ORDER BY tc.TABLE_NAME, tc.CONSTRAINT_NAME
 LIMIT %s
"""

#: The account's own privileges, from the views every account may read about itself.
OWN_PRIVILEGES_SQL = """
SELECT 'global' AS level, NULL AS table_name, PRIVILEGE_TYPE AS privilege
  FROM information_schema.USER_PRIVILEGES WHERE GRANTEE = %s
UNION ALL
SELECT 'schema', NULL, PRIVILEGE_TYPE
  FROM information_schema.SCHEMA_PRIVILEGES WHERE GRANTEE = %s AND TABLE_SCHEMA = %s
UNION ALL
SELECT 'table', TABLE_NAME, PRIVILEGE_TYPE
  FROM information_schema.TABLE_PRIVILEGES WHERE GRANTEE = %s AND TABLE_SCHEMA = %s
"""

CURRENT_ACCOUNT_SQL = "SELECT CURRENT_USER() AS account"

#: Other accounts' grants. Readable only with SELECT on these grant tables (see the docstring).
SCHEMA_GRANTS_SQL = """
SELECT User AS user, Host AS host, Select_priv AS select_priv, Insert_priv AS insert_priv,
       Update_priv AS update_priv, Delete_priv AS delete_priv, References_priv AS references_priv,
       Trigger_priv AS trigger_priv
  FROM mysql.db WHERE Db = %s
 ORDER BY User, Host
"""
TABLE_GRANTS_SQL = """
SELECT User AS user, Host AS host, Table_name AS table_name, Table_priv AS table_priv
  FROM mysql.tables_priv WHERE Db = %s
 ORDER BY User, Host, Table_name
"""

SET_TIMEOUT_SQL = "SET SESSION MAX_EXECUTION_TIME = %s"
START_READ_ONLY_SQL = "START TRANSACTION READ ONLY"
SHOW_GRANTS_SQL = "SHOW GRANTS"
ROLLBACK_SQL = "ROLLBACK"

# ------------------------------------------------------------------------------- pure mapping

_INTEGER = {"tinyint": "smallint", "smallint": "smallint", "mediumint": "integer", "int": "integer",
            "integer": "integer", "bigint": "bigint", "year": "smallint"}
_TEXT = {"tinytext", "text", "mediumtext", "longtext", "set"}
_BINARY = {"binary", "varbinary", "tinyblob", "blob", "mediumblob", "longblob", "bit"}
_UDT = {"integer": "int4", "bigint": "int8", "smallint": "int2", "boolean": "bool", "real": "float4",
        "double precision": "float8"}
_ENUM_LABEL = re.compile(r"'((?:[^']|'')*)'")
_MODIFIERS = re.compile(r"\(([^)]*)\)")


def canonical_type(column_type: str) -> str:
    """A MySQL ``COLUMN_TYPE`` under the twin's canonical (PostgreSQL ``format_type``) name."""
    full = column_type.strip().lower()
    base = full.split("(", 1)[0].replace(" unsigned", "").replace(" zerofill", "").strip()
    found = _MODIFIERS.search(full)
    modifiers = found.group(1).replace(" ", "") if found else ""
    if full.startswith("tinyint(1)"):
        return "boolean"
    if base in _INTEGER:
        return _INTEGER[base]
    if base in ("decimal", "numeric", "dec", "fixed"):
        return f"numeric({modifiers})" if modifiers else "numeric"
    if base in ("float",):
        return "real"
    if base in ("double", "double precision", "real"):
        return "double precision"
    if base == "varchar":
        return f"character varying({modifiers})" if modifiers else "character varying"
    if base == "char":
        return f"character({modifiers})" if modifiers else "character"
    if base in _TEXT:
        return "text"
    if base in _BINARY:
        return "bytea"
    if base == "date":
        return "date"
    if base == "datetime":
        return "timestamp without time zone"
    if base == "timestamp":
        # MySQL stores TIMESTAMP in UTC and converts on read: an instant, as timestamptz is.
        return "timestamp with time zone"
    if base == "time":
        return "time without time zone"
    if base == "json":
        return "json"
    return base


def enum_labels(column_type: str) -> list[str]:
    """``enum('a','it''s')`` -> ``['a', "it's"]``."""
    return [label.replace("''", "'") for label in _ENUM_LABEL.findall(column_type)]


_INTRODUCER = re.compile(r"_[A-Za-z0-9]+$")


def normalize_check(clause: str) -> str:
    """A MySQL CHECK_CLAUSE as a PostgreSQL-shaped definition, wrapped as ``CHECK (...)``.

    ``information_schema`` backslash-escapes the expression once more (every ``'`` arrives as
    ``\\'``), so that level is undone first. The expression is then scanned so only text *outside*
    string literals is rewritten: charset introducers (``_utf8mb4'...'``) and identifier backticks
    go, and each literal is re-quoted the SQL-standard way (``''`` for a quote), which is what the
    normalizer's closed-value-set reader expects. A value such as ``'on_leave'`` is never touched.
    """
    text = re.sub(r"\\(.)", r"\1", clause, flags=re.DOTALL)
    out: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "`":
            i += 1
            continue
        if ch != "'":
            out.append(ch)
            i += 1
            continue
        head = "".join(out)
        introducer = _INTRODUCER.search(head)
        if introducer and (introducer.start() == 0 or not head[introducer.start() - 1].isalnum()):
            out = list(head[: introducer.start()])
        value: list[str] = []
        i += 1
        while i < len(text):
            if text[i] == "\\" and i + 1 < len(text):
                value.append(text[i + 1])
                i += 2
            elif text[i] == "'" and text[i + 1: i + 2] == "'":
                value.append("'")
                i += 2
            elif text[i] == "'":
                i += 1
                break
            else:
                value.append(text[i])
                i += 1
        out.append("'" + "".join(value).replace("'", "''") + "'")
    return f"CHECK ({''.join(out)})"


def _grantee(user: str, host: str) -> str:
    return user if host in ("%", "") else f"{user}@{host}"


def _account_grantee(account: str) -> str:
    """``CURRENT_USER()`` (``user@host``) as the ``'user'@'host'`` GRANTEE string."""
    user, _, host = account.rpartition("@")
    return f"'{user}'@'{host}'"


_TABLE_PRIVILEGES = {"SELECT", "INSERT", "UPDATE", "DELETE", "REFERENCES", "TRIGGER"}


def snapshot_from_mysql(rows: Mapping[str, Any]) -> dict[str, Any]:
    """Raw ``information_schema`` rows (as :func:`capture_mysql` records them) -> the snapshot."""
    database = rows["database"]
    own = {g.lower() for g in rows.get("own_grantees", ())}
    readable_everywhere = bool(rows.get("own_select_global") or rows.get("own_select_schema"))
    readable_tables = set(rows.get("own_select_tables", ()))
    relations = []
    for r in rows["relations"]:
        kind = "v" if r["table_type"] == "VIEW" else "r"
        # A view cannot take a comment in MySQL; TABLE_COMMENT then reads the literal "VIEW".
        relations.append({"schema": r["schema"], "name": r["name"], "kind": kind,
                          "comment": (r["comment"] or None) if kind == "r" else None,
                          "readable": readable_everywhere or r["name"] in readable_tables})
    views = {r["name"] for r in relations if r["kind"] == "v"}
    enums: list[dict[str, Any]] = []
    columns = []
    for c in rows["columns"]:
        data_type, type_name, type_schema, is_enum = canonical_type(c["column_type"]), c["data_type"], "mysql", False
        if c["data_type"] == "enum":
            type_name = f"{c['table']}_{c['name']}"
            type_schema, is_enum = c["schema"], True
            data_type = q(type_name)
            enums.append({"schema": c["schema"], "name": type_name, "labels": enum_labels(c["column_type"])})
        extra = (c["extra"] or "").lower()
        columns.append({
            "schema": c["schema"], "table": c["table"], "name": c["name"], "position": int(c["position"]),
            "data_type": data_type, "not_null": c["is_nullable"] == "NO", "comment": c["comment"] or None,
            "is_enum": is_enum, "type_schema": type_schema,
            "type_name": _UDT.get(data_type, type_name),
            # AUTO_INCREMENT is identity, which the PostgreSQL catalog reports as no default; a
            # view's column echoes its base column's default, which inserting into the view
            # would not apply on PostgreSQL either.
            "has_default": (c["column_default"] is not None and "auto_increment" not in extra
                            and c["table"] not in views),
        })
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    kinds = {"PRIMARY KEY": "p", "UNIQUE": "u", "FOREIGN KEY": "f"}
    for k in rows["keys"]:
        key = (k["schema"], k["table"], k["name"])
        entry = grouped.setdefault(key, {
            "schema": k["schema"], "table": k["table"], "name": k["name"], "type": kinds[k["constraint_type"]],
            "columns": [], "ref_schema": None, "ref_table": None, "ref_columns": [], "definition": None,
            "comment": None,
        })
        entry["columns"].append(k["column_name"])
        if k["ref_table"]:
            entry["ref_schema"], entry["ref_table"] = k["ref_schema"], k["ref_table"]
            entry["ref_columns"].append(k["ref_column"])
    column_names: dict[tuple[str, str], set[str]] = {}
    for c in rows["columns"]:
        column_names.setdefault((c["schema"], c["table"]), set()).add(c["name"])
    for ch in rows["checks"]:
        definition = normalize_check(ch["clause"])
        names = column_names.get((ch["schema"], ch["table"]), set())
        referenced = sorted(n for n in names if re.search(rf"`{re.escape(n)}`", ch["clause"]))
        grouped[(ch["schema"], ch["table"], ch["name"])] = {
            "schema": ch["schema"], "table": ch["table"], "name": ch["name"], "type": "c",
            "columns": referenced, "ref_schema": None, "ref_table": None, "ref_columns": [],
            "definition": definition, "comment": None,
        }
    grants: set[tuple[str, str, str, str]] = set()
    names = [r["name"] for r in relations]
    for g in rows.get("schema_grants", ()):
        grantee = _grantee(g["user"], g["host"])
        if grantee.lower() in own:
            continue
        for privilege in _TABLE_PRIVILEGES:
            if g.get(f"{privilege.lower()}_priv") == "Y":
                grants.update((grantee, database, name, privilege) for name in names)
    for g in rows.get("table_grants", ()):
        grantee = _grantee(g["user"], g["host"])
        if grantee.lower() in own or g["table_name"] not in names:
            continue
        for privilege in str(g["table_priv"] or "").split(","):
            privilege = privilege.strip().upper()
            if privilege in _TABLE_PRIVILEGES:
                grants.add((grantee, database, g["table_name"], privilege))
    return {
        "relations": relations,
        "columns": columns,
        "constraints": [grouped[k] for k in sorted(grouped)],
        "enums": sorted(enums, key=lambda e: (e["schema"], e["name"])),
        "grants": [{"grantee": g, "schema": s, "table": t, "privilege": p} for g, s, t, p in sorted(grants)],
        "profiles": list(rows.get("profiles", ())),
        "issues": list(rows.get("issues", ())),
        "truncated": bool(rows.get("truncated")),
    }


_ALLOWED_PRIVILEGES = {"SELECT", "SHOW VIEW", "SHOW DATABASES", "USAGE"}
_WRITE_PRIVILEGES = {"INSERT", "UPDATE", "DELETE", "ALL", "ALL PRIVILEGES"}
_DDL_PRIVILEGES = {"CREATE", "DROP", "ALTER", "INDEX", "CREATE VIEW", "CREATE ROUTINE", "ALTER ROUTINE",
                   "TRIGGER", "EVENT", "CREATE TEMPORARY TABLES", "REFERENCES", "LOCK TABLES", "EXECUTE"}
_GRANT_LINE = re.compile(r"^GRANT\s+(.+?)\s+ON\s+(.+?)\s+TO\s+(.+?)(\s+WITH\s+GRANT\s+OPTION)?$", re.IGNORECASE)


def privilege_findings(grant_lines: Sequence[str]) -> tuple[str, ...]:
    """``SHOW GRANTS`` lines -> findings; empty means the account can only read."""
    findings: set[str] = set()
    for line in grant_lines:
        text = " ".join(line.split())
        if not text.upper().startswith("GRANT "):
            continue  # partial REVOKE lines only take away
        match = _GRANT_LINE.match(text)
        if match is None:
            findings.add("granted_roles")  # GRANT `role`@`host` TO ...: privileges not inspectable
            continue
        if match.group(4):
            findings.add("grant_option")
        privileges = re.sub(r"\([^)]*\)", "", match.group(1))  # column lists: SELECT (`a`, `b`)
        for privilege in (p.strip().upper() for p in privileges.split(",")):
            if privilege in _ALLOWED_PRIVILEGES:
                continue
            if privilege in _WRITE_PRIVILEGES:
                findings.add("table_write")
            elif privilege in _DDL_PRIVILEGES:
                findings.add("ddl")
            else:
                findings.add("admin")
    return tuple(sorted(findings))


# ------------------------------------------------------------------------------- capture


def _mysql_ident(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


_TEXTUAL = ("text", "character varying", "character")
#: The PostgreSQL connector's pattern rules for MySQL's ICU regex engine (POSIX class spelled \\s).
_ICU_PATTERNS = tuple((name, regex.replace("[:space:]", r"\s")) for name, regex in _PATTERNS)


def _profile_table(cur: Any, database: str, rel: Mapping[str, Any], cols: list[Mapping[str, Any]],
                   budget: DiscoveryBudget) -> list[dict[str, Any]]:
    """Aggregates over a bounded sample, computed by the server: no row value leaves it."""
    if not cols:
        return []
    exprs = ["COUNT(*) AS n"]
    params: list[Any] = []
    for i, col in enumerate(cols):
        ident = _mysql_ident(col["name"])
        exprs.append(f"COUNT({ident}) AS nn_{i}")
        exprs.append(f"COUNT(DISTINCT CAST({ident} AS CHAR)) AS d_{i}")
        if col["data_type"].startswith(_TEXTUAL):
            for p, (_name, regex) in enumerate(_ICU_PATTERNS):
                exprs.append(f"COALESCE(SUM(CAST({ident} AS CHAR) REGEXP %s), 0) AS p_{i}_{p}")
                params.append(regex)
    source = f"{_mysql_ident(database)}.{_mysql_ident(rel['name'])}"
    select_cols = ", ".join(_mysql_ident(c["name"]) for c in cols)
    params.append(budget.sample_rows)
    cur.execute(f"SELECT {', '.join(exprs)} FROM (SELECT {select_cols} FROM {source} LIMIT %s) s", params)
    row = cur.fetchone()
    out = []
    for i, col in enumerate(cols):
        textual = col["data_type"].startswith(_TEXTUAL)
        patterns = {name: int(row[f"p_{i}_{p}"]) for p, (name, _r) in enumerate(_ICU_PATTERNS)} if textual else {}
        distinct = int(row[f"d_{i}"])
        profile = {
            "schema": database, "table": rel["name"], "column": col["name"], "data_type": col["data_type"],
            "sampled_rows": int(row["n"]), "non_null": int(row[f"nn_{i}"]), "distinct": distinct,
            "patterns": patterns, "values": None,
        }
        if budget.sample_values and textual and 0 < distinct <= _CATEGORICAL_MAX_DISTINCT:
            ident = _mysql_ident(col["name"])
            cur.execute(
                f"SELECT DISTINCT LEFT(CAST({ident} AS CHAR), {int(_MAX_VALUE_CHARS)}) AS v FROM "
                f"(SELECT {ident} FROM {source} LIMIT %s) s WHERE {ident} IS NOT NULL ORDER BY 1",
                (budget.sample_rows,),
            )
            profile["values"] = [r["v"] for r in cur.fetchall()]
        out.append(profile)
    return out


def capture_mysql(conn: Any, *, database: str, budget: DiscoveryBudget, timeout_s: float = 15.0,
                  pymysql_module: Any = None) -> dict[str, Any]:
    """Run the catalog queries and the bounded profile on an open PyMySQL connection.

    Synchronous by design (PyMySQL is); the connector calls it in a worker thread.
    """
    import pymysql as _pymysql

    pymysql = pymysql_module or _pymysql
    deadline = time.monotonic() + budget.max_seconds
    limit = budget.max_objects + 1
    rows: dict[str, Any] = {"database": database, "issues": [], "truncated": False}
    with conn.cursor() as cur:
        cur.execute(SET_TIMEOUT_SQL, (int(timeout_s * 1000),))
        cur.execute(START_READ_ONLY_SQL)
        try:
            for key, sql in (("relations", RELATIONS_SQL), ("columns", COLUMNS_SQL), ("keys", KEYS_SQL),
                             ("checks", CHECKS_SQL)):
                cur.execute(sql, (database, limit))
                found = list(cur.fetchall())
                if len(found) == limit:
                    found = found[:-1]
                    rows["truncated"] = True
                    rows["issues"].append({"code": "budget_exhausted", "locator": key})
                rows[key] = found
            cur.execute(CURRENT_ACCOUNT_SQL)
            account = cur.fetchone()["account"]
            grantee = _account_grantee(account)
            rows["own_grantees"] = [_grantee(*account.rsplit("@", 1))]
            cur.execute(OWN_PRIVILEGES_SQL, (grantee, grantee, database, grantee, database))
            own = list(cur.fetchall())
            rows["own_select_global"] = any(r["level"] == "global" and r["privilege"] == "SELECT" for r in own)
            rows["own_select_schema"] = any(r["level"] == "schema" and r["privilege"] == "SELECT" for r in own)
            rows["own_select_tables"] = sorted({r["table_name"] for r in own
                                                if r["level"] == "table" and r["privilege"] == "SELECT"})
            for key, sql in (("schema_grants", SCHEMA_GRANTS_SQL), ("table_grants", TABLE_GRANTS_SQL)):
                try:
                    cur.execute(sql, (database,))
                    rows[key] = list(cur.fetchall())
                except pymysql.err.OperationalError:
                    rows[key] = []
                    if not any(i["code"] == "grants_not_visible" for i in rows["issues"]):
                        rows["issues"].append({"code": "grants_not_visible", "locator": "mysql.db"})
                except pymysql.err.ProgrammingError:
                    rows[key] = []
            rows["profiles"] = _profile(cur, pymysql, rows, database, budget, deadline)
        finally:
            cur.execute(ROLLBACK_SQL)
    return rows


def _profile(cur: Any, pymysql: Any, rows: dict[str, Any], database: str, budget: DiscoveryBudget,
             deadline: float) -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []
    if budget.sample_rows == 0 or budget.max_profiled_columns == 0:
        return profiles
    snapshot = snapshot_from_mysql({**rows, "profiles": [], "issues": []})
    by_table: dict[str, list[dict[str, Any]]] = {}
    for col in snapshot["columns"]:
        by_table.setdefault(col["table"], []).append(col)
    remaining = budget.max_profiled_columns
    for rel in snapshot["relations"]:
        if rel["kind"] != "r":
            continue
        locator = f"table:{q(database)}.{q(rel['name'])}"
        if not rel["readable"]:
            rows["issues"].append({"code": "not_readable", "locator": locator})
            continue
        if remaining <= 0 or time.monotonic() > deadline:
            rows["truncated"] = True
            rows["issues"].append({"code": "profile_budget_exhausted", "locator": locator})
            continue
        cols = by_table.get(rel["name"], [])[:remaining]
        remaining -= len(cols)
        try:
            profiles.extend(_profile_table(cur, database, rel, cols, budget))
        except pymysql.err.OperationalError as exc:
            code = exc.args[0] if exc.args else None
            rows["issues"].append({"code": "profile_timeout" if code == 3024 else "permission_denied",
                                   "locator": locator})
    return profiles


# ------------------------------------------------------------------------------- connector


def _target(endpoint: str) -> tuple[str, int, str] | None:
    try:
        parts = urlsplit(endpoint)
        port = parts.port or 3306
    except ValueError:
        return None
    database = unquote(parts.path.lstrip("/"))
    if parts.scheme != "mysql" or not parts.hostname or not database or "/" in database:
        return None
    return parts.hostname, port, database


class MySqlConnectorFactory:
    async def open(self, descriptor: ConnectorDescriptor, credential: Credential | None) -> "MySqlConnector":
        return MySqlConnector(descriptor, credential)


class MySqlConnector:
    def __init__(self, descriptor: ConnectorDescriptor, credential: Credential | None) -> None:
        self.descriptor = descriptor
        self._credential = credential

    def _preflight(self) -> tuple[tuple[str, int, str] | None, str | None]:
        target = _target(self.descriptor.endpoint)
        if target is None:
            return None, "invalid_endpoint"
        user = self.descriptor.options.get("user")
        tls = str(self.descriptor.options.get("tls", "require"))
        ca_file = self.descriptor.options.get("ca_file")
        if not isinstance(user, str) or not user or tls not in _TLS_MODES:
            return None, "invalid_options"
        if ca_file is not None and not isinstance(ca_file, str):
            return None, "invalid_options"
        return target, None

    def _connect_sync(self, target: tuple[str, int, str]) -> tuple[Any, str | None]:
        import pymysql

        host, port, database = target
        tls = str(self.descriptor.options.get("tls", "require"))
        context: ssl.SSLContext | None = None
        if tls != "disable":
            context = ssl.create_default_context(cafile=self.descriptor.options.get("ca_file") or None)
            if tls == "require":  # encrypted, certificate not verified -- as libpq's sslmode=require
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
        timeout = max(1, int(self.descriptor.timeout_s))
        try:
            conn = pymysql.connect(
                host=host, port=port, user=self.descriptor.options["user"],
                password=self._credential.reveal() if self._credential else "", database=database,
                connect_timeout=timeout, read_timeout=timeout + 5, write_timeout=timeout,
                charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor, ssl=context, autocommit=False,
                init_command="SET SESSION TRANSACTION READ ONLY", program_name="aura-connector",
            )
        except pymysql.err.OperationalError as exc:
            code = exc.args[0] if exc.args else None
            if code in (1045, 1044, 1698):
                return None, "auth_failed"
            if code == 1049:
                return None, "unknown_database"
            if isinstance(exc.__context__, ssl.SSLError):
                return None, "tls_failed"  # e.g. verify-full against an untrusted certificate
            if code in (2002, 2003, 2005, 2006, 2013):
                return None, "unreachable"
            logger.info("mysql connector refused", extra={"connector": self.descriptor.id, "code": code})
            return None, "connect_failed"
        return conn, None

    def _health_sync(self, target: tuple[str, int, str]) -> tuple[str, tuple[str, ...] | None]:
        import pymysql

        conn, reason = self._connect_sync(target)
        if conn is None:
            return reason or "connect_failed", None
        try:
            with conn.cursor() as cur:
                cur.execute(SHOW_GRANTS_SQL)
                lines = [next(iter(row.values())) for row in cur.fetchall()]
        except pymysql.err.Error:
            return "inspection_failed", None
        finally:
            conn.close()
        return "ok", privilege_findings(lines)

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
        import pymysql

        conn, reason = self._connect_sync(target)
        if conn is None:
            raise DependencyUnavailableError(
                "the connected database could not be reached for discovery", dependency="connector",
                details={"connector": self.descriptor.id, "reason": reason})
        try:
            return capture_mysql(conn, database=target[2], budget=budget, timeout_s=self.descriptor.timeout_s)
        except pymysql.err.Error as exc:
            code = exc.args[0] if exc.args else None
            logger.info("mysql discovery failed", extra={"connector": self.descriptor.id, "code": code})
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
        return artifacts_from_catalog(self.descriptor.id, snapshot_from_mysql(rows), budget, extractor="mysql",
                                      catalog_extractor=CATALOG_EXTRACTOR, profile_extractor=PROFILE_EXTRACTOR)

    async def read(self, template: ReadTemplate, arguments: Mapping[str, Any]) -> list[dict[str, Any]]:
        raise ToolExecutionError("templated reads are not available for MySQL yet", tool=template.id)

    async def aclose(self) -> None:
        return None
