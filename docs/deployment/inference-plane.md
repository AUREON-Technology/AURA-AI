# Inference Plane on Hugging Face (GPU)

Promotes the existing `deploy/spaces/inference-vllm/` Space from *failover* (as
`docs/guides/split-inference-deployment.md` §8 configures it) to **primary/sole** LLM inference
provider — a config change, not a new build. The Space's Dockerfile, entrypoint, and security
posture (refuses to boot without an API key, rejects unauthenticated requests) are unchanged;
read that Space's own `README.md`/`Dockerfile`/`entrypoint.sh` first, since this document only
describes what's different for the "primary" topology.

## 1. What the Inference Plane is (and isn't)

It is: `vllm/vllm-openai`, serving one model, exposing the OpenAI-compatible
`/v1/chat/completions`, `/v1/models` and (implicitly, vLLM's own) health surface. That's the
whole contract — see `docs/deployment/architecture.md` §7 for why no bespoke API was designed.

It is not: a place for auth, RBAC, agents, memory, RAG orchestration, tool policy, or any
application workflow. None of that exists in `deploy/spaces/inference-vllm/` today, and this
migration adds none of it — those stay on the Control Plane per the task's own §3 boundary.

ASR (Whisper), TTS (Piper), OCR (Tesseract) and reranking are **not** part of this plane; see
`architecture.md` §3 for why they're intentionally left running inside the Control Plane
container rather than moved here.

## 2. Choosing "primary" instead of "failover"

The only things that change from the existing §8 failover recipe:

| | Failover (existing, §8) | Primary (this migration) |
| --- | --- | --- |
| `AURA_LLM_PROVIDERS` | `local,hfspace` | `hfspace` |
| Ollama / `local` provider | still configured, still primary | removed — no CPU fallback exists |
| `AURA_PROVIDER_HFSPACE_IS_LOCAL` | `false` (ranked behind `local`) | `false` (still accurate: it's rented compute) — with only one provider configured this flag no longer affects routing, `ProviderRouter` has nothing to rank it against |
| Circuit breaker behavior | opens away from a healthy Space back to `local` | opens, and stays open — there is no second provider to fail over to. A GPU-plane outage is now a full outage, not a degradation. See `troubleshooting.md`. |

Everything else — the Dockerfile, the entrypoint's key-required check, `VLLM_MODEL`,
`VLLM_MAX_MODEL_LEN`, `VLLM_GPU_MEMORY_UTILIZATION` — is identical to the existing Space. This
is a deliberate minimal change: promoting to primary is a routing decision
(`AURA_LLM_PROVIDERS`), not a reason to touch the serving container.

**Consequence worth stating plainly, because §18/§27 of the task both call for honesty about
failure behavior:** removing the CPU/Ollama fallback means the Control Plane has exactly one
inference provider. Every one of the failure modes in `troubleshooting.md` §Inference-plane-down
becomes a full-system outage for generation, not a degraded-quality fallback. If that trade
isn't wanted, keep `docs/guides/split-inference-deployment.md` §8's `local,hfspace` topology
(Ollama on a VPS/GPU box you control as primary, this Space as insurance) instead of this
primary-only one. This document doesn't choose for you; it documents both and defaults to
neither.

## 3. Creating the Space

Identical to `split-inference-deployment.md` §8's "Create the Space" — same commands, same
`hf repo create ... --flavor <gpu-flavor> --secrets "VLLM_API_KEY=$key"`, same
`hf upload <user>/<space> deploy/spaces/inference-vllm . --repo-type space`. Choose an
always-on GPU flavor (not a sleeping one) for the primary topology even more strongly than that
section already recommends for failover-only use: with no fallback, a cold-start tax on every
wake is now the *only* thing generation latency has, not a rare failover latency.

## 4. Model selection for an L40S

`docs/reports/AI_HARDWARE_CAPACITY_REPORT.md` is the existing hardware-sizing reference. This
migration does not pin a specific model — that's an operational choice made when the Space is
created (`VLLM_MODEL` / `--served-model-name`), same as it already is for the failover Space.
Do not assume every AURA capability needs to stay resident in VRAM simultaneously: this Space
serves one chat model. If embeddings-on-GPU is wanted later, add a second Space rather than
multiplexing models in one vLLM process — matches the "no unnecessary microservices, but no
conflated ones either" balance the task asks for, and matches the pattern
`split-inference-deployment.md` §4 already uses.

## 5. Authenticated service-to-service connection

Already built, not added by this migration:

- `VLLM_API_KEY` (Space secret) / `AURA_PROVIDER_HFSPACE_API_KEY` (Control Plane secret,
  same value) — bearer-token auth on every request; `entrypoint.sh` refuses to start without it.
- `AuraSettings`'s `_reject_external_base_url` (`aura_core/config/settings.py`) refuses any
  `openai_compatible` base URL on a banned hosted-vendor host outright, and gates rented-compute
  hosts (Hugging Face included) behind `AURA_ALLOW_REMOTE_INFERENCE=true`, logging a warning on
  every boot while it's set — see `aura_core/config/external_inference.py` for the host lists,
  shared with `tests/architecture/test_no_external_inference.py` so the runtime check and the
  static fitness test can't disagree.
- TLS is inherent to `https://*.hf.space` — no separate certificate management needed on this
  side, unlike the Control Plane's own nginx/Let's Encrypt setup on the VPS.

Rotation: rotate `VLLM_API_KEY` in the Space's secrets, then update
`AURA_PROVIDER_HFSPACE_API_KEY` on Railway to match, then redeploy the `gateway` service (env
var changes need a restart to take effect — `ProviderConfig.api_key` is read lazily but the
provider is constructed once at `container.py` wiring time). There is a short window between the
two updates where the old key is rejected; size it like any credential rotation (stage the new
key as an *additional* accepted key on the Space first if zero-downtime rotation matters more
than this migration's scope).

## 6. Health, readiness, and what the Control Plane can see

vLLM's `/v1/models` endpoint (already what the co-located `vllm` Compose service's healthcheck
polls — see `deploy/docker-compose.prod.yml`'s `vllm.healthcheck`) is the readiness signal.
There is no separate GPU/CUDA/model-loaded telemetry endpoint beyond what vLLM itself exposes;
adding one would be new inference-plane functionality this deployment-only migration doesn't
build. The Control Plane's own `/health` endpoint's `dependencies` block reports whatever the
`hfspace` provider's circuit breaker currently believes (open/closed/probing) — see
`operations.md` §2.

## 7. CI

`.github/workflows/inference-plane-deploy.yml` validates `deploy/spaces/inference-vllm/`
(Dockerfile build, shellcheck on `entrypoint.sh`) on every PR touching that path, and — only on
`main`, only when `HF_TOKEN`/`HF_SPACE_ID` secrets are configured — pushes the directory to the
Space with `huggingface_hub`'s `upload_folder`. It never touches the Control Plane's Railway
deploy.

## 8. Vision: serving image understanding from this Space (ADR-031 Phase 6)

The gateway's image understanding (ADR-031) needs one provider that declares the image modality.
This Space is that provider: it serves one vision-language model for **both** chat and image
perception. That is decision D2 in `docs/plans/image-understanding-implementation-plan.md`: one
GPU instead of two, at the cost that text quality now depends on a VLM. That cost stays
unmeasured until the plan's P7-4 regression eval runs.

**Inference Plane side** (Space → Settings → Variables and secrets). `VLLM_API_KEY` is unchanged.

| Variable | Value | Why |
|---|---|---|
| `VLLM_MODEL` | **`Qwen/Qwen2.5-VL-7B-Instruct-AWQ`** on the current `a10g-small` (24 GB; ~6 GB AWQ weights leave room for a 16k KV cache). Owner decision 2026-11-17. Upgrade path: `Qwen/Qwen3-VL-8B-Instruct` on an L40S (48 GB, BF16), or `-FP8` on a 24 GB Ada/Hopper card (L4) | Apache-2.0 (Hub tag, not gated), supported by vLLM. Qwen2.5-VL-3B is excluded (`qwen-research` license). |
| `VLLM_MODEL_REVISION` | `536a35794df8831aa814970ee8f89eff577e7718` for the model above (the repo's head on 2026-11-17). Required: the entrypoint refuses to start without it | Without it, an upstream push silently changes what answers |
| `VLLM_MAX_MODEL_LEN` | `16384` | The gateway shrinks images to a 1536 px long edge, about 2,300 vision tokens each for a square image. Four images exceed 8192 on their own, and the router then excludes this provider (`context_window_too_small`) |
| `VLLM_MAX_IMAGES_PER_PROMPT` | `4` | Must be ≥ `AURA_VISION_MAX_IMAGES`. Any value > 0 also makes the entrypoint refuse remote media URLs (`--allowed-media-domains media.invalid`, redirects off). The gateway only ever sends base64 |

The entrypoint never passes `--allowed-local-media-path`, `--trust-request-mm-kwargs` or
`--enable-mm-embeds`. `tests/deployment/test_inference_contract.py` pins that.

**Control Plane side** (Railway variables, or `deploy/.env` for the VPS stack):

```bash
AURA_PROVIDER_HFSPACE_MODEL=<exactly VLLM_MODEL>
AURA_PROVIDER_HFSPACE_MODEL_REVISION=<exactly VLLM_MODEL_REVISION>
AURA_PROVIDER_HFSPACE_MODALITIES=text,image
AURA_PROVIDER_HFSPACE_CONTEXT_WINDOW=16384      # exactly VLLM_MAX_MODEL_LEN
AURA_VISION_ENABLED=true
AURA_VISION_MAX_IMAGES=4                        # <= VLLM_MAX_IMAGES_PER_PROMPT
AURA_PROVIDER_HFSPACE_HEALTH_PROBE=passive      # a sleeping Space: readiness must not wake it
```

Declare no pool. A generalist provider carrying the image modality is chosen for image turns
(the `vision` pool falls back to generalists), and text turns route to it exactly as before. A
hosted slot (`hosted1`) never receives an image, whatever it declares. The router refuses every
`third_party` provider for image requests, and image turns are SENSITIVE. `hfspace` is rented
compute running our own weights, so it is not third-party, and its ceiling is SENSITIVE.

**A Space that sleeps when idle** (owner decision 2026-11-17: pay for the GPU only while it is
used). Hugging Face wakes a sleeping Space on *any* request, so:

- Set `AURA_PROVIDER_HFSPACE_HEALTH_PROBE=passive` on the Control Plane. `/health/ready` then
  reports the Space from its circuit breaker (`"probe": "passive"`) and never calls it. Without
  this, a load-balancer health check or an uptime probe on `/health/ready` keeps the GPU awake
  around the clock (an `a10g-small` is roughly $1/hour). A passive provider can never hold the
  gateway ready on its own while a probed provider (`hosted1`) is down.
- Text turns route to `hosted1` first (higher `quality_tier`, and it may take SENSITIVE content).
  The Space wakes for image turns, and for text only when every hosted slot has failed.
- The first image after a quiet spell usually finds the Space waking. The turn then answers from
  OCR only (Tesseract is in the gateway image), and its vision step reports `degraded`: "the vision
  model is unavailable or still starting. Send it again in a minute". The request that got OCR is
  also the one that woke the Space.
- Cold start = container start + weight download + model load. On a Space without persistent
  storage the ~6 GB download repeats on every wake. **Persistent storage** (Settings → Persistent
  storage, mounted at `/data`) plus the Space variable `HF_HOME=/data/hf` keeps the weights between
  wakes. Measure the wake time once and record it in the Phase 6 report.
- *Settings → Sleep time* sets how long the Space stays awake after the last request.

**Order of operations:**
1. Change the Space variables and wait for `/v1/models` to list the new model.
2. Then change the Control Plane.
3. Only then set `AURA_VISION_ENABLED=true`.

Reversed, the gateway offers attachments to a text-only server, and image turns fail with
`AllProvidersFailedError` → 503.

**Evidence** (Phase 6 exit). Run from anywhere that can reach both:

```bash
AURA_VERIFY_GATEWAY=https://api.aureon-ai.tech AURA_VERIFY_TOKEN=<a user's access token> \
AURA_VERIFY_SPACE_URL=https://<owner>-<space>.hf.space AURA_VERIFY_SPACE_KEY=<VLLM_API_KEY> \
    python deploy/scripts/verify_vision.py
```

It writes `docs/reports/phase-6-vision-<timestamp>.txt`; commit that file. It checks:
- the gateway reports vision available;
- the Space refuses unauthenticated calls;
- the Space reads a base64 image;
- the Space refuses to fetch a remote URL;
- a `/chat` turn reads a randomly chosen number through `hfspace`;
- the stream reports the vision step;
- an image that says "create a task" cannot run a side-effecting tool;
- SVG and too many images are 422s.

It also records latency. Until a report with zero failures is committed, Phase 6 is built, not
proven.

**Quality** (plan Phase 7). Score the model itself on the 240-case labelled set. Then score
the whole gateway path, and for a bake-off compare the two runs. From `aura_backend/`:

```bash
export SPACE_KEY=<VLLM_API_KEY>
python -m evaluation.vision_eval run --target space --base-url https://<owner>-<space>.hf.space/v1 \
    --api-key-env SPACE_KEY --model <VLLM_MODEL> --revision <VLLM_MODEL_REVISION> \
    --label qwen3-vl-8b --hardware "HF L40S"
AURA_EVAL_TOKEN=<user access token> python -m evaluation.vision_eval run --target gateway \
    --base-url https://api.aureon-ai.tech --label prod-gateway
python -m evaluation.vision_eval compare ../docs/reports/vision-eval-*.json
```

Commit the `vision-eval-*.md`/`.json` pair. Pass `--save-baseline evaluation/suites/vision_baseline.json`
on the run the owner accepts, and `--baseline` on later runs, to gate against it.

**Rollback:**
- Set `AURA_VISION_ENABLED=false` on the Control Plane. Image turns then 503 and the client hides
  the attach button.
- To also revert the Space, set `VLLM_MODEL` back, `VLLM_MAX_IMAGES_PER_PROMPT=0`, and
  `VLLM_MAX_MODEL_LEN` / `AURA_PROVIDER_HFSPACE_CONTEXT_WINDOW` back to `8192`.
