"""Golden discovery output for the Track AP reference pilots (AP3).

Two jobs, both run from the repo root:

    # Render the expected discovery reports from the committed inputs (no database needed).
    python aura_backend/scripts/pilot_discovery_goldens.py

    # Re-record each pilot's catalog snapshot from a live Postgres, then render.
    python aura_backend/scripts/pilot_discovery_goldens.py --record-catalog postgresql://aura:pw@localhost:55436/postgres

Inputs per pilot (``aura_backend/tests/fixtures/pilots/<pilot>/``): ``schema.sql`` + ``seed.sql``
(loaded into a fresh database to record ``golden/catalog.json``) and ``openapi.yaml`` or
``openapi.json``. Outputs: ``golden/discovery_postgres.json`` and ``golden/discovery_openapi.json``.

``tests/unit/test_adaptation_discovery.py`` fails when a committed golden differs from what this
renders; ``tests/load/test_adaptation_discovery_postgres.py`` proves a live capture still equals
the recorded catalog.
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--record-catalog", metavar="DSN", help="superuser DSN of a throwaway Postgres")
    args = parser.parse_args()
    for pilot in PILOTS:
        if args.record_catalog:
            asyncio.run(record_catalog(args.record_catalog, pilot))
        golden = PILOTS_DIR / pilot / "golden"
        golden.mkdir(parents=True, exist_ok=True)
        (golden / "discovery_postgres.json").write_text(render_postgres(pilot), encoding="utf-8")
        (golden / "discovery_openapi.json").write_text(render_openapi(pilot), encoding="utf-8")
        print(f"rendered {pilot}/golden/discovery_postgres.json, discovery_openapi.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
