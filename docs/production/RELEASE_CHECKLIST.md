# Release Checklist

Pre-release gates, each tied to a real workflow in `.github/workflows/` or a named script or
report. A box may only be ticked with a link to the run or report that proves it: "built" and
"tested" are not "proven". Part of [MASTER_PRODUCTION_ROADMAP.md](MASTER_PRODUCTION_ROADMAP.md)
Phases 23–24. It complements the launch-day walkthrough in
[`docs/guides/launch-checklist.md`](../guides/launch-checklist.md).

> **Blocking precondition (2026-09-24):** GitHub Actions jobs on this repository currently die
> in about 3 s with no runner and no steps (GAP-043; likely an account-level Actions or billing
> block). Until that's fixed, **none of the CI gates below can be satisfied**, and `Deploy VPS`
> can't fire, because it only runs after a successful Backend CI. A local `pytest` run is not a
> substitute for a CI gate on this list.

## 0. Repository preconditions

- [ ] GitHub Actions executes jobs: a linked Backend CI run on `main` with every step run (GAP-043)
- [ ] `main` is branch-protected, requiring the checks in §1 (GAP-005)
- [ ] The release commit reached `main` through a reviewed PR, not a direct push
- [ ] No tracked binaries or logs (GAP-010)

## 1. CI gates (must be green on the release SHA)

| Gate | Workflow / job | Trigger today | Link to run |
| --- | --- | --- | --- |
| [ ] Syntax check, `aura_core` pytest with coverage, architecture fitness functions, legacy unittest | `backend-ci.yml` → `test` (lines 32-90) | PR/push touching `aura_backend/**`, `deploy/**` | |
| [ ] Dependency CVE audit of `requirements.lock` (fails on unauditable pins too) | `backend-ci.yml` → `pip-audit` (92-129) | same | |
| [ ] Gateway image **and** sandbox image build | `backend-ci.yml` → `docker-build` (131-153) | same | |
| [ ] `flutter analyze` (info-level fatal) + `flutter test` | `client-ci.yml` → `analyze-and-test` | PR/push touching `aura_client/**` | |
| [ ] Android / Windows / Linux builds | `client-ci.yml` → `build-android`, `build-windows`, `build-linux` | same | |
| [ ] `dart analyze --fatal-infos` + `dart test` | `hostd-ci.yml` → `analyze-and-test` | PR/push touching `aura_hostd/**` | |
| [ ] Admin lint, build and Playwright e2e | `admin-ci.yml` → `admin-ci` | **only** after `VPS Dev Ops` or on manual dispatch. It doesn't run on PRs (GAP-007) | |
| [ ] Agent / retrieval evaluation with `RegressionGate` | `eval-ci.yml` → `eval` | **manual only** (`workflow_dispatch`, GAP-006). The deterministic docs-corpus gates run inside `backend-ci` pytest | |

## 2. Quality gates

- [ ] `RegressionGate` passes against the stored baselines (`aura_backend/evaluation/suites/*_baseline.json`)
- [ ] Real-model evaluation (production LLM + embedder) meets the PRODUCTION_SLO Q1–Q3 proposals, or the release notes explicitly say it doesn't (GAP-021)
- [ ] Guard false-positive check on real model output (GAP-022)
- [ ] Any model change satisfies the MODEL_REGISTRY admission rule: pinned revision, license, measured latency and eval

## 3. Performance and capacity gates

- [ ] Load run (`pytest tests/load -m load -s`, opt-in) on the release SHA against the production topology. The report goes in `docs/reports/` (GAP-041)
- [ ] TTFT and answer p95 compared against the previous release. No regression beyond tolerance
- [ ] `aura_turn_duration_seconds` buckets cover the observed p95 (GAP-037)

## 4. Security gates

- [ ] `pip-audit` green (§1)
- [ ] Self-hosting fitness tests green (`tests/architecture/test_no_external_inference.py`, part of §1)
- [ ] Compose parity: socket-proxy allowlists and `internal:` flags are identical across prod and dev. Only `EXEC: "0"` in both files is test-pinned (`tests/unit/test_sandbox_exec_surface.py:116`, part of §1). The rest of the parity check is a manual diff until a fitness test exists
- [ ] `AURA_ALLOW_REMOTE_INFERENCE` is unset in production `.env` (GAP-034)
- [ ] `AuraInsecureModeActive` isn't firing after deploy
- [ ] Any new image reference is digest-pinned (GAP-032)

## 5. Data and recovery gates

- [ ] A backup completed within the last 24 h (`aura_backup_last_success_timestamp_seconds`) and the offsite copy succeeded (`aura_backup_offsite_last_exit_code == 0`)
- [ ] `deploy/scripts/pre-migration-backup.sh` ran if the release includes an Alembic migration
- [ ] `deploy/scripts/restore-drill.sh <latest dump>` passes (throwaway Postgres)
- [ ] A migration rollback path (downgrade or restore) is written in the PR

## 6. Deploy and verify

- [ ] `Deploy VPS` (`deploy-backend.yml`) completed for the release SHA (it fires via `workflow_run` after Backend CI on `main`)
- [ ] `/health` and `/health/ready` are OK on the public domain
- [ ] Grafana shows no new `AuraHighErrorRate`, `AuraAllProvidersUnhealthy` or `AuraRetrievalReturningNothing` alerts for 30 min after deploy
- [ ] The dev stack (`aura-dev`) was restarted separately if needed (`vps-update.sh` touches only `aura`)
- [ ] Engineer-workspace changes: `deploy/scripts/verify-engineer-workspaces.sh` report committed (GAP-025)
- [ ] Client release: `release-apk.yml` (manual) built with the production `BACKEND_URL`, signed

## 7. Canary and rollback (from Phase 24)

- [ ] Canary cohort defined (users or percentage) and the new model/provider enabled only for it through router config
- [ ] Rollback rehearsed: redeploy the previous SHA via `Deploy VPS` `workflow_dispatch` with `sha`, revert the provider list, roll the model registry back
- [ ] Post-release SLO review scheduled at 24 h and 7 d
