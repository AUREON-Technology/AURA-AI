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

AURA_DATABASE_URL=<Railway Postgres plugin's connection string>
AURA_REDIS_URL=<Railway Redis plugin's connection string, or an external Redis>
AURA_QDRANT_URL=<Qdrant Cloud or self-hosted Qdrant URL — Railway has no Qdrant plugin>
AURA_QDRANT_API_KEY=<Qdrant API key>
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
(per enabled hosted slot), `AURA_GITHUB_OAUTH_CLIENT_SECRET`
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
service links it as its config file. The production AURA-AI service does not (verified
2026-10-31: `railwayConfigFile` null). Its dashboard settings are what run, and editing
`railway.json` changes nothing live. Set the command in **Settings → Deploy → Pre-deploy
command** to the same value `railway.json` records:

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

## 8. CI

`.github/workflows/control-plane-deploy.yml` builds, lints and tests `aura_backend/` (the same
gates `backend-ci.yml` already runs), then — only on `main` and only when a Railway API token is
configured — triggers a Railway deploy via the Railway CLI, path-filtered so a Hugging Face
Space-only change never redeploys this service. See that workflow file for the exact trigger
conditions and required secrets.
