# Two-plane deployment architecture

This records the ownership boundary for AURA's deployment split — **Control Plane** on
Railway, **Inference Plane** on a Hugging Face GPU Space — and the dependency map that
boundary is drawn from. It is deployment scoping, not a redesign: `aura_core`'s hexagonal
layering (`docs/architecture/001-hexagonal-core.md`), `ProviderRouter` (ADR-003), the kernel
pipeline (ADR-006), and every other ADR under `docs/architecture/` are unchanged by this split.

```
                         INTERNET
                            │
                    Cloudflare / HTTPS
                            │
                            ▼
                 ┌─────────────────────┐
                 │ AURA CONTROL PLANE   │   Railway
                 │  aura_backend/       │   (this repo, one service per process)
                 │  FastAPI · Auth      │
                 │  AURA Kernel         │
                 │  ProviderRouter      │
                 │  Agents/Tools/Safety │
                 │  Memory/RAG/RBAC     │
                 │  Celery / Beat       │
                 └──────────┬──────────┘
                            │ authenticated HTTPS
                            │ (OpenAICompatibleProvider)
                            ▼
                 ┌─────────────────────┐
                 │ AURA INFERENCE PLANE │   Hugging Face Space (GPU)
                 │  deploy/spaces/      │
                 │  inference-vllm/     │
                 │  vLLM · OpenAI API   │
                 └─────────────────────┘
```

## 1. This is a logical split, not a physical repo split

The two planes are already **independently deployable today without moving code into two
GitHub repositories**:

- A Hugging Face Space is its own git repository on the Hub, populated by `hf upload` (see
  `docs/guides/split-inference-deployment.md` §2). It has never lived inside this repo's git
  history in any sense that matters for deployment — `deploy/spaces/inference-vllm/` is only
  the *source* that gets uploaded to it.
- Railway deploys from a subdirectory of a monorepo (`aura_backend/` as the service's root
  directory) with its own build, its own environment, and its own release cadence — nothing
  Railway does touches the Inference Plane, and nothing pushed to `deploy/spaces/**` triggers a
  Control Plane redeploy (see the path filters in the two workflows under
  `.github/workflows/`).

A literal two-GitHub-repo split was considered and rejected for this pass: it requires
creating new repositories outside this session's granted GitHub scope (`anasibrahimgnedy-collab/aura-ai`
only), moving code with history-rewriting tools, and fixing cross-repo imports — a large,
hard-to-reverse action with no corresponding architectural benefit, since the dependency
direction described below already holds inside one repo. If a physical split is wanted later,
this document's ownership boundary is exactly the cut line to use, and history can be
preserved with `git subtree split` per plane at that time.

## 2. Ownership boundary (dependency map)

| Path | Plane | Notes |
| --- | --- | --- |
| `aura_backend/aura_core/**` | Control Plane | Domain, ports, engines — stdlib + ports only below `container.py` |
| `aura_backend/aura_core/llm/registry.py`, `router.py` | Control Plane | `ProviderRouter` and provider construction stay here (task requirement: routing logic is not moved to the GPU box) |
| `aura_backend/aura_core/llm/providers/openai_compatible.py` | Control Plane | This **is** the "SelfHostedRemoteInferenceProvider" — an existing `LLMProvider` adapter that speaks the OpenAI-compatible wire format over HTTPS to any self-hosted server, vLLM included. No new adapter was needed. |
| `aura_backend/aura_core/adapters/whisper_stt.py`, `aura_core/knowledge/reranking.py`, Piper/espeak-ng/tesseract in `aura_backend/Dockerfile` | Control Plane | ASR, reranking, TTS and OCR run **in-process inside the Control Plane container today** — they are not a separate GPU service. Moving them to the Inference Plane would be an architecture change, which is explicitly out of scope for a deployment-only migration (see §6 below). |
| `aura_backend/api/v2/**`, `aura_service.py`, `aura_backend/tasks.py`, `celery_app.py` | Control Plane | v3 gateway, the permanent v2 adapters (ADR-002), Celery workers/beat |
| `aura_backend/alembic/**` | Control Plane | Migrations run against Railway's managed Postgres |
| `deploy/docker-compose.prod.yml`, `docker-compose.dev.yml` | Shared / VPS | Unchanged. This split does not remove the existing Contabo VPS topology — see §5 (cutover). |
| `deploy/spaces/inference/` (llama.cpp, CPU) | Inference Plane (dev/cheap variant) | Pre-existing; unaffected |
| `deploy/spaces/inference-vllm/` (vLLM, GPU) | Inference Plane (production variant) | Pre-existing; this migration promotes it from *failover* to *primary/sole* provider — config only, see `inference-plane.md` |
| `docs/guides/split-inference-deployment.md`, `gpu-hybrid-setup.md` | Shared docs | Pre-existing topologies this migration builds on rather than replaces |

**No file moved.** Every path above already existed before this migration; what changed is
which ones get their own Railway service / HF Space deploy target and CI workflow.

## 3. What crosses the plane boundary, and what doesn't

Only LLM chat/completion generation crosses to the Inference Plane in this migration, via the
existing `openai_compatible` provider kind. Embeddings, reranking, ASR (Whisper), TTS (Piper)
and OCR (Tesseract) continue running inside the Control Plane container exactly as they do on
the VPS today — they are CPU-bound library calls, not a second GPU service, and no ADR or
CLAUDE.md describes a plan to split them out. Section 9 of the source task lists these as
capabilities the Inference Plane "must" expose; this document records the deliberate scoping
decision to leave them as they are rather than invent a new GPU service for them, which would
be new architecture, not a deployment change (constraint in §32/33 of the task).

If embeddings-on-GPU is wanted later, `docs/guides/split-inference-deployment.md` §4 already
describes adding a second Space for that (`llama.cpp --embeddings`) — same pattern, not built
by this migration.

## 4. Provider flow (unchanged responsibilities)

```
User request → aura_service (FastAPI) → AIKernel → ProviderRouter → OpenAICompatibleProvider
    → HTTPS (Bearer token) → Hugging Face Space → vllm-openai → GPU (L40S or chosen flavor)
```

`ProviderRouter` (`aura_core/llm/router.py`) keeps every responsibility ADR-003 gives it:
provider/model selection, the five routing policies, per-provider circuit breaker, and the
"failover only before the first streamed token" rule. None of that moved. The Inference Plane
does exactly what `deploy/spaces/inference-vllm/entrypoint.sh` already does: load the model,
expose `/v1/chat/completions`, `/v1/models`, refuse to boot without an API key.

## 5. Relationship to the existing VPS deployment

The Contabo VPS + Docker Compose + Ollama topology (`deploy/docker-compose.prod.yml`,
`.github/workflows/deploy-backend.yml`) is **not removed by this migration**. Cutting production
traffic over to Railway + a GPU Space is an operational decision (DNS, decommissioning paid
infrastructure) with real cost and outage risk if done before the new path is proven — task
§17 itself requires health/contract tests to pass first. This migration adds the Railway/HF
path as a second, fully configured option; `docs/deployment/rollback.md` and
`docs/deployment/operations.md` describe how to run the cutover once you've verified it, and
what to fall back to if it fails.

## 6. Model registry — single source of truth

One source of truth for model identity/capability metadata, to avoid the two conflicting
registries §14 warns about:

| Field | Source of truth | Consumed by |
| --- | --- | --- |
| `model_id`, `context_window`, `quality_tier`, `max_concurrency`, `is_local` | `ProviderConfig` (Control Plane env: `AURA_PROVIDER_HFSPACE_*`) | `ProviderRouter` — routing/failover decisions |
| Actual served model, `--max-model-len`, quantization, GPU memory fraction | Inference Plane env: `VLLM_MODEL`, `VLLM_MAX_MODEL_LEN`, `VLLM_GPU_MEMORY_UTILIZATION` (`deploy/spaces/inference-vllm/entrypoint.sh`) | vLLM server itself |

These two are **necessarily duplicated** (one process's config can't read the other's env), so
`aura_backend/tests/deployment/test_inference_contract.py` pins the documented defaults against
each other in CI — a mismatch (e.g. Control Plane assuming an 8192 context window while the
Space serves 4096) fails the default `pytest` suite rather than surfacing as a production
truncation bug.

## 7. Versioning

| Plane | Version | Compatible with |
| --- | --- | --- |
| Control Plane | `v3.x` (`api/v3.py`, unchanged) | Inference API contract `v1` |
| Inference Plane | `v1.x` (OpenAI-compatible surface vLLM serves natively: `/v1/chat/completions`, `/v1/models`) | Control Plane `AURA_PROVIDER_HFSPACE_*` config `>= v1.x` |

No new versioning scheme was introduced: the Inference Plane's contract is the OpenAI
Chat Completions wire format vLLM already implements, which is why no bespoke client or
serializer was written for it — `OpenAICompatibleProvider` is a generic client for that format,
not an AURA-specific one.
