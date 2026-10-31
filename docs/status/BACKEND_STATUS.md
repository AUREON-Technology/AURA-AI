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
| Hexagonal layering fitness tests | TESTED | `tests/architecture/test_dependency_rules.py`; F-28 fixed 2026-10-31 (not CI-run): every `aura_core` package, relative and absolute imports, vendor-SDK denylist at module scope, self-tests. One allowlisted debt: `engineering2` → `adapters.coding_fakes` |
| Self-hosting invariant / ADR-030 host categories | TESTED | `tests/architecture/test_no_external_inference.py` |
| AIKernel plan → execute → generate | TESTED (scripted/echo providers) | GAP-021 open; F-24/CB-14 built (not CI-run): a planner failure is now a degraded turn with a reason, and the stream path records telemetry |
| Email/issue analysis off the event loop (F-23) | BUILT, tested locally, not CI-run | `/api/v3/automation/*` goes through `ProviderRouter` (awaited, admitted, local-only since unclassified) and returns 503 on failure instead of a 200 placeholder; the rate limiter's Redis `INCR` runs in the threadpool; the shared Redis client has a 2 s command timeout |
| Request metrics / outbox gauges (F-24, CB-15) | BUILT, not proven | Per-route HTTP histogram and status counter, buckets to 600 s, outbox backlog gauges and alerts. Needs a scrape on a live target and the M5 load run |
| Tool confirmation / taint (ADR-006) | BUILT (fix merged, not CI-run) | F-04 fixed (`d6ced5a`): single-use `approval_id` bound to stored turn + call digest; GAP-014 |
| ProviderRouter, breakers, failover | TESTED; F-19 fix not CI-run | F-19 fixed (`42af1be`): `release_probe()` frees the half-open slot |
| Self-hosted LLM (qwen2.5:3b, CPU Ollama) | TESTED in a sandbox only | A4 sandbox p50 56 s / p95 71 s (`docs/reports/phase-a4-load-test.md`); not on the current production target |
| Hosted open-weight inference, ADR-030 Phases 1–5 | TESTED | GAP-064; inert unless `AURA_ALLOW_HOSTED_INFERENCE=true` |
| ADR-030 Phase 6, live | BUILT (`built_not_proven`) | In-process run against live OpenRouter (`nvidia/nemotron-3-ultra-550b-a55b:free`, `ALLOW_PROMPT_TRAINING=true`): 9/10 checks pass, the 429 burst was skipped (`docs/reports/phase-6-hosted-inference-openrouter-inprocess-20261028.txt`). The Verify Hosted Inference workflow has never run. |
| Hybrid retrieval / knowledge | TESTED (hash embedder) | A3 recall@5 0.65; F-14 fixed: drop-on-read in `c415a52`; the cross-worker change feed and active-only `chunks_for` are in the `fix(retrieval)` F-14 commit (tested on real Postgres and Redis, not CI-run); F-30 built and benchmarked locally, not CI-run: the threshold no longer passes a rank pseudo-score, and the owner-sharded inverted BM25 brings the lexical stage at 50k chunks from 54.7 to 4.8 ms p50, with identical scores (`docs/reports/LEXICAL_RETRIEVAL_BENCHMARK.md`; the 200k point and Postgres FTS are open) |
| Memory engine | TESTED | F-15 fixed (`19244e6`); F-16 write races built and tested locally (in-memory + Postgres 16), not CI-run; AR-12 (two memory stores) open, see below |
| v2 sync protocol | BUILT (fixes merged, not CI-run); **broken live until 0014 applies** | F-11 (`31608f9`), F-12 (`19c64f9`, migration `0014`), F-13 (`c5b1924`), F-07 (`98143ff`) |
| Auth / RBAC / JWT | BUILT (fixes merged, not CI-run); **at risk live until 0015 applies** | F-02: refresh re-resolves role (`2d42388`) and Access-page assignments now take effect (`3e4e0f5`, migration `0015`); F-03 (`a3fa5c2`: dev keys, `iss`/`aud`); F-10 (`039865e`: no JWT secret → no start in production). Demotion still revokes no sessions. |
| Security regression suite (P1.5) | TESTED | GAP-048 `built_not_proven` |
| Engineer workspaces (ADR-013/014) | BUILT | GAP-025; off by default; no `verify-engineer-workspaces` report |
| Desktop hostd bridge | BUILT | GAP-026; F-21 (confirmed): non-streaming `run` returns `''` / `-1` |
| Observability | OPEN | GAP-036, GAP-037 |
| Backup / DR | TESTED in a sandbox; OPEN on any live target | A2 sandbox (`phase-a2-restore-drill.md`); GAP-028, GAP-046 |
| 50-user capacity | OPEN | GAP-011 (P0), GAP-041 |
| Real-model evaluation gates | OPEN | GAP-021; GAP-006 (`eval-ci.yml` is dispatch-only) |
| Schema / migrations | TESTED (local PG16, `0001`→`0017`); production at `0016` until the ADR-032 change deploys | Alembic `0001`→`0017`, single head; F-27 built: `0016` baselines the 16 runtime-only tables, `env.py` keeps autogenerate off `aura_*`, and a fitness test requires a migration for every runtime table. `0017` (ADR-032) adds `aura_user_data_keys`; `upgrade → downgrade -1 → upgrade` checked on PG16 |
| Personal-OS single store (ADR-032, CB-28 / F-18) | BUILT and TESTED locally (pytest, legacy unittest, real PG16); **not deployed, not CI-run** | Owner chose Option A (server-held per-user data key) on 2026-10-31. The agent's `create_/list_/update_/delete_*` tools now read and write the synced `tasks`/`calendar_events`/`finance_records`/`health_records` rows, encrypted `gcm2` with the user's data key; `GET /api/v3/keys/data` hands that key to the user's devices. **The synced fields are no longer zero-knowledge** (threat-model table in ADR-032). Old rows: `scripts/move_personal_os_records.py` (not yet run anywhere) |
| Learning / fine-tuning | BUILT | GAP-017; GAP-018 (a failed train is recorded as success) |
| Voice | BUILT | GAP-020: speech-to-text is English-only while an Arabic voice ships |
| Track AP: AP1–AP8 | TESTED (`built_not_proven`) | GAP-049…057; held at BUILT until M0 |
| Track AP: AP10 | In progress | GAP-059 |
| Track AP: AP9, AP11–AP13 | OPEN | GAP-058, GAP-060…062 |
| Flame integration AR-1…AR-8 | TESTED; one live Railway run | GAP-063; Link 2 passed only under a gate-coverage waiver (0.35); Link 1 never run |

## Open issues (most severe first)

**Operational**
0. **ADR-032 needs migration `0017` live.** After the merge that carries it, `/health/ready` must
   show `schema.revision 0017`. Until then the key endpoint and the agent's personal-OS tools
   fail on production, because `aura_user_data_keys` does not exist. `AURA_DATA_ENCRYPTION_KEYS`
   (already required in production) now also wraps every user's data key: losing it loses every
   user's synced personal-OS text on the server. Then run `python
   scripts/move_personal_os_records.py --dry-run`, and without `--dry-run` if it is clean.
1. **Pre-deploy migration fix unproven on a live deploy (CB-43 follow-up).** The database is at
   `0016` and readiness is green. The first deploy of the script (8bc8d4c) logged only
   `MIGRATION START`, and the step ended about a second later, so the outcome was invisible.
   The script now flushes its log before exiting. The next deploy must show `revision
   before/after` and `=== MIGRATION OK ===`. Only `MIGRATION START` means the script itself
   stops early.
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
- F-14 cross-worker half (R2.4 Option A, no migration). Built and tested locally, including
  against real Postgres 16 and Redis 7, two OS processes among the runs. Not CI-run and not
  deployed.
  - **The change feed.** Every ingest, new version and delete bumps a generation counter in the
    shared cache (Redis `INCR`) and records the changed document ids.
  - **Catching up.** Each worker checks the counter at most every 250 ms before a lexical
    search, then re-indexes those documents from the shared store.
  - **Fallbacks.** A reset counter, a lost entry, or a worker too far behind triggers a full
    rebuild, which is built off to the side and swapped in.
  - **The read-side fix.** `chunks_for` now resolves only active versions, on both Postgres and
    in-memory, so stale lexical ids resolve to nothing.
  - **Measured.** An upload became visible on the other process in about 20 ms. The worst-case
    per-query overhead is one Redis `GET`, 0.4–0.7 ms p50
    (`docs/reports/LEXICAL_RETRIEVAL_BENCHMARK.md` §7).
  - **Bonus.** Documents ingested by Celery now reach the gateway workers' lexical indexes too.
  - **Still open.** Per-worker memory and startup cost, which Option B addresses.

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
  - `container.shutdown()` (2026-10-31) now also closes the Postgres pool, the Redis client and
    the Qdrant client, each in its own `try` under a per-resource timeout inside a 10 s overall
    budget, and is idempotent (`tests/unit/test_container_shutdown.py`). Not done: the R2.7
    drill report.

- F-16 memory write races (built, tested locally, not CI-run).
  - Recall reinforcement no longer rewrites whole rows. It used to call `store.update(record.touched())`
    from the recall's snapshot: a full UPSERT that put `superseded_by` back to NULL (a superseded
    memory came back) and lost concurrent `access_count` increments. A new `MemoryStore.touch_many`
    port method does one field-level `UPDATE … SET access_count = access_count + 1,
    last_accessed_at = GREATEST(…), confidence = GREATEST(…) WHERE id = ANY($1) AND superseded_by
    IS NULL` (the in-memory adapter does the same under its lock). It never writes `content`,
    `superseded_by`, `expires_at` or `metadata`. `remember()`'s near-duplicate reinforcement goes
    through the same method. If the duplicate was superseded in the meantime, the statement is
    stored as a new fact.
  - `EpisodicMemory.append` takes a short per-(owner, session) lease through the shared `Cache`
    port (`incr` == 1, 15 s TTL). The lease covers only the load-append-save, so it holds across
    workers. Summarisation runs outside the lease on a snapshot of the oldest messages, then
    commits with a compare-and-set: the prefix must still be there and the summary unchanged. Only
    then is the prefix dropped, and messages appended during the model call are kept. One
    summarisation runs per session at a time (single-flight marker). If the cache cannot count,
    the append proceeds unprotected rather than failing the turn.
  - Tests: `tests/unit/test_memory_write_races.py` and `tests/unit/test_session_write_races.py`
    (both fail on the old code), plus `tests/load/test_memory_touch_postgres.py` against Postgres
    16. The Postgres test includes a touch that blocks on an uncommitted supersede and re-checks
    after the supersede commits.
  - Not addressed: consolidation's decay and supersede writes are still whole-row writes from its
    own snapshot. They can drop a concurrent reinforcement's increment, but they cannot resurrect
    anything.
- **AR-12: two memory stores. Current state (no unification in this change; it needs an ADR).**
  - `aura_memories` (`MemoryStore`, v3) is the only store the kernel reads. These write to it: the
    extractor, the `remember` tool, and `POST /api/v3/memory`.
  - The v2 `memories` table (`SYNCABLE_MODELS`) receives only an encrypted copy, which the client
    queues in `MemoryRepository.saveMemory` after the v3 POST:
    - When the POST returns 200, the copy uses the server's `MemoryId` (F-07/F-13), so both rows
      share one id.
    - When the POST fails, the copy keeps a client UUID that v3 never sees. The v3 memory is then
      never created and the save is not retried. That is the remaining id divergence.
    - Memories the server writes on its own (extractor, tool) never get a `memories` row.
  - Nothing reads live `memories` rows. A client pull applies only their tombstones
    (`_applyMemoryTombstones`), which is how a forget reaches other devices. The memory list comes
    from `GET /api/v3/memory`.
  - A client delete removes the v3 record by id and queues a sync tombstone. `push_sync` records
    that tombstone only if the row exists.
  - User erasure covers both stores: `forget_owner` for `aura_memories`, `users` `ON DELETE
    CASCADE` for `memories`.
  - No backend code path writes both stores for the same fact. The only dual write is the client's
    deliberate encrypted copy. Removing it would also remove the tombstone channel, so that is part
    of the store decision, not of this fix.

- F-17, "today" half (built, tested locally against Postgres 16, not CI-run). The 2026-10-26 audit
  found that insights and the daily briefing computed "today" and the 3 pm hydration cut-off in
  UTC. The client stores a day's health record at local midnight, so a UTC+3 user was told
  "0 ml of water logged today" every evening. Both services now take the user's timezone
  (`services/user_timezone.py`): "today" runs from local midnight to local midnight (23 or 25
  hours on a DST day), and the hydration query is now bounded on both sides.
  - **Where the timezone lives, without a migration.** No existing column fits: `users` has no
    settings or preferences field. Instead:
    - The client sends `X-Timezone` on the insights requests it already makes (`GET
      /api/v3/insights` on every Today-screen visit, and `POST …/generate`).
    - `/generate` uses that value directly. Both routes also store it in the shared cache
      (`user-tz:<user id>`, Redis in both deployments, 60-day TTL refreshed on each request).
    - The hourly insights job and the daily briefing job read it from there.
    - Missing, unknown or invalid values fall back to the UTC day, the old behaviour. A lost
      cache entry costs only that, and only until the user's next visit.
    - The follow-up migration would add one nullable `users.timezone VARCHAR(64)` and change only
      `remember_timezone`/`stored_timezone`.
  - **What the client sends.** A fixed offset (`+03:00`) from `DateTime.timeZoneOffset`, because
    no IANA source exists without a new native plugin (`flutter_timezone`). That plugin was
    deliberately not added. The backend also accepts IANA names, which are DST-correct on every
    date, so a later client can send one with no backend change. A fixed offset can be wrong for
    a few hours after a DST change, until the next insights request refreshes it.
  - Not changed: the briefing still runs at one fixed UTC time for everyone (it now counts the
    user's own day).
  - Tests: `tests/unit/test_user_timezone.py` covers UTC+3 and UTC-5 days, US DST days of 23 and
    25 hours, Santiago's skipped midnight, invalid input falling back to UTC, and header
    precedence. Six Postgres cases are in `test_insights_integration.py`. The client test is
    `test/core/insights_client_test.dart`.

**Still open (High)**
- F-22 and F-27 are built and tested locally, not CI-run:
  - F-22: one configured LLM deadline, an SSE keep-alive every 15 s, and `/api/v3/chat` given
    600 s at nginx.
  - F-27: migration `0016` baselines the 16 runtime-only tables.
- **GAP-011, GAP-041 (P0):** inference is single-slot and no load matrix exists.

- F-28 and F-38 code items (2026-10-31; built and tested locally, not CI-run):
  - F-28: the layering fitness test walks every `aura_core` package and resolves relative and
    absolute imports. It enforces domain/ports → stdlib, engines never → adapters/api/container,
    and no vendor SDK at module scope anywhere. One existing violation is allowlisted with a
    reason: dead `engineering2` defaults to `adapters.coding_fakes` (delete with F-29).
  - F-38/SEC-10: `requirements.lock` is hashed and compiled with uv `--torch-backend cpu` instead
    of `--extra-index-url … --index-strategy unsafe-best-match`. Pins are unchanged except
    pyasn1 0.6.5 and pypdf 6.21.4, bumped for advisories pip-audit reports against the old
    pins. Dev and training deps install from new hashed `requirements-dev.lock` and
    `requirements-train.lock`. Every install passes `--require-hashes` plus the PyTorch CPU index.
    The new command is `pip install --require-hashes --extra-index-url
    https://download.pytorch.org/whl/cpu -r requirements.lock -r requirements-dev.lock`; the
    old two-file command now fails, because pip's hash mode covers every file in the call.
    Verified: a fresh Python 3.11 venv on pip 24.0, and the Dockerfile's install lines in
    `python:3.11-slim` with training deps.
  - SEC-11: `deploy-backend.yml` and `control-plane-deploy.yml` jobs require
    `workflow_run.event == 'push'`, `head_branch == 'main'` and a head repository equal to this
    one. SEC-13: checkpoints load with `weights_only=True`. SEC-14: the secret scan covers every
    tracked text file in the client, backend, admin, hostd, deploy, workflows, scripts and docs.
    Its one hit is the public Firebase Android `api_key`, which is allowlisted.
  - Pinned by `tests/architecture/test_supply_chain.py`. SEC-08 is still open: it needs the
    Firebase console check.

**Medium:** F-16–F-18, F-23, F-24, F-26, F-33–F-38. F-25, F-28, F-30 and the F-38 code items
are built (above), not proven.

**Medium:** F-14, F-16, F-17, F-18 (ADR-032), F-23–F-26, F-28, F-30 and F-33–F-37, plus F-38's code items, are built and tested locally, not CI-run or proven. SEC-08 (part of F-38) needs the Firebase console check.

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

0. After the ADR-032 merge deploys: check that `/health/ready` reports `schema.revision 0017`, then
   run the personal-OS data move (`--dry-run` first), and confirm in the app that an
   agent-created task appears after a sync.
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
