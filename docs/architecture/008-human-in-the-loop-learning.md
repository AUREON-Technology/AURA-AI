# ADR-008 — Knowledge updates are automatic; model changes require human approval

**Status:** Accepted · **Date:** 2026-08-02

## Context

The brief asks for a nightly (02:00) pipeline that keeps AURA's knowledge current, and separately
states that the system must never retrain or modify the base language model automatically without
human approval. These two requirements define a boundary, and the architecture should make that
boundary structural rather than procedural.

## Decision

Two distinct loops with different automation levels.

### Loop A — Knowledge refresh (fully automatic, nightly at 02:00)

`aura_core/learning/knowledge_refresh.py`, scheduled by Celery Beat on the `learning` queue.

```
1. collect     approved sources only, from the SourceRegistry (id, type, uri, credentials ref,
               trust tier, schedule, last_success_cursor)
2. validate    schema, size, encoding, language, content-type, per-source ceilings
3. dedupe      exact (SHA-256) then near-duplicate (SimHash, Hamming ≤ 3)
4. clean       boilerplate strip, normalise whitespace/unicode, safety guards (ADR-006) —
               a source that fails safety is quarantined, not ingested
5. embed       batched, into a NEW index generation (ADR-007), never in place
6. graph       entity/relation extraction → knowledge-graph delta
7. evaluate    run the retrieval golden set against the candidate index
8. report      RefreshReport: counts, timings, quality deltas, quarantines, cost
9. promote     ONLY if every gate passes → alias switch; else keep the old index and alert
```

Every run produces an immutable, numbered `KnowledgeGeneration` record. Rollback is
`index_manager.rollback(generation_id)` — an alias repoint, O(1).

**Gates that block promotion** (all configurable): recall@5 regression > 2 pts; ingestion error rate
> 5 %; quarantine rate > 10 %; document count change > 40 % (catches a source returning an error
page); any P0 safety verdict.

### Loop B — Model adaptation (automatic preparation, human promotion)

`aura_core/learning/curation.py` + `finetune.py` + `approval.py`.

The system may, without approval: collect feedback and traces, curate and de-identify datasets,
compute dataset quality statistics, run evaluations, and **recommend** a fine-tuning job when
thresholds are met (≥ N high-quality examples, ≥ M distinct users, task-category coverage, measurable
gap against the current model on the eval set).

The system may **not**, without a recorded human approval: launch a training job, register a model
version as `production`, or change which model the router prefers.

`ApprovalRequest` records the requester (always the system), the artefacts (dataset hash, eval
report, cost estimate), and requires an `Admin` decision through
`POST /api/v3/admin/approvals/{id}` — written to the audit log with the approver's identity. The
router reads model assignments from the `ModelRegistry`, and the registry refuses to mark a version
`production` without a linked approved `ApprovalRequest`.

### Promotion of a model version

```
candidate → shadow (mirrored traffic, no user impact, metrics only)
          → canary (A/B at X %, guarded by automatic rollback on metric regression)
          → production
```

Automatic **rollback** is permitted without approval — reverting to a known-good state is always
safe; advancing is not.

## Alternatives rejected

| Alternative | Why rejected |
| --- | --- |
| Fully automatic continuous fine-tuning | Model behaviour would change without anyone deciding it should. Feedback loops on self-generated data degrade models, and the failure is slow and hard to detect. Explicitly excluded by the brief. |
| Fully manual knowledge updates | Defeats the purpose; stale knowledge is the failure mode being fixed. |
| Update the index in place nightly | No rollback, and a bad source corrupts the live index with no recovery. ADR-007 exists to prevent exactly this. |
| Approval by configuration flag | Not an audit trail. Approval must name a person and a time. |

## Consequences

**Positive** — knowledge stays fresh without human effort; every generation is versioned and
reversible; model behaviour never changes unattended; the approval record is the audit artefact a
reviewer asks for.

**Negative** — model improvements are gated on someone reviewing them, so iteration is slower. That
is the intended trade. Mitigated by making the recommendation packet complete enough to approve in
minutes (dataset stats, eval deltas, cost, diff of examples).

**Impact:** security and governance improve materially; operating cost is one nightly batch plus
transient double storage; scalability unaffected (the job runs on its own queue and can move to its
own host).
