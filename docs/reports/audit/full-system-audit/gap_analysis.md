# Gap analysis — consolidated, deduplicated findings

**Audit:** AURA AI full-system engineering audit · **Baseline:** `main` @ `ffbbb9f` · **Date:** 2026-10-31 · **Author:** lead auditor (consolidating passes A, B, C, D, E1, E2, E3, F1, F2, G).

Every `FSA-nnn` entry below is one root cause. The `Source` column names the pass-local IDs it merges (full evidence, line citations and confidence per source live in that pass's report and `findings_<pass>.json`). Severity follows the brief (`P0` critical … `P4` informational). Effort is relative: `S` (≤1 day), `M` (days), `L` (weeks). Priority is the remediation phase in `remediation_roadmap.md`.

Status of the whole system against these gaps: **no P0 found; 9 P1; 25 P2; 38 P3; 12 P4** (84 consolidated entries, from 117 pass-level findings; G-pass testing findings are folded into group 7). The two P1 security items (FSA-001, FSA-002) are not concealed by any score: see `readiness_scorecard.md`.

Fields per entry: **Current** behavior → **Expected** behavior · **Root cause** · **Impact** (U = user, S = security, R = reliability) · **Deps** · **Fix** · **Accept** (acceptance criteria) · **Tests** · Effort · Phase · Confidence.

---

## Group 1 — Critical correctness and security defects

### FSA-001 · P1 · Taint is not inherited across `delegate_to_agent`
Subsystem: tools/delegation, kernel · Source: C-01 · Evidence: `aura_core/tools/delegation.py:84-96,134-148`; `aura_core/kernel/kernel.py:111-170,399-426`; `aura_core/tools/records.py:10-14`; `aura_core/agents/registry.py:155-181`; `tests/unit/test_delegation.py` has no taint case.
**Current:** a parent turn tainted by a quarantined chunk / `fetch_url` output may call `delegate_to_agent` (not side-effecting); the child `TurnRequest` carries no taint, so `create_*/update_*/delete_*` Personal-OS tools in `task_planner`, `finance_advisor`, `health_companion` run without confirmation. → **Expected:** ADR-006 holds across kernel hops. · **Root cause:** taint derived only from the child's own inputs. · **Impact:** S high (prompt-injection → unconfirmed server-side writes), U medium. · **Deps:** none. · **Fix:** add `inherited_taint` to `TurnRequest`, set from `context.tainted` in the delegation tool, OR into child taint. · **Accept:** tainted parent + child side-effecting call ⇒ `confirmation_required`. · **Tests:** `tests/unit/test_delegation.py::test_tainted_parent_requires_confirmation_in_child`. · Effort S · Phase 0 · Confidence High (code) / Med (practical exploitability).

### FSA-002 · P1 · A GCP service-account key and a backend `.env` are recoverable from git history; no rotation recorded
Subsystem: secrets · Source: F1-01 · Evidence: `git log --all -- aura_backend/credentials.json` → `5b37323`; `aura_backend/.env` → `d2b92f0`; deleted in `044f417`; CB-06 covers only the `GOCSPX-` secret.
**Current:** anyone with repo read access or an old clone can recover the private key and historical env values. → **Expected:** keys provably dead, rotation dated in `docs/reports/`. · **Root cause:** early direct pushes. · **Impact:** S high. · **Fix:** rotate the service account key and every value in that `.env`; record; consider history rewrite while `main` is unprotected and collaborators are few. · **Accept:** rotation record committed; `git log -p` secrets fail against the live project. · Effort S · Phase 0 · Confidence High (presence) / rotation UNKNOWN.

### FSA-003 · P3 · Stale plaintext secrets sit beside the checkout and leak into desktop builds
Source: A-06, E3-13 · Evidence: untracked `deploy/.env:4` (`GOOGLE_DESKTOP_CLIENT_SECRET`), `deploy/.env.bak-20260914-134105`, `aura_backend/credentials.json`; `scripts/deploy_client_desktop.*` read `deploy/.env` into `--dart-define`; `tests/architecture/test_no_committed_client_secrets.py:200` fails on these files.
**Current:** CB-06 repo leak is fixed but desktop builds made on this host compile a client secret in again; the fitness test cannot tell tracked from untracked. → **Expected:** no secret in any desktop build; test scoped to `git ls-files`. · **Fix:** delete the key/backup locally, make the build scripts refuse a non-empty secret, scope the test. · Effort S · Phase 0 · High.

### FSA-004 · P2 · Desktop app sign-in mints a 30-day, unscoped, non-refreshable token that can fetch the user's data key; revoke is unreachable
Source: E3-06, F1-04, E3-08 · Evidence: `api/v2/auth.py:242-267`; `services/jwt_auth.py:46,263-284,435-442`; `aura_core/api/v3.py:1192-1252`; no client caller of `/api/v3/auth/desktop/devices/revoke`; `services/device_pairing.py:141-166` (approve shows no device identity).
**Current:** a stolen desktop token is a month-long full session exposing Personal-OS plaintext via `/keys/data`; pairing approval shows no machine identity. → **Expected:** short access + device-bound refresh for the app; 30-day scoped form only for the daemon; device list + revoke in Settings; approve names the device. · **Impact:** S high, U medium. · **Fix:** issue a normal token pair from `/desktop/login`; add `device_name` to pairing; wire `GET /desktop/devices` + revoke. · **Accept:** desktop access token TTL ≤ 15 min; revoke reachable from the app. · **Tests:** `tests/unit/test_desktop_oauth_audience.py` extension; widget test for the devices list. · Effort M · Phase 0 · High.

### FSA-005 · P2 · On Railway the legacy per-IP limiter and audit `source_ip` see the proxy address; uvicorn ignores forwarded headers
Source: A-04, F1-03, F2-14 · Evidence: `aura_backend/Dockerfile:147` (`--proxy-headers`, no `--forwarded-allow-ips`); `infrastructure/http_limits.py:67`; `aura_service.py:235`; `aura_core/api/v3.py:318,409,1233`; `aura_core/security/rate_limit.py:202-229` (v3 limiter unaffected).
**Current:** all users likely share one 100 req/min bucket on Railway; audit rows record the proxy IP; scheme-derived decisions see plain HTTP. → **Expected:** one limiter keyed on the trusted-hop client IP on every target. · **Fix:** remove `RateLimitMiddleware` (the aura_core limiter is wired) or key it on `extract_client_ip`; set `--forwarded-allow-ips` for the Railway edge. · **Accept:** two source IPs get independent buckets live; audit rows carry client IPs. · **Tests:** `tests/unit/test_deploy_robustness.py` pins the CMD flag. · Effort S · Phase 0 · Med (edge peer address not observed).

### FSA-006 · P2 · In a trusted root `run_command` has no path confinement, always runs in root[0], and the denylist is a bypassable regex
Source: E3-05, E3-07 · Evidence: `aura_hostd/lib/src/command_policy.dart:21-47,160-162`; `task_handler.dart:241-281`; `aura_core/tools/coding.py:602-605` (no `cwd` sent); ADR-011 §containment claims.
**Current:** an untainted Engineer turn in a trusted root runs any command as the user's OS account without prompt (`rm -r -f`, `find -delete`, `python -c` bypass the patterns), in whichever root is first. → **Expected:** destructive verbs keep argument containment even in trusted roots; the IDE's active root is passed as `cwd`. · **Fix:** apply the untrusted tier's containment to a documented verb set; thread the active root through `ToolContext.metadata`; label the trust toggle. · **Accept:** `rm -rf ../outside` in a trusted root is refused; `run` executes in the named root. · **Tests:** `aura_hostd/test/command_policy_test.dart` cases. · Effort M · Phase 0 · High.

### FSA-007 · P2 · Live-voice PCM frames are not size-validated
Source: E1-01 · Evidence: `aura_core/api/voice_live.py:209-211`; `aura_core/voice/session.py:131-153`; `voice/vad.py:42-81`.
**Current:** an authenticated client can buffer ~750 oversized frames per utterance; VAD iterates every sample on the event loop. → **Expected:** per-frame byte check and per-utterance byte ceiling. · **Fix:** validate `len(frame)` (close 4400), cap bytes in `push_pcm`. · **Tests:** unit test with an oversized frame. · Effort S · Phase 0 · High.

### FSA-008 · P2 · Backup container mounts the entire `deploy/secrets` directory, including the KEK that wraps every user's ADR-032 data key (CB-08 still open)
Source: F1-02 · Evidence: `deploy/docker-compose.prod.yml:1237,1215,1229-1231,220-284,1240`.
**Fix:** per-file mounts or a `secrets/backup/` subdirectory; compose fitness test that no service bind-mounts `./secrets` wholesale (prod and dev in the same commit). · Effort S · Phase 0 · High.

### FSA-009 · P2 · `/metrics` is unauthenticated unless `AURA_METRICS_TOKEN` is set; Railway doc never asks for it
Source: F2-07 · Evidence: `aura_core/api/v3.py:1764-1769`; `aura_core/config/settings.py:985-990`; `docs/deployment/control-plane.md:56-96`; code comment recording a live world-readable probe.
**Fix:** set the token on Railway; document; fail `validate()` when the app is the edge and the token is unset. · **Accept:** live `/metrics` → 401 without the token. · Effort S · Phase 0 · Med (live config UNKNOWN).

### FSA-010 · P3 · Production CORS default includes localhost origins with credentials (SEC-12)
Source: F1-05 · `deploy/docker-compose.prod.yml:516`; `aura_service.py:238-254`. Fix: production default names production origins only. Effort S · Phase 2 · High.

### FSA-011 · P3 · `fetch_url` buffers the whole body before truncating
Source: F1-06 · `aura_core/tools/builtin.py:461,478-496`. Fix: streamed read with a byte budget. Effort S · Phase 2 · High.

### FSA-012 · P3 · VPS gateway trusts `X-Forwarded-For` from any container (`--forwarded-allow-ips='*'`)
Source: F1-10 · `docker-compose.prod.yml:404`, `.dev.yml:277`. Fix: trust nginx's address only, both files. Effort S · Phase 2 · High.

### FSA-013 · P3 · hostd `control.json` bearer written with the process umask
Source: F1-11 · `aura_hostd/lib/src/control_server.dart:13`. Fix: write temp → restrict → rename. Effort S · Phase 2 · Med.

### FSA-014 · P4 · hostd loopback `/status` is unauthenticated and lists granted roots and backend URL
Source: E3-15 · `control_server.dart:7-14,138,232-241`. Fix: liveness fields only. Effort S · Phase 6 · High.

### FSA-015 · P3 · Session/device revocation `SCAN`s all Redis keys; no refresh-token reuse (family) revocation
Source: F1-08 · `services/jwt_auth.py:319-333,379-403`. Fix: per-uid index sets; family revocation on replay. Effort M · Phase 2 · High.

### FSA-016 · P3 · Audit log and turn telemetry keep email and source IP forever and survive erasure
Source: F1-07 · `alembic/versions/0016_runtime_tables_baseline.py:64-75`; `api/admin_platform.py:234-242` (`NOT_ERASED`); no purge job. Fix: beat purge with configurable retention; pseudonymise actor on erasure; document. Effort M · Phase 2 · High.

### FSA-017 · P3 · `_READ_TOOLS` grants `remember_fact` (a durable write) to every persona, including the "read-only" `decision_agent`
Source: C-04 · `aura_core/agents/registry.py:129,200-204`; `tools/builtin.py:269-281`. Fix: move it out of `_READ_TOOLS`. Effort S · Phase 0 · High.

### FSA-018 · P2 · Document chunks, every Qdrant payload and memory `subject` are plaintext, including on third-party Qdrant in the Railway design (CB-22)
Source: D-06, F1-09 · `aura_core/knowledge/index_manager.py:277-288`; `adapters/postgres_store.py:184-197,831-895`; `memory/engine.py:177-184`; `docs/deployment/control-plane.md:69-70`.
**Current:** a Qdrant Cloud operator or API-key leak exposes every user's document text. → **Expected:** a recorded decision per target; no readable content in off-host vector payloads. · **Fix:** `vector_payload_content` setting (off when Qdrant is off-host) and resolve chunk text from the owner-scoped store (engine already does so for lexical-only hits); seal `aura_chunks.content` if confidentiality is wanted. · Effort M · Phase 2 · High.

---

## Group 2 — Production blockers

### FSA-020 · P1 · Production ships every `main` push with no CI gate and no branch protection
Source: F2-01 (CB-02) · `.github/workflows/control-plane-deploy.yml:3-5,24-28`; `eval-ci.yml:17-20`; status docs. **Fix:** branch protection with required checks; disable Railway auto-deploy or enable "Wait for CI"; `vars.CONTROL_PLANE_DEPLOY_REQUIRED=true`. **Accept:** a failing PR shown blocked; a Railway deploy whose trigger is the workflow. Effort S (owner action) · Phase 0 · High.

### FSA-021 · P1 · GitHub Actions is not assigning runners (CB-44); every gate is inert and GAP-043 still says `proven`
Source: F2-02 · DOCUMENTED ONLY (`docs/status/BACKEND_STATUS.md:18-21`). **Fix:** owner restores Actions; re-run all CI on `main`; mark GAP-043 regressed. Effort S (owner) · Phase 0 · Med.

### FSA-022 · P1 · Railway ignores `aura_backend/railway.json`; deploy behaviour lives only in the dashboard
Source: F2-03 · `docs/deployment/control-plane.md:195-199`; `railway.json:8-14`; `tests/unit/test_release_identity.py:240-265` pin a file production does not read. **Fix:** link the config file (or delete it and retarget tests); record a dashboard snapshot. **Accept:** next deploy log shows the `/health/live` check and the pre-deploy script. Effort S (owner) · Phase 0 · Med.

### FSA-023 · P1 · VPS deploy path dead on a stale host-key pin; the VPS's running revision, secrets exposure and provenance of the key change are unknown
Source: F2-04 (CB-03) · `deploy/known_hosts:8-15`; `deploy-backend.yml:85-95`; tracked `deploy/vps_known_hosts.tmp`. **Fix:** confirm fingerprint out of band; decide VPS future; re-pin by PR or decommission and rotate any secret shared with Railway. Effort S–M (owner) · Phase 0 · Med.

### FSA-024 · P1 · On the live target every SENSITIVE generation has no eligible provider while `hfspace` is 503
Source: B-01, F2-10 · `aura_core/llm/router.py:325-327`; `config/settings.py:485` (hosted ceiling `internal`); `llm/sensitivity.py:49-50`; `kernel/kernel.py:1163-1196`; `tools/registry.py:405`; `BACKEND_STATUS.md:24,35-36`.
**Current:** turns that draw on memory, documents, goals, non-clean history, tool output, Engineer or image content, and all unclassified background generations, route only to `hfspace`; once its breaker opens they fail with `AllProvidersFailedError` (503). Only context-free chat works. → **Expected:** a healthy sensitive-capable provider, or an explicit degraded mode visible on `/health/ready` and alerts. · **Deps:** owner decision (ADR-030 §4: do not silently raise `hosted1` to sensitive). · **Fix:** restore the Space on an always-on flavor or add a second sensitive-capable node; readiness reports sensitive capability distinctly. · **Accept:** a RAG turn and a tool turn succeed live; readiness distinguishes the state. Effort M · Phase 0 · High (code) / Med (live).

### FSA-025 · P1 · Admission and hosted quota limits are per process while the gateway runs 4 uvicorn workers plus Celery workers (GAP-011/041)
Source: B-02 · `aura_core/llm/admission.py:27-31`; `llm/quota.py:36-40`; `docker-compose.prod.yml:403,434,440,782,952`. **Fix:** Redis-backed cross-process admission/quota (the module docstring names it) or `AURA_WORKERS=1` for inference-facing processes; run `tests/load` on the real topology. **Accept:** measured concurrency matrix committed. Effort M · Phase 2 · High.

### FSA-026 · P1 · The host-agent registry is per-process while the image defaults to 4 workers; on Railway the Engineer has no fallback runner
Source: E3-01, E3-14 · `aura_core/adapters/host_agent.py:156-181,383-391`; `Dockerfile:147`; `container.py:1438-1462`; ADR-011:400-412. **Current:** desktop tool calls served by a worker other than the one holding the WebSocket fail "no desktop app is connected and no server-side sandbox is configured". **Fix:** `AURA_WORKERS=1` on Railway with a startup assertion when the registry is enabled; then Redis pub/sub routing. **Accept:** an Engineer turn succeeds from every worker. Effort S then M · Phase 0/2 · High (code) / Med (live).

### FSA-027 · P2 · Whether Celery workers and beat exist on Railway is undocumented; durable background work may be silently absent
Source: A-03, F2-05 · `railway.json:4-14` (one service); `container.py:145-155` (DurableEventBus → outbox); `celery_app.py:157-216`; `tasks.py:199-450`. **Fix:** enumerate Railway services and record them; create the three per `control-plane.md` §2 if absent; expose beat heartbeat age and outbox backlog on `/admin/status` and fail the deploy health step on staleness. Effort S–M · Phase 0 · Med.

### FSA-028 · P2 · No metrics scrape, alerting or log retention for the production target; the alert catalogue is VPS-only
Source: F2-06 · `deploy/monitoring/prometheus.yml:6-21`; `deploy/monitoring/alerts/aura.yml`. **Fix:** Prometheus + Alertmanager as Railway services with the existing configs; record one live scrape showing `aura_http_*` and `aura_outbox_*`. Effort M · Phase 6 · High.

### FSA-029 · P2 · No repo-owned backup, restore drill or RPO/RTO for the Railway Postgres/Redis/Qdrant
Source: F2-08 (GAP-028) · `deploy/scripts/backup.sh`; `docs/production/DISASTER_RECOVERY.md:23-49`. **Fix:** run `backup.sh` as a Railway cron against the live URLs with an age recipient and S3 target; execute `restore-drill.sh` and file the report. Effort M · Phase 6 · High.

### FSA-030 · P2 · `AURA_DATA_ENCRYPTION_KEYS` (now wrapping every user's ADR-032 data key) is a lone dashboard variable with no escrow and is missing from the Railway runbook
Source: F2-09 · `aura_core/config/settings.py:1878-1887`; `docs/deployment/control-plane.md:54-96`. **Fix:** seal the keys offline; list every production-required variable in `control-plane.md` with a test that the doc covers them. Effort S · Phase 0 · High.

### FSA-031 · P2 · The non-semantic hash embedder is the default, is not refused in production despite the docstring, and nothing reveals the live embedder
Source: D-04 · `aura_core/config/settings.py:626-627,680-681`; `llm/embeddings.py:63-72`; no check in `validate()`; `/health/ready` silent. **Fix:** refuse `hash-embed-v1` in staging/production; add embedding `{provider, model, dimension}` to readiness; document `AURA_EMBEDDING_*` for Railway and verify the live value. Effort S · Phase 0 · High (code) / live UNKNOWN.

### FSA-032 · P4 · ADR-032 key fetch and the agent's Personal-OS tools need migration `0019` live; production is claimed at `0016`
Source: E2-08 (CB-28) · `aura_core/api/v3.py:1192-1252`; `alembic/versions/0019_user_data_keys.py`. **Fix:** no code change; verify `/health/ready` shows `0019` after the next deploy and run `scripts/move_personal_os_records.py --dry-run`. Phase 0 · High.

---

## Group 3 — Broken integrations

### FSA-040 · P2 · D8 per-task worktree isolation is never requested by the client; the Agent Review panel is always empty
Source: E3-03 · `aura_core/api/v3.py:234-237`; `aura_client/lib/core/ai/online_agents/sse_agent_client.dart:131-142` (no `desktop_task_isolation`); `aura_hostd/lib/src/isolated_task_registry.dart:76-154`; `control_center_panel.dart:142`. **Fix:** set the field from the IDE chat; cover in `sse_agent_client` tests; correct `DESKTOP_APP_STATUS.md` until wired. Effort S · Phase 3 · High.

### FSA-041 · P2 · Server-side tool timeouts (15 s write, 60 s Engineer deadline) cut off the daemon's 5-minute approval window; a late approval still executes
Source: E3-02 · `kernel/kernel.py:1499`; `tools/registry.py:361-376`; `tools/coding.py:483,504,539,581`; `aura_hostd/lib/src/approval_queue.dart:46`; `task_handler.dart:110-118,193-205`. **Current:** the model is told the write timed out and may retry; a later Approve writes/runs anyway; `run_command` > 60 s always times out despite its 605 s spec. **Fix:** send a cancel frame on timeout and mark the approval cancelled; give the Engineer context a deadline ≥ the longest host-routed tool. **Tests:** backend test with a fake daemon that answers after the tool timeout. Effort M · Phase 2 · High.

### FSA-042 · P2 · The operator console is unreachable on every live target; 32 admin routes (incl. ADR-029 service keys and all Track AP) are raw-API only
Source: E2-04 (WEB_CONSOLE_STATUS) · `docs/deployment/` never mentions the console; `docker-compose.prod.yml:334`; `admin_service_keys.py:53-72`; `admin_adaptation.py:99-348`; mock gateway covers 11/36 proxied paths. **Fix:** owner decides hosting (Railway service or VPS once deploys work); wire service-keys, job polling, scorecard, refresh/latest; extend the mock gateway. Effort M · Phase 3 · High (code) / Med (live).

### FSA-043 · P3 · A legacy learning/ingestion path is still mounted and enqueues tasks on a queue no worker consumes; workers import the legacy RAG service at module scope
Source: A-02, A-07, D-15 · `tasks.py:8,50,61,110`; `celery_app.py:131-149` (no `task_routes`); `aura_service.py:127,137` (`api/learning.py`); `services/drive.py:164-166`; `services/rag.py:35-51,374`. **Current:** `POST /api/v3/learning/build_dataset` returns a task id that never runs; un-curated datasets bypass ADR-008; every worker opens (or silently creates) a Qdrant client at import. **Fix:** remove or 410 `/api/v3/learning/*` and `build_lora_dataset_task`; route `ingest_pdf_task` through `KnowledgeEngine.ingest`; add `queue=` or a test that every registered task's queue is consumed by a compose worker. Effort S · Phase 3 · High.

### FSA-044 · P3 · Daemon pairing succeeds for accounts the host socket will refuse (4401 with no reason); the app has no paired-device list or revoke
Source: E2-05, E3-08, E3-12 · `aura_core/api/host_agent.py:53-54,87-92`; `settings.py:1021,1057` (`AURA_ENGINEER_AGENT_ROLES` default empty); no caller for `devices/revoke`; `rpc_client.dart:89-93,140-147` (never sends `reauth`). **Fix:** check `engineer_agent` at `/pair/approve` (403 `engineer_agent_required`); device list + revoke in `host_panel.dart`; surface 4401 as "pair again". Effort M · Phase 3 · High.

### FSA-045 · P3 · 47 routes have no shipped consumer (backlog says 16): `/tools`, `/agents/runs`, `/knowledge/graph`, `/learning/*`, 25 Track AP admin routes
Source: A-08, E2-06, C-07 · `docs/guides/api-reachability.md:14-127`; CB-30. **Fix:** owner decision per group (wire / API-only with owner and date / remove); update CB-30. Effort S–M · Phase 3 · High.

### FSA-046 · P3 · No client sends `Idempotency-Key`; the server-side idempotency machinery has zero callers (GAP-013 text STALE)
Source: E2-03 · `rg -i idempotency aura_client` → 0; `aura_core/api/idempotency.py:57`; `v3.py:324-330`. **Fix:** send the header from `KnowledgeCenter`, `MemoryRepository`, `FeedbackRepository`, `EngineerWorkspaces`; pin with a test. Effort S · Phase 2 · High.

### FSA-047 · P3 · `fetch_url`, `list_files`/`read_file` and `get_weather` builders are never wired by `container.py`; `research_agent`'s only network tool cannot be enabled
Source: C-08 · `tools/builtin.py:570-636`; `container.py:1400-1412`; `agents/registry.py:186-189`. **Fix:** wire the three parameters from settings or drop them from the grant. Effort S · Phase 3 · High.

### FSA-048 · P3 · Deploy Control Plane's readiness and revision probes cannot pass for any plausible `RAILWAY_HEALTH_URL`
Source: F2-11 · `control-plane-deploy.yml:130,149`; `v3.py:1734-1750`. **Fix:** derive the origin, poll `/health/live`, verify `/health/version`; unit-test the derivation. Effort S · Phase 1 · Med.

### FSA-049 · P3 · The dead `dev.aureon-ai.tech` preset is still selectable; switching clears tokens without revoking them
Source: E2-07 · `aura_client/lib/core/config/backend_url_settings.dart:21`; `backend_url_policy.dart:17`. **Fix:** drop or re-point the preset; probe `/health/ready` before switching. Effort S · Phase 6 · Med.

---

## Group 4 — Incomplete core features

### FSA-050 · P2 · The ten retrieval strategies, the adaptive router and GraphRAG are built and boot-validated but no turn or route uses them; the knowledge graph is written on every ingest and read by nothing in the request path (GAP-023)
Source: D-01 · `container.py:384-401,576`; `kernel.py:1084`; `v3.py:827`; `rag/facade.py:53-56`; `AURA_KNOWLEDGE_RETRIEVAL_STRATEGY` inert. **Knowledge-graph verdict: IMPLEMENTED BUT UNREACHABLE (written, never read in the request path).** **Fix:** pass `knowledge=self.retrieval` to `KernelDependencies` and `/knowledge/search` with a fitness test, or relabel the strategies and graph as not-in-path in ADR-005, GAP-023 and the prior review. Effort S (wire) · Phase 4 · High.

### FSA-051 · P2 · The model sees at most 2 chunks × 280 chars, ≤3 memories in 80 tokens and an 80-token CAG prefix; the global 400-token prompt budget sized for CPU Ollama also caps fast hosted providers
Source: D-05, B-10 · `context/engine.py:57-58,305-307`; `settings.py:714,735,840-842,818,639-641,693`; `container.py:242`; `knowledge/chunking.py:31` (~320-token chunks). **Current:** ~80 % of each retrieved chunk is discarded before the prompt; citations name text the model did not read; the eval gate scores ids, not the rendered prompt. **Fix:** per-provider/pool prompt budget; raise defaults for non-CPU providers; document the overrides for the hosted-primary topology; add an answer suite over the rendered prompt. Effort M · Phase 4 · High (behavior) / impact unmeasured.

### FSA-052 · P2 · Projects and Goals exist on the server (migration 0004, `models.py:191-232`) and in the client (`features/projects`, `features/goals`, synced via outbox) but are outside the agent's ADR-032 store, `RecordKind` and CRUD tools; goals reach the kernel as title-less snapshots
Source: E2-01 · `domain/personal_os.py:28-31`; `adapters/postgres_synced_personal_os.py:76-107`; `tools/records.py:162-195`; `adapters/postgres_goals.py:44-47`. **Personal OS verdict:** Tasks/Calendar/Finance/Health are end-to-end (client ↔ sync ↔ agent tools, pending migration 0019 live); Projects/Goals are app-only; Reminders/Notes/Income are fields of those tables or absent (see `client_api_reachability.md` §Personal OS). **Fix:** add `RecordKind.PROJECT/GOAL` + table rows + generated tools; decrypt `title_enc` under the user data key; cover in `test_personal_os_sync_integration.py` on real Postgres. Effort M · Phase 4 · High.

### FSA-053 · P2 · Nightly knowledge refresh promotion is not recall-gated: no evaluator is wired, `max_recall_drop` is dead config (ADR-007/008 claim otherwise)
Source: D-02 · `container.py:343-353`; `learning/knowledge_refresh.py:321-330`; `knowledge/index_manager.py:109-118`. **Fix:** wire `RetrievalEvaluator` with the committed `docs_corpus` suite; lifecycle test that a recall drop blocks promotion. Effort S · Phase 4 · High.

### FSA-054 · P3 · With an unchanged embedder the refresh "candidate" is the live collection: "never modified"/rollback do not hold and the corpus is embedded twice nightly
Source: D-03 · `knowledge_refresh.py:315-318`; `domain/knowledge.py:215-219`; `index_manager.py:208-247,334-335`. **Fix:** include the `GenerationId` in build names (real retention) or skip `build()` when identity is unchanged and document incremental refresh. Effort M · Phase 4 · High.

### FSA-055 · P2 · Fine-tuning never reaches traffic on any target: production uses `ScriptedTrainer` (`NOT_TRAINED`), a real LoRA adapter is unservable, the dataset store is ephemeral on Railway, and no weight-change run is committed
Source: B-05, D-13, D-14, D-18 (GAP-017) · `docker-compose.prod.yml:556,877-905`; `learning/finetune.py:121-140,364-398`; `adapters/local_lora_trainer.py:16-20,301-312`; `kernel/kernel.py:1990-2006` (`_servable_model_tag` discards path artefacts); `container.py:492` + `settings.py:1127` (`FileDatasetStore` at `/var/lib/aura/datasets`); `celery_app.py:202-206`; `admin.py:465-471`; `mlops/model_registry.py:238-251` (canary gate reads metrics nothing writes); `tests/training/test_lora_weights_change.py` opt-in only. **Training verdict: dataset curation + approval + registry exist; nothing modifies or serves weights in any deployment.** GAP-018's honesty bug is fixed in source (`finetune.py:444-448`). **Fix:** either (a) merge → GGUF → `OllamaProvider.create_model` → canary behind `RegressionGate`, with datasets in Postgres/object storage, or (b) relabel the capability as dataset preparation in status docs and the client; refuse promotion of non-servable artefacts in `ModelRegistry.transition`. Effort L (a) / S (b) · Phase 4 · High.

### FSA-056 · P2 · Image understanding (ADR-031) is implemented and tested with the echo provider but unreachable in every known deployment: no provider declares `text,image`, `AURA_VISION_ENABLED` is off, `hosted1` is third-party (refused), `hfspace` text-only and down; enabling vision without a VLM silently degrades to OCR-only
Source: B-09, E1-03, E1-07 · `aura_core/vision/engine.py:99-137`; `llm/router.py:309-316`; `docker-compose.prod.yml:467-468,547-549`; `v3.py:500-503,620-628`; no `phase-6-vision` report. **Video: NOT IMPLEMENTED (no ingestion, frame extraction or transcription path found).** **Fix:** pinned VLM pool on an always-on Space per `docs/deployment/inference-plane.md:118-138`; `mode` field (`vlm`/`ocr_only`) in capabilities and gate the attach UI on it; keep GAP-019 `built_not_proven`. Effort M · Phase 4 · High (code) / Med (live).

### FSA-057 · P2 · `write_files` (D6 atomic multi-file write) and every `mcp_*` tool are registered under `engineer_agent` but absent from the Engineer persona's grant, so the persona can never use them; tests bypass the grant with hand-built contexts
Source: C-02, E3-04 · `tools/coding.py:520-563,828`; `agents/registry.py:213-225`; `kernel.py:1468-1473`; `policy.py:86-87`; `tests/unit/test_engineer_tools.py:149-215`. **Fix:** add `write_files` and an `mcp_*` policy to the grant; test that the grant covers `build_sandbox_tools()+build_github_tools()` names. Effort S · Phase 3 · High.

### FSA-058 · P3 · Arabic is asymmetric: Arabic TTS ships, STT is `tiny.en` (English-only), OCR installs only `eng` (GAP-020)
Source: E1-05 · `settings.py:1491,1523,754,782`; `voice/session.py:196-203`; `Dockerfile:79`; `knowledge/documents.py:364-376`. **Fix:** multilingual Whisper size with a measured WER report; install `tesseract-ocr-ara`; validate `ocr_lang` against installed languages at build. Effort M · Phase 4 · High.

### FSA-059 · P3 · Live-voice `language` from the client start frame is passed unvalidated to Whisper
Source: E1-06 · `voice_live.py:162`; `session.py:196-219`. Fix: ISO-639-1 allow-list. Effort S · Phase 2 · Med.

### FSA-060 · P3 · Engineer tooling is reachable under the default `orchestrator` persona without the Engineer prompt or multi-pass loop
Source: C-03 · `agents/registry.py:144-148,226-232`; `v3.py:217`. Fix: explicit `allowed_tools` for orchestrator excluding capability-gated tools, or correct the comment. Effort S · Phase 4 · High.

### FSA-061 · P3 · hostd lifecycle: no restart-on-crash, no re-auth path, no bundled daemon, no installer/signing/updater (CB-26, M8.10)
Source: E3-12 · `aura_client/lib/core/hostd/hostd_supervisor_io.dart:25-38`; `rpc_client.dart:89-93`; `host_agent.py:138-149` (dead reauth branch). Effort L · Phase 6 · High.

### FSA-062 · P3 · macOS release build is sandboxed without `com.apple.security.network.client`; macOS is never built in CI
Source: E3-09 · `aura_client/macos/Runner/Release.entitlements:5-9`. Fix: add the entitlement or drop macOS from the supported list. Effort S · Phase 6 · High (code-read).

### FSA-063 · P3 · The Desktop IDE is gated to Windows (`aura_platform.dart:14,19`), the one desktop platform whose CI jobs are `continue-on-error`
Source: E3-10 · Fix: make Windows jobs gating once green, or widen `isDesktopIde` to Linux and test there. Effort S · Phase 1 · High.

### FSA-064 · P4 · Memory consolidation never merges (`merged` always 0); merging is write-time only
Source: D-09 · `memory/engine.py:131-143,333,341,385`; ADR-004:40-44. Fix: decide and align docs or implement. Effort S · Phase 4 · High.

### FSA-065 · P4 · No automatic memory extraction exists; memory is written only by `remember_fact`, `POST /memory` and the kernel's session-classification note
Source: D-10 · Documentation decision. Phase 4 · High.

### FSA-066 · P3 · `MemoryEngine.forget_source` deletes rows but cannot delete vectors (filters on a payload key never written); currently uncalled
Source: D-07 · `memory/engine.py:177-184,441-444`. Fix: add `source` to the payload or delete by returned ids; test. Effort S · Phase 2 · High.

### FSA-067 · P4 · `delegate_to_agent`'s target enum is frozen at container build; blueprint-compiled agents are never delegable
Source: C-06 · `delegation.py:64,74-77`; `adaptation/runtime.py:184-216`. Fix: document or validate via `agents.resolve()` at call time. Effort S · Phase 4 · High.

---

## Group 5 — Missing capabilities (informational unless noted)

### FSA-070 · P4 · Postgres FTS scale-out path (ADR-005) is not built; the in-process BM25 index is rebuilt from the whole store in every process at startup
Source: D-08 · `knowledge/engine.py:946-964`; `retrieval.py:60-66`; `LEXICAL_RETRIEVAL_BENCHMARK.md` (4.8 ms p50 at 50k chunks; 200k point open). Fix: record the corpus ceiling; build the FTS adapter when the 200k measurement is taken (M5). Phase 5 · Med.

### FSA-071 · P4 · Not implemented anywhere: video understanding; push notifications (`firebase_messaging` absent, in-app centre only); automatic memory extraction; Track AP super-admin screens (AP9)
Source: E1 (video), MOBILE_APP_STATUS (push), D-10, E2-04. Status `NOT IMPLEMENTED` / `PLANNED ONLY` in `feature_matrix.md`. No remediation proposed beyond the roadmap's own phases.

---

## Group 6 — Performance and scalability

### FSA-072 · P3 · In-process BM25 per worker: memory and boot time grow with corpus × process count; no ceiling documented
Source: D-08 (see FSA-070). Fix: record the ceiling in the capacity doc. Phase 5 · Med.

### FSA-073 · P3 · No `command_timeout` on the asyncpg pool; legacy SQLAlchemy engine never disposed; connection budget never computed (two pools × workers vs Postgres default 100)
Source: F1-12 · `adapters/postgres_store.py:120-122`; `infrastructure/db/base.py:69`; `container.py:905`. Fix: pass `command_timeout`/`max_inactive_connection_lifetime`; compute and set `max_connections` per deployment. Effort S · Phase 2 · High.

### FSA-074 · P3 · The only real-model latency evidence is the 2026-09 sandbox (qwen2.5:3b, CPU Ollama, p50 56 s / p95 71 s, single-flight); nothing measured on the current Railway + Groq topology
Source: B (performance section), `docs/reports/phase-a4-load-test.md`. Fix: run `tests/load` against the live topology after FSA-024/025 (M5). Phase 5 · High.

---

## Group 7 — Evaluation and testing

### FSA-080 · P2 · The architecture, v3-only, reachability, route-auth and IDOR gates cannot execute anywhere today: the local venv (Python 3.14, FastAPI 0.136.3) drifts from `requirements.lock` (FastAPI 0.141.1) and CI has no runners
Source: A-01; lead EXECUTED-HERE (`tests/architecture` → 8 failed, 6 of them `fastapi.routing.iter_route_contexts` missing; `tests/security/test_route_auth.py` errors at collection). Fix: owner re-syncs the venv to the lock (Python 3.11); until runners return, run `tests/architecture tests/security` locally before each merge and record it. Effort S · Phase 1 · High.

### FSA-081 · P2 · No engine-backed speech/OCR test runs anywhere: CI installs no espeak-ng/tesseract, every real-engine test is skipif-gated, the live-voice load round-trip unconditionally skips, and no Phase H device/latency report exists
Source: E1-02 · `tests/load/test_live_voice_roundtrip.py:17-26`; `tests/unit/test_voice_adapters.py:71,112,355,373`; `backend-ci.yml:174`. Fix: apt packages in the backend CI job; implement the round-trip against the built image; commit the report. Effort M · Phase 1 · High.

### FSA-082 · P2 · Every quality number is hash-embedder / scripted-provider; no real-embedder retrieval report and no real-model answer report exist (GAP-021); the eval gate cannot see the clipped prompt (FSA-051)
Source: D-12, B (perf) · `eval-ci.yml:26-73`; `evaluation/suites/docs_corpus_baseline.json` (recall@5 0.65, MRR 0.368, nDCG 0.511, hash embedder). Fix: run `evaluation/runner.py` once against sentence-transformers + `hosted1` and commit; keep the deterministic gate as the merge gate. Effort M · Phase 1 · High.

### FSA-083 · P3 · The committed API-reachability matrix over-credits callers (UI route paths, method-blind matches, test files)
Source: E2-02 · `docs/guides/api-reachability.md:40,80-81,90`; `scripts/api_reachability.py:29-32,118-135`. Fix: exclude test dirs, require an API base, record the verb. Effort S · Phase 1 · High.

### FSA-084 · P3 · ADR-030 hosted inference is live on Railway but Phase 6 is unproven; the verify workflow needs the dead VPS path and the only report is in-process (9/10, 429 burst skipped, against OpenRouter not the deployed provider)
Source: B-06 (GAP-064) · `verify-hosted-inference.yml:124-134`; `docs/reports/phase-6-hosted-inference-openrouter-inprocess-20261028.txt`. Fix: Railway-targetable run with Redis and real fallback, check 10 included, wire captures for the deployed provider. Effort M · Phase 1 · High.

### FSA-085 · P3 · Nothing in the desktop/hostd/Engineer-workspace track is proven: no green Host Daemon CI since 2026-09-11, no real-machine end-to-end report (GAP-026), no `verify-engineer-workspaces.sh` report (GAP-025)
Source: E3-11; lead EXECUTED-HERE hostd on Windows: 219 passed / 10 failed, several failures attributable to the full `C:` temp volume and missing debugpy (see `verification_log.md`). Fix: restore runners; green hostd CI on both OSes; record the sessions the gap register names. Effort M · Phase 1 · High.

### FSA-086 · P3 · Testing-pass findings (G) — see `testing_and_evaluation.md` for the suite inventory, executed results and per-subsystem regression proposals; its `G-xx` items are tracked there and summarised in `remediation_roadmap.md` Phase 1.

---

## Group 8 — Reliability and recovery

### FSA-090 · P3 · Deploy-time migration safety relies on an "additive migrations" assumption nothing enforces; rollback reverts the image only
Source: F2-12 · `vps-update.sh:22-25,381-384,474-476`; `railway-predeploy.sh:2-3`; `docs/deployment/rollback.md:8-14`. Fix: `upgrade head && downgrade -1 && upgrade head` in the postgres CI job; migration-review item in `RELEASE_CHECKLIST.md`. Effort S · Phase 2 · Med.

### FSA-091 · P4 · Fine-tune queue drain builds a new container every 120 s and reaches into `ApprovalService._store`
Source: D-16 · `tasks.py:162-172,267-284`; `finetune.py:358`. Effort S · Phase 2 · High.

### FSA-092 · P3 · Structured-output requests exclude every provider left at the default `SUPPORTS_JSON_MODE=false`; no boot-time warning
Source: B-08 · `router.py:330-331`; `settings.py:262`; `docker-compose.prod.yml:93,117,141`. Fix: boot check; set the flag in the documented Groq example; expose exclusion reasons in routing explain. Effort S · Phase 2 · Med.

### FSA-093 · P3 · Native Ollama adapter reports every completion as `finish_reason=stop` (truncation invisible)
Source: B-07 · `llm/providers/ollama.py:170,206`. Fix: map `done_reason`. Effort S · Phase 2 · High.

### FSA-094 · P4 · Legacy plaintext rows are read silently after keys are configured; no count of remaining plaintext rows
Source: F1-13 · `adapters/field_crypto.py:33-36,180-182`; `scripts/reencrypt_fields.py` (no evidence it ran). Fix: `needs_reencryption` counter; run and record. Effort S · Phase 2 · High.

---

## Group 9 — Architectural inconsistencies

### FSA-100 · P3 · Dev/prod compose parity gaps on security-relevant keys (sandbox network name, mock attestation default, sandbox limits, hosted/large-provider keys)
Source: A-05 · `docker-compose.dev.yml:333,365`; `prod.yml:448-585,519,565-566`; CLAUDE.md parity rule. Fix: align; extend the parity tests. Effort S · Phase 2 · Med.

### FSA-101 · P3 · Two stacked rate limiters with different keying (see FSA-005); two learning paths (see FSA-043); two sync-era stores for Personal OS partially unified (see FSA-052). Tracked under those entries.

---

## Group 10 — Technical debt

### FSA-110 · P3 · Embedder (`bge-small`), cross-encoder, Whisper weights and the Ollama tag load by name with no revision/digest; Whisper is fetched from the Hub on first transcription per fresh container
Source: B-03, D-11, E1-04 · `llm/embeddings.py:358`; `knowledge/reranking.py:104`; `adapters/whisper_stt.py:63,109-141`; `prod.yml:430`; Piper voices are SHA-pinned for contrast. Fix: `*_MODEL_REVISION` settings folded into index identity and telemetry; bake or warm Whisper at build/startup. Effort M · Phase 2 · High.

### FSA-111 · P3 · HF Space image is `vllm/vllm-openai:latest` with an optional model revision; nginx/postgres/redis/tinyproxy float within a tag
Source: B-04, F2-13 · `deploy/spaces/inference-vllm/Dockerfile:16-21`; `entrypoint.sh:22-24,65-67`; `prod.yml:289,733,1012,1037`. Fix: tag+digest pins; `VLLM_MODEL_REVISION` required; extend `test_repo_hygiene.py`. Effort S · Phase 2 · High.

### FSA-112 · P3 · `TOOLS_MAX_CALLS_PER_TURN` is parsed and documented but never enforced
Source: C-05 · `settings.py:905,916`; `execution/agentic_loop.py:111-138`. Fix: remove or wire into `RunBudgetGate`. Effort S · Phase 2 · High.

### FSA-113 · P4 · Tracked `deploy/vps_known_hosts.tmp` duplicates the stale pin; not covered by the artefact guard
Source: F2-15. Fix: delete by PR; refuse `*.tmp` in `scripts/check_tracked_artifacts.py`. Phase 1 · High.

### FSA-114 · P4 · Repository strays: three stale `.claude/worktrees` (one with an unmerged September client commit), root `ML_ENVIRONMENT_REPORT.md`, `test_personal_os.py`, `aura_backend/{learning_logs.json,routing_centroids.json,sentiment_centroids.json,local_qdrant_storage/,Deployment_info.txt}`, root `docker-compose.yml` (legacy local stack)
Source: lead (repository_inventory.md). Fix: prune worktrees; move or delete strays (GAP-010). Phase 6 · High.

---

## Group 11 — Documentation inaccuracies (all P4, one entry)

### FSA-120 · P4 · Documentation contradicted by the tree (each pass's "Documentation inaccuracies" section has the paired citations)
Source: A-09, B-11, D-17, E1-09, E3-16, F1 §docs, F2 §docs, E2 §6, lead DOC-01. Highlights:
- `CLAUDE.md`: lists `infra/` (Terraform) — **the directory does not exist**; ADR range ends at 031 while 032 exists; "nginx is the only published port" is VPS-only (Railway exposes uvicorn); says Ollama serves production (it does not; Groq `hosted1` does).
- `docs/status/BACKEND_STATUS.md:57,59,66,69`: F-16 "open" (fixed `8b34504`); "demotion revokes no sessions" (it does, `admin_platform.py:208-211`); `eval-ci.yml` "dispatch-only" (it triggers on PR/push); GAP-018 "failed train recorded as success" (fixed, `finetune.py:444-448`); ADR-032 migration "0017" vs actual `0019`.
- `docs/production/gap_register.yaml`: GAP-043 `proven` (regressed); GAP-013 "client sends Idempotency-Key" (no client does); GAP-017 `current_state` cites removed code; GAP-019 self-contradictory; GAP-023/024 text stale; GAP-038 "dead legacy tasks" (they are mounted); GAP-016 and `DISASTER_RECOVERY.md:45` say backups are unencrypted (they are age-encrypted).
- `docs/roadmaps/AURA_CRITICAL_BACKLOG.md`: CB-30 "16 uncalled routes" (47); CB-08 half-stale.
- `docs/architecture/011`: desktop exchange "sends a client secret", "no approval screen"; ADR-004/005 file paths; ADR-007/008 recall gate "runs"; ADR-030 "not deployed".
- `docs/deployment/`: `DEPLOYMENT_TOPOLOGY.md` presents the VPS as production; `control-plane.md:17-19`/`operations.md:35-38` demand `/health/ready` while `railway.json` uses `/health/live`; `operations.md` cites a `/health` route that does not exist; the console is never mentioned; required-variable list incomplete.
- `docs/reports/AURA_FULL_SYSTEM_REVIEW.md:327`: "vision not implemented" (superseded by ADR-031 code).
- `aura_hostd/README.md:25` ("no pairing flow"), `command_policy.dart:7-13` header, `control_center_panel.dart:142` copy, `agents/registry.py` "seven personas"/"read-only" comments, `embeddings.py:63-72` "validate() refuses" claim.
Fix: correct each cited line when the owning file is next touched; add a dated "verified against commit" line to each status doc. Effort S–M · Phase 1 · High.

---

## Previous-finding recheck (consolidated)

| Prior item | Verdict at `ffbbb9f` | Source pass |
| --- | --- | --- |
| CB-01/CB-44 CI runners | **regressed** (no runners since 2026-10-28); GAP-043 `proven` is STALE | F2 |
| CB-02 branch protection | still open | F2 |
| CB-03 VPS deploy / live SHA | still open; host key changed, state unknown | F2 |
| CB-04 / F-04 approval binding | fixed in source (`approval_id` bound to call digest), not CI-run | C, E2 |
| CB-05 / F-01b host-routed taint ⇒ confirmation | fixed in source for the tainted case; untainted-trusted case open (FSA-006) | C, E3 |
| CB-06 hardcoded GOCSPX secret | fixed in repo; secret still on the developer host (FSA-003); rotation UNKNOWN | F1, E3 |
| CB-07 / SEC-15 secrets in job-level `if:` | fixed-verified in source (`vars`/step gating) | F2 |
| CB-08 backup mounts all secrets | still open (FSA-008) | F1 |
| CB-13 / F-29 / GAP-010 tracked binaries | fixed-verified (`check_tracked_artifacts.py`, repo-hygiene workflow) | E3, F2 |
| CB-14 / F-24 planner failure disclosure, metrics | fixed in source, not proven live | A |
| CB-16 sole remote provider | partially fixed: now "sole SENSITIVE-capable provider is down" (FSA-024) | B |
| CB-18 / GAP-030/031 model pinning | still open (FSA-110) | B, D, E1 |
| CB-22 plaintext chunks | still open (FSA-018) | D, F1 |
| CB-26 hostd supervisor/packaging | still open (FSA-061) | E3 |
| CB-28 / F-18 Personal-OS single store | fixed in source for 4 kinds (ADR-032); projects/goals open (FSA-052); needs 0019 live | E2 |
| CB-30 uncalled routes | still open; count grew to 47 | A, E2 |
| CB-31 / GAP-039 "no OCR in agentic RAG" | fixed in code; register text STALE | E1 |
| CB-43 schema behind code | resolved (0016 live per status doc); 0019 pending | all |
| F-02 role on refresh / demotion revokes | fixed in source (contradicting BACKEND_STATUS:59) | F1 |
| F-03 JWT iss/aud, dev keys | fixed in source | F1 |
| F-06 erasure reach | fixed in source; audit/telemetry `NOT_ERASED` by design (FSA-016) | F1, D |
| F-07/F-11/F-12/F-13 sync defects | fixed in source (both halves), not operationally verified | E2 |
| F-09 / SEC-05 desktop login attestation bypass | pairing token scoped; app login still unscoped 30-day (FSA-004) | F1, E3 |
| F-10 JWT secret required in prod | fixed in source | F1 |
| F-14 / F-30 retrieval fixes | fixed in source; cross-worker convergence not proven on multi-worker | D |
| F-15 / F-16 memory | fixed in source (F-16 per `8b34504`; status doc STALE) | D |
| F-19 / F-22 router breaker | fixed in source, not CI-run | B |
| F-20 one event loop per Celery worker | fixed in source | F2 |
| F-21 non-streaming hostd `run` | fixed in source | E3 |
| F-25 deploy robustness | fixed in source; unproven on any target | F2 |
| F-28 fitness-test blind spots | **not reproduced**: the layering walker handles relative, absolute and nested imports (A) | A |
| F-31 stop/idle timeout | fixed in source (client), not proven live | E2 |
| GAP-004 cross-user ingestion | fixed in source (owner-scoped queries, Qdrant owner sentinel) | F1, D |
| GAP-006 eval gate dispatch-only | STALE: triggers on PR/push; but inert (FSA-021) | D |
| GAP-011/041 concurrency | still open (FSA-025) | B |
| GAP-017/018 fine-tuning | 018 fixed in source; 017 open (FSA-055) | B, D |
| GAP-019 vision | built_not_proven (FSA-056) | B, E1 |
| GAP-020 STT English-only | still open (FSA-058) | E1 |
| GAP-021 real-model eval | still open (FSA-082) | D |
| GAP-023 knowledge graph unused | still open, broader than stated (FSA-050) | D |
| GAP-024 agent memory isolation | STALE text; scopes exist (`memory/engine.py:165,271`); isolation test exit condition not evidenced | C |
| GAP-025/026 engineer workspaces / desktop E2E | still open (FSA-085) | E3 |
| GAP-028 backup on live target | still open (FSA-029) | F2 |
| GAP-036/037 observability | still open (FSA-028) | F2 |
| GAP-064 ADR-030 Phase 6 | still open (FSA-084) | B |
| PostgresPool first-use concurrency | **fixed-verified by code read**: double-checked creation under `asyncio.Lock` (`postgres_store.py`); same pattern in other lazy adapters | A, F1 |
| SEC-01 approval binding | fixed in source (see CB-04) | C |
| SEC-07 / GAP-027 HF image pin | still open (FSA-111) | B, F2 |
| SEC-09 encryption at rest for memories | fixed in source; chunks excluded (FSA-018) | D, F1 |
| SEC-12 CORS localhost | still open (FSA-010) | F1 |
