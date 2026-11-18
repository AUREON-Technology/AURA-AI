# Backing up the live Railway data

Production (`aureon-ai.tech`) keeps its data in Railway's Postgres and Redis and in Qdrant Cloud.
The VPS backup sidecar (`deploy/docker-compose.prod.yml` `backup:`) never reaches any of them. This
page sets up a **Railway cron service** that backs them up every night with the same `backup.sh`:

1. dump Postgres, Redis and every Qdrant collection;
2. encrypt each artifact to **both** age public keys, so either private key alone can restore;
3. copy the set to **Backblaze B2 and Cloudflare R2** and verify each copy;
4. exit 0 only when both copies verified, then ping a heartbeat URL.

Plan and decisions: `docs/plans/distributed_storage_plan.md` (decisions 6-9). Keys and providers:
`docs/guides/maintenance-guide.md`, sections "Backup encryption keys" and "Offsite providers".

## Status

**Built and tested locally, not run on Railway.** On 2026-11-17 the image built from
`deploy/backup/railway.Dockerfile` ran in run-once mode against stock Postgres 16, Redis 7 and
Qdrant v1.12.4 (with an API key), with two local folders standing in for B2 and R2:

- both collections snapshotted, then deleted from Qdrant;
- the set copied to and verified at both remotes; exit 0 and one heartbeat;
- `backup-fetch.sh` fetched the set from the R2 copy alone;
- `restore-drill.sh` with **key B alone** restored the table and both of its rows;
- with one remote read-only: exit 1, both errors logged, no heartbeat.
- re-run against **Postgres 18.7** (production reports 18.6) with the image at its default
  `PG_MAJOR=18`: dumped, both copies verified, and drilled with key B on `postgres:18-alpine`
  (both rows back). The old 16 drill failed on that set, which is why the drill now uses 18.

Still unproven, because it needs the real services: Railway's Postgres and Redis, Qdrant Cloud,
and real B2/R2 buckets.

## 1. Create the service

In the Railway project that runs production, add a service from this GitHub repository and set:

| Setting (dashboard) | Value |
|---|---|
| Root Directory | `deploy` |
| Variable `RAILWAY_DOCKERFILE_PATH` | `backup/railway.Dockerfile` |
| Cron Schedule (Settings → Deploy) | `17 3 * * *` (03:17 UTC daily; any quiet hour works) |
| Restart Policy | Never (a failed run waits for the next schedule) |
| Healthcheck, public domain | none: the service serves nothing |

Set these in the dashboard. A service reads a config file from the repository only when one is
linked to it, and none is for this one; the existing backend service ignores its `railway.json` for
the same reason (CLAUDE.md).

## 2. Variables

Set these on the backup service. Use Railway's **sealed** variables for every secret. The
`${{Postgres...}}` and `${{Redis...}}` references use the services' names in your project; adjust
them if yours are named differently.

| Variable | Value | Secret |
|---|---|---|
| `PG_MAJOR` | Leave unset: the image defaults to `18`, matching production (`18.6`, owner, 2026-11-17). Must be ≥ the server's major version, because `pg_dump` refuses a newer server; set it **before** Railway's Postgres is upgraded to 19. Check with `SHOW server_version;` | no |
| `BACKUP_DATABASE_URL` | `${{Postgres.DATABASE_URL}}` (the private URL) | yes |
| `BACKUP_REDIS_URL` | `${{Redis.REDIS_URL}}` | yes |
| `QDRANT_URL` | the Qdrant Cloud cluster URL, e.g. `https://xyz.cloud.qdrant.io:6333` (same as the app's `AURA_QDRANT_URL`) | no |
| `QDRANT_API_KEY` | a Qdrant Cloud API key for that cluster | yes |
| `BACKUP_AGE_RECIPIENT` | both **public** keys, comma-separated: `age1…A,age1…B`. Never a private key; the script refuses one | no |
| `BACKUP_OFFSITE_TARGETS` | `b2-aura:<b2-bucket> r2-aura:<r2-bucket>` | no |
| `BACKUP_RCLONE_CONFIG` | the whole filled-in `deploy/backup/rclone.conf.example`, pasted as one multi-line value | yes |
| `BACKUP_HEARTBEAT_URL` | the check URL from step 3 | yes |

Leave `BACKUP_OFFSITE_MIN_COPIES` unset (2). `PG_MAJOR` is read at **build** time; change it and
redeploy the service.

## 3. Monitoring: a heartbeat, because Prometheus does not watch Railway

The VPS alerts (`AuraBackupUnderReplicated`, `AuraBackupOffsiteStale`, `AuraBackupSingleKey`) read
metrics that nothing scrapes on Railway. Instead, a successful, **durable** run requests
`BACKUP_HEARTBEAT_URL`, and a failed run never does. Create a check in a dead man's switch service
(for example Healthchecks.io or Better Stack) with:

- period **1 day**, grace **6 hours**;
- an alert route to a human.

The check then fires for every failure mode, including the cron service never starting. Railway's
own run history also shows each run's exit status.

## 4. First run

Trigger one run from the dashboard instead of waiting for the schedule, then check its log for:

```text
[backup] qdrant snapshot copied: <every collection>
[backup] offsite: 2 of 2 target(s) verified (required 2)
[backup] cycle complete
[backup] heartbeat sent
```

A `WARNING: this set is decryptable by only ONE age key` line means `BACKUP_AGE_RECIPIENT` holds
one key, not two.

## 5. Restore drill (do this once per key, then quarterly)

On any machine with Docker and rclone, with the filled-in rclone config:

```bash
fetched=$(RCLONE_CONFIG_FILE=/path/to/rclone.conf \
  BACKUP_OFFSITE_TARGETS="r2-aura:<r2-bucket>" \
  ./deploy/scripts/backup-fetch.sh /tmp/aura-restore)
BACKUP_AGE_IDENTITY_FILE=/path/to/aura-backup-B.agekey ./deploy/scripts/restore-drill.sh "$fetched"
```

Repeat with the B2 target and key A. `restore-drill.sh` restores into a throwaway Postgres
(`postgres:18-alpine` by default, override with `DRILL_PG_IMAGE`), never into production. Its
Postgres must be at least as new as the dump's `pg_dump`.

## 6. Real restore into Railway

This is destructive, so practise with the drill first. Railway's Postgres service exposes `PGHOST`, `PGPORT`,
`PGUSER`, `PGPASSWORD` and `PGDATABASE`. With those set to the target database (its public
proxy values, when restoring from outside Railway), run `restore.sh` on the fetched set with one
private key, from a Postgres **18** client (the backup image built from `railway.Dockerfile` is
one). An older client cannot read the archive: `pg_restore: error: unsupported version (1.16) in file header`.

```bash
BACKUP_AGE_IDENTITY_FILE=/path/to/aura-backup-A.agekey ./deploy/scripts/restore.sh "$fetched"
```

It verifies and decrypts the whole set before it touches the database, and asks you to type the
database name. Redis and Qdrant are restored by hand from the decrypted set
(`backup-open.sh`): load `redis-*.rdb` into a Redis, and upload each
`qdrant-<stamp>/<collection>.snapshot` through Qdrant's snapshot-recovery API.

## Known limits

- **Qdrant Cloud with several nodes.** A collection snapshot is taken by the node that answers.
  On a single-node cluster, including the free tier, it covers the whole collection. On a
  multi-node cluster, check Qdrant's documentation on snapshots in distributed mode before
  relying on it. Retrieval resolves text from Postgres, so the vectors can also be rebuilt by
  re-indexing.
- **Redis.** `redis-cli --rdb` needs the server to allow replication commands (`SYNC`). Stock
  Redis does; confirm Railway's on the first run. Redis holds refresh tokens and caches, so a
  Redis-less restore means users sign in again (`backup.sh` keeps the rest of the set if this
  step fails).
- **One region.** The cron service runs in Railway; if Railway itself is down, nothing is taken
  that night. The heartbeat check reports it.
