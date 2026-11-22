# Adaptive Knowledge Update System ("Loop C") — design

**Status:** Design, not built · **Date:** 2026-11-19 · **Decision record:**
[ADR-035](../architecture/035-adaptive-knowledge-updates.md) · **Gap:** GAP-065 ·
**Roadmap:** M10 *Knowledge & Memory Quality*

Every path and line number in this document was checked against `main` at `7639789`. Line numbers
drift, so check them again before acting on one.

---

## 0. Summary

AURA's global knowledge base changes today in only two ways:

- **Loop A (ADR-008):** the nightly refresh from sources an admin registered.
- **Manual REST:** someone ingests or deletes a document directly.

Nothing reacts to what users actually ask. This design adds a third loop, **Loop C**, which runs in
five stages:

1. **Notices** four kinds of signal: questions the corpus could not answer, explicit "learn this"
   requests, corrections to answers, and stale or contradicting documents.
2. **Researches** the topic in approved sources and a self-hosted SearxNG.
3. **Drafts** a cited proposed change and checks it against what the corpus already says.
4. **Queues** the proposal for a platform admin.
5. **Applies** it to the global corpus once approved, using the existing versioned-ingest path,
   with a one-click revert.

Nothing in Loop C changes knowledge without an admin's recorded, digest-bound approval.

## 1. Requirements

### 1.1 Decisions taken with the owner (2026-11-19)

| Question | Decision |
|---|---|
| What triggers an update | An explicit chat request, a correction to an answer, a new version of a document, automatic staleness, **and** a question about a topic the corpus does not cover |
| Where research looks | Approved sources (registered sources, connectors, an admin-curated research-domain allowlist) **and** a self-hosted web search (SearxNG) |
| Who reviews | **Every** change is queued, and only **platform admins** (Admin/SuperAdmin) can approve |
| What the asker sees meanwhile | The normal model answer; research runs silently in the background |
| Where approved knowledge goes | Always the **global** corpus (`owner_id=None`) |

### 1.2 Interpretations (recorded in ADR-035; change them there)

- **I-1. Loop C only changes the global corpus.**
  - Personal and team-workspace corpora (ADR-034) keep today's direct ingest, versioning and
    delete. A user re-uploading their own document is unchanged.
  - The "document re-upload" trigger therefore means a new version of a *global* document. It
    arrives either as an admin upload or as a user's "suggest a newer version" against a global
    document an answer cited.
- **I-2. Loop A stays automatic, as ADR-008 decided.** Loop C sits next to it and does not
  replace it.
- **I-3. A staleness re-check that finds nothing has changed is not queued.** It only moves the
  document's `last_verified_at` forward. That is audited, but no knowledge content changes, so
  there is nothing to review.

### 1.3 Functional requirements

| ID | Requirement |
|---|---|
| F-1 | Record a **knowledge gap** when a knowledge-seeking turn retrieves nothing above the score floor, and aggregate repeated gaps on the same topic. |
| F-2 | Let a user **explicitly ask** AURA to learn about, or update, a topic: in chat via a kernel tool, and in the client via a "suggest" action. |
| F-3 | Turn an answer **correction** (`POST /api/v3/feedback` with `correction`) into a proposal against the global documents that turn cited. |
| F-4 | Turn a **new version of a global document** into a proposal with a diff, not a direct write. |
| F-5 | **Detect stale knowledge**: documents past `review_after`, and documents contradicted by a newer proposal or by repeated corrections. Re-research them. |
| F-6 | **Research** a topic from approved sources and SearxNG. Fetch pages through the SSRF-guarded fetcher, extract text, and run the context safety guards. |
| F-7 | **Draft** a cited proposal: new document, new version, or retirement. Attach the evidence and the conflict-check result. |
| F-8 | **Queue** every proposal for platform-admin review in the console. The admin can see the diff, evidence and conflicts, edit the draft, and approve or reject it. |
| F-9 | **Apply** an approved proposal to the global corpus through `KnowledgeEngine`, bound to the approved digest. **Revert** an applied proposal. |
| F-10 | Keep **provenance** end to end: proposal → evidence URLs → approver → document `(id, version)`. |

### 1.4 Non-functional requirements

| ID | Requirement | Target |
|---|---|---|
| N-1 | No added chat latency | Gap capture is fire-and-forget after the turn. p95 turn latency is unchanged (within ±2%). |
| N-2 | Privacy | No asker identity or raw PII reaches admins, SearxNG or a hosted model (§7). |
| N-3 | Self-hosting | No closed-model vendor (only ADR-033's gated exception) and no hosted search API. Search is self-hosted. |
| N-4 | Safety | Fetched content is DATA (ADR-006). The guards run before drafting. Nothing reaches the live index before approval. |
| N-5 | Bounded cost | A daily research budget, per-proposal source and byte caps, and a per-user cap on explicit requests. |
| N-6 | Off by default | `AURA_KNOWLEDGE_ADAPTIVE_ENABLED=false`. With it off, the default `pytest` run and every existing behaviour are unchanged. |
| N-7 | Testability | Every port has a real in-memory adapter, so the default suite needs no Docker, network or model. |

### 1.5 Constraints

- **Hexagonal layering** (`tests/architecture/`). The new engine package may import only `domain/`
  and `ports/`. SDKs (httpx) are imported lazily inside adapters.
- **Alembic is the only schema authority** (CB-20). Every new table comes from a migration with a
  working `downgrade()` (§4.4).
- **Inference throughput.** On the VPS a CPU-only local model runs at about 5–10 tok/s. Railway
  has no local model, so its only healthy provider is the hosted open-weight `hosted1` slot, and
  that accepts only `INTERNAL` content (ADR-030).
- **Two deploy targets with compose parity.** Isolation settings in the prod and dev compose files
  must match (`test_compose_security_parity.py`).

## 2. What exists today, and what Loop C reuses

| Need | Existing piece | Gap it leaves |
|---|---|---|
| Versioned writes | `KnowledgeEngine.ingest(document_id=…)` makes a new immutable version, supersedes the old one, retires its chunks and expires its graph facts (`knowledge/engine.py`). | — |
| Delete / retire | `KnowledgeEngine.delete_document`: purges graph facts, tombstones, cleans up vectors and BM25. | — |
| "Not found" signal | `searched_but_empty` (`kernel/kernel.py:1098`, `api/v3.py:1110`). | Sent to the client only; nothing server-side acts on it. |
| Review and approval | `ApprovalService` (`learning/approval.py:188`): digest binding, a requester can't approve their own request, 14-day TTL, audit, `ApprovalRequested`/`ApprovalDecided` events. `/admin/approvals` routes. Console Approvals page. | No kind for knowledge changes. |
| Safe fetching | `_assert_public_url` / `_assert_resolves_publicly` (`tools/builtin.py:386,415`), `domain/network.is_internal_address`, `HttpSourceFetcher` (`learning/sources.py:140`). | They enforce a host allowlist; nothing searches. |
| Extraction | `knowledge/documents.py` (HTML/PDF extraction, OCR). | — |
| Ingest safety | `SafetyPipeline.guard_context_items` (`safety/pipeline.py:199`), `PIIGuard` (`safety/guards.py:407`). | `trust_tier` is not passed to the guards. |
| Durable async | `DurableEventBus` plus the outbox (`adapters/runtime.py:170`). The Celery `learning` queue and beat (`celery_app.py:174`). | — |
| Feedback | `POST /api/v3/feedback` (`api/v3.py:1597`) stores `FeedbackRecord.correction`. | Only Loop B fine-tune curation reads it. |
| Admin audit | `audit_admin` (`api/v3.py:560`), `AuditEvent`, `GET /admin/audit`. | — |

**Why not memory?**
- Memory is per-person and never shared (ADR-034), so it cannot be the global store.
- Its near-duplicate check (`memory/engine.py:106`; SEMANTIC threshold 0.94) *reinforces* the old
  record and drops the new text. A correction like "the URL is now Y" would be swallowed by the
  stale "the URL is X". That defect is out of scope here and is noted in §11.

## 3. High-level design

```
                    ┌──────────────────────────── gateway (uvicorn) ────────────────────────────┐
 chat turn ───────► │ AIKernel ─ retrieval: searched_but_empty ∧ GeneralKnowledgeGate ──┐       │
                    │                                                                     │       │
 "learn about X" ─► │ tool propose_knowledge_update / POST /knowledge/suggestions ───────┤       │
 correction ──────► │ POST /feedback (correction, turn_id → cited global docs) ──────────┤       │
 admin upload ────► │ POST /admin/knowledge/documents (new version of a global doc) ─────┤       │
                    └─────────────────────────────────────────────────────────────────────┼───────┘
                                                        DurableEventBus → outbox (Postgres) ▼
                    ┌──────────────────────────── Celery `learning` queue ──────────────────────┐
 beat: staleness ─► │ GapAggregator ──► (threshold / explicit / budget) ──► ResearchService      │
                    │                                                         │                  │
                    │       WebSearch port ──► SearxngSearch  ───────► searxng container         │
                    │                     └──► ApprovedSourceSearch (registry, connectors)       │
                    │       SafeFetcher (public-IP, no redirect, byte cap) → extract → guards    │
                    │                                                         ▼                  │
                    │       Synthesizer (LLM, data_scope INTERNAL) ──► ConflictChecker           │
                    │                                                         ▼                  │
                    │       ProposalStore.put(pending_review) + ApprovalService.request(digest)  │
                    └───────────────────────────────────────────────────────────────────────────┘
                                                                              ▼
 console /knowledge/proposals: diff · evidence · conflicts · edit (new digest) · approve/reject
                                                                              ▼ approved
 ProposalApplier: ApprovalService.require(id, digest) → KnowledgeEngine.ingest(owner_id=None)
                  | delete_document → CAG invalidate → audit → gap.status=filled   (revert = re-ingest prior)
```

### 3.1 Components

The engine package is `aura_core/knowledge_updates/`. It imports only domain and ports.

| Component | Responsibility |
|---|---|
| `GeneralKnowledgeGate` | Runs in the kernel after retrieval. It decides whether an empty retrieval is a *global* gap worth recording (§7.1). Pure and deterministic, so it adds no model call. |
| `GapAggregator` | Upserts a `KnowledgeGap` by topic: normalise, then hash, then match by embedding to an existing open gap (cosine ≥ 0.90). Counts distinct askers through a salted hash. Marks a gap `ready` when the threshold is met. |
| `ResearchService` | For one gap, request or stale document: plans up to 3 queries, runs `WebSearch`, ranks and filters hits by domain policy, fetches at most 5 pages through `SafeFetcher`, extracts text, and guards it. Produces an `EvidenceBundle`. |
| `Synthesizer` | Drafts a concise, cited article from the evidence alone. The prompt forbids unsupported claims, and every paragraph cites `[n]`. Output is a `ProposalDraft`. |
| `ConflictChecker` | Retrieves the top global chunks for the draft's claims, then labels each pair agree / contradict / unrelated. It runs an LLM judge where the classification allows (§7.3); otherwise it falls back to embedding similarity plus negation heuristics. |
| `ProposalService` | The state machine (§4.3). Persists the proposal, requests the approval and handles admin edits (a new digest means a new approval). |
| `ProposalApplier` | On approval: `require(approval_id, expected_digest)`, then ingest or retire in the global corpus, invalidate the CAG prefix, audit, close the gap. Also performs revert. |
| `StalenessScanner` | Nightly beat job. Finds global documents past `review_after`, or with ≥ N open corrections, or contradicted by a pending proposal, and enqueues re-research. |

### 3.2 New ports, each with an in-memory and a real adapter

```python
class WebSearch(Protocol):
    async def search(self, query: str, *, limit: int, language: str | None = None) -> Sequence[SearchHit]: ...

class KnowledgeGapStore(Protocol):
    async def observe(self, observation: GapObservation) -> KnowledgeGap: ...      # upsert + count
    async def ready(self, *, limit: int) -> Sequence[KnowledgeGap]: ...
    async def get(self, gap_id: str) -> KnowledgeGap | None: ...
    async def set_status(self, gap_id: str, status: GapStatus, **refs) -> None: ...
    async def list(self, *, status: GapStatus | None, limit: int, offset: int) -> Sequence[KnowledgeGap]: ...

class KnowledgeProposalStore(Protocol):
    async def put(self, proposal: KnowledgeProposal) -> None: ...
    async def get(self, proposal_id: str) -> KnowledgeProposal | None: ...
    async def transition(self, proposal_id: str, *, expected: ProposalStatus, to: ProposalStatus, **changes) -> KnowledgeProposal: ...  # CAS
    async def list(self, *, status: ProposalStatus | None, kind: ProposalKind | None, limit: int, offset: int) -> Sequence[KnowledgeProposal]: ...
    async def open_for_document(self, document_id: str) -> Sequence[KnowledgeProposal]: ...
```

**Adapters:**

- `SearxngSearch` calls SearxNG's `GET /search?format=json`. It imports httpx lazily and makes no
  network call in `__init__`.
- `ApprovedSourceSearch` searches two things:
  - the registered `KnowledgeSource`s' hosts, through SearxNG `site:` queries;
  - connectors that implement `DocumentConnector`, by title and path match.
- `StaticWebSearch` serves tests.
- `PostgresKnowledgeGapStore` and `PostgresKnowledgeProposalStore` get their pool through
  `PostgresPool._get_pool()` behind the usual `asyncio.Lock`.
- The in-memory store twins live in `adapters/memory_stores.py`.

## 4. Deep dive

### 4.1 Domain model (`domain/knowledge_updates.py`, stdlib only)

```python
class GapStatus(str, Enum):    OPEN, READY, RESEARCHING, PROPOSED, FILLED, DISMISSED, NO_SOURCES
class ProposalKind(str, Enum): GAP_FILL, EXPLICIT, CORRECTION, NEW_VERSION, REFRESH, RETIRE
class ProposalStatus(str, Enum): DRAFTING, PENDING_REVIEW, APPROVED, APPLYING, APPLIED,
                                 REJECTED, EXPIRED, FAILED, REVERTED, SUPERSEDED

@dataclass(frozen=True)
class Evidence:          # one fetched source
    url: str; title: str; fetched_at: datetime; content_hash: str
    domain_policy: Literal["allowlisted", "registered_source", "open_web"]
    excerpt: str          # ≤ 2 000 chars actually used by the synthesizer
    quarantined_chunks: int

@dataclass(frozen=True)
class KnowledgeProposal:
    id: str; kind: ProposalKind; status: ProposalStatus
    title: str; content: str; content_hash: str           # the exact artefact reviewed
    target_document_id: str | None; target_version: int | None
    evidence: tuple[Evidence, ...]; conflicts: tuple[ConflictFinding, ...]
    guard_report: Mapping[str, int]
    gap_id: str | None; feedback_id: str | None; turn_id: str | None
    requested_by: str | None                               # None = system (gap/staleness)
    approval_id: str | None; decided_by: str | None
    applied_document_id: str | None; applied_version: int | None; previous_version: int | None
    created_at: datetime; updated_at: datetime

    @property
    def digest(self) -> str:   # what ApprovalService binds to
        return sha256(f"{self.kind}\0{self.target_document_id}\0{self.target_version}\0{self.title}\0{self.content_hash}")
```

**The digest covers the target and the base version.** If someone else updates the target
document after the review, `apply` sees `target_version != live version` and fails closed with
`stale_base` instead of overwriting the newer version.

### 4.2 Data model (migration `0031_knowledge_updates`)

**`aura_knowledge_gaps`**

| Column | Type | Notes |
|---|---|---|
| id | text PK | |
| topic | text | Normalised and PII-redacted; ≤ 200 chars |
| topic_hash | text | Unique among open gaps (partial index) |
| language | text | |
| ask_count | int | |
| distinct_askers | int | |
| asker_hashes | text[] | HMAC(`AURA_GAP_SALT`, subject), capped at 50; never a raw id |
| sample_queries | jsonb | ≤ 5 redacted samples |
| status | text | `GapStatus` |
| proposal_id | text null | |
| first_seen, last_seen, updated_at | timestamptz | |

**`aura_knowledge_proposals`**

| Column | Type | Notes |
|---|---|---|
| id | text PK | |
| kind, status | text | |
| title | text | |
| content | text | |
| content_hash | text | |
| target_document_id | text null | |
| target_version | int null | |
| evidence, conflicts, guard_report | jsonb | |
| gap_id, feedback_id, turn_id | text null | |
| requested_by | text null | Kept for the four-eyes rule and audit; never shown with the asker's question |
| approval_id, decided_by | text null | |
| applied_document_id | text null | |
| applied_version, previous_version | int null | |
| failure_reason | text null | |
| created_at, updated_at | timestamptz | |
| | | Indexes: (status, created_at), (target_document_id) |

**`aura_documents` (ALTER):** add `last_verified_at timestamptz null` and `review_after
timestamptz null`. Add the partial index `ix_aura_documents_review_due` on (`review_after`) where
`owner_id IS NULL AND status='active'`.

**`aura_research_domains`:** `domain` text PK, `policy` (`allow` | `deny`), `added_by`,
`added_at`, `note`. This is the admin research allowlist.

**Turn telemetry (no migration, if it's a JSON column; otherwise one more ALTER):** add
`cited_documents: tuple[str, ...]`, holding **global** `id@vN` references only, never content
(`domain/telemetry.py`). A correction can then name its target without storing the answer.

`downgrade()` drops the three new tables, the two columns and the index. It must restore 0030's
schema exactly (`tests/load/test_migration_roundtrip_postgres.py`).

### 4.3 State machines

```
Gap:       OPEN ──(threshold met | explicit)──► READY ──► RESEARCHING ──► PROPOSED ──(applied)──► FILLED
             │                                                  └──(no usable evidence)──► NO_SOURCES (re-tried after 7 d, max 3)
             └──(admin)──► DISMISSED   (suppresses the topic_hash for 90 d)

Proposal:  DRAFTING ──► PENDING_REVIEW ──approve──► APPROVED ──► APPLYING ──► APPLIED ──revert──► REVERTED
                             │   │                                   └──(stale_base | ingest error)──► FAILED
                             │   └─edit──► PENDING_REVIEW (new digest; old approval rejected as superseded)
                             ├──reject──► REJECTED
                             ├──approval TTL (14 d)──► EXPIRED
                             └──newer proposal for same target──► SUPERSEDED
```

**Every transition is a compare-and-set** (`transition(expected=…)`), so a worker and a console
click cannot both apply the same proposal.

### 4.4 Flows

**(a) Gap capture.** This runs in the gateway and adds no latency.

1. After the turn completes, the kernel checks `searched_but_empty` and runs
   `GeneralKnowledgeGate` (§7.1). If the gate fails, nothing is recorded.
2. If it passes, the kernel publishes a durable `KnowledgeGapObserved(topic_redacted, language,
   asker_hash, turn_id)` event through the outbox.
3. A worker consumes the event and calls `GapAggregator.observe`.
4. Beat job `knowledge-gaps-research` runs every 15 minutes. It takes `ready()` gaps up to the
   remaining daily budget and dispatches `research_gap_task(gap_id)`.

**(b) Explicit request.**

1. The user asks in chat, for example "learn about the 2027 tax brackets" or "your info on X is
   outdated, it's now Y". The tool `propose_knowledge_update(topic, claim?, source_url?)`
   validates the input, applies the per-user rate limit (default 5/day), and creates a gap with
   `explicit=True`. An explicit request skips the distinct-asker threshold but not the budget.
2. The tool replies: "Queued for review by AURA's administrators. If approved it becomes shared
   knowledge."
3. The client's "Suggest knowledge" action calls `POST /api/v3/knowledge/suggestions` and follows
   the same path.

**(c) Correction.**

1. A `POST /feedback` with a non-empty `correction` looks up the turn's `cited_documents`.
   - If that turn cited **global** documents, a durable `KnowledgeCorrectionSubmitted` event
     creates a `CORRECTION` research job targeting the most-cited document.
   - If it cited none, the correction is handled as an explicit request on the turn's topic.
2. The user's correction text is treated as a **claim to verify**, never as content to ingest. The
   research must find evidence that supports it.
3. If no evidence supports the claim, the proposal is still queued, marked `unsupported_claim`, so
   the admin decides with that context.

**(d) New version of a global document.**

1. `POST /admin/knowledge/documents` (Admin) receives `{document_id?, title, content | file}` and
   runs extraction and the guards.
2. It creates a `NEW_VERSION` proposal with a unified diff against the live version, plus the
   conflict check. It skips research: the evidence is the upload itself.
3. A user's "suggest a newer version" goes through (b), with `target_document_id` set.

**(e) Staleness (nightly, 03:00, after Loop A's 02:00 refresh and memory consolidation at 02:30).**

1. Select global documents where `review_after < now()`, or that have ≥ 3 corrections in 30 days,
   or that a pending proposal contradicts.
2. Re-research each (cap: 20 per night).
3. If the outcome is **unchanged**, set `last_verified_at` and `review_after = now() + policy`,
   and audit it. No proposal is created (interpretation I-3).
4. If the outcome is **changed**, create a `REFRESH` proposal.
5. If there is **no supporting evidence and contradicting evidence exists**, create a `RETIRE`
   proposal.

**(f) Review and apply.**

1. The console lists `PENDING_REVIEW` proposals. Each shows:
   - the diff (for a new document, the full text);
   - the evidence with its domain policy;
   - conflict findings and guard counts;
   - the redacted demand ("asked by 7 people", for gap fills).
2. **Approve:** `POST /admin/approvals/{id}/decide` (the existing route). An `ApprovalDecided`
   handler then enqueues `apply_proposal_task(proposal_id)`.
3. `ProposalApplier` runs:
   1. Transition `APPROVED → APPLYING` (CAS).
   2. Call `ApprovalService.require(approval_id, expected_digest=proposal.digest)`.
   3. Check the base version.
   4. Write the change:
      - **gap fill, explicit, refresh or new version:**
        `KnowledgeEngine.ingest(owner_id=None, document_id=target or derived, source_type=WEB | UPLOAD, trust_tier=tier(evidence), metadata={proposal_id, evidence_urls, approved_by, fetched_at})`;
      - **retire:** `delete_document`.
   5. Set `review_after`.
   6. Call `cag.on_document_changed`.
   7. Audit `knowledge.proposal.applied`.
   8. Mark the gap `FILLED`.
   9. Transition to `APPLIED`.
4. **Edit:** `PATCH /admin/knowledge/proposals/{id}` replaces title or content. That changes the
   digest, so the open approval is rejected as superseded and a new one is requested. An admin
   who edits a proposal **may not approve their own edit** while another admin exists (the INT-106
   four-eyes rule). The editor becomes `requested_by` for the new approval.
5. **Revert:** `POST /admin/knowledge/proposals/{id}/revert` (Admin), which needs a fresh approval
   of kind `KNOWLEDGE_UPDATE`.
   - It re-ingests `previous_version`'s content as a new version, because versions are immutable
     (ADR-007).
   - If there was no previous version, it tombstones the document.

### 4.5 API surface

**User-facing (`/api/v3`, any authenticated principal; a service key needs the route grant):**

| Route | Body → Response | Notes |
|---|---|---|
| `POST /knowledge/suggestions` | `{topic ≤ 200, claim? ≤ 2000, source_url?, target_document_id?}` → `{suggestion_id, status:"queued"}` | Rate-limited and idempotent. The response states that suggestions become shared knowledge if approved. |
| `POST /feedback` (existing) | adds nothing | A correction now also feeds Loop C, as in flow (c). |

**Kernel tool:** `propose_knowledge_update`.

- `side_effecting=False` with respect to knowledge (it creates a queued suggestion, not a change),
  `sensitivity=INTERNAL`.
- Granted to the orchestrator and the research agent.
- It needs an intent pattern in `planning/engine.needs_plan` ("learn about", "update your
  knowledge", "that's outdated", "is now"). That pattern is kept apart from `propose_task`'s
  "remember … later" pattern (`tools/personal_os.py:163`).

**Admin (`/api/v3/admin`, global Admin role; every write goes through `audit_admin`):**

| Route | Purpose |
|---|---|
| `GET /admin/knowledge/proposals?status&kind&limit&offset` | Review queue |
| `GET /admin/knowledge/proposals/{id}` | Detail: diff against the live version, evidence, conflicts |
| `PATCH /admin/knowledge/proposals/{id}` | Edit the draft (new digest, new approval) |
| `POST /admin/knowledge/proposals/{id}/revert` | Revert an applied proposal (goes through approval) |
| `GET /admin/knowledge/gaps?status` / `POST …/gaps/{id}/research` / `POST …/gaps/{id}/dismiss` | Gap triage |
| `GET` / `PUT /admin/knowledge/research-domains` | Research allowlist; `PUT` is SuperAdmin |
| `POST /admin/knowledge/documents` | Propose a new version of a global document |
| `POST /admin/approvals/{id}/decide` (existing) | Approve or reject; the new kind `knowledge_update` is accepted |

**Console (`aura_admin`):**

- New page `app/(console)/knowledge/proposals/page.tsx`, with list and detail views. The diff
  view reuses whatever the blueprint review uses, if anything; otherwise it is a plain
  side-by-side.
- New page `app/(console)/knowledge/gaps/page.tsx`.
- `approvals/page.tsx` learns the new kind.
- Playwright e2e tests run against the mock gateway, as Admin CI already does.

### 4.6 Events and queues

| Event (`domain/events.py`) | Durable? | Producer → consumer |
|---|---|---|
| `KnowledgeGapObserved` | yes (outbox) | kernel → `GapAggregator` |
| `KnowledgeCorrectionSubmitted` | yes | feedback route → research enqueue |
| `KnowledgeProposalCreated` | no (telemetry) | `ProposalService` → metrics |
| `ApprovalDecided` (existing) | existing | approval service → `apply_proposal_task` when kind = `knowledge_update` |
| `KnowledgeProposalApplied` / `Reverted` | no | applier → metrics, CAG |

**Celery tasks (`learning` queue):**

- `research_gap_task`, `research_document_task`, `apply_proposal_task`.
- Beat: `knowledge-gaps-research` (every 15 minutes) and `knowledge-staleness-scan` (03:00).

### 4.7 Error handling and retries

| Failure | Behaviour |
|---|---|
| SearxNG down or timing out (10 s) | Retry the task twice with backoff (60 s, 300 s). Then gap → `READY` again with a `next_attempt_at`. Metric plus alert after 1 hour of failures. |
| A fetch is refused (private IP, redirect, size, content type) | Skip that hit and record it in `guard_report`. Not an error. |
| Every page quarantined or unusable | Gap → `NO_SOURCES`; retry after 7 days, at most 3 times. |
| LLM timeout or `AllProvidersFailedError` | Retry once. Then `FAILED` with the reason; the gap returns to `READY`. Output is sized to fit the timeout (§6.2), so this should be rare. |
| Synthesizer output fails validation (no citations, citation index out of range, > max length) | One repair attempt, then `FAILED`, so a malformed draft is never queued. |
| `stale_base` at apply | `FAILED`, with the console offering "re-draft against the current version". |
| Ingest fails after approval | Ingest is idempotent per `(document_id, content_hash)`, so retry 3 times, then `FAILED`. The approval stays consumed, and a retry needs a new approval. |
| Worker crash mid-apply | CAS on `APPLYING`. A reaper moves a stuck `APPLYING` (older than 15 minutes) back to `APPROVED` once it has checked whether the document version already exists, which keeps apply idempotent. |

## 5. Research, in detail

1. **Query planning.**
   - Explicit or gap: the topic itself plus up to 2 model-generated reformulations. This is a short
     generation, `max_output_tokens=64`.
   - Correction: the claim, phrased as a question.
2. **Search.** Run `WebSearch` for each query. Merge the hits and drop duplicates by normalised URL.
3. **Domain policy, in order.**
   1. `deny` list: drop the hit.
   2. Registered `KnowledgeSource` hosts and the `allow` list: keep, as `allowlisted` or
      `registered_source`.
   3. Anything else: kept as `open_web` only if `AURA_RESEARCH_OPEN_WEB=true`; dropped by default.
4. **Ranking.** Allowlisted hits first, then SearxNG score. Take the top 5.
5. **Fetch.** `SafeFetcher` is `HttpSourceFetcher`'s checks, extracted into one shared module so
   the tool, the nightly fetcher and this path cannot drift (`domain/network.py` already states
   that intent):
   - https only, public address after DNS resolution, no redirects;
   - 2 MB byte cap, `text/html` or `application/pdf` only;
   - 15 s timeout and a descriptive User-Agent with no user data.
6. **Extract.** `knowledge/documents.py`, then chunking.
7. **Guard.** `SafetyPipeline.guard_context_items`. Quarantined chunks are dropped and counted.
8. **Corroboration.** A claim backed only by `open_web` evidence from a single domain is marked
   `single_source` for the reviewer.

## 6. Scale and reliability

### 6.1 Load estimate (for sizing, not a forecast)

The basis is the SaaS phase-1 target: 50 concurrent users, about 2,000 turns a day.

| Quantity | Estimate | Basis |
|---|---|---|
| Turns with an empty retrieval | ~10% → 200/day | The observed share is unmeasured; it becomes the first metric |
| ... that pass the general-knowledge gate | ~30% → 60 observations/day | |
| Distinct gap topics after clustering | ~25/day | |
| Gaps meeting the threshold (≥ 3 askers) plus explicit requests | ~5–10/day | |
| Research jobs | ≤ `AURA_RESEARCH_DAILY_BUDGET` (default 20) | Hard cap |
| Fetches | ≤ 100/day (20 × 5) | |
| Proposals queued for review | ~5–15/day | **Reviewer load is the real bottleneck** (§8) |
| Storage | proposals ~10 KB each, so ~5 MB/year | |

### 6.2 Inference budget per research job

The job has three generations:

| Generation | `max_output_tokens` | Worst case at 5 tok/s | Timeout |
|---|---|---|---|
| Query plan | 64 | ~13 s | 60 s |
| Synthesis (≈ 350-word article) | 600 | ~120 s | 180 s |
| Conflict judge (batched; ≤ 6 pairs, ~10 tokens each) | 80 | ~16 s | 60 s |

Synthesis input is capped at 5 × 2,000 chars of evidence, which keeps prompt processing on the
CPU model within budget. This follows the `PlanningEngine.plan()` sizing rule
(`planning/engine.py`): 600 tokens fits inside the deployed 180 s `request_timeout_s` with
headroom.

### 6.3 Failure isolation

- Loop C runs only in workers. With SearxNG, the LLM or the stores down, chat is unaffected
  (N-1).
- The gateway's only new work is one outbox insert per qualifying turn.
- Nothing reaches the live index before approval. A bad proposal costs a reviewer's time, not
  correctness.

### 6.4 Metrics and alerts (Prometheus, `aura_kc_*`)

**Metrics:**

- `gap_observations_total{gate_result}` and `gaps_open`.
- `research_jobs_total{outcome}` and `research_duration_seconds`.
- `fetch_refusals_total{reason}`.
- `proposals_total{kind,status}`, and `proposal_review_age_seconds` (the age of the oldest
  pending proposal).
- `apply_failures_total{reason}` and `searxng_up`.

**Alerts:**

- The oldest pending proposal is older than 7 days, so the review queue is starving.
- `searxng_up == 0` for 1 hour.
- More than 50% of applies failed in 24 hours.
- The research budget is exhausted 3 days in a row, a sign of demand or abuse.

## 7. Security and privacy

### 7.1 The general-knowledge gate (deterministic, in the kernel)

A gap is recorded only when **all** of these hold:

1. The turn retrieved nothing above the score floor in **any** scope: the asker's personal,
   workspace or global corpus.
2. No memory was recalled for the turn.
3. The turn is untainted, and it is not an Engineer turn or a service-key turn carrying caller
   evidence. Those are SENSITIVE scopes.
4. The message is a question or an information request (`nlp/intent.py` labels `question` or
   `search`), and it is not personal-OS, scheduling or chit-chat.
5. After `PIIGuard` redaction, the topic still holds ≥ 3 content words, and no redaction removed
   more than 30% of it. A question that is mostly about a person is not a general-knowledge gap.
6. The topic shares no named entity with the asker's private graph (`GraphStore.find_entities`
   over the personal and workspace owner). This stops "When does Project Falcon launch?" from
   becoming a global research topic.

Conditions 1 and 6 are what keep a team's confidential question from leaking into the global
corpus. This is the main cost of the "always global" decision; see §8.

### 7.2 What each party sees

| Party | Sees | Never sees |
|---|---|---|
| Platform admin (console) | Redacted topic, ≤ 5 redacted sample queries, distinct-asker **count**, evidence, draft. For explicit requests and corrections, the claim text the user submitted, which they were told would be reviewed. | Who asked a gap question, conversation text, the asker's documents or memory |
| SearxNG and upstream engines | Redacted topic queries | Any identity; requests come from the server, with no cookies |
| Hosted model (ADR-030) | Redacted topic and public web evidence, if the operator allows hosted research (§7.3) | User documents, memory, global-corpus chunks (unless the operator lowers that classification) |
| Fetched sites | A fetch from the server's IP | Anything about the user |

### 7.3 Data classification of Loop C generations

- **Query planning and synthesis** run inside `data_scope(INTERNAL, "loop-c research: redacted
  topic + public evidence")`. Their prompts contain only the redacted topic and fetched public
  pages, so `INTERNAL` is accurate. On Railway this lets them route to `hosted1`.
  - A fitness test pins this: the research module's prompt builders accept only `str` topic and
    `Evidence` inputs, never a `Document`, `MemoryRecord` or `Message`.
- **The conflict judge** sees global-corpus chunks, which may be the operator's internal
  documents. It runs at `AURA_KNOWLEDGE_GLOBAL_CLASSIFICATION`, default `sensitive` (local only).
  - Where no local provider exists (Railway today), it degrades to the embedding and negation
    heuristic and flags `conflict_check=degraded` for the reviewer.
  - The operator can set `internal` to allow hosted judging.

### 7.4 Threats and controls

| Threat | Control |
|---|---|
| **Knowledge poisoning**: an attacker gets a page ranked, or several accounts ask the same question | Allowlist-only fetch by default; ≥ 3 distinct askers (accounts, not sessions); guards; `single_source` flag; **mandatory admin review**; digest binding; revert |
| **Prompt injection in fetched pages** | Content travels as DATA in the synthesis prompt, fenced with a nonce (`fence_untrusted`); `PromptInjectionGuard` quarantines first; the draft is validated so every sentence carries a citation |
| **SSRF** through search results | Public-IP check after DNS resolution, no redirects, https only, byte and time caps; shared `SafeFetcher` |
| **Cross-tenant leakage** into the global corpus | §7.1 gate (conditions 1 and 6); admins see redacted text only; a user's documents are never research input |
| **Admin abuse** (one admin pushes content alone) | The four-eyes rule (INT-106) applies when another approver exists; every decision is audited; an edit invalidates the approval |
| **Cost or DoS** by explicit-request spam | Per-user cap (5/day); global daily budget; idempotency keys; rate limiter on the suggestions route |
| **Stale-approval replay** | Approvals are digest-bound and expire after 14 days; the base-version check at apply |

## 8. Trade-offs

| Decision | We gain | We pay | Revisit when |
|---|---|---|---|
| **Every change reviewed** | No unreviewed text ever enters shared knowledge; there is an accountable approver | Throughput is bounded by admin time. At about 10/day it is fine; at 100/day the queue starves. | `proposal_review_age` alerts fire weekly. Then consider auto-approve for `REFRESH` with high agreement from allowlisted sources and no conflicts. |
| **Always global** | Every user benefits from every gap filled; one corpus to curate | Cross-tenant leakage risk (mitigated, not removed, by §7.1); a workspace's specialised topics can't be filled for that workspace alone | A customer asks for workspace-private gap filling. Then add a `target_scope` reviewed by the workspace Owner, as ADR-034 anticipates. |
| **Platform admins only** | One trusted reviewer pool; simple RBAC | Admins become a bottleneck and see redacted user topics | Multi-customer scale |
| **Self-hosted SearxNG** vs a hosted search API | No third-party key, no per-query cost, fits the self-hosting stance | Weaker ranking; SearxNG still forwards queries to upstream engines; one more container to patch | Result quality measurably blocks proposals (`NO_SOURCES` rate above 40%) |
| **Allowlist-only fetch by default** | A small, curated attack surface | Many gaps end `NO_SOURCES` until admins grow the allowlist | Allowlist curation becomes the bottleneck |
| **Background research; the asker gets the normal answer** | Zero chat latency; nothing provisional shown as fact | The asker isn't told their gap is being worked on, and the gap stays unanswered until approval | Users ask for "notify me when AURA learns this" |
| **Revert by re-ingesting** (immutable versions) | Keeps ADR-007's invariants and a full history | A revert creates version N+1 with the content of N−1, which is slightly confusing in the document list | Never; this is the right shape |
| **A new loop beside Loop A** | No change to the proven nightly refresh | Two ingestion paths into the global corpus to reason about | Loop A gets a review gate of its own (an ADR-008 amendment) |

## 9. Phased rollout

Each phase ends with evidence, not just code. "Built" and "tested" are not "proven".

| Phase | Scope | Exit criteria (evidence) |
|---|---|---|
| **C0** Foundation | Domain types; the three ports with in-memory adapters; Postgres adapters; migration 0031; `ApprovalKind.KNOWLEDGE_UPDATE`; settings and flag; fitness tests for layering and research-prompt inputs | Default `pytest` and `tests/architecture` green locally; migration round-trip on a real Postgres (`tests/load/test_migration_roundtrip_postgres.py`) |
| **C1** Gap capture | `GeneralKnowledgeGate`, outbox event, `GapAggregator`, `cited_documents` on telemetry, admin gaps routes and console page | Unit tests for every gate condition, including a "Project Falcon" leakage test; console e2e green; a gap count visible on the dev stack |
| **C2** Research | `SafeFetcher` extraction (shared with `fetch_url` and the nightly fetcher), `SearxngSearch`, `ApprovedSourceSearch`, the `searxng` service in **both** compose files plus a Railway service, research-domain allowlist routes, `Synthesizer`, proposal drafting | Parity tests extended and green; a live run on the dev stack drafts at least 3 proposals from allowlisted domains, report in `docs/reports/loop-c-research-*.md` |
| **C3** Review and apply | Console proposals page, edit with re-digest, `ProposalApplier`, revert, CAG invalidation, audit | One real approve, apply, retrieve and revert cycle on the dev stack, with retrieval citing the applied `id@vN`; report committed |
| **C4** Corrections and suggestions | Feedback path, `POST /knowledge/suggestions`, `propose_knowledge_update` tool, intent pattern, client "Suggest knowledge" action, admin new-version upload | Client `flutter analyze` and `flutter test` counts quoted; an end-to-end correction-to-applied-refresh run on the dev stack |
| **C5** Staleness and conflicts | `review_after` policy, `StalenessScanner` beat job, `ConflictChecker` (LLM plus degraded mode), metrics and alerts | A staleness run on the dev stack producing one unchanged verification and one `REFRESH`; alerts fire in a drill |

GAP-065 moves to `built_not_proven` after C3 is built, and to `proven` only when the C3 and C5
reports are committed.

## 10. Open questions

1. **Threshold value.** Is 3 distinct askers right for a small customer base? With 50 users, a
   real gap may take weeks to reach 3. The proposal is to make it configurable and start at 2 on
   the dev stack.
2. **Review cadence.** Who are the admins, and is a 7-day staleness alert on the queue realistic?
3. **Initial research allowlist.** It should be seeded from the registered `KnowledgeSource`
   hosts. Which other domains should go in (official documentation sites, government sources for
   Egypt, Wikipedia)?
4. **Arabic.** SearxNG engine selection and `language` handling for AR queries (the
   `arabic-language_plan.md` interplay).
5. **Railway.** Should SearxNG run as a Railway service, or should research run only on the VPS
   stack's workers while Railway only queues? The design assumes a Railway service.
6. **Notifying the asker.** The owner chose "normal answer". Should an explicit requester at
   least get an "approved, AURA now knows X" notification later?

## 11. Related defects found while designing (not fixed here)

- **Memory swallows corrections.** `MemoryEngine.remember()` reinforces a near-duplicate and drops
  the new text (`memory/engine.py:106`). A personal correction like "my office moved to Y" is
  lost until consolidation, and then only if both records share a `subject`.
- **Loop A writes into the live index before its gate.** *Fixed 2026-11-22.* The nightly refresh
  ingested into the live identity before `_promote` evaluated its gates
  (`learning/knowledge_refresh.py`), so ADR-008's "never in place" held only when the embedding
  model changed; the count-delta gate compared the live index with itself, the quarantine-rate gate
  did not exist, and `dry_run` still wrote. Each source is now assessed without writing, gated as a
  whole, and applied only if it passes; the index is rebuilt and gated only on an embedder or layout
  change. See the amendment in [ADR-008](../architecture/008-human-in-the-loop-learning.md).
- **Unused approval kind.** `ApprovalKind.SOURCE_REGISTRATION` exists, but `POST /admin/sources`
  registers a source with no approval.
