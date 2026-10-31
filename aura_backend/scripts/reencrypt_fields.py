#!/usr/bin/env python3
"""One-shot re-encryption of sensitive columns at rest (P1.2/P5.1).

Seals every row the current data key would store differently:

* **legacy plaintext** -- memories and personal-OS records written before
  ``AURA_DATA_ENCRYPTION_KEYS`` was configured (the stores read these fine and seal them lazily on
  their next write; this script does the rest in one pass instead of waiting);
* **rows under a retired key** -- after prepending a new key to ``AURA_DATA_ENCRYPTION_KEYS``,
  rows still sealed under an older one. Run this, confirm it reports nothing left, and only then
  drop the old key.

Covers ``aura_memories.content``, ``aura_personal_os_records.fields`` and -- since ADR-032 --
``aura_user_data_keys.wrapped_key`` (each user's data key, wrapped by these same keys), for every
owner. Re-wrapping a data key changes only its wrapping: the ``gcm2`` ciphertext in the synced
tables is sealed with the data key itself and is untouched. Server-side encryption at rest, not
end-to-end: see ``aura_core/adapters/field_crypto.py``.

Properties:

* **Idempotent.** A row already sealed under the primary key is skipped by the ``SELECT`` itself
  (its envelope names the key id), so a second run finds nothing to do.
* **Batched.** Keyset pagination on ``id``, one transaction per batch.
* **Safe against concurrent writes.** Each ``UPDATE`` is conditional on the column still holding
  the value that was read; if the app rewrote the row meanwhile (which already seals it under
  the primary key), the update matches nothing and the row counts as ``raced``.
* **Never prints content.** Row ids and counts only.
* ``--dry-run`` reads and decrypts every candidate row -- proving the configured keys can open
  everything -- and writes nothing.

Usage (inside the gateway container, which has the database URL and key files)::

    docker compose -p aura -f deploy/docker-compose.prod.yml exec gateway \\
        python scripts/reencrypt_fields.py --dry-run
    docker compose -p aura -f deploy/docker-compose.prod.yml exec gateway \\
        python scripts/reencrypt_fields.py

Exit status: 0 on success, 1 if any row could not be decrypted (tampered, or sealed under a key
that is no longer configured -- the ids are listed), 2 if no data key is configured.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# `python scripts/reencrypt_fields.py` puts scripts/ on sys.path, not the backend root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@dataclass
class TableReport:
    table: str
    examined: int = 0
    plaintext: int = 0
    rotated: int = 0
    written: int = 0
    raced: int = 0
    failed_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "examined": self.examined,
            "plaintext": self.plaintext,
            "old_key": self.rotated,
            "written": self.written,
            "raced": self.raced,
            "failed": len(self.failed_ids),
            "failed_ids": self.failed_ids[:50],
        }


async def _table_exists(conn: Any, name: str) -> bool:
    return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", name))


async def _reencrypt_memories(pool: Any, cipher: Any, *, batch: int, dry_run: bool) -> TableReport:
    from aura_core.adapters.field_crypto import ENVELOPE_PREFIX
    from aura_core.adapters.postgres_store import PostgresMemoryStore
    from aura_core.domain.errors import AuraError

    report = TableReport("aura_memories")
    current = f"{ENVELOPE_PREFIX}{cipher.primary_key_id}:%"
    last_id = ""
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "aura_memories"):
            return report
        while True:
            rows = await conn.fetch(
                "SELECT id, owner_id, content FROM aura_memories "
                "WHERE id > $1 AND content NOT LIKE $2 ORDER BY id LIMIT $3",
                last_id, current, batch,
            )
            if not rows:
                break
            last_id = rows[-1]["id"]
            async with conn.transaction():
                for row in rows:
                    report.examined += 1
                    stored = row["content"]
                    context = PostgresMemoryStore.encryption_context(row["id"], row["owner_id"])
                    try:
                        plaintext = cipher.decrypt(stored, context=context)
                    except AuraError:
                        report.failed_ids.append(row["id"])
                        continue
                    if cipher.is_encrypted(stored):
                        report.rotated += 1
                    else:
                        report.plaintext += 1
                    if dry_run:
                        continue
                    status = await conn.execute(
                        "UPDATE aura_memories SET content = $1 WHERE id = $2 AND content = $3",
                        cipher.encrypt(plaintext, context=context), row["id"], stored,
                    )
                    if status.endswith(" 1"):
                        report.written += 1
                    else:
                        report.raced += 1
    return report


async def _reencrypt_personal_os(
    pool: Any, cipher: Any, *, batch: int, dry_run: bool
) -> TableReport:
    from aura_core.adapters.field_crypto import ENVELOPE_PREFIX, JSON_ENVELOPE_KEY
    from aura_core.adapters.postgres_personal_os import PostgresPersonalOsStore
    from aura_core.domain.errors import AuraError

    report = TableReport("aura_personal_os_records")
    current = f"{ENVELOPE_PREFIX}{cipher.primary_key_id}:%"
    last_id = ""
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "aura_personal_os_records"):
            return report
        while True:
            # `fields->>'_aura_enc'` is the one JSONB operator this feature uses, and only on the
            # envelope key to find rows not yet sealed under the primary key -- never on content.
            rows = await conn.fetch(
                "SELECT id, owner_id, fields::text AS fields FROM aura_personal_os_records "
                "WHERE id > $1 AND COALESCE(fields->>$2, '') NOT LIKE $3 "
                "ORDER BY id LIMIT $4",
                last_id, JSON_ENVELOPE_KEY, current, batch,
            )
            if not rows:
                break
            last_id = rows[-1]["id"]
            async with conn.transaction():
                for row in rows:
                    report.examined += 1
                    stored_text = row["fields"]
                    stored = json.loads(stored_text)
                    context = PostgresPersonalOsStore.encryption_context(row["id"], row["owner_id"])
                    try:
                        fields = cipher.decrypt_mapping(stored, context=context)
                    except (AuraError, ValueError):
                        report.failed_ids.append(row["id"])
                        continue
                    if cipher.is_encrypted_mapping(stored):
                        report.rotated += 1
                    else:
                        report.plaintext += 1
                    if dry_run:
                        continue
                    status = await conn.execute(
                        "UPDATE aura_personal_os_records SET fields = $1::jsonb "
                        "WHERE id = $2 AND fields = $3::jsonb",
                        json.dumps(cipher.encrypt_mapping(fields, context=context)),
                        row["id"], stored_text,
                    )
                    if status.endswith(" 1"):
                        report.written += 1
                    else:
                        report.raced += 1
    return report


async def _rewrap_user_data_keys(
    pool: Any, cipher: Any, *, batch: int, dry_run: bool
) -> TableReport:
    """Re-wrap each user's data key (ADR-032) under the primary KEK. Never prints a key."""
    from aura_core.adapters.field_crypto import ENVELOPE_PREFIX
    from aura_core.adapters.user_data_keys import DataKeyWrapper
    from aura_core.domain.errors import AuraError

    report = TableReport("aura_user_data_keys")
    wrapper = DataKeyWrapper(cipher)
    current = f"{ENVELOPE_PREFIX}{cipher.primary_key_id}:%"
    last = ("", "")
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "aura_user_data_keys"):
            return report
        while True:
            rows = await conn.fetch(
                "SELECT owner_id, kid, wrapped_key FROM aura_user_data_keys "
                "WHERE (owner_id, kid) > ($1, $2) AND wrapped_key NOT LIKE $3 "
                "ORDER BY owner_id, kid LIMIT $4",
                last[0], last[1], current, batch,
            )
            if not rows:
                break
            last = (rows[-1]["owner_id"], rows[-1]["kid"])
            async with conn.transaction():
                for row in rows:
                    report.examined += 1
                    label = f"{row['owner_id']}/{row['kid']}"
                    stored = row["wrapped_key"]
                    try:
                        key = wrapper.unwrap(row["owner_id"], row["kid"], stored)
                    except AuraError:
                        report.failed_ids.append(label)
                        continue
                    if cipher.is_encrypted(stored):
                        report.rotated += 1
                    else:
                        report.plaintext += 1
                    if dry_run:
                        continue
                    status = await conn.execute(
                        "UPDATE aura_user_data_keys SET wrapped_key = $1, kek_id = $2 "
                        "WHERE owner_id = $3 AND kid = $4 AND wrapped_key = $5",
                        wrapper.wrap(row["owner_id"], row["kid"], key), cipher.primary_key_id,
                        row["owner_id"], row["kid"], stored,
                    )
                    if status.endswith(" 1"):
                        report.written += 1
                    else:
                        report.raced += 1
    return report


async def run(*, dry_run: bool, batch: int, tables: str, database_url: str | None = None) -> int:
    from aura_core.adapters.postgres_store import PostgresPool
    from aura_core.config.settings import load_settings
    from aura_core.container import build_field_cipher

    settings = load_settings(refresh=True, validate=False)
    cipher = build_field_cipher(settings)  # refuses in staging/production without a key
    if not cipher.enabled:
        print(
            json.dumps({"error": "AURA_DATA_ENCRYPTION_KEYS is not set; nothing to encrypt with"}),
            file=sys.stderr,
        )
        return 2

    pool = PostgresPool(database_url or settings.infrastructure.database_url, min_size=1, max_size=2)
    reports: list[TableReport] = []
    try:
        pg = await pool._get_pool()
        if tables in ("all", "memories"):
            reports.append(await _reencrypt_memories(pg, cipher, batch=batch, dry_run=dry_run))
        if tables in ("all", "personal_os"):
            reports.append(await _reencrypt_personal_os(pg, cipher, batch=batch, dry_run=dry_run))
        if tables in ("all", "user_data_keys"):
            reports.append(await _rewrap_user_data_keys(pg, cipher, batch=batch, dry_run=dry_run))
    finally:
        await pool.close()

    print(json.dumps({
        "dry_run": dry_run,
        "primary_key_id": cipher.primary_key_id,
        "configured_key_ids": list(cipher.key_ids),
        "tables": [r.as_dict() for r in reports],
    }, indent=2))
    return 1 if any(r.failed_ids for r in reports) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="decrypt and count candidate rows, write nothing")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument(
        "--tables", choices=("all", "memories", "personal_os", "user_data_keys"), default="all"
    )
    parser.add_argument("--database-url", default=None,
                        help="override AURA_DATABASE_URL (default: the configured one)")
    args = parser.parse_args(argv)
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")

    from infrastructure.secret_hydration import hydrate_secrets_from_files

    hydrate_secrets_from_files()
    return asyncio.run(run(
        dry_run=args.dry_run, batch=args.batch_size, tables=args.tables,
        database_url=args.database_url,
    ))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
