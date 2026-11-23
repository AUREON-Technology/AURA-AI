# Backend status

**As of 2026-10-28 (evening), `main` at `2337577`.** Covers `aura_backend/` and the deploy stack under
`deploy/`. Evidence scale and refresh steps: [README.md](README.md).

**Re-read against commit:** `b2e14c4` (2026-11-08) for every row dated 2026-11-07 or later (their live checks came first; the re-read confirmed that tree holds what they describe); older rows carry the commit they were checked at. Refresh this line whenever a row is re-verified (audit FSA-120).

## Summary

**Update 2026-11-23, Coding Workspace: `delete_file`, `rename_file` and `POST /api/v3/engineering/workspaces` (branch `anas/laughing-darwin-6g7r2l`, TESTED locally, not CI-run or deployed; no migration).** Execution stays in the Local Runtime: **no HTTP route reads, writes, or runs anything on a user's files** (the existing `GET/DELETE /engineering/workspaces` are the only workspace routes besides the new one). The Engineer gains two tools and the `SandboxRunner` port two methods, implemented for the Docker and in-memory runners (`adapters/sandbox_fs.py`, one shared rule set), the desktop bridge (one RPC each, `confirmed` forwarded) and the composite. In `aura_hostd` the daemon enforces its own gates: containment, nothing at or inside a `.git` directory, no directories (a tree is still a deliberate `run_command`), rename never replaces an existing entry, a symlink is acted on rather than followed, the trust/approval queue in an untrusted folder (the person sees the removed lines, or the rename), remote-task confirmation, and a re-check after approval that the file did not change meanwhile. Both tools are `workspace_local` like `write_file` (pinned in `tests/architecture/test_workspace_local_tools.py`) and granted to the Engineer persona; **that is a design choice:** the backend asks for no confirmation, the daemon asks the person in an untrusted folder. `POST /engineering/workspaces` creates or resumes the caller's durable workspace for a repository (201 new / 200 existing, 503 when the host cannot afford one, 403 without the Engineer, 404 where workspaces are off) and stops it again at once; **it does not clone** (the repository arrives when the agent clones it with the user's GitHub connection). Evidence: `tests/unit/test_sandbox_file_ops.py`, the boundary cases in `tests/security/test_workspace_boundaries.py` (both runners; `..`, absolute paths, symlinked directories, null bytes), the route tests in `tests/unit/test_api_surface.py`, and 29 daemon tests (`aura_hostd/test/file_ops_test.dart`) plus a wire-level dispatch test; each guarantee was broken on purpose and a test failed (two survivors were equivalent: a second check refuses the same input). **Full local suite: 7560 passed, 76 skipped, 0 failed; legacy `unittest` OK (148, 39 skipped); daemon 621 passed, 1 failed** (`control_server_test` "debug console ... spawns a real debugpy": the Python `debugpy` module is not installed in this container and it fails the same way on unmodified code). A first full run stopped on `test_personal_os_records.py::test_default_agent_registry_grants_name_real_tools`, whose hand-written tool-name lists lacked the two new tools; fixed. **Not shown:** a real desktop daemon run end to end, a real Docker workspace, and CI (CB-44). `docs/guides/api-reachability.md` lists the new POST as "called": that matcher compares the path only, and the app calls only GET and DELETE on it, so **the app cannot create a workspace yet**. Not built, on purpose: a stored command result by id (the ledger holds no content; a run's status is `GET /agents/runs/{id}/status`) and any route that proxies file or command calls to the user's machine (they would skip `ToolPolicy` and the taint rules, ADR-006/011).

**Update 2026-11-22, agent runs as tasks (`/api/v3/agents/runs`, branch `anas/laughing-darwin-6g7r2l`, TESTED locally on a real Postgres 16.15, not CI-run or deployed; adds migration `0036`).** An agent "task" here is an `AgentRun`: it exists because a chat turn planned and it runs **inside that request**; there is no separate create/start, and no background execution (the Celery tasks in `tasks.py` are ingestion, fine-tuning and insights, and Railway runs no worker service). Added around the existing list and detail routes: `GET /agents/runs` is now keyset-paged (`cursor`, `next_cursor`) and filtered by `since`, `until`, `retry_of` as well as `agent` and `status` (a cursor made for other filters is a 422; the response keeps its shape); `GET /agents/runs/{id}/status` (small, for polling); `GET /agents/runs/{id}/logs?after_seq=` (the run's steps, to follow a live run); `POST /agents/runs/{id}/cancel`; and `retry_of` on `/chat`. **Logs are structured and carry no content** (phase, kind, tool, status, error kind, argument digest), because the run ledger stores none; that is a property of the ledger. **Cancel is cooperative and stops further work only:** the run's budget gate refuses the next tool dispatch and the loop ends at the next pass, but a model call or tool already running finishes, and the turn then still answers with what completed (a client that wants the reply gone also closes the stream). The request is a cache key every worker polls for the runs it executes, so it reaches a run on another worker; a run still `running` and untouched for 15 minutes is treated as orphaned (its worker died) and closed directly. New terminal status `cancelled`. **Retry cannot re-run:** the instruction is never stored, so a retry is the client sending the turn again with `retry_of=<run id>`, which must be the caller's own run in `partial`/`failed`/`budget_exhausted`/`cancelled` (409 otherwise; another person's id and a missing one both 404) and is recorded on the new run (migration `0036`, written once, never moved by a later save). Evidence: `tests/unit/test_agent_tasks.py` 40 passed and `tests/load/test_agent_tasks_postgres.py` 5 passed on a real Postgres (breaking the gate, the loop stop, the watch cleanup, the cancel key's owner scope, the finished-run refusal, the orphan threshold in both directions, the retryable check, the cursor and the SQL's after_seq, retry_of and cursor each fails them; the first orphan test was circular and was rewritten with fixed ages after its mutation passed), `tests/security` 533 passed with the three new id routes registered in the IDOR gate, and the all-revisions migration round-trip. **Not shown:** cancel of a turn on a real multi-worker Redis deployment (tests use the in-memory cache and a simulated second worker), and no app screen calls any of this.

**Update 2026-11-22, custom agents (`/api/v3/agents`, branch `anas/laughing-darwin-6g7r2l`, TESTED locally on a real Postgres 16.15, not CI-run or deployed; adds migration `0035`).** `GET /agents/{id}`, `POST /agents`, `PATCH /agents/{id}`, `PUT /agents/{id}/config`, `DELETE /agents/{id}`, `POST /agents/{id}/clone` (`aura_core/api/agents.py`), and `GET /agents` gains a separate `custom_agents` key (the `agents` array is unchanged). The built-in agents are still code and read-only here. A custom agent is a built-in persona **plus** extra instructions, a narrower tool list and a lower risk ceiling, owned by one person, so it can only narrow its base: `apply_custom_agent` intersects the tool list with what the *resolved* base permits on every turn (a tool the base never had cannot appear even if stored), drops the base's prefix grants when a list is given, and takes the lower ceiling; the `engineer` can never be a base or cloned (also a database CHECK). It runs by naming its id as `agent` on `/chat` or `/chat/stream`, resolved in `to_turn` under the caller's ownership (another person's id and a missing one both 404; a service key cannot use one). The instructions are appended under the persona's prompt as a delimited block that says it is the user's and cannot override the rules above; the real guards are structural (tool grants, ADR-006), not that text. Limits: 20 per person, name 60, description 280, instruction 4000 chars, 30 changes a minute; names unique per person ignoring case; the limit is held under concurrency by a per-owner advisory lock (verified: without it the limit is exceeded in every round of a 16-way race). Account erasure deletes them. **Instructions are stored as plain text** (the server must read them to build the prompt), there is **no plan gating and no sharing between workspace members** in this version, and `base_agent` cannot be changed after creation. Evidence: `tests/unit/test_custom_agents.py` 53 passed and `tests/load/test_custom_agents_postgres.py` 6 passed (breaking the tool intersection, the overlay, the owner check, the ceiling rule or the lock each fails them), `tests/security` 528 passed with the five id routes registered in the IDOR gate against real two-person scenarios, and the all-revisions migration round-trip. **Not shown:** that a real model follows the instructions (tests use the echo provider and a fixed planner, so they prove what the model is shown and which tools run, not how it behaves), and no app screen calls the routes (the app's `AgentConfigurations` instruction table is local, unused and never sent to the server).

**Update 2026-11-22, `/api/v3/chats` (branch `anas/laughing-darwin-6g7r2l`, TESTED locally on a real Postgres 16.15, not CI-run or deployed; adds migration `0034`).** `GET/POST /chats`, `GET/PATCH/DELETE /chats/{id}` and `POST /chats/{id}/archive|unarchive|pin|unpin` (`aura_backend/api/chats.py`). A chat is still not a row: the list is derived from `messages.conversation_id` (a FULL JOIN of the live messages' aggregate to a new `conversations` metadata table), so every existing chat is listed with no backfill. `conversations` (title as ciphertext, `pinned`, `archived`) is a synced table, so a pin or title set through the API reaches the other devices and one set offline reaches the API; two devices creating the row offline merge onto one by `conversation_key`. The list is keyset-paged (a cursor made for another sort, order or filter is a 422) and filters by archived, pinned and time; **it cannot search or sort by title or content, which are ciphertext the server never reads**, so search stays on the client. Delete tombstones the messages and the metadata (their ciphertext cleared, like the sync's own delete) and forgets the server transcript; a stranger's chat id answers exactly what a missing one does (404). **Sync protocol:** the paged pull's table order is now explicit (the original eight keep their positions, new tables are appended), because adding `conversations`, which sorts before `finance_records`, would otherwise have slid every index in a page token read mid-deploy. The shared payload contract fixture (`aura_client/test/fixtures/sync_payload_contract.json`) gains `conversations`; the Flutter side reads pulled tables by name and ignores unknown ones, but that is from reading `sync_client.dart`, **not run**: no Flutter test, device or old app build was tried. Evidence: `tests/load/test_chats_postgres.py` 22 passed (real HTTP beside the real sync routes, two people; breaking the user scoping or the cursor on purpose fails it), the all-revisions migration round-trip, and the migrated table matches the ORM model column for column. No app screen calls the routes yet (`docs/guides/api-reachability.md`, triaged "to wire"); the app still lists, pins and renames chats locally.

**Update 2026-11-22, `GET /api/v3/turns/{turn_id}/usage` (branch `anas/laughing-darwin-6g7r2l`, TESTED locally, not CI-run or deployed; adds migration `0033`).** A turn's tokens by purpose, cost, model and latency, readable later by `turn_id` (the same numbers still come back inline on `/chat` and the stream's `done` frame). `aura_turn_telemetry` holds no user id by design, so the route is scoped by a new `owner_ref` column: the HMAC of the user id under a key derived from the JWT secret (`aura_core/security/owner_ref.py`), not the id itself. A turn that is missing, someone else's, recorded before `0033`, or recorded with no secret configured all answer the same 404, and a deployment without a secret fails closed. `owner_ref` is personal data: account erasure deletes by it (`telemetry_owner_turns` step), retention still purges by age, and it is kept out of `TurnTelemetry.as_dict`, logs and metrics. Rotating the JWT secret orphans existing rows (unreadable, never readable by someone else). No new Railway variable: the key derives from the existing JWT secret. Verified on a real Postgres 16.15 (new `tests/load/test_turn_usage_postgres.py`, the audit-retention test on the same table, and the all-revisions up/down round-trip, 5 passed) and in `tests/unit/test_turn_usage.py` (19 passed; breaking the owner check on purpose fails 4 of them, one over HTTP between two users). The first deploy after the merge applies `0033` in Railway's pre-deploy step (a nullable column and a partial index, no rewrite). **Correction after the merge:** the full local suite on the merged commit gave 7315 passed, 76 skipped and 1 failed, `tests/security/test_idor.py::test_every_id_taking_route_is_accounted_for`: the new route takes an id and had no IDOR scenario (the earlier targeted run had not included `tests/security`). It was merged and live with that test failing; the follow-up adds the scenario (Bob naming Alice's turn gets the same 404 as a missing one; breaking the owner check fails it) and `tests/security` is 507 passed. The legacy `unittest` run was OK. No app screen calls the route yet (`docs/guides/api-reachability.md`, triaged "to wire").

**Update 2026-11-22, `/api/v3/me` (branch `anas/laughing-darwin-6g7r2l`, TESTED locally, not CI-run or deployed).** `GET /me`, `PATCH /me` (display name, photo, language, time zone; the email is refused) and `POST /me/password` (`aura_backend/api/me.py`). Sign-in and the password stay at Firebase: this server stores no password. A password change needs a Firebase ID token from signing in with the *current* password in the last 5 minutes, for the same account, then revokes every session and paired device. Register and forgot/reset password remain in the client's Firebase SDK. 41 new tests in `tests/unit/test_me_profile.py` (a mutation of the freshness and account checks makes 3 of them fail); `tests/architecture` and the neighbouring auth tests: 544 passed. The Firebase calls are replaced by a scripted provider, so nothing has run against a real Firebase project, and there is no app screen yet (`docs/guides/api-reachability.md`, triaged "to wire"). A full `pytest -x` stopped at `test_context_window_improve.py::TestCountEachItemOnce::test_history_is_counted_once_even_when_the_cap_drops_messages`. That test was flaky on its own (5 failures in 40 isolated runs), not order-dependent as first written here: the turn's fence nonce is random and the heuristic counts a digit/letter-alternating nonce as more tokens, so the prompt's floor ranged from 188 to 207 against a cap of 200. The test now fixes the nonce (0 failures in 60 runs); production behaviour is unchanged (an over-cap prompt only logs a warning).

**Update 2026-11-18, Phase R (same branch, TESTED locally, not CI-run or deployed).** On Railway,
`/metrics` was answered by one random uvicorn worker per scrape. The image `CMD` now shares one
`PROMETHEUS_MULTIPROC_DIR`. Tracing produced no spans at all; requests, provider attempts and turns
are now traced, with no content or identity in any span. Grafana Cloud runbook:
`docs/deployment/observability.md`. Nothing is configured on Grafana Cloud yet (GAP-036
`in_progress`).

**Update 2026-11-17, Phase I (same branch, TESTED locally, not CI-run or deployed).**
`AURA_PROVIDER_<NAME>_HEALTH_PROBE=passive` lets readiness report a provider from its circuit
breaker without calling it. That is for the HF Space, which sleeps when idle and wakes (and
bills) on any request, including a `/health/ready` probe. An image turn answered by OCR alone
now ends its vision step `degraded` with a "send it again in a minute" description. The Space
itself is **PAUSED** (`a10g-small`). The chosen VLM (`Qwen2.5-VL-7B-Instruct-AWQ` @ `536a3579`)
is not served yet, so GAP-019 stays `in_progress`.

**Update 2026-11-17 (live probe of `api.aureon-ai.tech` at `446e004`, plus Phase V on branch
`anas/laughing-hamilton-glfevj`).** `/health/ready`: healthy, schema `0021`, `hfspace` 503. **The
knowledge alias still points at the `hash-embed-v1` index while the embedder is bge-small**, so
`resolve_for_read()` refuses it and every retrieval returns `strategy="unavailable"`: RAG is off
live until an admin runs `POST /api/v3/admin/index/rebuild` (plan Phase 0.2,
`docs/plans/runtime_feature_enablement_plan.md`). The apex `aureon-ai.tech` answers Railway's
`Application not found`. Phase V, TESTED locally (pytest 5156 passed / 42 skipped, legacy
unittest 146 OK), not CI-run or deployed: `/voice/transcribe` reports the `language` it used
(merged 2026-11-18 with Arabic stage 4, whose rule stands: an unknown code is a 422, and an
English-only model ignores the hint), an opt-in Silero VAD (`AURA_VOICE_LIVE_VAD=silero`, reported
as `voice.vad` on `/health/ready`) and `scripts/measure_stt_wer.py`. After the merge: pytest 5307
passed / 42 skipped, legacy unittest 148 OK.

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
| **Hugging Face GPU Space** (`hfspace`) | Down or asleep (503 from `/health/ready`, still so on 2026-11-18 at `api.aureon-ai.tech`; after PR #246 merged, production serves `61ad66e` with schema `0029`, healthy). **SaaS P1-G (owner decision 2026-11-18):** one L4, always on (~$584/mo); the deploy workflow now enforces hardware and no sleep, and `deploy/scripts/verify_gpu_source.py` collects readiness items 2, 3, 6, 9. **Not proven until the owner steps in `docs/deployment/inference-plane.md` §9 are done and a report with no FAIL/SKIP is committed.** Seen live 2026-11-18: `/metrics`, `/docs`, `/openapi.json` → 404 (item 3 holds, unrecorded until the report) |
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
| Open code-analysis tools (ADR-036) | BUILT, tested locally, **not proven** | GAP-066. 17 pure analysis tools (`tools/analysis.py`) on by default; two code-running tools (`tools/open_exec.py`) off by default, `engineer_agent` + per-call confirmation, run in a child of the gateway with no container; three docker/profiling tools (`tools/desktop_ops.py`) that run only on a paired desktop. Six duplicate classes stay unregistered; dev-server and browser tools deferred (need hostd RPCs). Unit and architecture pins only: no live listing, no real desktop run, planner prompt cost on the production model unmeasured |
| Engineer on Claude: workload fence, consent, per-user allowance (ADR-033 amendment 2026-11-17) | BUILT; merged in PR #251 (`d47e064`, 2026-11-18) and DEPLOYED: live `/health/ready` at 08:46 UTC showed `git_sha d47e064`, `schema.revision 0023`, `healthy: true` -- the first real deploy where Railway's pre-deploy command applied a new migration; TESTED locally, not CI-run | WP6a `AURA_PROVIDER_<N>_WORKLOADS`; WP1 consent (`/api/v3/engineering/consent`, migration `0023`, kernel refusal `consent_required`); WP2 `USER_TPD` (`user_quota_exhausted`). Real Postgres 16 and Redis tests pass. No Claude slot is configured on Railway yet (WP6: the runbook is `docs/deployment/control-plane.md`, "Claude for the Engineer"; the variables are the owner's to set), so no Engineer turn runs live; notice text awaits owner review (O-03). See `docs/module-readiness/engineer-agent/desktop-github/2026-11-17/` |
| Desktop hostd bridge | BUILT | GAP-026; F-21 (confirmed): non-streaming `run` returns `''` / `-1` |
| Observability | OPEN | GAP-036, GAP-037 |
| Backup / DR | TESTED in a sandbox; OPEN on any live target | A2 sandbox (`phase-a2-restore-drill.md`); GAP-028, GAP-046. Multi-provider offsite (2026-11-17, PR #252, `docs/plans/distributed_storage_plan.md`): copies to every `BACKUP_OFFSITE_TARGETS` remote, durable only at `BACKUP_OFFSITE_MIN_COPIES` (default 2), `backup-fetch.sh` for restore -- BUILT and TESTED with a stub rclone, **not deployed, no real provider used**. **The live Railway data had no backup in this repo** (the sidecar runs only on the VPS): a Railway cron service (`deploy/backup/railway.Dockerfile`, `docs/deployment/backups.md`) now covers Railway Postgres, Redis and Qdrant Cloud -- proven locally end to end against stock services, **not yet created on Railway** |
| 50-user capacity | OPEN | GAP-011 (P0), GAP-041. Phase 2 (2026-11-09): admission caps are divided among the gateway's workers and daily quotas are shared across processes (FSA-025) -- built and tested, still unmeasured on the live topology |
| Phase 2 core reliability (audit FSA-010/011/012/015/016/018/025/041/046/059/066/073/091/092/093/094/100/110/111/112) | BUILT and TESTED locally (pytest, legacy unittest, real PG16 for the Postgres-backed items, `dart test` 274, `flutter test` 630); **not CI-run, not deployed** | Branch `anas/magical-bell-e9spvh` after #229: nginx-only forwarded headers, production CORS, dev/prod parity, streamed `fetch_url`, Ollama `done_reason`, `forget_source` vectors, per-turn tool cap, JSON-mode boot warning, finetune drain reuse, asyncpg pool bounds + computed connection budget, Whisper language allow-list, legacy-plaintext read counter, digest-pinned images + required Space revision, model revisions + Whisper warm-up, per-user session index + refresh-family revocation, audit/telemetry retention + pseudonymised erasure, off-host vector payloads without text, host-tool cancel frames + approval-window budget, client Idempotency-Key, cross-process admission/quota. Live-only items (FSA-084/085, load matrix, re-encryption run) remain the owner's |
| Real-model evaluation gates | OPEN | GAP-021; GAP-006 (`eval-ci.yml`'s deterministic gate runs on PR and push; only its `dispatch-run` job is manual -- FSA-120 correction 2026-11-08) |
| Schema / migrations | TESTED (local PG16, `0001`→`0019`); production at `0019` (2026-11-07 probe). One schema authority (CB-20) TESTED locally and in CI (#225, `postgres` job green 2026-11-09), **not deployed** | Alembic `0001`→`0019`, single head. F-27: `0016` baselines the 16 runtime-only tables. **CB-20: the adapters no longer issue DDL**: no `CREATE TABLE IF NOT EXISTS` on first use, no runtime vocabulary seeding. Before removing it, a fresh PG16 at `head` and the same database after every adapter's old bootstrap gave identical `pg_dump --schema-only` and identical seed rows. `tests/architecture/test_no_runtime_ddl.py` forbids DDL under `aura_core` and requires a migration for every `aura_*` table the code queries. `tests/load/test_migration_roundtrip_postgres.py` undoes and re-applies all 19 revisions; `0017`–`0019` restore their parent exactly, and `0007`/`0016` keep only their named pre-existing tables. Consequence: an unmigrated database now fails requests, not just `/health/ready` |
| Personal-OS single store (ADR-032, CB-28 / F-18) | BUILT and TESTED locally (pytest, legacy unittest, real PG16); **not deployed, not CI-run** | Owner chose Option A (server-held per-user data key) on 2026-10-31. The agent's `create_/list_/update_/delete_*` tools now read and write the synced `tasks`/`calendar_events`/`finance_records`/`health_records` rows, encrypted `gcm2` with the user's data key; `GET /api/v3/keys/data` hands that key to the user's devices. **The synced fields are no longer zero-knowledge** (threat-model table in ADR-032). Old rows: `scripts/move_personal_os_records.py` (not yet run anywhere) |
| Workspaces / team scopes (SaaS P1-A, ADR-034) | BUILT and TESTED locally (pytest 4915 passed, legacy unittest, real PG16 contract + migration round-trip); **not CI-run, not deployed** | Branch `claude/saas-production-readiness-f5i61d`: migration `0020` (workspaces, members, invites), `TenancyService`, `X-Aura-Workspace` resolved per request, `/api/v3/workspaces` routes, knowledge/graph/CAG scoped by `Principal.knowledge_owner` (AST fitness test), HTTP isolation + IDOR scenarios. Also `/metrics` now fails closed in production without `AURA_METRICS_TOKEN`. No client/console screens, no invite email, no RLS yet |
| Customer sign-in (SaaS P1-B) | BUILT and TESTED locally; **not CI-run; inert in production until the owner steps in `docs/deployment/customer-sign-in.md` are done** (no `FIREBASE_CREDENTIALS_JSON` on Railway) | `POST /api/v3/auth/consumer/token` (no attestation, `channel=consumer_web` capped at `User` at issue/refresh/per request, 10/min/IP/worker); `email_verified` on the access token, teams require it; `scripts/import_users_to_firebase.py` (subject-preserving, dry-run default; dry run against a local PG16 quoted in the plan ledger). `tests/unit/test_consumer_web_sign_in.py`, `tests/unit/test_import_users_to_firebase.py` |
| Plans and metering (SaaS P1-C) | BUILT and TESTED locally (pytest incl. real-PG16 contract with 20-way concurrency, migration `0024` round-trip); **not CI-run, not deployed** | Free/Pro/Team catalogue as data (`AURA_PLAN_CATALOGUE`, placeholder numbers until the owner prices them); per-workspace monthly messages, Engineer gate, storage, team seats; 402 `plan_limit`; `GET /workspaces/{id}/plan`, SuperAdmin `PUT /admin/workspaces/{id}/plan`. Live voice turns not metered yet. `docs/deployment/plans-and-limits.md` |
| Subscriptions and Fawry billing (SaaS P1-D1) | BUILT and TESTED locally (pytest, real-PG16 store contract, migration `0025` round-trip, Fawry wire format against httpx.MockTransport); **off by default, not proven against Fawry staging, not CI-run, not deployed** | Reference-code checkout, signature-verified Server Notification V2, saved-card renewals, grace and expiry, reconciliation; hourly `scripts/run_billing_cycle.py`. Fawry signature formulas need confirming in the merchant's staging account; first card capture not built. `docs/deployment/billing-fawry.md` |
| App Store / Google Play subscriptions (SaaS P1-D2) | BUILT and TESTED locally (pytest: Apple JWS chain against a generated root plus the pinned Apple G3 pin, Play API via httpx.MockTransport, service and HTTP tests, real-PG16 contract, migration `0026` round-trip); **off until configured, not proven against App Store or Play sandbox, not CI-run, not deployed**; the app's purchase screens are Plan F | Binding token, verified App Store Server Notifications V2 and Play RTDN, mapped onto the same subscription record. `docs/deployment/billing-stores.md` |
| Desktop Engineer for customers (SaaS P1-E) | BUILT and TESTED locally (pytest: entitlement, turn capability, daemon-socket recheck on downgrade, desktop sign-in/refresh/pairing gates and limits, customer session refused by console routes; legacy unittest unchanged); **off by default (`AURA_ENGINEER_FOR_CUSTOMERS`), not CI-run, not deployed, not tried with a real desktop app and daemon** | Pro/Team customers sign in on desktop and pair `aura_hostd`; their sessions are capped at `User`; startup refuses the switch beside the server sandbox, a shared GitHub token, MCP or relaxed confirmation. ADR-011 "Customers", `docs/deployment/desktop-engineer-customers.md` |
| Customer surfaces, backend (SaaS P1-F1) | BUILT and TESTED locally (pytest incl. real-PG16 contracts, migrations `0027`/`0028` round-trip, IDOR entries); **email off by default (`AURA_EMAIL_ENABLED`), no provider chosen, not CI-run, not deployed** | Invite/receipt/renewal/past-due email over SMTP; shared team chats; account deletion refused while owning a team; operator Customers API and audited grace extension. App screens are F2, console page F3. `docs/deployment/email.md` |
| Learning / fine-tuning | BUILT | GAP-017; GAP-018 fixed in code (`finetune.py` records a failed train as failed; FSA-120 correction 2026-11-08), `built_not_proven` |
| Voice | BUILT | GAP-020: speech-to-text default is now Whisper `small` (multilingual; FLEURS WER English 6.46%, Egyptian Arabic 24.96%, `docs/reports/STT_WER_ARABIC_ENGLISH_2026-11-17.md`), branch `anas/kind-rubin-dpm0sq`, unmerged; built, not proven until measured on a deploy target |
| Arabic text handling and reply language (Arabic plan, stage 1) | BUILT; TESTED locally 2026-11-17, not CI-run (CB-44), not deployed | `aura_core/text/`: BM25 tokenizer, token counter, chunker, groundedness guard, complexity, adaptive routing and curation read Arabic; migration `0022` (`users.language`, from `X-Client-Language`); persona prompts tell an Arabic reply to be Egyptian (English prompts unchanged). Whether a real model follows the line is untested. Stage 3: insights and the daily briefing are written in `users.language`. Stage 4: search defaults to `multilingual-e5-small` plus the `either` relevance gate (recall@5 English 0.70 → 0.95, Arabic-asks-English 0.10 → 0.90; `docs/reports/ARABIC_RETRIEVAL_EVAL_2026-11-17.md`), needs `scripts/rebuild_index.py` after deploy; OCR `ara+eng`. `docs/plans/arabic-language_plan.md` |
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
- 2026-11-17, branch `anas/kind-rubin-dpm0sq`, unmerged: Arabic plan stages 1-4 (above). Adds migration `0022`; after it merges, `/health/ready`'s `schema.revision` should read `0022` (the first merge adding a migration since the pre-deploy fix).
- #252 (open, 2026-11-17): multi-provider offsite backups with a two-copy quorum, configuration
  checks, `backup-fetch.sh`, and the `AuraBackupUnderReplicated` / `AuraBackupOffsiteStale`
  alerts. Deploy-stack only; no backend code. Local checks pass; CI ran with no logs (CB-44).

- 2026-11-21, merged as #275 and #276 (live at `85258222`, `schema.revision 0031` checked on
  `/health/ready`): console control center stage 1
  (ADR-037, GAP-067; plan `docs/plans/console-control-center-plan.md`). It adds:
  - `/api/v3/admin/control/*`: overview, settings catalog, changes, step-up challenge, redeem
    and revoke, and platform credential and disconnect. Every route is SuperAdmin-only, and
    every write is step-up gated and typed-confirmed.
  - Migration `0031` (four tables); after it merges, `/health/ready`'s `schema.revision` should
    read `0031`.
  - Platform tokens are sealed in the vault under `AURA_SECRETS_ENCRYPTION_KEY`. A missing or
    malformed key disables the control center, not the gateway.

  TESTED locally:
  - default `pytest`: 6576 passed, 12 failed, 50 skipped. The 12 failures are already on `main`
    from the `tools/open` work: `test_open_tools_fixes.py` (10), `test_analysis_tools.py` (1),
    and `test_no_runtime_ddl.py` (`database_intelligence_tool.py:168`);
  - legacy `unittest`: 148 OK;
  - `tests/load/test_control_store_postgres.py` and the migration round trip on PG16.

  Not CI-run (CB-44). Not proven: no real Google step-up or platform call has been made.

- 2026-11-22, merged as #278 (live at `6aab72f7`): control center stage 2, Railway
  (ADR-037, GAP-067). It adds:
  - `/api/v3/admin/control/railway/*`: services, variables (secret values never returned),
    deployments, logs (secrets masked), a dry-run validate, and the step-up-gated
    set-variables, redeploy, restart and rollback actions.
  - `adapters/railway_platform.py`: the GraphQL client, checked against Railway's live schema.
  - `adapters/settings_probe.py`: candidate settings validated in a child process.
  - The rollback Reconciler is started by the gateway in staging/production
    (`AURA_CONTROL_RECONCILER`).
  - No migration.

  TESTED locally:
  - default `pytest` after merging `main` at `7591762f`: 6952 passed, 0 failed, 55 skipped
    (`main` had fixed the 12 `tools/open` failures);
  - legacy `unittest`: 148 OK.

  Not CI-run (CB-44). Not proven: no live Railway call has been made with a real project token.

- 2026-11-22, merged as #278: control center stage 3, the GPU Space (ADR-037,
  GAP-067). It adds:
  - `/api/v3/admin/control/space/*`: status, variables (watched for
    `AURA_CONTROL_SPACE_WATCH_S`, 20 minutes, and put back if the Space errors), write-only
    secrets journaled as not restorable, pause/resume, restart, rebuild, hardware, sleep time
    and logs.
  - `adapters/hf_space.py` on `huggingface_hub` 1.28.
  - The reconciler now watches the Space too.

  TESTED locally after merging `main` at `f880826a`: `pytest` 7035 passed, 0 failed, 55
  skipped; legacy `unittest` 148 OK. Not CI-run (CB-44). Not proven: no live Hub call has been
  made from the gateway.

- 2026-11-22, merged as #278: control center stage 4, provider keys (ADR-037,
  GAP-067). It adds:
  - `/api/v3/admin/control/keys/*`. Each key is a gateway variable change, checked, watched and
    rolled back by stage 2's machinery.
  - Replace (any slot).
  - Rotate both sides (the GPU Space's `VLLM_API_KEY` and the gateway's variable, with the
    reconciler's new companion restore).
  - Cloudflare Workers AI create and revoke (`adapters/cloudflare_tokens.py`).

  TESTED locally (`main` at `f880826a`): `pytest` 7069 passed, 0 failed, 55 skipped; legacy
  `unittest` 148 OK. Not CI-run (CB-44). Not proven: no live key rotation has been made.

- 2026-11-22, branch `anas/laughing-hamilton-glfevj`, unmerged: control center stage 5 (ADR-037,
  GAP-067; every stage is now built). It adds:
  - `/api/v3/admin/control/monitoring`: Grafana Cloud. Alert rule groups are applied to the
    stack's ruler with per-group results; the scrape job and uptime checks are shown as exact
    values with their live state; firing alerts are listed.
  - `/api/v3/admin/control/vector`: Qdrant Cloud. Health, collections, snapshots, and a restore
    that takes a safety snapshot first.
  - A packaged copy of `deploy/monitoring/alerts/aura.yml`, kept byte-identical by a test.
  - Grafana tokens are verified on Connect.

  TESTED locally (`main` at `6aab72f7`): `pytest` 7108 passed, 0 failed, 55 skipped; legacy
  `unittest` 148 OK. Not CI-run (CB-44). Not proven: no live Grafana or Qdrant call has been
  made from the gateway, and the Cloud restore path is unconfirmed.

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
