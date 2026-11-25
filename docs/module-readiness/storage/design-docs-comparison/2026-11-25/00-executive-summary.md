# Storage system vs. the two design PDFs (DISCOVER)

**Date:** 2026-11-25 · **Mode:** DISCOVER (read-only; no application code changed) · **Repo:** `AURA-AI`, branch `ccr-bd0a08ff-g5dv2x`

**Compared:** (1) *AI IDE Storage System Architecture Design* (20 pp, 20 Nov 2026, "proposed baseline");
(2) *JERA IDE Backend schema & relationships* (10 pp; says it follows §3-9, 15-16 and App. A of (1) and adds two
memory tables). Treated as one target ("the PDFs"). Where they differ, noted inline.

**Evidence level:** code/migration/doc reading and greps only. No tests run, no live target probed, no DB inspected.
Anything marked *(unverified)* was not traced to the end.

## Verdict

AURA's storage is a **different, more mature design in the areas it covers** (encrypted personal sync, versioned
vector indexes, graph, billing, workspaces) but it **has no file/blob storage layer at all**, which is the centre of
both PDFs. Readiness against the PDFs: **NO-SHIP for the PDFs' IDE-storage scope** (cloud code sync, uploads,
assets, claim/evidence model). Not a defect in AURA's current product; a scope gap.

## A. In the PDFs, missing (or only partly present) in the system

| # | PDF requirement | State in AURA | Evidence |
|---|---|---|---|
| A1 | S3-compatible object storage; private buckets; opaque keys; versioning/lifecycle (App. §6, JERA p9) | **Missing.** The only S3 use is offsite *backups* via rclone | no S3/boto/presign code in `aura_core`/`api`; `BACKUP_S3_URI` only in `deploy/docker-compose.prod.yml`, `docs/plans/distributed_storage_plan.md` |
| A2 | Secure direct upload: prepare → authorize → presigned/multipart → verify size/hash/malware → commit → process; short-lived download URL | **Missing.** Only `POST /knowledge/documents/upload` (server-side, text for the knowledge index) | `aura_core/api/v3.py` routes |
| A3 | `assets`, `message_assets` (images/audio/video/docs catalogue, attached to messages) | **Missing** (no table, no API) | `alembic/versions/*` has no such table |
| A4 | Cloud project code: `projects`, `files`, immutable `file_versions` (sha256, revision), `/projects/{id}/manifest`, `/files/{id}/revisions` | **Missing.** Engineer workspaces are server-side durable checkouts + host git (ADR-013/014, `aura_agent_workspaces`), not user-file sync | `ADR 013/014`, `0016`; no file/revision tables |
| A5 | `sync_operations`: base_revision optimistic concurrency, idempotent retry, **preserve both drafts** on conflict (§10, "no silent last-writer-wins for code") | **Different, weaker for files.** v2 cursor sync covers personal-OS rows and messages; conflict rule is client-timestamp comparison (`conflict_comparand`) — last-writer-wins by design. Not applicable to code, which isn't synced | `api/v2/sync.py` |
| A6 | Private vs Knowledge as **two independently credentialed databases** (separate roles, optionally separate instances) (§2.2, §11) | **Missing.** One `DATABASE_URL`; `aura_*` knowledge and user tables share a DB/credentials. Isolation is application-level (`owner_id` in every port signature) | `config.py:149`, `settings.py:1395`; `ports/storage.py` header |
| A7 | PostgreSQL **row-level security** (§12.2) | **Missing.** No RLS/`current_setting('app.user_id')` anywhere | grep of `alembic`, `aura_core` empty |
| A8 | **Claim/evidence model**: `claims`, `claim_evidence` (passage-level, support/contradict stance), `review_events`, `knowledge_versions`, `refresh_jobs`; lifecycle candidate→pending→approved→stale→under_review→superseded/rejected; per-claim `valid_until`/`next_review_at`/volatility | **Missing as modelled.** AURA is document+chunk based: `DocumentStatus` active/superseded/tombstoned, source-level trust + licence, nightly source refresh, tombstone-checked retrieval. No atomic claims, no reviewer audit table, no per-claim expiry/volatility, no human approval queue | `domain/knowledge.py`, `0032`, source-registry report |
| A9 | `source_snapshots` (fetched_at, content_hash, object_key) keeping the permitted source artifact | **Partial.** Document versions + content hash exist; no snapshot table, no stored source artifact | `0016` `aura_documents` |
| A10 | Unified retrieval order **private → approved global → live web**, with escalation rule, and `POST /v1/retrieval/query` | **Partial.** Hybrid dense+BM25→RRF→rerank over private+global exists in `KnowledgeEngine.retrieve`; no live-web tier found in the retrieval path *(`browser_subagent` open tool exists; not traced)*; no single retrieval endpoint | `knowledge/engine.py`, `/knowledge/search` |
| A11 | Response metadata: source URL, retrieval timestamp, source version, knowledge status, live-verified flag (§8.2) | **Partial.** Citations exist; the full provenance set isn't returned *(unverified)* | `kernel`, `v3.py` |
| A12 | **Dynamic memory** `memory_items`/`memory_revisions` (JERA p8): explicit `consent_state`, append-only revisions, workspace value over account default, forget invalidates cache + index pointers | **Partial.** `aura_memories` has `superseded_by`, `expires_at`, delete by provenance; no consent state, no revision table, no workspace-over-account precedence *(unverified)* | `domain/memory.py`, `ports/storage.py` `MemoryStore` |
| A13 | Three vector collections `private_code`, `private_memory`, `global_knowledge` with `workspace_id/project_id/file_version_id` payload; **code-aware hybrid search** (symbols, paths, stale-version check) | **Missing/different.** Points carry `owner_id` (user or `global`); no workspace/project/file-version payload; no code index | `ports/storage.py`, `adapters/qdrant_store.py` |
| A14 | Outbox events `asset.upload.completed`, `file.revision.committed`, `knowledge.claim.approved|expired`, `remote.command.completed` … | **Partial.** Outbox exists (`aura_event_outbox`, claim/retry/DLQ); those event types and a `GET /jobs/{id}` poll don't | `postgres_outbox.py` |
| A15 | Reconciler / GC for staged-never-committed uploads, orphan objects, abandoned multipart | **Missing** (no objects to reconcile) | — |
| A16 | Quotas: GB, uploads/day, largest video, indexable code size, embedding volume, remote commands, web searches (§14.1) | **Partial.** Per-workspace messages, `storage_bytes` (knowledge), Engineer gate, seats | `plans/service.py` |
| A17 | Restore *tests* for object storage and vector rebuild-from-canonical as a documented drill | **Partial.** Postgres/Redis/Qdrant backup + restore drill in a sandbox; no live-target proof; no object store | `docs/status/BACKEND_STATUS.md` |
| A18 | Terraform/IaC, dev/staging/prod separation (§11.2) | **Missing.** CLAUDE.md: no `infra/` dir; two deploy targets, no staging | `CLAUDE.md` |
| A19 | Registered `devices` table with `public_key`, `last_seen` | **Present in another form** — device pairing/tokens in `services/device_pairing`, `api/v2/auth.py` *(schema not inspected)* | — |

**Covered by the PDFs and already satisfied:** server-side authz in the API (not just UI); tenant/owner on every
read; private/global vector separation; immutable chunk versions; outbox pattern; idempotency keys; Alembic as the
single schema authority; erase-account path; encrypted offsite backups; mobile→desktop commands via
`/api/v3/host/agent` (ADR-011); GitHub as external system of record.

## B. In the system, absent from both PDFs

- **Field-level encryption**: `*_enc` ciphertext columns, per-user data keys (`aura_user_data_keys`), credential vault,
  encryption-boundary tests. The PDFs never mention encrypting content at rest above the disk.
- **Knowledge graph** with provenance (`aura_graph_*`, `GraphStore`).
- **Versioned vector indexes** with aliases, candidate build, promote gates, rebuild (ADR-007); CAG pinned prefixes
  and corpus revisions.
- **Retrieval-safety layer**: trust channels, instruction quarantine at ingest, taint checks, SSRF-hardened fetcher,
  near-duplicate drop, licence policy D-001 (CC0/CC BY only), per-source ceilings.
- **Billing/plans/metering**: subscriptions, payments surviving erasure, store bindings, entitlements, usage counters.
- **Team model**: workspace invites, member emails, shared conversations, service keys / caller context.
- **Platform stores**: approvals, audit events, turn telemetry, feedback, experiments, fine-tune jobs, model
  versions, prompt pins, control-center tables, adaptation (connectors, twin, blueprints), agent runs/steps,
  Engineer consent, Personal-OS records, goals.
- **Sync mechanics**: transaction-id cursor, tombstones that blank ciphertext, server-owned columns.
- **Operations**: Redis relay bus, rate-limit counters, multi-provider age-encrypted backups with object lock,
  migration round-trip tests, connection-budget arithmetic test, in-memory adapters for every port.
- **Hexagonal layering enforced by AST fitness tests**; the PDFs say "modular monolith" but set no such rules.

**Gaps inside the PDFs themselves** (worth fixing before building from them): `claim_evidence` key is
`(claim_id, source_id)` in App. A, `(claim_id, document_id, excerpt_ref)` in JERA; JERA says `ingestion_jobs` has no
FK to sources/snapshots while the main text says it tracks `query_hash`; no account-deletion exceptions for legal/
payment records; no encryption-at-rest model; no billing; RPO/RTO left unset; the JERA doc's memory write rule
(serialise per item) has no stated mechanism.

## C. Open decisions (needed before a PLAN)

1. **Is the PDFs' IDE-storage scope (cloud code sync + object storage + assets) in AURA's roadmap, or are the PDFs a
   different product (JERA IDE)?** AURA today has no code sync. Everything in A1–A5 and A13 depends on this.
2. Claim/evidence model vs. AURA's document/chunk model (A8): adopt, or keep documents and add only
   reviewer-audit + per-document expiry?
3. Separate knowledge database (A6) and RLS (A7): required, or is signature-level `owner_id` enforcement accepted?

Nothing here was verified on a live target.
