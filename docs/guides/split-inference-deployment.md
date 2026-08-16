# Split Inference Deployment — gateway on your VPS, model on a Space

Running the AURA gateway on your own host while generation happens on a Hugging Face Space. This
is a supported topology and a deliberate trade, not a workaround — the gateway will refuse to talk
to a Space until you say so explicitly.

---

## 1. What this costs, before anything else

Two of the platform's own rules stop holding:

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

`--flavor cpu-basic` is the free tier: 2 vCPU, 16 GB, and it sleeps when idle. `--sleep-time -1`
disables sleeping but requires a paid flavor; on free it is rejected rather than ignored.

Optional, same screen, if you want a different model:

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
cd aura_client
flutter pub get
dart run build_runner build --delete-conflicting-outputs
flutter build apk --release --dart-define=BACKEND_URL=https://<your-vps-domain>
```

**The app's URL is your VPS, never the Space.** The Space is a backend dependency of the gateway;
the client must not know a model server address exists (Rule 30), and pointing it at the Space would
bypass auth, retrieval, memory and every safety guard in the pipeline.

That build also needs HTTPS — Android blocks cleartext by default — and a real signing config;
`android/app/build.gradle.kts` currently signs release builds with the debug keystore.

---

## 7. Honest summary

This gets you working generation without buying hardware. What it costs is Rule 20 outright, Rule 7
in substance, and your prompts and document content leaving your infrastructure. On the free tier it
also sleeps, and each wake re-downloads the model before answering.

It is a very good way to **prove the pipeline works end to end** before spending anything. As a
place to serve real users with a real corpus, a $16/month resize and a model on your own box is
better on every axis except the first month's invoice.

---

Related: [Hardware Capacity Report](../../AI_HARDWARE_CAPACITY_REPORT.md) ·
[Provisioning Runbook](provisioning-runbook.md) ·
[Host Migration Runbook](host-migration-runbook.md) ·
[Space source](../../spaces/inference/)
