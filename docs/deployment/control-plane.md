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
AURA_PROVIDER_HFSPACE_MAX_CONCURRENCY=8
AURA_LLM_TIMEOUT_S=60

AURA_DOMAIN=<railway-issued or custom domain>
ALLOWED_ORIGINS=https://<AURA_DOMAIN>
```

Full list of every variable the gateway reads: `deploy/docker-compose.prod.yml`'s `gateway.environment`
block (lines ~234–394) is the canonical reference — copy names, not VPS-specific values
(hostnames like `postgres`/`redis`/`qdrant` don't resolve on Railway; use the connection strings
Railway's Postgres/Redis plugins or your external Qdrant provide instead).

## 4. Secrets (names only)

Set as Railway environment variables marked "sensitive" in the dashboard, never committed:

`AURA_DATABASE_URL`, `AURA_REDIS_URL`, `AURA_QDRANT_API_KEY`, `AURA_JWT_SECRET`,
`AURA_SECRETS_ENCRYPTION_KEY`, `AURA_PROVIDER_HFSPACE_API_KEY`, `AURA_GITHUB_OAUTH_CLIENT_SECRET`
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

Railway does not run a Compose `depends_on` gate. Use Railway's **pre-deploy command** (or a
one-off `railway run` before the first deploy) to run:

```bash
cd aura_backend && alembic upgrade head
```

against `AURA_DATABASE_URL`, before the `gateway` service's new release receives traffic. This
is the same command CLAUDE.md documents for every other environment — no new migration tooling.

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
