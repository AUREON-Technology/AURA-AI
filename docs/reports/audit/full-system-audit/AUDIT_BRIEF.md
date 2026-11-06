# Shared brief for every audit pass (read first)

**Audit:** AURA AI full-system engineering audit, 2026-10-31.
**Baseline:** repository `E:\AURA AI`, branch `main`, HEAD `ffbbb9fff777a7b6117236c67d517fd201ac2208` (short `ffbbb9f`).
Working tree: 5 modified generated Flutter plugin-registrant files (not source), plus untracked `AGENTS.md` and `.claude/skills/system-audit/`. Treat the tree as the baseline.
**Output directory:** `E:\AURA AI\docs\reports\audit\full-system-audit\` (you write only the file(s) you are assigned, there).

## Hard rules

1. **Read-only.** Do not modify any application source, config, dependency, git state, or existing report. Do not run migrations, deploys, or anything that mutates external state. You may run read-only commands (`git log`, `grep`, `pytest --collect-only`, a single targeted `pytest tests/...` that needs no Docker/network/model) and `python -c` imports. No `pip install`, no `docker compose up`, no network probes against production unless explicitly told.
2. **Evidence or `UNKNOWN`.** Every claim of behavior carries `path:line` (relative to repo root) or a command + observed output. If you did not see it, say `UNKNOWN` or `CODE-PRESENT / BEHAVIOR UNVERIFIED`. Never invent line numbers, test results, model names, hardware, or deployment state.
3. **Docs are claims, not proof.** `docs/status/*.md`, `docs/roadmaps/*`, `docs/reports/*`, `CLAUDE.md` and ADRs (`docs/architecture/`) describe what the project believes. Verify against source. Where a doc is contradicted by code, record it as a documentation inaccuracy with both citations.
4. **Implementation ≠ operation.** A class/route/tool existing is not proof it is wired, reachable from a client, run in CI, or healthy in production. Classify on these axes separately.
5. **Preserve the architecture.** Hexagonal layering (`domain/ ports/` stdlib-only → engines → `adapters/ api/` → `container.py`), AIKernel as the only execution path (ADR-006), ProviderRouter as the only inference path, self-hosted-inference invariant (ADR-003/030), confirmation gating, Alembic + existing stores. Recommendations must fit these; flag any proposal that would conflict with an ADR.
6. **No secrets in reports.** If you find a credential, record file + key name, redact the value, describe the exposure.

## Evidence labels (use these exact tokens)

`EXECUTED-HERE` (you ran it, output observed) · `CODE-READ` (read at the cited line, not executed) · `CODE-PRESENT / BEHAVIOR UNVERIFIED` · `DOCUMENTED ONLY` · `NOT RUNNABLE` (say why) · `UNKNOWN` · `STALE` (doc contradicted by tree).

## Status vocabulary for features/components

`VERIFIED WORKING` · `IMPLEMENTED — NOT FULLY VERIFIED` · `PARTIALLY IMPLEMENTED` · `IMPLEMENTED BUT UNREACHABLE` · `BROKEN` · `NOT IMPLEMENTED` · `PLANNED ONLY` · `UNKNOWN`.

## Severity scale for findings

`P0` critical (immediate severe security/data-integrity risk) · `P1` high (major exploitable weakness or critical production failure) · `P2` medium (significant reliability/privacy/correctness concern) · `P3` low · `P4` informational/documentation.

## Required structure of every report you write

1. Title, pass name, baseline SHA, date, author line "Audit subagent pass <X>".
2. Scope and method (what you read, what you ran, what you could not).
3. The inventory/matrix tables the pass asks for.
4. **`## Findings`** — a table with columns: `Local ID` (`<PASS>-01`, `<PASS>-02`…), `Severity`, `Title`, `Subsystem`, `Evidence (path:line / command)`, `Current behavior`, `Expected behavior`, `Recommended action`, `Confidence (High/Med/Low)`. Deduplicate; root causes, not symptoms.
5. **`## Previous-finding recheck`** — for the prior-audit items relevant to your pass (see below), one row each: still open / fixed-verified / fixed-in-source-not-operationally-verified / partially fixed / regressed / superseded / not applicable / cannot verify.
6. **`## Verification log`** — every command you ran, its result, and what you could not run and why.
7. **`## Documentation inaccuracies`** — doc claim vs. code, with both citations.

Also write a sidecar `findings_<pass>.json` next to your report: a JSON array of objects `{id, severity, title, subsystem, evidence:[...], current_behavior, expected_behavior, recommendation, confidence}` mirroring the Findings table.

## Prior-audit material to recheck (read the parts relevant to your pass)

- `docs/reports/AURA_FULL_SYSTEM_REVIEW.md` (2026-09-25, baseline `5db6692`) — sections on your subsystem.
- `docs/roadmaps/AURA_CRITICAL_BACKLOG.md` (CB-01 … CB-44).
- `docs/production/gap_register.yaml` (GAP-001 … GAP-064; statuses are claims).
- `docs/status/BACKEND_STATUS.md`, `DESKTOP_APP_STATUS.md`, `MOBILE_APP_STATUS.md`, `WEB_CONSOLE_STATUS.md` (as of 2026-10-28/31; list of F-xx findings from the 2026-10-26 audit and which are "fixed in code, not CI-run").
- Specific prior concerns every pass must recheck where in scope: PostgresPool concurrency safety; encryption of sensitive server-side data (and ADR-032 server-held data key); agent capability and memory isolation; Personal OS CRUD completeness (Projects/Goals existence); fine-tuning and actual weight updates; evaluation CI enforcement; vision/video; inference concurrency/latency; client-to-backend API reachability; production deployment boundaries (Railway control plane vs HF Space vs Contabo VPS); desktop IDE readiness.

## Repository map (so you don't rediscover it)

- `aura_backend/` Python 3.11+/FastAPI. Core: `aura_core/` (domain, ports, engines: `kernel/`, `llm/`, `planning/`, `execution/`, `tools/`, `safety/`, `memory/`, `knowledge/`, `context/`, `prompts/`, `reasoning/`, adapters under `aura_core/adapters/`), wiring `aura_core/container.py`, gateway `aura_service.py`, v3 routes `aura_core/api/` (and ADR-002 platform adapters `api/v2/{auth,sync,insights}`), Celery `celery_app.py`/`tasks.py`, Alembic `alembic/versions/0001…0019`, config `config.py` + `aura_core/config/`. Tests: `tests/{unit,security,e2e,architecture,deployment,integration,load,benchmarks,training,certification}` (pytest, in-memory adapters, no infra) + root-level legacy `test_*.py` (unittest, `TESTING=True`). venv at `aura_backend/venv` (Python 3.14; use `venv/Scripts/python -m pytest`).
- `aura_client/` Flutter 3.44.6 (Riverpod, go_router, Drift). `lib/core/` (ai clients, auth, database, design_system, platform, security), `lib/features/` (chat, voice, knowledge, memory, tasks, calendar, finance, health, you/settings…), `lib/desktop/` (IDE, agent panel, host panel). Tests `test/`. Flutter SDK is installed at `E:\Flutter\flutter` (3.44.6).
- `aura_hostd/` pure Dart daemon (pairing, RPC over WS to `/api/v3/host/agent`, containment, command policy, PTY, git worktrees, DAP). Tests `test/`.
- `aura_admin/` Next.js 15 console at `/console` (10 pages, proxy to `/api/v3/admin/*`), Playwright e2e against `tests/e2e/mock-gateway.mjs`.
- `deploy/` prod/dev compose (`docker-compose.prod.yml`, `docker-compose.dev.yml`), nginx templates, sandbox Dockerfile, backup, `scripts/` (vps-update, verify-*.sh), `spaces/inference-vllm/` (HF Space). `aura_backend/railway.json` + `scripts/railway-predeploy.sh`. `.github/workflows/` (12 workflows).
- `docs/architecture/` ADR 001–032 (no 012/017; 018–028 Track AP drafts).

## Known live facts (from status docs; treat as claims dated 2026-10-28…31, you cannot probe production)

Railway serves `aureon-ai.tech` at recent `main`; DB migrated to `0016` on 2026-10-30; providers `hosted1` (Groq, ADR-030 hosted slot) healthy and `hfspace` 503; no Ollama in production. GitHub Actions stopped assigning runners ~2026-10-28 19:50 UTC (CB-44); Railway auto-deploys every `main` push without CI. Contabo VPS deploy path dead since 2026-10-18 (host-key change). `main` unprotected.
