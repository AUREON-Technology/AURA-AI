# MLOps Guide

Training, fine-tuning, evaluation, and promotion. Covers both learning loops from
[ADR-008](../architecture/008-human-in-the-loop-learning.md):

- **Loop A — knowledge.** Runs nightly and unattended. New documents, re-embedding, index
  promotion. Gated automatically; no human in the path.
- **Loop B — model.** Runs when someone decides to. Curated examples, fine-tuning, evaluation,
  promotion. **Requires a recorded human approval,** and the registry refuses the transition
  without one. The gateway itself never changes weights. Real LoRA training happens only on the
  opt-in `worker-training` service, and it has been proven only on a tiny test model. See
  [What training does, and what it does not](#what-training-does-and-what-it-does-not).

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
per source:  fetch → validate → dedupe → assess (writes nothing) → content gate → apply
afterwards:  index current for the embedder?  yes → done
                                              no  → rebuild a new index → gates → promote
```

**Nothing from a source reaches the live corpus unless that source passes the content gates**
(quarantine rate ≤ 10 %, updated documents shrink ≤ 40 %); a failing source is held and retried
next run while the others apply
([ADR-008 amendment](../architecture/008-human-in-the-loop-learning.md)). The index is rebuilt only
when the embedder or layout changed: it is identified by
`(logical_name, schema_version, embedding_model_id, dimension)`, so that candidate is a separate
physical index, and promotion is an alias repoint — O(1), and reversible in the same operation
([ADR-007](../architecture/007-versioned-indexes.md)). With an unchanged embedder, new documents are
embedded into the live index and the report says `index_rebuild: not_needed`.

Index gates (only on that rebuild):

| Gate | Default | Catches |
| --- | --- | --- |
| `gate_max_recall_drop` | 0.02 | A rebuild that retrieves worse than what it replaces (needs an evaluator; none is wired yet) |
| `gate_max_error_rate` | 0.05 | An embedding backend degrading part-way through |
| `gate_max_count_delta` | 0.40 | A rebuild whose chunk count collapsed against a *readable* index |
| `min_chunks` | 1 | A failed build promoting cleanly |

A held source is reported under `held_sources` and raises `AuraKnowledgeSourcesHeld` after two
runs; a failed fetch or a blocked rebuild raises `AuraKnowledgeStale` after 48 hours. Until then
nothing is lost: the live corpus is untouched by a held source, which is why a held source or a
blocked rebuild is a ticket rather than an incident.

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

#### What training does, and what it does not

Read this before believing a job status. As of 2026-09-25 (GAP-017 / GAP-018):

| Claim | True? | Where it is shown |
| --- | --- | --- |
| The gateway changes model weights | **No, never.** Both compose files pin the gateway to `AURA_FINETUNE_TRAINER=scripted`. Every job it runs ends `not_trained` and registers nothing. | `learning/finetune.py` `ScriptedTrainer`; `deploy/docker-compose.{prod,dev}.yml` |
| `LocalLoraTrainer` does real LoRA | **Yes.** It loads the base causal LM with `transformers`, wraps it with a PEFT LoRA adapter, trains on the approved dataset, and saves the adapter. | `adapters/local_lora_trainer.py`; `tests/training/test_lora_weights_change.py` |
| The mechanism is proven | **Only on small models, on CPU, with a 4-row dataset.** Two runs. `hf-internal-testing/tiny-random-gpt2`: all 5 `lora_B` tensors went from zero to non-zero, logits changed versus the base (max abs diff 0.56), and dataset loss fell 6.915 → 6.811. `HuggingFaceTB/SmolLM2-135M-Instruct` (rev `12fd25f7`, chat-template path): 60/60 `lora_B` tensors changed, and dataset loss fell 2.875 → 0.010 in 16 steps (70 s). That is memorisation of 4 rows, not a quality result. | opt-in `pytest tests/training -m training -s` (`AURA_TRAIN_TEST_BASE_MODEL=` selects the base) |
| A real AURA base model has been fine-tuned | **No.** No run against the production base, and no report in `docs/reports/`. This is GAP-017's exit condition, and it is still open. | — |
| A trained adapter serves traffic | **No.** The artefact is a PEFT adapter *directory*: not merged, not converted to GGUF, not created in Ollama. The kernel won't send a path as a model name, so a promoted adapter falls back to the provider default. | `AIKernel._servable_model_tag` |
| Training is evaluated against the production model | **No.** The only quality numbers are loss on the training dataset before and after (`dataset_loss_before` / `dataset_loss_after`). That shows the adapter fitted its data. It says nothing about answer quality. | — |
| `aura_core/adapters/ml/` fine-tunes an LLM | **No.** It builds a randomly initialised 2-class classification transformer and never loads a base model. Nothing in production imports it any more. | package docstring |

#### Job outcomes

A job ends in exactly one of these states:

| Status | Meaning | Model version registered? |
| --- | --- | --- |
| `succeeded` | The trainer reported `weights_changed=True` **and** named the artefact (`artefact_uri` + `artefact_digest`). | Yes, `CANDIDATE`, with `artefact_sha256=` and `base_revision=` in its notes |
| `not_trained` | The trainer finished but, by its own account, changed no weights (the `scripted` default, or a LoRA run whose adapter did not move). The reason is in `error`. | **No.** Nothing exists to promote |
| `failed` | The trainer raised: missing training dependencies, an Ollama tag as base model, an unloadable base, an empty or malformed dataset, a bad hyperparameter, a diverged loss. The exception is in `error`. | **No** |

`TrainResult.weights_changed` defaults to `False`. A trainer must assert, positively, that it changed weights. There is no fallback artefact anywhere. The old trainer turned every exception into a `modelfile://` "success", and that path is gone. `tests/unit/test_finetune_honesty.py` pins this.

#### Running real training

Training runs outside the request path. The gateway only enqueues.

```python
job = await finetune_jobs.enqueue(approval_id=approval.id)
done = await finetune_jobs.run(job.id)  # or tasks.finetune_job_task.delay(job.id)
```

`enqueue` and `run` both call `ApprovalService.require(approval_id,
expected_digest=dataset_digest)` **before** the trainer runs. A pending approval, an expired
approval, or a digest mismatch raises. This is the ADR-008 gate.

| Trainer | Where | Dependencies |
| --- | --- | --- |
| `ScriptedTrainer` (`AURA_FINETUNE_TRAINER=scripted`, the default) | gateway, every worker except `worker-training`, pytest | none; trains nothing |
| `LocalLoraTrainer` (`AURA_FINETUNE_TRAINER=local`) | `worker-training` only (compose profile `training`) | `requirements-train.txt` (peft, accelerate), installed only when the image is built with `AURA_TRAINING_DEPS=true` |

Any other `AURA_FINETUNE_TRAINER` value is a `ConfigurationError` at startup. It used to fall
back to `scripted` silently.

To run a real job:

1. Start the worker: `docker compose -f deploy/docker-compose.prod.yml --profile training up -d worker-training`.
   It consumes only the Celery `training` queue. Neither `finetune_job_task` nor
   `drain_finetune_queue_task` goes to `learning` any more, because `worker-ingestion` consumes
   `learning` and cannot train. Without this profile, a queued job stays `queued`.
2. Launch with `POST /api/v3/admin/learning/jobs` and `{"approval_id": ..., "base_model":
   "<HF repo id>", "run_inline": false}`. `run_inline: true` (the API default) runs the
   gateway's `scripted` trainer and ends `not_trained`.
3. The base model must be a Hugging Face repo id or a local transformers checkpoint directory.
   **An Ollama tag (`qwen2.5:3b-instruct-q4_K_M`) is refused**, because a quantised GGUF inside
   Ollama has no trainable weights. Use the matching HF checkpoint (e.g. `Qwen/Qwen2.5-3B-Instruct`)
   on a host that can hold it.
4. On a CPU-only host, also set `LEARNING_FINETUNE_ALLOW_CPU=true`. The base must match
   `LEARNING_FINETUNE_CPU_BASES`, which defaults to `HuggingFaceTB/SmolLM2-135M*`,
   `Qwen/Qwen2.5-0.5B*` and `hf-internal-testing/tiny-random-*`. A 3B model in fp32 does not fit
   the worker's 4G memory limit.

Hyperparameters come from the job's `parameters`. They are typed fields on `FineTuneJobSpec`
with defaults, and bad values fail the job at the boundary:

| Key (alias) | Default | |
| --- | --- | --- |
| `epochs` | 3 (engine default) | |
| `rank` (`lora_rank`) | 16 (engine default) | |
| `lr` (`learning_rate`) | 3e-4 (engine default) | |
| `alpha` (`lora_alpha`), `dropout` (`lora_dropout`) | 16, 0.05 | |
| `batch_size`, `max_steps`, `max_seq_len`, `seed` | 1, none, 512, 0 | |
| `target_modules` | PEFT's per-architecture default | |
| `revision` (`base_model_revision`) | resolved at load and recorded | |

The loss is causal-LM cross-entropy on the assistant/completion tokens only. Prompt tokens are
masked. `chatml`, `alpaca` and `openai` dataset rows are accepted.

A successful run writes `<AURA_FINETUNE_WORK_DIR>/<job_id>/adapter/`
(`adapter_model.safetensors`, `adapter_config.json`) and `training_manifest.json` beside it. The
manifest holds the base model and resolved revision, the dataset digest, the adapter sha256, the
hyperparameters, the torch/transformers/peft versions, and these metrics: `steps`,
`epochs_completed`, `train_loss_first`/`train_loss_last`, `dataset_loss_before`/`dataset_loss_after`,
`lora_weight_delta_l2`, `trainable_params`, `total_params` and `duration_s`.

To verify locally, use a separate venv. Never install the training deps into the gateway's venv:

```bash
pip install --require-hashes --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.lock -r requirements-train.lock
pip install -r requirements-dev.txt
pytest tests/training -m training -s   # downloads tiny-random-gpt2; skips without peft/network
```

`requirements-train.txt` constrains itself to `requirements.lock`, so the training image cannot
drift from the gateway's torch/transformers pins. What is installed is `requirements-train.lock`,
compiled from it with hashes (the command is in `requirements-train.txt`'s header). Contabo-style CPU hosts should train elsewhere
and use `POST /api/v3/admin/models/import` to register a local Ollama tag as `CANDIDATE`.

Every job is also tracked as an experiment run. A `not_trained` job closes its run as `failed`,
with the reason attached, so an experiment list never shows a "completed" run that produced no
model.

```python
experiment = await tracker.experiment("finetune-qwen")
run = await tracker.start_run(
    experiment.id,
    parameters={"method": "lora", "rank": 16, "lr": 3e-4, "epochs": 3},
    dataset_digest=recommendation.dataset_digest,
    base_model="Qwen/Qwen2.5-0.5B-Instruct",
    code_version="git:deadbeef",
)
await tracker.log_metrics(run.id, {"train_loss": 0.42}, step=100)
await tracker.finish_run(run.id)
```

`dataset_digest`, `base_model`, and `code_version` are the reproducibility inputs. Without them,
"which data produced this model?" has no answer six months later, and someone always asks it at
the worst possible moment.

LoRA is the only method implemented. Full fine-tuning of a 7B model needs multiple high-memory
GPUs. LoRA adapters train on one GPU, are a few dozen megabytes, and can be swapped without
redeploying the base weights. Architecture specifications (`aura_core/ml/nn.py`) are declared as
**data**, so parameter counts and memory footprints can be computed with no torch installed. An
operator can check whether a model fits before provisioning anything. Those specs are sizing
tools, not the trainer.

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

**Production** requires a **MODEL_PROMOTION** approval bound to ``model_id@version``
(not the training dataset digest). Reusing a FINETUNE approval as a production
token is refused. Exactly one production version exists per model: the previous one
is archived in the same operation.

```python
promo = await approvals.request(
    kind=ApprovalKind.MODEL_PROMOTION,
    summary=f"Promote {version.key}",
    artefact_digest=version.key,  # aura-ft@v3
)
await approvals.decide(promo.id, approved=True, by="ops@example.com")
await registry.transition(
    "aura-ft", "v3", to=ModelStage.PRODUCTION, approval_id=promo.id
)
```

The kernel resolves ``CompletionRequest.model`` from ``ModelRegistry.assign`` when
unset, so a canary/production artefact serves traffic **only if it is an inference model tag**
(e.g. an imported Ollama tag). A path or URI artefact, which includes every adapter
``LocalLoraTrainer`` produces, is ignored with a warning, and the provider default serves.
Promoting a trained adapter therefore changes nothing a user sees until a merge/convert/serve
step exists, and that step is not built. Shadow versions receive a fire-and-forget mirrored
completion whose tokens are never returned to the user.

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
