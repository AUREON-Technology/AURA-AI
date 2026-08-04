# MLOps Guide

Training, fine-tuning, evaluation, and promotion. Covers both learning loops from
[ADR-008](../architecture/008-human-in-the-loop-learning.md):

- **Loop A — knowledge.** Runs nightly and unattended. New documents, re-embedding, index
  promotion. Gated automatically; no human in the path.
- **Loop B — model.** Runs when someone decides to. Curated examples, fine-tuning, evaluation,
  promotion. **Requires a recorded human approval,** and the registry refuses the transition
  without one.

The distinction is the whole design. Loop A is reversible in seconds by repointing an alias, so
it can be automated. Loop B produces an artefact that changes how the system reasons, and its
failure mode is subtle and slow to detect, so it cannot.

---

## The rule everything else follows

**No automatic path modifies the base model.**

`learning.finetune_auto_launch` exists as a setting so its absence is explicit and testable
rather than merely undocumented. Setting it to true makes the process refuse to start:

```
invalid configuration for environment=production
  learning.finetune_auto_launch is not permitted: promoting a fine-tune requires a
  recorded human approval
```

The gate is not advisory. `ModelRegistry.transition(..., to=PRODUCTION)` calls
`ApprovalService.require()` and raises `ApprovalRequiredError` when there is no valid approval —
so it cannot be bypassed by calling a lower-level API, which is the usual way a policy gate
becomes a suggestion.

---

## Loop A — the nightly knowledge refresh

Runs at 02:00 by default (`AURA_LEARNING_REFRESH_CRON`).

```
collect → validate → dedupe → clean + guard → embed into a NEW index
        → refresh graph → evaluate → report → promote (gated) → prune
```

**The live index is never modified.** Everything is built into a new generation identified by
`(logical_name, schema_version, embedding_model_id, dimension)`, and promotion is an alias
repoint — O(1), and reversible in the same operation
([ADR-007](../architecture/007-versioned-indexes.md)).

Promotion gates:

| Gate | Default | Catches |
| --- | --- | --- |
| `gate_max_recall_drop` | 0.02 | A rebuild that retrieves worse than what it replaces |
| `gate_max_error_rate` | 0.05 | An embedding backend degrading part-way through |
| `gate_max_count_delta` | 0.40 | A source silently returning error pages instead of documents |
| `min_chunks` | 1 | An empty build promoting cleanly |

A blocked promotion is reported in the `RefreshReport` and raises `AuraKnowledgeStale` after
48 hours. Until then the previous index keeps serving, which is why a blocked promotion is a
ticket rather than an incident.

### Deduplication

Length-aware, because one method does not work at both scales:

- **≥ 200 tokens:** 64-bit SimHash, Hamming distance ≤ 6.
- **< 200 tokens:** exact shingle Jaccard ≥ 0.75.

The split exists because SimHash is unusable on short documents — appending three words to a
13-word document flips 11 of 64 bits, which reads as "completely different". This was found by
testing, not by reasoning.

---

## Loop B — fine-tuning

### 1. Curate

`DatasetCurator` turns feedback into training examples. What it enforces:

- **Corrections outrank ratings.** A user rewriting an answer is a supervised label; a thumbs-up
  is a weak signal.
- **PII is redacted before the example is stored**, not before training. A dataset is copied,
  shared, and outlives the pipeline that made it.
- **Every example carries provenance**, so a dataset can be reduced when a user asks to be
  removed from it.
- **The dataset digest is content-addressed**, which is what binds an approval to specific data.

```python
examples, stats = curator.curate(feedback_records)
print(stats.as_dict())
# {"total_candidates": 1840, "accepted": 212, "acceptance_rate": 0.115,
#  "distinct_users": 34, "corrected_fraction": 0.61, …}
```

### 2. Ask whether to train at all

```python
recommendation = curator.recommend(examples, stats, base_model="qwen2.5:7b-instruct")
```

**A negative recommendation is the more useful output**, because it names what is missing:

| Condition | Why it blocks |
| --- | --- |
| Fewer than `min_examples` | The model memorises instead of generalising |
| Fewer than `min_users` distinct users | The fine-tune encodes one person's preferences as general behaviour |
| Acceptance rate < 5% over 100+ candidates | Feedback quality is the problem; training on the survivors bakes it in |
| All examples from one agent persona | A warning, not a block — specialising may be intended |

`estimated_tokens` and `estimated_cost_usd` are reported so the decision is made with the bill
visible.

### 3. Request approval

```python
approval = await approvals.request(
    kind=ApprovalKind.FINETUNE,
    subject="qwen2.5-7b-aura-v3",
    artefact_digest=recommendation.dataset_digest,
    requested_by="ops@example.com",
    rationale="212 examples, 61% human-corrected, 34 users",
)
```

The approval binds **person + time + artefact digest**. Consequences:

- An approval granted for one dataset **cannot** be used to train on another — the digest will
  not match.
- Approvals **expire after 14 days**. A six-month-old approval refers to a dataset and an
  evaluation that no longer describe the system, and treating it as current is how a stale
  decision becomes a production model.

### 4. Train

Training runs outside the gateway — it needs a GPU and hours, neither of which belongs in a
request path. The platform records the run rather than executing it:

```python
experiment = await tracker.experiment("finetune-qwen")
run = await tracker.start_run(
    experiment.id,
    parameters={"method": "lora", "rank": 16, "lr": 3e-4, "epochs": 3},
    dataset_digest=recommendation.dataset_digest,
    base_model="qwen2.5:7b-instruct",
    code_version="git:deadbeef",
)
await tracker.log_metrics(run.id, {"train_loss": 0.42}, step=100)
await tracker.finish_run(run.id)
```

`dataset_digest`, `base_model`, and `code_version` are the reproducibility inputs. Without them,
"which data produced this model?" has no answer six months later — and that question is always
asked at the worst possible moment.

LoRA is the default method. Full fine-tuning of a 7B model needs multiple high-memory GPUs; LoRA
adapters train on one, are a few dozen megabytes, and can be swapped without redeploying the base
weights. Architecture specifications (`aura_core/ml/nn.py`) are declared as **data**, so
parameter counts and memory footprints are computable with no torch installed — an operator can
answer "does this fit?" before provisioning anything.

### 5. Compare

```python
ranked = await tracker.compare(experiment.id, metric="accuracy")
summary = await tracker.summary(experiment.id, metric="accuracy")
# {"runs": 12, "best": 0.91, "worst": 0.62, "mean": 0.79, "stdev": 0.08}
```

**Variance matters as much as the mean.** A configuration that wins on average with a wide spread
is not obviously better than a stable second place, and reporting only the best run hides that
entirely. `summary` reports both.

### 6. Promote

```
candidate → shadow (mirrored traffic, no user impact)
          → canary (a traffic percentage, stable-hash assignment per user)
          → production
```

**Shadow** mirrors real traffic and discards the output. It catches latency and crash regressions
against real inputs, which a static evaluation set cannot.

**Canary** assigns by a stable hash of the user id, so a user does not flip between models mid
conversation — which would look like the assistant losing its mind rather than an experiment.

**Production** requires the approval. Exactly one production version exists per model: the
previous one is archived in the same operation, so there is never an ambiguous "which model is
live?".

```python
await registry.transition(
    "qwen2.5-7b-aura", "v3", to=ModelStage.CANARY, canary_percent=10
)
await registry.transition(
    "qwen2.5-7b-aura", "v3", to=ModelStage.PRODUCTION, approval_id=approval.id
)
```

### 7. Roll back

```python
await registry.rollback("qwen2.5-7b-aura", to_version="v2")
```

**Rollback requires no approval.** An incident must not wait on a reviewer. This asymmetry is
deliberate: advancing to a new state carries risk, reverting to a known-good one does not.

---

## Evaluation

`aura_core/evaluation/harness.py` serves three consumers with one implementation: CI regression
gates, the nightly refresh's recall comparison, and on-demand admin scoring.

**Retrieval:** recall@k, precision@k, MRR, nDCG.
**Answers:** substring coverage, token F1, citation presence, groundedness, refusal correctness.

Two properties are worth knowing:

**A case with no relevant documents asserts silence.** Scoring an out-of-corpus question as
recall 0 would penalise correct behaviour. The harness scores `empty_correct` instead — the
system passes by returning nothing.

**LLM-as-judge is opt-in and never a gate on its own.** A judge is another model with its own
biases and its own bill, and a gate that depends on one can be moved by prompt drift in the
judge rather than by a real change in the system under test.

```python
result = await RetrievalEvaluator(knowledge, k=5).run(suite)
ok, failures = RegressionGate().check(result, baseline)
```

Tolerances exist (2% on recall, 3% on nDCG) because these metrics are not perfectly stable:
retrieval ties break differently and sampled generation varies run to run. **A zero-tolerance
gate on a noisy metric is a gate that gets disabled after the third false alarm.**

---

## Local model lifecycle

```bash
AURA_MODEL_ALLOWLIST=qwen2.5:7b-instruct-q4_K_M,llama3.1:8b-instruct-q4_K_M
```

**Empty means pulls are disabled.** This fails closed on purpose: model weights are executable
artefacts fetched from a third-party registry, and "pull whatever the caller names" is a
supply-chain vector — exactly the shape of SEC-01.

| Operation | Notes |
| --- | --- |
| `list_models` | Installed models with size, quantisation, and whether resident |
| `pull_model` | Streams progress; allowlisted only. A pull takes minutes. |
| `warm_model` | Loads weights without generating — keeps a hot swap invisible to users |
| `delete_model` | Fails loudly. A silent success would let an operator believe a model was removed. |

---

## Routine schedule

| Cadence | Task |
| --- | --- |
| Daily | Check the refresh reported success; scan retrieval quality on the AI Quality dashboard |
| Weekly | Review blocked promotions and their `gate_failures` |
| Monthly | Review pending approvals; expire stale ones; run `curator.recommend` to see whether a fine-tune has become worthwhile |
| On embedding-model change | Reindex — vectors from one model are meaningless when searched with another |
| On model promotion | Watch shadow metrics for 24 h before canary; watch canary for 72 h before production |

---

Related: [ADR-008 Human-in-the-loop learning](../architecture/008-human-in-the-loop-learning.md) ·
[ADR-007 Versioned indexes](../architecture/007-versioned-indexes.md) ·
[Administrator Guide](administrator-guide.md) · [Maintenance Guide](maintenance-guide.md)
