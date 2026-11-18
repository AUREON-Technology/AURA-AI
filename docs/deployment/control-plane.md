# Control Plane on Railway

Deploys `aura_backend/` — the FastAPI gateway, Celery workers, and Beat scheduler — to Railway.
Nothing in `aura_core`'s layering, the kernel pipeline, or `ProviderRouter` changes for this;
see `docs/deployment/architecture.md` for the ownership boundary this rests on.

## 1. What's already Railway-ready, unchanged

- `aura_backend/Dockerfile`'s default `CMD` already reads `$PORT` (`uvicorn aura_service:app
  --host 0.0.0.0 --port ${PORT:-8000} ...`) — Railway injects `PORT`; no Dockerfile change was
  needed.
- `aura_backend/infrastructure/secret_hydration.py` only populates an `AURA_*` variable from its
  `*_FILE` counterpart **when the variable isn't already set directly** — Railway's plain
  environment-variable secrets work with this module completely unmodified. The Docker Compose
  `*_FILE` pattern (Swarm secrets mounted at `/run/secrets/...`) is a VPS-specific detail, not a
  requirement `AuraSettings` imposes.
- The readiness endpoint the VPS healthcheck already polls, `/health/ready`, is what Railway's
  healthcheck should poll too — it reflects Postgres/Redis/Qdrant/inference dependency health,
  not just process liveness (see `docs/deployment/operations.md` §2 for what it reports).

## 2. Railway services to create

Railway has no first-class "one service, many processes" primitive for a Celery-based stack —
create **four services from the same repo**, each with `aura_backend/` as its Root Directory
(so all four share one build) and a different Start Command override:

| Service | Start Command | Healthcheck |
| --- | --- | --- |
| `gateway` (web) | *(default image CMD — no override)* | `/health/ready`, see `railway.json` |
| `worker-realtime` | `celery -A tasks worker -Q realtime -c 4 --loglevel=info --max-tasks-per-child=200` | none (not a web service) |
| `worker-ingestion` | `celery -A tasks worker -Q ingestion,learning -c 2 --loglevel=info --max-tasks-per-child=50` | none |
| `beat` | `celery -A celery_app beat --loglevel=info` | none |

These are exactly the commands already run by `worker-realtime`, `worker-ingestion`, and `beat`
in `deploy/docker-compose.prod.yml` — copied, not redesigned. `worker-training` (the fourth
Celery queue in Compose) is a fifth optional service on the same pattern if learning-loop
training is enabled; omit it if not.

Every service must set `AURA_SANDBOX_ENABLED=false` unless the Engineer-agent workspace feature
(ADR-013/014) is explicitly wanted — that feature depends on `docker-socket-proxy` and a bind
mount that Railway's container model does not provide, and is out of scope here; see §5.

`railway.json` (added at `aura_backend/railway.json`) configures the `gateway` service's build
and healthcheck. Railway does not support declaring four services with different start commands
in one config file today — the other three are dashboard/CLI service creations pointed at the
same repo, using the commands in the table above.

**Backups are a separate, sixth service.** Nothing above backs up Railway's Postgres or Redis,
or Qdrant Cloud. A cron service built from `deploy/backup/railway.Dockerfile` does it nightly, to
Backblaze B2 and Cloudflare R2: see [backups.md](backups.md).

## 3. Environment variables (Control Plane)

Set these as Railway **service variables** (encrypted at rest by Railway, never committed).
Names match `deploy/docker-compose.prod.yml`'s `gateway` block exactly — no new configuration
system was invented, per the task's own instruction to reuse project conventions.

Direct values (no `_FILE` suffix — see §1):

```bash
AURA_ENVIRONMENT=production
AURA_LOG_FORMAT=json
AURA_METRICS_ENABLED=true
AURA_VECTOR_BACKEND=qdrant
AURA_DOCUMENT_BACKEND=postgres
AURA_MEMORY_BACKEND=postgres
AURA_CACHE_BACKEND=redis
AURA_TRUSTED_PROXY_COUNT=1          # Railway's own edge proxy is the one trusted hop
DEBUG_MODE=false

# Embedder for knowledge retrieval and memory recall. REQUIRED: the code default is the
# non-semantic hash embedder used by the test suite, and AuraSettings.validate() refuses to
# boot staging/production with it (audit FSA-031). The image ships sentence-transformers;
# /health/ready reports the live value under "embedding" ("semantic": true expected).
# Changing model or dimension after documents exist means re-indexing (see the knowledge
# engine's index manager); the dimension must match the model (bge-small = 384).
AURA_EMBEDDING_PROVIDER=sentence-transformers
AURA_EMBEDDING_MODEL=intfloat/multilingual-e5-small
AURA_EMBEDDING_DIMENSION=384
# Arabic search (docs/plans/arabic-language_plan.md, stage 4): the "either" relevance gate.
AURA_KNOWLEDGE_RERANK_PROVIDER=either
AURA_KNOWLEDGE_RERANK_MODEL=cross-encoder/mmarco-mMiniLMv2-L12-H384-v1
# After changing the embedder, rebuild the index once (reads refuse an index built by another
# model until then): `railway ssh`, then `python scripts/rebuild_index.py` (--dry-run first).
# Pin the weights to a Hub commit (audit FSA-110): the revision is part of the index identity,
# so changing it builds and promotes a new index rather than silently re-reading the live one.
# AURA_KNOWLEDGE_RERANK_MODEL_REVISION and AURA_VOICE_STT_MODEL_REVISION pin the cross-encoder
# and Whisper the same way; /health/ready reports all three. Whisper loads at startup in
# production-like environments (AURA_VOICE_STT_WARM_ON_STARTUP overrides).
AURA_EMBEDDING_MODEL_REVISION=<commit SHA from the model repo's Files and versions>

AURA_DATABASE_URL=<Railway Postgres plugin's connection string>
# Connection budget (audit FSA-073). Every uvicorn worker holds one asyncpg pool plus the legacy
# SQLAlchemy pool: AURA_POSTGRES_POOL_MAX_SIZE (10) + AURA_LEGACY_DB_POOL_SIZE (5) +
# AURA_LEGACY_DB_MAX_OVERFLOW (10) = 25 connections per worker by default, so AURA_WORKERS=4 can
# open 100 -- plus any Celery workers (per prefork child). Size these against the Railway
# Postgres plan's connection limit, leaving room for the pre-deploy `alembic upgrade head`.
# AURA_POSTGRES_COMMAND_TIMEOUT_S (60) cancels a hung statement; idle pooled connections close
# after AURA_POSTGRES_MAX_INACTIVE_CONNECTION_LIFETIME_S (300).
# Also what lets a paired desktop (aura_hostd) be reached from every gateway worker: the
# host-agent relay (ADR-011 amendment, 2026-11-08) is on whenever this and AURA_JWT_SECRET are
# set. Without it, the image's default 4 workers each see only their own desktop connections, so
# set AURA_WORKERS=1 instead. AURA_HOST_AGENT_RELAY=false turns the relay off.
# This one URL is also the Celery broker and result backend (celery_app.broker_url). Before
# 2026-11-16 Celery read only REDIS_HOST (default localhost, port 6379 fixed), so on this target
# every .delay() failed and every /admin/jobs/{id} read UNKNOWN unless REDIS_HOST was also set.
AURA_REDIS_URL=<Railway Redis plugin's connection string, or an external Redis>
AURA_QDRANT_URL=<Qdrant Cloud or self-hosted Qdrant URL — Railway has no Qdrant plugin>
AURA_QDRANT_API_KEY=<Qdrant API key>
# Recorded decision for this target (audit FSA-018 / CB-22): with an off-host Qdrant (any dotted
# public hostname, e.g. Qdrant Cloud) no readable chunk text or memory subject is written into
# vector payloads; retrieval resolves the text from Postgres. /health/ready reports it under
# "vectors.payload_content". AURA_KNOWLEDGE_VECTOR_PAYLOAD_CONTENT=true|false overrides the rule.
# Existing payloads keep their text until the index is rebuilt (knowledge refresh / re-index).
AURA_JWT_SECRET=<random>
AURA_SECRETS_ENCRYPTION_KEY=<random>

# Inference Plane connection — see inference-plane.md for the Space side
AURA_ALLOW_REMOTE_INFERENCE=true
AURA_LLM_PROVIDERS=hfspace
AURA_PROVIDER_HFSPACE_KIND=openai_compatible
AURA_PROVIDER_HFSPACE_BASE_URL=https://<space>.hf.space/v1
AURA_PROVIDER_HFSPACE_MODEL=<model id served by vLLM>
AURA_PROVIDER_HFSPACE_API_KEY_ENV=AURA_PROVIDER_HFSPACE_API_KEY
AURA_PROVIDER_HFSPACE_API_KEY=<same value as the Space's VLLM_API_KEY secret>
AURA_PROVIDER_HFSPACE_IS_LOCAL=false
AURA_PROVIDER_HFSPACE_CONTEXT_WINDOW=<must match VLLM_MAX_MODEL_LEN on the Space>
AURA_PROVIDER_HFSPACE_TIMEOUT_S=45
AURA_PROVIDER_HFSPACE_HEALTH_TIMEOUT_S=30   # not 5 — a Space sleeps; see below
AURA_PROVIDER_HFSPACE_MAX_CONCURRENCY=8
AURA_LLM_TIMEOUT_S=60
# Image understanding (ADR-031): only once the Space serves a vision-language model -- see
# inference-plane.md §8 for the matching Space variables and the order to change them in.
# AURA_PROVIDER_HFSPACE_MODALITIES=text,image
# AURA_PROVIDER_HFSPACE_MODEL_REVISION=<the Space's VLLM_MODEL_REVISION>
# AURA_VISION_ENABLED=true

AURA_DOMAIN=<railway-issued or custom domain>
ALLOWED_ORIGINS=https://<AURA_DOMAIN>
```

### Why `AURA_PROVIDER_HFSPACE_HEALTH_TIMEOUT_S` is not left at its default

A Hugging Face Space scales to zero. The liveness probe's default deadline is 5 seconds, which is
right for an endpoint that is always up and wrong for one that has to start first — measured on
this deployment after ~9.5 hours idle (EV-28 in `docs/reports/AURA_PRODUCTION_TEST_EVIDENCE.md`):

```
10:44:17Z  healthy=False  5143.8 ms  ReadTimeout     20 consecutive failures,
   ...                                               every one at a flat ~5150 ms
10:49:48Z  healthy=False  5156.8 ms  ReadTimeout
10:50:03Z  healthy=True    220.3 ms  ok              then 11 consecutive successes
10:51:49Z  healthy=True    212.6 ms  ok
```

Nothing changed at that boundary except that the Space finished waking. At 5 seconds the probe
**cannot observe the recovery at all**: it fails identically whether the Space is seconds from
ready or permanently dead, so `/health/ready` reports the same `healthy: false` for "asleep,
warming" as for "broken". An operator cannot tell them apart, and an orchestrator acting on that
signal pulls a service that was about to be fine.

Two things worth knowing before choosing a value:

* **A longer probe means `/health/ready` can block for that long** when the Space is cold, since
  the endpoint waits on the probe. That is the cost, and it is why 30 rather than 300.
* **It does not affect deploys.** `aura_backend/railway.json` sets `healthcheckPath` to
  `/health/live`, which has no dependency checks — deliberately, because readiness 503s on any
  unhealthy dependency and a deploy could otherwise never pass while inference was down.

What this does *not* fix: whether a user's first turn after an idle period succeeds. A turn goes
through `AURA_PROVIDER_HFSPACE_TIMEOUT_S` / `AURA_LLM_TIMEOUT_S`, not this probe. Raising the
probe timeout makes the health signal honest; it does not make a cold start fast.

On the VPS target `deploy/docker-compose.prod.yml` forwards this variable with a default of 30
(Compose only forwards variables a service names, so it had to be added there explicitly —
`aura_backend/tests/unit/test_hfspace_health_timeout_plumbing.py` pins that). Railway takes its
environment from the dashboard instead, so on this target it has to be set by hand.

Full list of every variable the gateway reads: `deploy/docker-compose.prod.yml`'s `gateway.environment`
block (lines ~234–394) is the canonical reference — copy names, not VPS-specific values
(hostnames like `postgres`/`redis`/`qdrant` don't resolve on Railway; use the connection strings
Railway's Postgres/Redis plugins or your external Qdrant provide instead).

### Optional: hosted open-weight providers (ADR-030)

The same slots `deploy/docker-compose.prod.yml` pre-wires (`x-hosted-providers`) apply here,
set in the Railway dashboard instead of `.env`. The full walkthrough, with example values for
Groq, Cloudflare Workers AI and OpenRouter, is the "Hosted open-weight providers" block in
`deploy/.env.example`.

```
AURA_ALLOW_HOSTED_INFERENCE=true
AURA_LLM_PROVIDERS=hosted1,hfspace         # or hosted1,local where a local provider exists
AURA_LLM_ROUTING_POLICY=quality_first
AURA_PROVIDER_HOSTED1_BASE_URL=<allowlisted hosted base URL>
AURA_PROVIDER_HOSTED1_MODEL=<open-weight model id>
AURA_PROVIDER_HOSTED1_API_KEY_ENV=AURA_PROVIDER_HOSTED1_API_KEY
AURA_PROVIDER_HOSTED1_IS_LOCAL=false
AURA_PROVIDER_HOSTED1_QUALITY_TIER=3
AURA_PROVIDER_HOSTED1_RPM=... / _RPD / _TPM / _TPD   # the provider's published free limits
AURA_PROVIDER_HOSTED1_TOKENS_PER_SECOND=200          # lets the planner use 1200 tokens here
AURA_PROVIDER_HOSTED1_EXTRA_BODY={"reasoning_effort": "low"}   # reasoning models only
```

### Claude for the Engineer (module readiness WP6; ADR-033, D-02)

This turns on Engineer turns for operators. Its gate: the consent route (WP1), the per-user
allowance (WP2) and the workload fence (WP6a) must already be **live**. They shipped in PR #251;
check `/health/ready` shows `schema.revision` `0023` and `release.git_sha` at or after that merge
before going on. Otherwise Engineer code would reach Anthropic without consent, without a cap, or
for work that is not the Engineer's.

Set these in the Railway dashboard (Variables). The slot name `claude1` is free on Railway; the
VPS has to use a pre-wired slot instead (`hosted3` in `deploy/.env.example`'s Engineer block),
because Compose forwards only `HOSTED1..3`.

```
AURA_ALLOW_CLAUDE_INFERENCE=true
AURA_LLM_PROVIDERS=hosted1,hfspace,claude1      # append claude1 to whatever is there now
AURA_PROVIDER_CLAUDE1_KIND=claude                # no BASE_URL: the claude kind refuses one
AURA_PROVIDER_CLAUDE1_MODEL=claude-opus-5-5      # exactly as Anthropic's API names it
AURA_PROVIDER_CLAUDE1_API_KEY_ENV=AURA_PROVIDER_CLAUDE1_API_KEY
AURA_PROVIDER_CLAUDE1_IS_LOCAL=false
AURA_PROVIDER_CLAUDE1_MAX_DATA_CLASSIFICATION=sensitive   # Engineer workspaces are SENSITIVE
AURA_PROVIDER_CLAUDE1_WORKLOADS=engineer         # the fence: never omit with a sensitive ceiling
AURA_PROVIDER_CLAUDE1_POOLS=coder                # the pool Engineer turns ask for
AURA_PROVIDER_CLAUDE1_SUPPORTS_TOOLS=true
AURA_PROVIDER_CLAUDE1_SUPPORTS_REASONING=true
AURA_PROVIDER_CLAUDE1_QUALITY_TIER=3
AURA_PROVIDER_CLAUDE1_CONTEXT_WINDOW=200000
AURA_PROVIDER_CLAUDE1_MAX_OUTPUT_TOKENS=32000    # thinking counts against it (+8000 headroom)
AURA_PROVIDER_CLAUDE1_TIMEOUT_S=300
AURA_PROVIDER_CLAUDE1_TOKENS_PER_SECOND=60
AURA_PROVIDER_CLAUDE1_COST_INPUT=0.004           # USD per 1k tokens ($4 / MTok)
AURA_PROVIDER_CLAUDE1_COST_OUTPUT=0.02           # USD per 1k tokens ($20 / MTok)
AURA_PROVIDER_CLAUDE1_RPM=50                     # your Anthropic tier's limits, or lower
AURA_PROVIDER_CLAUDE1_TPD=5000000                # the deployment's hard daily ceiling
AURA_PROVIDER_CLAUDE1_USER_TPD=400000            # D-05: one person's daily allowance
AURA_ENGINEER_AGENT_ROLES=Developer,Admin,SuperAdmin   # D-04
```

The numbers for `RPM`, `TPD` and `USER_TPD` are starting points, not measurements: size `TPD` to
the monthly spend you accept (`TPD × 30 × blended $/token`), and `USER_TPD` so a handful of
operators cannot exhaust it between them.

The key itself, `AURA_PROVIDER_CLAUDE1_API_KEY`, is a **sensitive** variable (§4), created in the
Claude Console (Settings → API keys). Never name it `ANTHROPIC_API_KEY`: that name is on
`BANNED_CREDENTIAL_NAMES`, and the adapter never uses the SDK's ambient lookup anyway.

Two things already set on this target that the Engineer depends on:

- **`AURA_REDIS_URL`** and **`AURA_CACHE_BACKEND=redis`.** The per-user allowance is counted in
  the shared cache, so without Redis each worker counts on its own and the cap multiplies by the
  worker count. Redis also carries the host-agent relay: a desktop's daemon socket lives on one
  worker, and the relay lets a turn on another worker reach it. The boot log must not show
  `host-agent relay off`.
- **One more boot line to look for:** with the slot set as above, the log shows
  `Claude inference permitted by AURA_ALLOW_CLAUDE_INFERENCE` and `Claude provider may receive
  SENSITIVE content`. If it also shows `Claude provider at the sensitive ceiling serves every
  workload`, the fence did not apply: stop and fix `WORKLOADS` before anyone uses the gateway.

**Accept** (each is evidence for WP10a, not something a green test shows):

1. `/health/ready` lists `claude1` with `healthy: true` and `is_local: false`.
2. `GET /api/v3/agents` as a Developer shows the Engineer `usable: true` and `consent_required`
   until that person accepts; as a plain user it is not usable.
3. An operator accepts the notice (`POST /api/v3/engineering/consent`) and one Engineer turn
   completes; an `llm.third_party_generation` audit row names `claude1`.
4. An ordinary chat turn after that still routes to `hosted1` or `hfspace`, never `claude1`.

**Rollback:** take `claude1` out of `AURA_LLM_PROVIDERS` and redeploy. Engineer turns go back to
`capability_unavailable`; nothing else changes. Consents and usage counters stay where they are.

### Capacity and latency settings (provider performance audit, 2026-11-08)

Without these, the code defaults apply on Railway:
- the admission queue is unlimited and has no timeout;
- one user may hold every generation slot;
- `hosted1`'s concurrency is 8, whatever its rate limit;
- the prompt cap is 400 tokens, sized for a CPU 3B model;
- a failing chat turn can take about 210 s before it reports an error: up to 3 providers per
  generation, two generations per turn, each attempt with a full timeout.

None of these is in `railway.json`, and Railway ignores that file for this service anyway, so set
them in the dashboard (Variables).

**These are starting values, not measured ones.** No generation on this topology has been
measured yet (GAP-041). Phase 3 of the provider performance plan runs the load test that confirms
or replaces them. Record what it finds here.

```
# Admission (aura_core/llm/admission.py). Waiting is bounded, and a request that cannot start in
# time gets a retryable 503 "busy" instead of an indefinite spinner.
AURA_LLM_ADMISSION_MAX_QUEUE=8
AURA_LLM_ADMISSION_QUEUE_TIMEOUT_S=10
AURA_LLM_USER_MAX_CONCURRENT_GENERATIONS=2

# hosted1: size concurrency to its rate limit (rule below), and declare the limit so the router
# skips the provider *before* a 429 rather than after.
AURA_PROVIDER_HOSTED1_MAX_CONCURRENCY=<rule below>
AURA_PROVIDER_HOSTED1_RPM=<published limit>   # and _TPM / _RPD / _TPD where published
AURA_PROVIDER_HOSTED1_TIMEOUT_S=30

# Per-turn bounds (PP-06/PP-07, aura_core/llm/deadline.py). Engineer turns never get the budget.
AURA_LLM_TURN_GENERATION_BUDGET_S=45
AURA_LLM_STREAM_MAX_S=120

# Prompt cap (PP-09). 400 is sized for qwen2.5:3b on CPU; nothing on this target is that slow.
AURA_LLM_MAX_PROMPT_TOKENS=<rule below>
```

Why each value:

- **`AURA_WORKERS`.** Every provider's `MAX_CONCURRENCY` is declared per capacity group, and the
  image runs `AURA_WORKERS` processes (4 by default). Since audit FSA-025 the gateway divides each
  group's cap among its processes: `AURA_LLM_ADMISSION_PROCESSES` defaults to `AURA_WORKERS` when
  unset, so a declared cap of 4 is 1 slot in each of 4 workers and the upstream sees 4, not 16.
  Declare the cap the upstream should see in total; a cap below the worker count keeps one slot per
  worker and the boot log says so. The per-minute arithmetic below still has to account for every
  worker, because each worker's slot is independently busy. Daily and per-minute quotas
  (`RPM`/`RPD`/`TPM`/`TPD`) are shared counters in Redis across every process.
- **`AURA_PROVIDER_HOSTED1_MAX_CONCURRENCY`.** A generation that takes `d` seconds, held at
  concurrency `c`, sends `c × 60 / d` requests a minute. To stay under the provider's `RPM`, use
  `c ≤ RPM × d / (60 × AURA_WORKERS)`, rounded down, and never below 1.

  For example, at 30 RPM with about 5 s per generation and 4 workers: `30 × 5 / 240 = 0.6`, so 1.
  That is a hard ceiling. A free tier like that cannot carry the 50-concurrent-user target
  (`docs/production/PRODUCTION_SLO.md` S6). The load test will show it as a stream of `busy` and
  `rate_limited` responses rather than slow ones.
- **Queue timeout, 10 s.** The answer target is p95 < 15 s (S4). A turn that waited 20 s for a
  slot has already missed it, and a prompt "busy, retry" is more honest than a long wait.
- **`AURA_PROVIDER_HOSTED1_TIMEOUT_S`, 30 s.** The only measured `hosted1` completion took 4.8 s
  (n=1, in-process, 2026-10-28). 30 s leaves headroom for a slow reply and still leaves part of the
  turn budget for a fallback.
- **`AURA_LLM_TURN_GENERATION_BUDGET_S`, 45 s.** This caps all of a chat or voice turn's
  generations, fallbacks included. It counts from the first generation, so retrieval does not use
  it up. When it runs out, the turn ends with an error, and the attempt it cut short is not held
  against the provider's circuit breaker.
- **`AURA_LLM_STREAM_MAX_S`, 120 s.** This caps a stream after its first chunk. Every chunk is
  also bounded by the request timeout. 1024 output tokens at a slow 20 tok/s is about 51 s, so
  120 s only cuts off a stream that is really stuck.
- **`AURA_LLM_MAX_PROMPT_TOKENS`.** The prompt has to fit every provider a turn may be routed to.
  Use `min(context_window) − max_output_tokens − 500` across `hosted1` and `hfspace`. Also mind
  `TPM`: a chat turn sends roughly the prompt twice (planner, then answer), so
  `2 × prompt × turns per minute` must stay under it. `3000` is a reasonable first value for
  8k-context models with a generous TPM. The cap is one global value, not per provider. That is
  fine here because no provider on this target is slow enough to need a small prompt. A mixed
  deployment with a CPU model would need a per-provider cap, which does not exist yet.

What these do not fix: sensitive turns (memory, documents, tools, images, Engineer) still have
no eligible model while only `hosted1` is healthy, because its ceiling is `internal`. For the
Engineer, the owner's decision (D-02, ADR-033 amendment 2026-11-17) is a Claude slot fenced to
the Engineer workload; the settings are in `deploy/.env.example`'s Claude block and must not be
switched on before the consent route and the per-user allowance (`USER_TPD`) are deployed. The owner
accepted this as the chat-only launch limitation on 2026-11-08. Those turns now end with
`capability_unavailable` ("This needs a feature that isn't available yet") instead of a
retryable "model not answering".

Two differences from the VPS:

- **Railway has no secret files.** Set `AURA_PROVIDER_HOSTED1_API_KEY` itself as a sensitive
  variable (§4).
- **The hosted slot is never the fallback for sensitive content.** That content can only go to
  `hfspace` (rented compute, ceiling `sensitive`), so a deployment whose only non-hosted provider
  is the Space sends every document, memory and tool-output turn there (ADR-030 §8). If that
  provider is down, those turns fail rather than going to the hosted slot.

## 4. Secrets (names only)

Set as Railway environment variables marked "sensitive" in the dashboard, never committed:

`AURA_DATABASE_URL`, `AURA_REDIS_URL`, `AURA_QDRANT_API_KEY`, `AURA_JWT_SECRET`,
`AURA_SECRETS_ENCRYPTION_KEY`, `AURA_PROVIDER_HFSPACE_API_KEY`, `AURA_PROVIDER_HOSTED<n>_API_KEY`
(per enabled hosted slot), `AURA_PROVIDER_CLAUDE1_API_KEY` (once the Engineer's Claude slot
is on), `AURA_GITHUB_OAUTH_CLIENT_SECRET`
(if GitHub OAuth connect is enabled), `HF_TOKEN` (only if the embedding model or a gated model
repo needs authenticated download — this authenticates a weight *download*, never inference,
per `deploy/spaces/inference-vllm/README.md`).

## 5. What's deliberately not enabled on Railway

`AURA_SANDBOX_ENABLED` (Engineer-agent Docker workspaces, ADR-013/014) stays `false`. That
feature requires `docker-socket-proxy` and a host bind mount (`AURA_SANDBOX_HOST_ROOT`) that
assume a Docker-Compose host, not Railway's managed container runtime. Enabling it is a
separate infrastructure decision outside this migration's scope, not something this migration
blocks — the code path is already inert-by-default even on the VPS.

## 6. Migrations

Railway does not run a Compose `depends_on` gate. Instead, the service's **pre-deploy command**
runs `alembic upgrade head` against the service's `DATABASE_URL` before the new release receives
traffic.

**Where that command lives matters.** Railway applies `aura_backend/railway.json` only when the
service links it as its config file. The production AURA-AI service does not (`railwayConfigFile`
null), so its dashboard settings are what run, and editing `railway.json` changes nothing live.
The command is set in **Settings → Deploy → Pre-deploy command** (done 2026-10-31; first live run
logged `MIGRATION OK` at `0016 (head)`). It matches the value `railway.json` records:

```json
"preDeployCommand": "sh /app/scripts/railway-predeploy.sh"
```

Railway does **not** evaluate that field in a shell. An inline chain such as
`echo … && alembic upgrade head` runs only its first command and still counts as passed, which
left production at `0013` across every deploy until 2026-10-30 (CB-43). Keep the field one plain
argv and put any logic in `scripts/railway-predeploy.sh`, which `TestRailwayPreDeployMigration`
pins. The script logs `revision before/after` and `=== MIGRATION OK|FAILED ===`, each with a UTC
timestamp, and a `database url: set|UNSET` line (never the value). alembic's own errors go to the
same log. It gives up after `AURA_MIGRATION_TIMEOUT_S` (200 s) for the upgrade and
`AURA_MIGRATION_CURRENT_TIMEOUT_S` (30 s) for each `alembic current`. It then pauses
`AURA_PREDEPLOY_LOG_FLUSH_S` (5 s) on every exit, so Railway reads the last lines before the
container stops. The whole budget stays under the 300 s pre-deploy limit.

**Reading a deploy's pre-deploy log:**
- `revision before … after` and `=== MIGRATION OK ===`: it worked.
- `=== MIGRATION FAILED (…) ===`: the deploy is refused, and the line says why.
- `=== MIGRATION START ===` and nothing else: the script never got past its first line. That was
  the case on 8bc8d4c, the first deploy of the script, before the flush pause existed. Check
  `/health/ready`'s `schema` block, and migrate by hand as below.

To migrate by hand (what resolved CB-43):

```bash
railway ssh --project=<id> --environment=<id> --service=<gateway>
cd /app && alembic current && alembic upgrade head && alembic current
```

## 7. Graceful startup/shutdown, logging, health

Unchanged from the existing image: `docker-entrypoint.sh` hydrates secrets and wipes the
Prometheus multiprocess directory before Uvicorn workers spawn; `AURA_LOG_FORMAT=json` is
already structured logging with correlation IDs (ADR-009, observability-first). Railway's own
SIGTERM handling reaches the same Uvicorn process the VPS does — no different shutdown path was
built or is needed.

**The image runs as the unprivileged user `aura` (uid/gid 10002) since 2026-11-16.** It can write
`/var/lib/aura` (the default `AURA_DATA_DIR` is `/var/lib/aura/data`), `/app/local_qdrant_storage`
and its home directory, and nothing else. If a Railway volume is attached to any service built
from this image, Railway mounts it root-owned: set `RAILWAY_RUN_UID=0` on that service, or the
process cannot write to the volume. The VPS compose stacks keep running these services as root
(`user: "0:0"`) until their root-only secret files and volumes are migrated.

## 8. CI

`.github/workflows/control-plane-deploy.yml` builds, lints and tests `aura_backend/` (the same
gates `backend-ci.yml` already runs), then — only on `main` and only when a Railway API token is
configured — triggers a Railway deploy via the Railway CLI, path-filtered so a Hugging Face
Space-only change never redeploys this service. See that workflow file for the exact trigger
conditions and required secrets.
