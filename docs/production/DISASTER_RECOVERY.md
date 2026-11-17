# Disaster Recovery

What AURA can recover from today, what has actually been proven, and what the plan needs before
recovery can be called production-grade. Part of
[MASTER_PRODUCTION_ROADMAP.md](MASTER_PRODUCTION_ROADMAP.md) Phase 20. Verified against tree
`11abb50` on 2026-09-24. The operator procedure lives in
[`docs/guides/maintenance-guide.md`](../guides/maintenance-guide.md) ("Practise a restore"). Note
that it still shows `/opt/aura/...` paths, while the VPS checkout is at `/opt/aura-stack`
(GAP-039).

## 1. What exists

| Component | What it does | Source |
| --- | --- | --- |
| `backup` service | Long-lived container (`postgres:16-alpine` + rclone/wget/redis-cli). Every `BACKUP_INTERVAL_SECONDS` (default 86400) it takes a Postgres custom dump (`pg_dump --format=custom --compress=6`), **verifies** it with `pg_restore --list` plus a TABLE DATA count, copies a Qdrant snapshot file per collection, and takes a Redis RDB over the wire. It prunes after `BACKUP_RETENTION_DAYS` (default 14) and writes Prometheus textfile metrics | `deploy/scripts/backup.sh:1-140`, `deploy/docker-compose.prod.yml:920-955`, `deploy/backup/Dockerfile` |
| Offsite copy | After a successful local cycle, `rclone` copies `$BACKUP_DIR` to `BACKUP_S3_URI` when that's set, with separate offsite retention | `backup.sh` `copy_offsite`, compose env `BACKUP_S3_*` |
| `restore.sh` | Destructive Postgres restore (`pg_restore --clean --if-exists --jobs=2`). It requires the typed DB name or `RESTORE_CONFIRM=$PGDATABASE`, and reminds the operator to `POST /api/v3/admin/index/rebuild` afterwards (documents are the source of truth, ADR-007). It must be invoked with `--entrypoint /bin/sh`, or it silently runs another backup | `deploy/scripts/restore.sh` |
| `restore-drill.sh` | Throwaway `postgres:16-alpine`. Refuses to run if `PGHOST`/`PGDATABASE`/`PGUSER` is set. Restores and asserts both tables **and rows** | `deploy/scripts/restore-drill.sh` |
| `pre-migration-backup.sh` | Backup before migrations | `deploy/scripts/pre-migration-backup.sh` |
| `backup-fetch.sh` | Pulls a set back from offsite: the newest set held by any `BACKUP_OFFSITE_TARGETS` remote, from the first provider whose copy verifies keyless. Built and stub-tested (#252), not yet run against a real provider | `deploy/scripts/backup-fetch.sh` |
| Alerts | `AuraBackupStale`, `AuraBackupOffsiteFailed`, `AuraBackupUnderReplicated` (last set on fewer than `BACKUP_OFFSITE_MIN_COPIES` providers), `AuraBackupOffsiteStale` (no durable offsite set in 36 h), `AuraBackupSingleKey` (fewer than two age keys can decrypt new sets) on `aura_backup_last_success_timestamp_seconds`, `aura_backup_offsite_*` | `deploy/monitoring/alerts/aura.yml` |
| Host rebuild | Runbooks for moving or provisioning a host | `docs/guides/host-migration-runbook.md`, `provisioning-runbook.md` |

## 2. What is proven

Phase A2 ([phase-a2-restore-drill.md](../reports/phase-a2-restore-drill.md), run 2026-08-23
against `aa43ed9`) is the only recovery evidence:

- **Where:** a disposable Linux container with Docker. The report says, verbatim: *"No VPS or
  production data was involved."*
- The drill failed on its first-ever run (a missing `-U drill`) and was fixed. Its assertion was
  tightened from table count to row count.
- The `backup` service ran one cycle against a seeded data tier and produced all three artefacts.
- **All three stores restored from that output:** Postgres (9 tables, 7 FKs, `alembic_version`
  0003, `users` row intact, `tasks.title_enc` ciphertext byte-identical), Qdrant (collection and
  point payload back via `snapshots/recover`), and Redis (key restored from the RDB).
- Retention pruning was tested rather than assumed.

## 3. What is not proven

| Item | State | Gap |
| --- | --- | --- |
| Offsite `rclone` copy | Never exercised (roadmap A2). Whether `BACKUP_S3_URI` is set on the VPS is unknown | GAP-028 |
| `restore.sh` | Never exercised against a real target (roadmap A2) | GAP-028 |
| Drill on the production VPS / production-sized data | Never. A2 used seeded data in a sandbox | GAP-028 |
| Backup encryption | **None.** Dumps, snapshots and RDBs are plaintext locally and when copied offsite. Memory, documents and v3 personal-OS content are plaintext in Postgres (GAP-016) | GAP-016 |
| Qdrant rebuild vs snapshot | A2 restored a snapshot. The ADR-007 path (rebuild the index from Postgres documents) was never timed | GAP-028 |
| Host loss (VPS destroyed) | No rebuild drill. Backups default to `./backups` on the **same VPS** unless offsite is configured, so losing the host loses the backups with it | GAP-028, GAP-029 |
| Ollama model volume, secrets, `.env` | Not in `backup.sh`. Models can be re-pulled (by tag only, GAP-030). Secrets and `.env` recovery rely on the operator | GAP-028 |
| Measured RPO / RTO | **Never measured** | GAP-028 |

## 4. Proposed objectives (unmeasured targets)

These are **proposals**. Nothing here has been measured, and none of them may be quoted as a
capability until a drill report shows it.

| Scenario | Proposed RPO | Proposed RTO | Current mechanism | Measured? |
| --- | --- | --- | --- | --- |
| Gateway/worker container crash | 0 | ≤ 5 min | `restart: unless-stopped` | NOT MEASURED |
| Postgres data corruption / bad migration | ≤ 24 h (daily dump). Target ≤ 1 h after WAL archiving | ≤ 1 h | `restore.sh` + index rebuild | NOT MEASURED |
| Qdrant loss | 0 for content (rebuild from Postgres). ≤ 24 h via snapshot | ≤ 2 h (corpus-size dependent) | snapshot recover or `/admin/index/rebuild` | NOT MEASURED |
| Redis loss | Sessions and rate-limit state are acceptable to lose. Refresh tokens are lost, so users re-login | ≤ 15 min | RDB restore or start empty | NOT MEASURED |
| Full VPS loss | ≤ 24 h (only if offsite is configured, otherwise **total loss**) | ≤ 8 h rebuild onto a fresh host | host-migration runbook + offsite restore | NOT MEASURED |
| GPU inference host loss (target topology) | N/A (stateless, weights pinned and mirrored) | Degrade to CPU Ollama immediately. Replacement ≤ 24 h | router failover | NOT BUILT |

## 5. Work to close (Phase 20 exit criteria)

1. Configure and verify the offsite copy, with **client-side encryption** (rclone `crypt` or
   age/gpg) and key custody off the VPS.
2. A quarterly drill that restores the **offsite** copy onto a **fresh host**: Postgres via
   `restore.sh`, Qdrant via snapshot *and* via index rebuild, and Redis. Record wall-clock time per
   step, to give the measured RTO and RPO. The report goes in `docs/reports/`.
3. Add WAL archiving (or more frequent dumps) if the 24 h RPO is unacceptable.
4. Include the `.env`, secret material (sealed) and the pinned model digests in the recovery kit.
5. Page on `AuraBackupStale` / `AuraBackupOffsiteFailed` / `AuraBackupUnderReplicated` /
   `AuraBackupOffsiteStale`. Confirm Alertmanager routes to a human.
