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
- **`aura_admin/`** — Next.js admin dashboard (lightly used so far; `npm run dev|build|lint`,
  `npm run test:e2e` for Playwright).
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
`infra/` (Terraform), `scripts/` (client build and Firebase helpers). Historical docs live under
`docs/archive/`.

The production stack above (Contabo VPS, `docker-compose.prod.yml`, `deploy-backend.yml`) is
one of two supported deploy targets. `docs/deployment/` documents the other: a Railway Control
Plane (`aura_backend/`) talking to a Hugging Face GPU Space Inference Plane
(`deploy/spaces/inference-vllm/`) over the existing `openai_compatible` provider — read
`docs/deployment/architecture.md` first for the ownership boundary and why this is a logical
split within one repo rather than two physically separate ones. Both targets stay live side by
side until a deliberate cutover; neither workflow redeploys the other.

The end-user guide (tabs, personas, the Engineer persona's repository tools, troubleshooting) is
published at https://claude.ai/code/artifact/2743a01b-ba92-4473-99e9-04f84996ba8a — not part of
`docs/`, since it's written for people using the app rather than people changing it. It has to be
shared from the artifact's own share menu before this link works for anyone but the account that
published it.

`docs/AURA_UPGRADE_ROADMAP.md` is the current source of truth for what's actually built vs. only
claimed — it's baselined against a specific commit and corrects stale claims with file:line
citations. Check it before assuming a capability does or doesn't exist. Its standing rule also
applies to any work you do here: **don't mark something done without the evidence its exit
criteria name** — "built" and "tested" are not "proven."

## Commands

### Backend (`cd aura_backend` first)

```bash
pip install -r requirements.lock -r requirements-dev.txt

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

There is often no Flutter/Dart toolchain available in the environment a change is written in.
Write Dart carefully against the existing code's conventions and let Client CI (`flutter
analyze` + `flutter test` + the Android build) be the actual verification — don't claim a client
change is confirmed working unless you watched it pass CI.

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
only builds `echo`, `openai_compatible` (vLLM/llama.cpp/TGI/Ollama's `/v1`), and `ollama`
providers. There is no hosted-vendor branch anywhere — adding one fails
`tests/architecture/test_no_external_inference.py`. The Flutter client is checked too.

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

**v3 is the only live HTTP surface.** `aura_service.py` is a v3-only gateway that never imports
the legacy `main.py`-era app. `api/v2/{auth,sync,insights}` are a deliberate exception —
permanent platform adapters mounted outside `aura_core` (ADR-002), not legacy code awaiting
removal.

Full ADR list and the ten essential decisions are in `docs/architecture/00-overview.md` and
`docs/architecture/001-*.md`–`014-*.md` (there is no 012); read the relevant one before touching
retrieval, memory, routing, safety, indexing, desktop hostd (011), or engineer workspaces
(013/014) — each encodes a reason something is shaped the way it is, not just what it does.

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
- **`nginx` is the only container with a published port** (SEC-12) — `gateway` binds no host
  port, so reaching it directly means `docker exec`-ing into another container on the same
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
  5–10 tokens/sec in production**, degrading further as context grows. Any new completion call
  needs its `max_output_tokens` sized so a worst-case generation actually finishes inside its
  `timeout_s` at that throughput, or the provider call gets hard-cancelled mid-generation
  (`AllProvidersFailedError`) before ever returning something parseable.
  `PlanningEngine.plan()`'s call (`aura_core/planning/engine.py`) is the worked example — 700
  tokens against the deployed 180s `request_timeout_s`, with the full reasoning in its own
  comment — after the previous 1200-token ceiling was measured taking up to 240s in production.
- Push-triggered pipeline: `Deploy VPS` (`.github/workflows/deploy-backend.yml`) fires via
  `workflow_run` only after `Backend CI` succeeds on `main` — merging to `main` is the entire
  deploy action, there is no separate manual step.

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
