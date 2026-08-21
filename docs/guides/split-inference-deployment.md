# Split Inference Deployment — gateway on your VPS, model on a Space

Running the AURA gateway on your own host while generation happens on a Hugging Face Space. This
is a supported topology and a deliberate trade, not a workaround — the gateway will refuse to talk
to a Space until you say so explicitly.

---

## 1. What this costs, before anything else

**It is not free.** A Docker Space needs a PRO subscription at **$9/month** even on `cpu-basic` —
only static Spaces are free — and that is *on top of* the VPS you still need for the gateway. See
[§2](#cpu-basic-is-not-free-for-this) for the arithmetic against simply buying a bigger VPS, which
wins on every axis except the first invoice.

Two of the platform's own rules also stop holding:

| Rule | Status | Why |
| --- | --- | --- |
| **20** — functional with no internet | **broken** | Generation is a network call to a third party. No internet, no answers. |
| **7** — all AI processing on our own infrastructure | **weakened** | Your container and your weights, on hardware you do not own or control. |

And the substantive cost, which matters more than the rule numbers: **every prompt and every
retrieved chunk of your documents travels to Hugging Face.** Retrieval still happens on your VPS,
but what retrieval *finds* is pasted into the prompt and sent onward. If the corpus is sensitive,
this topology is not for it.

What does **not** change: no hosted vendor model is ever called. Inference runs against a GGUF you
chose, in a container you built. That distinction is why this is gated rather than banned.

### The gate

```bash
AURA_ALLOW_REMOTE_INFERENCE=true
```

Without it the gateway raises `ConfigurationError` at construction and names what stops working.
With it, every boot logs a warning identifying the host — a trade accepted once should stay visible
to whoever operates the system later, who may not be the person who accepted it.

`api.openai.com` and friends stay refused with the flag on. The escape hatch is for rented compute
running your model, and there is a test asserting it cannot be used for anything else.

---

## 2. Create the Space

Everything needed is in [`spaces/inference/`](../../spaces/inference/). These commands need your
Hugging Face credentials, so they are yours to run.

Two notes that cost a round trip the first time:

- The `cli` extra **does not exist** — the CLI ships in the base `huggingface_hub` package. `pip
  install -U "huggingface_hub[cli]"` warns and installs the right thing anyway, but the extra is
  not a thing.
- `hf upload` is used below rather than `git push`. A Space is its own git repository, so the git
  route means initialising a **nested** repo inside this one, which works and then confuses every
  later `git status`. `hf upload` sends a directory and skips all of it.

### PowerShell

```powershell
pip install -U huggingface_hub
hf auth login

# The Space files live on this repo's branch — make sure they exist locally first.
git fetch origin
git checkout main          # or the branch carrying spaces/inference

# A key for the endpoint. Save it; you need the same value on the gateway.
$key = -join ((1..64) | ForEach-Object { '{0:x}' -f (Get-Random -Maximum 16) })
$key

# Create the Space with the secret already set, so it is never briefly unauthenticated.
hf repo create aura-inference --type space --sdk docker --flavor cpu-basic --secrets "LLAMA_API_KEY=$key"

# Upload the three files. This triggers the build.
hf upload <your-username>/aura-inference spaces/inference . --repo-type space
```

### bash / zsh

```bash
pip install -U huggingface_hub
hf auth login

git fetch origin && git checkout main
key=$(openssl rand -hex 32); echo "$key"

hf repo create aura-inference --type space --sdk docker --flavor cpu-basic --secrets "LLAMA_API_KEY=$key"
hf upload <your-username>/aura-inference spaces/inference . --repo-type space
```

`--secrets` sets `LLAMA_API_KEY` at creation, which matters: the container **exits at startup
without it**, and setting it up front means there is never a window where the Space is running
unauthenticated. A Space is public by default, and llama-server with no `--api-key` is an open
inference endpoint for anyone who finds the URL.

To set or rotate it later, use Space → Settings → Variables and secrets.

### `cpu-basic` is not free for this

**A Docker Space requires a PRO subscription ($9/month), even on `cpu-basic`.** Only *static*
Spaces are free. Creating one without PRO fails at the API with:

```
402 Payment Required
Static Spaces are free for everyone, but hosting Gradio and Docker Spaces
on free cpu-basic requires a PRO subscription.
```

This is worth stating loudly because it removes the main reason anyone reaches for this topology.
The Spaces *hardware* table does list CPU Basic at $0/hr, and that is accurate — the hardware is
free, the right to run a Docker Space on it is not. Reading the price table alone gives you the
wrong answer, which is how this guide originally came to claim a free tier.

`cpu-basic` is 2 vCPU / 16 GB and sleeps when idle. `--sleep-time -1` disables sleeping but needs
a paid flavor; on `cpu-basic` it is rejected rather than ignored.

**Recompute before continuing:**

| | Monthly | vCPU | Sleeps | Rules | Data egress | Hosts |
| --- | --- | --- | --- | --- | --- | --- |
| PRO + `cpu-basic` Space | **$9** | 2 | yes | 20 broken, 7 weakened | prompts + documents | the model only |
| Contabo Cloud VPS 3 | **$16.18** | 8 | no | all intact | none | the whole stack |

Note the last column. The Space hosts the model and nothing else, so its $9 is *additional to* the
VPS you still need for the gateway, Postgres, Qdrant and retrieval — not a replacement for it.

**$7.18/month is the entire difference**, and the cheaper option is worse on every other axis —
including that it still leaves you needing a VPS for the gateway, so it is $9 *on top of* whatever
you already pay rather than instead of it.

### ZeroGPU does not apply here, despite appearances

PRO's headline feature is ZeroGPU, and it is the obvious thing to reach for once you are paying
anyway. It does not work for this. From the
[ZeroGPU documentation](https://huggingface.co/docs/hub/spaces-zerogpu):

> Currently, ZeroGPU Spaces are exclusively compatible with the **Gradio SDK**.

A Docker Space cannot use ZeroGPU at all. Three further properties make it the wrong shape for a
chat backend even if the SDK restriction were lifted:

| Property | Consequence |
| --- | --- |
| GPU attached **per decorated function call**, released after | No persistent server holding weights in VRAM — which is exactly what `llama-server` is |
| **60 s** default runtime per call | Extensible via `duration=`, but the model is a request-scoped resource, not a service |
| **40 min/day** quota on PRO, then $1 per 10 min | At ~10 s GPU per turn that is roughly 240 turns/day before metered billing at $6/hour |

Reaching it would mean rewriting the Space as a Gradio app in Python with `@spaces.GPU`, then
building an OpenAI-compatible shim on top of Gradio — which is not what Gradio is, and would still
leave the quota and the queue.

**So for this use case PRO buys exactly one thing: permission to run a Docker Space on
`cpu-basic`.** Two sleeping vCPUs, hosting the model and nothing else.

### One feature to deliberately not use

PRO includes *"Inference Providers: 20× included credits across all inference providers."* That is
the hosted inference API — `api-inference.huggingface.co` — which is on this platform's banned list
and rejected at construction by `ProviderConfig`. It will sit there looking like the convenient
option. The `AURA_ALLOW_REMOTE_INFERENCE` gate deliberately does **not** unlock it, and there is a
test asserting so.

### Choosing a different model

Set these as Space variables (Settings → Variables and secrets), or pass `--env` at creation:

| Name | Default |
| --- | --- |
| `MODEL_REPO` | `bartowski/Qwen2.5-3B-Instruct-GGUF` |
| `MODEL_FILE` | `Qwen2.5-3B-Instruct-Q4_K_M.gguf` |

**Verify `MODEL_FILE` against the actual repo listing.** GGUF filenames vary between publishers,
and a wrong one is a 404 at startup rather than a helpful message.

### Your URL

```
https://<your-username>-aura-inference.hf.space
```

Confirm before wiring anything to it:

```bash
KEY=<the LLAMA_API_KEY you set>
BASE=https://<your-username>-aura-inference.hf.space

curl -sS "$BASE/v1/models" -H "Authorization: Bearer $KEY" | jq
curl -sS "$BASE/v1/chat/completions" -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"local","messages":[{"role":"user","content":"reply with one word: ready"}]}' \
  | jq -r '.choices[0].message.content'

# This MUST return 401. If it returns a completion, the endpoint is open — stop and fix it.
curl -s -o /dev/null -w '%{http_code}\n' "$BASE/v1/models"
```

First build takes a while, and the first request after a cold start downloads the model before it
answers. Both are expected.

---

## 3. Configure the gateway

On the VPS, in `deploy/.env`:

```bash
AURA_ALLOW_REMOTE_INFERENCE=true

AURA_LLM_PROVIDERS=remote
AURA_PROVIDER_REMOTE_KIND=openai_compatible
AURA_PROVIDER_REMOTE_BASE_URL=https://<your-username>-aura-inference.hf.space/v1
AURA_PROVIDER_REMOTE_MODEL=local
AURA_PROVIDER_REMOTE_API_KEY_ENV=AURA_PROVIDER_REMOTE_API_KEY
AURA_PROVIDER_REMOTE_IS_LOCAL=false
AURA_PROVIDER_REMOTE_TIMEOUT_S=300
AURA_PROVIDER_REMOTE_MAX_CONCURRENCY=2

# The key itself, matching the Space secret. Never in the provider block.
AURA_PROVIDER_REMOTE_API_KEY=<same value as LLAMA_API_KEY>
```

Notes on three of those:

- **`TIMEOUT_S=300`.** The default 60 s is tuned for a model on the same host. A cold Space has to
  download several gigabytes first, and a 60 s timeout turns that into a failed turn rather than a
  slow one.
- **`MAX_CONCURRENCY=2`.** Two CPU cores serve roughly one generation at a time; more concurrency
  makes everyone wait rather than raising throughput.
- **`IS_LOCAL=false`** is accurate here, and it matters — `LOCAL_FIRST` routing uses it as a hint.
  With one provider it changes nothing; with two it decides which is preferred.

Then remove the `ollama` service from `docker-compose.prod.yml`. Nothing points at it any more,
and it is the single largest memory consumer in the stack.

---

## 4. What this frees up, and what it does not

Dropping Ollama removes about 6 GB from the host's budget, which is the one change that makes a
small VPS plausible at all. On the 3.8 GB `srv1794216` the arithmetic is still tight:

| | |
| --- | --- |
| Velvet (already resident) | ~1.5 GB |
| Gateway, 2 workers | ~0.5 GB |
| **sentence-transformers (torch)** | **~1.0 GB** |
| Postgres + Qdrant + Redis | ~1.0 GB |
| **Total** | **~4.0 GB of 3.8 GB** |

It does not fit, and **embeddings are the reason**. Torch is the second-largest consumer in the
stack once the model is gone. Three ways out, in order of preference:

1. **Resize to the 24 GB plan** ($16.18/mo). Everything fits with room, and it is the option that
   does not trade quality for memory.
2. **A second Space for embeddings.** llama.cpp serves them with `--embeddings` and an embedding
   GGUF. Better retrieval than option 3, at the cost of another remote dependency and another
   thing that can sleep.
3. **`AURA_EMBEDDING_PROVIDER=local-embeddings`** (the `hash-embed-v1` default). Zero memory, no
   torch — and materially worse retrieval. It is the right choice for tests and the wrong one for
   a corpus you intend to search.

Skipping the observability tier saves a further ~1.5 GB, and it is the first thing to shed if you
must — it is the loss you can actually detect.

---

## 5. Verify the link

```bash
curl -fsS http://localhost:8080/health/ready | jq
curl -fsS http://localhost:8080/health | jq '.dependencies'
```

Then a real turn, end to end, and watch where the time goes:

```bash
curl -N -X POST http://localhost:8080/api/v3/chat/stream \
  -H "Authorization: Bearer $USER_TOKEN" -H 'Content-Type: application/json' \
  -d '{"message":"hello","session_id":"smoke"}'
```

Time-to-first-token now includes a network round trip to HF on top of prompt processing. Measure
`aura_llm_ttft_seconds` against the two numbers that matter, and keep them straight:
`AuraSlowTimeToFirstToken` fires at **p95 > 8 s**; the Performance Report's *target* is 3 s.

---

## 6. The APK

```bash
# from the repo root; injects https://$AURA_DOMAIN from deploy/.env
./deploy_client.sh
```

Or, equivalently:

```bash
cd aura_client
flutter pub get
dart run build_runner build --delete-conflicting-outputs
flutter build apk --release --dart-define=BACKEND_URL=https://$AURA_DOMAIN
```

**The app's URL is your VPS hostname (`AURA_DOMAIN`), never the Space and never a raw IP.** The Space is a backend dependency of the gateway;
the client must not know a model server address exists (Rule 30), and pointing it at the Space would
bypass auth, retrieval, memory and every safety guard in the pipeline.

That build also needs HTTPS — Android blocks cleartext by default — and a real signing config;
`android/app/build.gradle.kts` currently signs release builds with the debug keystore.

---

## 7. Honest summary

This gets you working generation without buying hardware. What it costs is Rule 20 outright, Rule 7
in substance, and your prompts and document content leaving your infrastructure. It also is not
free — a Docker Space needs PRO at $9/month — and on `cpu-basic` it sleeps, with each wake
re-downloading the model before answering.

Once the free tier turned out not to apply, the argument for this topology largely went with it.
$9/month for 2 sleeping vCPUs that host only the model, against $16.18 for 8 vCPUs that host
everything and keep every rule intact, is not a close comparison. It remains a reasonable way to
**prove the pipeline works end to end** if you already have PRO. It is not the cheap option it
looked like.

---

Related: [Hardware Capacity Report](../../AI_HARDWARE_CAPACITY_REPORT.md) ·
[Provisioning Runbook](provisioning-runbook.md) ·
[Host Migration Runbook](host-migration-runbook.md) ·
[Space source](../../spaces/inference/)
