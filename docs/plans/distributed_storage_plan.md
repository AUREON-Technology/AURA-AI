# Plan: Multi-provider offsite storage for AURA backups

Status: **DRAFT, awaiting approval.** No code written.
Module: `deploy/scripts/` (backup, restore, verify), `deploy/docker-compose.{prod,dev}.yml` (backup service env), `deploy/monitoring/alerts/aura.yml`, `docs/guides/maintenance-guide.md`.
Base: branch `anas/confident-gates-j2vj8w` at `0d6c53e`. Working tree was clean at Phase 0.

## Goal

Stop a single dropped server from losing AURA's data. Concretely: every nightly, age-encrypted backup set is copied to at least **two independent paid or ToS-compliant object stores**, each copy verified, so losing the VPS or one provider does not lose the data.

Explicitly not the goal: pooling many free-tier accounts for capacity. Storage capacity is not the constraint for this data volume; durability is.

## User decisions (do not re-ask)

1. Target: **offsite encrypted copies now** (option A). Live replication (option C) is **deferred** to a later phase.
2. Providers: only accounts the user owns and pays for or that are ToS-compliant. Available: **Backblaze B2, Cloudflare R2, Wasabi or AWS S3**. No multi-account quota pooling. No free-tier sharding of data.
3. Data scope: **all stores, age-encrypted** before leaving the host (Postgres, Redis, Qdrant). Same threat model as `backup-lib.sh`: providers never hold a decryption key.
4. Recovery target: **nightly, 24h max data loss.** Hourly or continuous is out of scope for now.
5. A second VPS exists that the user runs. It is the candidate target for the deferred replication phase. Not used in this plan.

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

## Open items for the user before build

1. Where is the age private key kept, and is there a second copy? **(needed before any restore can be trusted)**
2. Which two of B2, R2, Wasabi or AWS S3 do you want as the first pair? Are both accounts ready?
3. Is `BACKUP_S3_URI` set on the VPS right now? (Needed for rollout step 1.)
4. Approve this plan, or say what to change.
