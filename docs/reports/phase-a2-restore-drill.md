# Phase A2 — First Execution of the Restore Drill

Run 2026-08-23 against `aa43ed9`, in a disposable Linux container with Docker. Roadmap reference:
[`docs/AURA_UPGRADE_ROADMAP.md`](../AURA_UPGRADE_ROADMAP.md) Phase A2.

## Status: the drill now passes, and did not before

`deploy/scripts/restore-drill.sh` was written and had never been executed. On first run it
**failed**, for the same reason A1's compose file had four defects: nothing that has never been
run is known to work.

The bug is fixed, the drill passes against both a synthesized archive and a real AURA backup, and
its success assertion has been tightened because the original one could not tell a good backup
from a worthless one.

No VPS or production data was involved. The script starts its own throwaway `postgres:16-alpine`
and refuses to run if `PGHOST`, `PGDATABASE` or `PGUSER` is set.

## Defect 1 — the drill could never have succeeded

```
pg_restore: error: connection to server on socket "/var/run/postgresql/.s.PGSQL.5432" failed:
FATAL:  role "root" does not exist
```

Every command in the script passes `-U drill` except one. `pg_restore --list` on lines 68 and 70
does not need it (it reads the archive file and never connects), but line 78 does connect:

```sh
docker exec "$CID" pg_restore --no-owner --no-privileges --dbname=drill_restore "$INNER"
```

`docker exec` runs as root, libpq defaults the connection user to the OS user, and the container's
Postgres has no `root` role. The drill therefore failed at the restore step every time it was run
— which, until now, was never.

Fixed by adding `-U drill`, matching every other call in the file.

## Defect 2 — the success assertion did not check the data

The original check was:

```sh
SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public';
```

A restore whose every `COPY` silently restored nothing still leaves the full schema behind, so
this passes while the backup is worthless. For a drill whose entire purpose is proving a backup
can be recovered, counting tables is the wrong measurement.

The script now also counts rows and fails when the schema comes back without them. The message
names the one case where that failure is expected — deliberately drilling a backup of an empty
database — so an operator is not left guessing.

Verified as a real check rather than a decorative one: an archive containing a table and no rows
(1 `TABLE DATA` entry, full schema restored) now **fails**, where before it reported OK.

## What was proven

| Case | Result |
| --- | --- |
| Synthesized archive (default, no argument) | OK — 1 table, 1 row |
| Real AURA backup, `pg_dump --format=custom --compress=6` as `backup.sh` produces | OK — 9 tables, 2 rows |
| Archive with tables but no data | **FAIL**, correctly |

The real backup was produced the way the deployment produces one: a Postgres instance migrated
with `alembic upgrade head` to revision `0003`, given a row in `users`, dumped in `backup.sh`'s
exact format, then restored through the drill's `$DUMP` path — which the synthesized run never
exercises, since it never calls `docker cp`.

Restoring it brings back all nine tables with their indexes and every foreign key constraint.
Data integrity was confirmed independently of the script's own assertion: the `users` row returns
with its `email` and `display_name` intact.

One detail worth recording: the restored database reports `alembic_version = 0003`. A gateway
pointed at a restored backup therefore satisfies the schema readiness probe added for A1's
Finding 1, rather than being quarantined by it. The two phases agree.

## Second pass — the `backup` service itself

Run the same day, after the above. The compose `backup` service had also never started. Unlike the
compose file and the drill, **it works exactly as written**: no defects found in any path
exercised.

One cycle against a seeded data tier produced all three artifacts and verified its own Postgres
dump before declaring success:

```
[backup] postgres ok: 9 tables, 20.0K
[backup] qdrant snapshot copied: drill_probe (drill_probe-…-2026-08-23-07-10-18.snapshot)
[backup] redis ok: 4.0K
[backup] pruning backups older than 14d
[backup] cycle complete
```

It also writes the Prometheus textfile the monitoring stack alerts on —
`aura_backup_last_success_timestamp_seconds` advanced, and `aura_backup_offsite_configured 0`
correctly reflected an unset `BACKUP_S3_URI`.

### All three stores restore from that output

Postgres was drilled through `restore-drill.sh` (9 `TABLE DATA` entries, 9 tables, 3 rows), and
the two stores the drill deliberately does not cover were verified by hand — which had never been
done:

| Store | Restored into | Result |
| --- | --- | --- |
| Postgres | throwaway `postgres:16-alpine` | 9 tables, 7 FK constraints, `alembic_version` 0003 |
| Qdrant | empty `qdrant` via `snapshots/recover` | collection back, point 1 with its payload |
| Redis | empty `redis:7-alpine` loading the RDB | `drill:token` = `abc123` |

Data integrity was checked independently of any script's own assertion. The `users` row returns
with its `email` and `display_name`; `tasks.title_enc` returns byte-identical, so the
application-layer ciphertext survives the round trip intact. The restored Qdrant vector is
L2-normalised relative to what was inserted, which is Qdrant storing a cosine-distance vector
rather than data loss.

### Retention pruning

The only destructive path in the script, so it was tested rather than assumed. Against a 14-day
retention with backdated artifacts: the three 20-day-old files and directories were deleted, and
the 5-day-old and current ones survived. Correct.

Worth knowing rather than fixing: the two `find` calls for dumps and RDBs pass `-print`, so
deletions appear in the log, while the `qdrant-*` directory prune does not. The directory *is*
removed — it simply does so silently.

## Still not covered

- **Offsite copy (`rclone` to S3).** Deliberately not exercised: `BACKUP_S3_URI` was left unset,
  because triggering it would ship data to external storage. The code path is untested.
- **`deploy/scripts/restore.sh`.** A separate script for restoring *into* a running stack, which,
  like `restore-drill.sh` was until today, has never been executed.
- **A full recovery rehearsal.** The three stores were restored in isolation, not as one coherent
  system with an application on top. The manual checklist in
  `docs/guides/maintenance-guide.md` still applies.

## Environment caveat

Building the backup image needed the same proxy-CA workaround as A1: Alpine's `apk` failed with
`TLS: server certificate not trusted` against the sandbox's intercepting proxy. Resolved by
trusting the CA in a locally retagged `postgres:16-alpine` base. No Dockerfile change, and TLS
verification was never disabled. This is a property of the sandbox, not of the repository.
