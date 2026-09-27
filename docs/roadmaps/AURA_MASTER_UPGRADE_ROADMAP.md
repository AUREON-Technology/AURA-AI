# AURA Master Upgrade Roadmap

**Status:** CANONICAL · **Baseline:** `5db6692` (2026-09-25) · **Replaces** as the forward plan:
`docs/AURA_UPGRADE_ROADMAP.md` (A–K), `docs/production/MASTER_PRODUCTION_ROADMAP.md` (0–26),
`docs/plans/AURA_FULL_UPGRADE_ROADMAP.md` (DPL/IDE/Platform/A–R). Those remain as **historical
evidence ledgers**; how each of their phases maps here is in
[AURA_ROADMAP_RECONCILIATION.md](AURA_ROADMAP_RECONCILIATION.md). Findings and evidence behind every
statement: [AURA_FULL_SYSTEM_REVIEW.md](../reports/AURA_FULL_SYSTEM_REVIEW.md). Actionable items:
[AURA_CRITICAL_BACKLOG.md](AURA_CRITICAL_BACKLOG.md). Gap status stays machine-checked in
[`gap_register.yaml`](../production/gap_register.yaml).

**Standing rule (binding):** a phase or item is complete only with the evidence its exit criteria
name, linked from `docs/reports/`. Source code existing is IMPLEMENTED; nothing more. Use the
maturity ladder in §2 and never collapse its states.

---

## 0. Executive Objective

Take AURA from *well-architected but undeliverable* (CI dead since 2026-09-13, production 12 days
stale, single-flight CPU inference, no real-model quality evidence) to a **production-certified,
self-hosted AI platform for 50+ concurrent users with a certified desktop IDE**, without rewriting
the architecture. The order is dictated by evidence: nothing can be proven until CI runs, nothing
reaches users until deploy works, nothing scales until inference is GPU-backed, nothing is "good"
until a real model is evaluated.

## 1. Current Verified State (summary)

| Fact | Evidence |
| --- | --- |
| Backend: 2707 pytest pass / 0 fail; 100 legacy pass | EXECUTED 2026-09-25 on `5db6692` |
| CI: every job on `main` dies in 2–5 s since 2026-09-13 | GitHub API, Backend CI #222–#267 |
| Production: last automated deploy `78bdc83` (2026-09-13) | Deploy VPS #151; #152–#175 skipped |
| `main` unprotected; direct pushes on 2026-09-25 | `git log --first-parent --no-merges` |
| Inference: CPU Ollama, 3B q4, 1 request at a time; p95 70.5 s (A4) | compose; A4 report |
| Quality metrics: scripted providers / hash embedder only | eval baselines |
| Security: SEC-01 (confirmation binding), SEC-02 (desktop exemption), SEC-03 (committed OAuth secret) open | review §11 |
| Desktop & hostd: implemented, never compiled/tested in CI since 2026-09-13 | API + review §22 |

## 2. Architectural Principles & Maturity Ladder

Binding principles (unchanged from ADRs, re-affirmed by this review):

1. Evolve, don't rewrite — hexagonal layering, `container.py` as the only wiring point.
2. `AIKernel` is the only execution path; model `tool_calls` are never dispatched (ADR-006).
3. `ProviderRouter` owns model selection and failover; failover only before the first token.
4. Only `echo` / `openai_compatible` / `ollama` provider kinds; no hosted-AI vendor; no model on
   the Flutter client (fitness-tested).
5. Every side effect passes ToolPolicy; **confirmation binds to the exact call the user saw** (new,
   M1).
6. **A confirmation exemption is valid only where the effect is provably disposable** (new, amends
   ADR-014, M1).
7. Default `pytest` needs no Docker/network/model.
8. No Kubernetes, microservices, distributed DB, or physical repo split without a load/ops report
   showing the need.
9. One migration authority for schema (new, M6).

Maturity ladder: **DOCUMENTED → SCAFFOLDED → IMPLEMENTED → BUILT → EXECUTED → TESTED →
FAILURE-TESTED → MEASURED → VERIFIED → PRODUCTION-CERTIFIED.** "VERIFIED" = measured on the
production topology (or a declared equivalent) with a linked report. "PRODUCTION-CERTIFIED" =
VERIFIED + in M15's release checklist for the launch SHA.

## 3. Current Architecture

```
clients (Flutter mobile/web, desktop IDE, admin) ─► nginx ─► gateway (FastAPI v3, 4 workers)
   ─► AIKernel ─► Planning/Execution/AgenticExecutor ─► ToolRegistry/ToolPolicy ─► adapters
   ─► ProviderRouter ─► Ollama CPU (prod) | vLLM (gpu profile, unrun) | hfspace (opt-in, unrun)
   Postgres · Qdrant · Redis · Celery (4 queues) · beat · outbox · Prometheus/Loki/Grafana
desktop IDE ─► aura_hostd (loopback control) ; aura_hostd ─WSS─► /api/v3/host/agent
```

## 4. Target Architecture

```
Clients ─► Control Plane (gateway + workers; one host or Railway) ─► AIKernel ─► ProviderRouter
        ─► Inference Plane: ≥1 GPU vLLM pool (pinned image+model revision), CPU Ollama last resort
           (hosting model fixed by ADR-017: owned/dedicated GPU vs rented GPU Space)
Desktop IDE ─► aura_hostd ─► local OS, governed by the same argument-bound confirmation contract
Data: Postgres (single migration authority) · Qdrant · Redis ; encrypted offsite backups with drilled RPO/RTO
```

## 5. Critical Blockers

| ID | Blocker | Blocks | Phase |
| --- | --- | --- | --- |
| B-1 | GitHub Actions not executing (GAP-043, since 09-13) | every "tested/proven" claim; every deploy | M0 |
| B-2 | `main` unprotected, direct pushes (GAP-005) | trust in `main` | M0 |
| B-3 | Production stale at `78bdc83` (SEC-00) | users receiving security fixes | M2 |
| B-4 | Confirmation not argument-bound (SEC-01) | any side-effecting autonomy | M1 |
| B-5 | Desktop-routed execution exempt from confirmation (SEC-02) | desktop GA | M1 |
| B-6 | Committed OAuth client secret (SEC-03) | desktop release | M1 |
| B-7 | Decision D-1: inference hosting (ADR-017) | M4 cutover | M4 |
| B-8 | Single-flight CPU inference (GAP-011) | >1 concurrent AI user | M4/M5 |
| B-9 | No real-model quality baseline (GAP-021) | every quality claim | M7 |

## 6–18. Phases

Every phase lists: **Objective · Why · Inputs · Dependencies · Implementation areas · Security ·
Testing · Failure tests · Metrics · Exit criteria · Rollback · Evidence required.**

---

### M0 — Evidence Pipeline & Governance *(Immediate remediation, part 1)*

- **Objective:** CI executes on every PR and on `main`; `main` is protected; one canonical roadmap.
- **Why:** since 2026-09-13 no code has been gated or deployed; every later phase's evidence depends
  on this.
- **Inputs:** GAP-043, GAP-005, SEC-15, GAP-045, gap register.
- **Dependencies:** none. **Account-level action by the repo owner** (Actions/billing) is the first
  step and cannot be done from code.
- **Implementation areas:** GitHub account Actions/billing; `.github/workflows/*` (fix
  `secrets.*` in job-level `if:` in `control-plane-deploy.yml`, `inference-plane-deploy.yml` —
  move the check into a step or use `vars`); add `actionlint` job; pin third-party actions to SHAs
  (GAP-045); branch protection with required checks: Backend CI (`test`, `pip-audit`,
  `docker-build`), Client CI (`analyze-and-test`, `build-android`, `build-linux`), Host Daemon CI,
  Admin CI (made PR-triggered, GAP-007); update gap register dates (GAP-043 → 2026-09-13).
- **Security:** deploy workflow holds `VPS_SSH_KEY` — SHA-pin actions before re-enabling deploy.
- **Testing:** first green run of each workflow on `main`.
- **Failure tests:** a PR with a deliberately failing test is shown blocked from merge.
- **Metrics:** CI pass rate; time-to-green; count of direct pushes to `main` since protection (must be 0).
- **Exit criteria:** (1) linked green Backend/Client/Hostd/Admin runs on the same `main` SHA;
  (2) protection record (API output or screenshot) in `docs/reports/`; (3) one blocked-PR run
  linked; (4) both deploy workflows parse (no file-named 0-second runs).
- **Rollback:** none needed (process change); protection can be relaxed by an admin.
- **Evidence required:** `docs/reports/m0-ci-restoration.md` with run URLs.

### M1 — Security & Correctness Blockers *(Immediate remediation, part 2)*

- **Objective:** close the exposures that make any side-effecting feature unsafe, and remove known
  false/dead code.
- **Why:** SEC-01/02 break the ADR-006 promise that a poisoned input cannot silently cause a write;
  SEC-03 is a committed credential.
- **Inputs:** SEC-01..SEC-03, SEC-05, SEC-06, SEC-09..SEC-11, SEC-13, GAP-008, GAP-010, GAP-027,
  GAP-046, GAP-047.
- **Dependencies:** code can start now; **exit requires M0** (green CI).
- **Implementation areas:**
  1. **SEC-01** — persist pending approvals server-side `(user, session, turn, call_id, tool,
     sha256(canonical arguments), expires_at)`; return an opaque approval token in the SSE
     `needs_confirmation` frame; on resume execute the **stored** step (no re-plan for the approved
     call) and refuse on digest mismatch; remove name-level `confirmed_tools` from the policy path
     (keep accepting it only as a no-op for old clients); client sends tokens only.
  2. **SEC-02** — compute `workspace_local` per *routing*: exempt only when the resolved runner is
     the Docker sandbox; on `HostAgentSandboxRunner` a tainted turn requires confirmation for every
     side-effecting tool; amend ADR-014; hostd: add a write denylist for `.git/hooks/**`,
     `.git/config` in all tiers.
  3. **SEC-03** — rotate the Google desktop OAuth secret; remove the `defaultValue`; add a
     secret-pattern test over `aura_client/` and `aura_hostd/`.
  4. **SEC-09** — explicit global owner sentinel; reject vector/memory writes without an owner.
  5. **SEC-10** — refuse startup in staging/production without `JWT_SECRET` (≥32 bytes).
  6. **SEC-11** — streamed body-size enforcement; per-route limits consistent with nginx.
  7. **SEC-08** — single client-IP resolver (`extract_client_ip`) for all limiters; per-target
     trusted-proxy config (VPS: 1 hop; Railway: documented value).
  8. **GAP-046/047/027** — per-file secret mounts for backup; separate dev key file; dev stack off
     prod Ollama — both compose files in one commit with a parity test.
  9. **GAP-008/010** — delete `engineering2`, `proactive`, `continuous_intelligence`,
     `security/{authenticator,policy,rate_limiter,secret_manager}`; untrack `aura_hostd/build/*`
     and root strays; guard test.
  10. `_plan_and_execute` records planning/execution failure as a typed degraded outcome + metric
      instead of a silent fallback.
- **Security:** this phase *is* security; each fix lands with a test that fails on the old code.
- **Testing:** new tests in `tests/security/` for each item; architecture test pinning the
  routing-aware exemption.
- **Failure tests:** approval replay with altered arguments; tainted desktop turn issuing
  `run_command`; write without owner; production boot without JWT secret; chunked 50 MB body.
- **Metrics:** count of open P0/P1 security gaps (target 0 for this list).
- **Exit criteria:** every listed item merged with its failing-first test, green in CI (M0);
  gap register entries `proven` or `built_not_proven` → deployed in M2.
- **Rollback:** each item is an independent PR; SEC-01 ships behind a flag that falls back to
  "refuse all resumes" (fail closed), never to name-level confirmation.
- **Evidence required:** PR links + CI runs; `docs/reports/m1-security-closure.md`.

### M2 — Production Reconciliation *(Production foundation, part 1)*

- **Objective:** production runs current `main` and the result is observed, not assumed.
- **Why:** 25 merges of fixes (incl. 11 vulnerabilities) are not live.
- **Inputs:** Deploy VPS workflow; `vps-update.sh`; encrypted-backup precondition (age key);
  domain change to `aureon-ai.tech`.
- **Dependencies:** M0, M1.
- **Implementation areas:** record the live SHA on the VPS before touching it; place the age public
  key and data-encryption key file per M1 layout; run the migration dry-run against a restored copy
  of production (runtime DDL + Alembic 0006–0008); deploy via the normal workflow; post-deploy
  smoke (`/health/ready`, chat SSE, voice-live WS, host-agent WS, upload, idempotent retry);
  re-run `tests/security` HTTP-level cases against the live gateway with test accounts; verify one
  encrypted backup cycle completes.
- **Security:** confirm field encryption active (ciphertext visible via `psql`); confirm no dev
  container can reach prod Ollama/keys.
- **Testing / failure tests:** deploy a known-bad image to staging (dev stack) and confirm the
  rollback procedure restores service; kill the gateway mid-stream and confirm clean recovery.
- **Metrics:** deploy lead time; post-deploy error rate for 24 h.
- **Exit criteria:** live SHA == `main` SHA of the M1 exit; smoke report; one successful encrypted
  backup; 24 h without 5xx regression (nginx logs until M3 metrics exist).
- **Rollback:** previous image tags + database restore point taken immediately before deploy
  (required because runtime DDL is not reversible — see M6).
- **Evidence required:** `docs/reports/m2-production-reconciliation.md`.

### M3 — Observability Foundation *(Production foundation, part 2)*

- **Objective:** every SLO and every latency component is measurable before load/inference work.
- **Why:** M4/M5 decisions need decomposed latency; GAP-036/037 make availability and p95 invisible.
- **Inputs:** GAP-036, GAP-037, P2 latency decomposition (built), PRODUCTION_SLO.md.
- **Dependencies:** M0 (code); M2 (live exit).
- **Implementation areas:** per-route HTTP request histogram + status counter; widen turn buckets
  to ≥300 s and TTFT to ≥60 s; admission-queue wait and depth gauges; outbox age / dead-letter
  gauges with alerts; external blackbox probe; GPU exporter (DCGM) wired for M4; desktop/hostd
  crash & error counters (content-free) for M8.
- **Security:** telemetry stays content-free (ADR-009) — test it.
- **Testing:** unit tests on metric names/labels; Grafana p95 reproduces a load-test p95 within one
  bucket.
- **Failure tests:** outbox stalled → alert fires; gateway down → blackbox alert.
- **Metrics:** SLO coverage = SLO rows with status `measurable` / total (target 100%).
- **Exit criteria:** every PRODUCTION_SLO row measurable on live dashboards; alert rules tested.
- **Rollback:** metrics are additive; revert PR.
- **Evidence required:** dashboard screenshots + alert test log in `docs/reports/`.

### M4 — Inference Platform *(Inference platform)*

- **Objective:** at least one pinned GPU pool serves production through the existing
  `openai_compatible` provider, with CPU Ollama as last resort and proven failover.
- **Why:** the single real bottleneck (review §29); prerequisite for M5, M7 (real-model eval),
  M8 inline AI, M12.
- **Inputs:** GAP-011/012/030/031/034/035/042; DPL-2/3/5/6/9/10/11; MODEL_REGISTRY.md;
  `deploy/spaces/inference-vllm/`, compose `vllm` profile.
- **Dependencies:** M1, M3; **Decision D-1 → ADR-017** first.
- **Implementation areas:**
  1. **ADR-017 (decision gate):** owned/dedicated GPU host on a private link vs rented HF GPU Space
     vs both. Must state: what data leaves the control plane (prompts, retrieved chunks, memory),
     retention/logging at the host, encryption in transit, contractual/licensing position, cold-start
     and sleep behavior, cost per 1k turns. Until merged, `AURA_ALLOW_REMOTE_INFERENCE` stays off in
     production (GAP-034).
  2. **Inference contract v1:** document the subset of OpenAI `/v1/chat/completions` + `/v1/models`
     AURA depends on (streaming, `max_tokens`, stop, errors, request-id header); keep
     `tests/deployment/test_inference_contract.py`; add a live contract smoke usable against any pool.
  3. **Pinning:** image digest and model **revision** for every served model (Ollama digest for
     the CPU model; `--revision` for vLLM; HF Space base image digest); startup check that loaded
     == pinned; license review per model recorded.
  4. **Service auth:** per-plane API key with documented rotation; `/metrics` and `/health` not
     public (or proxied); request size and timeout bounds.
  5. **Router:** per-provider circuit thresholds (GAP-035); re-size `max_output_tokens`/timeouts
     from measured GPU throughput; **never a sole remote provider** — CPU Ollama stays configured as
     last resort (or the degraded mode is a documented, tested 503).
  6. Disable multi-sample reasoning strategies on the CPU provider.
- **Security:** no Flutter path to the inference plane (fitness-tested); ADR-017 data-egress rules
  enforced by settings validation.
- **Testing:** contract tests; routing tests with the real pool (extend
  `tests/load/test_multi_model_routing.py`).
- **Failure tests (from DPL-9):** pool down, timeout, 401/403, 429, 5xx, model not loaded, OOM,
  stream cut mid-generation (must surface error, not duplicate), control-plane restart, inference
  restart, network partition — each with expected router behavior asserted.
- **Metrics:** TTFT p50/p95, tokens/s, queue wait, GPU util/VRAM, error class counts, cost/1k turns.
- **Exit criteria:** ADR-017 merged; one pool serving production traffic with pinned
  image+revision; failover-to-CPU report; failure-test report covering every case above;
  MODEL_REGISTRY has no `UNPINNED` production entry; GAP-011/012/030/031/034/035/042 closed.
- **Rollback:** router config flip back to CPU Ollama (tested in failure tests); previous pool
  image+revision retained.
- **Evidence required:** `docs/reports/m4-inference-cutover.md`, failure-test report, license table.

### M5 — Capacity Evidence *(Scalability)*

- **Objective:** measured capacity at 1/5/10/25/50/75/100 concurrent users on the production
  topology, and the first saturating component named.
- **Why:** GAP-041; no capacity claim exists that isn't an echo artifact.
- **Inputs:** Phase-3 harness (built, self-validated only), M3 metrics, M4 pool.
- **Dependencies:** M3, M4.
- **Implementation areas:** realistic turn mix (chat, RAG, tool, voice, upload); 2 h soak at 50;
  tune `AURA_WORKERS`, pool sizes, Celery concurrency, vLLM batching from data; verify abandoned
  streams release inference capacity; measure gateway memory with in-process Whisper/embedder ×
  workers; Postgres connection budget.
- **Security:** load accounts isolated; no production user data.
- **Failure tests:** pool failure at 50 users; Redis restart during load; admission control
  returns 429/503 with `Retry-After` rather than timeouts.
- **Metrics:** TTFT/answer p50/p95/p99, error rate, goodput, queue depth, CPU/GPU/mem, DB
  connections; each with hardware, model, dataset, concurrency, method, samples, warm/cold.
- **Exit criteria:** report showing PRODUCTION_SLO S3/S4/S5 at 50 users (TTFT p95 < 2.5 s, answer
  p95 < 15 s — targets to be re-affirmed from M3 baselines) and the saturating component at 100;
  GAP-041 closed. **If targets fail, the report names the component and the next step; no
  Kubernetes proposal without that report.**
- **Rollback:** tuning changes are config; revert.
- **Evidence required:** `docs/reports/m5-capacity-matrix.md` + raw JSON.

### M6 — Data Protection & DR *(Reliability / DR)*

- **Objective:** confidentiality and recoverability claims hold on production data, and schema
  changes are reversible.
- **Why:** GAP-016/028/046; runtime DDL makes image rollback unsafe; Railway target moves data to
  third parties.
- **Inputs:** P1.2 field encryption, P1.3 encrypted backups (built), DISASTER_RECOVERY.md.
- **Dependencies:** M1 (secret layout), M2 (deployed).
- **Implementation areas:** ADR on encryption at rest (what is ciphertext: memory, personal-OS v3
  fields; decision on chunks/documents and Qdrant payloads); **move all 26 runtime-DDL tables under
  Alembic** (baseline migration that is a no-op on existing databases, then forbid runtime DDL via
  fitness test); migration downgrade tests; offsite encrypted backups; quarterly drill restoring the
  offsite copy onto a fresh host with measured RPO/RTO; key-rotation drill; data deletion
  (`forget_source`, account deletion) verified end to end across Postgres/Qdrant/Redis/backups
  retention; Railway/managed-service backup equivalents if ADR-017 adopts Railway.
- **Security:** backup container cannot read data keys (M1); restore requires the offline private
  key.
- **Failure tests:** restore from a backup older than a migration; restore with a rotated key;
  partial Qdrant loss → re-index from Postgres.
- **Metrics:** RPO, RTO, restore success rate, time-to-reindex.
- **Exit criteria:** drill report from offsite data with measured RPO/RTO meeting the stated
  objectives; no runtime DDL in `aura_core` (test); downgrade path tested for every migration after
  the baseline; account-deletion test on real infra.
- **Rollback:** the Alembic baseline is additive; drills are non-destructive by design.
- **Evidence required:** `docs/reports/m6-dr-drill-<date>.md`, ADR.

### M7 — Evaluation Gates *(Evaluation)*

- **Objective:** real-model quality baselines exist and gate merges and model changes.
- **Why:** GAP-021/022/006 — today every gated number comes from a scripted provider.
- **Inputs:** `EvalSuite`, `RetrievalEvaluator`, `AnswerEvaluator`, `AgentEvaluator`,
  `RegressionGate`, eval-ci.yml.
- **Dependencies:** M0 (gating), M4 (real model).
- **Implementation areas (evaluation requirements EV-1..EV-12):**
  EV-1 retrieval with the **production embedder** (+ per-strategy); EV-2 groundedness & citation
  precision with the production LLM; EV-3 factuality/hallucination on a labelled QA set;
  EV-4 planning correctness (valid DAG, right tools, right arguments); EV-5 tool-argument accuracy;
  EV-6 agent task success & recovery with a real model; EV-7 guard precision/FP (leak, groundedness,
  injection) on labelled real outputs; EV-8 instruction following EN/AR; EV-9 memory relevance;
  EV-10 WER EN/AR (feeds M12); EV-11 desktop AI UX tasks (feeds M8); EV-12 cost/latency per quality
  point. Deterministic metrics gate; LLM-judge advisory only. Scheduled real-model run against the
  pool; PR-time runs keep scripted providers for plumbing regressions.
- **Security:** eval datasets contain no user data; guard suites include injection corpora.
- **Failure tests:** a deliberately regressing PR (retrieval ranking, prompt change) is blocked.
- **Metrics:** each EV metric with dataset size, model revision, embedder revision, date.
- **Exit criteria:** baselines committed for EV-1..EV-9; blocked-PR run linked; scheduled run
  publishing `aura_eval_score`; GAP-021/022/006 closed.
- **Rollback:** gates can be set advisory by config, recorded in the report.
- **Evidence required:** `docs/reports/m7-eval-baselines.md`.

### M8 — Desktop IDE Productization *(Desktop product)*

- **Objective:** a Windows-first (then Linux) professional IDE with AURA integrated, certified on a
  golden path.
- **Why:** declared product priority; large implemented surface that has never been built in CI
  since 2026-09-13.
- **Inputs:** ADR-011, ADR-015, desktop Phase-0 audit, IDE-0..IDE-24.
- **Dependencies:** M0 (Dart/Flutter CI), M1 (SEC-02, SEC-03). Runs **in parallel** with M3–M7.
  M8.5b depends on M4 (latency). M8.13 depends on M7 (EV-11).
- **Sub-milestones (each: exit = CI green + named evidence):**

| ID | Scope (IDE-n absorbed) | Exit criteria |
| --- | --- | --- |
| M8.0 | Re-baseline: compile/analyze/test everything since 09-13 in CI; convert Phase-0 audit "VERIFIED (code-read)" rows into executed checks (IDE-0) | Client CI + Hostd CI (Linux & Windows) green; `build-windows` made gating |
| M8.1 | Design system & shell: density tokens, component states, resize/persist, focus modes (IDE-1, IDE-2) | Golden widget tests; layout survives restart (integration test on Windows runner) |
| M8.2 | Workspace, explorer, editor, navigation: context menu, grant-access UX, preview tabs, cursor restore, fuzzy quick-open, command registry (IDE-3–IDE-5) | Integration tests; 10k-file repo opens < target (M8.9) |
| M8.3 | Terminal cross-platform: POSIX PTY for Linux/macOS, shell selection, env control, cleanup on exit/crash (IDE-6) | PTY tests on Windows + Linux runners; no orphan processes after kill test |
| M8.4 | Git & diff: branch list, fetch/pull/push via hostd with confirmation, side-by-side diff, hunk actions, conflict UI (IDE-7) | Real-git tests incl. conflict path |
| M8.5a | AI coding: agent panel with plan/steps/changed-files, multi-file edit review using server diff previews and SEC-01 approval tokens (IDE-8) | EV-11 subset; approval-token E2E |
| M8.5b | Inline intelligence (IDE-9) — **gated on M4 TTFT p95 < 500 ms for the coder pool** | Latency report + acceptance-rate metric |
| M8.6 | Problems, test explorer, debug (DAP beyond Python) (IDE-10) | Test explorer runs pytest/dart test; DAP for ≥2 languages |
| M8.7 | Agent workspace, approvals & security UX: per-task worktrees, approval queue UX, trust-tier UX that explains consequences (IDE-11, IDE-12) | Security UX review; SEC-02 regression tests |
| M8.8 | Host management & crash recovery: bundled signed `aura_hostd`, supervisor restart-on-crash with backoff, hot-exit/autosave, recovery of unsaved buffers (IDE-13, IDE-18) | Kill-daemon and kill-app tests recover state |
| M8.9 | Performance, large repos, accessibility, personalization (IDE-14–IDE-17) | Measured: cold start, open 100k-file repo, typing latency, memory; screen-reader pass |
| M8.10 | Packaging & updates: MSIX/Inno (Windows), AppImage/deb (Linux), code signing, update channel with rollback (IDE-19) | Signed installer; update + rollback test |
| M8.11 | Desktop observability: content-free crash/error telemetry, opt-in (IDE-21) | Telemetry schema test; opt-out honored |
| M8.12 | Cross-platform & security certification: Windows + Linux matrix, junction/symlink/ACL tests, pairing/token threat review (IDE-20, IDE-22) | Certification report; macOS **deferred** until Windows/Linux certified |
| M8.13 | Golden-path certification (IDE-23, IDE-24): scripted end-to-end task on a real repo (open → edit → test → debug → agent change → review → commit → push) | Recorded run + EV-11 metrics |

- **Security:** SEC-02 closed before M8.5a; hostd write denylist; signed binaries only.
- **Failure tests:** hostd killed mid-command; backend unreachable; token revoked mid-session;
  junction escape attempt; disk full.
- **Metrics:** crash-free sessions, cold start, typing latency p95, agent task success (EV-11).
- **Rollback:** update channel rollback (M8.10); feature flags per sub-milestone.
- **Evidence required:** one report per sub-milestone in `docs/reports/desktop/`.

### M9 — Engineer Execution Surfaces Proof *(Agent platform, part 1)*

- **Objective:** prove server-side persistent workspaces (ADR-013/014) and the hostd path end to end.
- **Dependencies:** M2 (current SHA live), M1 (SEC-01/02), M8.7 for the hostd half.
- **Implementation areas:** enable `AURA_SANDBOX_ENABLED` + `sandbox` profile on the VPS (or a
  prod-equivalent host); run `deploy/scripts/verify-engineer-workspaces.sh` (checks 0–9); manual
  check 10 (exactly one confirmation per clone→edit→test→push); hostd E2E report (pair, grant,
  read/edit/run, containment refusal, approval, revoke).
- **Failure tests:** gateway restart re-adopts workspace; reaper deletes expired; egress denied to
  unlisted host; socket-proxy `EXEC` still 403.
- **Exit criteria:** script report with all checks passing committed; hostd E2E report; GAP-025/026
  closed.
- **Rollback:** sandbox profile off (current state).
- **Evidence required:** the two reports.

### M10 — Knowledge & Memory Quality *(AI platform)*

- **Objective:** RAG and memory meet measured quality targets with production models.
- **Dependencies:** M7 (EV-1/2/9), M4 (optional embed/rerank pool), M6 (encryption decision for chunks).
- **Implementation areas:** pin embedder/reranker revisions; larger multilingual (EN/AR) labelled
  corpus; per-strategy comparison and default chosen by data; index upgrade via ADR-007 promotion
  (new `IndexIdentity`, reindex, recall gate, alias switch, rollback) exercised for real;
  knowledge-graph strategy enabled only where a multi-hop suite shows it wins (GAP-023); memory
  consolidation at volume on real Postgres; client memory pagination (remove the 100 cap);
  cross-user probe suite on real infra.
- **Exit criteria:** Recall@10 ≥ 0.80, groundedness ≥ 0.90, citation precision ≥ 0.90 with
  production LLM+embedder, per strategy (targets re-affirmed from M7 baselines); one real index
  promotion + rollback executed; isolation probe report.
- **Rollback:** alias switch back (ADR-007).
- **Evidence required:** `docs/reports/m10-rag-memory.md`.

### M11 — Agents, Proactivity & Personal OS *(Agent platform, part 2; Personal OS)*

- **Objective:** deliberate, measured autonomy; one coherent Personal-OS model; safe proactivity.
- **Dependencies:** M1 (SEC-01), M7 (EV-4..6).
- **Implementation areas:** ADR on per-persona autonomy (which personas may replan; budgets from
  EV-6 data); **Personal-OS store decision** — either expose `aura_personal_os_records` to the client
  (route + sync + Drift) or route server tools to propose-and-confirm against the encrypted legacy
  tables; document the choice and delete the loser's write path; proactivity: new triggers only
  through outbox/event bus with opt-out, quiet hours, per-user rate caps, and confirmation policy
  for any action; outbox no-loss under worker crash on real Postgres.
- **Exit criteria:** ADRs merged; agent success measured with real model; a task created by an
  agent is visible in the app (or the tool-only decision documented and the UI says so); trigger
  crash test report.
- **Rollback:** per-persona config; triggers individually disableable.
- **Evidence required:** reports per item.

### M12 — Multimodal, Voice & NLP *(Multimodal)*

- **Objective:** vision (image/document understanding) and multilingual voice with measured quality.
- **Dependencies:** M4 (VLM/ASR pools), M7 (EV-10).
- **Implementation areas:** VLM pool behind `openai_compatible`; kernel image path with an image
  trust channel, size limits, taint semantics; client upload plumbing; multilingual ASR (Arabic) —
  fixes the TTS/STT asymmetry; neural VAD; device-proven live duplex (Android + iOS); NLP intent
  layer evaluated on real transcripts; **video only on demonstrated product need**.
- **Exit criteria:** vision eval report with pinned model; WER report EN/AR; live-voice
  first-audio latency report on real devices; labelled intent suite accuracy.
- **Rollback:** capability flags per modality.

### M13 — Learning / MLOps *(Evaluation / MLOps)* — **deferrable**

- **Objective:** either real, gated fine-tuning on the production base model, or an honest relabel.
- **Dependencies:** M4 (GPU), M7 (RegressionGate on real metrics).
- **Implementation areas:** LoRA/QLoRA on the pinned base; dataset digest; eval delta vs production;
  promotion only via ApprovalService + model registry; rollback; relabel "knowledge refresh" and
  "memory consolidation" everywhere as non-training.
- **Exit criteria:** a run report (base revision, dataset digest, weight delta, eval delta,
  approval record, rollback test) **or** a merged relabel.

### M14 — Client Reach & Admin Control Plane *(Flutter / Admin)*

- **Objective:** every user-facing backend capability reachable from the client; an ops console
  covered by PR-time tests.
- **Dependencies:** M0. Parallel with everything after M1.
- **Implementation areas:** keep the generated reachability matrix gating; wire or declare the 16
  uncalled routes; pin release toolchain to CI Flutter (GAP-044); Flutter↔backend schema contract
  tests; admin Playwright specs for all 9 areas, PR-triggered (GAP-007); admin audit views for tool
  executions and approvals.
- **Exit criteria:** inventory shows no unconsumed user-facing route; release build uses the CI
  toolchain (run linked); 9/9 admin areas covered on PRs.

### M15 — Production Certification & Launch *(Security certification; Production certification)*

- **Objective:** certify a release SHA and launch to 50+ users under operated SLOs.
- **Dependencies:** M3, M5, M6, M7, M9, M10 (+ M8.13 for the desktop product), all P0/P1 gaps closed.
- **Implementation areas:** external penetration test and remediation; secret-rotation drill; image
  and action pinning test; compose parity test beyond `EXEC`; chaos pass (kill a pool, Postgres
  restore, Redis loss); full RELEASE_CHECKLIST with links; canary cohort via router config + feature
  flags with automatic rollback on SLO burn; 24 h and 7 d reviews; SLOs adopted from measured
  baselines with error budgets, deploy-freeze policy, on-call; then continuous improvement
  (monthly gap review, quarterly DR drills and model bake-offs).
- **Exit criteria:** checklist complete with links for the launch SHA; canary holds SLOs 7 days;
  30 days of SLO reporting in production.
- **Rollback:** canary rollback trigger (tested during canary).

## 19. Final Target State

| Dimension | Target (each measured, linked) |
| --- | --- |
| Delivery | Protected `main`, required checks, deploy-on-green, zero direct pushes |
| Security | 0 open P0/P1; argument-bound confirmation; routing-aware exemptions; pen test remediated |
| Inference | ≥1 pinned GPU pool + CPU last resort; failover-tested; ADR-017 in force |
| Capacity | 50 concurrent users within SLO; saturation point at 100 documented |
| Quality | EV-1..EV-9 baselines gated; RAG ≥ targets with production models |
| Data | One migration authority; encryption decision implemented; offsite drill within RPO/RTO |
| Observability | Every SLO measurable, alerting with burn rates |
| Desktop | Signed, auto-updating Windows + Linux IDE certified on the golden path |
| Engineer | Server workspaces and hostd both proven by committed reports |

## 20. Master Execution Sequence

```
M0 Evidence pipeline ─► M1 Security blockers ─► M2 Production reconciliation ─► M3 Observability
                                  │                                                   │
                                  ├──────────────► M8 Desktop (M8.0…M8.12) ──────────┐│
                                  ├──────────────► M14 Client reach & admin          ││
                                  │                                                   ▼▼
                                  │                    ADR-017 ─► M4 Inference ─► M5 Capacity
                                  │                                   │
                                  └► M6 Data protection & DR          ├─► M7 Evaluation ─► M10 RAG/Memory
                                         (after M2)                   │                 ├─► M11 Agents/PIM/Proactivity
                                                                      │                 └─► M13 MLOps (deferrable)
                                                                      ├─► M12 Multimodal/Voice (after M7)
                                                                      └─► M8.5b Inline AI
M2 + M1 + M8.7 ─► M9 Engineer proof
M3 + M5 + M6 + M7 + M9 + M10 (+ M8.13) ─► M15 Certification ─► canary ─► SLOs in force
```

| ID | Name | Priority | Depends on | Complexity | Risk | Current status | Primary components | Exit (short) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| M0 | Evidence pipeline & governance | Critical | — | Low | Low (account action) | Not started (blocked on account) | `.github/`, repo settings | Green CI + protection |
| M1 | Security & correctness blockers | Critical | M0 (exit) | Medium | Medium | Not started (SEC-01/02/03 new) | tools/policy, execution, client chat, hostd, compose | All fixes + failing-first tests green |
| M2 | Production reconciliation | Critical | M0, M1 | Medium | High (first deploy after 25 merges) | Not started | deploy/, VPS | Live SHA == main |
| M3 | Observability foundation | High | M0, M2 | Medium | Low | Partial (turn metrics) | observability/, deploy/prometheus | All SLOs measurable |
| M4 | Inference platform | Critical | M1, M3, ADR-017 | High | High | Built (config), unproven | llm/, deploy/spaces, compose gpu | Pinned GPU pool in prod + failure report |
| M5 | Capacity evidence | Critical | M3, M4 | Medium | Medium | Harness built | tests/load | 50-user SLO report |
| M6 | Data protection & DR | High | M1, M2 | High | High (schema) | Partial (built, not deployed) | adapters, alembic, backup | Offsite drill + no runtime DDL |
| M7 | Evaluation gates | High | M0, M4 | Medium | Medium | Scripted gates only | evaluation/ | EV baselines gated |
| M8 | Desktop IDE productization | High (product) | M0, M1 | High | Medium | Implemented, uncompiled since 09-13 | aura_client/desktop, aura_hostd | M8.13 golden path |
| M9 | Engineer execution proof | Medium | M1, M2, M8.7 | Medium | Medium | Built, unproven | sandbox, hostd | Two reports |
| M10 | Knowledge & memory quality | High | M7, M4, M6 | Medium | Medium | Tested (hash embedder) | rag/, knowledge/, memory/ | RAG targets met |
| M11 | Agents, proactivity, Personal OS | Medium | M1, M7 | Medium | Medium | Tested (echo) | agents/, triggers/, personal-OS | ADRs + measured success |
| M12 | Multimodal, voice, NLP | Medium | M4, M7 | High | Medium | OCR/batch voice executed | voice/, nlp/, kernel image path | Vision + WER + device reports |
| M13 | Learning / MLOps | Low (deferrable) | M4, M7 | High | Medium | Mechanism executed on tiny model | learning/, mlops/ | Run report or relabel |
| M14 | Client reach & admin | Medium | M0 | Medium | Low | In progress | aura_client, aura_admin | No unconsumed route; 9/9 specs |
| M15 | Certification & launch | Critical | M3,M5,M6,M7,M9,M10 (+M8.13) | High | High | Not started | all | 7-day canary + 30-day SLOs |

**Critical path (backend production):** M0 → M1 → M2 → M3 → M4 → M7 → M10 → M15 (longest chain;
M5 and M6 run beside it and must finish before M15).
**High-risk path:** M2 (first deploy of 25 merges incl. runtime DDL) and M4 (hosting decision +
cutover).
**Parallel lanes after M1:** Desktop (M8) · Client/admin (M14) · Data protection (M6, after M2) ·
Observability (M3).

## 21. Definition of Done

AURA is production-ready only when **every** box has a linked artifact in `docs/reports/`:

- [ ] GitHub Actions runs every workflow; `main` protected; 0 direct pushes since protection (M0)
- [ ] SEC-00..SEC-03 and every P0/P1 gap closed and deployed (M1, M2)
- [ ] Live SHA reported and equal to the certified SHA (M2, M15)
- [ ] Every PRODUCTION_SLO metric measurable and dashboarded (M3)
- [ ] ADR-017 merged; ≥1 pinned GPU pool; failover and failure-test reports (M4)
- [ ] 50 concurrent users within SLO on the production topology (M5)
- [ ] One migration authority; offsite restore drill within RPO/RTO; deletion verified (M6)
- [ ] Real-model EV baselines gate merges; a regressing PR shown blocked (M7)
- [ ] RAG/memory targets met with production models; isolation probe clean (M10)
- [ ] Engineer workspaces and hostd proven by reports (M9)
- [ ] Personal-OS store decision implemented (M11)
- [ ] All production models/images pinned with licenses reviewed (M4)
- [ ] Learning is real and gated, or relabelled (M13)
- [ ] Pen test remediated; release checklist complete; 7-day canary; 30 days of SLOs (M15)
- [ ] For the desktop product: M8.0–M8.13 complete, signed installers, golden path recorded

**Engineering completion rule:** report the highest maturity state reached with its evidence — never
"implemented" as a synonym for "works", never "done" without the named artifact.
