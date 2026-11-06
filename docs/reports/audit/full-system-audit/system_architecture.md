# AURA AI full-system audit — Pass A: System architecture

- **Pass:** A — system architecture (layering, wiring, execution path, inference path, deployment units, client reachability, CI gates)
- **Baseline:** `main` @ `ffbbb9fff777a7b6117236c67d517fd201ac2208` (short `ffbbb9f`, committed 2026-10-31 13:00 +0200); working tree as described in `AUDIT_BRIEF.md`
- **Date:** 2026-10-31
- **Author:** Audit subagent pass A
- **Read-only:** nothing under the repository was modified except this report and `findings_A.json`. The only commands executed were `git ls-files`, `git check-ignore`, `git log -1`, `ls`/`wc`, a Python version probe, and one `pytest tests/architecture` run (in-memory, no Docker/network/model).

## 1. Scope and method

**Read (CODE-READ):** `aura_backend/aura_service.py` (all 324 lines), `aura_core/container.py` (lines 98–327, 896–995, 1586–1834; method index of the rest), `aura_core/llm/registry.py` (all), `aura_core/llm/router.py` (policy/failover sections), `aura_core/kernel/kernel.py` (lines 436–485 and all `tool_calls`/`quarantin`/`except` sites), `aura_core/api/v3.py` (1660–1780), `aura_core/adapters/postgres_store.py` (pool section), `infrastructure/http_limits.py` (40–110), `aura_core/security/rate_limit.py` (200–230), `celery_app.py` (60–154), `tasks.py` (40–130), `Dockerfile` (120–148), `scripts/api_reachability.py` (20–100), `tests/architecture/{test_dependency_rules,test_no_external_inference,test_runtime_tables_have_migrations}.py`, both compose files at the cited lines, `.github/workflows/control-plane-deploy.yml` (20–34), `docs/deployment/control-plane.md` (grep), `docs/guides/api-reachability.md` (grep).

**Delegated (three read-only Explore subagents, results cross-checked against the files above where cited):** full route inventory vs. client callers; compose/CI/Railway/HF-Space map; extraction of prior-audit claims from `AURA_FULL_SYSTEM_REVIEW.md`, `AURA_CRITICAL_BACKLOG.md`, `gap_register.yaml`, `BACKEND_STATUS.md`, `00-overview.md`, `CLAUDE.md`. Subagent line numbers that I did not personally reopen are marked `(subagent)`.

**Executed (EXECUTED-HERE):** `tests/architecture` under the repo venv — see §7. **Not run:** anything needing Docker, Postgres, Qdrant, Redis, a model, Flutter/Dart, or network; no production probe.

**Environment caveat:** the C: drive (temp) was full; Bash output was unreliable, so inspection used Read/Grep and the test log was written to `E:\aura_audit_tmp\arch_tests.log`.

## 2. System map (observed from code unless marked)

```
                 ┌──────────── clients ────────────┐
   aura_client (Flutter)   aura_admin (Next.js /console)   aura_hostd (Dart daemon)
      /api/v3/*  (HTTP+SSE+WS)   /console/api/v3/* → proxy → /api/v3/admin/*     WS /api/v3/host/agent
                 └────────────────┬────────────────┘
        VPS target: nginx (only published port, prod.yml:292-294) ──► gateway:8000
        Railway target: Railway edge ──► uvicorn (Dockerfile:147, --proxy-headers, 4 workers)
                                  │
   aura_service.py:276-324 create_app()
     hydrate_secrets → load_settings → _require_jwt_secret → build_container
     build_app() (aura_core/api/v3.py:1669-1674)  +  _mount_platform_routers() (aura_service.py:124-139)
        /api/v3 kernel router (v3.py:197)             api/v2/auth, api/v2/sync, api/v2/insights,
        + admin, admin_adaptation, admin_service_keys, api/automation, api/learning, api/admin_platform,
          voice_live (WS), host_agent (WS)            api/v2/github_integration  (ADR-002 platform adapters)
                                  │
   aura_core/container.py:98-327  Container.__init__  (composition root; the only concrete↔abstract seam)
     ├─ ProviderRouter(providers=build_providers(settings.llm))     registry.py:119-176: echo | openai_compatible | ollama only
     ├─ AIKernel  ← SafetyPipeline, ContextEngine, KnowledgeEngine, IndexManager, CagEngine, ToolRegistry, agents registry
     ├─ stores: documents/memory/personal-OS/user-data-keys/engineer-runs/workspaces/goals/graph/feedback/audit/
     │          telemetry/sources/idempotency/service-keys   (memory | postgres, one shared PostgresPool :900-912)
     ├─ DurableEventBus(outbox) → TriggerEngine :145-155        (drained by Celery `drain_event_outbox_task`)
     └─ optional: docker sandbox runner, host git, workspace manager, MCP client, vision, STT/TTS, live voice
                                  │
   Background: Celery (celery_app.py / tasks.py) queues realtime | ingestion,learning | training; beat schedule :157-216
   Data: Postgres (Alembic 0001–0019), Qdrant, Redis; sandbox via docker-socket-proxy (EXEC=0, IMAGES=0)
   Inference: self-hosted Ollama/vLLM (VPS compose) | ADR-030 hosted open-weight slots hosted{1,2,3} | HF Space vLLM
```

Observed vs. documentation-only:
- **Observed in code:** everything above the "Data" line.
- **Documentation-only / cannot verify here:** what is actually running on Railway (gateway only vs. gateway + hand-created workers), the VPS state, HF Space health, DB revision `0016`. Treated as claims per the brief.

## 3. Inventory and matrices

### 3.1 Layering invariants — claim vs. code

| Invariant (claim) | Where enforced | Observed | Status |
|---|---|---|---|
| `domain/`,`ports/` stdlib-only; engines never import adapters/api/container; vendor SDKs lazy; only `container.py` imports container; `aura_core` never imports legacy `services/infrastructure` (CLAUDE.md:192-195, ADR-001/002) | `tests/architecture/test_dependency_rules.py:266-327, 334-409, 564-617` — AST walker covering relative and absolute imports, `try`/`if`/class bodies, with a two-entry allowlist (:126-139) | All dependency-rule tests passed in the local run (§7). F-28's "misses absolute and vendor imports" is addressed by `_resolve()` :174-189 and the self-tests :430-561 | VERIFIED WORKING (locally) |
| `registry.py` builds only `echo`/`openai_compatible`/`ollama`; no hosted-vendor branch (CLAUDE.md:197-199) | `aura_core/llm/registry.py:122-164`; `tests/architecture/test_no_external_inference.py` (six layers, :7-27) | Three branches then `ConfigurationError` :155-164. ADR-030 slots are `openai_compatible` with an operator base URL (:14-16) | VERIFIED WORKING (locally) |
| `AIKernel` is the only execution path; model `tool_calls` informational (CLAUDE.md:210-213, ADR-006) | `kernel.py:626-629, 822-870` | Streaming path emits `tool_calls` frames without dispatch :854-870; execution only via `_plan_and_execute` :443 | CODE-READ, consistent |
| Confirmation gating / quarantine (ADR-006, F-04) | `kernel.py:400-425, 474-476, 1698+` | `guard_context_items` quarantines; `_hold_for_confirmation` returns `pending_tool_calls` :474 | CODE-READ, consistent |
| Failover only before first token (CLAUDE.md:216-221) | `router.py:36, 774-777, 825-846, 897-909` | `emitted` flag set after first chunk; failover loop skipped once emitted | CODE-READ, consistent |
| Container builds eagerly, connects lazily; `PostgresPool._get_pool()` double-checked lock | `container.py:98-104, 900-912`; `postgres_store.py:100-122` | One memoised pool for every Postgres store; closed once at shutdown :1706-1712; `asyncio.Lock` with re-check :106-111 | VERIFIED (CODE-READ); concurrent cold-start on real Postgres NOT RUN |
| `aura_service.py` never imports `main.py` (CLAUDE.md:233-236) | `aura_service.py:1-6, 276-281` | `main.py` does not exist in `aura_backend/` (ls). Legacy `services/` (23 files) is reached lazily by name :67, :97, :154, :188, :207, :271, :298 | VERIFIED |
| Every runtime `aura_*` table has a migration; 0016 baseline pinned to adapter DDL (CB-20 / F-27) | `tests/architecture/test_runtime_tables_have_migrations.py:52-70` | Passed locally | VERIFIED WORKING (locally) |
| Sandbox never `docker exec`; both compose files keep `EXEC: "0"` | `tests/unit/test_sandbox_exec_surface.py` (not run this pass); compose allowlists prod.yml:675-694, dev.yml:437-456 (subagent) | Allowlist identical in both files: `CONTAINERS=1`, `POST=1`, everything else `0` | CODE-PRESENT / BEHAVIOR UNVERIFIED |
| `nginx` is the only published port (SEC-12) | `deploy/docker-compose.prod.yml:292-294` (subagent) | True for the VPS compose only. On Railway uvicorn is the edge (Dockerfile:147), with `build_app`'s own security headers/metrics gate as the compensating control (`v3.py:1676-1691`) | VERIFIED for VPS; N/A on Railway (see A-04) |

### 3.2 Deployment units

| Unit | Defined in | Runs | Gate before deploy | Live status (claim) |
|---|---|---|---|---|
| VPS prod stack (`aura`) | `deploy/docker-compose.prod.yml` — nginx, console, gateway, 3 workers + beat, ollama, (vllm `gpu`), postgres/redis/qdrant, observability, backup, sandbox profile | gateway + Celery + inference | `Deploy VPS` ← `workflow_run` Backend CI (`deploy-backend.yml:26-28`) | Dead since 2026-10-18 (host-key change) — DOCUMENTED ONLY |
| VPS dev stack (`aura-dev`) | `deploy/docker-compose.dev.yml` — no nginx/ollama/observability; rides prod's `aura_edge` network (dev.yml:162-164, subagent) | gateway + Celery | manual (`vps-dev-ops.yml`) | DNS no longer resolves — DOCUMENTED ONLY |
| Railway control plane | `aura_backend/railway.json` (one service: Dockerfile build, `/health/live`, pre-deploy `sh /app/scripts/railway-predeploy.sh` :4-13); Dockerfile CMD = uvicorn gateway only (:147) | gateway; workers/beat **only if hand-created** (`control-plane-deploy.yml:24-28`, `docs/deployment/control-plane.md:23-36`) | Railway git auto-deploy ships every `main` push regardless of CI (CB-44) | Serving `aureon-ai.tech`, DB `0016` — DOCUMENTED ONLY |
| HF Space inference plane | `deploy/spaces/inference-vllm/` — `FROM vllm/vllm-openai:latest` (Dockerfile:21, unpinned), model `${VLLM_MODEL:-Qwen/Qwen2.5-7B-Instruct-AWQ}` (entrypoint.sh:18) (subagent) | vLLM OpenAI API on 7860 | `inference-plane-deploy.yml` (own validate/preflight) | 503 — DOCUMENTED ONLY |
| Sandbox image | `deploy/sandbox/Dockerfile`, built by compose `sandbox-image` and Backend CI `docker-build` | throwaway containers per command | — | never verified live (CB-27) |

### 3.3 CI gates

| Suite | Workflow (file:line, subagent) | Gates a deploy? | Currently executing? |
|---|---|---|---|
| `pytest tests --cov-fail-under=82`, legacy `unittest`, `tests/architecture` | `backend-ci.yml:94, 110, 99` | Yes — upstream of Deploy VPS and Deploy Control Plane | No runners since 2026-10-28 (CB-44) — DOCUMENTED ONLY |
| Flutter analyze/test; Android/Windows/Linux builds | `client-ci.yml:61-147` | No | Partially (per `CLAUDE.md`) |
| `dart test` (hostd) | `hostd-ci.yml:63, 102` | No | Red (PTY hang) — DOCUMENTED ONLY |
| Admin `tsc`, build, Playwright | `admin-ci.yml:65-81` | No | Never run — DOCUMENTED ONLY |
| Agent evaluation | `eval-ci.yml:96, 144` | No (dispatch-only per BACKEND_STATUS:66) | — |

Net: **no suite gates what reaches production today.** Railway ships on push; the only workflow that would gate it depends on runners that are not being assigned.

### 3.4 Client-to-backend reachability (147 routes; subagent inventory, spot-checked)

| Group | Routes | Flutter caller | Admin caller | hostd caller | No caller |
|---|---|---|---|---|---|
| `/api/v3` kernel (v3.py) | 32 + 4 health/metrics | 24 | 4 (`/admin/status`, index rebuild, prompt pin, reset-circuit) | 0 | `POST /chat` (server/ADR-029 callers only), `knowledge/graph*` (2), `/tools`, `/agents/runs*` (2), health/metrics (infra) |
| `admin.py` | 33 | 0 | 28 | 0 | 5 (`admin/knowledge/.../cag-pin` ×2, `refresh/latest`, `scorecard`, `learning/jobs/{id}`) |
| `admin_adaptation.py` (Track AP) | 25 | 0 | 0 | 0 | **25** |
| `admin_service_keys.py` | 3 | 0 | 0 | 0 | 3 |
| `admin_platform.py` | 6 | 0 | 5 | 0 | 1 (`jobs/{task_id}`) |
| `api/v2/auth` | 10 | 7 | 3 | 2 | 1 (`desktop/devices/revoke`) |
| `api/v2/sync`, `insights`, `automation`, `github` | 2+3+2+5 | 2+3+2+3 | 0 | 0 | 2 (OAuth redirect targets — server-only) |
| `api/learning` | 4 | 0 | 0 | 0 | 4 |
| WS `voice/live`, `host/agent` | 2 | 1 | 0 | 1 | 0 |

47 routes have no app caller. All 47 are triaged in `scripts/api_reachability.py:37-48` and rendered in `docs/guides/api-reachability.md` (rows with `| none |`, lines 14–127); none is `untriaged`. No client path lacks a backend route (subagent (d)). The committed matrix **cannot be regenerated or checked locally** — see A-01.

## Findings

| Local ID | Severity | Title | Subsystem | Evidence (path:line / command) | Current behavior | Expected behavior | Recommended action | Confidence |
|---|---|---|---|---|---|---|---|---|
| A-01 | P2 | The architecture gate (v3-only surface, route reachability, route-auth/IDOR scans) is not executable anywhere right now | Verification / CI | EXECUTED-HERE: `pytest tests/architecture` → 8 failed; 6 of them `AttributeError: module 'fastapi.routing' has no attribute 'iter_route_contexts'` (`tests/architecture/test_v3_only_surface.py:29`, `scripts/api_reachability.py:73`). `requirements.lock:587` pins `fastapi==0.141.1`; venv has `fastapi 0.136.3 / starlette 1.2.1` (`venv/Scripts/python -c "import fastapi…"`). Same call in `tests/security/test_route_auth.py:125`, `tests/security/test_idor.py:125`. CI not running (CB-44, DOCUMENTED ONLY) | Locally the six invariants error before asserting anything; in CI nothing runs. The committed `docs/guides/api-reachability.md` is unverifiable against the tree | Every merge proves "all `/api/` routes are under `/api/v3`", "admin surface cannot browse user content", "every uncalled route is triaged", and the auth/IDOR sweeps | Re-sync the venv to `requirements.lock` (owner action; `pip install` is out of audit scope) and re-run; separately, until CI runners return, run `pytest tests/architecture tests/security -q` locally before each merge and record the result in the PR. Consider pinning the FastAPI feature with a skip-with-reason guard so a stale interpreter fails loudly with "lock not installed" rather than an AttributeError | High |
| A-02 | P3 | Ingestion-family Celery tasks are published to a queue no worker consumes | Background jobs / wiring | `tasks.py:50, 61, 110` (`@celery_app.task` without `queue=`), `celery_app.py:131-149` (no `task_routes`/`task_default_queue`; grep returned none), consumers `-Q realtime`, `-Q ingestion,learning`, `-Q training` (`deploy/docker-compose.prod.yml:747-911` subagent; `docs/deployment/control-plane.md:30-32`). Callers: `api/learning.py:52` (`POST /api/v3/learning/build_dataset`, live route, no client caller) and `services/drive.py:166` (router not mounted in `aura_service.py:124-139`) | `.delay()` lands on Celery's default `celery` queue; nothing consumes it, so the task id returned to the caller never runs and `GET /admin/jobs/{task_id}` would stay pending | A task either runs on the `ingestion` queue it is named for or the route is removed (GAP-013 "to wire or remove") | Either add `queue="ingestion"` to the three decorators (one-line each) or remove the dead `learning`/drive paths; add a unit test asserting every registered task's queue is one a compose worker consumes | High (code), behavior unverified at runtime |
| A-03 | P2 | Railway target codifies only the gateway; the durable outbox, insights, briefings, workspace reaper and idempotency purge depend on workers/beat that exist only if created by hand | Deployment boundary | `railway.json:4-14` (one service), `Dockerfile:147` (uvicorn only), `.github/workflows/control-plane-deploy.yml:24-28`, `docs/deployment/control-plane.md:23-36`; consumers of those schedules: `container.py:145-155` (DurableEventBus → outbox), `tasks.py:199-450`, `celery_app.py:157-216` | Whether `worker-realtime`/`worker-ingestion`/`beat` run on Railway is UNKNOWN (no manifest, no CI check, status docs silent). If absent: durable events accumulate unconsumed, no hourly insights/briefings, idle workspaces never paused, idempotency keys never purged | The production control plane's background tier is declared in-repo and its presence is asserted by the deploy's health verification | Add a readiness/`/admin/status` signal for "beat heartbeat age" and "outbox backlog" (the gauges exist per BACKEND_STATUS:50) and have `control-plane-deploy.yml`'s health step fail when the beat heartbeat is stale; document in `docs/status/BACKEND_STATUS.md` whether the three services exist today | Med (existence unknown; consequence certain if absent) |
| A-04 | P2 | Two stacked per-IP rate limiters key on different addresses; on Railway the legacy one likely collapses all users into one 100 rpm bucket | Edge / security | `infrastructure/http_limits.py:67` (`request.client.host`), applied at `aura_service.py:235`; `aura_core/security/rate_limit.py:202-229` (right-most `X-Forwarded-For`, trusted-proxy aware) applied via `build_router`; Dockerfile:147 uses `--proxy-headers` with no `--forwarded-allow-ips` (uvicorn then trusts forwarded headers only from 127.0.0.1); Railway has no nginx (SEC-08, review:252 subagent) | On the VPS nginx hands the real peer to the gateway, so both limiters agree. On Railway, if the edge peer is not loopback, `request.client.host` is the proxy address for every user and the legacy limiter throttles the whole deployment at 100 req/min collectively; the aura_core limiter remains per-client | One limiter, keyed on the trusted-proxy-resolved client address, on every target | Drop `infrastructure/http_limits.RateLimitMiddleware` from `aura_service.py` in favour of the aura_core limiter (already wired at :288-292), or key it with `extract_client_ip`; set `--forwarded-allow-ips` to Railway's edge range once known. Verify live with two clients from different IPs and the `aura_http_requests_total{status="429"}` series | Med (Railway peer address not observable here) |
| A-05 | P3 | Dev/prod compose parity gaps on security-relevant keys, contrary to the stated parity rule | Deploy | `deploy/docker-compose.dev.yml:365` `AURA_SANDBOX_NETWORK: sandbox_egress` (hard-coded, unprefixed) vs `prod.yml:565-566` `${AURA_SANDBOX_NETWORK:-aura_sandbox_egress}` with the comment that the name must be project-prefixed; `ALLOW_MOCK_ATTESTATION` default `true` in dev :333 vs `false` in prod :519 (subagent); dev lacks the HFSPACE/HF_TOKEN/large-provider block and the sandbox resource limits present in prod :448-585 (subagent); `CLAUDE.md:264-271` requires same-commit parity on isolation settings | A dev sandbox would be attached to a network named `sandbox_egress` that compose creates as `aura-dev_sandbox_egress` (CODE-PRESENT / BEHAVIOR UNVERIFIED); dev accepts mock attestation by default; sandbox CPU/memory limits unset in dev | The two files differ only in project name, ports and secrets | Align the keys above; extend `tests/unit/test_hosted_provider_compose_plumbing.py`-style parity checks to the sandbox and attestation keys | Med |
| A-06 | P3 | Secret-literal fitness test scans gitignored files, so it fails on every developer checkout that has real secrets on disk; two of those files are stale copies | Secrets hygiene / verification | EXECUTED-HERE: `test_no_committed_client_secrets.py:200` fails on `deploy/.env:4` (Google OAuth client secret), `deploy/.env.bak-20260914-134105:4` (same), `aura_backend/credentials.json:5` (`-----BEGIN PRIVATE KEY-----`). `git ls-files` lists none of them; `git check-ignore -v` → `.gitignore:8 .env`, `.gitignore:9 .env.*`, `aura_backend/.gitignore:1 credentials.json`. Values not reproduced here | Nothing is committed (good), but the test cannot tell tracked from untracked, and a 2026-09-14 `.env.bak` plus a service-account private key sit in plaintext beside the checkout | The test checks the committed tree only; no stale secret copies on disk | Scope `_matches()` to `git ls-files` output; owner to delete `deploy/.env.bak-20260914-134105` and move `credentials.json` out of the repo directory (and rotate if its provenance is unclear) | High |
| A-07 | P3 | Every Celery worker imports the legacy RAG service at module scope, which opens a Qdrant client (or silently creates a local on-disk one) at import | Legacy tree / background jobs | `tasks.py:8` `from services.rag import rag_service`; `services/rag.py:374` module-level `rag_service = RAGService()`; `rag.py:35-51` `QdrantClient(...)` in `__init__` with fallback `QdrantClient(path="local_qdrant_storage")` when `qdrant_host` is empty. This is exactly the TD-01 pattern `test_dependency_rules.py:594-617` bans inside `aura_core`, surviving outside it | Workers that only serve `realtime`/`training` still construct a Qdrant client at import; a worker started with `QDRANT_HOST` unset creates a private sqlite Qdrant nothing reads. The only tasks using it are the ones in A-02 (currently unreachable), so the user-visible effect today is import-time coupling, not data loss | Workers connect lazily and only to the stores the queue they serve needs | Move the `services.rag` import inside the two ingestion tasks (or delete them with A-02); keep `tasks.py` importable without infrastructure | Med |
| A-08 | P4 | Backend route surface grew to 47 uncalled routes; the backlog still says 16 | Reachability / docs | `docs/guides/api-reachability.md:14-127` (47 `none` rows); `docs/roadmaps/AURA_CRITICAL_BACKLOG.md:195` CB-30 "16 uncalled routes" (subagent); 25 of the 47 are Track AP admin routes (`admin_adaptation.py:99-347`), 3 service-key routes, 4 `learning`, 2 `agents/runs`, 2 `knowledge/graph`, `/tools` | All are IMPLEMENTED BUT UNREACHABLE from any shipped UI; triage notes say "to wire (AP9)" or "to wire or remove" | Backlog count matches the matrix; each group has an owner decision | Update CB-30; decide remove-vs-wire for `learning/*` and `/tools` | High |
| A-09 | P4 | Documentation inaccuracies affecting the architecture description | Docs | See §Documentation inaccuracies | — | — | Correct `CLAUDE.md` and `BACKEND_STATUS.md` as listed | High |

**Cleared hypotheses (not findings):** the `services.jwt_auth` lazy import in `aura_service.py:67` is the documented ADR-002 seam, not a layering breach; the legacy `RateLimitMiddleware` exempting `/health` (:64-65) is intentional; `Container.__init__` building providers eagerly is safe because `OpenAICompatibleProvider` opens its `httpx.AsyncClient` in `_get_client()` (`providers/base.py:133, 193`), not in `__init__`; `health()` treating a throwing readiness probe as healthy (`container.py:1760-1766`) is a documented trade-off.

## Previous-finding recheck

| Prior item | Pass-A status | Evidence |
|---|---|---|
| PostgresPool concurrency (GAP-001, review §25) | fixed-in-source-not-operationally-verified | `postgres_store.py:100-122` double-checked `asyncio.Lock`; single memoised pool `container.py:900-912`; closed once :1706-1712. Concurrent cold-start on real Postgres NOT RUN. Whether `api/v2/sync` still holds a second SQLAlchemy pool per worker: UNKNOWN (not traced this pass) |
| Server-side encryption + ADR-032 data key (GAP-016, CB-28) | fixed-in-source-not-operationally-verified | `container.py:946-992` wires `PostgresUserDataKeyStore` and `PostgresSyncedPersonalOsStore`; `build_field_cipher` refuses production without keys :1818-1822; migration `alembic/versions/0019_user_data_keys.py` exists; live DB claimed at `0016` (BACKEND_STATUS:11), so not applied in production |
| Agent capability and memory isolation (GAP-024, CB-40) | partially fixed | Per-persona `memory_scope` (`aura_core/agents/registry.py:147-242`: shared/finance/health/engineer); `ActiveBlueprint` swap for Track AP (`container.py:224-229`). Cross-user probe against real Qdrant still open (CB-40) — cannot verify |
| Personal OS CRUD / two stores (CB-28) | fixed-in-source-not-operationally-verified | ADR-032 wiring above; legacy store kept for the move script (:964-976) |
| Fine-tuning / weight updates (GAP-017) | not applicable to pass A (code present: `_build_finetune_trainer` :645; dev compose `AURA_FINETUNE_TRAINER: scripted` dev.yml:358) | out of scope; recheck in the learning pass |
| Evaluation CI enforcement (GAP-006) | still open | `eval-ci.yml` does not gate any deploy (§3.3); CI runners dead |
| Vision/video (GAP-019) | not rechecked beyond wiring | `_build_vision_engine` :1111, both `None` when disabled (:318-322) |
| Inference concurrency/latency (CB-17, GAP-011/041) | cannot verify | admission limits derived per capacity group `registry.py:91-116`; no measurement possible here |
| Client reachability (GAP-013, CB-30) | regressed in count / still open | A-01, A-08 |
| Deployment boundaries (review §28, CB-03/16/44) | still open | §3.2, A-03, A-04 |
| Desktop IDE readiness (CB-25/26) | not applicable to pass A | — |
| CB-14 kernel swallows planning exceptions | fixed-in-source-not-CI-run | `kernel.py:447-451` still catches, but now sets `planning_failed` and appends `PLANNING_FAILED_NOTE` (:276) so the answer discloses it |
| F-28 layering tests narrower than claimed | fixed-verified (locally) | `test_dependency_rules.py:13-18, 174-189, 430-561` and the passing run |
| CB-20 / F-27 runtime DDL outside Alembic | fixed-verified (locally) | `test_runtime_tables_have_migrations.py` passed; `0016_runtime_tables_baseline.py` present |
| SEC-08 `--forwarded-allow-ips` unset on Railway | still open | `Dockerfile:147`; A-04 |
| SEC-12 nginx only published port | fixed-verified for VPS compose | prod.yml:292-294 (subagent); not applicable on Railway |
| GAP-043 GitHub Actions "proven" | regressed | CB-44 (DOCUMENTED ONLY) |

## Verification log

| Command | Result |
|---|---|
| `cd "/e/AURA AI/aura_backend" && venv/Scripts/python -m pytest tests/architecture -q -p no:cacheprovider > /e/aura_audit_tmp/arch_tests.log 2>&1; tail -5 /e/aura_audit_tmp/arch_tests.log` (background; exit code 0 reported by the harness for the pipeline, i.e. `tail`'s) | Log tail: `FAILED tests/architecture/test_api_reachability.py::test_the_committed_matrix_is_current` / `…::test_every_uncalled_route_is_triaged` / `test_no_committed_client_secrets.py::test_no_credential_literal_in_committed_source[Google OAuth client secret]` / `…[private key]` / `test_v3_only_surface.py::test_every_api_route_is_under_v3` / `…::test_platform_and_kernel_prefixes_are_mounted` / `…::test_legacy_api_prefixes_are_not_registered` / `…::test_admin_surface_has_no_user_content_browser`. **8 failed**; every other test in the directory (progress lines show roughly 350 tests) passed; pytest's final count line was not captured in the log. Six failures are the `iter_route_contexts` AttributeError (A-01), two are secret literals in gitignored files (A-06) |
| `git ls-files deploy/.env deploy/.env.bak-20260914-134105 aura_backend/credentials.json` | empty — none tracked |
| `git check-ignore -v …` | `.gitignore:8 .env`, `aura_backend/.gitignore:1 credentials.json`, `.gitignore:9 .env.*` |
| `grep -n "^fastapi==\|^starlette==" requirements.lock` / `venv/Scripts/python -c "import fastapi,starlette;…"` | lock `fastapi==0.141.1`, `starlette==1.6.0`; installed `0.136.3` / `1.2.1` |
| `git log -1` | `ffbbb9f`, 2026-10-31 13:00:13 +0200 |
| `ls aura_backend/services | wc -l; ls main.py …` | 23 legacy modules; `main.py` absent |
| An earlier full `pytest` run (log `E:\aura_audit_tmp\backend_pytest.log`) | file is empty — that run produced no output before the session was interrupted; NOT RUN this pass |
| Not run: full `pytest`, legacy `unittest`, `tests/security`, `tests/unit/test_sandbox_exec_surface.py`, Flutter/Dart suites, `docker compose config`, any network/production probe | Out of the pass's budget or needs tooling/infra; the brief forbids installs and probes |

## Documentation inaccuracies

| Doc claim | Code / tree | Note |
|---|---|---|
| `CLAUDE.md:239` ADRs run `001-*.md`–`031-*.md` | `docs/architecture/032-personal-os-single-store.md` exists (Accepted 2026-10-31, overview:139 subagent) | STALE |
| `CLAUDE.md:201` ADR-030 "Proposed" | overview:137 "Proposed; Phase 1 built"; slots wired in both compose files and `registry.py:14-16` | STALE wording |
| `CLAUDE.md:248` `SYNCABLE_MODELS` in `core/database/database.dart` | review:525 places it in `aura_backend/infrastructure/db/models.py` (subagent; not independently opened) | UNKNOWN which is right — verify in the client pass |
| `CLAUDE.md:272-275` "nginx is the only container with a published port" | true for `deploy/docker-compose.prod.yml` only; Railway exposes uvicorn directly (`Dockerfile:147`) | scope should say "on the VPS stack" |
| `docs/status/BACKEND_STATUS.md:67, 187` ADR-032 migration `0017` vs `:79-80` and CB-28 `0019` | tree: `0017_corpus_revisions.py`, `0019_user_data_keys.py` | `0017` references are wrong |
| `AURA_CRITICAL_BACKLOG.md:195` CB-30 "16 uncalled routes" | `docs/guides/api-reachability.md` has 47 `none` rows | STALE count |
| `AURA_FULL_SYSTEM_REVIEW.md:157` "AST fitness tests enforce layering (TESTED)" and `gap_register.yaml` GAP-043 `proven` | tests pass locally only after the venv is re-synced; CI has not executed since 2026-10-28 | "TESTED" holds for the walker; "gated" does not hold today |
| `control-plane-deploy.yml:24-28` / `control-plane.md:23-36` say workers are created by hand | nothing in-repo records whether they were | gap, not contradiction — A-03 |

## Residual risks and unknowns

- Whether Railway runs any Celery process (A-03), what peer address its edge presents to uvicorn (A-04), and the live schema/provider state are not observable from this environment.
- `tests/security/test_route_auth.py` and `test_idor.py` share the A-01 failure; their invariants are unproven in this checkout.
- The second-pool question (asyncpg + SQLAlchemy per worker) and the `/api/v2/sync` adapter were not traced this pass.
- Nothing above the architecture seams (prompt content, retrieval quality, client UI, hostd behaviour) was reviewed here.

## Strengths to preserve

AST-based layering enforcement that resolves relative/absolute imports and nested scopes (`test_dependency_rules.py`), a single composition root with lazily-connecting adapters and one shared Postgres pool, the six-layer self-hosting scan, pre-first-token-only failover, confirmation/quarantine gating in the kernel, the runtime-DDL ↔ Alembic pin, a triaged reachability matrix with a test that refuses "untriaged", and an edge that applies security headers and a metrics token inside `build_app` so Railway is not naked without nginx.
