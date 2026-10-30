# Backend status

**As of 2026-10-28 (evening), `main` at `2337577`.** Covers `aura_backend/` and the deploy stack under
`deploy/`. Evidence scale and refresh steps: [README.md](README.md).

## Summary

**Production is up and ready (update 2026-10-30).** Railway serves `main` at `6417c43`. On
2026-10-28 its database sat two migrations behind (`0013`, code expected `0015`; CB-43). On
2026-10-30 the owner ran `alembic upgrade head` over `railway ssh` (`0013 → 0016`, clean), and live
`/health/ready` then showed `healthy: true` and `schema.revision 0016`. The pre-deploy step that
should have done this ran only its leading `echo`, because Railway does not evaluate
`preDeployCommand` in a shell. It now calls `scripts/railway-predeploy.sh` (TESTED against a
stand-in `alembic`; not yet seen on a live deploy).

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
| CI hygiene (F-37, 2026-10-30) | BUILT, not CI-run (CB-44) | Every third-party action pinned to a commit SHA; `concurrency` + `timeout-minutes` on the CI workflows; `--cov-fail-under=82` (83.4% measured locally); a `postgres` job running `tests/load/*_postgres.py` against a `postgres:16` service (not run locally: no Docker daemon); `repo-hygiene.yml` (pinned actionlint + tracked-artefact guard, every PR). actionlint 1.7.12 with shellcheck is clean locally. `tests/unit/test_repo_hygiene.py` pins all of it |
| Hexagonal layering fitness tests | TESTED | `tests/architecture/`; F-28: narrower than CLAUDE.md claims (misses absolute and vendor-SDK imports) |
| Self-hosting invariant / ADR-030 host categories | TESTED | `tests/architecture/test_no_external_inference.py` |
| AIKernel plan → execute → generate | TESTED (scripted/echo providers) | GAP-021 open; F-24/CB-14 built (not CI-run): a planner failure is now a degraded turn with a reason, and the stream path records telemetry |
| Email/issue analysis off the event loop (F-23) | BUILT, tested locally, not CI-run | `/api/v3/automation/*` goes through `ProviderRouter` (awaited, admitted, local-only since unclassified) and returns 503 on failure instead of a 200 placeholder; the rate limiter's Redis `INCR` runs in the threadpool; the shared Redis client has a 2 s command timeout |
| Request metrics / outbox gauges (F-24, CB-15) | BUILT, not proven | Per-route HTTP histogram and status counter, buckets to 600 s, outbox backlog gauges and alerts. Needs a scrape on a live target and the M5 load run |
| Tool confirmation / taint (ADR-006) | BUILT (fix merged, not CI-run) | F-04 fixed (`d6ced5a`): single-use `approval_id` bound to stored turn + call digest; GAP-014 |
| ProviderRouter, breakers, failover | TESTED; F-19 fix not CI-run | F-19 fixed (`42af1be`): `release_probe()` frees the half-open slot |
| Self-hosted LLM (qwen2.5:3b, CPU Ollama) | TESTED in a sandbox only | A4 sandbox p50 56 s / p95 71 s (`docs/reports/phase-a4-load-test.md`); not on the current production target |
| Hosted open-weight inference, ADR-030 Phases 1–5 | TESTED | GAP-064; inert unless `AURA_ALLOW_HOSTED_INFERENCE=true` |
| ADR-030 Phase 6, live | BUILT (`built_not_proven`) | In-process run against live OpenRouter (`nvidia/nemotron-3-ultra-550b-a55b:free`, `ALLOW_PROMPT_TRAINING=true`): 9/10 checks pass, the 429 burst was skipped (`docs/reports/phase-6-hosted-inference-openrouter-inprocess-20261028.txt`). The Verify Hosted Inference workflow has never run. |
| Hybrid retrieval / knowledge | TESTED (hash embedder) | A3 recall@5 0.65; F-14 fixed (`c415a52`, not CI-run); F-30 built and benchmarked locally, not CI-run: the threshold no longer passes a rank pseudo-score, and the owner-sharded inverted BM25 brings the lexical stage at 50k chunks from 54.7 to 4.8 ms p50, with identical scores (`docs/reports/LEXICAL_RETRIEVAL_BENCHMARK.md`; the 200k point and Postgres FTS are open) |
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
| Schema / migrations | TESTED (local PG16, `0001`→`0016`); **production at `0013`** | Alembic `0001`→`0016`, single head; F-27 built: `0016` baselines the 16 runtime-only tables, `env.py` keeps autogenerate off `aura_*`, and a fitness test requires a migration for every runtime table |
| Learning / fine-tuning | BUILT | GAP-017; GAP-018 (a failed train is recorded as success) |
| Voice | BUILT | GAP-020: speech-to-text is English-only while an Arabic voice ships |
| Track AP: AP1–AP8 | TESTED (`built_not_proven`) | GAP-049…057; held at BUILT until M0 |
| Track AP: AP10 | In progress | GAP-059 |
| Track AP: AP9, AP11–AP13 | OPEN | GAP-058, GAP-060…062 |
| Flame integration AR-1…AR-8 | TESTED; one live Railway run | GAP-063; Link 2 passed only under a gate-coverage waiver (0.35); Link 1 never run |

## Open issues (most severe first)

**Operational**
1. **Pre-deploy migration fix unproven on a live deploy (CB-43 follow-up).** The database is at
   `0016` and readiness is green. The next deploy that adds a migration must show
   `revision before/after` and `=== MIGRATION OK ===` in its pre-deploy log.
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

- Audit phase R0/R1 remainder (built and tested locally against Postgres 16, not CI-run): F-01b
  (host-routed `workspace_local` side effects in a tainted turn need confirmation; hostd refuses
  `.git/hooks` and `.git/config` writes), F-21 (non-streaming hostd `run` waits for `result`),
  F-20 (one event loop per Celery worker process), F-06 (erasure reaches memories, documents and
  chunks, personal-OS records and transcripts, and deletes the `users` row; it lists what it does
  not reach), F-02 remainder (demotion revokes refresh tokens and paired devices).
- F-30 (built, tested and benchmarked locally, not CI-run). With reranking disabled or a
  reranker that raises, the relevance floor is now gated on heuristic relevance, not on
  `1 - rank/n`, so an unrelated query is `searched_but_empty` (`2307bc4`). `BM25Index` is an
  owner-sharded inverted index with float-identical scores and unchanged A3 metrics. Results and
  the Postgres FTS recommendation are in `docs/reports/LEXICAL_RETRIEVAL_BENCHMARK.md`. F-14's
  cross-worker half (uploads invisible on other workers) is unchanged.

**Still open (High)**
- F-22 and F-27 are built and tested locally, not CI-run:
  - F-22: one configured LLM deadline, an SSE keep-alive every 15 s, and `/api/v3/chat` given
    600 s at nginx.
  - F-27: migration `0016` baselines the 16 runtime-only tables.
- **GAP-011, GAP-041 (P0):** inference is single-slot and no load matrix exists.

**Medium:** F-16–F-18, F-23–F-26, F-28, F-33–F-38 (F-30 built, see above).

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

1. **Owner:** confirm a real sign-in works on `aureon-ai.tech` now that the schema is `0016` (CB-43 migrated 2026-10-30).
2. **Owner:** restore Actions (CB-44); re-run Backend CI on `main`; gate Railway deploys on CI.
3. Enable branch protection with required checks (GAP-005 / CB-02).
4. Get CI to run the R0/R1 remainder (F-01b, F-02 revocation, F-20, F-21, F-06) and record it.
5. CI-run F-08 and F-09 (built and tested locally). For F-08, the GitHub callback now shows a
   confirmation page naming the AURA account, and a GitHub account already linked to another
   user is refused. For F-09, pairing tokens are scoped to the host-agent socket, and open
   sockets re-check their credential every 60 s.
6. **ADR-030 Phase 6:** owner adds the `AURA_HOSTED1_API_KEY` secret, then runs Verify Hosted
   Inference with the `openrouter-free` preset and commits the report (GAP-064).
7. Metrics are built (F-24). Next, confirm that a live `/metrics` scrape shows `aura_http_*`
   and the outbox gauges, then run the M5 load matrix (GAP-041) to measure what the F-22
   budgets assume.
8. **Owner:** decide the VPS's future (CB-03).

## Unverified

- What the VPS runs, and whether its host-key change was legitimate.
- Which provider and model `hosted1` is on Railway (the health endpoint does not say).
- The "5–10 tok/s" figure for qwen2.5:3b. No measurement on a live target is committed.
- F-20 at runtime (needs worker logs). F-03 secret paths on the VPS. The Firebase providers
  (SEC-08).
- Whether sign-in works live after the 2026-10-30 migration (no real account has probed it).
- Whether `scripts/railway-predeploy.sh` runs as intended on Railway. The cause of the old failure is known (no shell, so only the `echo` ran), but the fix has not been through a deploy yet.
