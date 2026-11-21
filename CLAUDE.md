# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository shape

Four independent apps in one repo:

- **`aura_backend/`** — Python/FastAPI. The actual product: a self-hosted AI platform (hybrid
  retrieval, memory, tools, planning) behind a v3 HTTP gateway. Read `README.md` and
  `docs/guides/developer-guide.md` before making non-trivial changes here — they cover the
  layering rules and extension points in depth; this file only summarizes what's operationally
  essential.
- **`aura_client/`** — Flutter app (Android/iOS/macOS/web/desktop) that talks to the backend's
  `/api/v3/*` routes.
- **`aura_admin/`** — Next.js operator console. On Railway it is its own host,
  `https://console.aureon-ai.tech/`, served from the root (the image's default base path; old
  `/console/...` links 308 to the same page without the prefix). The VPS stacks still serve it
  under `/console` via nginx, because the compose files pass that base path explicitly. Plan and
  decisions: `docs/plans/console-subdomain-plan.md`. (11 pages; `npm run
  dev|build|lint`, `npm run test:e2e` for Playwright against a mock gateway; `lint` is ESLint
  with Next's rule sets, warnings fail it, and Admin CI runs it from PR #226 on -- no run with that step had executed when this was written). Built, but not
  reachable on any live target as of 2026-11-07. Admin CI ran for the first time on 2026-11-07
  (run #3, dispatched: typecheck, build and Playwright e2e all green).
- **`aura_hostd/`** — pure-Dart local daemon (no Flutter dependency) that lets the desktop Engineer
  agent act on the user's real files and terminal. Pairs with the backend's
  `/api/v3/host/agent` WebSocket. `dart pub get && dart test` (`.github/workflows/hostd-ci.yml`
  gates it — a separate workflow from `client-ci.yml` since it needs `dart-lang/setup-dart`, not
  Flutter). See `docs/architecture/011-desktop-engineer-agent.md` (ADR-011) for the design and
  what is and is not built yet. Server-side persistent Engineer workspaces (durable Docker
  checkouts per user/repo, host-side git, confirmation at the workspace boundary) are ADR-013 /
  ADR-014 — opt-in via `AURA_SANDBOX_ENABLED` + `COMPOSE_PROFILES=sandbox`; Phase J in the
  upgrade roadmap is **built, not proven** until the VPS live checks pass.
  `deploy/scripts/verify-engineer-workspaces.sh` is what produces that evidence — run on the VPS,
  it drives the checks against a real daemon through the gateway's own wiring and writes a report
  to `docs/reports/`. Nothing short of that report proves the phase; a green `pytest` cannot,
  because the default suite's sandbox runner never touches the Docker API.

Supporting directories: `deploy/` (production stack), `docs/` (architecture, guides, reports),
`scripts/` (client build and Firebase helpers). There is no `infra/` directory (audit FSA-120: an
earlier version of this file named a Terraform tree that does not exist). Historical docs live
under `docs/archive/`.

The production stack above (Contabo VPS, `docker-compose.prod.yml`, `deploy-backend.yml`) is
one of two supported deploy targets. `docs/deployment/` documents the other: a Railway Control
Plane (`aura_backend/`) talking to a Hugging Face GPU Space Inference Plane
(`deploy/spaces/inference-vllm/`) over the existing `openai_compatible` provider — read
`docs/deployment/architecture.md` first for the ownership boundary and why this is a logical
split within one repo rather than two physically separate ones. Both targets stay live side by
side until a deliberate cutover; neither workflow redeploys the other.

**Current status lives in `docs/status/`**: one file each for the backend, desktop app, mobile
app and web console, plus an index. Read it before assuming what is deployed or green. These facts
from it change how the paragraph above reads (checked live on 2026-10-28, `main` at `2337577`):

- `aureon-ai.tech` is now served by **Railway** (`server: railway-hikari`), running current
  `main`, with providers `hosted1` (healthy) and `hfspace` (503), and no Ollama.
  `dev.aureon-ai.tech` no longer resolves.
- **Its database was behind the code until 2026-10-30** (CB-43, resolved; on 2026-11-07 live
  `/health/ready` showed `schema.revision 0019`, the current head): it sat at `0013` while
  the code expected `0015`. The owner ran `alembic upgrade head` over `railway ssh` (`0013 → 0016`).
  Live `/health/ready` then showed `healthy: true`, `schema.revision 0016` at `6417c43`. The cause:
  Railway does not run `preDeployCommand` in a shell, so the old inline `echo … && alembic upgrade
  head && …` ran only the `echo` and still passed. `aura_backend/railway.json` now says
  `sh /app/scripts/railway-predeploy.sh` (one argv; pinned by `tests/unit/test_release_identity.py`).
  **Railway does not read `railway.json` for this service**: no config file is linked, so its
  dashboard settings win. On 2026-10-31 the owner set the dashboard's pre-deploy command
  (Settings → Deploy) to `sh /app/scripts/railway-predeploy.sh`. It has since run on a real
  deploy (`4afdd9a8` at `9bfaa22`). The log showed `[HH:MM:SSZ] database url: set`,
  `revision before: 0016 (head)`, alembic's own output, `revision after: 0016 (head)` and
  `=== MIGRATION OK ===`, all before the app container started.

  What still holds:
  - **Edit the dashboard, not just the file.** A change to `railway.json` alone changes nothing
    live. Its health check (`/health/live`) and builder settings are not applied either.
  - Never put `&&`, `||` or `;` into that field.
  - An unprefixed `=== MIGRATION START ===` in a deploy log means the old command is back.
  - That deploy had nothing to apply. Check `/health/ready`'s `schema` block after the first merge
    that adds a migration.
- **Railway auto-deploys every `main` push itself, CI or not.** CI stopped getting runners
  around 19:50 UTC on 2026-10-28 (CB-44: jobs end in 2–4 s with no log). **Runners were seen
  assigned again on 2026-11-06/07** (Repo Hygiene executed; Admin CI run #3 executed and passed),
  but Backend, Client and Host Daemon CI have had no trigger since 2026-10-31, so no application
  suite has run in CI since `dc87beb`. Until a PR touching `aura_backend/` shows a real run, treat
  "CI green" as unavailable — run the suites locally and say so. `main` is still unprotected, so a
  merge ships to production with no gate either way.
- **Deploy VPS has failed on every run since at least 2026-09-27.** Since 2026-10-18 it fails
  with `Host key verification failed`: 169.58.188.43 presents a new ED25519 key. Do not update
  `deploy/known_hosts` to make it pass. A changed host key is either a reinstall or an
  interception, and only the owner can establish which.

The end-user guide (tabs, personas, the Engineer persona's repository tools, troubleshooting) is
published at https://claude.ai/code/artifact/2743a01b-ba92-4473-99e9-04f84996ba8a — not part of
`docs/`, since it's written for people using the app rather than people changing it. It has to be
shared from the artifact's own share menu before this link works for anyone but the account that
published it.

`docs/roadmaps/AURA_MASTER_UPGRADE_ROADMAP.md` is the one canonical roadmap (phases `M0`–`M15`),
reconciled from every earlier plan in `docs/roadmaps/AURA_ROADMAP_RECONCILIATION.md` and backed by
`docs/reports/AURA_FULL_SYSTEM_REVIEW.md`; open actionable items are in
`docs/roadmaps/AURA_CRITICAL_BACKLOG.md`. `docs/AURA_UPGRADE_ROADMAP.md` (phases A–K) is kept as the
historical evidence ledger for work done before 2026-09-25, and `docs/production/gap_register.yaml`
stays the machine-checked gap status. Check these before assuming a capability does or doesn't
exist. ADR-030's hosted open-weight inference (Phases 1–5) is **built, not proven** until
`deploy/scripts/verify-hosted-inference.sh` (on the VPS, or via the *Verify Hosted Inference*
workflow) produces a passing report that is committed (GAP-064). `docs/roadmaps/AURA_ADAPTIVE_PLATFORM_ROADMAP.md` is the canonical plan for the one parallel
track, **Track AP** (AP0–AP13: attaching AURA to existing software systems via connectors, a
Universal Semantic Model, a versioned Project Blueprint and Super-Admin-approved deployment;
ADR-018…028, GAP-049…062). It is gated by M0–M15, not a replacement for them. The standing rule applies to any work you do here: **don't mark something done without the
evidence its exit criteria name** — "built" and "tested" are not "proven."

## Commands

### Backend (`cd aura_backend` first)

```bash
# The one sanctioned local environment: Python 3.11, both lockfiles, hashes checked (audit G-07).
# Anything else may run the suite but its result does not stand for the locked one; conftest
# warns at session start when a watched package differs (scripts/lock_drift.py).
uv venv --python 3.11 .venv && source .venv/bin/activate
pip install --require-hashes --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.lock
pip install --require-hashes -r requirements-dev.lock   # separate: pip resolves each command on its own

pytest                                   # aura_core suite — no Docker, no network, no model
pytest tests/some_test.py::TestClass::test_name   # single test
pytest tests/architecture -q             # layering fitness functions, on their own for signal

TESTING=True python -m unittest discover -p "test_*.py"   # legacy tree — separate suite, also gated in CI

pytest tests/load -m load -s             # opt-in; needs a real Postgres/Qdrant/Redis/Ollama stack
pytest tests/benchmarks -m bench         # opt-in; wall-clock, excluded from default run

alembic upgrade head                     # migrations, against AURA_DATABASE_URL
```

Both suites (`pytest` and the legacy `unittest`) run in CI and both must pass — they cover
different trees (ADR-002 strangler migration) and neither substitutes for the other.

Regenerate the lockfile with the exact command in `requirements.lock`'s own header comment (uses
`uv pip compile` with a CPU-only PyTorch index — don't hand-edit pins).

### Client (`cd aura_client` first)

```bash
flutter pub get
dart run build_runner build --delete-conflicting-outputs   # Drift codegen; needed after touching
                                                             # database.dart or anything it generates from
flutter analyze                          # fails the CI gate on info-level lints too, not just warnings/errors
flutter test
flutter test test/some_test.dart         # single file
flutter build apk --release --dart-define=BACKEND_URL=<url>
```

**Client CI runs again (since 2026-10-28), but is red for reasons outside most diffs.** After
weeks with no runner, runs 142–143 executed every step:
- `analyze-and-test` and `build-linux` are green (analyze clean, 381 tests passed);
- `build-android` fails because the runner runs out of disk (Gradle `mergeJavaRes`);
- `build-windows` (`continue-on-error`) fails 10 `path_containment_test.dart` cases on 8.3
  short paths.

**Update 2026-11-08:** all four jobs went green on #213–#215, Android and Windows included, and
`build-windows` is no longer `continue-on-error`: a red Windows job now fails the run, so the
run's overall tick means something again. Windows' coarse clock has already exposed one real bug
(#215), so treat a Windows-only failure as a possible defect, not noise. Host Daemon CI's
last executed run (`dc87beb`) was red: on Linux a `PosixPtySession` isolate hung until the
15-minute timeout, and on Windows 5 tests failed. Both are fixed on `main` (#185; the Linux suite
now exits in about 16 s locally), but no Host Daemon CI run has executed since, because nothing
under `aura_hostd/` has changed.

No Flutter SDK is preinstalled in the environment a change is usually written in, but one can be
fetched in about two minutes, and `analyze` + `test` then run exactly as CI would:

```bash
curl -sSL -o flutter.tar.xz \
  https://storage.googleapis.com/flutter_infra_release/releases/stable/linux/flutter_linux_3.44.6-stable.tar.xz
tar xf flutter.tar.xz && export PATH="$PWD/flutter/bin:$PATH"   # Dart 3.12.2, the CI pin
cd aura_client && flutter pub get \
  && dart run build_runner build --delete-conflicting-outputs \
  && flutter analyze && flutter test
```

Do that before claiming a client change works — and quote the actual counts. What it still cannot
settle, because the host is headless and has no device: font fallback per platform, screen-reader
output, and any runtime performance figure.

### Host daemon (`cd aura_hostd` first)

```bash
dart pub get
dart analyze --fatal-infos
dart test
dart test test/some_test.dart            # single file
dart run bin/aura_hostd.dart run          # connect and serve until killed
```

Same toolchain caveat as the client: this environment often has no Dart SDK either. Host Daemon
CI (`.github/workflows/hostd-ci.yml`) is the actual verification.

## Backend architecture — the parts that span files

**Hexagonal layering, enforced by AST-based fitness tests in `tests/architecture/`, not just
documented:**

```
domain/  ports/          stdlib only — entities, value objects, Protocols
engines                  domain + ports only (prompts, context, memory, knowledge, tools,
                         safety, planning, reasoning, execution, llm)
adapters/  api/          vendor SDKs, imported lazily inside functions, not at module scope
container.py             the only place concrete meets abstract
```

The arrow never reverses. `container.py` is the single wiring point — every adapter is
constructed lazily (a class's `__init__` never makes a network call; the first real use does,
behind an `asyncio.Lock` when concurrent callers could race). `PostgresPool._get_pool()` is the
canonical example; match that pattern for any new adapter.

**Self-hosting is a fitness-tested invariant, not a preference.** `aura_core/llm/registry.py`
builds `echo`, `openai_compatible` (vLLM/llama.cpp/TGI/Ollama's `/v1`), `ollama`, and one gated
vendor kind, `claude` (ADR-033, below). Any other hosted-vendor branch fails
`tests/architecture/test_no_external_inference.py`. The Flutter client is checked too. The first
sanctioned exception is ADR-030 (Proposed). Hosted *open-weight* APIs (Groq, Workers AI,
OpenRouter, …) are a third host category in `aura_core/config/external_inference.py`. They are
refused unless `AURA_ALLOW_HOSTED_INFERENCE=true`, only accept open-weight model IDs, can never be
`is_local`, and may never be named in Python source. One that may train on prompts (any `:free`
model ID) also needs its slot's `ALLOW_PROMPT_TRAINING=true` and can never be raised to `sensitive`. Closed-model vendors stay banned under
either opt-in flag. The one closed-model exception is ADR-033 (Proposed; built, not proven): provider kind
`claude` (Anthropic), refused unless `AURA_ALLOW_CLAUDE_INFERENCE=true`, no `base_url` (SDK endpoint
only), key only via `AURA_PROVIDER_<NAME>_API_KEY_ENV`, third-party semantics (internal ceiling, no
images, audited), and the `anthropic` SDK importable only from `aura_core/llm/providers/claude.py`
(`SANCTIONED_SDK_IMPORTERS`). With `AURA_LLM_COMPLEXITY_ROUTING=true`, interactive turns whose message
scores as complex (`aura_core/llm/complexity.py`) prefer the `reasoning` pool that provider declares;
everything else routes as before. Every generation also has a data classification (`llm/sensitivity.py`). A
hosted provider receives only `internal` content by default. An unclassified generation counts
as `sensitive`, so any new background caller of the router stays local unless it opts in.

**`AIKernel` (`aura_core/kernel/kernel.py`) is the only execution path.** It plans, then executes
tools, then generates. A model's own emitted `tool_calls` frames are informational only — they do
not execute anything (ADR-006); planning/execution is what runs tools, behind capability grants
and taint checks.

**`ProviderRouter` (`aura_core/llm/router.py`)** implements five routing policies (`LOCAL_FIRST`,
`CHEAPEST`, `FASTEST`, `QUALITY_FIRST`, `BALANCED`) over providers declared as data
(`ProviderCapabilities`), with per-provider circuit breakers and failover — but **only before the
first streamed token**; once output has reached the caller, a stream failure surfaces as an error
rather than silently retrying elsewhere, because failover after that point would duplicate or
contradict what the user already saw.

**Trust channels (SYSTEM/USER/DATA) gate what content can do.** Retrieved knowledge and tool
output travel on `DATA`, fenced with a per-turn nonce; a turn whose context was quarantined
cannot run a `side_effecting` tool without explicit confirmation (ADR-006). This is the mechanism
that stops a poisoned document from silently triggering a write.

**Every storage port has a real in-memory adapter**, not a mock — this is why the default
`pytest` run needs no infrastructure at all, and why `tests/conftest.py`'s `container` fixture is
pinned to the in-memory substrate on purpose (see its own docstring). Real Postgres/Qdrant/Redis
adapters exist for production and are only exercised by the opt-in `tests/load` suite and
targeted integration tests using `testcontainers`.

**Alembic is the only schema authority (CB-20).** No adapter creates or alters a table; the old
`CREATE TABLE IF NOT EXISTS`-on-first-use bootstrap is gone, and
`tests/architecture/test_no_runtime_ddl.py` fails on any DDL string under `aura_core`. A schema
change is a new migration in `alembic/versions/` with a working `downgrade()`;
`tests/load/test_migration_roundtrip_postgres.py` undoes and re-applies every revision on a real
Postgres and requires each one after `0016` to restore its parent's schema exactly. An unmigrated
database now fails requests as well as `/health/ready`, so both deploy targets must keep running
`alembic upgrade head` before the app starts (Railway's pre-deploy command, `vps-update.sh`).

**v3 is the only live HTTP surface.** `aura_service.py` is a v3-only gateway that never imports
the legacy `main.py`-era app. `api/v2/{auth,sync,insights}` are a deliberate exception —
permanent platform adapters mounted outside `aura_core` (ADR-002), not legacy code awaiting
removal.

Full ADR list and the ten essential decisions are in `docs/architecture/00-overview.md` and
`docs/architecture/001-*.md`–`037-*.md` (there is no 012 or 017; 018–028 are Track AP drafts, Proposed); read the relevant one before touching
retrieval, memory, routing, safety, indexing, desktop hostd (011), engineer workspaces
(013/014), service keys / caller context / structured output on `/chat` (029), images on
`/chat` (031), the Claude provider and complexity routing (033), customer workspaces and team scopes (034), adaptive knowledge updates (035), the `tools/open` analysis tools (036), or the console's Super-Admin control center (037) — each encodes a reason something is shaped the way it is, not just what it does.

## Client architecture — the parts that span files

- State via Riverpod (`Notifier`/`ConsumerWidget`), routing via `go_router`, local persistence via
  Drift (SQLite) synced against `/api/v3/sync` through a generic cursor protocol
  (`SYNCABLE_MODELS` in `core/database/database.dart`).
- `core/design_system/` (`AuraButton`, `AuraTextField`, `AuraCard`, `AuraSnackbar`, `AuraSpacing`,
  etc.) is the UI kit — use it for new screens rather than raw `Material` widgets, to stay visually
  and behaviorally consistent with the rest of the app.
- **A backend endpoint shipping does not mean the client can reach it.** This codebase's history
  has repeatedly shipped a v3 API and left it with zero client callers for a while — check whether
  a feature you're asked to touch actually has client-side plumbing before assuming it does.
- Each API area gets its own thin client class under `core/ai/*` or `core/*` (e.g.
  `KnowledgeCenter`, `VoiceClient`) wrapping `package:http`, attaching the JWT and device
  attestation headers (`DeviceValidationService.getAttestationHeaders()`) the same way every other
  client does — copy an existing one's shape rather than inventing a new auth pattern.

## Deploy — what's easy to get wrong

- **Production VPS layout**: the checkout lives at `/opt/aura-stack` (not `/opt/aura`) — a single
  git clone that `deploy/scripts/vps-update.sh` locates automatically (see its own `find`
  fallback if `AURA_REPO` isn't set). Two independent Compose projects run side by side on that
  host: `aura` (prod, `deploy/docker-compose.prod.yml`) and `aura-dev`
  (`docker-compose.dev.yml`) — `docker compose ls` shows both. `vps-update.sh` only ever touches
  `aura`; the dev stack needs its own explicit restart. **The two files are meant to stay at
  parity on isolation settings** (network `internal:` flags, socket-proxy env allowlists, which
  images are built rather than pulled) — they share a host, so hardening applied to only one of
  them leaves the weaker stack as the real attack surface. Change a security-relevant key in one
  and change it in the other in the same commit.
- **On the VPS stack, `nginx` is the only container with a published port** (SEC-12) — `gateway`
  binds no host port there. On Railway there is no nginx: uvicorn is the edge, which is why the
  header floor and the metrics gate live in `build_app` (`tests/unit/test_security_headers.py`).
  Reaching the VPS gateway directly means `docker exec`-ing into another container on the same
  Docker network (or using `deploy/nginx/host-proxy.conf.example`'s override pattern for a
  shared-host setup).
- **Never re-add `http2 on;` to the HTTPS server blocks in
  `deploy/nginx/templates/aura.conf.template`** (or its `host-proxy.conf.example` mirror). Any
  client whose TLS ALPN offers h2 — curl by default, and Dart's `dart:io` WebSocket client the
  Flutter app uses — gets negotiated onto HTTP/2, which forbids the legacy `Connection`/`Upgrade`
  header fields the `/api/v3/voice/live` WebSocket handshake depends on; the request silently
  degrades to a plain GET and 404s, while the identical request succeeds hitting the gateway
  directly over HTTP/1.1. nginx can't disable HTTP/2 per-location (ALPN happens before request
  routing even starts), so this is a deliberate server-block-wide HTTP/1.1-only tradeoff, not a
  location-level fix — confirmed live and fixed in PR #65 after two other hypotheses (missing
  location block, stale rendered config) were individually ruled out first.
- **Hosted open-weight slots (ADR-030) are one YAML anchor, `x-hosted-providers`, in both compose
  files.** It is merged into the gateway *and* every worker. Workers never route to a slot
  (their generations are unclassified, hence SENSITIVE), but they construct every provider
  `AURA_LLM_PROVIDERS` lists, so a slot missing from a worker is a boot failure.
  `tests/unit/test_hosted_provider_compose_plumbing.py` pins the anchor's coverage, prod/dev
  parity, and the `hosted{1,2,3}_api_key` placeholders `bootstrap-secrets.sh` must create.
- **Never run a sandbox command with `docker exec`** (`exec_run`, `exec_create`/`exec_start`) in
  `aura_core/adapters/docker_sandbox.py`. The gateway reaches the daemon only through
  `docker-socket-proxy`, which sets `EXEC: "0"` in both compose files, so those calls come back
  **403** — and the flag cannot be narrowed to the sandbox, because the proxy's allowlist is
  daemon-wide: allowing exec so a workspace could be entered would equally allow entering
  `postgres`, `ollama` and `nginx`. Each command therefore runs as its own throwaway container
  over the workspace's bind mount, which needs only `CONTAINERS`. That is sound because a
  workspace's state is the checkout on the host, not anything living in a process — the same
  claim the hot/warm design already rests on when it stops a workspace between turns. This is
  invisible to the test suite (the in-memory runner never touches the Docker API, and no CI job
  stands up the socket proxy), so `tests/unit/test_sandbox_exec_surface.py` pins both halves: the
  adapter never execs, *and* both compose files still refuse it. Found in #75 as a latent blocker
  that would have made every `run_command` 403 on the first real run.
- **The sandbox image is built, never pulled.** `deploy/sandbox/Dockerfile` (digest-pinned base,
  uid 10001 matching the bind-mount `chown` in `bootstrap-host.sh`) is built by the
  `sandbox-image` compose service and by `Backend CI`'s `docker-build` job. That is what lets
  `IMAGES: "0"` stay off the socket proxy's allowlist — adding a runtime `docker pull` would
  silently require putting it back.
- **The self-hosted LLM (`qwen2.5:3b-instruct-q4_K_M` on Ollama, CPU-only) sustains roughly
  5–10 tokens/sec on the VPS stack**, degrading further as context grows. The Railway target that
  serves `aureon-ai.tech` today runs no Ollama at all: its only healthy provider is the hosted
  open-weight slot `hosted1` (ADR-030), so "production" throughput is that provider's, not this.
  The sizing rule below still binds every completion call, because the VPS stack is a supported
  target. Any new completion call
  needs its `max_output_tokens` sized so a worst-case generation actually finishes inside its
  `timeout_s` at that throughput, or the provider call gets hard-cancelled mid-generation
  (`AllProvidersFailedError`) before ever returning something parseable.
  `PlanningEngine.plan()`'s call (`aura_core/planning/engine.py`) is the worked example — 700
  tokens against the deployed 180s `request_timeout_s`, with the full reasoning in its own
  comment — after the previous 1200-token ceiling was measured taking up to 240s in production.
- **nginx holds static addresses** (`10.77.10.10` on `edge`, `10.77.11.10` on `aura_edge`) and the
  prod and dev gateways trust `X-Forwarded-For` from exactly those (`--forwarded-allow-ips`), never
  `'*'` (audit FSA-012). Change an address in one place and change it in the other in the same
  commit; `tests/unit/test_compose_security_parity.py` pins both and that each address lies in its
  network's declared subnet. Compose does not recreate an existing network whose `ipam` changed:
  it stops with "network needs to be recreated", and `aura_edge` is shared with the dev stack's
  containers. The first deploy after a subnet change is therefore manual: stop the dev stack,
  `docker network rm aura_edge` (and `edge` if it changed), deploy prod, start dev. Until that is
  done nginx cannot start, because a static address needs a user-configured subnet, and nginx is
  the only published port. Reasoned from Compose's behaviour, not yet run on the VPS.
- **The Postgres connection budget is arithmetic, not a comment.** Every uvicorn worker and every
  Celery prefork child holds one asyncpg pool plus the legacy SQLAlchemy pool
  (`InfrastructureSettings.postgres_connections_per_process`); both compose files size them per
  service (gateway 20 per worker, Celery children 4) so prod's worst case is 108 against
  `max_connections = 120`. `tests/unit/test_postgres_connection_budget.py` recomputes it from the
  compose files and `postgresql.conf` and fails when a change breaks it (audit FSA-073).
- Push-triggered pipeline: `Deploy VPS` (`.github/workflows/deploy-backend.yml`) fires via
  `workflow_run` only after `Backend CI` succeeds on `main` — merging to `main` is the entire
  deploy action, there is no separate manual step. `Deploy Control Plane (Railway)` fires the
  same way, but Railway's own GitHub auto-deploy also ships every `main` push and is what
  actually reached `aureon-ai.tech` for the last merges — so the CI gate does not hold for
  Railway (CB-44). Deploy VPS is currently blocked by the host-key change described near the top
  of this file.

## Testing conventions worth knowing before writing a test

- Tests assert **behavior**, not call choreography — nothing internal is mocked; the in-memory
  adapters are the real substrate, so a test exercises the same engine code production runs.
- Useful backend fixtures (`aura_backend/tests/conftest.py`): `container` (fully wired, in-memory),
  `knowledge`, `memory`, `tools`, `router`, `safety`, `echo` (deterministic provider), `clock`
  (`FrozenClock` — decay/circuit-breaker timing is testable without sleeping).
- A change that makes the default `pytest` suite need Docker, network, or a real model is wrong by
  construction — that capability belongs in `tests/load` (opt-in, `-m load`) instead.
- When a change touches a Postgres-backed adapter, verify it against a real (even throwaway)
  Postgres instance before considering it done — asyncpg's type mapping (e.g. `Decimal` from
  `avg()`/`percentile_cont`) has caused real bugs the in-memory adapter can't surface.
- A real-Postgres test must build its database with `alembic upgrade head`
  (`tests/postgres_migrations.py`: `migrated_database()`, `migrate()`), never by letting an
  adapter create its tables — adapters no longer do.
