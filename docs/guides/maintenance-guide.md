# Maintenance Guide

Keeping a running deployment healthy: what to check, on what cadence, and what to do when a
check fails. The [Administrator Guide](administrator-guide.md) covers day-to-day operations;
this covers the recurring work that keeps those operations from becoming incidents.

---

## Daily

### The one check that matters most

```bash
curl -s http://localhost:8000/metrics | grep aura_insecure_mode | grep -v ' 0$'
```

Empty output is correct. Anything else means debug mode, mock authentication, disabled safety, or
disabled rate limiting is live. Settings validation should have prevented it; the metric exists
because SEC-05 was invisible partly because nothing reported it. `AuraInsecureModeActive` pages
on this.

### Backups actually completed

```bash
cat /opt/aura-stack/deploy/backups/.last_success | xargs -I{} date -d @{}
```

The backup service **verifies each dump is restorable and non-empty before counting it**, then
seals the cycle into an encrypted, checksummed set (`backups/aura-<stamp>/`) and verifies that too.
A timestamp older than ~26 hours means the last run failed verification, not merely that it did
not run. `AuraBackupStale` pages on that; `AuraBackupMisconfigured` pages sooner when the sidecar
is refusing to run at all (usually: encryption on, no recipient key configured). If
`BACKUP_S3_URI` is set, also confirm `AuraBackupOffsiteFailed` is not firing -- it now covers the
post-upload remote verification, not just the copy. The failure that matters is discovering at
recovery time that months of dumps were empty.

### The nightly refresh reported success

```bash
curl -s -H "$AUTH" $AURA/api/v3/admin/status | jq '.learning'
```

A blocked promotion is **not** an incident — the previous index is still serving. It is a ticket:
read `gate_failures` and decide whether the gate was right. `AuraKnowledgeStale` fires only after
48 hours without a successful promotion, which is the point at which "the index is a bit old"
becomes "the index is wrong".

### Scan the Security dashboard

Two panels are worth a daily glance:

- `stage="context"` guard verdicts — a rising quarantine rate means a document in the knowledge
  base contains instruction-shaped content. Review recent ingestions.
- `guard="secret"` blocks — either an attack or a real credential leaking into answers.
  Investigate before tuning the threshold.

---

## Weekly

**Retrieval quality.** `AuraRetrievalReturningNothing` fires above a 15% empty rate. Below the
threshold, a rising trend is still worth noticing: it usually means the corpus has drifted away
from what users ask about, not that retrieval broke.

**Cost.** The Overview dashboard's cost panel, broken down by provider. A sudden change usually
means routing shifted — check whether a local provider's circuit is open, which silently moves
traffic to a paid one.

**Circuit breakers.** Any provider showing `circuit_opens > 0` over the week had an outage that
may have gone unnoticed because failover worked.

```bash
curl -s -H "$AUTH" $AURA/api/v3/admin/status | jq '.providers[] | {provider, state, failure_rate, circuit_opens}'
```

**Disk.** Model weights (5–15 GB) and Prometheus (~5 GB at 30 days) dominate.

```bash
df -h /var/lib/docker && du -sh /opt/aura-stack/deploy/backups
```

---

## Monthly

### Practise a restore

**A restore procedure that has never been run is not a recovery plan.**

First, run the throwaway drill (never against the compose `postgres` service):

```bash
cd /opt/aura-stack
./deploy/scripts/restore-drill.sh
# or, against a real backup set (still throwaway Postgres, not prod). Needs `age` on the host and
# the offline private key, brought in for the drill only:
BACKUP_AGE_IDENTITY_FILE=/path/to/aura-backup.agekey \
DRILL_RECORD=/opt/aura-stack/deploy/backups/drills.tsv \
  ./deploy/scripts/restore-drill.sh /opt/aura-stack/deploy/backups/aura-<stamp>
```

For a set, the drill first verifies `CHECKSUMS.sha256`, decrypts every artifact and checks it
against the set's encrypted manifest (Qdrant and Redis included), then starts an ephemeral Postgres
container, `pg_restore --list`, restores into an empty database, and asserts tables **and rows**.
It prints how long verify+decrypt, `pg_restore` and the whole drill took, and with `DRILL_RECORD`
appends a tab-separated line (time, source, encryption, bytes, verify_s, restore_s, total_s,
tables, rows, OK/FAIL) -- that file is the RTO evidence; keep it. It refuses to run if `PGHOST` /
`PGDATABASE` are set. Legacy pre-encryption `postgres-<stamp>.dump` files are still accepted.

Then, on a **staging** copy of the data (not production), restore for real. Non-interactive
confirm is allowed only when the env value **equals** the target database name:

```bash
docker compose -f docker-compose.prod.yml stop gateway worker-realtime worker-ingestion beat
RESTORE_CONFIRM=aura docker compose -f docker-compose.prod.yml run --rm \
  -v /path/to/aura-backup.agekey:/run/restore-key/age.key:ro \
  -e BACKUP_AGE_IDENTITY_FILE=/run/restore-key/age.key \
  -e RESTORE_CONFIRM=aura --entrypoint /bin/sh backup \
  /scripts/restore.sh /backups/aura-<stamp>
docker compose -f docker-compose.prod.yml start gateway worker-realtime worker-ingestion beat
curl -X POST -H "$AUTH" $AURA/api/v3/admin/index/rebuild
```

`--entrypoint /bin/sh` is required. The `backup` service's own compose entrypoint is
`/scripts/backup.sh`; `docker compose run` overrides the *command*, not the *entrypoint*, so
without this override the invocation above silently runs `/scripts/backup.sh /scripts/restore.sh
/backups/postgres-<stamp>.dump` — another backup cycle, with restore.sh's path passed to it as
an ignored extra argument. No error is printed either way, so this is easy to miss mid-incident.

The final step matters: documents are the source of truth and the vector index is *derived*
(ADR-007), so a restored database needs its index rebuilt. Skipping it leaves an index describing
data that no longer exists.

**Qdrant / Redis (manual — not applied by restore-drill.sh or restore.sh):**

Each set holds `qdrant-<stamp>/<collection>.snapshot.age` and `redis-<stamp>.rdb.age`. Decrypt
them (verified against the manifest) into an empty directory, restore, then delete the plaintext:

```bash
BACKUP_AGE_IDENTITY_FILE=/path/to/aura-backup.agekey \
  ./deploy/scripts/backup-open.sh deploy/backups/aura-<stamp> /root/restore-plain
```

On the VPS tick:

- [ ] Latest set verifies: `./deploy/scripts/backup-open.sh --verify deploy/backups/aura-<stamp>`
      (no key needed); the drill above restored its Postgres dump with rows.
- [ ] The set's `qdrant-<stamp>/` directory contains `.snapshot.age` files (recover via Qdrant
      snapshot recover API, then rebuild the index if documents were the source of truth).
- [ ] The set contains `redis-<stamp>.rdb.age`. Restoring a stale RDB means users **re-login** (refresh
      tokens live in Redis). Prefer a fresh login over a days-old RDB if unsure.

### Send a test alert

After setting `ALERTMANAGER_WEBHOOK_URL` in `deploy/.env` (or a gitignored secrets file) and
recreating Alertmanager, confirm a human is paged:

```bash
docker compose -f docker-compose.prod.yml exec alertmanager amtool config routes
# Fire a synthetic alert (must receive it on the webhook / inbox):
docker compose -f docker-compose.prod.yml exec alertmanager \
  amtool alert add alertname=AuraManualTest severity=critical
```

Empty `ALERTMANAGER_WEBHOOK_URL` is silent on purpose. Do not point it at a dummy URL that 404s.

### Review pending approvals

```python
pending = await approvals.pending()
```

Approvals expire after 14 days. An accumulating queue of expired requests usually means the
review step has no owner — worth fixing before someone works around it.

### Memory consolidation

Runs on a schedule, but check that it is doing something:

```bash
curl -s http://localhost:8000/metrics | grep aura_memory_consolidation_total
```

All-zero counters over a month mean either nobody is using memory or the job is not running.
Both are worth knowing. Per-user quota is 20,000 records; exceeding it raises rather than
silently evicting, so a `MemoryStoreError` in the logs is a real signal.

### Prune old indexes

Retained for `AURA_LEARNING_INDEX_RETENTION_DAYS` (default 7). Do not set this to 0 unless disk is
genuinely scarce — retention is what makes rollback a thirty-second operation rather than a
rebuild.

---

## Quarterly

**Pull one offsite set back and verify it** (from each offsite target). The nightly job already
`rclone check`s each upload; this proves every copy is restorable from the far side.
`backup-fetch.sh` downloads a set, checks it against its `CHECKSUMS.sha256` (no key needed) and
prints its path. Give it one target at a time, so each provider's copy is drilled:

```bash
export RCLONE_CONFIG_FILE=deploy/secrets/backup/rclone.conf
for target in $BACKUP_OFFSITE_TARGETS; do
  fetched=$(BACKUP_OFFSITE_TARGETS="$target" ./deploy/scripts/backup-fetch.sh /root/offsite-check) || break
  BACKUP_AGE_IDENTITY_FILE=/path/to/aura-backup.agekey DRILL_RECORD=deploy/backups/drills.tsv \
    ./deploy/scripts/restore-drill.sh "$fetched"
  rm -rf /root/offsite-check
done
```

**Restoring after losing the VPS.** Run `backup-fetch.sh` with every target listed. With no set
name it takes the newest set held by any provider, and it skips a copy that is missing, partial
or fails verification and tries the next provider. Pass a set name (`aura-<stamp>`) to fetch an
older one. Then restore the printed path with `restore.sh`, as for a local set.

### Offsite providers (multi-target)

A set is durable only when `BACKUP_OFFSITE_MIN_COPIES` targets (default 2) verify it. Anything
below that fails the cycle and fires `AuraBackupUnderReplicated`, because a single provider
holding the only copy is one outage from data loss.

This section sets up the VPS sidecar. The **live** data on Railway is backed up by a separate
Railway cron service using the same providers and keys: [deployment/backups.md](../deployment/backups.md).

The chosen pair is **Backblaze B2** and **Cloudflare R2** (two companies, so neither outage nor
account loss takes both copies). Any other S3-compatible provider works the same way.

1. **Backblaze B2.** Create a private bucket with **Object Lock** enabled, and set a default
   retention of **6 days**. Then create an application key restricted to that bucket alone, with
   read and write. Keep its keyID and applicationKey for step 3.
2. **Cloudflare R2.** Create a bucket and add a **bucket lock** rule of **6 days** for all
   objects. Then create an R2 API token with *Object Read & Write*, scoped to that bucket. Keep its
   access key ID, secret, and your Cloudflare account ID (the endpoint is
   `https://<account-id>.r2.cloudflarestorage.com`).
3. Copy `deploy/backup/rclone.conf.example` to `deploy/secrets/backup/rclone.conf`, run
   `chmod 600` on it, and fill in the placeholders. It defines the remotes `b2-aura` and
   `r2-aura`. Credentials stay in that file, never in the image or the compose file. Check that it
   parses: `rclone --config deploy/secrets/backup/rclone.conf listremotes`.
4. Set, in the environment that runs compose:
   ```bash
   BACKUP_OFFSITE_TARGETS="b2-aura:<b2-bucket> r2-aura:<r2-bucket>"
   BACKUP_OFFSITE_MIN_COPIES=2
   ```
   `BACKUP_OFFSITE_TARGETS` is a space-separated list. Several targets require `rclone.conf`;
   one target may still use `BACKUP_S3_URI` with the S3 key files.

**Why 6 days.** The lock is what stops a compromised VPS from deleting the offsite copies, since
the VPS holds keys that can delete. It must be shorter than the daily retention
(`BACKUP_OFFSITE_KEEP_DAILY`, 7 by default; the compose file sets
`BACKUP_OFFSITE_RETENTION_DAYS=14`). Otherwise pruning tries to delete sets that are still
locked. That is harmless, because the cycle logs `could not remove remote` and retries the next
night, but those sets keep being billed until the lock expires. Lengthening the lock means
lengthening the daily retention with it.

**Two settings in the example matter.** `no_check_bucket = true` on R2: a bucket-scoped token
cannot check or create buckets, so without it every upload fails. `hard_delete = true` on B2:
otherwise a pruned set is only hidden, and is stored and billed indefinitely. Do not add
`disable_checksum`. rclone stores an MD5 (R2) or SHA1 (B2) of each large upload, which is what lets
the nightly `rclone check` compare hashes rather than sizes. For a full re-read of every object
instead, set `BACKUP_OFFSITE_VERIFY=download`. R2 charges no egress; B2 egress is free only up to
an allowance.
4. Confirm both copies after the first cycle: `aura_backup_offsite_copies_verified` should equal
   `aura_backup_offsite_copies_required`.

Before uploading anything, the cycle refuses a configuration that could miscount copies: two
targets on the same rclone remote, the same target listed twice, a glob character in a target,
or a `BACKUP_OFFSITE_MIN_COPIES` that is not a whole number of at least 1. The local set is still
taken; offsite is reported as failed. Two alerts watch the result: `AuraBackupUnderReplicated`
(the last set reached too few providers) and `AuraBackupOffsiteStale` (no set has met the
quorum in 36 hours, including when offsite has never succeeded).

The age private key is still needed for every restore. Losing it loses every backup on every
provider; see "Backup encryption keys" above.

**Rotate `jwt_secret`.** This invalidates every session, so announce it first.

```bash
openssl rand -hex 32 > deploy/secrets/jwt_secret
docker compose -f docker-compose.prod.yml restart gateway
```

**Review the approved-source registry.** Sources accumulate; each one is network access the
nightly job exercises unattended. Remove what nobody uses.

**Review capability grants.** Grants are role-derived today, so this is a review of who holds
`Admin`.

**Update dependencies.** No automated CVE scan is wired up — this is the manual stand-in, and
adding `pip-audit` to CI is the recommended fix (see the
[Security Audit](../reports/security-audit.md) §7).

---

## Backup encryption keys

Backup sets are encrypted with [age](https://age-encryption.org) public-key encryption (P1.3). The
VPS holds only the **public** key (the recipient): a compromised VPS or backup bucket can neither
decrypt any set nor recover the key from it. The **private** key never lives on the VPS. Lose it
and every encrypted backup is unrecoverable -- so it is stored offline, in at least two places.

What this does and does not protect: confidentiality of every set, on disk and offsite, and
detection of corruption or tampering (age's AEAD plus the two manifests). It does not stop a
fully compromised host from deleting sets or writing new forged ones -- anyone holding a public
key can encrypt to it. Offsite object lock / versioning is the defence against deletion.

### Generate two key pairs (once, on trusted machines -- never the VPS)

AURA uses **two independent key pairs** (decision recorded in
`docs/plans/distributed_storage_plan.md`). Every set is encrypted to both public keys, and either
private key alone can decrypt it. Losing one key, or the place it was kept, then loses no backup.
`AuraBackupSingleKey` fires while fewer than two distinct public keys are configured.

```bash
# Key pair A -- e.g. on the owner's own computer
age-keygen -o aura-backup-A.agekey        # prints "Public key: age1..."
# Key pair B -- ideally made by, or for, whoever keeps the second copy
age-keygen -o aura-backup-B.agekey
age-keygen -y aura-backup-A.agekey        # re-print a public key at any time
```

Keep the two private keys **apart**. That is the whole point of having two:

- **A:** a password-manager entry the owner controls.
- **B:** a different place that does not fail with A, such as a printed copy in a safe, or a
  trusted second person's password manager.

Neither goes in a backup bucket, the repository or the VPS. Each is small text, so paper works.
Keep the offline copy of `data_encryption_keys` (below) with them. Delete the key files from the
machines you generated them on unless that machine is one of the storage places.

Test each key once before relying on it. After the first nightly set, run the restore drill once
with `aura-backup-A.agekey` and once with `aura-backup-B.agekey` (*Practise a restore* above).

### Configure the VPS (public key only)

Either in `deploy/.env`:

```bash
BACKUP_ENCRYPTION=age
BACKUP_AGE_RECIPIENT=age1...your-public-key...
```

or (preferred, and what the two-key setup uses) one public key per line in
`deploy/secrets/backup/backup_age_recipients.txt`, comments allowed:

```text
# key pair A -- owner's password manager
age1...public-key-A...
# key pair B -- printed copy in the safe
age1...public-key-B...
```

The file is gitignored. The `backup/` subdirectory is the only part of `deploy/secrets` the sidecar
can see. `deploy/scripts/bootstrap-secrets.sh` creates it, and moves a file left at the old flat
`deploy/secrets/` location into it. Then:

```bash
docker compose -f docker-compose.prod.yml up -d --build backup
docker compose -f docker-compose.prod.yml logs -f backup    # expect "sealing set ... (encryption=age)"
```

Encryption is **on by default and fails closed**: with no valid recipient the sidecar logs
`cycle REFUSED`, writes `aura_backup_config_error 1` (`AuraBackupMisconfigured`) and takes no
backup until fixed, rather than writing plaintext. It also refuses a private key pasted where the
public key belongs. `BACKUP_ENCRYPTION=none` is an explicit, alerted opt-out
(`AuraBackupUnencrypted`); unencrypted sets are never shipped offsite.

### Restore

Either private key works; bring **one** of them to the host for the restore only, mount it
read-only into the one-off container (see *Practise a restore* above), and delete it from the host
afterwards:

```bash
shred -u /path/to/aura-backup.agekey 2>/dev/null || rm -f /path/to/aura-backup.agekey
```

`restore.sh` verifies `CHECKSUMS.sha256`, decrypts, and checks every artifact against the
decrypted manifest before it touches the database; any mismatch aborts with the database
untouched. `backup-open.sh --verify` checks a set's integrity without the key.

### Rotate

Rotate yearly, when anyone who had access to a private key leaves, or at once if it may have
been exposed. Rotate **one key pair at a time**, so the other keeps every existing set readable
throughout. During the rotation the recipients file briefly holds three public keys.

1. Generate a new pair (as above) and store the new private key offline.
2. Put **both** public keys in `deploy/secrets/backup/backup_age_recipients.txt` (new sets are then
   decryptable by either key) and recreate the `backup` service.
3. After the first set sealed with both keys verifies, remove the old public key from the file
   and recreate the service again.
4. Keep the **old private key** until every set encrypted only to it has aged out of local and
   offsite retention (longest: `BACKUP_KEEP_MONTHLY` / `BACKUP_OFFSITE_KEEP_MONTHLY` months). Then
   destroy it. If the old key was *compromised* rather than merely retired, also delete the sets
   encrypted only to it once enough new-key sets exist -- they are readable by whoever has it.

### The application data-encryption key (`data_encryption_keys`) -- keep it with the age key, not with the backups

P1.2 encrypts sensitive columns at the application layer with keys from the Docker secret
`data_encryption_keys` (`deploy/secrets/data_encryption_keys`). **A restored database is
useless without it**: the rows come back, but the encrypted columns cannot be read. The backup
scripts deliberately do **not** copy it, and it must **never** be put inside a backup set or the
backup bucket -- a backup that carries its own decryption key protects nothing from whoever
obtains the backup.

Custody, same discipline as the age private key:

- Store a copy **offline, separately from the backups** (password manager + a second physical
  place), and update that copy in the same sitting whenever the key file changes (a key rotation
  adds a key; old keys must be kept as long as any row or any retained backup was written with
  them).
- Treat it as part of the disaster-recovery kit: a restore onto a fresh host needs *three* things
  -- a backup set, the age private key, and `data_encryption_keys` -- plus the other
  `deploy/secrets/*` (see the deployment guide's warning to back up `deploy/secrets/` somewhere
  the database backups are not stored).
- The restore drill does not exercise it (the throwaway Postgres never runs the application).
  A full recovery rehearsal should read at least one encrypted column through the gateway with
  the offline copy of the key before that copy is trusted.

### Not yet proven

Everything above is built and covered by `deploy/scripts/tests/test_backup_crypto.sh` (real age,
stubbed rclone) in Backend CI. It is **not proven** until a drill runs on the VPS with encryption
on: a real `backup` cycle sealing a real set, a real offsite upload verified against the real
bucket, and `restore-drill.sh` restoring that set with the offline key, timings recorded in
`DRILL_RECORD`, written up under `docs/reports/`.

---

## Event-driven maintenance

### After changing the embedding model or dimension

**Required.** Vectors from one model are meaningless when searched with another. The change
provisions a *new* index rather than corrupting the existing one (ADR-007):

```bash
curl -X POST -H "$AUTH" $AURA/api/v3/admin/index/rebuild
```

Verify `promoted: true`. If not, read `gate_failures` — the old index is still serving.

### After a bad prompt change

The fastest lever in the system, no deploy required:

```bash
curl -X POST -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"version": "1.0.0"}' $AURA/api/v3/admin/prompts/system.orchestrator/pin
```

Every turn records `prompt.template` and `prompt.version` in its telemetry, so a quality
regression can be traced to the change that caused it.

### After a provider outage

The circuit half-opens automatically after 30 seconds and admits one probe. To restore
immediately once the cause is fixed:

```bash
curl -X POST -H "$AUTH" $AURA/api/v3/admin/providers/local/reset-circuit
```

An unknown provider name returns 422 listing the known ones rather than succeeding silently — an
operator must not be able to believe a circuit was reset while the outage continues.

### After a data-deletion request

```bash
DELETE /api/v3/memory/{id}                        # one record
memory.forget_source(owner_id=…, source=…)        # everything from one source
DELETE /api/v3/knowledge/documents/{id}           # a document and its chunks
```

`forget_source` removes the vector points via the same filter, so the index retains no orphans
that would still be retrievable. This works only because every write carries provenance.

### After certificate renewal

Let's Encrypt certificates last 90 days; the bundled cron renews weekly. Verify after any manual
intervention:

```bash
echo | openssl s_client -connect $AURA_DOMAIN:443 2>/dev/null | openssl x509 -noout -dates
```

---

## Log hygiene

Logs are structured JSON. To follow one request end to end:

```bash
docker compose -f docker-compose.prod.yml logs gateway \
  | jq -R 'fromjson? // empty | select(.correlation_id == "<id>")'
```

**Logs never contain prompts, documents, memories, or answers** — lengths, hashes, counts, and
scores only. The logging filter enforces this for `extra=` fields. If you find content in a log
line, that is a defect worth reporting, not a convenience to preserve.

Loki retention defaults to 14 days. Audit records are separate, live in Postgres, and have their
own retention: they must survive a restart, which a log shipper does not guarantee.

---

## When something is wrong

Start with the [Troubleshooting Guide](troubleshooting-guide.md). The three questions worth asking
first, in order:

1. **Is `/health/ready` returning 200?** If not, the answer names the failing dependency.
2. **Is any provider circuit open?** `admin/status` reports it. An open circuit explains slow,
   expensive, or failing turns without any other symptom.
3. **Did anything change?** Prompt versions, index generation, and model stage are all in
   `admin/status`, and all three are individually reversible.

---

Related: [Administrator Guide](administrator-guide.md) ·
[Troubleshooting Guide](troubleshooting-guide.md) · [MLOps Guide](mlops-guide.md) ·
[Deployment Guide](deployment-guide.md)
