# Administrator Guide

Day-to-day operation of a running AURA deployment: knowledge, indexes, prompts, models,
approvals, and the security posture.

Day-to-day ops use the **AURA Console** (Google sign-in via `POST /api/v3/auth/web/token`) or curl
with a Bearer token. The console is at `https://console.aureon-ai.tech/` on the Railway deploy, and
at `https://$AURA_DOMAIN/console/` on a VPS stack.

Two operator roles:

- **Admin** — status, rebuild/rollback, prompt pin, circuit reset, source list/enable, approvals,
  model promote/rollback, learning export/build, audit read. Access is read-only.
- **SuperAdmin** — everything Admin can do, plus role assignment, session revoke, GDPR forget,
  source register/delete, and allowlisted Ollama pull/delete. `ADMIN_EMAILS` mints Admin only;
  SuperAdmin is assigned via `SUPERADMIN_EMAILS` or the Access page.

The email lists (`SUPERADMIN_EMAILS`, `ADMIN_EMAILS`, `DEVELOPER_EMAILS`, and email keys in
`roles.json`) match only a **verified Google sign-in**: the ID token must say
`email_verified: true`, and a Firebase token must also say
`firebase.sign_in_provider: "google.com"`. An account created with email/password -- or any
other provider -- under a listed address is an ordinary User. A role held by uid in `roles.json`
or assigned on the Access page does not depend on this.

Mutations write an audit record.

```bash
AURA=https://aura.example.com
AUTH="Authorization: Bearer $ADMIN_TOKEN"
```

---

## Knowledge

### Adding a source to the nightly refresh

The 02:00 job reads **only** from the approved source registry. There is deliberately no path
by which it fetches an arbitrary URL — a scheduled job running unattended with the deployment's
network position would otherwise be a server-side request forgery primitive.

Sources must be HTTPS, must not resolve to a private or link-local address, and redirects are
not followed (a redirect is a trivial allowlist bypass).

Each source declares its own ceilings, so one misbehaving source cannot consume the whole run's
budget:

| Field | Purpose |
| --- | --- |
| `max_documents` | Cap per run |
| `max_bytes_per_document` | Rejects a source that started returning something unexpected |
| `trust_tier` | Drives guard strictness during ingestion |
| `schedule_cron` | Defaults to `0 2 * * *` |

### What the nightly run does

```
collect → validate → dedupe → clean + guard → embed into a NEW index
        → refresh graph → evaluate → report → promote (gated) → prune
```

**The live index is never modified.** Everything is built into a new generation and promoted
only if every gate passes, so a bad source, a degraded embedding backend, or a collapsed corpus
leaves production exactly as it was.

Promotion gates:

| Gate | Default | Catches |
| --- | --- | --- |
| `gate_max_recall_drop` | 0.02 | A rebuild that retrieves worse than what it replaces |
| `gate_max_error_rate` | 0.05 | An embedding backend degrading mid-build |
| `gate_max_count_delta` | 0.40 | A source silently returning error pages |
| `min_chunks` | 1 | An empty build promoting cleanly |

A blocked promotion is visible in the `RefreshReport` and raises `AuraKnowledgeStale` after
48 hours.

### Reindexing

Required whenever `AURA_EMBEDDING_MODEL` or `AURA_EMBEDDING_DIMENSION` changes. Vectors from
one model are meaningless when searched with another, so a change **provisions a new index**
rather than overwriting the existing one (ADR-007).

```bash
curl -X POST -H "$AUTH" $AURA/api/v3/admin/index/rebuild
```

```json
{ "index": "aura-knowledge__v1__bge-small-en-v15__384",
  "documents": 1240, "chunks": 8931, "failures": 0,
  "promoted": true, "gate_failures": [], "measurements": {"count_delta": 0.02} }
```

If `promoted` is false, read `gate_failures`. The old index is still serving; nothing broke.

**Rollback** is an alias repoint — O(1), no data movement, and never gated, because reverting
to a known-good state is safe by definition while advancing is not.

The previous index is retained for `AURA_LEARNING_INDEX_RETENTION_DAYS` (default 7). Do not set
this to 0 unless disk is genuinely scarce: retention is what makes rollback a thirty-second
operation rather than a rebuild.

---

## Prompts

Prompts are versioned data, not string literals (TD-11). A prompt change is a registry update
with a version bump, traceable in telemetry and reversible by pinning.

```bash
curl -H "$AUTH" $AURA/api/v3/admin/status | jq '.prompts'
```

```json
[{ "id": "system.orchestrator", "versions": ["1.0.0", "1.1.0"], "active": "1.1.0",
   "pinned": null, "checksum": "a295d91094321879" }]
```

**Rolling back a bad prompt** — the fastest lever in the system, no deploy required:

```bash
curl -X POST -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"version": "1.0.0"}' $AURA/api/v3/admin/prompts/system.orchestrator/pin
```

Every turn records `prompt.template` and `prompt.version` in its telemetry, so a quality
regression can be traced to the prompt change that caused it.

`checksum` is derived from the body: if two deployments report the same version with different
checksums, someone edited a template without bumping its version.

---

## Models

### Local model lifecycle

Pulls require an explicit allowlist and fail closed when it is empty:

```bash
AURA_MODEL_ALLOWLIST=qwen2.5:7b-instruct-q4_K_M,llama3.1:8b-instruct-q4_K_M
```

Model weights are executable artefacts fetched from a third-party registry; "pull whatever the
caller names" is a supply-chain vector, which is exactly the shape of SEC-01.

### Promoting a fine-tuned model

Promotion to production **requires a recorded human approval** (ADR-008). The registry refuses
the transition without one, so the gate cannot be bypassed by calling a lower-level API.

```
candidate → shadow (mirrored traffic, no user impact)
          → canary (a traffic percentage, stable-hash assignment per user)
          → production
```

The approval names a person, a time, and an **artefact digest** — so an approval granted for
one dataset cannot be used to train on or promote another. Approvals expire after 14 days: a
six-month-old approval refers to a dataset and an evaluation that no longer describe the
system.

**Rollback needs no approval.** An incident must not wait on a reviewer.

### Provider health

```bash
curl -H "$AUTH" $AURA/api/v3/admin/status | jq '.providers'
```

```json
[{ "provider": "local", "state": "closed", "healthy": true, "inflight": 1,
   "requests": 4213, "failures": 12, "failure_rate": 0.0028,
   "latency_ms": 1840.2, "ttft_ms": 610.4, "circuit_opens": 1 }]
```

`state: "open"` means the circuit breaker removed the provider from candidacy after consecutive
failures. It half-opens automatically after 30 seconds and admits exactly one probe. To restore
immediately once the cause is fixed:

```bash
curl -X POST -H "$AUTH" $AURA/api/v3/admin/providers/local/reset-circuit
```

---

## Security posture

### The one check that matters most

```bash
curl -s http://localhost:8000/metrics | grep aura_insecure_mode
```

Every series must be `0`. A non-zero value means debug mode, mock authentication, disabled
safety, or disabled rate limiting is live. Production settings validation should have prevented
it; the metric exists because SEC-05 was invisible partly because nothing reported it.
`AuraInsecureModeActive` pages on this.

### Reading guard activity

```promql
sum(rate(aura_guard_verdicts_total[15m])) by (guard, stage, action)
```

`stage="context"` and `stage="tool_result"` are the data channels — where indirect prompt
injection arrives. A rising quarantine rate there means a document in the knowledge base
contains instruction-shaped content: review recent ingestions.

A **block** spike on `guard="secret"` is either an attack or a genuine credential leaking into
answers. Investigate before tuning the threshold.

### Rate limits

Layered and cost-weighted — a chat turn costs 10 and an agent turn 20, against a health check's
0, because they are not the same request:

```bash
AURA_RATE_LIMIT_USER_PER_MIN=60
AURA_RATE_LIMIT_IP_PER_MIN=120
AURA_TRUSTED_PROXY_COUNT=1     # must match the number of proxies in front
```

`AURA_TRUSTED_PROXY_COUNT` is security-relevant: `X-Forwarded-For` is client-controlled, and a
wrong count means the gateway reads the wrong entry. With the bundled Nginx, 1 is correct.

### Audit log

Every tool invocation (including refusals), approval decision, model transition, and safety
quarantine is recorded with actor, action, resource, outcome, and correlation id. Arguments are
stored as a **digest, never verbatim** — they can contain private data and an audit log is
long-lived and widely readable.

---

## Memory

Six kinds with different lifecycles (ADR-004). Consolidation runs on a schedule: it merges
near-duplicates, marks contradictions `superseded` rather than deleting them (the user may be
correcting the *newer* fact), decays confidence on disuse, and expires records below the floor.

**Honouring a deletion request.** Because every write carries provenance:

```
DELETE /api/v3/memory/{id}                      # one record
memory.forget_source(owner_id=…, source=…)      # everything learned from a source
```

Per-user quota defaults to 20,000 records. Exceeding it raises rather than silently evicting —
a user losing memories without being told is worse than a visible error.

---

## Routine schedule

| Cadence | Task |
| --- | --- |
| Daily | Check `.last_success` advanced; scan the Security dashboard |
| Weekly | Review `AuraRetrievalReturningNothing`; check cost trend |
| Monthly | Practise a restore on staging; review pending approvals; check disk |
| Quarterly | Rotate `jwt_secret` (invalidates sessions — announce first); review the source registry |
| On model change | Reindex, verify gates, keep the previous index for the retention window |
