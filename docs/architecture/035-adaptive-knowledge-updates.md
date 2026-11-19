# ADR-035 — Adaptive knowledge updates: a reviewed loop from user demand to the global corpus

**Status:** Proposed (designed, not built) · **Date:** 2026-11-19 · **Related:** ADR-004 (memory),
ADR-005 (hybrid retrieval), ADR-006 (trust channels), ADR-007 (versioned indexes), ADR-008
(human-in-the-loop learning), ADR-030 (hosted open-weight inference), ADR-034 (workspaces) ·
**Design:** `docs/plans/adaptive-knowledge-update-system.md` · **Gap:** GAP-065

## Context

AURA's global knowledge base changes today in only two ways:

- **Loop A (ADR-008):** an automatic nightly refresh from sources an admin registered.
- **Manual REST:** someone ingests or deletes a document directly.

What users ask has no effect on what AURA knows:

- **Unanswered questions are dropped.** A question the corpus cannot answer sets
  `searched_but_empty` (`kernel/kernel.py:1098`). That flag reaches the client and is then
  forgotten.
- **Corrections only train models.** A correction sent through `POST /api/v3/feedback` feeds only
  Loop B, the fine-tune curation step.
- **Documents never go stale.** They carry no freshness fields, and nothing notices when two
  documents contradict each other.
- **Memory is the wrong place for these updates.** It is per-person and never shared (ADR-034).
  Its near-duplicate check also keeps the old fact and drops a correction (`memory/engine.py:106`).

The owner wants knowledge to adapt to users in five situations: a topic it does not cover, an
explicit request, a correction, a new document version, and staleness. The owner also set four
conditions:

- every change goes through review;
- only platform admins review;
- the asker gets the normal answer while research runs;
- approved knowledge goes into the global corpus.

## Decision

### 1. A third loop, "Loop C", that is always human-gated

1. **Collect signals.** Loop C gathers five kinds:
   - knowledge gaps;
   - explicit requests (a kernel tool and `POST /api/v3/knowledge/suggestions`);
   - corrections;
   - admin-proposed new versions;
   - nightly staleness and contradiction scans.
2. **Research** each signal from approved sources and a self-hosted SearxNG, and draft a cited
   `KnowledgeProposal`.
3. **Wait for review.** Every proposal waits for a platform admin. Nothing in Loop C changes
   knowledge before a recorded approval.
4. **Reuse the approval machinery.** Approval goes through the existing `ApprovalService`, with a
   new kind, `knowledge_update`. That brings its digest binding, its four-eyes rule (INT-106), its
   14-day expiry and its audit trail.
5. **Bind the digest to the target too.** The digest covers the proposed content *and* the target
   document's base version. Editing a proposal invalidates its approval. If the target changed
   after review, applying fails closed with `stale_base`.

### 2. Only the global corpus, only through `KnowledgeEngine`

- **Writes and retirements.** An approved proposal is written with
  `KnowledgeEngine.ingest(owner_id=None, …)`. A retirement uses `delete_document`.
- **Every ADR-007 invariant holds:**
  - versions are immutable;
  - supersession retires the old chunks and graph facts;
  - CAG invalidation follows.
- **Revert** re-ingests the previous content as a new version. It never edits in place.
- **Provenance** goes in the document metadata: the proposal id, the evidence URLs, the approver
  and the fetch time.
- **Users' own corpora are untouched.** Personal and workspace corpora keep today's direct ingest,
  so Loop C never writes them.

### 3. Research is bounded and self-hosted

- **Search engine.** Search runs through a new `WebSearch` port. The production adapter is a
  self-hosted SearxNG container, deployed with the same settings in both compose files, plus a
  Railway service. No hosted search API is used.
- **What may be fetched.** By default, only domains on an admin-curated research allowlist and the
  hosts of registered `KnowledgeSource`s are fetched. Fetching the open web needs
  `AURA_RESEARCH_OPEN_WEB=true`.
- **How pages are fetched.** Fetches use one shared SSRF-guarded fetcher. It is the existing
  `fetch_url` and `HttpSourceFetcher` checks, extracted into one place: public address after DNS
  resolution, no redirects, https only, byte and time caps.
- **Safety.** Fetched text is DATA (ADR-006) and goes through the context guards before drafting.
- **Cost limits:**
  - a gap needs 3 or more distinct askers (an explicit request skips this);
  - a daily research budget;
  - at most 5 sources per proposal;
  - a per-user cap on explicit requests.

### 4. Privacy at the gate, not at the reviewer

A question becomes a global research topic only if a deterministic gate passes (design §7.1):

- **Nothing retrieved anywhere.** Retrieval found nothing in *any* of the asker's scopes, and no
  memory was recalled.
- **A general-knowledge turn.** The turn is untainted and is not an Engineer or caller-evidence
  turn. It is a question or information request.
- **Little personal data.** PII redaction leaves a topic with enough content.
- **No private entities.** The topic shares no named entity with the asker's private knowledge
  graph.

What admins and SearxNG see:

- **Admins** see the redacted topic and a count of distinct askers, never who asked. Asker ids are
  stored only as salted HMACs.
- **SearxNG** receives only redacted topic queries.

### 5. Data classification of Loop C generations

- **Query planning and synthesis** run in an explicit `data_scope(INTERNAL)`. Their prompts carry
  only the redacted topic and public evidence. A fitness test pins those inputs.
- **The conflict judge** sees global-corpus chunks. It runs at
  `AURA_KNOWLEDGE_GLOBAL_CLASSIFICATION`, which defaults to `sensitive` (local only). With no
  local provider, it degrades to an embedding-and-negation heuristic and flags that to the
  reviewer.
- **Output sizing.** Every Loop C completion sizes `max_output_tokens` so the worst case finishes
  within its timeout at 5 tok/s.

### 6. Off by default

`AURA_KNOWLEDGE_ADAPTIVE_ENABLED=false`. Every port has a real in-memory adapter, so the default
`pytest` run needs no Docker, network or model.

## Consequences

**Positive**

- **Demand drives knowledge.** Repeated unanswered questions and corrections become reviewable
  changes with sources attached. They no longer vanish.
- **Every change to shared knowledge can be traced.** Each one has an approver, evidence, a digest
  and a revert path.
- **Mostly reuse.** The work is mostly existing machinery: versioned ingest, `ApprovalService`,
  the safe-fetch checks, the outbox and the Celery `learning` queue.

**Negative**

- **Admin time limits throughput.** About 10 proposals a day is fine; at about 100 the queue
  starves, and auto-approval would need its own ADR amendment.
- **Leakage risk is reduced, not removed.** "Always global" means a team's specialised question
  could still become shared research if the gate misjudges it. The gate's conditions are the
  control, and each needs a test.
- **One more internet-facing container.** SearxNG has to be patched, and it forwards redacted
  queries to upstream search engines.
- **Many early gaps go unfilled.** By default the research allowlist is small, so many gaps will
  end `NO_SOURCES` until admins curate it.
- **Two paths write the global corpus.** Loop A (automatic) and Loop C (reviewed) both do.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Auto-apply researched content, with undo | Puts unreviewed web text into shared knowledge; the owner chose review for everything |
| Personal or workspace scope as the target | Avoids leakage, but the owner wants every user to benefit; recorded as the first thing to revisit |
| Write corrections into memory | Memory is per-person, never shared, and its duplicate check drops corrections |
| Hosted search API (paid) | Sends user-derived queries to a third party and needs a vendor key; at odds with the self-hosting stance |
| Model-drafted articles with no fetching | High hallucination risk with nothing for a reviewer to check against |
| Extend Loop A instead of a new loop | Loop A is automatic by design (ADR-008) and source-driven, not demand-driven; mixing in a review gate would change its contract |

## Relationship to other ADRs

- **ADR-008:** Loop C is a third loop. It does not amend Loop A's automatic refresh. ADR-008's
  rejected alternative, "fully manual knowledge updates", rejected manual updates *instead of* an
  automatic refresh. Loop C is reviewed and demand-driven, and it runs *alongside* that refresh.
- **ADR-005 / ADR-007:** applied changes are ordinary versioned ingests and deletes, so graph
  supersession, chunk retirement and index invariants are inherited, not reimplemented.
- **ADR-006:** fetched content is DATA. Injection-shaped chunks are quarantined before drafting.
- **ADR-030:** research prompts are classified `INTERNAL` explicitly, and corpus-reading prompts
  are `SENSITIVE` by default.
- **ADR-034:** workspace roles play no part in Loop C. Review uses platform roles only, and
  workspace corpora are never a research input or a write target.
