"""Golden discovery output for the Track AP reference pilots (AP3).

Two jobs, both run from the repo root:

    # Render the expected discovery reports from the committed inputs (no database needed).
    python aura_backend/scripts/pilot_discovery_goldens.py

    # Re-record each pilot's catalog snapshot from a live Postgres, then render.
    python aura_backend/scripts/pilot_discovery_goldens.py --record-catalog postgresql://aura:pw@localhost:55436/postgres

    # Re-record each pilot's table rows (AP8's in-memory replica), from a live Postgres.
    python aura_backend/scripts/pilot_discovery_goldens.py --record-rows postgresql://aura:pw@localhost:55436/postgres

Inputs per pilot (``aura_backend/tests/fixtures/pilots/<pilot>/``): ``schema.sql`` + ``seed.sql``
(loaded into a fresh database to record ``golden/catalog.json``), ``openapi.yaml`` or
``openapi.json``, and ``repo/`` -- the application's code, read by the Git connector (AP10).
Outputs: ``golden/discovery_postgres.json``, ``golden/discovery_openapi.json`` and
``golden/discovery_git.json``.

``tests/unit/test_adaptation_discovery.py`` fails when a committed golden differs from what this
renders; ``tests/load/test_adaptation_discovery_postgres.py`` proves a live capture still equals
the recorded catalog.

The HR pilot is also written in MySQL and T-SQL (``hr/mysql/schema.sql``, ``hr/mssql/schema.sql``,
AP10), seeded from ``golden/rows.json`` so every engine holds the same data:

    # Re-record the raw MySQL / SQL Server captures (admin URLs of throwaway servers), then render.
    python aura_backend/scripts/pilot_discovery_goldens.py --record-mysql mysql://root:pw@localhost:3306
    python aura_backend/scripts/pilot_discovery_goldens.py --record-mssql mssql://sa:pw@localhost:1433

These write ``golden/catalog_mysql.json`` / ``catalog_mssql.json`` -- the capture's *raw* catalog
rows, before mapping, so the default suite exercises the pure mapping on them -- and render
``golden/discovery_mysql.json`` / ``discovery_mssql.json``.
``tests/load/test_adaptation_discovery_engines.py`` proves a live capture still equals them.

``golden/rows.json`` is every base table's rows as JSON values (exactly what
``PostgresConnector.read`` returns), ordered by primary key: the data the evaluation gate's
in-memory replica serves in the default test suite (``tests/unit/test_adaptation_gate.py``).
``tests/load/test_adaptation_gate_postgres.py`` proves the replica answers every pilot template
exactly as the loaded database does.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import uuid
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "aura_backend"))

from aura_core.adapters.connectors.git import scan_repository  # noqa: E402
from aura_core.adapters.connectors.openapi import artifacts_from_openapi, parse_spec  # noqa: E402
from aura_core.adapters.connectors.postgres_catalog import artifacts_from_catalog, capture_catalog  # noqa: E402
from aura_core.domain.discovery import DiscoveryBudget  # noqa: E402
from aura_core.domain.serialization import to_data  # noqa: E402

PILOTS_DIR = ROOT / "aura_backend" / "tests" / "fixtures" / "pilots"
PILOTS = ("hr", "ecommerce")
#: Pilots that also have a MySQL and a T-SQL twin of their schema.
ENGINE_PILOTS = ("hr",)
#: The database each engine's pilot is loaded into. Fixed, because MySQL reports the database as
#: every object's schema, so the name is part of the recorded catalog.
ENGINE_DATABASE = "aura_pilot_hr"
#: The read-only account a capture runs as (its own grants are excluded from the catalog).
DISCOVERY_ACCOUNT = "aura_discovery"


def _dump(data: object) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def db_source_id(pilot: str) -> str:
    return f"{pilot}-db"


def api_source_id(pilot: str) -> str:
    return f"{pilot}-api"


def repo_source_id(pilot: str) -> str:
    return f"{pilot}-repo"


def spec_path(pilot: str) -> Path:
    for name in ("openapi.yaml", "openapi.json"):
        if (PILOTS_DIR / pilot / name).exists():
            return PILOTS_DIR / pilot / name
    raise FileNotFoundError(f"no OpenAPI description for pilot {pilot!r}")


def render_postgres(pilot: str) -> str:
    catalog = json.loads((PILOTS_DIR / pilot / "golden" / "catalog.json").read_text(encoding="utf-8"))
    return _dump(to_data(artifacts_from_catalog(db_source_id(pilot), catalog, DiscoveryBudget())))


def render_mysql(pilot: str) -> str:
    from aura_core.adapters.connectors.mysql import CATALOG_EXTRACTOR, PROFILE_EXTRACTOR, snapshot_from_mysql

    raw = json.loads((PILOTS_DIR / pilot / "golden" / "catalog_mysql.json").read_text(encoding="utf-8"))
    report = artifacts_from_catalog(db_source_id(pilot), snapshot_from_mysql(raw), DiscoveryBudget(),
                                    extractor="mysql", catalog_extractor=CATALOG_EXTRACTOR,
                                    profile_extractor=PROFILE_EXTRACTOR)
    return _dump(to_data(report))


def render_mssql(pilot: str) -> str:
    from aura_core.adapters.connectors.mssql import CATALOG_EXTRACTOR, PROFILE_EXTRACTOR, snapshot_from_mssql

    raw = json.loads((PILOTS_DIR / pilot / "golden" / "catalog_mssql.json").read_text(encoding="utf-8"))
    report = artifacts_from_catalog(db_source_id(pilot), snapshot_from_mssql(raw), DiscoveryBudget(),
                                    extractor="mssql", catalog_extractor=CATALOG_EXTRACTOR,
                                    profile_extractor=PROFILE_EXTRACTOR)
    return _dump(to_data(report))


def render_openapi(pilot: str) -> str:
    spec = parse_spec(spec_path(pilot).read_bytes())
    return _dump(to_data(artifacts_from_openapi(api_source_id(pilot), spec, DiscoveryBudget())))


def render_git(pilot: str) -> str:
    report = scan_repository(repo_source_id(pilot), PILOTS_DIR / pilot / "repo", DiscoveryBudget())
    return _dump(to_data(report))


async def load_pilot(admin_dsn: str, pilot: str) -> str:
    """Create a fresh database holding ``pilot``; return its DSN (caller drops it)."""
    import asyncpg

    name = f"pilot_{pilot}_{uuid.uuid4().hex[:8]}"
    admin = await asyncpg.connect(admin_dsn)
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    dsn = urlunsplit(urlsplit(admin_dsn)._replace(path=f"/{name}"))
    conn = await asyncpg.connect(dsn)
    try:
        for part in ("schema.sql", "seed.sql"):
            await conn.execute((PILOTS_DIR / pilot / part).read_text(encoding="utf-8"))
    finally:
        await conn.close()
    return dsn


async def drop_database(admin_dsn: str, dsn: str) -> None:
    import asyncpg

    admin = await asyncpg.connect(admin_dsn)
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{urlsplit(dsn).path.lstrip("/")}" WITH (FORCE)')
    finally:
        await admin.close()


async def record_catalog(admin_dsn: str, pilot: str) -> None:
    import asyncpg

    dsn = await load_pilot(admin_dsn, pilot)
    try:
        conn = await asyncpg.connect(dsn)
        try:
            snapshot = await capture_catalog(conn, budget=DiscoveryBudget(), timeout_s=30.0)
        finally:
            await conn.close()
    finally:
        await drop_database(admin_dsn, dsn)
    (PILOTS_DIR / pilot / "golden" / "catalog.json").write_text(_dump(snapshot), encoding="utf-8")
    print(f"recorded {pilot}/golden/catalog.json")


async def record_rows(admin_dsn: str, pilot: str) -> None:
    import asyncpg

    from aura_core.adapters.connectors.postgres import json_value
    from aura_core.domain.discovery import quote_identifier as q

    catalog = json.loads((PILOTS_DIR / pilot / "golden" / "catalog.json").read_text(encoding="utf-8"))
    tables = sorted((r["schema"], r["name"]) for r in catalog["relations"] if r["kind"] == "r")
    keys = {(c["schema"], c["table"]): c["columns"] for c in catalog["constraints"] if c["type"] == "p"}
    dsn = await load_pilot(admin_dsn, pilot)
    out: dict[str, list[dict[str, object]]] = {}
    try:
        conn = await asyncpg.connect(dsn)
        try:
            for schema, table in tables:
                order = keys.get((schema, table))
                text = f"SELECT * FROM {q(schema)}.{q(table)}"
                if order:
                    text += " ORDER BY " + ", ".join(q(c) for c in order)
                rows = await conn.fetch(text)
                out[f"{schema}.{table}"] = [{k: json_value(v) for k, v in row.items()} for row in rows]
        finally:
            await conn.close()
    finally:
        await drop_database(admin_dsn, dsn)
    (PILOTS_DIR / pilot / "golden" / "rows.json").write_text(_dump(out), encoding="utf-8")
    print(f"recorded {pilot}/golden/rows.json")


# ------------------------------------------------------------------ MySQL and SQL Server twins

#: Insertion order: every foreign key points at a table loaded before it (employees' manager_id
#: points at rows with smaller ids, which sorted insertion has already written).
_SEED_ORDER = ("departments", "employees", "leave_requests", "attendance", "assets")


def _seed_rows(pilot: str) -> list[tuple[str, list[dict[str, object]]]]:
    data = json.loads((PILOTS_DIR / pilot / "golden" / "rows.json").read_text(encoding="utf-8"))
    return [(table, data[f"public.{table}"]) for table in _SEED_ORDER]


def _server(admin_url: str, schemes: tuple[str, ...], default_port: int) -> dict[str, object]:
    parts = urlsplit(admin_url)
    if parts.scheme not in schemes or not parts.hostname:
        raise SystemExit(f"expected {schemes[0]}://user:password@host:port, got {admin_url!r}")
    return {"host": parts.hostname, "port": parts.port or default_port, "user": unquote(parts.username or ""),
            "password": unquote(parts.password or "")}


def load_mysql_pilot(admin_url: str, pilot: str, password: str) -> dict[str, object]:
    """(Re)create ``ENGINE_DATABASE`` holding ``pilot`` and a read-only discovery account that may
    read the grant tables; return that account's connection arguments."""
    import pymysql

    server = _server(admin_url, ("mysql",), 3306)
    admin = pymysql.connect(**server, autocommit=True, client_flag=pymysql.constants.CLIENT.MULTI_STATEMENTS)
    try:
        with admin.cursor() as cur:
            _drop_mysql_objects(cur, pilot)
            cur.execute(f"CREATE DATABASE `{ENGINE_DATABASE}` CHARACTER SET utf8mb4")
            cur.execute(f"USE `{ENGINE_DATABASE}`")
            cur.execute("SET time_zone = '+00:00'")
            schema = (PILOTS_DIR / pilot / "mysql" / "schema.sql").read_text(encoding="utf-8")
            cur.execute(schema.replace("__DATABASE__", ENGINE_DATABASE))
            while cur.nextset():
                pass
            for table, rows in _seed_rows(pilot):
                for row in rows:
                    values = [_mysql_value(v) for v in row.values()]
                    cur.execute(f"INSERT INTO `{table}` ({', '.join(f'`{c}`' for c in row)}) "
                                f"VALUES ({', '.join(['%s'] * len(row))})", values)
            cur.execute(f"CREATE USER '{DISCOVERY_ACCOUNT}'@'%%' IDENTIFIED BY %s", (password,))  # %% : bound
            for target in (f"`{ENGINE_DATABASE}`.*", "mysql.db", "mysql.tables_priv"):
                cur.execute(f"GRANT SELECT ON {target} TO '{DISCOVERY_ACCOUNT}'@'%'")
    finally:
        admin.close()
    return {**server, "user": DISCOVERY_ACCOUNT, "password": password, "database": ENGINE_DATABASE}


def _mysql_value(value: object) -> object:
    # rows.json carries timestamptz as ISO with +00:00; the session runs in UTC.
    if isinstance(value, str) and value.endswith("+00:00") and "T" in value:
        return value[:-6].replace("T", " ")
    return value


def _drop_mysql_objects(cur: object, pilot: str) -> None:
    """The database, the discovery account and the pilot's roles. MySQL roles are server-wide and
    DROP DATABASE leaves their grants behind, so a role left from an earlier run would carry stale
    privileges into the next capture."""
    schema = (PILOTS_DIR / pilot / "mysql" / "schema.sql").read_text(encoding="utf-8")
    roles = re.search(r"CREATE ROLE IF NOT EXISTS ([^;]+);", schema)
    cur.execute(f"DROP DATABASE IF EXISTS `{ENGINE_DATABASE}`")  # type: ignore[attr-defined]
    cur.execute(f"DROP USER IF EXISTS '{DISCOVERY_ACCOUNT}'@'%'")  # type: ignore[attr-defined]
    if roles:
        cur.execute(f"DROP ROLE IF EXISTS {roles.group(1)}")  # type: ignore[attr-defined]


def drop_mysql_pilot(admin_url: str, pilot: str) -> None:
    import pymysql

    admin = pymysql.connect(**_server(admin_url, ("mysql",), 3306), autocommit=True)
    try:
        with admin.cursor() as cur:
            _drop_mysql_objects(cur, pilot)
    finally:
        admin.close()


def capture_mysql_pilot(account: dict[str, object]) -> dict[str, object]:
    import pymysql

    from aura_core.adapters.connectors.mysql import capture_mysql

    conn = pymysql.connect(**account, cursorclass=pymysql.cursors.DictCursor, autocommit=False,
                           init_command="SET SESSION TRANSACTION READ ONLY")
    try:
        return capture_mysql(conn, database=ENGINE_DATABASE, budget=DiscoveryBudget(), timeout_s=30.0)
    finally:
        conn.close()


def _tsql_batches(text: str) -> list[str]:
    return [b for b in re.split(r"(?m)^\s*GO\s*$", text) if b.strip()]


def load_mssql_pilot(admin_url: str, pilot: str, password: str) -> dict[str, object]:
    """(Re)create ``ENGINE_DATABASE`` holding ``pilot`` and a read-only discovery user with
    ``VIEW DEFINITION``; return that login's connection arguments."""
    import pytds

    server = _server(admin_url, ("mssql", "sqlserver"), 1433)
    admin = pytds.connect(server["host"], user=server["user"], password=server["password"],
                          port=server["port"], autocommit=True)
    try:
        cur = admin.cursor()
        _drop_mssql_database(cur)
        cur.execute(f"CREATE DATABASE [{ENGINE_DATABASE}]")
        cur.execute(f"USE [{ENGINE_DATABASE}]")
        for batch in _tsql_batches((PILOTS_DIR / pilot / "mssql" / "schema.sql").read_text(encoding="utf-8")):
            cur.execute(batch)
        for table, rows in _seed_rows(pilot):
            cur.execute(f"SET IDENTITY_INSERT dbo.[{table}] ON")
            for row in rows:
                cur.execute(f"INSERT INTO dbo.[{table}] ({', '.join(f'[{c}]' for c in row)}) "
                            f"VALUES ({', '.join(['%s'] * len(row))})", tuple(row.values()))
            cur.execute(f"SET IDENTITY_INSERT dbo.[{table}] OFF")
        cur.execute(f"IF SUSER_ID('{DISCOVERY_ACCOUNT}') IS NOT NULL DROP LOGIN [{DISCOVERY_ACCOUNT}]")
        # CREATE LOGIN takes no parameters; the password is generated hex, so it needs no quoting.
        cur.execute(f"CREATE LOGIN [{DISCOVERY_ACCOUNT}] WITH PASSWORD = '{password}', CHECK_POLICY = OFF")
        cur.execute(f"CREATE USER [{DISCOVERY_ACCOUNT}] FOR LOGIN [{DISCOVERY_ACCOUNT}]")
        cur.execute(f"GRANT SELECT ON SCHEMA::dbo TO [{DISCOVERY_ACCOUNT}]")
        cur.execute(f"GRANT VIEW DEFINITION TO [{DISCOVERY_ACCOUNT}]")
    finally:
        admin.close()
    return {**server, "user": DISCOVERY_ACCOUNT, "password": password, "database": ENGINE_DATABASE}


def _drop_mssql_database(cur: object) -> None:
    cur.execute(f"IF DB_ID('{ENGINE_DATABASE}') IS NOT NULL BEGIN "  # type: ignore[attr-defined]
                f"ALTER DATABASE [{ENGINE_DATABASE}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; "
                f"DROP DATABASE [{ENGINE_DATABASE}]; END")


def drop_mssql_pilot(admin_url: str, pilot: str) -> None:
    import pytds

    server = _server(admin_url, ("mssql", "sqlserver"), 1433)
    admin = pytds.connect(server["host"], user=server["user"], password=server["password"],
                          port=server["port"], autocommit=True)
    try:
        cur = admin.cursor()
        _drop_mssql_database(cur)
        cur.execute(f"IF SUSER_ID('{DISCOVERY_ACCOUNT}') IS NOT NULL DROP LOGIN [{DISCOVERY_ACCOUNT}]")
    finally:
        admin.close()


def capture_mssql_pilot(account: dict[str, object]) -> dict[str, object]:
    import pytds

    from aura_core.adapters.connectors.mssql import capture_mssql

    conn = pytds.connect(account["host"], database=account["database"], user=account["user"],
                         password=account["password"], port=account["port"], as_dict=True, autocommit=True)
    try:
        return capture_mssql(conn, budget=DiscoveryBudget(), timeout_s=30.0)
    finally:
        conn.close()


def record_engine(engine: str, admin_url: str, pilot: str) -> None:
    load, capture, drop = {
        "mysql": (load_mysql_pilot, capture_mysql_pilot, drop_mysql_pilot),
        "mssql": (load_mssql_pilot, capture_mssql_pilot, drop_mssql_pilot),
    }[engine]
    try:
        raw = capture(load(admin_url, pilot, "Aa1-" + uuid.uuid4().hex))
    finally:
        drop(admin_url, pilot)
    (PILOTS_DIR / pilot / "golden" / f"catalog_{engine}.json").write_text(_dump(raw), encoding="utf-8")
    print(f"recorded {pilot}/golden/catalog_{engine}.json")


def load_rows(pilot: str) -> dict[tuple[str, str], list[dict[str, object]]]:
    """The recorded rows, keyed ``(schema, table)`` as the in-memory replica takes them."""
    data = json.loads((PILOTS_DIR / pilot / "golden" / "rows.json").read_text(encoding="utf-8"))
    return {tuple(key.split(".", 1)): rows for key, rows in data.items()}  # type: ignore[misc]


def enum_orders(pilot: str) -> dict[tuple[str, str, str], list[str]]:
    """Enum columns' labels in declaration order, from the recorded catalog (PostgreSQL sorts an
    enum by it, so the in-memory replica must too)."""
    catalog = json.loads((PILOTS_DIR / pilot / "golden" / "catalog.json").read_text(encoding="utf-8"))
    labels = {(e["schema"], e["name"]): list(e["labels"]) for e in catalog["enums"]}
    return {(c["schema"], c["table"], c["name"]): labels[(c["type_schema"], c["type_name"])]
            for c in catalog["columns"] if c["is_enum"] and (c["type_schema"], c["type_name"]) in labels}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--record-catalog", metavar="DSN", help="superuser DSN of a throwaway Postgres")
    parser.add_argument("--record-rows", metavar="DSN", help="superuser DSN of a throwaway Postgres")
    parser.add_argument("--record-mysql", metavar="URL", help="mysql://admin:pw@host:port of a throwaway MySQL 8")
    parser.add_argument("--record-mssql", metavar="URL", help="mssql://sa:pw@host:port of a throwaway SQL Server")
    args = parser.parse_args()
    for pilot in ENGINE_PILOTS:
        if args.record_mysql:
            record_engine("mysql", args.record_mysql, pilot)
        if args.record_mssql:
            record_engine("mssql", args.record_mssql, pilot)
        golden = PILOTS_DIR / pilot / "golden"
        (golden / "discovery_mysql.json").write_text(render_mysql(pilot), encoding="utf-8")
        (golden / "discovery_mssql.json").write_text(render_mssql(pilot), encoding="utf-8")
        print(f"rendered {pilot}/golden/discovery_mysql.json, discovery_mssql.json")
    for pilot in PILOTS:
        if args.record_catalog:
            asyncio.run(record_catalog(args.record_catalog, pilot))
        if args.record_rows:
            asyncio.run(record_rows(args.record_rows, pilot))
        golden = PILOTS_DIR / pilot / "golden"
        golden.mkdir(parents=True, exist_ok=True)
        (golden / "discovery_postgres.json").write_text(render_postgres(pilot), encoding="utf-8")
        (golden / "discovery_openapi.json").write_text(render_openapi(pilot), encoding="utf-8")
        (golden / "discovery_git.json").write_text(render_git(pilot), encoding="utf-8")
        print(f"rendered {pilot}/golden/discovery_postgres.json, discovery_openapi.json, discovery_git.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
