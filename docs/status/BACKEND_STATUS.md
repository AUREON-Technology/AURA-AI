# Backend status

**As of 2026-10-28 (evening), `main` at `2337577`.** Covers `aura_backend/` and the deploy stack under
`deploy/`. Evidence scale and refresh steps: [README.md](README.md).

## Summary

**Production is up but not ready.** Railway serves current `main` (`2337577`), and its database is
two migrations behind (`0013`; the code expects `0015`). The ORM maps columns those migrations add,
so sign-in and sync very likely fail for every user until `alembic upgrade head` runs against the
production database (CB-43). The migrations themselves apply cleanly on Postgres 16.

**CI stopped executing again** around 19:50 UTC (no runner assigned; CB-44). The last Backend CI
that ran was green at `dc87beb` (3848 passed, 56 skipped; unittest 100 OK; pip-audit; docker-build).
Four PRs merged after that — #187, #185, #188, #143 — and Railway deployed them without CI.
A local run on `2337577` in this session: pytest 3943 passed, 56 skipped, 0 failed (3999 collected, including the 4 audience tests ported here); legacy `unittest` 106 run, OK (10 skipped); local Postgres 16 `alembic upgrade 0013 → head` clean. That is local evidence, not CI.

Providers: `hosted1` healthy, `hfspace` 503, no Ollama. The Contabo VPS has not taken an automated
deploy in about six weeks (SSH host key changed).

Under the repository's evidence rule, almost nothing is **proven**. The only `proven` gap is
GAP-043 (CI runners restored) — and that has now regressed. The 2026-10-26 audit raised 38
findings, 18 High; 13 now have fixes on `main` (listed below). Only the four from #186 have been through CI.

## Where it runs

| Target | State (checked 2026-10-28) |
| --- | --- |
| **Railway control plane** (`aureon-ai.tech`) | Serves `2337577` (deployment `ae2746c4…`); `environment: production`; **`/health/ready` unhealthy: schema `0013`, expected `0015`**; blueprint v2 active. Providers: `hosted1` healthy, `hfspace` `HTTP 503`. Railway's GitHub auto-deploy ships every `main` push whether or not CI ran (Deploy Control Plane failed instantly for the last three merges, yet they are live). |
| **Hugging Face GPU Space** (`hfspace`) | Down or asleep (503 from `/health/ready`) |
| **Contabo VPS** (169.58.188.43, `/opt/aura-stack`) | Stale and unreachable for CI. Deploy VPS has failed on every run since at least 2026-09-27. Since 2026-10-18 the failure is `Host key verification failed`; the new key is ED25519 `SHA256:6dFAzu5+m0A5GSFG5QRt/rNSyZ2JleERZNeRoBWLk2g`. What it runs now is unknown. |
| **Dev stack** (`dev.aureon-ai.tech`) | DNS no longer resolves |

## Capabilities

| Area | Status | Evidence |
| --- | --- | --- |
| Backend CI gate (test, pip-audit, docker-build) | PROVEN at `dc87beb`; **not executing since** | GAP-043; run 40719101969 green on `dc87beb`; runs #392–#397 died in 2–4 s with no runner (CB-44) |
| Hexagonal layering fitness tests | TESTED | `tests/architecture/`; F-28: narrower than CLAUDE.md claims (misses absolute and vendor-SDK imports) |
| Self-hosting invariant / ADR-030 host categories | TESTED | `tests/architecture/test_no_external_inference.py` |
| AIKernel plan → execute → generate | TESTED (scripted/echo providers) | GAP-021 open; F-24: planner failure silently becomes "answered directly" |
| Tool confirmation / taint (ADR-006) | BUILT (fix merged, not CI-run) | F-04 fixed (`d6ced5a`): single-use `approval_id` bound to stored turn + call digest; GAP-014 |
| ProviderRouter, breakers, failover | TESTED; F-19 fix not CI-run | F-19 fixed (`42af1be`): `release_probe()` frees the half-open slot |
| Self-hosted LLM (qwen2.5:3b, CPU Ollama) | TESTED in a sandbox only | A4 sandbox p50 56 s / p95 71 s (`docs/reports/phase-a4-load-test.md`); not on the current production target |
| Hosted open-weight inference, ADR-030 Phases 1–5 | TESTED | GAP-064; inert unless `AURA_ALLOW_HOSTED_INFERENCE=true` |
| ADR-030 Phase 6, live | BUILT (`built_not_proven`) | In-process run against live OpenRouter (`nvidia/nemotron-3-ultra-550b-a55b:free`, `ALLOW_PROMPT_TRAINING=true`): 9/10 checks pass, the 429 burst was skipped (`docs/reports/phase-6-hosted-inference-openrouter-inprocess-20261028.txt`). The Verify Hosted Inference workflow has never run. |
| Hybrid retrieval / knowledge | TESTED (hash embedder) | A3 recall@5 0.65; F-14 fixed (`c415a52`, not CI-run) |
| Memory engine | TESTED | F-15 fixed (`19244e6`); F-16 write races open |
| v2 sync protocol | BUILT (fixes merged, not CI-run); **broken live until 0014 applies** | F-11 (`31608f9`), F-12 (`19c64f9`, migration `0014`), F-13 (`c5b1924`), F-07 (`98143ff`) |
| Auth / RBAC / JWT | BUILT (fixes merged, not CI-run); **at risk live until 0015 applies** | F-02: refresh re-resolves role (`2d42388`) and Access-page assignments now take effect (`3e4e0f5`, migration `0015`); F-03 (`a3fa5c2`: dev keys, `iss`/`aud`); F-10 (`039865e`: no JWT secret → no start in production). Demotion still revokes no sessions. |
| Security regression suite (P1.5) | TESTED | GAP-048 `built_not_proven` |
| Engineer workspaces (ADR-013/014) | BUILT | GAP-025; off by default; no `verify-engineer-workspaces` report |
| Desktop hostd bridge | BUILT | GAP-026; F-21 (confirmed): non-streaming `run` returns `''` / `-1` |
| Observability | OPEN | GAP-036, GAP-037 |
| Backup / DR | TESTED in a sandbox; OPEN on any live target | A2 sandbox (`phase-a2-restore-drill.md`); GAP-028, GAP-046 |
| 50-user capacity | OPEN | GAP-011 (P0), GAP-041 |
| Real-model evaluation gates | OPEN | GAP-021; GAP-006 (`eval-ci.yml` is dispatch-only) |
| Schema / migrations | TESTED (local PG16, `0013`→`0015`); **production at `0013`** | Alembic `0001`→`0015`, single head; F-27: 16 runtime-DDL tables exist in no migration |
| Learning / fine-tuning | BUILT | GAP-017; GAP-018 (a failed train is recorded as success) |
| Voice | BUILT | GAP-020: speech-to-text is English-only while an Arabic voice ships |
| Track AP: AP1–AP8 | TESTED (`built_not_proven`) | GAP-049…057; held at BUILT until M0 |
| Track AP: AP10 | In progress | GAP-059 |
| Track AP: AP9, AP11–AP13 | OPEN | GAP-058, GAP-060…062 |
| Flame integration AR-1…AR-8 | TESTED; one live Railway run | GAP-063; Link 2 passed only under a gate-coverage waiver (0.35); Link 1 never run |

## Open issues (most severe first)

**Operational**
1. **Production DB schema behind the code (CB-43).** Run `alembic upgrade head` on the Railway
   database and find out why the pre-deploy migration did not.
2. **CI not executing (CB-44)**, and Railway deploys without it. Restore Actions; gate Railway on
   CI (turn off its GitHub auto-deploy or enable "Wait for CI").
3. **The VPS deploy path is dead** (host-key change; CB-03). The owner must confirm whether the
   server was reinstalled before anyone updates `deploy/known_hosts`, and decide whether the VPS
   is still a target now that the domain is on Railway.
4. **`main` is unprotected** (GAP-005 / CB-02).
5. **`hfspace` is down (503).** Model capacity is whatever `hosted1` provides.

**Fixed in code on `main` since the 2026-10-26 audit** (TESTED only where noted; everything from
#187 onward has had no CI run)
- F-15 (`19244e6`), F-05 (`5153394`), F-01 daemon half (`627a03d`), F-02 refresh (`2d42388`) — CI
  green at `dc87beb`.
- #187: F-10 (JWT secret required in production; streamed request-body limit, `039865e`), insights
  no longer delete the daily briefing (`e92edc1`).
- #185: F-03, F-04, F-19, F-14, F-11, F-12, F-13, F-07; Android disk, Windows 8.3-path and hostd
  PTY CI fixes; `openrouter-free` preset for Verify Hosted Inference.
- #188: F-02 second half — Access-page role assignments take effect (`users.role_assigned_*`).
- Ported in this change: an audience test for desktop login
  (`tests/unit/test_desktop_oauth_audience.py`, from closed PR #78; mutation-checked).

**Still open (High)**
- **F-01b / CB-05:** server-side `workspace_local` taint exemption (`tools/policy.py:114-115`).
- **F-21:** hostd non-streaming `run` returns nothing.
- **F-20:** Celery reuses one asyncpg pool across event loops.
- **F-06:** admin erasure reports success while incomplete.
- **F-22:** timeouts disagree between layers.
- **F-27:** 16 tables have no migration.
- **F-02 remainder:** demotion revokes no live sessions.
- **GAP-011, GAP-041 (P0):** inference is single-slot and no load matrix exists.

**Medium:** F-08 (GitHub OAuth CSRF), F-09, F-16–F-18, F-23–F-26, F-28, F-30, F-33–F-38.

**Conditional:** SEC-08 / F-38 becomes Critical if Firebase email/password sign-in is enabled.
That needs a check in the Firebase console.

## Changed since 2026-10-20

- `e7e0db1`: full-system audit at `699f27b`, with a remediation roadmap.
- `3090b8f`: OpenRouter free models behind the per-slot `ALLOW_PROMPT_TRAINING` consent.
- `a70579a`: seven dependency bumps for new advisories.
- `627a03d`, `2d42388`, `19244e6`, `5153394` (#186): audit batch 1.
- #187, #185, #188, #143, #165: listed above; #143 is client-only (failure classifier on every
  `AsyncValue.error`), #165 docs-only.
- PRs #78 and #88 closed as superseded (both features are on `main` in reworked form).

## Next steps

1. **Owner, now:** migrate the production database (CB-43) and confirm sign-in works.
2. **Owner:** restore Actions (CB-44); re-run Backend CI on `main`; gate Railway deploys on CI.
3. Enable branch protection with required checks (GAP-005 / CB-02).
4. Finish R0 security: F-01b (routing-aware `workspace_local` exemption), F-02 session revocation
   on demotion.
5. Fix the remaining data-loss and lockout defects: F-20, F-21, F-06.
6. **ADR-030 Phase 6:** owner adds the `AURA_HOSTED1_API_KEY` secret, then runs Verify Hosted
   Inference with the `openrouter-free` preset and commits the report (GAP-064).
7. Fix the F-22 timeout budgets and add metrics (GAP-036), then run the M5 load matrix (GAP-041).
8. **Owner:** decide the VPS's future (CB-03).

## Unverified

- What the VPS runs, and whether its host-key change was legitimate.
- Which provider and model `hosted1` is on Railway (the health endpoint does not say).
- The "5–10 tok/s" figure for qwen2.5:3b. No measurement on a live target is committed.
- F-20 at runtime (needs worker logs). F-03 secret paths on the VPS. The Firebase providers
  (SEC-08).
- Whether sign-in actually fails live (CB-43 impact is inferred from the ORM mapping; no real
  account was used to probe it).
- Why Railway's pre-deploy migration left the database at `0013`.
