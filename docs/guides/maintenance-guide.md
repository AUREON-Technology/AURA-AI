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
cat /opt/aura/deploy/backups/.last_success | xargs -I{} date -d @{}
```

The backup service **verifies each dump is restorable and non-empty before counting it**. A
timestamp older than ~26 hours means the last run failed verification, not merely that it did not
run. `AuraBackupStale` pages on that. If `BACKUP_S3_URI` is set, also confirm an offsite object
exists (`rclone ls`) and that `AuraBackupOffsiteFailed` is not firing. The failure that matters
is discovering at recovery time that months of dumps were empty.

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
df -h /var/lib/docker && du -sh /opt/aura/deploy/backups
```

---

## Monthly

### Practise a restore

**A restore procedure that has never been run is not a recovery plan.**

First, run the throwaway drill (never against the compose `postgres` service):

```bash
cd /opt/aura
./deploy/scripts/restore-drill.sh
# or, against a copied dump (still throwaway Postgres, not prod):
./deploy/scripts/restore-drill.sh /opt/aura/deploy/backups/postgres-<stamp>.dump
```

That script starts an ephemeral Postgres container, `pg_restore --list`, restores into an empty
database, and asserts at least one table. It refuses to run if `PGHOST` / `PGDATABASE` are set.

Then, on a **staging** copy of the data (not production), restore for real. Non-interactive
confirm is allowed only when the env value **equals** the target database name:

```bash
docker compose -f docker-compose.prod.yml stop gateway worker-realtime worker-ingestion beat
RESTORE_CONFIRM=aura docker compose -f docker-compose.prod.yml run --rm \
  -e RESTORE_CONFIRM=aura --entrypoint /bin/sh backup \
  /scripts/restore.sh /backups/postgres-<stamp>.dump
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

Nightly `backup.sh` now copies Qdrant snapshot files into `backups/qdrant-<stamp>/` and a Redis
RDB into `backups/redis-<stamp>.rdb`. Offsite copy is Phase 8 (`rclone`). Until then, on the
VPS tick:

- [ ] Latest `postgres-*.dump` verified (`pg_restore --list` shows TABLE DATA).
- [ ] Matching `qdrant-<stamp>/` directory contains `.snapshot` files (recover via Qdrant
      snapshot recover API, then rebuild the index if documents were the source of truth).
- [ ] Matching `redis-*.rdb` exists. Restoring a stale RDB means users **re-login** (refresh
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
