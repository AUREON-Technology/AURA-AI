# Backend status

**As of 2026-10-28 (evening), `main` at `2337577`.** Covers `aura_backend/` and the deploy stack under
`deploy/`. Evidence scale and refresh steps: [README.md](README.md).

**Re-read against commit:** `b2e14c4` (2026-11-08) for every row dated 2026-11-07 or later (their live checks came first; the re-read confirmed that tree holds what they describe); older rows carry the commit they were checked at. Refresh this line whenever a row is re-verified (audit FSA-120).

## Summary

**Update 2026-11-07 (`main` at `772f9ef`, live probe):** production serves `ffbbb9f` and
`/health/ready` reports **`schema.revision 0019`**, so open issue 0 below is resolved live and the
pre-deploy migration path has now (by one route or another) applied real revisions; confirm which in
the Railway deploy log. Still true: `hfspace` 503 and `hosted1` capped at `internal`, the
`hash-embed-v1` index live, `/metrics` and `/docs` readable without a token, `main` unprotected,
Railway auto-deploying, VPS dead. Runners are assigned again (Repo Hygiene, Admin CI executed) but
Backend CI has had no trigger since 2026-10-31. Full check:
`docs/reports/AURA_PRODUCTION_READINESS_2026-11-07.md`.

**Production is up and ready (update 2026-10-30).** Railway serves `main` at `6417c43`. On
2026-10-28 its database sat two migrations behind (`0013`, code expected `0015`; CB-43). On
2026-10-30 the owner ran `alembic upgrade head` over `railway ssh` (`0013 → 0016`, clean), and live
`/health/ready` then showed `healthy: true` and `schema.revision 0016`. The pre-deploy step that
should have done this ran only its leading `echo`, because Railway does not evaluate
`preDeployCommand` in a shell. The service also ignores `railway.json` (no config file linked), so
the fix lives in the Railway dashboard. On 2026-10-31 the owner set the pre-deploy command to
`sh /app/scripts/railway-predeploy.sh`, and it is now **seen running on a live deploy**
(deploy `4afdd9a8` at `9bfaa22`, 2026-10-31 08:40 UTC): `revision before: 0016 (head)`, alembic connected, `revision after: 0016
(head)`, `=== MIGRATION OK ===`, all before the app started.

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
| Hybrid retrieval / knowledge | TESTED (hash embedder) | A3 recall@5 0.65; F-14 fixed: read-time drop (`c415a52`) and cross-worker convergence via per-owner corpus revisions (migration 0017), neither CI-run; F-30 built and benchmarked locally, not CI-run: the threshold no longer passes a rank pseudo-score, and the owner-sharded inverted BM25 brings the lexical stage at 50k chunks from 54.7 to 4.8 ms p50, with identical scores (`docs/reports/LEXICAL_RETRIEVAL_BENCHMARK.md`; the 200k point and Postgres FTS are open) |
| Memory engine | TESTED | F-15 fixed (`19244e6`); F-16 write races fixed (`8b34504`, audit FSA-120 correction 2026-11-08) |
| v2 sync protocol | BUILT (fixes merged, not CI-run); **broken live until 0014 applies** | F-11 (`31608f9`), F-12 (`19c64f9`, migration `0014`), F-13 (`c5b1924`), F-07 (`98143ff`) |
| Auth / RBAC / JWT | BUILT (fixes merged, not CI-run); **at risk live until 0015 applies** | F-02: refresh re-resolves role (`2d42388`) and Access-page assignments now take effect (`3e4e0f5`, migration `0015`); F-03 (`a3fa5c2`: dev keys, `iss`/`aud`); F-10 (`039865e`: no JWT secret → no start in production). Demotion revokes refresh tokens and paired devices (`admin_platform.py`; FSA-120 correction 2026-11-08). |
| Security regression suite (P1.5) | TESTED | GAP-048 `built_not_proven` |
| Engineer workspaces (ADR-013/014) | BUILT | GAP-025; off by default; no `verify-engineer-workspaces` report |
| Desktop hostd bridge | BUILT | GAP-026; F-21 (confirmed): non-streaming `run` returns `''` / `-1` |
| Observability | OPEN | GAP-036, GAP-037 |
| Backup / DR | TESTED in a sandbox; OPEN on any live target | A2 sandbox (`phase-a2-restore-drill.md`); GAP-028, GAP-046 |
| 50-user capacity | OPEN | GAP-011 (P0), GAP-041. Phase 2 (2026-11-09): admission caps are divided among the gateway's workers and daily quotas are shared across processes (FSA-025) -- built and tested, still unmeasured on the live topology |
| Phase 2 core reliability (audit FSA-010/011/012/015/016/018/025/041/046/059/066/073/091/092/093/094/100/110/111/112) | BUILT and TESTED locally (pytest, legacy unittest, real PG16 for the Postgres-backed items, `dart test` 274, `flutter test` 630); **not CI-run, not deployed** | Branch `anas/magical-bell-e9spvh` after #229: nginx-only forwarded headers, production CORS, dev/prod parity, streamed `fetch_url`, Ollama `done_reason`, `forget_source` vectors, per-turn tool cap, JSON-mode boot warning, finetune drain reuse, asyncpg pool bounds + computed connection budget, Whisper language allow-list, legacy-plaintext read counter, digest-pinned images + required Space revision, model revisions + Whisper warm-up, per-user session index + refresh-family revocation, audit/telemetry retention + pseudonymised erasure, off-host vector payloads without text, host-tool cancel frames + approval-window budget, client Idempotency-Key, cross-process admission/quota. Live-only items (FSA-084/085, load matrix, re-encryption run) remain the owner's |
| Real-model evaluation gates | OPEN | GAP-021; GAP-006 (`eval-ci.yml`'s deterministic gate runs on PR and push; only its `dispatch-run` job is manual -- FSA-120 correction 2026-11-08) |
| Schema / migrations | TESTED (local PG16, `0001`→`0019`); production at `0019` (2026-11-07 probe). One schema authority (CB-20) TESTED locally and in CI (#225, `postgres` job green 2026-11-09), **not deployed** | Alembic `0001`→`0019`, single head. F-27: `0016` baselines the 16 runtime-only tables. **CB-20: the adapters no longer issue DDL**: no `CREATE TABLE IF NOT EXISTS` on first use, no runtime vocabulary seeding. Before removing it, a fresh PG16 at `head` and the same database after every adapter's old bootstrap gave identical `pg_dump --schema-only` and identical seed rows. `tests/architecture/test_no_runtime_ddl.py` forbids DDL under `aura_core` and requires a migration for every `aura_*` table the code queries. `tests/load/test_migration_roundtrip_postgres.py` undoes and re-applies all 19 revisions; `0017`–`0019` restore their parent exactly, and `0007`/`0016` keep only their named pre-existing tables. Consequence: an unmigrated database now fails requests, not just `/health/ready` |
| Personal-OS single store (ADR-032, CB-28 / F-18) | BUILT and TESTED locally (pytest, legacy unittest, real PG16); **not deployed, not CI-run** | Owner chose Option A (server-held per-user data key) on 2026-10-31. The agent's `create_/list_/update_/delete_*` tools now read and write the synced `tasks`/`calendar_events`/`finance_records`/`health_records` rows, encrypted `gcm2` with the user's data key; `GET /api/v3/keys/data` hands that key to the user's devices. **The synced fields are no longer zero-knowledge** (threat-model table in ADR-032). Old rows: `scripts/move_personal_os_records.py` (not yet run anywhere) |
| Workspaces / team scopes (SaaS P1-A, ADR-034) | BUILT and TESTED locally (pytest 4915 passed, legacy unittest, real PG16 contract + migration round-trip); **not CI-run, not deployed** | Branch `claude/saas-production-readiness-f5i61d`: migration `0020` (workspaces, members, invites), `TenancyService`, `X-Aura-Workspace` resolved per request, `/api/v3/workspaces` routes, knowledge/graph/CAG scoped by `Principal.knowledge_owner` (AST fitness test), HTTP isolation + IDOR scenarios. Also `/metrics` now fails closed in production without `AURA_METRICS_TOKEN`. No client/console screens, no invite email, no RLS yet |
| Customer sign-in (SaaS P1-B) | BUILT and TESTED locally; **not CI-run; inert in production until the owner steps in `docs/deployment/customer-sign-in.md` are done** (no `FIREBASE_CREDENTIALS_JSON` on Railway) | `POST /api/v3/auth/consumer/token` (no attestation, `channel=consumer_web` capped at `User` at issue/refresh/per request, 10/min/IP/worker); `email_verified` on the access token, teams require it; `scripts/import_users_to_firebase.py` (subject-preserving, dry-run default; dry run against a local PG16 quoted in the plan ledger). `tests/unit/test_consumer_web_sign_in.py`, `tests/unit/test_import_users_to_firebase.py` |
| Plans and metering (SaaS P1-C) | BUILT and TESTED locally (pytest incl. real-PG16 contract with 20-way concurrency, migration `0021` round-trip); **not CI-run, not deployed** | Free/Pro/Team catalogue as data (`AURA_PLAN_CATALOGUE`, placeholder numbers until the owner prices them); per-workspace monthly messages, Engineer gate, storage, team seats; 402 `plan_limit`; `GET /workspaces/{id}/plan`, SuperAdmin `PUT /admin/workspaces/{id}/plan`. Live voice turns not metered yet. `docs/deployment/plans-and-limits.md` |
| Subscriptions and Fawry billing (SaaS P1-D1) | BUILT and TESTED locally (pytest, real-PG16 store contract, migration `0022` round-trip, Fawry wire format against httpx.MockTransport); **off by default, not proven against Fawry staging, not CI-run, not deployed** | Reference-code checkout, signature-verified Server Notification V2, saved-card renewals, grace and expiry, reconciliation; hourly `scripts/run_billing_cycle.py`. Fawry signature formulas need confirming in the merchant's staging account; first card capture not built. `docs/deployment/billing-fawry.md` |
| App Store / Google Play subscriptions (SaaS P1-D2) | BUILT and TESTED locally (pytest: Apple JWS chain against a generated root plus the pinned Apple G3 pin, Play API via httpx.MockTransport, service and HTTP tests, real-PG16 contract, migration `0023` round-trip); **off until configured, not proven against App Store or Play sandbox, not CI-run, not deployed**; the app's purchase screens are Plan F | Binding token, verified App Store Server Notifications V2 and Play RTDN, mapped onto the same subscription record. `docs/deployment/billing-stores.md` |
| Desktop Engineer for customers (SaaS P1-E) | BUILT and TESTED locally (pytest: entitlement, turn capability, daemon-socket recheck on downgrade, desktop sign-in/refresh/pairing gates and limits, customer session refused by console routes; legacy unittest unchanged); **off by default (`AURA_ENGINEER_FOR_CUSTOMERS`), not CI-run, not deployed, not tried with a real desktop app and daemon** | Pro/Team customers sign in on desktop and pair `aura_hostd`; their sessions are capped at `User`; startup refuses the switch beside the server sandbox, a shared GitHub token, MCP or relaxed confirmation. ADR-011 "Customers", `docs/deployment/desktop-engineer-customers.md` |
| Customer surfaces, backend (SaaS P1-F1) | BUILT and TESTED locally (pytest incl. real-PG16 contracts, migrations `0024`/`0025` round-trip, IDOR entries); **email off by default (`AURA_EMAIL_ENABLED`), no provider chosen, not CI-run, not deployed** | Invite/receipt/renewal/past-due email over SMTP; shared team chats; account deletion refused while owning a team; operator Customers API and audited grace extension. App screens are F2, console page F3. `docs/deployment/email.md` |
| Learning / fine-tuning | BUILT | GAP-017; GAP-018 fixed in code (`finetune.py` records a failed train as failed; FSA-120 correction 2026-11-08), `built_not_proven` |
| Voice | BUILT | GAP-020: speech-to-text is English-only while an Arabic voice ships |
| Track AP: AP1–AP8 | TESTED (`built_not_proven`) | GAP-049…057; held at BUILT until M0 |
| Track AP: AP10 | In progress | GAP-059 |
| Track AP: AP9, AP11–AP13 | OPEN | GAP-058, GAP-060…062 |
| Flame integration AR-1…AR-8 | TESTED; one live Railway run | GAP-063; Link 2 passed only under a gate-coverage waiver (0.35); Link 1 never run |

## Open issues (most severe first)

**Operational**
0. **ADR-032 needs migration `0019` live** — *resolved 2026-11-07: `/health/ready` shows `0019`; the data move script is still unrun.* After the merge that carries it, `/health/ready` must
   show `schema.revision 0019`. Until then the key endpoint and the agent's personal-OS tools
   fail on production, because `aura_user_data_keys` does not exist. `AURA_DATA_ENCRYPTION_KEYS`
   (already required in production) now also wraps every user's data key: losing it loses every
   user's synced personal-OS text on the server. Then run `python
   scripts/move_personal_os_records.py --dry-run`, and without `--dry-run` if it is clean.
1. **Deploy-time migration runs, but has not yet applied a revision (CB-43 follow-up).** Since
   2026-10-31 the dashboard's pre-deploy command is `sh /app/scripts/railway-predeploy.sh`. Its
   first live run (deploy `4afdd9a8` at `9bfaa22`, 2026-10-31 08:40 UTC) logged `revision before/after: 0016 (head)` and
   `=== MIGRATION OK ===`. That deploy had nothing to apply, so the first PR that adds a migration
   is the real test: its log should show `before: 0016 … after: 0017`. Railway still ignores
   `railway.json` for this service, which leaves the `/health/live` health check and the builder
   setting unapplied. Linking it as the config file is a separate, larger change.
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
  the Postgres FTS recommendation are in `docs/reports/LEXICAL_RETRIEVAL_BENCHMARK.md`.
- F-14 cross-worker half (built and tested locally, including against a throwaway Postgres 16;
  not CI-run, not proven on a multi-worker deploy). Every corpus change advances a per-owner
  revision in `aura_corpus_revisions` (migration 0017) inside the writing transaction. Each
  worker reads the revisions for the owners a search touches, and diff-refreshes only the shards
  that moved. An upload, new version, delete or erasure served by one worker reaches the others'
  BM25 indexes on their next search of that owner, not at restart.

- F-25 deploy and runtime robustness (R2.7). BUILT and tested locally (pytest, legacy unittest,
  `docker compose config -q` on both files, the Celery healthcheck run against a local Redis
  worker and beat). Not CI-run and not proven: only a VPS deploy (VPS path still blocked, CB-03)
  or a Railway deploy can prove it.
  - The edge no longer waits for readiness. nginx and console depend on the gateway only being
    *started*, and the gateway healthcheck uses `/health/live` (prod and dev).
  - `vps-update.sh` now fails the deploy on three checks, each after a bounded wait: edge
    liveness (`/health/live`, HTTP 200 exactly), the revision check, and readiness (`healthy:
    true` plus a verified `schema` block, waiting up to `AURA_READY_TIMEOUT_S`, default 300 s).
    The old `https://$domain/health` probe passed on nginx's 301.
  - In-flight work now drains on shutdown. The gateway gets `stop_grace_period: 180s` and uvicorn
    `--timeout-graceful-shutdown 150`. Workers get 120 s and beat 30 s. On Railway, the
    Dockerfile CMD `exec`s uvicorn with the same timeout and `railway.json` sets
    `drainingSeconds: 180`.
  - Celery tasks now have soft and hard time limits per task family and `acks_late` with
    `reject_on_worker_lost`. Training tasks are opted out of `acks_late`, and ingestion tasks are
    not requeued after a crash. Prefetch is 1, and the Redis visibility timeout (2 h) is longer
    than any late-acked task's hard limit. A soft limit no longer leaves its coroutine on the
    reused worker loop.
  - Every worker and beat now has a healthcheck (`scripts/celery_healthcheck.py`).
  - Pinned by `tests/unit/test_deploy_robustness.py`.
  - Not done here: the R2.7 drill report.
  - Follow-up (R2.7, built and tested locally, not CI-run): `Container.shutdown()` now closes
    what the container actually connected: the shared asyncpg pool (terminated if a graceful
    close outlasts its budget), Redis, Qdrant, each provider's httpx client, the Docker client,
    and the GitHub and MCP clients. It never builds a client in order to close it. Closes run
    concurrently, each bounded by 5 s, so the whole shutdown fits the 15 s lifespan margin. One
    failing or hanging close is logged and the rest still run, and a second call does nothing.
    The gateway lifespan calls it after uvicorn's drain, and each Celery worker process calls it
    on exit (`worker_process_shutdown`/`worker_shutdown`), on its own event loop. Pinned by
    `tests/unit/test_container_shutdown.py`. The legacy SQLAlchemy engine
    (`infrastructure/db/base.py`) is outside the container and is still not disposed.

**Still open (High)**
- F-22 and F-27 are built and tested locally, not CI-run:
  - F-22: one configured LLM deadline, an SSE keep-alive every 15 s, and `/api/v3/chat` given
    600 s at nginx.
  - F-27: migration `0016` baselines the 16 runtime-only tables. CB-20 (2026-11-09) then removed
    the runtime DDL itself (see the schema row).
- **GAP-011, GAP-041 (P0):** inference is single-slot and no load matrix exists.

**Medium:** F-16, F-17, F-23, F-24, F-26, F-28, F-33–F-38. F-25 and F-30 are built (above), not proven. F-18 is built (ADR-032), tested locally, not CI-run or deployed.

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

0. ~~After the ADR-032 merge deploys: check that `/health/ready` reports `schema.revision 0019`~~ (done: live `0019` on 2026-11-07), then
   run the personal-OS data move (`--dry-run` first), and confirm in the app that an
   agent-created task appears after a sync.
1. **Owner:** confirm a real sign-in works on `aureon-ai.tech` now that the schema is `0016` (CB-43 migrated 2026-10-30).
2. **Owner:** restore Actions (CB-44); re-run Backend CI on `main`; gate Railway deploys on CI.
3. Enable branch protection with required checks (GAP-005 / CB-02).
4. Get CI to run the R0/R1 remainder (F-01b, F-02 revocation, F-20, F-21, F-06) and record it.
5. CI-run F-08 and F-09 (built and tested locally). For F-08, the GitHub callback now shows a
   confirmation page naming the AURA account, and a GitHub account already linked to another
   user is refused. For F-09, pairing tokens are scoped to the host-agent socket, and open
   sockets re-check their credential every 60 s. Since 2026-11-16, `/desktop/login` is for
   operators only, and `/auth/refresh` ends a non-operator's desktop session (owner decision;
   ADR-011 amendment; TESTED locally).
6. **ADR-030 Phase 6:** owner adds the `AURA_HOSTED1_API_KEY` secret, then runs Verify Hosted
   Inference with the `openrouter-free` preset and commits the report (GAP-064).
7. Metrics are built (F-24). Next, confirm that a live `/metrics` scrape shows `aura_http_*`
   and the outbox gauges, then run the M5 load matrix (GAP-041) to measure what the F-22
   budgets assume.
8. **Owner:** decide the VPS's future (CB-03).
9. Prove F-25 with the R2.7 drill on a live stack and file the report in `docs/reports/`. The
   drill has two halves: stop Ollama, recreate nginx, and the edge must still serve
   `/health/live`; a deploy with readiness broken must fail the workflow. On Railway, check that
   a deploy during a streamed turn lets the stream finish (`drainingSeconds`).

## Unverified

- What the VPS runs, and whether its host-key change was legitimate.
- Which provider and model `hosted1` is on Railway (the health endpoint does not say).
- The "5–10 tok/s" figure for qwen2.5:3b. No measurement on a live target is committed.
- F-20 at runtime (needs worker logs). F-03 secret paths on the VPS. The Firebase providers
  (SEC-08).
- Whether sign-in works live after the 2026-10-30 migration (no real account has probed it).
- F-25 at runtime. Four things are unverified:
  - that uvicorn drains under Docker/Railway SIGTERM as configured;
  - the beat healthcheck's schedule-file freshness with the image's Python 3.11 `dbm` backend
    (it was run only against 3.13's sqlite backend);
  - that `drainingSeconds` is honoured on this Railway plan;
  - whether a deploy that fails readiness because inference is down is the trade the owner
    wants (it is intended: such a release cannot answer a chat turn).
- Whether `scripts/railway-predeploy.sh` runs as intended on Railway. The cause of the old failure is known (no shell, so only the `echo` ran), but the fix has not been through a deploy yet.
