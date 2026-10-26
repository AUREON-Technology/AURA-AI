# Lens: performance, reliability, observability, tests, CI/CD, deploy

Baseline: `699f27b` (verified with `git log -1`). This audit was read-only: static reads and greps only, with no pytest runs, installs, or network calls. Every latency number below is **code-inferred**: configured token caps divided by the CLAUDE.md figure of 5–10 tok/s for the CPU model. None of them was measured. Line numbers refer to the files as opened at this commit.

Throughput arithmetic used throughout: worst-case decode time = `max_output_tokens / 5` (slow end), best case = `/10`. Prefill time is excluded, so real times are longer.

---

## DEFECTS

### PR-01: Non-streaming `/chat` generation runs under a 60 s deadline, not the 180 s one the kernel computes
- **Type:** Defect (timeout vs token budget) · **Severity:** High · **Evidence:** code-inferred, reading verified
- **Files:** `aura_backend/aura_core/kernel/kernel.py:1751-1760` (builds `completion` with `timeout_s=self._completion_timeout_s()`), `kernel.py:1776-1786` (when `deps.reasoner` is set, the turn goes through the reasoner instead; `completion` is then only used as a cache key), `aura_core/container.py:411-421,559` (the reasoner is always wired), `aura_core/reasoning/engine.py:221-226` (`_direct`), `:253-258` (CoT), `:281-289` (self-consistency, 3 concurrent samples), `:369-373`, `:401-406` (ToT expand, 400 tok), `:428-433` (evaluate, 120 tok), `:456-461` (verify, 600 tok). None of them passes `timeout_s`. `aura_core/domain/llm.py:542` sets the default `timeout_s: float = 60.0`. `aura_core/llm/router.py:640-641` applies `asyncio.wait_for(provider.complete(request), timeout=request.timeout_s)` to the **whole** non-streaming call.
- **Trigger:** any `POST /api/v3/chat` with no `response_schema`. The default `max_output_tokens=1024` is at `api/v3.py:179`.
- **Actual vs expected:** a 1024-token answer needs 102–205 s at 5–10 tok/s and is cancelled at 60 s. Expected: the same `request_timeout_s` (180 s in prod, `docker-compose.prod.yml:382`) that `PlanningEngine` (`planning/engine.py:209`) and the kernel's own completion get. Self-consistency/CoT/ToT are auto-selected (`settings.py:909` `REASONING_AUTO_SELECT` default True; `reasoning/engine.py:140-163`), and verification is on by default (`settings.py:917`). That adds a 600-token verify call (60–120 s) that also has a 60 s ceiling.
- **Impact:** non-streaming answers longer than about 300–600 tokens fail with `AllProvidersFailedError` on the CPU stack. Verification always comes back INCONCLUSIVE at worst-case length. Each timeout also counts against the local provider's circuit breaker (see PR-05).
- **Root cause:** the reasoner builds its own `CompletionRequest`s and never received the deadline-propagation fix applied to the kernel and planner (`kernel.py:1636-1651` docstring explains exactly this failure mode).
- **Minimal fix:** pass `request_timeout_s` into `ReasoningEngine.__init__` from `container.py:411` and set `timeout_s=` on every `CompletionRequest` in `reasoning/engine.py`. Alternatively, have `ReasoningRequest` carry the deadline. Cap verify/expand tokens so they fit inside it.
- **Regression test:** unit test using the `echo` provider wrapped to record the `CompletionRequest` it receives. Assert `timeout_s == settings.llm.request_timeout_s` for each strategy. Mirror whatever `tests/unit/test_planning_execution.py` already does for the planner.
- **Confidence:** High on the mechanism, Medium on how often it bites (depends on answer length).

### PR-02: Several auxiliary LLM calls rely on the 60 s default with caps that cannot finish in 60 s. One comment states a wrong deadline.
- **Type:** Defect · **Severity:** Medium · **Evidence:** code-inferred
- **Files / budgets:**
  - `aura_core/knowledge/extraction.py:233-241`: 600 tok, no `timeout_s`. The comment at `:237-239` says "inside the 180s request timeout". That is false: the effective deadline is 60 s (`domain/llm.py:542`). Worst case is 60–120 s. Opt-in via `KNOWLEDGE_GRAPH_LLM_EXTRACTION` (`settings.py:734`, `container.py:1225-1233`).
  - `aura_core/memory/session.py:278-283`: conversation summariser, 512 tok, no `timeout_s`, so 51–102 s against a 60 s cap. Always wired (`container.py:354`). It runs **inline in the streaming turn** after the last token and before the `done` frame (`kernel.py:780-787` → `session.py:212-215`).
  - `aura_core/rag/expansion.py:42-46`: HyDE/multi-query/rewriter, no `timeout_s`.
  - `aura_core/kernel/kernel.py:1711-1719`: shadow-model mirror, no `timeout_s`.
- **Trigger:** session window overflow (the summariser), document ingest with LLM extraction enabled, RAG expansion strategies.
- **Impact:** the summariser fails silently and falls back to truncation (`session.py:258-261`). The client still waits up to 60 s for `done` after the answer has finished streaming. Extraction yields an empty graph delta. Every one of these timeouts also feeds the breaker (PR-05).
- **Root cause:** same as PR-01. The dataclass default of 60 s is the de facto deadline for every caller that forgets to set one.
- **Minimal fix:** either change `CompletionRequest.timeout_s` to `None`, meaning "router default from settings", and have the router substitute `settings.llm.request_timeout_s`, or pass it explicitly at each site. Correct the extraction comment.
- **Regression test:** an architecture-style test that walks the AST of `aura_core/**` and fails on any `CompletionRequest(` call without `timeout_s=` outside an allowlist. A behavioural alternative: a router test where the `CompletionRequest` default resolves to settings.
- **Confidence:** High.

### PR-03: Non-streaming `/api/v3/chat` sits behind nginx `proxy_read_timeout 120s`, which is shorter than the gateway's own 180 s budget
- **Type:** Defect (timeout chain) · **Severity:** High · **Evidence:** code-inferred
- **Files:** `deploy/nginx/templates/aura.conf.template:185-191` and `:399-405` (`location /api/` gives 120 s; only `/api/v3/chat/stream` gets 300 s at `:164-177`), `aura_core/api/v3.py:424` (`@router.post("/chat", dependencies=idempotent)`), plan 180 s (`planning/engine.py:209`) + generation ≤180 s (`kernel.py:1760`) + admission queue ≤90 s (`docker-compose.prod.yml:397`).
- **Trigger:** a non-streaming chat turn whose plan + generation takes more than 120 s. On CPU, a single 1024-token generation is enough (102–205 s).
- **Actual vs expected:** nginx returns 504 while the gateway keeps generating and holds the only Ollama slot (`AURA_PROVIDER_LOCAL_MAX_CONCURRENCY:-1`, compose `:390`). The idempotency claim stays `running` until the handler finishes, so a client retry with the same `Idempotency-Key` gets 409 (`api/idempotency.py:242-248`). Expected: the edge timeout should be at least the worst-case server budget, or the route should be refused/streamed.
- **Impact:** the exactly-once path the idempotency module recommends (`idempotency.py:18-23`: "a client that needs an exactly-once chat turn uses the non-streaming POST /chat") is the path most likely to 504 on CPU.
- **Minimal fix:** add a `location = /api/v3/chat` block with `proxy_read_timeout 600s` in both server blocks of the template and its `host-proxy.conf.example` mirror, or reduce the server budget below 120 s for this route.
- **Regression test:** a static test parsing the nginx template. It asserts that every location proxying a route which calls `kernel.turn` has a `proxy_read_timeout` ≥ `AURA_LLM_TIMEOUT_S × 2 + AURA_LLM_ADMISSION_QUEUE_TIMEOUT_S`. Measure on the VPS with `curl -w %{time_total}` on a long prompt.
- **Confidence:** High on the config mismatch. Medium on frequency.

### PR-04: The SSE stream can stay silent for longer than nginx's 300 s read timeout (no heartbeat)
- **Type:** Defect · **Severity:** Medium · **Evidence:** code-inferred
- **Files:** `kernel.py:1414-1417` (`_iter_execution` awaits `_compose_plan` before yielding anything), `kernel.py:632-700` (the first possible frames are `step`, `actions` only if tools ran, and `citations` only if any exist), then `router.stream` first-chunk `wait_for` ≤180 s (`router.py:772`). No heartbeat or comment frame appears in `api/v3.py:501-531` (grep for `heartbeat|keepalive|: ping` found nothing).
- **Trigger:** a stream turn where planning runs and returns no tool steps, with no citations. The gap is plan (≤180 s, plus up to 90 s admission wait) followed by TTFT (≤180 s, plus up to 90 s). That gives 360–540 s with zero bytes, against `proxy_read_timeout 300s` (`aura.conf.template:177`).
- **Impact:** nginx closes the stream mid-turn. The client sees a dropped connection, not an error frame. Ollama keeps generating the abandoned answer and blocks the next user.
- **Minimal fix:** emit an SSE comment (`: keepalive\n\n`) every 15–30 s from `events()` while waiting. Alternatively, yield a `{"type":"status","phase":"planning"}` frame before `_compose_plan`.
- **Regression test:** an async test with a provider stub whose first chunk is delayed via `FrozenClock`/an event. Assert that the SSE generator emits a keepalive before the first token.
- **Confidence:** Medium. The timings are worst-case, and the planning fallback reduces typical cost.

### PR-05: Timeouts from background and auxiliary calls open the circuit breaker that interactive chat depends on
- **Type:** Defect (reliability amplifier) · **Severity:** Medium · **Evidence:** code-inferred
- **Files:** `router.py:656-660` (a timeout calls `_record_failure`), `router.py:582-585` (`stats.breaker.record_failure()`), `settings.py:642` (`LLM_CIRCUIT_THRESHOLD` default 5). In prod `AURA_LLM_PROVIDERS` defaults to `local` only (compose `:380`).
- **Trigger:** PR-01/PR-02 timeouts. These are timeouts caused by the caller's own budget, not by provider faults.
- **Impact:** five such timeouts in a row in one uvicorn worker open the breaker for its only provider. All turns on that worker then fail fast until the half-open probe succeeds. A self-inflicted deadline is being counted as provider ill-health.
- **Minimal fix:** fix PR-01/02 first. Separately, consider not counting a `TimeoutError` as a breaker failure when the provider had already streamed or acknowledged, or when `timeout_s` is below the provider's own `timeout_s`.
- **Regression test:** a router unit test with `FrozenClock` and a slow echo provider. Five requests at `timeout_s=0.01` should not trip the breaker for a provider whose configured timeout is larger, or should be documented as intended.
- **Confidence:** Medium.

### PR-06: Celery tasks call `asyncio.run()` per task on a container cached per process, so asyncpg pools are reused across closed event loops
- **Type:** Defect · **Severity:** High · **Evidence:** code-inferred (mechanism is standard asyncpg/asyncio behaviour; not observed in logs here)
- **Files:** `aura_backend/tasks.py:212-233` (`_worker_container` built once per process, `startup()` once), every task wrapper using `asyncio.run(...)` (`tasks.py:95,106,168,205,283,294,318,350,375`), `aura_core/adapters/postgres_store.py:104-125` (`asyncpg.create_pool` is memoised on `self._pool`, so it is bound to the first loop and never rebuilt), `celery_app.py:80-84` (`drain-event-outbox` every 60 s), compose worker `-c 4 --max-tasks-per-child=200`.
- **Trigger:** the second Postgres-touching task executed in the same prefork child.
- **Actual vs expected:** the first task builds the pool in loop A. `asyncio.run` then closes loop A. The next task runs in loop B and acquires connections whose transports belong to closed loop A, which raises `RuntimeError`/`InterfaceError` ("Event loop is closed" / "attached to a different loop"). Expected: one long-lived loop per worker process, or a pool rebuilt per loop.
- **Impact:** the durable outbox (Phase E1), daily briefings, insights, memory consolidation, workspace reaping and idempotency purge can fail on most runs. Each wrapper catches the error and **returns** `{"status": "error"}` (e.g. `tasks.py:319-321`). Celery records success, nothing is retried, there is no metric, and there is no alert (see PR-09). The same applies to the `redis.asyncio` cache client.
- **Minimal fix:** create one event loop per worker process (`worker_process_init` signal) and run every task with `loop.run_until_complete`. Alternatively, reset `_worker_container` and `_worker_started` at the end of each `asyncio.run` after an awaited `container.shutdown()` that also closes the pool.
- **Regression test:** a test in the default suite with the in-memory substrate cannot catch this. Put it in `tests/load` (Postgres via `AURA_TEST_DATABASE_URL`): call `drain_event_outbox_task()` twice in the same process and assert both return `status != "error"`. Falsify quickly on the VPS: `docker compose logs worker-realtime | grep -c "Outbox drain task failed"`.
- **Confidence:** High on the mechanism, Medium on observed impact (not verified against live logs).

### PR-07: The legacy rate-limit middleware runs a synchronous Redis `INCR` on the event loop with no socket timeout, on every request
- **Type:** Defect · **Severity:** Medium · **Evidence:** code-inferred
- **Files:** `aura_backend/infrastructure/http_limits.py:34-40,64-66` (sync `_rate_limit_redis.incr`/`expire` inside `async def dispatch`), `infrastructure/redis_client.py:60-68` (`socket_connect_timeout=2` but no `socket_timeout`), applied to the v3 gateway at `aura_service.py:182-184`.
- **Trigger:** any Redis latency spike, AOF fsync stall, or half-open TCP connection.
- **Impact:** the whole uvicorn worker blocks, including every in-flight SSE stream and voice WebSocket on it. With no `socket_timeout`, a hung socket blocks until the kernel's TCP timeout. This rate limiter (100 req/min/IP) also duplicates the v3 `SlidingWindowRateLimiter` (`aura_service.py:232-235`). It is a `BaseHTTPMiddleware`, which wraps every streaming response.
- **Minimal fix:** set `socket_timeout=0.5` in `redis_client.py`, and move the call to `await asyncio.to_thread(...)` or use `redis.asyncio`. Better: remove the middleware from the v3 app, since v3 already enforces per-user/IP limits.
- **Regression test:** a unit test with a fake Redis whose `incr` sleeps 1 s. Assert that a concurrent `/health/live` request completes in under 100 ms.
- **Confidence:** High on blocking, Medium on severity (Redis is local).

### PR-08: An LLM outage takes down the whole edge whenever nginx is (re)created
- **Type:** Defect · **Severity:** Medium-High · **Evidence:** code-inferred from compose
- **Files:** `deploy/docker-compose.prod.yml`: nginx `depends_on: gateway: condition: service_healthy`, and the gateway healthcheck probes `/health/ready` (compose gateway `healthcheck`). `aura_core/container.py:1586-1588` gives `healthy = any(p["healthy"] for p in providers.values())`. `vps-update.sh:408-411` always force-recreates nginx when it is among the updated services.
- **Trigger:** Ollama down or still loading (the Ollama healthcheck is just `ollama list`), or any failing readiness check (e.g. `schema`, `aura_service.py:208-223`), during `up -d`/deploy.
- **Impact:** `docker compose up` refuses to start nginx because the gateway is unhealthy. TLS, `/console`, `/grafana`, `/healthz` and every non-LLM API go dark, so an LLM degradation becomes a full outage.
- **Minimal fix:** point the compose healthcheck at a "can serve HTTP" probe (`/health/live` or a probe that excludes providers), or change nginx's dependency to `service_started`. Keep `/health/ready` for alerting.
- **Regression test:** extend `tests/unit/test_hosted_provider_compose_plumbing.py`-style compose parsing to assert that nginx does not depend on a healthcheck that includes LLM providers, in both prod and dev files (parity rule).
- **Confidence:** Medium-High.

### PR-09: The deploy "liveness" gate is satisfied by nginx alone, and readiness failure is non-fatal
- **Type:** Defect (deploy verification) · **Severity:** Medium · **Evidence:** code-inferred (nginx semantics, not executed)
- **Files:** `deploy/scripts/ci-deploy.sh:25-28` (`AURA_HEALTH_URL=https://$domain/health`), `vps-update.sh:439-444` (`curl -fsS` without `-L`), `aura.conf.template:198` (`location /health/` with `proxy_pass`; nginx answers a bare `/health` with a **301** to `/health/`, which `curl -f` accepts), no `/health` route in the app (grep found none), `vps-update.sh:462-469` (readiness "Not fatal"). This contradicts the script's own comment at `:415-416`: "readiness is the one ... worth gating on".
- **Mitigation already present:** the BLOCK-03 revision check (`vps-update.sh:484-535`) does prove that the new gateway process answers `/health/version`.
- **Impact:** a deploy whose gateway reports readiness 503 (no provider, schema behind) still exits 0, and `Deploy VPS` goes green.
- **Minimal fix:** poll `/health/live` through the edge, and make readiness fatal after N retries in prod (it can stay a warning for `--dev`).
- **Regression test:** a shell test in `deploy/scripts/tests` with a stub HTTP server returning 301 for `/health` and 503 for `/health/ready`. Assert a non-zero exit.
- **Confidence:** Medium. The 301 behaviour comes from nginx documentation and was not reproduced.

### PR-10: CB-15 is still open. Turn, LLM and tool histograms top out at 60 s, TTFT at 12 s, there are no HTTP request metrics, and workers are not scraped.
- **Type:** Defect (observability) · **Severity:** High (backlog rating) · **Evidence:** verified by reading
- **Files:** `aura_core/observability/metrics.py:45-46` (`_TURN_BUCKETS` max 60.0, `_TTFT_BUCKETS` max 12.0), applied to `turn_duration` (`:161-162`), `llm_ttft` (`:178-179`), `llm_duration` (`:182`, which uses `_TURN_BUCKETS`, not `_GEN_BUCKETS`), `tool_duration` (`:497`). There are no `http_request*` metrics (grep for `http|status_code` in `metrics.py` found nothing), and there is no HTTP metrics middleware in `aura_service.py:182-200`. `deploy/monitoring/prometheus.yml:18-31` scrapes only the gateway, node-exporter and Prometheus itself: no Celery, Postgres, Redis or Qdrant exporters. `aura_dependency_up` is only set for `provider:*` (`router.py:931`). There is no outbox depth/age gauge.
- **Impact:** with a 70 s production p95 (A4, cited at `metrics.py:49`), `turn_duration`/`llm_duration` p95 falls in `+Inf`, so `histogram_quantile` caps at 60. `AuraSlowTimeToFirstToken` (`alerts/aura.yml:162-163`, threshold 8 s against a 12 s top bucket) is pinned near the cap and cannot distinguish 13 s from 180 s. PR-06 failures are invisible.
- **Minimal fix:** move `llm_duration` and `turn_duration` to `_GEN_BUCKETS`, extend TTFT to 240 s, add `aura_http_requests_total{route,method,status}` and a duration histogram (route template, not raw path), add an outbox pending/oldest-age gauge, and add Celery task success/failure counters that inspect returned `status`.
- **Regression test:** a unit test asserting `max(buckets) >= settings.llm.request_timeout_s` for every latency histogram fed by LLM calls. Verify against load p95 per CB-15's own criterion.
- **Confidence:** High.

### PR-11: The host daemon reconnects at 1 Hz forever when the server rejects its token
- **Type:** Defect (retry storm) · **Severity:** Medium · **Evidence:** verified by reading
- **Files:** `aura_hostd/lib/src/rpc_client.dart:78-91` (`await _connectAndServe(); backoff = minBackoff;` — a server-initiated close completes the `await for` normally and counts as a "clean connection"), `:100-103` (`WebSocket.connect(wsUrl)` with no timeout), server side `aura_backend/aura_core/api/host_agent.py:61-78` (auth failure → `websocket.close(code=_CLOSE_UNAUTHORIZED)`). No `closeCode` handling exists anywhere in `lib/` (grep found nothing).
- **Trigger:** a revoked or expired pairing token, or the gateway closing with `_CLOSE_UNAVAILABLE`.
- **Impact:** one TLS handshake plus WebSocket upgrade per second per daemon, indefinitely, with log spam. Because backoff never grows, a backend restart is followed by an immediate synchronized reconnect.
- **Minimal fix:** reset backoff only after a connection that stayed up for more than N seconds or received an authenticated frame. Treat an unauthorized close code as terminal: stop and prompt re-pairing. Add a `.timeout()` on connect.
- **Regression test:** in `test/rpc_client_test.dart`, a server that accepts and immediately closes with the unauthorized code. Assert that connection attempts within 5 s are ≤3, or that `run()` returns.
- **Confidence:** High.

### PR-12: There is no shutdown budget for in-flight turns, and container shutdown leaks the data-store pools
- **Type:** Defect · **Severity:** Medium · **Evidence:** verified by reading compose; impact code-inferred
- **Files:** no `stop_grace_period` on any service (compose parse; grep found nothing), so Docker's default is 10 s before SIGKILL. `aura_core/container.py:1544-1560` (`shutdown()` closes router, embedder, GitHub and MCP clients but **not** the Postgres pool, Redis cache or Qdrant client). Uses the deprecated `@app.on_event` (`api/v3.py:1516-1522`).
- **Trigger:** every deploy (`vps-update.sh:403` `up -d`) or recreate.
- **Impact:** streaming turns, which can last minutes on CPU, are killed mid-answer on every deploy, with no `done`/error frame. Postgres sees abrupt disconnects.
- **Minimal fix:** set `stop_grace_period: 300s` on gateway and workers in **both** compose files, and pass uvicorn `--timeout-graceful-shutdown`. Close `_postgres_pool()`, the cache and Qdrant in `shutdown()`.
- **Regression test:** a compose-parity test asserting `stop_grace_period` ≥ the stream route's `proxy_read_timeout`. Run a unit test on `container.shutdown()` with a recording pool.
- **Confidence:** High.

### PR-13: The Celery configuration has no time limits and no late acknowledgement, and workers have no healthcheck
- **Type:** Defect/Opportunity · **Severity:** Medium · **Evidence:** verified by reading
- **Files:** `aura_backend/celery_app.py:54-61` (no `task_time_limit`, `task_soft_time_limit`, `task_acks_late`, `worker_prefetch_multiplier`, or `task_reject_on_worker_lost`). In compose, `worker-realtime`, `worker-ingestion` and `beat` have no healthcheck. `tasks.py:11-20` (ingest tasks swallow exceptions and return `False`, so there are no retries).
- **Impact:** a hung task (e.g. an LLM call in insights/briefings) occupies a worker slot indefinitely. A worker crash (OOM at the 1–2 G limits) loses the in-flight ingestion message, because the default ack happens on receipt. A wedged worker is never detected.
- **Minimal fix:** `task_acks_late=True`, `task_reject_on_worker_lost=True`, `worker_prefetch_multiplier=1`, and a per-queue `task_time_limit`. Add a `celery inspect ping` healthcheck to both compose files.
- **Regression test:** unit-assert the `celery_app.conf` keys. Compose-parity test for worker healthchecks.
- **Confidence:** High.

### PR-14: CB-30 is open. The release APK builds on floating Flutter `stable`, and other CI hygiene gaps remain.
- **Type:** Defect (CB-30) + Opportunities · **Severity:** Medium · **Evidence:** verified by reading
- **Files:** `.github/workflows/release-apk.yml:30-32,78-81` (`channel: stable`, no `flutter-version`, while `client-ci.yml:36,68,102,142` pin `3.44.6`). Every action is pinned by tag, not SHA (21× `actions/checkout@v4`, 6× `subosito/flutter-action@v2`, 3× `docker/build-push-action@v5`, …). There is no `actionlint` in any workflow (grep found none), even though CB-07's required action names it. `admin-ci.yml` triggers only on `workflow_run` of "VPS Dev Ops" and `workflow_dispatch`, never on `pull_request` (the GAP-007 part of CB-30). `backend-ci.yml` has no linter/type-check step and no coverage threshold (`pytest tests --cov=aura_core` at `:74` with no `--cov-fail-under`). No CI job provides Postgres, so the Postgres adapter tests skip by design (`tests/unit/test_idempotency_store.py`, `tests/unit/test_agent_run_store.py`, and 14 `tests/load/*_postgres.py`), which conflicts with CLAUDE.md's "verify against a real Postgres" rule for adapter changes.
- **Impact:** release artefacts are not reproducible, the supply-chain surface is mutable tags, admin regressions are unguarded, and Postgres type-mapping bugs (the CLAUDE.md asyncpg `Decimal` example) can merge.
- **Minimal fix:** pin `flutter-version: "3.44.6"` in release-apk, SHA-pin actions with Dependabot, add an actionlint job, add a `pull_request` trigger with a build+lint job to admin-ci, and add a `services: postgres` job running the opt-in Postgres tests with `AURA_TEST_POSTGRES_URL`.
- **Confidence:** High.

---

## OPPORTUNITIES

### PR-15: The "fire-and-forget" work in the turn path is either awaited inline or created with no reference kept
- **Type:** Opportunity (latency + correctness hygiene) · **Severity:** Low-Medium · **Evidence:** verified by reading
- **Files:** `kernel.py:789-793`: the comment says "Fire-and-forget shadow", but the code runs `await self._run_shadow(...)`, which loops over every shadow version sequentially with 60 s-default completions (`:1706-1719`) **before** the `done` frame. `kernel.py:1864-1872`: `asyncio.ensure_future(self._record(...))` and `ensure_future(self._publish(...))` keep no reference. The asyncio docs warn such tasks can be garbage-collected mid-flight, which loses failure telemetry and `TurnFailed` events.
- **Fix:** keep a `set[Task]` on the kernel with `add_done_callback(discard)`, and dispatch the shadow and the summariser (PR-02) after the `done` frame or as tracked tasks.
- **Test:** a stream test with a registered shadow version whose provider delays. Assert that `done` arrives before the shadow completes.
- **Confidence:** High.

### PR-16: Gaps in the test map and dead code
- **Type:** Opportunity · **Severity:** Low · **Evidence:** verified by grep
- `aura_core/engineering2/` (4 files), `aura_core/proactive/` (5 files) and `aura_core/continuous_intelligence/` (only `__init__.py`) have **zero importers** outside themselves and **zero tests**. Either delete them or wire and test them.
- No test pins the deadline for the reasoner, summariser, extraction or RAG expansion. `tests/unit/test_planning_execution.py` covers only the planner. This is why PR-01/02 survived.
- No hostd test covers server-rejected auth (`aura_hostd/test/rpc_client_test.dart:53-288` covers auth send, ping, connected state and drops, but not close codes or backoff growth).
- Vacuous-test check came out clean: no `assert True` and no `assertTrue(True)`. Only 3 test files use `MagicMock/patch/AsyncMock`, which is consistent with the no-mock convention. The only unconditional skip is `tests/unit/test_provider_wire_contracts.py:297` ("no live captures committed yet"), which is honest. The certification suite skips on incomplete turns (`tests/certification/test_adversarial_safety.py`), but its conftest states that "a skip is not a pass" (`tests/certification/conftest.py:19`).

### PR-17: Dead per-user rate limiter with a broken branch, and an uncapped per-user GitHub client cache
- **Type:** Opportunity · **Severity:** Low
- `aura_core/security/rate_limiter.py:121-185` (`RateLimitManager`) is exported but never constructed (grep finds only `security/__init__.py:19`). Its per-user/IP branch can never fire, because `if user_key in self._user_counters` is checked against a `defaultdict` that is never populated. Delete it to avoid a future mis-wire.
- `container.py:390,1460-1466`: `_github_clients_by_user` keeps one `httpx` client (with its own pool) per user forever, with no LRU. This is negligible at current scale, but it is unbounded.

---

## Coverage
- **(a) Timeouts:** grepped every `max_output_tokens=` and `timeout_s=` in `aura_core` (planner, reasoning, extraction, session summariser, rag expansion, kernel paths, evaluation harness, labeling, `/complete` code completion, voice). Traced `CompletionRequest.timeout_s` → `router.complete`/`router.stream`. Compared against nginx `proxy_read_timeout`s and compose env.
- **(b) Blocking I/O:** grepped `time.sleep`, `requests.*`, `urlopen`, and model `encode`/`predict` in `aura_core`. Read `infrastructure/http_limits.py` and `redis_client.py`.
- **(c) Unbounded state:** grepped dict/defaultdict state in api, llm, security, safety and voice. Read `llm/cache.py`, `security/rate_limiter.py`, and `asyncio.create_task`/`ensure_future` sites.
- **(d) Celery/outbox/idempotency:** read `celery_app.py`, `tasks.py`, `adapters/postgres_outbox.py` (claim/fail) and `api/idempotency.py`.
- **(e) Shutdown/health:** read `container.health/shutdown`, the `v3.py` health routes, compose healthchecks and depends_on, and `vps-update.sh` §7–8b.
- **(f) Metrics:** `observability/metrics.py`, `deploy/monitoring/prometheus.yml`, `alerts/aura.yml`.
- **(g) Tests:** subpackage→test reference map, skip/skipif inventory, mock usage.
- **(h) CI:** every workflow's triggers, `if:` conditions, action pins, caching and Flutter pins.
- **(i) Deploy:** parsed prod compose for healthcheck, restart, limits and stop_grace on every service; checked backup/alert presence.
- **(j) hostd:** `rpc_client.dart` loop and its tests.
- **Not covered:** the dev compose file (parity is only asserted, not diffed). I did not run the backup/restore scripts. The client app's retry loops and Railway/HF deploy specifics were not examined. No live logs were read, so PR-06's runtime manifestation is unconfirmed.

## Cleared hypotheses
- **The planner's token cap is unsafe:** cleared. 700 tok against 180 s passes `timeout_s` (`planning/engine.py:202-209`).
- **The streaming generation gets cut at 180 s mid-answer:** cleared. Only the first chunk is under `wait_for` (`router.py:772`), and the provider uses per-read HTTP timeouts.
- **Voice live tokens:** cleared. 400 tok (`settings.py:1527-1529`) through the stream path.
- **Adaptation labeler:** cleared at defaults. 8×45 = 360 tok against 170 s (`labeling.py:217,234`; `settings.py:1236,1257`). It only breaks if the batch size is raised past about 18.
- **`/complete` code completion:** cleared. Default 64 tok, `le=256`, 20 s timeout (`v3.py:212,571-576`). 256 tokens at 5 tok/s (51 s) would exceed 20 s, but the comment marks this as intentional fast-fail UX.
- **CPU-heavy embedding or reranking on the loop:** cleared. `asyncio.to_thread` is used for load and encode/predict (`llm/embeddings.py:358,392`; `knowledge/reranking.py:104,133`).
- **Unbounded LLM response cache:** cleared. It is LRU plus TTL with `max_entries=2048` (`llm/cache.py:56-66`).
- **Idempotency lease shorter than a turn:** cleared. 900 s default (`settings.py:1004,1026`). Claims fail on 5xx and on exceptions.
- **Outbox double-delivery under overlapping beat runs:** cleared. `FOR UPDATE SKIP LOCKED` plus a lease and dead-letter at 5 attempts (`postgres_outbox.py:8-11,98-103,134-145`).
- **The legacy limiter keys every user to nginx's IP:** cleared. Prod uvicorn runs `--proxy-headers --forwarded-allow-ips='*'` (compose gateway command).
- **CB-07 (`secrets.*` in a job-level `if:`):** cleared and fixed. Gating now happens in steps (`control-plane-deploy.yml:45,92`; `inference-plane-deploy.yml:65,107`). No `secrets.` appears in any `if:`.
- **Deploy runs even when Backend CI fails:** cleared. `deploy-backend.yml:50` requires `conclusion == 'success'`, with a reporting job at `:127`.
- **Missing resource limits or restart policies:** cleared. Every prod service has `limits` and `unless-stopped` (except the one-shot `sandbox-image`).
- **Backups lack encryption, offsite copy or alerting:** cleared at code level. Age encryption, rclone offsite with verification, and `AuraBackupStale/Misconfigured/Unencrypted/OffsiteFailed` alerts all exist. The offsite restore drill remains open per CB-21 (not re-litigated here).
