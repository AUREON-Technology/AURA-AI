# Lens: end-to-end user flows & business logic — baseline 699f27b

Read-only audit. No tests were run (deps not installed, by instruction). Every file:line below was opened with `sed -n`/`grep -n` at 699f27b. "CODE-VERIFIED" means the defect follows from the code as read; it has not been shown at runtime.

## Summary table

| ID | Title | Type | Sev | Evidence |
|----|-------|------|-----|----------|
| FL-01 | Saving a memory poisons the sync outbox: `content_enc` vs `fact_enc` → NOT NULL violation → push 500 forever, pull never runs | Defect | **Critical** | CODE-VERIFIED |
| FL-02 | Health record per day hits `uq_health_records_user_date` across devices/reinstalls → same permanent sync stall | Defect | High | CODE-VERIFIED |
| FL-03 | One bad mutation fails the whole `/sync` batch (no per-mutation isolation); client stops before pull | Defect | High | CODE-VERIFIED |
| FL-04 | Pull cursor is app-clock `now()` but rows carry Postgres txn-start `now()` → committed-late rows skipped forever | Defect | High | CODE-VERIFIED (race) |
| FL-05 | Memory UI ↔ server id mismatch: client id ≠ server id → delete 404s silently, server memory keeps being recalled | Defect | High | CODE-VERIFIED |
| FL-06 | Knowledge delete/re-version only purges BM25 in the worker that served it (4 workers) → deleted content still retrievable | Defect | High | CODE-VERIFIED |
| FL-07 | Admin "forget user" reports `forgotten: true` but leaves aura_core memory+vectors, knowledge, sessions, PersonalOS, goals/projects, device tokens | Defect | High | CODE-VERIFIED |
| FL-08 | Hourly insights job deletes the daily briefing (no `kind` filter) | Defect | Medium | CODE-VERIFIED |
| FL-09 | Insights/briefing use UTC day and UTC hour; client stores local-midnight → false "0 ml water" nudge for UTC+ users, wrong "today" | Defect | Medium | CODE-VERIFIED |
| FL-10 | Confirmation resume replays *last* user message, re-plans, re-executes auto-allowed side effects; name-level confirms (CB-04 still open + extra paths) | Defect | High | CODE-VERIFIED / PARTIAL for duplication |
| FL-11 | Deleting a chat does not clear server session history (30-day Redis); `default_session` keeps the deleted context | Defect | Medium | CODE-VERIFIED |
| FL-12 | `/chat/stream` (the client's only chat path) records no telemetry, no TurnStarted/Completed, no TurnBudget, cost 0 | Defect | Medium | CODE-VERIFIED |
| FL-13 | `/automation/analyze-email|issue` do blocking sync HTTP (≤60 s) inside `async def` → stalls the worker event loop; failure returned as HTTP 200 | Defect | Medium | CODE-VERIFIED |
| FL-14 | Role demotion ineffective: refresh re-stamps the old (max) role and `get_or_create_user` writes it back to DB | Defect | Medium | CODE-VERIFIED |
| FL-15 | LWW comparand is an unbounded client clock: a device clock ahead locks rows; skewed conflict → silent permanent divergence | Defect | Medium | CODE-VERIFIED |
| FL-16 | Episodic `append` is load→LLM summarise→set with no lock → concurrent turns lose exchanges | Defect | Low-Med | CODE-VERIFIED (race) |
| FL-17 | Device-pairing token is a full-account 30-day bearer (all v3 + sync + admin caps); approve/poll are get-then-set (non-atomic) | Defect | Medium | PARTIALLY VERIFIED |
| FL-18 | CB-14 still open: planning/execution failures swallowed in both `run` and `stream` | Defect (dup CB-14) | Medium | CODE-VERIFIED |
| FL-19 | CB-28 still open: agent `create_task` etc. write PersonalOsStore that no client route reads | Defect (dup CB-28) | Medium | CODE-VERIFIED |
| FL-20 | Semantic LLM cache matches on last user message only, ignoring system/DATA context → stale answers after forget/delete (temp=0 callers only) | Defect | Low | CODE-VERIFIED |

---

## FL-01 — Saving a memory permanently breaks sync for that device
- **Type / Severity:** Defect / **Critical**. **Evidence:** CODE-VERIFIED.
- **Files:** `aura_client/lib/core/sync/sync_payloads.dart:81-83` (`userMemory` → `{"content_enc": ...}`); `aura_client/lib/features/chat/data/memory_repository.dart:166-187` (`saveMemory` enqueues `table: 'memories', op: 'upsert', payload: SyncPayloads.userMemory`); `aura_backend/infrastructure/db/models.py:211-218` (`Memory.fact_enc: Mapped[str] = mapped_column(Text)`, no default); `aura_backend/alembic/versions/0001_initial_schema.py:104` (`fact_enc ... nullable=False`); `aura_backend/api/v2/sync.py:188-196` (unknown keys are skipped by `hasattr`, so no `fact_enc` is ever set); `sync.py:161-204` (no try/except around a mutation or commit); `aura_client/lib/core/sync/sync_client.dart:90-94,126-129`.
- **Trigger:** the user adds a memory from the Memory Bank dialog (`memory_bank_dialog.dart:98`) or the memory screen (`memory_view_model.dart:78`), then any sync runs.
- **Actual:** the server builds `Memory(id, user_id)` with `fact_enc=NULL`. The INSERT fails with a NOT NULL violation at the next autoflush (`db.get`) or at `commit`, and the request returns HTTP 500. The client treats any non-200 as `SyncFailureKind.unknown` and leaves the outbox untouched. `synchronize()` returns before `pull()`, so every later sync fails the same way. The device never pushes or pulls anything again, for every table.
- **Expected:** either the payload key matches the column (`fact_enc`), or the server rejects only that one mutation with `status:"error"`.
- **Impact:** total loss of multi-device sync after one ordinary UI action. Every other edit stays in the outbox, and its "Synced" flag never flips.
- **Root cause:** the client and server disagree on the column name, and nothing validates required columns per mutation.
- **Minimal fix:** in `sync_payloads.dart:82`, change `"content_enc"` to `"fact_enc"`. Server side, see FL-03.
- **Regression test:** an integration test like `test_sync_integration.py` that pushes `SyncPayloads.userMemory`-shaped JSON and asserts it is applied. A client test that asserts every key of every `SyncPayloads.*` is a column of the matching model (generate the column list from `models.py`).
- **Confidence:** High. Not in the backlog.

## FL-02 — The unique daily health record collides across devices or after a reinstall
- **Severity:** High. **Evidence:** CODE-VERIFIED.
- **Files:** `aura_backend/infrastructure/db/models.py:126-131` (`UniqueConstraint("user_id","record_date")`), `alembic/versions/0001_initial_schema.py:97`; `aura_client/lib/features/health/data/health_repository.dart:21-23,45-56` (each device mints its own `_uuid.v4()` for "today", at local midnight); `api/v2/sync.py:173-196` (an unknown id means INSERT, never a merge on `(user_id, record_date)`).
- **Trigger:** two devices (same timezone) both log health data for the same day before syncing. A simpler case: a reinstall or a fresh device where the user logs water before the first pull. Push runs first in `synchronize()`, so the local "today" row always has a new UUID.
- **Actual:** the UniqueViolation fails the whole batch with a 500, and the device stalls permanently exactly as in FL-01. Devices in different timezones get *different* `record_date` instants for the same calendar day, which creates duplicate "days" instead.
- **Expected:** the server merges by natural key, or reports a per-mutation conflict that returns the server's id.
- **Fix:** in push, look up `(user_id, record_date)` before inserting a HealthRecord and treat a hit as a conflict, or upsert onto it. Store `record_date` as a DATE plus the user's tz, not as a UTC instant of local midnight.
- **Test:** push two different ids with the same `record_date` and assert the second is `conflict`, not a 500.
- **Confidence:** High.

## FL-03 — `/api/v3/sync` push has no per-mutation fault isolation (a poison pill)
- **Severity:** High. **Evidence:** CODE-VERIFIED.
- **Files:** `aura_backend/api/v2/sync.py:161-205`. The only per-mutation error paths are "unknown table", "id must be a UUID" and "not owned". `_coerce_value` (`:51-60`) raises `ValueError` on a malformed ISO string, and constraint errors surface at autoflush or commit. `aura_client/lib/core/sync/sync_client.dart:126-129` keeps the whole outbox on a non-200, and `:90-94` skips the pull.
- **Actual vs expected:** one bad row means a 500 for the batch and the device stalls forever. The documented contract (`SyncMutationResult.status="error"`, and the client comment at `sync_client.dart:131-133`) implies per-row errors.
- **Fix:** wrap each mutation in `async with db.begin_nested():` (a SAVEPOINT) inside try/except (`IntegrityError`, `ValueError`, `DataError`), append `status="error"` and continue. Client side, cap retries per outbox row and surface it after N failures. Also let `pull()` run even when push failed.
- **Test:** a batch of [valid, invalid-date, valid] returns [applied, error, applied] and commits the valid ones.
- **Confidence:** High.

## FL-04 — The pull cursor can skip rows committed after a concurrent pull
- **Severity:** High. **Evidence:** CODE-VERIFIED. The race needs concurrency; it has not been demonstrated.
- **Files:** `aura_backend/api/v2/sync.py:136-143` (filter `updated_at > since`; cursor is `datetime.now(timezone.utc)` from the **app** clock, taken *after* the reads); `infrastructure/db/models.py:79` (`server_default=func.now(), onupdate=func.now()`, and Postgres `now()` is the **transaction start** time); push runs in one transaction across all mutations (`sync.py:158-204`).
- **Trigger:** device A pushes a batch. Its transaction starts at T0 (the `get_or_create_user` SELECT) and commits at T1. Device B pulls at Tp with T0 < Tp < T1. B does not see the uncommitted rows and stores cursor ≈ Tp. After commit those rows have `updated_at = T0 < Tp`, so no later pull from B returns them. Skew between the app container's clock and the DB clock (separate hosts on the Railway target) produces the same effect.
- **Impact:** silent, permanent divergence on B for edits, deletes and tombstones, until that row changes again.
- **Fix:** derive the cursor from the DB rather than the app clock. Options: `SELECT max(updated_at)` of the returned rows; or `statement_timestamp()` minus a safety lag with an overlap window (the client's apply is an idempotent upsert, so re-delivery is harmless); or a monotonically increasing `bigserial`/`xid8` change sequence. Use `clock_timestamp()` for `updated_at`.
- **Test:** open a push transaction, run a pull, commit the push, pull again with the returned cursor, and assert the row is delivered. This needs a real Postgres (testcontainers).
- **Opportunity in the same place:** pull has no pagination. It returns every row of 8 tables (including all `messages`) on a first sync (`sync.py:135-140`).

## FL-05 — Memory ids differ between client and server, so "forget" silently fails
- **Severity:** High (a privacy expectation). **Evidence:** CODE-VERIFIED.
- **Files:** `aura_client/lib/features/chat/data/memory_repository.dart:171` (local id = `getDeterministicId(fact)`), `:188-207` (POST `/api/v3/memory`; the response's `id` is ignored), `:145-163` (`deleteMemory(localId)` → `DELETE /api/v3/memory/{localId}`; a non-200 is only `debugPrint`ed, and the local row is already deleted), `:104-131` (`syncMemories` sends `offset`). `aura_backend/aura_core/memory/engine.py:147` (server id is `MemoryId.new()`), `aura_core/api/v3.py:1040-1054` (unknown id → 404), `v3.py:989-995` (`/memory` has no `offset` parameter).
- **Actual:** a memory saved from the UI exists locally under id X and on the server under id Y. A later `syncMemories` inserts Y as a second local row, so the UI shows the memory twice. Deleting X returns 404 at the server, which is swallowed. Y is still recalled in every chat turn (`kernel._gather_context` → `memory.recall`). Paging with `offset` returns the first page every time. Memories deleted server-side are never removed locally.
- **Fix:** adopt the server id from the POST response, or send a client-chosen id the server honours. Surface a delete failure and queue a retry. Implement `offset`/cursor on GET `/memory`. Reconcile deletions.
- **Test:** client test: save, then delete, then assert DELETE is called with the id the mock server returned.
- **Confidence:** High.

## FL-06 — The BM25 index is per process, so delete and re-version leave content searchable
- **Severity:** High. **Evidence:** CODE-VERIFIED.
- **Files:** `aura_core/knowledge/engine.py:176` (`self._lexical = BM25Index()`, in-process), `:483-502` (`delete_document` removes lexical entries only in the current process), `:439-452` (`_retire_chunks` likewise for superseded versions), `:644-657` (lexical search filters by owner only), `:659-675` and `:739-770` (lexical-only hits resolve content through `chunks_for`). `aura_core/adapters/postgres_store.py:442-457` (`chunks_for` has **no** `status='active'` filter). `aura_core/container.py:1501-1502` (rebuild only at startup). `deploy/docker-compose.prod.yml:359` (`--workers ${AURA_WORKERS:-4}`).
- **Trigger:** the user deletes (or re-uploads a new version of) a document. That request lands on worker 1. Later chats and searches land on workers 2–4.
- **Actual:** workers 2–4 still hold the old chunk ids in BM25 and fetch the tombstoned or superseded chunk text straight from `aura_chunks`, so "deleted" text is returned in `/knowledge/search` and fed into chat context until the next restart. A newly ingested document is also missing from BM25 on the other workers (hybrid quietly degrades to dense-only for it). The tenant boundary holds (same owner only).
- **Fix:** make `chunks_for` (or `_resolve_contents`/`_chunk_from`) join `aura_documents.status='active'` as a guard. Propagate lexical add/remove across processes through the event bus or Redis pub/sub, or move lexical search to Postgres FTS.
- **Test:** two `KnowledgeEngine` instances sharing a Postgres/vector store: ingest and delete through A, retrieve through B, and assert no result. A unit test for the in-memory store: `chunks_for` of a tombstoned doc is excluded from retrieval.
- **Confidence:** High.

## FL-07 — Admin "forget user" (erasure) is partial but reports success
- **Severity:** High. **Evidence:** CODE-VERIFIED.
- **Files:** `aura_backend/api/admin_platform.py:158-212`. It soft-deletes (`deleted_at`) only Task, CalendarEvent, FinanceRecord, HealthRecord, Memory and Message (`:198-203`), revokes refresh tokens (`:206`), sets `role="User"` and returns `{"forgotten": True}`.
- **What it misses:** `Project`/`Goal` sync rows (both are in `SYNCABLE_MODELS`, `models.py:262-271`); the soft-deleted rows themselves (ciphertext retained, never purged); all aura_core stores: memory records **and vectors** (`MemoryEngine.forget_source`/store), knowledge documents, chunks, vectors and graph, episodic session transcripts (Redis, 30-day TTL, `memory/session.py:152`), the PersonalOsStore records written by agent tools, feedback records (`v3.py:1080`), telemetry, idempotency stored responses, and LLM cache entries; device tokens (`jwt_auth.revoke_all_for_uid` touches refresh tokens only, so 30-day device tokens stay valid, `jwt_auth.py:46,242-266`); and outstanding 15-minute access tokens. No user-facing account deletion exists at all (grep for `delete_account` in the client and backend found nothing).
- **Fix:** an erasure orchestrator in `container.py` that calls each port's owner-scoped purge (memory store + vectors, knowledge `delete_document` for each owned document, episodic clear, personal-OS, feedback, cache namespace) and hard-deletes sync rows. Also revoke devices for the uid, and return per-store counts with a failure list rather than an unconditional `true`.
- **Test:** seed every store for user U, call forget, and assert each port returns nothing for U and that a device token for U gets a 401.

## FL-08 — The hourly insights job deletes the daily briefing
- **Severity:** Medium. **Evidence:** CODE-VERIFIED.
- **Files:** `aura_backend/services/insights.py:117-123` (deletes **all** un-dismissed insights of the user, with no `kind` filter); `services/daily_briefing.py:92-99` (the briefing is an `Insight(kind="daily_briefing")`); `celery_app.py:69-73` (insights every 3600 s) and `:86-90` (briefing at 06:00); `api/v2/insights.py` `POST /generate` reaches the same function.
- **Actual:** the 06:00 briefing survives at most until the next hourly run (or until the client calls `/generate`). Insight ids also change every hour, so a client dismissing an id it fetched an hour ago gets a 404.
- **Fix:** add `Insight.kind != "daily_briefing"` (or restrict to the kinds this function produces) to the stale query. Better still, upsert by `(user_id, kind)` so ids stay stable.
- **Test:** generate the briefing, run `generate_insights_for_user`, and assert the briefing row still exists. The current test (`test_insights_integration.py:130+`) runs the two separately.

## FL-09 — Server-side "today" is the UTC day, not the user's day
- **Severity:** Medium. **Evidence:** CODE-VERIFIED.
- **Files:** `services/insights.py:72,77,82` (`today_start` is UTC midnight, and `now.hour >= 15` is UTC); `services/daily_briefing.py:38-39,49-60` (due today / events today in UTC); `aura_client/lib/features/health/data/health_repository.dart:21-23` (record at **local** midnight), `core/sync/sync_payloads.dart:40` (`record_date` = local midnight `.toUtc()`); `services/automation.py:28` (`Today's Date: datetime.now()`, the server's clock) for relative due dates; the 06:00 cron in `celery_app.py:88` is UTC.
- **Example:** a UTC+3 user's Oct 26 record is stored as Oct 25 21:00Z, which is earlier than `today_start` (Oct 26 00:00Z). After 15:00Z (18:00 local) the user gets "Only 0ml of water logged today" every day, whatever they logged. A UTC−8 user gets the nudge at 07:00 local, which the code comment explicitly intends to avoid. Briefing "due today" lists shift by a day near midnight.
- **Fix:** persist the user's IANA timezone (from the client) on `User`. Compute day boundaries and hour gates in that timezone. Store date-only values (`record_date`, all-day due dates) as `DATE`.
- **Test:** a FrozenClock at 16:00Z with a UTC+3 user and a record for the local date: assert no hydration insight.

## FL-10 — Confirmation resume re-runs the turn: wrong message, stale confirmations, possible duplicate side effects (CB-04 extended)
- **Severity:** High. **Evidence:** CODE-VERIFIED for the mechanics. Duplication is PARTIALLY VERIFIED because it depends on the planner producing the same steps again.
- **Files:** `aura_client/lib/features/chat/data/chat_repository.dart:253-286` (`confirmTaintedTools` resends `userMessages.last.content`, the *latest* user message, not the one that produced the pending call); `aura_client/lib/features/chat/presentation/chat_view_model.dart:136-146` (sends **both** `confirmedTools` names and `confirmedCallIds`; by default every pending call, accumulated across turns by `addPending`); `aura_backend/aura_core/execution/engine.py:476-484` (`call_id = f"step_{step.id}"` is positional per plan, and `confirmed = call_id in ids or step.tool in confirmed_tools`); `kernel/kernel.py:1384-1400` (resume = fresh `_compose_plan`); `tools/records.py:69-90` (`create_*` side-effecting with no confirmation gate when untainted); `api/idempotency.py` module docstring, which says streaming is protected by the per-call confirmation gate (it is not, for auto-allowed steps).
- **Actual:** (a) CB-04 is still **open**: the name-level match is still at `engine.py:484`, and the client still sends names. (b) If the user typed another message after the confirmation card appeared, approving replays the newer message with the old approvals, which can authorize same-named tools in an unrelated plan. (c) On resume, any step that was auto-allowed on the first run (for example `create_task` in an untainted turn) runs again, creating duplicate records. (d) The resumed user message is appended to the server's episodic history a second time (`kernel.py:780-787`).
- **Fix:** CB-04's server-stored pending approvals (an argument digest, keyed by turn_id plus call_id), with resume executing the stored step rather than re-planning. Drop `confirmedTools` from the client. Resend the message bound to the pending turn.
- **Test:** turn 1 plans [create_task (auto), send_x (confirm)]; confirm send_x; assert create_task ran once in total, and that a later different message cannot consume the approval.

## FL-11 — Deleting a conversation leaves the server session transcript in use
- **Severity:** Medium. **Evidence:** CODE-VERIFIED.
- **Files:** `aura_client/lib/features/chat/presentation/chat_view_model.dart:84-87` (`deleteSession` clears local and sync rows, then switches to `'default_session'`); `chat_repository.dart:546-552` (no server call); `aura_core/memory/session.py:152` (30-day TTL) and `:234-243` (a `clear` exists but has no caller; grep finds no route).
- **Actual:** the user "deletes" `default_session` (or any session), but the server still holds the plaintext exchanges and summary in Redis. The next turn on `default_session` injects the deleted history into the prompt, so the assistant "remembers" a conversation the user deleted.
- **Fix:** add `DELETE /api/v3/sessions/{id}` → `episodic.clear(session_id, owner)` and call it from `clearConversation`. Have the client mint a new session id after a delete.
- **Test:** append, clear through the API, then `history()` is empty.

## FL-12 — The streaming path skips telemetry, lifecycle events and the turn budget
- **Severity:** Medium. **Evidence:** CODE-VERIFIED.
- **Files:** `aura_core/kernel/kernel.py:564-838` (`stream` never calls `_record`, `_publish(TurnStarted/…)` or `turns_in_flight`, builds no `TurnBudget`, calls `deps.router.stream(completion)` without `budget=` at `:712`, and reports `"cost_usd": 0.0`). By contrast `run` does all of these at `:268-306,543`. `aura_client/lib/core/ai/online_agents/sse_agent_client.dart:118` shows the client uses only `/chat/stream`.
- **Impact:** `telemetry_sink` (Postgres) and admin quality summaries (`aura_core/api/admin.py:309`) see none of the real users' turns. Feedback `turn_id`s reference turns that were never recorded. The in-flight gauge and per-turn cost ceilings don't apply to the main path. Provider-level quota (`router._reserve`) still applies.
- **Fix:** factor the start/record/budget wrapper out of `run` and wrap `stream` in it, recording on `done`, on `error` and on client disconnect (`finally`).
- **Test:** stream a turn with a fake sink and assert one `record_turn` call carrying the stream's `turn_id`.

## FL-13 — Automation analysis blocks the event loop and reports failure as success
- **Severity:** Medium. **Evidence:** CODE-VERIFIED.
- **Files:** `aura_backend/api/automation.py:25-61` (`async def` handlers call the sync `automation_service.analyze_*` directly); `services/automation.py:58-67,95-110`; `services/ollama.py:271-275` (`httpx.Client().post(..., timeout=60)` is synchronous); a failure returns the `fallback` dict, so the response is **HTTP 200** with "analysis unavailable" (`automation.py:43-52`). The call also bypasses ProviderRouter, admission control and data classification.
- **Impact:** each analysis call freezes one uvicorn worker (one of 4) for the whole Ollama generation, which at 5–10 tok/s is tens of seconds. Every other request on that worker stalls, including live SSE chat streams. The Gmail tab shows a fake "General / low urgency" result as if it were real.
- **Fix:** `await asyncio.to_thread(...)`, or better, route through `container.router.complete` with a sized `max_output_tokens`. Return 503 `dependency_unavailable` instead of a 200 fallback.
- **Test:** an ASGI test where a slow fake Ollama does not delay a concurrent `/health/live`.

## FL-14 — Role demotion doesn't stick
- **Severity:** Medium. **Evidence:** CODE-VERIFIED.
- **Files:** `api/admin_platform.py:95-139` (PATCH role writes the DB only and does not revoke sessions); `services/jwt_auth.py:226-235` (refresh re-stamps role from the **stored** user_info, which carries the old role claim); `services/rbac.py:69-98` (`get_user_role` = **max** of persisted, claimed, env and roles.json, so the stale claim wins); `infrastructure/db/user_repo.py:26-34` (every sync or insights request writes a higher incoming role back to the DB).
- **Actual:** an Admin demoted to User keeps Admin on every refresh for as long as they keep refreshing (30-day rolling window), and their next `/sync` call restores `users.role=Admin`. A device token carries the role for 30 days.
- **Fix:** make the persisted DB role authoritative over the claimed role (the claim is only a cache). Call `revoke_all_for_uid` and device revocation in `assign_role`. Stop upgrading the DB role from token claims in `get_or_create_user`.
- **Test:** login as Admin, demote, refresh, and assert the new token's role is User and `users.role` stays User after a `/sync` call.

## FL-15 — Client-clock LWW without bounds; skewed conflicts diverge silently
- **Severity:** Medium. **Evidence:** CODE-VERIFIED.
- **Files:** `api/v2/sync.py:78-97` (comparand = `client_updated_at`), `:109` (envelope accepts any datetime), `:179-181` (`>=` means conflict), `:183-186` (a delete wins regardless of a later edit; an upsert onto a tombstoned row reports `applied` but `deleted_at` stays set). `aura_client/lib/core/database/database.dart:344-362` (`clientUpdatedAt: DateTime.now()`, the device clock); `aura_client/lib/core/sync/outbox_ack.dart:8-22` (a conflict is dropped from the outbox).
- **Actual:** (a) a device whose clock runs a day ahead writes rows that no correctly-clocked device can update for a day. The docstring at `sync.py:63-68` names exactly this "far-future comparand" risk, which the envelope path reintroduces. (b) A conflict is dropped on the client, but the pull uses `since=cursor`. If the server row's `updated_at` is older than the cursor, it is never re-sent, so the device keeps its rejected local edit indefinitely while the server holds a different value. (c) An edit after a remote delete reports `applied` and is then deleted locally on the next pull.
- **Fix:** clamp `client_updated_at` to `≤ server_now + ε` (reject or clamp). Return the server row inside a `conflict` result so the client can reconcile without depending on the cursor. Decide delete-vs-edit precedence explicitly and return `conflict` for writes to tombstones.
- **Test:** push with `client_updated_at = now+1d` and assert it is rejected or clamped. A conflict result includes the server row.

## FL-16 — Concurrent turns in one session lose exchanges
- **Severity:** Low-Medium. **Evidence:** CODE-VERIFIED (race).
- **Files:** `aura_core/memory/session.py:197-228`: `load` → append → optional `await self._summarise(...)` (an LLM call that can take tens of seconds on CPU) → `cache.set`. No lock or CAS. Callers are `kernel.py:499-505` and `:780-787`, plus voice live (`api/voice_live.py:166-167`, which shares the session id).
- **Actual:** two overlapping turns (a double-send, voice plus text, two devices on `default_session`) both load the same state, and the last `set` wins, dropping the other exchange and possibly a summary.
- **Fix:** a per-(owner, session) lock (Redis `SET NX` lease) or optimistic `turn_count` CAS with retry. Summarise outside the critical section.
- **Test:** two `append`s gathered concurrently with a slow summariser; assert both exchanges are present.

## FL-17 — Device pairing yields a full-power 30-day token; approval and consumption are not atomic
- **Severity:** Medium. **Evidence:** PARTIALLY VERIFIED. The code is verified; the phishing flow is inferred from RFC 8628 threat models.
- **Files:** `services/jwt_auth.py:46,171-185` (device token = a normal access JWT plus a `device_id` claim, carrying the role); `aura_service.py:41-121` (v3 auth gives device tokens the same principal and capabilities, including `admin.act` for admins); `api/v2/sync.py` and the other v2 routes accept it through `verify_access_token`; `services/device_pairing.py` `approve_pairing` (get → `_pairing_set`, non-atomic) and `poll_pairing` (get → delete, non-atomic; contrast the GETDEL fix for refresh in `jwt_auth.py:92-108`); `api/v2/auth.py:200-227` (start and poll are unauthenticated).
- **Impact:** a lured approval of an attacker-started code ("enter ABCD-EFGH to verify") hands over a 30-day token for the victim's whole account, not one scoped to `/api/v3/host/agent`. Concurrent polls can mint two tokens, and concurrent approvals can bind the daemon to the last approver.
- **Fix:** scope device tokens (an `aud`/`scope` claim) to the host-agent WebSocket and the routes hostd needs, and reject them elsewhere. Show the device/IP on the approval screen. Use GETDEL/Lua for poll and approve.
- **Test:** a device token on `/api/v3/memory` gets 403. Concurrent polls yield exactly one token.

## FL-18 — CB-14 is still open (silent planning/execution fallback)
- **Severity:** Medium. **Evidence:** CODE-VERIFIED. The backlog cites `kernel.py:1171-1173`; the code has moved to **`kernel.py:1401-1403`** (`run` path), and the same swallow also exists in **`kernel.py:642-644`** (`stream` path).
- **Impact on flows:** a planner timeout (the documented 180 s CPU budget) silently becomes "answer directly" with no `tool_results` and no "could not do" notes (`kernel.py:1109-1179` (`_execution_notes`) only run when an outcome exists). A "create a task for me" turn can therefore get a prose answer implying success while nothing ran. The `done` frame shows no `degraded` flag for this.
- **Fix:** CB-14's typed degraded outcome, plus `degraded=true` and a reason in `done`/TurnResult, plus a metric.

## FL-19 — CB-28 is still open (two personal-OS stores)
- **Severity:** Medium. **Evidence:** CODE-VERIFIED.
- **Files:** `aura_core/tools/records.py:1-20,69-140` (`create_/update_/delete_*` write `PersonalOsStore`). There are no client references (`grep personal_os|PersonalOs aura_client/lib` finds nothing) and no v2/v3 route reads that store. The app's tasks live in the v2 `tasks` table (`models.py:84-99`, sync).
- **Impact:** tasks created by the agent are invisible in the Tasks tab. `list_tasks` in chat doesn't see the user's real (client-encrypted) tasks, so the agent and the app disagree about "my tasks".

## FL-20 — The semantic cache ignores context (low exposure)
- **Severity:** Low. **Evidence:** CODE-VERIFIED.
- **Files:** `aura_core/llm/cache.py:241-251,253-300` (matches on the embedding of the last user message only when there is exactly one non-system message; memories, retrieval and tool results go in SYSTEM messages, `context/engine.py:590-599`); `config/settings.py:595-596` (enabled, threshold 0.97, TTL 3600 s at `:594`); `kernel.py:1764-1771`. Exposure is limited because only `temperature==0` requests are cacheable (`domain/llm.py:341-342`; the default is 0.3 at `v3.py:180`), and the stream path never consults the cache.
- **Impact:** for temperature-0 `/chat` callers (service keys, integrations), within an hour of a document delete or a memory forget, a near-identical question returns the answer built from the deleted context.
- **Fix:** include a digest of the system and DATA content in the semantic index key, or invalidate the owner's namespace on knowledge or memory mutation.

---

## Opportunities (not defects)
- **O-1:** pagination or streaming for `GET /api/v3/sync` (FL-04 note), plus `limit`/`has_more`.
- **O-2:** refresh-token reuse detection: rotate per family and revoke the family on a replayed token (`jwt_auth.py:226-235` simply returns None today).
- **O-3:** `/knowledge/documents/{id}` DELETE returns `deleted:true` even when nothing was owned or found (`v3.py:722-733`). Fine for idempotency, but a `found` flag would help the UI.
- **O-4:** `POST /voice/transcribe` and `/knowledge/documents/upload` call `await file.read()` before the size check (`v3.py:658,957`). This overlaps **CB-12** (still open as far as these handlers go).
- **O-5:** the client's `SyncPayloads.task` sets `completed_at` from `updatedAt` (`sync_payloads.dart:18`), so the completion time drifts on every later edit.

## Backlog cross-reference
- **CB-04:** still OPEN (`execution/engine.py:484`; client sends names, `chat_view_model.dart:139-144`). FL-10 adds new paths: replaying the wrong message, duplicate auto-steps, and approvals accumulated across turns.
- **CB-14:** still OPEN, at new lines `kernel.py:1401-1403` plus the stream twin at `:642-644` (FL-18).
- **CB-28:** still OPEN (FL-19).
- **CB-03:** "cross-user session overwrite" is fixed in code (`memory/session.py:161-195` owner-namespaced keys). "Refresh double spend" is fixed (GETDEL, `jwt_auth.py:92-108`). "Cross-user ingestion versioning" is fixed (`knowledge/engine.py:269-279`). Deployment status is out of scope.
- **CB-12:** partially relevant (O-4).
- FL-01, 02, 03, 04, 05, 06, 07, 08, 09, 11, 12, 13, 14, 15, 16, 17 and 20 are **not** in the backlog.

## Coverage
**Inspected:** `aura_core/api/v3.py` (chat, stream, knowledge, memory, feedback, voice handlers); `api/idempotency.py`; `kernel/kernel.py` (run, stream, plan/execute, generate, record); `execution/engine.py` (step run, confirmation); `tools/policy.py`; `tools/records.py`; `memory/engine.py` (forget); `memory/session.py`; `knowledge/engine.py` (ingest, delete, retrieve, lexical); `adapters/postgres_store.py` (get_by_hash, chunks_for, iter_all_chunks, tombstone); `adapters/memory_stores.py` (get_by_hash, iter_all_chunks); `llm/cache.py`; `llm/router.py` stream head; `api/v2/sync.py`, `auth.py`, `insights.py`; `api/automation.py`; `api/admin_platform.py` (role, revoke, forget); `services/jwt_auth.py`, `device_pairing.py`, `rbac.py`, `insights.py`, `daily_briefing.py`, `automation.py`, `ollama.py` (generate_json); `infrastructure/db/models.py`, `user_repo.py`, `base.py`; `celery_app.py` beat, `tasks.py` insight/briefing jobs; `aura_service.py` authenticator; Alembic 0001 constraints. Client: `core/sync/sync_client.dart`, `outbox_ack.dart`, `sync_payloads.dart`, `database.dart` (enqueueOutbox); `features/chat/data/chat_repository.dart` (SSE, confirm, clear), `chat_view_model.dart`, `memory_repository.dart`; `features/health/data/health_repository.dart`; `features/automations/presentation/gmail_view_model.dart`; `encryption_service.dart` (decryptText).

**Not inspected / not deeply:** `voice_live.py` beyond the auth/reauth lines; `agentic_loop.py` beyond the replan guard; `planning/engine.py`; Qdrant vector adapter delete semantics; the calendar, finance and goal client repositories in depth; the Google Tasks/Calendar sync paths; `api/v2/github_integration.py`; `host_agent.py`; hostd; event-outbox drain (`tasks.drain_event_outbox_task`); idempotency store adapters; the admin console (Next.js).

## Cleared hypotheses
- Sync IDOR through mutation id: blocked (`sync.py:175-177`), and `id`/`user_id` are server-owned (`:73-75`).
- Insight dismiss IDOR: blocked (`insights.py` dismiss checks `row.user_id`).
- Knowledge delete of another user's document: blocked (`knowledge/engine.py:484-490`). Ingest into another user's document id: blocked (`:269-279`).
- Re-uploading a deleted document is skipped as "unchanged": fixed (`get_by_hash` filters `status='active'` in both adapters).
- Lexical rebuild resurrecting tombstoned chunks: no (`iter_all_chunks` joins active).
- Memory delete IDOR: owner-scoped store delete runs before the vector delete (`memory/engine.py:408-412`).
- Refresh-token double spend: fixed (GETDEL).
- Device revoke by a non-owner: blocked (`jwt_auth.py:191-200`).
- Unredacted text persisting after a post-hoc safety block: the client overwrites the message with the error text (`chat_repository.dart:211-216`).
- Client SSE retry re-executing a turn: retries only on 401 before the body is processed (`sse_agent_client.dart:162-168`).
- Semantic-cache exposure on the main chat path: no. Stream never uses the cache, and the default temperature of 0.3 is not cacheable.
