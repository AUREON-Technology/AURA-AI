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

## Not covered

The script says so itself, and it remains true:

- **Qdrant snapshots and the Redis RDB are not applied.** Different engines; the drill is Postgres
  only. A full recovery rehearsal still needs the manual checklist in
  `docs/guides/maintenance-guide.md`.
- **No backup produced by the running `backup` service was tested.** The archive here was
  generated with the same `pg_dump` invocation `backup.sh` uses, but the compose `backup` service
  itself has still never run — it was among the services A1 could not start.
- **Restore-into-production was not rehearsed.** `deploy/scripts/restore.sh` is a separate script
  and, like this one was until today, has never been executed.

## Recommended next

Run the compose `backup` service once and drill its actual output. That closes the last gap
between "the drill works" and "our backups restore", and it is now cheap: the gateway image that
blocked A1 from starting the auxiliary services is 2.34 GB rather than 8.88 GB.
