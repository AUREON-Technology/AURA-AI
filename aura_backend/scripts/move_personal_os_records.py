#!/usr/bin/env python3
"""Move the agent's old personal-OS records into the app's synced tables (ADR-032).

Before ADR-032 the agent's ``create_task`` & co. wrote ``aura_personal_os_records``, which no
client reads. This copies every row of it into ``tasks``/``calendar_events``/``finance_records``/
``health_records`` through ``PostgresSyncedPersonalOsStore`` -- encrypted with each owner's data
key, stamped as a sync change -- so the records reach the owner's devices on their next sync.

Properties:

* **Idempotent.** A record keeps its id; a row with that id already in the synced table
  (including a tombstone, i.e. the user deleted it after a previous run) is skipped. A health
  record for a day the app already has a row for is skipped too, rather than overwriting the
  app's newer values with the agent's older ones.
* **Non-destructive.** ``aura_personal_os_records`` is read, never modified or dropped.
* ``--dry-run`` decrypts and maps every row and writes nothing -- not even a data key.
* **Never prints content.** Counts and ids only.

The moved row's ``created_at`` is the time of the move; the old table's timestamp is not carried
over.

Usage (inside the gateway container, which has the database URL and the data keys)::

    python scripts/move_personal_os_records.py --dry-run
    python scripts/move_personal_os_records.py

Exit status: 0 on success, 1 if any row could not be decrypted, mapped or written (ids listed),
2 if the memory backend is not Postgres.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# `python scripts/move_personal_os_records.py` puts scripts/ on sys.path, not the backend root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def move(pool: Any, cipher: Any, store: Any, *, batch: int, dry_run: bool) -> dict:
    """Copy every legacy row through ``store``. ``pool`` is an asyncpg pool."""
    from aura_core.adapters.field_crypto import FieldCipher
    from aura_core.adapters.postgres_app_users import find_user_id
    from aura_core.adapters.postgres_personal_os import PostgresPersonalOsStore
    from aura_core.adapters.postgres_synced_personal_os import TABLES, to_columns
    from aura_core.domain.errors import AuraError
    from aura_core.domain.identifiers import PersonalOsRecordId, UserId
    from aura_core.domain.personal_os import PersonalOsRecord, RecordKind
    from aura_core.domain.user_keys import DataKey, UserKeyring

    counts: Counter = Counter()
    failed: list[str] = []
    # Dry-run maps fields with a throwaway key so nothing is created; the ciphertext is discarded.
    dry_ring = UserKeyring(owner_id=UserId("dry-run"), keys=(DataKey(
        kid="00000000", key=bytes(32), created_at=datetime.now(timezone.utc), primary=True,
    ),))
    last_id = ""
    async with pool.acquire() as conn:
        exists = await conn.fetchval("SELECT to_regclass('aura_personal_os_records') IS NOT NULL")
        if not exists:
            return {"examined": 0}
        while True:
            rows = await conn.fetch(
                "SELECT id, owner_id, kind, fields::text AS fields, source, created_at, updated_at "
                "FROM aura_personal_os_records WHERE id > $1 ORDER BY id LIMIT $2",
                last_id, batch,
            )
            if not rows:
                break
            last_id = rows[-1]["id"]
            for row in rows:
                counts["examined"] += 1
                try:
                    kind = RecordKind(row["kind"])
                    fields = (cipher or FieldCipher.disabled()).decrypt_mapping(
                        json.loads(row["fields"]),
                        context=PostgresPersonalOsStore.encryption_context(
                            row["id"], row["owner_id"]
                        ),
                    )
                    row_id = uuid.UUID(row["id"])
                except (AuraError, ValueError):
                    failed.append(row["id"])
                    continue
                table = TABLES[kind]
                if await conn.fetchval(f"SELECT 1 FROM {table.name} WHERE id = $1", row_id):
                    counts["already_moved"] += 1
                    continue
                user_id = await find_user_id(conn, row["owner_id"])
                if table.natural_key is not None and user_id is not None:
                    try:
                        key_value = to_columns(
                            kind, fields, dry_ring, now=datetime.now(timezone.utc)
                        ).get(table.natural_key)
                    except AuraError:
                        failed.append(row["id"])
                        continue
                    if key_value is not None and await conn.fetchval(
                        f"SELECT 1 FROM {table.name} "
                        f"WHERE user_id = $1 AND {table.natural_key} = $2",
                        user_id, key_value,
                    ):
                        counts["kept_app_row"] += 1
                        continue
                record = PersonalOsRecord(
                    id=PersonalOsRecordId(row["id"]), owner_id=UserId(row["owner_id"]),
                    kind=kind, fields=fields, created_at=row["created_at"],
                    updated_at=row["updated_at"], source=row["source"],
                )
                if dry_run:
                    try:
                        to_columns(kind, fields, dry_ring, now=datetime.now(timezone.utc))
                    except AuraError:
                        failed.append(row["id"])
                        continue
                    counts["would_move"] += 1
                    continue
                try:
                    await store.put(record)
                except AuraError:
                    failed.append(row["id"])
                    continue
                counts["moved"] += 1
                counts[f"moved_{kind.value}"] += 1
    return {**dict(counts), "failed": len(failed), "failed_ids": failed[:50]}


async def run(*, dry_run: bool, batch: int, database_url: str | None = None) -> int:
    from aura_core.adapters.postgres_store import PostgresPool
    from aura_core.adapters.postgres_synced_personal_os import PostgresSyncedPersonalOsStore
    from aura_core.adapters.postgres_user_data_keys import PostgresUserDataKeyStore
    from aura_core.config.settings import load_settings
    from aura_core.container import build_field_cipher

    settings = load_settings(refresh=True, validate=False)
    if settings.infrastructure.memory_backend != "postgres" and database_url is None:
        print(json.dumps({"error": "the memory backend is not postgres; nothing to move"}),
              file=sys.stderr)
        return 2
    cipher = build_field_cipher(settings)  # refuses in staging/production without a key
    url = database_url or settings.infrastructure.database_url
    pool = PostgresPool(url, min_size=1, max_size=3)
    try:
        keys = PostgresUserDataKeyStore(url, pool=pool, cipher=cipher)
        store = PostgresSyncedPersonalOsStore(url, pool=pool, keys=keys)
        report = await move(
            await pool._get_pool(), cipher, store, batch=batch, dry_run=dry_run
        )
    finally:
        await pool.close()
    print(json.dumps({"dry_run": dry_run, **report}, indent=2))
    return 1 if report.get("failed") else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="decrypt and map every row, write nothing")
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--database-url", default=None,
                        help="override AURA_DATABASE_URL (default: the configured one)")
    args = parser.parse_args(argv)
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")

    from infrastructure.secret_hydration import hydrate_secrets_from_files

    hydrate_secrets_from_files()
    return asyncio.run(run(
        dry_run=args.dry_run, batch=args.batch_size, database_url=args.database_url,
    ))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
