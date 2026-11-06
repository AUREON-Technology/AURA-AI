# Testing and Evaluation — Audit Pass G

**Audit:** AURA AI full-system engineering audit · **Pass:** G (testing, CI gates, AI evaluation)
**Baseline:** `main` @ `ffbbb9fff777a7b6117236c67d517fd201ac2208` (`ffbbb9f`) · **Date:** 2026-10-31
**Author:** Audit subagent pass G

## 1. Scope and method

Read: `aura_backend/pytest.ini`, `tests/conftest.py`, every file name under `aura_backend/tests/` and the 18 root-level legacy `test_*.py`, all 12 `.github/workflows/*.yml` (backend, eval, client, hostd, admin, repo-hygiene in full), `aura_backend/evaluation/{gate,runner,vision_eval}.py`, `aura_core/evaluation/harness.py` (`RegressionGate`), the four committed baselines under `evaluation/suites/`, the gate tests under `tests/e2e/`, the certification/training/load/benchmark suite headers, `aura_admin/playwright.config.ts` + `package.json`, the client and hostd test directories, and the prior-audit material named in the brief (`AURA_FULL_SYSTEM_REVIEW.md` §31–32, `AURA_CRITICAL_BACKLOG.md`, `gap_register.yaml`, the four `docs/status/*.md`).

Ran (read-only, see §7): `pytest --collect-only` on the default suite; `pytest tests/architecture tests/e2e tests/deployment`; attempted `flutter test` and `dart test`.

Could not: probe GitHub (branch protection, Actions run history), run the legacy `unittest` suite with Docker, run `tests/load`/`tests/benchmarks`/`tests/training`/`tests/certification` (need infrastructure, a model download, or live credentials), finish the Dart suites (the machine's `C:` temp volume is full and both Dart runners write their kernel snapshots there — see §7).

**Environment caveat that colours every local number in this report and in `docs/status/*.md`:** the repository venv is CPython 3.14.4 with `fastapi 0.136.3` and no `prometheus_client`, while `requirements.lock` pins `fastapi==0.141.1` / `prometheus-client==0.26.0` for Python 3.11 (`aura_backend/requirements.lock`; EXECUTED-HERE: `venv/Scripts/python -c "import fastapi,sys; print(fastapi.__version__, sys.version)"` → `0.136.3 3.14.4`). The suite as run locally is therefore not the suite CI would run.

## 2. Test inventory (what exists)

### 2.1 Backend — default `pytest` suite (`aura_backend/tests/`, in-memory, no infra)

| Directory | Files | Tests collected (EXECUTED-HERE, `--collect-only`) | What it covers |
|---|---:|---:|---|
| `tests/unit` | 173 | 3605 | engines, adapters, routes, compose/Dockerfile invariants, repo hygiene (`test_repo_hygiene.py`), release identity |
| `tests/security` | 18 | 272 (+ `test_route_auth.py`, **not collected locally**, see G-07) | route-enumerating auth, IDOR, SSRF, injection end-to-end, concurrent taint, token rotation, WS identity, attestation |
| `tests/architecture` | 18 | 347 | AST layering (`test_dependency_rules.py`), no external inference, v3-only surface, workflows parse, gap-register schema, certification-matrix consistency (`test_certification_gate.py`), API reachability inventory |
| `tests/e2e` | 5 | 44 | `agent_tasks`, `docs_corpus`, `agent_runs` gates; `test_evaluation_gate.py` proves `evaluation/gate.py` goes red on an unmeetable baseline; `test_pipeline.py` |
| `tests/integration` | 2 | 33 | learning pipeline; load-harness smoke in-process over `httpx.ASGITransport` |
| `tests/deployment` | 1 | 4 | inference contract |
| **Total collected** | **215** | **4305** | one collection error (`tests/security/test_route_auth.py`, local venv only) |

`pytest.ini:17` excludes `bench`, `load`, `training`, `cert` markers from every default run (CODE-READ).

### 2.2 Backend — opt-in suites (never in the default run)

| Suite | Files | Marker / how run | CI? | Evidence of ever having run |
|---|---:|---|---|---|
| `tests/load/*_postgres.py` | 11 | `-m load` + `AURA_TEST_DATABASE_URL` | **Yes** — `backend-ci.yml:197-242` `postgres` job (Postgres 16 service) | Status docs claim green at `dc87beb`; UNKNOWN here |
| `tests/load/` other (Qdrant, Redis, Ollama, vLLM, Docker sandbox, GitHub live, MCP live, voice round-trip, capacity matrix, MySQL/MSSQL discovery) | 11 | `-m load` + env | No | `docs/reports/phase-a4-load-test.md`, `phase-3-loadtest-harness-self-validation.md` (dated reference runs) |
| `tests/benchmarks/bench_*.py` | 2 | `-m bench` | No | `docs/reports/benchmark-report.md` (DOCUMENTED ONLY) |
| `tests/training/test_lora_weights_change.py` | 1 | `-m training` + `requirements-train.lock` + model download | No | GAP-017 (`gap_register.yaml:465`); skips, never passes, without PEFT (`tests/training/test_lora_weights_change.py:20-22`) |
| `tests/certification/` | 4 | `-m cert` + `AURA_CERT_BASE_URL/TOKEN_A/TOKEN_B` against a live deployment; **writes to the target** | No (by design) | `docs/reports/AURA_CERTIFICATION_EVIDENCE_MATRIX.md:7` — "OVERALL VERDICT: NOT PRODUCTION CERTIFIED", updated 2026-09-28 |

### 2.3 Backend — legacy `unittest` tree (root-level `aura_backend/test_*.py`)

18 files, 133 `def test_` methods (EXECUTED-HERE: grep count; CI reported "100 OK" / status doc "106 run, 10 skipped"). Run as `TESTING=True python -m unittest discover -p "test_*.py"` (`backend-ci.yml:101-110`). Four files (`test_celery_loop_integration.py`, `test_erasure_integration.py`, `test_insights_integration.py`, `test_github_link_integration.py`) need Docker via testcontainers and `skipUnless(_docker_available())` otherwise (CODE-READ, e.g. `test_erasure_integration.py:31`). `pytest.ini` `testpaths = tests` means **pytest never collects these** (collect-only: 0 root-level ids); the two suites are disjoint, as CLAUDE.md states.

### 2.4 Client, daemon, console

| App | Test files | Runner | CI job | Last known result (DOCUMENTED ONLY, `docs/status/*`) |
|---|---:|---|---|---|
| `aura_client` (Flutter) | 76 `*_test.dart` | `flutter test` | `client-ci.yml` `analyze-and-test`; `build-android`; `build-windows` (`continue-on-error: true`, :120); `build-linux` | 381 passed / 3 skipped on Linux (run 142–143, 2026-10-28); Windows 371 passed / 10 failed (`path_containment_test.dart`); Android out of disk |
| `aura_hostd` (Dart) | 10 `*_test.dart` | `dart test` | `hostd-ci.yml` Linux (gating) + Windows (`continue-on-error: true`, :73) | Linux 220 passed then PTY isolate hang to the 15-min timeout; Windows 5 failures |
| `aura_admin` (Next.js) | 5 Playwright specs (`tests/e2e/*.spec.ts` ×4, `tests/unit/refresh.spec.ts`) | `playwright test` (`testDir: "tests"`, mock gateway + `next dev` via `webServer`) | `admin-ci.yml` (PR-triggered since `5465a2a`, 2026-10-30): `tsc --noEmit`, `next build`, `test:e2e`; **no lint step** (`admin-ci.yml:13-15`) | Admin CI has never run (`WEB_CONSOLE_STATUS.md:15,44`); local run 2026-10-30 had one failing spec (`AURA_CRITICAL_BACKLOG.md:195`) |
| `deploy/scripts` (shell) | `tests/test_backup_crypto.sh` | `sh` + `age` | `backend-ci.yml:167-189` | UNKNOWN |

## 3. CI gate matrix (what is wired vs. what actually blocks)

| Workflow | Trigger | Gates | Blocks a merge? | Observed state |
|---|---|---|---|---|
| Backend CI (`backend-ci.yml`) | PR/push on `aura_backend/**`, `deploy/**` | syntax; `pytest tests --cov=aura_core --cov-fail-under=82` (:94); `pytest tests/architecture`; legacy unittest; pip-audit; backup-script tests; Postgres adapter tests; 3 Docker builds | Only via branch protection — **`main` unprotected** per brief / `AURA_FULL_SYSTEM_REVIEW.md:248` (SEC-04, GAP-005 open). UNKNOWN here (no GitHub access) | Last green `dc87beb` (3848 passed); no runner since ~2026-10-28 19:50 UTC (CB-44) |
| Agent Evaluation (`eval-ci.yml`) | PR/push path-filtered to engines, `evaluation/**`, `docs/architecture/**` (:26-72) | `python -m evaluation.gate` (4 deterministic gates); `dispatch-run` is manual only | Not required (`eval-ci.yml:17-20` says so itself) | No run recorded in `docs/reports/` (GAP-006 reverification text) |
| Client CI | PR/push `aura_client/**` | analyze, test, 3 builds | Not required | Red: Android disk, Windows non-gating |
| Host Daemon CI | PR/push `aura_hostd/**` | analyze `--fatal-infos`, test (Linux gating, Windows advisory) | Not required | Red: Linux PTY hang (fix `4b1968e` not CI-run) |
| Admin CI | PR/push `aura_admin/**`, dispatch | tsc, build, Playwright | Not required | Never run |
| Repo Hygiene | every PR | actionlint; `check_tracked_artifacts.py`; `test_repo_hygiene.py` (action SHA pins, Flutter/Dart pins) | Not required | Built 2026-10-30, not CI-run |
| Deploy Control Plane / Deploy VPS | `workflow_run` after Backend CI | — | — | Railway's own GitHub auto-deploy ships every `main` push with or without CI (CLAUDE.md; `BACKEND_STATUS.md:19-22`) |

Net effect: **no test suite in this repository currently prevents any change from reaching production.** This is the single most important fact of this pass and is a process state (CB-44 + GAP-005), not a code defect.

## 4. AI evaluation matrix

| Suite | Cases | Provider / embedder | Baseline (`evaluation/suites/*_baseline.json`) | Tolerance | Gated where | What it proves |
|---|---:|---|---|---|---|---|
| `agent_tasks` | 50 | `EchoProvider` | pass 1.0 / tool_success 1.0 / completion 1.0 | 0.02 pass_rate, 0.05 others | `tests/e2e/test_agent_tasks_gate.py`, `evaluation/gate.py` | Kernel round-trip smoke only; by construction a case passes iff its expected substring is already in the query (`test_agent_tasks_gate.py:5-11`) |
| `docs_corpus` retrieval | 23 queries / 10 ADRs | `HashEmbeddingProvider` (non-semantic) | recall@5 0.65 / nDCG 0.51 / MRR 0.37 / pass 0.61 | 0.02 recall, 0.03 ndcg, 0.05 others | same + `test_docs_corpus_gate.py` | Holds a lexical-overlap number steady; production embedder (`bge-small-en-v1.5`) never evaluated |
| `docs_corpus` answer | 23 | `ContextGroundedProvider` (scripted) | groundedness 0.94 / cited 0.83 | 0.05 | same | The scripted stand-in reads fenced content; says nothing about any real model |
| `agent_runs` | 12 scenarios | scripted planner + FrozenClock | completion 0.33, partial 0.56, tool_accuracy 0.53, replan 0.40, unsafe_prevention 1.0 | 0.001 | `test_agent_runs_gate.py`, `gate.py` | Loop/budget/replan mechanics; the 0.33 completion is a scripted fixture value, not a capability measurement |
| Vision (`evaluation/vision_eval.py`, 15 KB `vision_cases.py`) | — | real model required (`space`/`gateway`/`kernel` targets) | none committed; "No pass threshold is built in" (`vision_eval.py:36-38`) | — | not in any workflow; only `tests/unit/test_vision_eval.py` self-test | Harness exists; **no vision eval report in `docs/reports/`** (ls: none) |
| Real-model answer quality, hallucination, planning correctness, guard FP rate, memory relevance, voice WER, instruction following EN/AR | — | — | — | — | — | **NOT IMPLEMENTED** (unchanged from `AURA_FULL_SYSTEM_REVIEW.md:498-507`; GAP-021 `status: open`, GAP-022 open) |

`RegressionGate.check` (`harness.py:710-726`) fails only on a **drop** beyond tolerance, silently skips a baselined metric that is no longer measured (`:716-717`; `gate.py:259-261` closes that hole for the CI gate only), and treats any un-baselined metric as `info`. Baselines must be re-recorded by hand; nothing ratchets upward.

## 5. Status classification (pass-G view of the prior concerns the brief lists)

| Concern | Test(s) | Status |
|---|---|---|
| PostgresPool concurrency | `tests/unit/test_postgres_pool_concurrency.py` (exists) | IMPLEMENTED — NOT FULLY VERIFIED (in-memory lock test; real-PG job exists in CI but CI not running) |
| Encryption / ADR-032 data key | `tests/unit/test_data_key_endpoint.py`; `tests/load/test_field_encryption_postgres.py` (CI `postgres` job) | IMPLEMENTED — NOT FULLY VERIFIED (not CI-run since merge) |
| Agent memory isolation | no test under `tests/` references `memory_scope` outside adaptation fixtures (grep) | PARTIALLY IMPLEMENTED (GAP-024 claims isolation tests; could not locate a dedicated one — UNKNOWN) |
| Personal OS CRUD | `tests/unit/test_personal_os_records.py` | IMPLEMENTED — NOT FULLY VERIFIED |
| Fine-tuning weight change | `tests/training/` opt-in, never in CI; `tests/unit/test_finetune_honesty.py` | IMPLEMENTED BUT UNREACHABLE by any automated gate |
| Evaluation CI enforcement | `eval-ci.yml` + `evaluation/gate.py` + `test_evaluation_gate.py` | IMPLEMENTED — NOT FULLY VERIFIED (no recorded run; not a required check) |
| Vision | `tests/security/test_image_turns.py`, `tests/unit/test_vision_eval.py` | harness VERIFIED (unit); model quality UNKNOWN |
| Inference concurrency/latency | `tests/unit/test_llm_admission.py`; capacity matrix opt-in, skips without `AURA_LOADTEST_ARGS` | admission TESTED; capacity figures DOCUMENTED ONLY (`phase-a4-load-test.md`) |
| Client→backend reachability | `tests/architecture/test_api_reachability.py` | VERIFIED (fitness test in default run) |
| Deployment boundaries | `tests/deployment/test_inference_contract.py`, `tests/unit/test_release_identity.py`, compose fitness tests | IMPLEMENTED — NOT FULLY VERIFIED (compose/Railway state unprobed) |
| Desktop IDE | `aura_client/test/desktop/*`, `core/hostd/*`, hostd suite | IMPLEMENTED — NOT FULLY VERIFIED (Windows CI advisory only; Linux hostd hangs) |

## Findings

| Local ID | Severity | Title | Subsystem | Evidence (path:line / command) | Current behavior | Expected behavior | Recommended action | Confidence |
|---|---|---|---|---|---|---|---|---|
| G-01 | P1 | No test gate blocks a production deploy | process/ci | `eval-ci.yml:17-20`; `backend-ci.yml` (no protection setting possible in-file); `BACKEND_STATUS.md:19-22`; CB-44, GAP-005 (`gap_register.yaml:132-158`, `status` not proven); CLAUDE.md "Railway auto-deploys every main push" | Railway ships every `main` push; Actions assigns no runner; `main` unprotected, so even when CI runs it is advisory | Required status checks (Backend CI test/pip-audit/docker-build, Eval gate, Repo Hygiene) on a protected `main`; Railway "Wait for CI" | Owner: restore Actions, protect `main`, gate Railway; record both in `docs/reports/` | High |
| G-02 | P1 | Every AI-quality gate is scripted; no real-model evaluation exists or is scheduled | evaluation | `evaluation/gate.py:11-23`; baselines (`agent_tasks` 1.0/1.0/1.0 by construction, `test_agent_tasks_gate.py:5-11`); `docs_corpus` hash embedder (`test_docs_corpus_gate.py:7-15`); GAP-021 `status: open` (`gap_register.yaml:585-600`); `dispatch-run` manual only (`eval-ci.yml:144-146`) with `EchoProvider` (gate refuses anything else, `gate.py:141-157`) | Gates detect plumbing regressions only; production embedder/LLM quality has never been measured; the "100 % task completion" number is tautological | A scheduled real-model eval (production embedder + hosted/local model) producing recall@10, groundedness, citation precision per PRODUCTION_SLO, reported in `docs/reports/` | Add a `schedule:`/dispatch job that runs `evaluation/runner.py` and `vision_eval.py` against the dev stack with `AURA_LLM_PROVIDERS` set, outside `gate.py`'s determinism check; commit the first report; then gate on it (fits ADR-003/030: local-first, hosted only with the flag) | High |
| G-03 | P2 | Windows coverage for the "Windows-first" desktop targets is advisory only | ci/desktop | `client-ci.yml:120` and `hostd-ci.yml:73` `continue-on-error: true`; `DESKTOP_APP_STATUS.md:47,55` (10 client + 5 hostd Windows failures) | Known Windows failures (8.3 short paths, errno 32 file locks) cannot turn a run red | Windows jobs gating once green, with the path-canonicalisation fixes verified | Fix `path_containment_test.dart` / hostd 8.3 handling, then drop `continue-on-error` | High |
| G-04 | P2 | Host Daemon CI hangs on Linux; the fix has never been CI-run | ci/hostd | `hostd-ci.yml:30-33` (15-min timeout comment); `DESKTOP_APP_STATUS.md:16,56,70`; commit `4b1968e` | Linux job passes 220 tests then hangs until timeout → red; fix merged 2026-10-28 with no runner since | Green Linux hostd run | Re-run on `main` once runners return; if still hanging, isolate `posix_pty_session_test.dart` behind a tag | Med |
| G-05 | P2 | Coverage floor covers `aura_core` only; the legacy/platform tree and the four Docker-dependent legacy tests have no coverage or local path | ci/backend | `backend-ci.yml:94` (`--cov=aura_core`); no `.coveragerc`/`pyproject` coverage config (ls/grep); legacy `skipUnless(_docker_available())` (`test_erasure_integration.py:31` etc.) | `api/v2`, `services/`, `infrastructure/` (ADR-002 permanent adapters incl. JWT/sync) are outside the ratchet; their PG tests skip anywhere without Docker | Coverage measured over the whole shipped tree, or an explicit second floor for the legacy tree | Add `--cov=api --cov=services --cov=infrastructure` reporting (no floor at first), and move the four testcontainers tests to `tests/load/*_postgres.py` so the CI `postgres` job runs them without Docker-in-Docker | Med |
| G-06 | P3 | Dev/test tooling unpinned (GAP-033 still open) and no flake/parallel tooling | supply-chain/test | `requirements-dev.txt` (`pytest>=8.0`, `pytest-asyncio>=0.23`, `pytest-cov>=5.0`, `testcontainers[postgres]>=4.0.0`); grep for `xdist|hypothesis|flaky|mutmut` → none | 4305 tests run serially under whatever pytest resolves that day; no property-based or mutation testing anywhere | `requirements-dev.lock` with hashes; optional `pytest-xdist` for the 45-min job | Generate and consume `requirements-dev.lock` in all three backend workflows | High |
| G-07 | P3 | Local verification environment diverges from the locked one | process/testing | EXECUTED-HERE: venv Python 3.14.4, `fastapi 0.136.3`, no `prometheus_client`; lock pins `fastapi==0.141.1`, `prometheus-client==0.26.0`; `--collect-only` → `tests/security/test_route_auth.py:125 AttributeError: module 'fastapi.routing' has no attribute 'iter_route_contexts'` | Local "tested, not CI-run" claims in `docs/status/*` are produced on an interpreter/deps CI does not use; the route-auth suite (the one that fails on any unprotected route) cannot even collect here | Local runs made from the lock on Python 3.11 | Document a `uv venv --python 3.11` + lock install as the only sanctioned local path; add a `conftest.py` assertion that warns when `fastapi.__version__` differs from the lock | High |
| G-08 | P3 | Certification, training, benchmark and non-Postgres load suites have never produced a passing artefact | evaluation/certification | `AURA_CERTIFICATION_EVIDENCE_MATRIX.md:3,7` (NOT PRODUCTION CERTIFIED, 2026-09-28); `tests/certification/conftest.py:19-20` ("a skip is not a pass"); GAP-017; `phase-a4-load-test.md` (Ollama CPU, 2026-09) | Suites exist and are honest about skipping; nothing has run them against the Railway deployment that is now production | One `-m cert` run against a synthetic-user pair on the dev/prod stack; one `-m training` run; refreshed load matrix on the current inference plane | Owner-scheduled; results committed under `docs/reports/` and the evidence matrix updated (its consistency test `test_certification_gate.py` will enforce the wording) | High |
| G-09 | P4 | Vision eval harness has no threshold, no baseline, no report | evaluation/vision | `evaluation/vision_eval.py:36-38`; `ls docs/reports` → no `vision-eval-*` | ADR-031 Phase 7 exit criterion ("a vision eval report with a pinned model") unmet | First measured run and an owner-set `--min-accuracy` | Run `--target space`/`gateway` once hosted vision is reachable; commit report | High |
| G-10 | P4 | Admin console has no lint gate and ~6/10 pages covered by e2e | ci/admin | `admin-ci.yml:13-15`; `WEB_CONSOLE_STATUS.md:52-53`; `package.json` `lint: next lint` with no ESLint config | Lint deliberately skipped; four pages untested | ESLint config committed and `npm run lint` in CI; one spec per page | Follow GAP-007 | High |

## Previous-finding recheck

| Prior item | Status now | Evidence |
|---|---|---|
| `AURA_FULL_SYSTEM_REVIEW.md` §31 "2707 passed" | superseded | 4305 collected now (EXECUTED-HERE); status doc reports 3943 passed locally at `2337577` |
| §31 "CI merge gates: none effective" | still open | G-01 |
| §31 Admin Playwright never PR-triggered | partially fixed | PR trigger added `5465a2a`; never executed (CB-44) |
| §32 all metrics scripted / real model "No" | still open | G-02; GAP-021 `open` |
| §32 `agent_runs` gated "UNKNOWN" | fixed-verified (code) | `gate.py:224-227`, `tests/e2e/test_agent_runs_gate.py` |
| §32 Vision "No" | partially fixed | harness + unit self-test exist; no model run (G-09) |
| GAP-006 eval CI dispatch-only | fixed-in-source-not-operationally-verified | `eval-ci.yml:26-72` path-filtered PR trigger; no recorded run; not required |
| GAP-007 Admin CI | fixed-in-source-not-operationally-verified | `admin-ci.yml:17-26` |
| GAP-010 tracked binaries / stray tests | fixed-in-source-not-operationally-verified | `test_repo_hygiene.py`, `repo-hygiene.yml`; `aura_backend/test_personal_os2.py` still at root (inventory) |
| GAP-033 dev deps unpinned | still open | `requirements-dev.txt` ranges |
| GAP-043 / CB-44 runners | regressed | brief; `BACKEND_STATUS.md:19` |
| GAP-048 security suite gating every PR | still open (built, not gating) | 272 collected + route-auth; no protection, no runner |
| GAP-017 LoRA weight-change proof | cannot verify | opt-in, model download required |
| F-37 coverage ratchet 82 % | fixed-in-source-not-operationally-verified | `backend-ci.yml:91-94` |
| CB-30 admin e2e one failing spec | cannot verify | no Node run attempted (read-only pass, C: full) |
| DESKTOP_APP_STATUS hostd PTY hang | still open | G-04 |

## Verification log

| Command (from `aura_backend` unless noted) | Result |
|---|---|
| `venv/Scripts/python -m pytest --collect-only -q -p no:cacheprovider` → `/e/aura_audit_tmp/collect.log` | exit 2; 4305 tests in 215 files; 1 collection error (`tests/security/test_route_auth.py:125`, `fastapi.routing.iter_route_contexts` missing in local fastapi 0.136.3) |
| `venv/Scripts/python -c "import fastapi,sys; print(fastapi.__version__, sys.version)"` | `0.136.3 3.14.4` |
| `venv/Scripts/python -c "import prometheus_client"` | `ModuleNotFoundError` |
| `grep -E '^(fastapi|prometheus-client|starlette)==' requirements.lock` | `fastapi==0.141.1`, `prometheus-client==0.26.0`, `starlette==1.6.0` |
| `venv/Scripts/python -m pytest tests/architecture tests/e2e tests/deployment -q -p no:cacheprovider` → `/e/aura_audit_tmp/run_arch_e2e.log` | **See "Executed results" below** (run was still in progress when this report was written; lead records the outcome) |
| `flutter test` (Flutter 3.44.6 at `E:\Flutter\flutter`) in `aura_client` | NOT RUNNABLE: `pub get` succeeded, test loading stalled; the Dart runner writes kernel snapshots to `C:\Users\HP\AppData\Local\Temp`, which is full |
| `dart test` in `aura_hostd` | NOT RUNNABLE for a verdict: `Failed to load "test\task_handler_test.dart" … There is not enough space on the disk, errno = 112` (and `workspace_store_test.dart`); partial counter `+113 -3` is contaminated by those load failures and is not cited as a result |
| `find tests -name "test_*.py" -o -name "bench_*.py"` | 246 files (incl. 22 load, 4 cert, 2 bench, 1 training) |
| `cat test_*.py \| grep -c "    def test_"` | 133 legacy test methods in 18 files |
| `git log -5 -- .github/workflows` | last change `3349703` 2026-10-31 |
| Not run: `unittest discover` (Docker-dependent cases would skip; adds nothing the status doc lacks), `tests/load`, `tests/benchmarks`, `tests/training`, `tests/certification` (infra/model/credentials), any GitHub API call (no network by instruction), `npm run test:e2e` (Node/Playwright would also spill to `C:`) |

### Executed results (filled by lead)

Filled by the lead on 2026-10-31 from `E:\aura_audit_tmp\pytest_<dir>.log`, `backend_unittest.log`, `hostd_test.log` and the Flutter logs; full detail, failure classification and environment limits in `verification_log.md` §3.

| Suite | Command | Count (collected) | Run here? | Result | Env/deps | Skips | Blocks merge/deploy? | Notes |
|---|---|---:|---|---|---|---|---|---|
| Backend `tests/unit` | `venv/Scripts/python -m pytest tests/unit -q -p no:cacheprovider` | 3,606 | yes | 3,529 passed / **6 failed** / 71 skipped (16:04) | none | 71 | No | 2 adaptation-git goldens "stale", 1 askpass POSIX-mode assert on NTFS, 1 `UnicodeDecodeError` (YAML opened without `encoding`), 2 workspace admission `DID NOT RAISE` — Windows effects/suspects |
| Backend `tests/security` | same, `--ignore=tests/security/test_route_auth.py` | 273 | yes (1 module uncollectable) | 271 passed / **1 failed** | none | 0 | No | failure + ignored module = FastAPI 0.136 vs lock 0.141 (`iter_route_contexts`); **route-auth and IDOR sweeps did not run** |
| Backend `tests/architecture` | same (pass A) | 347 | yes | 339 passed / **8 failed** | none | 0 | No | 6 FastAPI drift, 2 untracked local secret files matched by the scan |
| Backend `tests/e2e` | same | 44 | yes | 43 passed / **1 failed** | none | 0 | No | `prometheus_client` missing in venv |
| Backend `tests/integration` | same | 33 | yes | 33 passed | none | 0 | No | |
| Backend `tests/deployment` | same | 4 | yes | 4 passed | none | 0 | No | |
| Backend `tests/certification` | same | 69 | no | 69 deselected (`-m cert`) | live target | — | No | NOT RUN: writes to a live stack |
| Backend `tests/training` | same | 1 | no | 1 deselected | train deps/GPU | — | No | NOT RUN |
| Backend `tests/load` / `tests/benchmarks` | `-m load` / `-m bench` | 22 / 2 | no | NOT RUN | Postgres/Qdrant/Redis/model | — | load `*_postgres.py` only in the CI postgres job | |
| Legacy `unittest` | `TESTING=True … -m unittest discover -p "test_*.py"` | 135 | yes | Ran 135, **2 errors**, 37 skipped | Docker for 4 files | 37 | No | both errors are import failures of pytest modules under `tests/` (same venv drift); legacy methods 0 failures |
| Client `flutter analyze` | `flutter analyze` | — | yes | **No issues found** (158.6 s) | Flutter 3.44.6 | — | CI `analyze-and-test` (not required) | |
| Client `flutter test` | `flutter test` | 76 files | no | **NOT RUNNABLE** (kernel snapshots to full `C:`; stalled at 19/1 after 7 h) | Flutter 3.44.6 | — | CI (not required) | status docs: 381 (CI run 143), 529 locally 2026-10-31 |
| hostd `dart analyze --fatal-infos` | | — | yes | No issues found | Dart 3.12.2 | — | hostd CI (Windows advisory) | |
| hostd `dart test` | `dart test --reporter expanded` | 10 files | yes | **219 passed / 10 failed** (Windows) | git, debugpy, temp dirs | — | Windows job `continue-on-error` | ≥4 failures are disk-full temp-dir effects; debugpy absent; `task_handler` approval and `baseBranch` cases need a clean rerun |
| Admin `tsc` / `next build` / Playwright | `npm run test:e2e` | 5 specs | no | NOT RUN (Chromium download / `C:` writes) | node 24 | — | Admin CI never ran | status doc: 10 passed locally 2026-10-30 |
| `eval-ci.yml` gate | workflow | 4 gates | no | NOT RUN (CI only; runners dead) | echo/scripted providers, hash embedder | — | not required | deterministic only (G-02) |

The empty template rows that follow are superseded by the table above.

| Suite | Command | Passed | Failed | Skipped | Errors | Notes |
|---|---|---:|---:|---:|---:|---|
| Backend default `pytest` | `venv/Scripts/python -m pytest -q -p no:cacheprovider` | | | | | collection error in `test_route_auth.py` expected on this venv (G-07) |
| Backend `tests/architecture tests/e2e tests/deployment` | as above | | | | | log at `E:\aura_audit_tmp\run_arch_e2e.log` |
| Legacy `unittest` | `TESTING=True venv/Scripts/python -m unittest discover -p "test_*.py"` | | | | | |
| Flutter client | `flutter test` | | | | | blocked by full `C:` at audit time |
| Host daemon | `dart test` | | | | | blocked by full `C:` at audit time |

## Documentation inaccuracies

| Doc claim | Code / observed | Citations |
|---|---|---|
| `docs/guides/developer-guide.md:15` — "`pytest` # 1010 tests, ~7 seconds" | 4305 tests collected; status doc reports 3943 passed | EXECUTED-HERE collect-only; `BACKEND_STATUS.md:22` |
| `docs/guides/developer-guide.md:205` and `README.md:181` — "Coverage is 90 % of `aura_core`" | CI ratchet set at 82 % from a measured 83.4 % on 2026-10-30 | `backend-ci.yml:91-94` |
| `README.md:180` — "Tests 1284+, ~7 s" | see above | — |
| `.github/workflows/client-ci.yml:3-5` — "full test suite (32 tests as of Phase 4…)" | 76 test files; 381 passed on the last CI run | `MOBILE_APP_STATUS.md:19` |
| `AURA_FULL_SYSTEM_REVIEW.md:98` — "155 `test_*.py` under `tests/`" | 246 (incl. bench) at this baseline | `find` count |
| `gap_register.yaml:160-166` (GAP-006 `current_state`) — eval-ci "workflow_dispatch-only" | PR/push triggers exist since the reverification text; `current_state` block itself is STALE while the reverification note is right | `eval-ci.yml:26-72` |
| `docs/status/*` "TESTED locally" rows | produced on CPython 3.14 / fastapi 0.136.3, not the lock | G-07 |
| `CLAUDE.md` — "Both suites … run in CI and both must pass" | True of the workflow file; false operationally since ~2026-10-28 (no runner) and never enforced (no protection) | G-01 |
