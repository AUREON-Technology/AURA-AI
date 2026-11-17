# Plan: Multi-provider offsite storage for AURA backups

Status: **Approved by the user on 2026-11-17. BUILT and TESTED locally; not proven.** Nothing has run against real buckets or on the VPS. See "Implementation status" at the end.
Module: `deploy/scripts/` (backup, fetch, verify), `deploy/docker-compose.prod.yml` (backup service env), `deploy/monitoring/alerts/aura.yml`, `docs/guides/maintenance-guide.md`.
Base: branch `anas/confident-gates-j2vj8w` at `0d6c53e`. Working tree was clean at Phase 0. PR: anasibrahimgnedy-collab/AURA-AI#252.

## Goal

Stop a single dropped server from losing AURA's data. Concretely: every nightly, age-encrypted backup set is copied to at least **two independent paid or ToS-compliant object stores**, each copy verified, so losing the VPS or one provider does not lose the data.

Explicitly not the goal: pooling many free-tier accounts for capacity. Storage capacity is not the constraint for this data volume; durability is.

## User decisions (do not re-ask)

1. Target: **offsite encrypted copies now** (option A). Live replication (option C) is **deferred** to a later phase.
2. Providers: only accounts the user owns and pays for or that are ToS-compliant. Available: **Backblaze B2, Cloudflare R2, Wasabi or AWS S3**. No multi-account quota pooling. No free-tier sharding of data.
3. Data scope: **all stores, age-encrypted** before leaving the host (Postgres, Redis, Qdrant). Same threat model as `backup-lib.sh`: providers never hold a decryption key.
4. Recovery target: **nightly, 24h max data loss.** Hourly or continuous is out of scope for now.
5. A second VPS exists that the user runs. It is the candidate target for the deferred replication phase. Not used in this plan.
6. (2026-11-17) Offsite providers: **Backblaze B2 and Cloudflare R2**, as rclone remotes `b2-aura` and `r2-aura` (`deploy/backup/rclone.conf.example`), each with a bucket-scoped key and a 6-day object lock.
7. (2026-11-17) Encryption keys: **two independent age key pairs**. Both public keys go in `backup_age_recipients.txt`, so either private key alone restores any set. The two private keys are kept offline in separate places. `AuraBackupSingleKey` fires while fewer than two distinct keys are configured.
8. (2026-11-17) The **live Railway data** is backed up by a **Railway cron service** running the same `backup.sh` in run-once mode (`deploy/backup/railway.Dockerfile`), to the same B2 + R2 and the same two keys. Runbook: `docs/deployment/backups.md`.
9. (2026-11-17) Production's vector index is on **Qdrant Cloud**; it is backed up through its snapshot API with `QDRANT_API_KEY`.

## Current state (from code)

- `backup.sh` writes age-encrypted, manifested, GFS-retained sets nightly and verifies them locally.
- Offsite is a single rclone remote, enabled only when `BACKUP_S3_URI` is set. `bk_offsite_push` copies, runs `rclone check`, then prunes the remote with `bk_offsite_prune`.
- The `AuraBackupOffsiteFailed` alert already covers a failed offsite copy, for the one remote.
- `test_backup_crypto.sh` tests the offsite path against a stub rclone.

Gap: **one remote, not two.** Offsite may also be unset in production; the plan must check that before anything else.

## Changes by layer

### Shell backup layer (`deploy/scripts/`)

- `backup-lib.sh`: accept a list of offsite targets (`BACKUP_OFFSITE_TARGETS`, one rclone remote per entry) and run push and verify for each. A set is "durable" only when **at least `BACKUP_OFFSITE_MIN_COPIES` (default 2)** remotes verify. Pruning runs per remote and never prunes the last verified copy of a set on any remote.
- `backup.sh`: loop over targets. Report per-remote status in the metrics file.
- Keep the existing single-`BACKUP_S3_URI` path working as a one-target list, so nothing regresses for a single-provider setup.
- `restore.sh` / `restore-drill.sh`: restore from whichever remote verifies first. Test that path explicitly.

### Compose (`deploy/docker-compose.prod.yml`, `docker-compose.dev.yml`)

- Add the new env vars to the backup service in **both** files with the same values (CLAUDE.md parity rule). Covered by `tests/unit/test_compose_security_parity.py`.
- Secrets: rclone remote configs come from `bootstrap-secrets.sh` (already has `rclone.conf`). Extend it to create one credential per provider. Never commit credentials.

### Monitoring (`deploy/monitoring/alerts/aura.yml`)

- Add a gauge per remote and an alert when **fewer than 2** remotes verified within the last 36 hours.

### Docs

- `docs/guides/maintenance-guide.md`: provider setup, bucket object-lock/versioning, key custody, restore steps.
- `docs/status/BACKEND_STATUS.md` if it tracks backup state.

## Protections the design must keep

- **Deletion defence:** each bucket needs object lock or versioning, because a compromised host could otherwise delete old sets. The backup-lib header already says this.
- **Key custody:** the age private key is needed for every restore and lives offline. Losing it loses every backup. The user must decide where the key is kept and whether a second copy exists. **Open item.**
- **Provider independence:** the two chosen providers should be in different companies. If the user picks B2 and R2 and both are on the same CDN path, the failure mode is still shared. Flag any shared dependency to the user.

## Rollout order

1. Confirm on the VPS whether `BACKUP_S3_URI` is set and whether the last nightly set reached offsite. This determines whether a single copy exists today.
2. Implement multi-target support behind the existing single-target default, so it deploys without a behaviour change.
3. Create two buckets in two providers, with object lock or versioning on.
4. Run one real backup with two targets. Verify both remotes with `rclone check`.
5. Run `restore-drill.sh` against a remote copy, restoring into a scratch database, and keep the report.

## Verification

- Automated: `deploy/scripts/tests/test_backup_crypto.sh` (extended with two stub remotes), then `pytest tests/unit/test_compose_security_parity.py` and the compose tests.
- Real: the rollout steps 3–5 above, which need provider credentials and VPS access. Nothing here is "proven" until the restore drill passes against a remote copy.

## Blockers and risks

- **Deploy VPS is blocked** by a changed host key at 169.58.188.43 (CLAUDE.md). The owner must confirm whether that host was reinstalled or intercepted. This plan does not update `deploy/known_hosts`. Until the owner decides, the VPS may not be deployable.
- **Railway target** (`aureon-ai.tech`) runs its own database and does not run `backup.sh`. Its backups are out of scope for this plan, and need a separate decision.
- **Cost:** storage for nightly encrypted sets across two providers is small, but egress on restore can be charged. Estimate needed from the user's actual DB size.
- **Plaintext locally:** `backup.sh` stages plaintext before encryption on the host. That is existing behaviour, not changed here.

## Out of scope

- Live replication (option C) and the second VPS. Deferred to a later plan.
- Railway's own backups.
- Free-tier or multi-account storage pooling.
- Hourly WAL shipping.

## Open items for the user (needed for rollout, not for the code)

1. ~~Age key custody~~ Two key pairs (decision 7). Still needed: the owner generates both off the VPS, stores the private keys apart, and installs both public keys.
2. ~~Which two providers?~~ B2 and R2 (decision 6). Still needed: both buckets and keys created per the maintenance guide.
3. ~~Is `BACKUP_S3_URI` set on the VPS?~~ **No** (owner, 2026-11-17). No offsite copy of any backup exists today, and there is no old single-target bucket to migrate.

## Finding (2026-11-17): the live data is not covered

Production (`aureon-ai.tech`) runs on Railway: Railway-managed Postgres and Redis, and a Qdrant outside Railway (`docs/deployment/control-plane.md`). The backup sidecar this plan extends runs only in the VPS compose stack, and the VPS is in an unknown state: Deploy VPS has failed since its host key changed on 2026-10-18 (`docs/status/BACKEND_STATUS.md`). So **nothing in this repo backs up the live Railway data**, and the multi-provider work above protects the stack that is not serving users. Resolved by decisions 8 and 9: a Railway cron service backs up the live data (see the implementation status below).
4. ~~Approve this plan~~ Approved 2026-11-17.

## Implementation status (2026-11-17)

Built on PR anasibrahimgnedy-collab/AURA-AI#252:

- `backup.sh`: `BACKUP_OFFSITE_TARGETS` (falls back to `BACKUP_S3_URI`); per-target upload, verify
  and retention; durable only at `BACKUP_OFFSITE_MIN_COPIES` verified copies (default 2). Below
  that the cycle reports failure and `.last_offsite_success` does not advance.
- Configuration is checked before any upload. These are refused: two targets on one rclone remote
  (not independent copies), a duplicated target, a glob character, and a non-integer or zero
  minimum. The local set is still taken.
- `backup-fetch.sh` (new): fetches the newest set held by any target, from the first target whose
  copy verifies keyless; feeds `restore.sh` / `restore-drill.sh`.
- Metrics `aura_backup_offsite_copies_verified` / `_required`. Alerts `AuraBackupUnderReplicated`
  (last cycle) and `AuraBackupOffsiteStale` (36 hours without a durable set).
- Tests: `deploy/scripts/tests/test_backup_crypto.sh`, 90 assertions, stub rclone. shellcheck
  `-s sh` clean. `backup-fetch.sh` added to Backend CI's shellcheck list.

Deviations from the plan above, and why:

- **Compose:** only `docker-compose.prod.yml` changed. The dev stack has no backup service, so
  there is nothing to keep in parity.
- **Secrets:** `bootstrap-secrets.sh` is unchanged. All provider credentials live in the one
  `secrets/backup/rclone.conf` it already moves into place, one named remote per provider.
- **Restore:** `restore.sh` and `restore-drill.sh` are unchanged. `backup-fetch.sh` picks and
  verifies the remote copy, then hands a local set to them, which keeps decryption in one place.
- **Behaviour change:** with the default minimum of 2, an existing single-target setup now
  reports offsite failure and fires both alerts until a second target is added. This is
  intended: one provider is not durable.

Railway cron service (decisions 8-9), added the same day:

- `backup.sh` run-once mode (`BACKUP_RUN_ONCE=1`): one cycle, exit 0 only when the set is durable. Inputs from env for hosts with no secret files: `BACKUP_DATABASE_URL`, `BACKUP_REDIS_URL`, `QDRANT_API_KEY`, `BACKUP_RCLONE_CONFIG`. `BACKUP_HEARTBEAT_URL` is pinged only on a durable run (dead man's switch, since nothing scrapes metrics on Railway).
- `deploy/backup/railway.Dockerfile`: the VPS image's packages with the scripts copied in; `PG_MAJOR` build argument, default **18**: Railway's Postgres is 18.6 (owner, 2026-11-17), and `pg_dump` refuses a newer server. `restore-drill.sh` moved to `postgres:18-alpine` (`DRILL_PG_IMAGE`) because a 16 drill cannot read pg_dump 18 archives; 18 still reads the VPS's 16 dumps. Both verified with real containers; `deploy/.dockerignore` allows only `scripts/` into its build context.
- Two pre-existing bugs fixed in `backup_qdrant`, affecting the VPS too: a greedy `sed` kept only the LAST collection name, so all other collections were silently skipped; and snapshots were never deleted from Qdrant after download, so they accumulated on its disk nightly.
- Proven locally end to end with the built image (stock Postgres 16, Redis 7, Qdrant v1.12.4, local folders as remotes): both copies verified; fetched from one remote; restored with key B alone into a throwaway Postgres; a read-only remote gave exit 1 and no heartbeat. Stub tests for the same paths are in the shell suite (118 assertions).

Not proven until the rollout steps run: a real cycle to two real providers, and a restore drill
from each provider's copy (maintenance-guide "Quarterly"). CI on the PR shows red because the
runners return no logs (CB-44); the checks above were run locally with the CI commands.
