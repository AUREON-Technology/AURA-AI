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
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "aura_backend"))

from aura_core.adapters.connectors.git import scan_repository  # noqa: E402
from aura_core.adapters.connectors.openapi import artifacts_from_openapi, parse_spec  # noqa: E402
from aura_core.adapters.connectors.postgres_catalog import artifacts_from_catalog, capture_catalog  # noqa: E402
from aura_core.domain.discovery import DiscoveryBudget  # noqa: E402
from aura_core.domain.serialization import to_data  # noqa: E402

PILOTS_DIR = ROOT / "aura_backend" / "tests" / "fixtures" / "pilots"
PILOTS = ("hr", "ecommerce")


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
    args = parser.parse_args()
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
