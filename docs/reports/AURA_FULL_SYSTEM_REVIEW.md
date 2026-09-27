# AURA AI — Full System Review

**Date:** 2026-09-25 · **Baseline:** `5db6692` (= `origin/main`, merge of PR #109) · **Scope:** the
whole repository, every roadmap/audit/ADR in the tree, GitHub Actions history, compose/deploy
configuration.

**Companion documents (produced together, same baseline):**
[Master roadmap](../roadmaps/AURA_MASTER_UPGRADE_ROADMAP.md) (the one canonical plan from here on) ·
[Roadmap reconciliation](../roadmaps/AURA_ROADMAP_RECONCILIATION.md) ·
[Critical backlog](../roadmaps/AURA_CRITICAL_BACKLOG.md).

Evidence labels used everywhere in this report:

| Label | Meaning |
| --- | --- |
| **EXECUTED-HERE** | Run during this review, in this container, output observed |
| **VERIFIED-API** | Observed via the GitHub API during this review |
| **CODE-READ** | Read in source at the cited line; behavior not executed |
| **CODE-PRESENT / BEHAVIOR UNVERIFIED** | Code exists; nothing observed running it |
| **DOCUMENTED ONLY** | Claimed in a doc; no code/test/run found backing it |
| **NOT EXECUTED / NOT RUNNABLE** | A test or check that could not run here (no Dart/Flutter SDK, no GPU, no VPS access) |
| **UNKNOWN** | Evidence not available to this review. Never upgraded to VERIFIED. |
| **STALE** | A documented claim that the current tree contradicts |

---

## 1. Executive Summary

AURA is a **well-architected, heavily tested, honestly self-documenting single-tenant-per-user AI
platform whose delivery pipeline is broken.** The architecture is sound and enforced in code; the
code base passes its own suites; but nothing merged since **2026-09-13** has been through CI or
reached production, and the process that should stop unreviewed code reaching `main` does not
exist.

Five facts dominate everything else:

1. **CI has been dead on `main` since 2026-09-13, not 2026-09-22.** Backend CI run #220
   (`78bdc83`, 2026-09-13 14:34Z) is the last success; every later `main` run's jobs complete in
   2–5 s with no steps executed (VERIFIED-API: runs #222–#267, job ids e.g. `108130701162`). The
   gap register (GAP-043) dates it to 2026-09-22 — **STALE by nine days**.
2. **Production is ~25 merges behind `main`.** The last successful `Deploy VPS` is run #151
   (2026-09-13, `78bdc83`); runs #152–#175 were all *skipped* because deploy is gated on Backend CI
   (VERIFIED-API). Every fix recorded since — the 11 vulnerabilities of the P1.5 security pass
   (GAP-048), the cross-user ingestion leak (GAP-004), encryption at rest, idempotency — is
   **not deployed**, unless someone deployed out-of-band (UNKNOWN; check
   `git -C /opt/aura-stack rev-parse HEAD` on the VPS).
3. **`main` is unprotected and still receives direct pushes** — four on 2026-09-25 alone
   (`61caac0`, `35f0371`, `d3e2918` — a path-resolution security change — and `8e26156`), none
   gated by any CI because none runs.
4. **Two confirmation-gate weaknesses let side effects run without the approval the design
   promises** (§11, SEC-01, SEC-02): confirmation is not bound to what the user saw, and the
   ADR-014 "workspace-local" exemption applies unchanged when the Engineer's tools are routed to the
   user's real machine through `aura_hostd`.
5. **No real-model quality number exists anywhere.** Every gated quality metric runs against a
   scripted provider (`EchoProvider`, `ContextGroundedProvider`) or a non-semantic hash embedder;
   the only real-model latency number (p95 70.5 s, CPU, 3B model) proves the system is
   single-flight and unfit for more than one concurrent AI user.

What is genuinely strong: hexagonal layering enforced by AST fitness tests; a single kernel
execution path with model `tool_calls` never dispatched (`kernel.py:644,1392,1434` pass
`tools=()`); owner-scoping applied *in-query* for vectors and memories; an unusually honest
documentation culture that repeatedly corrects its own claims. **2,707 backend tests pass with 0
failures and 100 legacy tests pass** (EXECUTED-HERE).

**Verdict (§40):** AURA is a *production-capable architecture* at *pre-production maturity*, with
*zero* production-certified subsystems and a **broken evidence pipeline that is the single
critical-path blocker.** It is not production-ready for any multi-user workload.

---

## 2. Audit Method

1. **Document discovery:** 130 Markdown files indexed by path, line count, md5 prefix, first/last
   commit date (§3). No two files are byte-identical; duplication is semantic, not literal.
2. **Roadmap reading:** every phase of `docs/AURA_UPGRADE_ROADMAP.md` (A–K),
   `docs/production/MASTER_PRODUCTION_ROADMAP.md` (0–26) + `gap_register.yaml` (48 gaps),
   `docs/plans/AURA_FULL_UPGRADE_ROADMAP.md` (DPL-0–11, IDE-0–24, Platform-1–20, its own A–R
   sequence), the 2026-09-13 audit, the 2026-09-25 desktop Phase-0 audit, ADR-001–016.
3. **Claim verification:** each load-bearing claim re-derived from source with `rg`/targeted
   reads; file:line citations below are at `5db6692`.
4. **Execution:** backend dependencies installed from `requirements.lock` +
   `requirements-dev.txt` into a scratch venv (Python 3.11.15, Linux).
   - `pytest` (default markers): **2707 passed, 56 skipped, 0 failed**, exit 0.
   - `TESTING=True python -m unittest discover`: **Ran 100 tests, OK (skipped=4)**.
   - `aura_client` (Flutter), `aura_hostd` (Dart), `aura_admin` Playwright: **NOT RUNNABLE**
     (no Dart/Flutter SDK; admin not attempted).
   - `tests/load`, `tests/benchmarks`, `tests/training`: **NOT EXECUTED** (need real infra/GPU).
5. **CI/CD:** GitHub Actions run and job history pulled via API (§28).
6. **No VPS, Railway, or Hugging Face access.** Every statement about live deployments is
   inferred from CI/deploy history and marked UNKNOWN where it cannot be observed.

---

## 3. Repository Inventory

| Component | Size | Tests | Notes |
| --- | --- | --- | --- |
| `aura_backend/aura_core/` | 31 packages, ~57k LOC Python | 155 `test_*.py` under `tests/` (unit, security, e2e, architecture, deployment, integration, load, benchmarks, training) | Hexagonal core |
| `aura_backend/` platform (`api/`, `services/`, `infrastructure/`) | ADR-002 permanent adapters | 16 root-level legacy `test_*.py` (unittest) | Deliberately outside the core |
| `aura_client/lib/` | 260 Dart files, ~51k LOC (43 under `desktop/`) | 44 `*_test.dart` | NOT RUNNABLE here |
| `aura_hostd/lib/` | 16 files, ~4.1k LOC Dart | 9 test files | NOT RUNNABLE here |
| `aura_admin/` | Next.js 15, 9 console areas | 2 Playwright specs + mock gateway | Admin CI never runs on PRs (GAP-007) |
| `deploy/` | prod compose (17+ services), dev compose, nginx, backup, sandbox, 2 HF Space variants | `tests/unit/test_sandbox_exec_surface.py` etc. pin compose invariants | |
| `.github/workflows/` | 10 workflows | — | 2 are structurally invalid (§28) |
| Alembic | 8 revisions (`0001`–`0008`) | — | Plus **26 runtime-DDL tables** in 15 `aura_core` adapters (§25) |

### 3.1 Document index (authoritative status as of this review)

| Document | Date (first→last commit) | Baseline | Purpose | Status | Supersedes | Superseded by |
| --- | --- | --- | --- | --- | --- | --- |
| `docs/roadmaps/AURA_MASTER_UPGRADE_ROADMAP.md` | 2026-09-25 | `5db6692` | Canonical plan | **AUTHORITATIVE** | all roadmaps below | — |
| `docs/AURA_UPGRADE_ROADMAP.md` | 09-12→09-25 | `8286b1a` (claims 2026-08-22) | Evidence ledger A–K | **Historical evidence ledger** (keep for its per-phase evidence); forward plan superseded; contains a duplicate "Phase J" ID and a stale §1 "still open" list | V2 blueprint §13 | Master roadmap |
| `docs/production/MASTER_PRODUCTION_ROADMAP.md` | 09-24 | `11abb50` | 27-phase production plan | **Superseded** (content merged into M-phases) | — | Master roadmap |
| `docs/production/gap_register.yaml` / `GAP_REGISTER.md` | 09-24→09-25 | `11abb50` | Machine-checked gap ledger | **AUTHORITATIVE for gap status** (schema-tested); 7 entries STALE (§39.3) | 09-13 audit | — |
| `docs/plans/AURA_FULL_UPGRADE_ROADMAP.md` | 09-25 | none stated | DPL/IDE/Platform plan | **Superseded**; conflicts with ADR-003 intent on inference hosting and with `docs/deployment/architecture.md` on repo split | — | Master roadmap |
| `docs/reports/FULL_SYSTEM_ARCHITECTURE_AUDIT.md` | 09-13 | `dd88f95` | 40-section audit, 56/100 | **Historical**; findings re-verified in gap register | analysis/0x | This review |
| `docs/reports/desktop-ide-phase0-audit-2026-09-25.md` | 09-25 | ~`5097858` | Desktop code-read audit | **Current for desktop** (note: its "VERIFIED" = code-read, not executed) | ADR-015 status lines | — |
| `docs/implementation/aura_execution_state.md` | 09-23 | 09-22 | State matrix | **Obsolete** (pre-dates the 09-23 breakage/repair) | — | Gap register |
| `docs/deployment/*.md` | 09-24 | — | Railway + HF two-plane | **Current, unproven**; §1 logical-split decision is retained | — | — |
| `docs/architecture/000–016` | 09-12→09-25 | — | ADRs | **Authoritative** (ADR-014 needs an amendment, §11 SEC-02) | — | — |
| `docs/analysis/01–07` | 09-12 | Aug | Early analysis | **Obsolete** | — | 09-13 audit |
| `docs/reports/AURA_AI_PRODUCTION_READINESS_SCORE.md`, `AURA_AI_FINAL_IMPLEMENTATION_REPORT.md`, `AURA_AI_CURRENT_ARCHITECTURE.md` | 09-12 | Aug | Scores/claims | **Historical reference** | — | This review |
| `docs/reports/phase-a1..a4`, `phase-3-loadtest-harness-self-validation.md`, `benchmark-report.md`, `performance-report.md` | 09-12/09-25 | — | Measurements | **Reference measurements** (conditions in §29) | — | — |
| `Doc/AURA AI — Current System Re-Assessment.md`, `docs/reports/System Report Explanaition.md`, `Doc/Modern Agent Systems.md`, `Doc/Topics…` | 09-12/09-23 | — | Pasted chat transcripts / talking points | **Informal, non-authoritative**; contain unverified scores | — | This review |
| `.opencode/plans/desktop_ide_d0-d3…plan.md` | 09-13 | — | Tool-generated plan | **Obsolete** (D0–D3 executed) | — | ADR-015 |
| `docs/plans/phase-h-live-voice-chat.md`, `phase-i-nlp.md` | 09-12 | — | Designs | **Current design, unproven** | — | — |
| `docs/plans/{migration,refactor,risk-assessment,testing}-plan.md` | 09-12 | Aug | Early plans | **Obsolete** | — | Master roadmap |
| `docs/archive/**` | — | — | Archive | **Archived** (correctly) | — | — |
| `ML_ENVIRONMENT_REPORT.md` (root), `test_personal_os.py` (root) | 09-23 | — | Strays from a direct push | **Hygiene defect** (GAP-010) | — | — |

---

## 4. Actual Architecture (verified)

```
Flutter mobile/web/desktop ──HTTPS/WSS──► nginx (only published port, SEC-12)
aura_admin (Next.js) ───────────────────► │
aura_hostd (desktop daemon) ──WSS─────────► gateway: aura_service.py (FastAPI, v3-only, 4 uvicorn workers)
                                             │  build_app(): /api/v3/* + ADR-002 platform routers
                                             ▼
                                        AIKernel (kernel.py) — the only execution path
                         ┌───────────────────┼─────────────────────────────┐
                 context gather (memory,   PlanningEngine → ExecutionEngine /   ProviderRouter (5 policies,
                 RAG, history, goals, CAG)  AgenticExecutor (budgets)           circuit breaker, pre-first-token
                                            → ToolRegistry → ToolPolicy          failover, admission control)
                                                                                     │
      Postgres (Alembic + runtime DDL) · Qdrant · Redis · Celery (realtime/ingestion/  ├─ ollama (CPU, qwen2.5:3b, NUM_PARALLEL=1)
      learning/training queues) · beat · outbox drain · Prometheus/Loki/Grafana        ├─ vllm (compose `gpu` profile, never run)
                                                                                     └─ hfspace (openai_compatible, opt-in, never proven)
      Engineer: CompositeSandboxRunner → Docker sandbox (opt-in, off in prod) | HostAgentSandboxRunner → aura_hostd on user's machine
```

Verified properties:

| Property | Evidence | State |
| --- | --- | --- |
| Layering enforced | `tests/architecture/` (AST dependency rules) in the 2707-pass run | TESTED (EXECUTED-HERE) |
| Only self-hosted provider kinds | `aura_core/llm/registry.py`; `tests/architecture/test_no_external_inference.py` | TESTED |
| Model `tool_calls` never executed | `kernel.py:644,1392,1434` `tools=()` | CODE-READ + TESTED |
| v3-only gateway, legacy unmounted | `aura_service.py:201-225`; `tests/unit/test_v1_locked_routes.py` | TESTED |
| Single wiring point | `aura_core/container.py` | CODE-READ |
| Owner scoping in-query (vectors) | `adapters/qdrant_store.py:397-442` | CODE-READ; see SEC-09 caveat |
| Lazy adapters behind lock | `PostgresPool._get_pool()` double-checked lock; `tests/unit/test_postgres_pool_concurrency.py` | TESTED |

## 5. Target Architecture

The target is unchanged from ADR intent and is endorsed by this review:

```
Clients (mobile, desktop IDE, admin) → Control Plane (gateway + kernel + workers) → ProviderRouter
      → self-hosted inference plane (GPU pools behind openai_compatible) ; Desktop → aura_hostd → local OS
```

No evidence in the tree justifies Kubernetes, microservices, a distributed database, or a
physical repository split (§30, §39). The one open architectural *decision* is where the GPU
inference plane runs and what data may leave the control-plane host (§16, decision D-1).

## 6. Architecture Gap Analysis

| Area | Current | Target | Gap | Blocking? |
| --- | --- | --- | --- | --- |
| Delivery pipeline | CI dead, no protection, deploy stale | Required checks, protected `main`, deploy-on-green | Total | **Yes — critical path head** |
| Inference | 1 CPU generation at a time | ≥1 GPU pool, pinned, failover-tested | No GPU host proven | Yes (for >1 user) |
| Confirmation integrity | Name/position-based, client-asserted | Server-issued, argument-bound approval tokens | SEC-01 | Yes |
| Desktop execution policy | Server exempts desktop-routed writes/commands | Exemption only for disposable sandboxes | SEC-02 | Yes (desktop) |
| Schema management | Alembic + 26 runtime-DDL tables | One migration authority with downgrade path | Dual authority | Yes (for rollback safety) |
| Quality evidence | Scripted providers only | Real-model gated baselines | Total | Yes |
| Observability | Turn/LLM/tool metrics; no HTTP metrics | SLO-complete | GAP-036/037 | Yes (for SLOs) |
| DR | One sandbox drill (Aug) | Offsite drill with RPO/RTO on prod-like data | GAP-028 | Yes |

---

## 7. AI Kernel

| | |
| --- | --- |
| **Current state** | `AIKernel.run/stream` (`kernel.py:226,506`): gather context concurrently (`_gather_context:776`, `asyncio.gather` of memory/RAG/history/goals/CAG) → `_should_plan:1017` → plan/execute (`_plan_and_execute:1138`, `_iter_execution:1176`) → generate (`_generate:1415`) → telemetry (`_record:1549`) → events (`_publish:1574`). Timeouts derived from provider `request_timeout_s` (`_completion_timeout_s:1311`). Client disconnect → router `aclose()` (`router.py:603`). |
| **Strengths** | Telemetry-as-return-value (ADR-009); degraded flag on results; one path for chat, voice-live and agents. |
| **Defects** | (1) `_plan_and_execute` catches **every** exception and silently answers directly (`kernel.py:1171-1173`, logs a warning only) — a failing tool pipeline is indistinguishable from "no tools needed" to the user and to telemetry unless the warning is alerted on. (2) Confirmed resumes re-run the whole turn including a fresh plan (SEC-01). (3) Whether Ollama aborts generation when the HTTP stream is closed is BEHAVIOR UNVERIFIED — with `NUM_PARALLEL=1` an orphaned generation blocks every other user. |
| **Required upgrade** | Record planning/execution failure as a typed telemetry outcome and metric; bind confirmation to stored plan steps (M1); cancellation test against real Ollama (M5). |
| **Verification** | Fault-injection test: planner raises → turn result carries `degraded_reason="planning_failed"`; load test: abandoned streams release the slot within N s. |

## 8. Agents

| Aspect | Finding | Evidence |
| --- | --- | --- |
| Registry | 8 personas as data (`AgentSpec`) with `allowed_tools`, `risk_ceiling`, `required_capabilities`, `memory_scope`, five budgets | `agents/registry.py:130-248` — CODE-READ, TESTED |
| Autonomy | **Only `engineer` is autonomous** (`max_plan_iterations=8`, `tool_budget=216`, `risk_budget=864`). The other seven run the same loop with zero replans — they are *tool-scoped prompt personas*, not autonomous agents. `orchestrator` has `allowed_tools=None` (every tool the caller's capabilities allow) | `registry.py:143-146,229-247` |
| Memory scope | `finance`, `health`, `engineer` scopes exist; others `shared`. **GAP-024's "every persona uses shared" is STALE** | `registry.py:170,178,247` |
| Loop bounds | Iterations, tokens, time, tools, risk all enforced pre-dispatch (`RunBudgetGate`) → an agent cannot loop indefinitely unless budgets are unset *and* the settings deadline is disabled | `execution/agentic_loop.py:106-212` |
| Human approval | Via ToolPolicy confirmation — inherits SEC-01/SEC-02 | §11 |
| Durable runs | `aura_agent_runs`/`aura_agent_steps` (Alembic 0008), content-free | ADR-016; real-Postgres tests claimed — NOT EXECUTED here (testcontainers skipped) |
| Failure recovery | Replan on failed step for `engineer`; others fail to a direct answer | CODE-READ |
| Quality | Task completion 100% on 50 cases **with `EchoProvider`** — measures round-trip plumbing, not agent success | `evaluation/suites/agent_tasks_baseline.json` |

## 9. Tools

| Aspect | Finding |
| --- | --- |
| Registry & schemas | JSON-schema validated arguments; invalid args returned to model with schema (`tools/registry.py:275-288`) |
| Permissions | Deny/allowlist → agent grant (`outside_agent_grant`) → sensitivity ceiling → capability (fails closed) → confirmation (`tools/policy.py:53-133`) |
| Trust/taint | `untrusted_content` tag taints `ToolContext` on return; concurrent-batch ordering defers gated calls (`registry.py:371-389`, `tests/security/test_concurrent_taint.py`) — GAP-014 is now **built and tested**, not merely documented |
| Confirmation | **Weak binding (SEC-01)** and **desktop exemption (SEC-02)** |
| Side effects / idempotency | Personal-OS CRUD owner- and kind-scoped; `Idempotency-Key` middleware on side-effecting routes (real-Postgres claim, NOT EXECUTED here); client sends the header — **not compiled** |
| Auditability | Every allow/deny/confirm goes through `_audit_call` → `AuditSink` (Postgres in prod) with an arguments *digest*, not arguments |
| Server-side CRUD | 16 personal-OS tools against `aura_personal_os_records` (server-owned, field-encrypted per P1.2 — not deployed) |
| Client-side execution | `propose_*` tools write client-side after approval (encrypted legacy tables) — two parallel Personal-OS stores (§21) |

## 10. Safety

Trust channels SYSTEM/USER/DATA with per-turn nonce fences (ADR-006), `SystemPromptLeakGuard`,
`GroundednessGuard`, quarantine of retrieved content. **Unmeasured precision:** the only time guards
saw real model output (A4) the leak guard blocked 3/15 turns and groundedness flagged 9/15, and
whether those were true positives was never established (GAP-022, still open). Safety is
therefore **TESTED against synthetic inputs, UNMEASURED against real ones.**

## 11. Security

Threat model: authenticated multi-user service; untrusted inputs are user uploads, fetched web
content, cloned repositories, local repository content on a paired desktop, and model output.

| ID | Sev | Component | Threat / attack path | Evidence | Current mitigation | Required fix | Verification / regression test |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **SEC-00** | **Critical** | Delivery | Known-fixed vulnerabilities remain exploitable in production because nothing since `78bdc83` was deployed: foreign `session_id` history overwrite, refresh-token double spend, WebSocket reauth as another user, cross-user ingestion versioning, SSRF gaps, `git_push` to default branch via refspec (GAP-048, GAP-004) | VERIFIED-API: last Deploy VPS success run #151 (`78bdc83`); live VPS SHA UNKNOWN | None in prod | Restore CI (M0), deploy `main` (M2) | `rev-parse HEAD` on VPS = deployed SHA; rerun `tests/security` against the live gateway |
| **SEC-01** | **High** | Tool confirmation | User approves call A; the resumed turn *re-plans from the same message* and executes whatever the new plan's `step_N` / same-named tool is. Client sends **both** `confirmed_call_ids` and every pending tool **name** (`chat_view_model.dart:139-147`); server accepts `call_id in ids OR tool in names` (`execution/engine.py:480-484`), and ids are positional (`step_{id}`). Under prompt injection a different target/argument set can execute under a stale approval | CODE-READ | Taint forces confirmation to be *requested*; nothing binds it | Server-side pending-approval record: `(turn, call_id, tool, sha256(canonical args))`, approval token returned to client, resume executes the **stored** step, reject on mismatch; drop name-level confirmation | Test: approve `delete_task(id=A)`, force re-plan to `delete_task(id=B)` → must not execute |
| **SEC-02** | **High** | Desktop Engineer policy | `write_file`/`edit_file`/`write_files`/`run_command`/`git_commit` are `workspace_local`, which skips taint and confidential-side-effect gates (`tools/policy.py:114-115`). ADR-014's justification ("scratch directory destroyed on expiry") is false when `CompositeSandboxRunner` routes the task to a paired desktop (`kernel.py:988-1015`). In a hostd root marked *trusted*, `command_policy.dart` allows anything off a small denylist, and writes (incl. `.git/hooks/*`) are allowed → injected content (local repo files, fetched pages) can run arbitrary commands as the user with **no confirmation anywhere** | CODE-READ | Untrusted roots queue for hostd approval (D5); denylist | Make `workspace_local` a per-routing decision: exempt only on the Docker sandbox runner; on host routing, taint ⇒ confirmation. Amend ADR-014 | Test: desktop-routed tainted turn + `run_command` → `needs_confirmation`; pin in `test_workspace_local_tools.py` |
| **SEC-03** | **High** | Client config | Google "Desktop app" OAuth client secret hardcoded as `defaultValue` (`aura_client/lib/core/config/config.dart:45-48`), in git since `cad6e7a` (2026-09-12, direct push). The adjacent doc comment and the upgrade roadmap both state it is *not* committed | CODE-READ | Google treats desktop-client secrets as non-confidential, limiting impact to client impersonation/consent-screen abuse | Rotate the secret in Google Cloud, remove the default, add a secret-pattern test for `aura_client/` | `git grep GOCSPX-` returns nothing; login fails closed without `--dart-define` |
| **SEC-04** | **High** | Governance | Unprotected `main` + dead CI ⇒ unreviewed, untested changes reach the deploy branch (4 direct pushes on 09-25 incl. security-relevant `d3e2918`) | VERIFIED-API + `git log --first-parent --no-merges` | None | Branch protection with required checks (GAP-005) after M0 | Settings/API record in `docs/reports/`; first protected merge green |
| SEC-05 | Medium | Backup | Backup container on the egress network mounts all of `deploy/secrets`, including `data_encryption_keys` (GAP-046) | gap register; compose | Backups encrypted with age | Per-file secret mounts | Fitness test on compose |
| SEC-06 | Medium | Shared host | Dev and prod share data-encryption keys (GAP-047) and prod Ollama (GAP-027) | compose | None | Separate key file & inference | Parity test |
| SEC-07 | Medium | Inference egress | HF Space plan sends prompts + retrieved documents off-host to rented infra; Space base image `vllm/vllm-openai:latest` (unpinned, `deploy/spaces/inference-vllm/Dockerfile:21`), model has no revision; vLLM `--api-key` guards `/v1/*` only (health/metrics public) | CODE-READ | `VLLM_API_KEY` required at boot | ADR-017 decision; pin image+revision; front metrics | Contract test + ADR |
| SEC-08 | Medium | Rate limiting | Legacy `RateLimitMiddleware` keys on `request.client.host` (`infrastructure/http_limits.py:64`); on Railway there is no nginx and uvicorn runs `--proxy-headers` without `--forwarded-allow-ips` (`Dockerfile:116`), so all clients plausibly share the edge proxy's bucket (100 req/min global) | CODE-READ; Railway behavior UNKNOWN | nginx `limit_req` on VPS; v3 limiter uses right-most XFF correctly | Use `extract_client_ip` everywhere; set trusted-proxy config per target | Test with forged/multi-hop XFF |
| SEC-09 | Medium | Tenant isolation | Qdrant owner filter treats **missing** `owner_id` as global (`qdrant_store.py:408-410`) — any ingestion path that forgets the owner publishes a user's chunks to everyone | CODE-READ | All current v3 paths pass `owner_id` (`api/v3.py:452-506`) | Make global explicit (`owner_id="__global__"`), reject writes without owner | Test: write without owner → rejected |
| SEC-10 | Medium | Auth config | Missing `JWT_SECRET` in production silently falls back to a per-process random secret (`services/jwt_auth.py:54-67`); with 4 workers tokens validate on 1 of 4 workers | CODE-READ | Warning log | Refuse startup in staging/production (same as mock-attestation refusal) | Settings validation test |
| SEC-11 | Medium | Request limits | Payload limit checks only `Content-Length` (`http_limits.py:89-101`) — chunked bodies bypass; nginx allows 20 MB while gateway caps 5 MB (uploads 5–20 MB fail) | CODE-READ | nginx `client_max_body_size` on VPS only | Streamed byte counting; align limits per route | Chunked-body test |
| SEC-12 | Medium | Data residency | Railway target puts Postgres/Redis on Railway and Qdrant on Qdrant Cloud with plaintext chunk payloads (`docs/deployment/control-plane.md:67-70`); GAP-016 encryption covers memory/personal-OS fields, not chunks | DOCUMENTED | — | Include in ADR-017 / encryption ADR | — |
| SEC-13 | Low | Dead code | ~2.8k LOC unreviewed, zero importers: `engineering2`, `proactive`, `continuous_intelligence`, `security/{authenticator,policy,rate_limiter,secret_manager}` (GAP-008) | `rg` importer search | Unreachable | Delete | Import test |
| SEC-14 | Low | Supply chain | Tag-pinned `nginx`, `postgres`, `redis`, `tinyproxy:latest`, socket-proxy; Actions on mutable tags while deploy job holds the VPS SSH key (GAP-032/045); committed `aura_hostd.exe` (GAP-010) | compose; `git ls-files` | Most observability images digest-pinned; prod `vllm` pinned `v0.29.0@sha256` | Pin everything; build hostd in CI | Pinning fitness test |
| SEC-15 | Info | CI config | `control-plane-deploy.yml` and `inference-plane-deploy.yml` reference `secrets.*` in job-level `if:` (lines 40-42/77-79 and 61/108) — not permitted by Actions; both appear on every push as file-named runs failing in 0 s | VERIFIED-API (runs 36156223086, 36156221775) | — | Move the check into a step or use `vars` | Workflow lint (actionlint) in CI |

Positive security controls verified: SSRF guard with DNS-rebinding check (G4); `docker exec` refused
by socket proxy and pinned by test; sandbox egress `internal: true` + allowlisting proxy; hostd
containment checked after symlink resolution with per-request re-check; nginx is the only
published port; XFF set (not appended) by nginx; right-most XFF parsing in the v3 limiter; mock
attestation refused in production.

## 12. Memory

| Current | Target | Gap / risk | Verification |
| --- | --- | --- | --- |
| Taxonomy (ADR-004), Postgres store, decay + nightly consolidation (Celery 02:30), `forget()` owner-scoped (`api/v3.py:879`), scoped by persona where configured; field encryption built (P1.2, not deployed) | Confidential, isolated, correctable, exportable | Consolidation never verified at volume; client memory sync capped at 100 with no pagination (roadmap B2); no offline outbox for memory writes; correction = forget + re-add only | Real-Postgres consolidation test at 10⁵ rows; pagination test; cross-user probe suite (M6/M10) |

## 13. RAG / Knowledge

| Aspect | State |
| --- | --- |
| Ingestion | v3 text + PDF upload (OCR via Tesseract) — content-hash idempotent, owner-scoped; the old `hash(title)` cross-user leak fixed but **not deployed** (GAP-004) |
| Retrieval | BM25 + dense + RRF + optional cross-encoder rerank; strategies `adaptive`, `agentic`, `base`, `expansion`, `leaves`, plus graph/relational routing |
| Index versioning | `IndexIdentity`, aliases, recall-gated promotion (ADR-007) — CODE-PRESENT; promotion never exercised on a real corpus |
| Embedding identity | Real bug found and fixed in A4 (`matches_embedding` slug round-trip) — proves the in-memory suite could not see it |
| Evaluation | recall@5 0.65 / nDCG 0.51 / MRR 0.37 on 10 docs / 23 queries **with the non-semantic hash embedder**; production embedder `bge-small-en-v1.5` never evaluated; per-strategy comparison never done |
| Knowledge graph | Rule-based extraction on every ingest (default on), Postgres store, owner-scoped read API; extraction quality unmeasured (GAP-023) |
| Stale text | `rag/agentic.py:119` still says "There is no OCR" (GAP-019/039) |

## 14. Planning

`PlanningEngine.plan()` produces a DAG with step-output references (`_resolve_output_tokens`),
data-flow dependencies forced for the engineer (#89), 700-token cap sized to the CPU model (CLAUDE.md).
Replanning bounded by `can_replan`. **Planning quality is unmeasured** — no plan-correctness suite
exists; the agent suite checks substrings of echoed text.

## 15. Reasoning

`reasoning/engine.py` exposes strategies (direct, CoT, self-consistency, tree-of-thought) selected
by `ReasoningStrategy`. At 5–10 tok/s CPU, any multi-sample strategy multiplies a 60 s turn —
**self-consistency and ToT are infeasible in production today** and should be disabled for the CPU
provider until M4. No evaluation shows any strategy improves answers.

## 16. LLM / Inference

| Aspect | State | Evidence |
| --- | --- | --- |
| Router | 5 policies, capability filtering, per-provider breaker (global thresholds, GAP-035), pre-first-token failover, admission control with 429/503 + `Retry-After` (P2, built) | `llm/router.py`; 48 FrozenClock tests (in 2707 run) |
| Production provider | Ollama CPU, `qwen2.5:3b-instruct-q4_K_M` by **tag** (GAP-030), `NUM_PARALLEL=1`, `MAX_LOADED_MODELS=1`, `LOCAL_MAX_CONCURRENCY=1` | `docker-compose.prod.yml:266-270,719-723` |
| GPU | `vllm` service pinned `v0.29.0@sha256:c291…` behind `gpu` profile — **never run**; GAP-012's "`:latest` unpinned" is STALE for prod compose, still true for the HF Space Dockerfile | compose:755 |
| Remote plane | `hfspace` provider opt-in (`AURA_ALLOW_REMOTE_INFERENCE`); Railway docs make it the **sole** provider (`AURA_LLM_PROVIDERS=hfspace`) — no fallback, so a sleeping/cold Space takes the whole AI surface down | `docs/deployment/control-plane.md:75-76` |
| Measured routing | Two real Ollama models: FASTEST/QUALITY_FIRST/BALANCED/failover behave (G2, `tests/load/test_multi_model_routing.py`) | Reference, NOT EXECUTED here |
| Model licensing | Not recorded (GAP-042); Qwen2.5-3B license status UNKNOWN | — |

**Decision D-1 (unresolved, blocking M4):** the production roadmap forbids rented off-host
inference without an ADR (MASTER rule 5, GAP-034); the deployment docs and the 09-25 roadmap make
a Hugging Face GPU Space the primary provider. Both cannot stand. The master roadmap requires
ADR-017 before cutover (§M4).

## 17. Learning / MLOps

| Claim | Reality |
| --- | --- |
| "Learning" / nightly refresh | `knowledge_refresh_task` (02:00) re-ingests knowledge; `memory_consolidate_task` consolidates memory — **neither changes model weights**. Calling them learning/training is incorrect |
| Fine-tuning | Production uses `ScriptedTrainer` (GAP-017). A real PEFT LoRA path exists and changed weights once on `tiny-random-gpt2` (loss 6.90→6.81) — *mechanism* proven, **never on the production base model** |
| Failure honesty | GAP-018 fixed (non-training ⇒ `NOT_TRAINED`, unpromotable) — not deployed |
| Promotion | ApprovalService + model registry + RegressionGate exist; never run end to end |
| Model-weight modification in prod | **None** |

## 18. Multimodal

OCR: real (Tesseract), proven end to end through the upload endpoint. Vision: **not implemented**
(capability flag only, `domain/llm.py:148`). Video: **not implemented**. No self-hosted VLM has been
evaluated.

## 19. Voice

Batch STT (`faster-whisper tiny.en`, English-only default — GAP-020) and TTS (Piper EN + Arabic,
espeak fallback) proven by a synth→transcribe round trip (F2). Live duplex (`/voice/live`, energy
VAD) is **scaffolding**: never device-tested, never load-tested; it was unreachable in production
until the nginx HTTP/2 fix (#65). **Arabic speech in / Arabic speech out asymmetry is a product
defect** (TTS ships Arabic, STT cannot hear it).

## 20. Proactivity

Durable outbox (`FOR UPDATE SKIP LOCKED`, lease, dead-letter at 5 attempts) drained every 60 s;
`TriggerEngine` is the event bus's first subscriber; one real trigger (`document_quarantined`) plus a
rule-based daily briefing and rule-based insights with record-id citations. No user preference
model, no notification-safety policy (quiet hours, rate caps), no autonomous actions. The unreviewed
`aura_core/proactive` package is dead code (SEC-13).

## 21. Personal OS

**Two stores for the same nouns** — a documented but unresolved design fork:

| Store | Written by | Encryption | Client-visible |
| --- | --- | --- | --- |
| Legacy `tasks`/`calendar_events`/`finance_records`/`health_records`/`projects`/`goals` (Alembic, `SYNCABLE_MODELS`) | Client (+ `propose_*` after approval) | Client-side zero-knowledge | Yes (Drift sync) |
| `aura_personal_os_records` (runtime DDL) | Server tools `create_*/update_*/delete_*` | Server-side field encryption (built P1.2, not deployed) | **No** — server-visible only |

A task the Task Planner creates with `create_task` never appears in the app's Tasks screen. Projects
and Goals now have Flutter feature modules and Drift tables (`features/projects`, `features/goals`,
`database.dart:154,168`) — the upgrade roadmap's "Flutter half deferred" is **STALE**; whether they
compile is NOT RUNNABLE here. Offline conflict resolution: cursor sync with client clock
(`test_sync_conflict_clock.py` in the legacy suite, passing).

## 22. Desktop (IDE)

Source: desktop Phase-0 audit (code-read) + this review's spot checks. **Nothing on the desktop has
been compiled or tested by any CI run since 2026-09-13.**

| Area | State | Notes |
| --- | --- | --- |
| Shell/layout/theme | IMPLEMENTED | resize handles + layout persistence added 09-25, unverified |
| Explorer | IMPLEMENTED | context menu added in #108, junction containment test in #109 — unverified |
| Editor | IMPLEMENTED (partial) | no preview tabs, no cursor restore, no autosave/hot-exit |
| Command palette/quick open | IMPLEMENTED (partial) | substring matching, 5,000-file cap |
| Terminal/PTY | IMPLEMENTED, **Windows-only** | real ConPTY; no POSIX PTY; `powershell.exe` hardcoded |
| Git/diff | IMPLEMENTED | stage/commit/checkout real; fetch/pull/push added by direct push `35f0371`, unverified |
| Problems / debug | IMPLEMENTED | real diagnostics parsers; real DAP (Python/debugpy only) |
| Test explorer | NOT IMPLEMENTED | |
| Inline AI | NOT IMPLEMENTED — and **infeasible on CPU inference** (ghost-text needs sub-second TTFT) | depends on M4 |
| Agent workspace / control center | IMPLEMENTED (narrow) | per-task git worktrees; no live orchestration state |
| Approvals / security UX | IMPLEMENTED in hostd; **SEC-02** undermines it server-side | |
| Host management | IMPLEMENTED | no restart-on-crash; no bundled `aura_hostd` |
| Packaging/updates/signing | NOT IMPLEMENTED | |
| Accessibility/personalization/perf/large-repo | NOT MEASURED | |

## 23. Flutter (mobile + shared)

Riverpod + go_router + Drift (schema v12). API reachability matrix: 74/97 routes called, 16 uncalled
and triaged (generated, test-gated — `docs/guides/api-reachability.md`). Idempotency-Key and
`Retry-After` handling written but **not compiled**. Release APK builds on floating `stable` while CI
pins 3.44.6 (GAP-044). Infrastructure credentials: no inference URL or key in the client
(fitness-tested) — **but see SEC-03** for the OAuth secret.

## 24. Host daemon / Engineer

`aura_hostd`: outbound WSS to `/api/v3/host/agent` (first-frame token auth, reauth bound to the
original user), loopback control server with per-run token file, containment after symlink
resolution, command policy (denylist → trusted → metachar floor → read-only allowlist → approval
queue), approval queue re-derives the operation before executing, Win32 ConPTY, git service with
argv-only execution. Pairing: RFC-8628-shaped device grant, 30-day non-refreshable device tokens,
owner-checked revocation. **No restart-on-crash, no bundled binary, a committed `.exe` of unknown
provenance.** Server-side Docker workspaces (ADR-013/014): built, sandbox off in production, the
10-check VPS verification never run (GAP-025).

## 25. Database

- Alembic: 8 revisions for platform tables.
- **26 additional `aura_*` tables are created at runtime by 15 adapters** (`CREATE TABLE IF NOT
  EXISTS`, plus 6 runtime `ALTER TABLE`s) — outside Alembic, with no downgrade and no version
  record. Consequences: a rollback to an older image cannot undo runtime schema changes; schema
  drift between environments is invisible; readiness only checks Alembic head
  (`_register_schema_readiness`). Some later tables (0006–0008) *did* go through Alembic, so the
  rule is inconsistent rather than absent.
- Pooling: asyncpg pool (race fixed, G3) + SQLAlchemy for ADR-002 routers — two pools per worker ×
  4 workers × gateway + 3 Celery services; total connection budget vs Postgres `max_connections`
  UNKNOWN.
- Real-Postgres verification is done ad hoc per phase (throwaway instances); testcontainers tests
  skip without Docker (4 skips in the legacy run here).

## 26. API

~97 routes (reachability matrix). v3-only; errors via `api/http_errors.py`; 429/503 + `Retry-After`
from admission control; SSE for chat; WebSockets for voice-live and host-agent (both needed
dedicated nginx locations after live 404s — #65, `3ee314a`). **Three rate limiters exist** (nginx
`limit_req`, legacy per-IP middleware, v3 sliding window) plus one dead one. Idempotency on
side-effecting v3/admin routes (built). No published OpenAPI compatibility policy; no contract test
between Flutter models and backend schemas.

## 27. Distributed systems

| Mechanism | Assessment |
| --- | --- |
| Celery queues realtime/ingestion/learning/training | Beat single instance per stack; no beat lock — safe only while one beat runs per database |
| Duplicate execution | Outbox claims are `SKIP LOCKED` + lease; insight sink `ON CONFLICT DO NOTHING`; daily briefing idempotency relies on the sink. Legacy `ingest_*_task` still registered but unreachable (GAP-038) |
| Event delivery | In-memory bus at-most-once for non-durable types; durable types go through the outbox (at-least-once + idempotent sinks) |
| Consistency | Two Personal-OS stores (§21); memory cache on client is a wipe-and-refetch capped at 100 |
| Failure recovery | Outbox dead-letter has no operator surface/alert (no outbox-age gauge — planned) |

## 28. Deployment

| Target | Status | Evidence |
| --- | --- | --- |
| Contabo VPS (compose, 17 services) | **Live, stale** — last automated deploy `78bdc83` (2026-09-13). Domain moved to `aureon-ai.tech` in a 09-25 direct push; live DNS/TLS state UNKNOWN | VERIFIED-API |
| Railway control plane | **Never deployed** by CI — its workflow is invalid (SEC-15) and CI is dead | VERIFIED-API |
| HF GPU Space | **Never deployed** by CI (same); a uid-collision fix (`8e26156`) suggests a manual build attempt; live state UNKNOWN | commit message |
| Dev stack (`aura-dev`) | Shares host, keys, and prod Ollama (SEC-06) | compose |
| Backups | Service runs daily (claimed); encrypted backups built, not deployed; **fail-closed precondition**: age public key must be on the VPS first or backups stop | roadmap §6 |
| Rollback | `docs/deployment/rollback.md` — DOCUMENTED ONLY; runtime DDL makes image rollback unsafe (§25) |
| Health | `/health/live`, `/health/ready` (schema-gated); Railway healthcheck `/health/ready` |

## 29. Performance — revalidated

| Metric | Value | Environment / model / method | Samples | Verdict |
| --- | --- | --- | --- | --- |
| Pipeline p95 | 4.56 ms; 478 turns/s | In-memory adapters + `EchoProvider`, in-process | bench | **REJECT** as performance evidence — measures Python overhead only |
| Single-turn latency (A4) | p50 56.3 s / p95 70.5 s | 3–4 CPU cores (docs disagree: roadmap "3", GAP-011 "4-core/15 GB"), real Postgres/Qdrant/Redis/Ollama, `qwen2.5:3b` q4, full answers, sandbox not VPS | n=20 sequential, 1 run | **VALID reference**, low confidence (n=20, single run, not prod hardware) |
| Concurrency (A4) | ~0.04–0.06 turns/s flat 1→8 | same | 1 run | **VALID**: proves single-flight |
| Throughput (CLAUDE.md) | 5–10 tok/s in prod | production VPS, method unstated | UNKNOWN | Reference, unmeasured method |
| Routing (G2) | ~130 ms vs ~480 ms/completion | two Ollama models, short completions | small | Valid for routing logic only |
| Phase-3 harness | "all thresholds pass to 50 users, knee at 30" | **in-process echo**, buffered TTFT | self-validation | **REJECT** as capacity evidence (the report says so itself) |
| Gateway image | 2.34 GB | A1 build | 1 | Valid |

Latency components that exist separately: retrieval, memory, planning, tool, TTFT, generation are
recorded per turn in `TurnTelemetry` (and P2 latency decomposition, built); **no HTTP request
metric**, no queue-wait metric in Prometheus, histogram buckets truncate at 60 s/12 s (GAP-037), so
the measured p95 is unobservable in Grafana.

**Real bottleneck:** CPU LLM generation serialized to one request (`NUM_PARALLEL=1`). Every other
stage is orders of magnitude faster in every measurement available. Horizontal scaling of the
control plane cannot change this; only GPU inference can.

## 30. Scalability

Single VPS, single inference slot. Admission control now bounds the queue (fails fast with 429/503)
rather than adding throughput. Gateway: 4 workers, each loading in-process Whisper/embedder/
reranker within a 3 GB container limit — memory headroom under load UNKNOWN. **No evidence
supports Kubernetes, microservices, or database sharding**; the first scale step is a GPU pool,
the second is measuring what saturates next (M5).

## 31. Testing

| Suite | Executed here | Result |
| --- | --- | --- |
| `pytest` default (unit, security 257+, architecture fitness, e2e gates, deployment contract) | Yes | **2707 passed / 56 skipped / 0 failed** |
| Legacy `unittest` | Yes | **100 run, OK, 4 skipped** (testcontainers without Docker) |
| `tests/load`, `tests/benchmarks`, `tests/training` | No | NOT EXECUTED (infra/GPU) |
| Flutter `analyze`/`test`/builds | No | NOT RUNNABLE; **not run in CI since 09-13** |
| `aura_hostd` `dart analyze`/`test` (Linux + new Windows job) | No | NOT RUNNABLE; CI jobs die in 2–4 s |
| Admin Playwright | No | Not attempted; Admin CI never PR-triggered |
| CI merge gates | — | **None effective** (CI dead + no protection) |

## 32. AI Evaluation

| Metric | Exists? | Real model? | Gated? |
| --- | --- | --- | --- |
| Retrieval recall/nDCG/MRR | Yes (23 queries) | Hash embedder | Yes (pytest) — but CI dead |
| Groundedness / citation coverage | Yes | Scripted provider | Yes |
| Task completion / tool success / tool accuracy | Yes (50 cases) | Echo | Yes |
| Agent-run suite (`agent_runs`) | Yes | Echo | UNKNOWN |
| Factuality / hallucination rate | **No** | — | — |
| Planning correctness | **No** | — | — |
| Tool-argument correctness with a real model | **No** | — | — |
| Recovery success | **No** | — | — |
| Guard precision / FP rate | **No** (GAP-022) | — | — |
| Memory relevance | **No** | — | — |
| Instruction following (EN/AR) | **No** | — | — |
| WER (EN/AR), TTS quality, live-voice latency | **No** | — | — |
| Vision | **No** | — | — |
| Desktop AI UX | **No** | — | — |

## 33. Observability

Metric catalogue for turns, LLM, retrieval, tools, guards; Prometheus multiprocess; Loki/Promtail;
Grafana; Alertmanager; per-turn Postgres telemetry feeding `/admin/scorecard`. Missing: HTTP
request metrics (availability and 5xx SLOs unmeasurable, GAP-036), histogram range (GAP-037),
outbox-age gauge, GPU exporter, blackbox probe, desktop/hostd telemetry, burn-rate alerts.

## 34. Technical Debt (highest-interest items)

1. Dual schema authority (Alembic + runtime DDL).
2. Two Personal-OS stores for the same nouns.
3. Dead unreviewed packages (~2.8k LOC) and three live rate limiters.
4. `api/v2/` name for permanent v3 routers (cosmetic, known).
5. Legacy Celery tasks registered but unreachable.
6. Windows-only PTY in a daemon whose CI runs Linux first.
7. Doc drift: CLAUDE.md says ADRs run to 014 (015/016 exist), locates `SYNCABLE_MODELS` in the
   client (it is `aura_backend/infrastructure/db/models.py`), calls admin "lightly used"; roadmap §1
   still lists "agents are prompts" as open after Phase C; two "Phase J" headings.
8. Root-level strays (`test_personal_os.py`, `ML_ENVIRONMENT_REPORT.md`, `aura_backend/test_personal_os2.py`).

## 35. Critical Risks

| Risk | Likelihood | Impact |
| --- | --- | --- |
| Exploitation of vulnerabilities already fixed on `main` but not deployed (SEC-00) | Medium (needs an account) | High (cross-user data) |
| Unreviewed direct push breaks production at the next successful deploy (the 09-23 pushes already broke `Container` construction once) | High once CI returns | High |
| Engineer executes injected commands on a user's machine (SEC-02) | Low–Medium | High |
| Backups stop when encrypted-backup code deploys without the age key (documented precondition) | Medium | High |
| Railway/HF cutover with sole remote provider and no failover | Medium | Total AI outage |
| Image rollback against runtime-migrated schema | Medium | Data errors |

## 36. Top Problems (ranked)

1. CI dead since 2026-09-13 (GAP-043, date corrected). 2. Production ~25 merges stale (SEC-00).
3. No branch protection, direct pushes continue (GAP-005). 4. Confirmation not bound to approved
call (SEC-01). 5. Desktop-routed execution exempt from confirmation (SEC-02). 6. Committed OAuth
secret (SEC-03). 7. Single-flight CPU inference (GAP-011). 8. No real-model quality evidence
(GAP-021). 9. Inference-hosting contradiction unresolved (D-1/GAP-034). 10. Dual schema authority.
11. No HTTP metrics (GAP-036). 12. No offsite DR drill (GAP-028). 13. Backup can read data keys
(GAP-046). 14. Dev/prod share keys and inference (GAP-027/047). 15. Two Personal-OS stores.
16. Unpinned LLM/embedder/STT revisions (GAP-030/031). 17. Invalid deploy workflows (SEC-15).
18. Arabic STT absent while Arabic TTS ships (GAP-020). 19. Desktop never compiled in CI since
09-13. 20. Dead code packages (GAP-008).

## 37. Strengths

Enforced hexagonal layering; single kernel execution path; `tools=()` model calls; capability-gated,
audited tool policy with agent grants; taint propagation with concurrency-safe scheduling;
in-query tenant filters; durable outbox with SKIP LOCKED; honest self-correcting documentation and
a schema-tested gap register; 2,707-test suite that runs with zero infrastructure; real-Postgres
verification habit that has repeatedly caught bugs the in-memory suite could not; sandbox design
that needs neither `EXEC` nor `IMAGES` on the socket proxy; hostd containment and approval queue
re-derived per request; admission control that fails fast instead of queueing unboundedly.

## 38. Maturity Matrix

Ladder: DOCUMENTED → SCAFFOLDED → IMPLEMENTED → BUILT → EXECUTED → TESTED → FAILURE-TESTED →
MEASURED → VERIFIED → PRODUCTION-CERTIFIED. "Highest state reached" — **no row is
PRODUCTION-CERTIFIED.**

| Area | State | Evidence | Missing evidence | Blocking? |
| --- | --- | --- | --- | --- |
| Architecture | TESTED | fitness tests (EXECUTED-HERE) | — | No |
| Delivery / CI | **BROKEN** | API history | a green run | **Yes** |
| Security | TESTED (fixes) / not deployed | 257+ security tests pass here | deployment; SEC-01/02/03 fixes; pen test | **Yes** |
| Reliability | FAILURE-TESTED (outbox, pool, router, in unit scope) | unit/real-PG claims | chaos on real stack | Yes |
| Performance | MEASURED (once, CPU, n=20) | A4 | GPU numbers, prod hardware | Yes |
| Scalability | MEASURED (single-flight proven) | A4 | load matrix | Yes |
| AI capability | TESTED (scripted) | eval gates | real-model eval | Yes |
| RAG | TESTED + MEASURED (hash embedder) | A3 | production embedder, per-strategy | Yes |
| Memory | TESTED | unit | volume, isolation on real PG | Partial |
| Agents | TESTED | unit + echo suite | real-model agent success | Partial |
| Tools | TESTED | policy/taint tests | SEC-01/02 fixes | Yes |
| Learning | EXECUTED (tiny model) | training test | production base run or relabel | No (deferrable) |
| Multimodal (OCR) | EXECUTED end to end | F1 | vision: NOT IMPLEMENTED | No |
| Voice (batch) | EXECUTED end to end | F2 | WER, Arabic STT | No |
| Voice (live) | SCAFFOLDED/IMPLEMENTED | code | device, load | No |
| Personal OS | TESTED (server) / IMPLEMENTED (client) | unit | store unification, client compile | Partial |
| Flutter | IMPLEMENTED | code | **any CI run since 09-13** | Yes |
| Desktop | IMPLEMENTED | code-read audit | compile, E2E, packaging | Yes (for desktop GA) |
| Host daemon | IMPLEMENTED | code | CI run, E2E report | Yes (for desktop GA) |
| Deployment (VPS) | EXECUTED (stale SHA) | Deploy VPS #151 | current SHA live | **Yes** |
| Deployment (Railway/HF) | BUILT (config) | files | any successful deploy | No (optional target) |
| Observability | IMPLEMENTED (partial) | metrics module | HTTP metrics, SLO dashboards | Yes |
| Evaluation | TESTED (scripted) | gates | real-model, merge-gating | Yes |
| DR | EXECUTED once (sandbox, Aug) | A2 | offsite drill, RPO/RTO | Yes |
| Maintainability | TESTED | suites | dead code removal, schema unification | No |

Historical scores (**REFERENCE only, not revalidated**): 09-13 audit 56/100; Doc re-assessment
(chat transcript) scores; `AURA_AI_PRODUCTION_READINESS_SCORE.md`. This review deliberately
assigns no numeric score: no measurement supports one.

## 39. Roadmap Validation

### 39.1 Structural problems in the existing roadmaps

| # | Problem | Where |
| --- | --- | --- |
| R1 | Three concurrent phase systems (A–K, 0–26, DPL/IDE/Platform) plus a fourth letter sequence (A–R) whose letters collide with A–K but mean different things | all three roadmaps |
| R2 | Duplicate ID: two "Phase J" sections | `AURA_UPGRADE_ROADMAP.md:495,634` |
| R3 | Priority conflict: 09-25 plan puts Desktop certification before correctness/security and before CI exists; production plan puts CI/security first | FULL §53 vs MASTER Phase 1 |
| R4 | Inference-hosting conflict (D-1) | FULL DPL-3, `docs/deployment/*` vs MASTER rule 5 / GAP-034 |
| R5 | Repo split demanded (FULL DPL-1, DoD "independent GitHub repositories") vs rejected with reasons (`docs/deployment/architecture.md` §1) | — |
| R6 | FULL Platform-1 lists the PostgresPool race as open — closed (GAP-001) | FULL §33 |
| R7 | Desktop inline intelligence (IDE-9) scheduled before any inference that could serve it | FULL §53 order |
| R8 | "Production deployments: 1 and counting" presented as current | upgrade roadmap §5 |
| R9 | Master Phase 0's exit "CI actually runs on main" never met, yet Phases 1–17 work proceeded and was recorded as "built" | MASTER + roadmap §6 |
| R10 | Weak acceptance criteria in FULL (IDE-1..24, DPL) — mostly feature lists without measurable exit criteria | FULL |

### 39.2 Phase validation

Every phase of every roadmap is re-derived individually in the
[reconciliation report](../roadmaps/AURA_ROADMAP_RECONCILIATION.md) (§2–§5), with keep / merge /
replace / remove / defer decisions and evidence.

### 39.3 Gap-register entries this review found STALE or wrong

| Gap | Register says | Actually |
| --- | --- | --- |
| GAP-043 | Actions dead since 2026-09-22 | Since **2026-09-13** (Backend CI #222 onward) |
| GAP-005 | "No direct pushes since protection" pending | Direct pushes continue on 2026-09-25 |
| GAP-012 / GAP-032 | prod `vllm` image `:latest` | prod compose pins `v0.29.0@sha256`; HF Space Dockerfile still `:latest` |
| GAP-014 | taint gap "still present" | scheduler deferral + `tests/security/test_concurrent_taint.py` exist (built, tested here) |
| GAP-024 | every persona `shared` | `finance`, `health`, `engineer` scopes exist |
| GAP-013 | Projects/Goals no client | Flutter modules exist (compile unverified) |
| — (new) | — | SEC-01, SEC-02, SEC-03, SEC-08–SEC-12, SEC-15, dual schema authority, two Personal-OS stores |

## 40. Final Architectural Verdict

1. **What is AURA today?** A self-hosted AI platform (FastAPI control plane + hexagonal kernel,
   hybrid RAG, memory, bounded agents, audited tools) with a Flutter client, a Windows-first desktop
   IDE and local daemon, and an admin console — running in production on one CPU VPS at a
   12-day-old SHA, serving one AI generation at a time.
2. **Genuinely implemented (TESTED or better):** kernel pipeline; provider router with failover and
   admission control; tool policy/taint/audit; agent registry with budgets; hybrid retrieval with
   versioned identity; memory with decay/consolidation; durable outbox + first trigger; OCR; batch
   STT/TTS; personal-OS server CRUD; device pairing; security regression suite; encryption
   primitives (AES-GCM field crypto, age backups).
3. **Only scaffolded / unproven:** live duplex voice; NLP layer; knowledge-graph usefulness;
   Engineer Docker workspaces on the VPS; the whole desktop IDE and hostd (never compiled in CI
   since 09-13); Railway + HF deployment; GPU inference; real LoRA training; Personal-OS v3 in the
   client.
4. **Production-ready:** nothing is production-certified. The *design* of layering, tool policy and
   tenant scoping is production-grade.
5. **Not production-ready:** delivery pipeline, inference capacity, confirmation integrity, desktop
   execution policy, DR, observability for SLOs, quality evidence.
6. **Current blockers:** GAP-043 (CI), GAP-005 (protection), SEC-00 (stale prod), SEC-01, SEC-02,
   SEC-03, D-1 (inference hosting decision), GAP-011/041 (capacity).
7. **Strengths:** §37.
8. **Wrong or stale assumptions:** that "merged" means "tested" (CI has not run for 12 days); that
   production reflects `main`; that `workspace_local` work is always disposable; that a tool-name
   confirmation equals the user's approval of a specific action; that scheduled refresh is
   "learning"; that a HF Space is uncontroversially "self-hosted"; that a repo split is needed; that
   echo/in-process numbers describe capacity.
9. **What next:** M0 → M1 → M2 (master roadmap).
10. **What NOT to build yet:** repo split; Kubernetes/microservices; inline AI completions (before
    GPU TTFT); video understanding; additional autonomous personas; real LoRA on the production base
    model; macOS desktop certification; Business-OS/CRM features; a second model-registry.
11. **Parallelizable after M1:** Desktop track (M8) ‖ platform track (M3→M4→M5) ‖ client reach/admin
    (M14) ‖ data protection (M6).
12. **Critical path to production:** M0 → M1 → M2 → M3 → M4 → M5 → M7 → M10 → M15, with M6 joining
    before M15.
13. **Missing evidence:** a green CI run; live SHA; real-model quality; GPU latency; load matrix;
    offsite drill; guard precision; desktop compile/E2E; engineer-workspace VPS report.
14. **Must be proven before declaring production readiness:** the master roadmap's Definition of
    Done (§21 there) — every item with a linked artifact.
15. **Final target architecture:** §5 of this report — unchanged intent, one control plane, one
    kernel, one router, a self-hosted GPU inference plane whose hosting model is fixed by ADR-017,
    and a desktop IDE whose local execution is governed by the same confirmation contract as the
    server.
