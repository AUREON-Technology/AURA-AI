# API Reference

The `/api/v3` gateway. Every route requires a bearer token; admin routes additionally require an
`Admin` or `SuperAdmin` role and write an audit record.

```bash
AURA=https://aura.example.com
AUTH="Authorization: Bearer $TOKEN"
```

Production (`aura_service`) serves `/api/v3` only. Unversioned and `/api/v2` paths are not
mounted. New work must target v3.

**Service keys.** Another *system* (not a person) authenticates with a service key,
`Authorization: Bearer aurask_<key_id>_<secret>`, issued by a Super Admin
([Service keys](#service-keys)). A key reaches only the routes it was issued for (403 elsewhere,
WebSockets included), has its own per-minute rate, and a service turn never uses memory, tools or
a session. See [ADR-029](../architecture/029-service-principals-and-caller-context.md).

---

## Conventions

**Correlation.** Every response carries `X-Correlation-Id`. Supply your own on the request and it
is honoured, so a client-side trace and a server-side one describe the same turn. It appears in
every log line and every audit record for that turn.

**Errors** are a consistent shape:

```json
{ "error": "validation_error",
  "message": "human-readable, safe to display",
  "retryable": false,
  "details": { "…": "machine-readable context" } }
```

`retryable` is the field to branch on. A `false` means the same request will fail the same way;
retrying wastes a round trip and, on a metered provider, money.

| Status | When |
| --- | --- |
| 400 | The turn was blocked by a safety guard |
| 401 | Missing, malformed, or expired credential |
| 403 | Authenticated but not permitted (admin routes, missing capability) |
| 404 | Unknown route — including the SEC-01 routes, which are gone |
| 422 | Request failed validation before any work was done |
| 429 | Rate limited (carries `Retry-After`) or the turn's cost budget was exhausted |
| 503 | Every provider failed, or a dependency is unavailable |

**Rate limiting** is cost-weighted, not per-request: a chat turn costs 10 and an agent turn 20
against a health check's 0. Limits are layered per user then per IP.

### Idempotency-Key (optional)

Side-effecting routes accept an optional `Idempotency-Key` request header, following the IETF
draft `draft-ietf-httpapi-idempotency-key-header`. Send a fresh random value (a UUIDv4) per
logical operation and **reuse it verbatim on every retry of that operation** — after a timeout, a
dropped connection, an app restart with a queued write. Omit the header and nothing changes: the
request runs every time, exactly as it did before this existed.

```bash
curl -X POST "$AURA/api/v3/memory" -H "$AUTH" \
  -H "Idempotency-Key: 5b0e9a4e-3c52-4f7e-9d0a-8f1f2a4c7e11" \
  -H "Content-Type: application/json" -d '{"content": "I take my tea with milk."}'
```

| Situation | Answer |
| --- | --- |
| First request with this key | Runs normally |
| Retry, same key and same request, first one finished | The first response, replayed byte for byte (same status and body) with `Idempotent-Replayed: true`. The operation does **not** run again |
| Retry while the first is still running | `409 idempotency_key_in_progress`, `retryable: true`, `Retry-After: 1`. Retry and you get the replay |
| Same key, different request | `422 idempotency_key_reused`, `retryable: false` — a client bug; generate a new key |
| Malformed key (empty, over 255 chars, spaces or non-ASCII) | `400` |

What counts as "the same request": the method, the concrete path (so `DELETE /memory/a` and
`DELETE /memory/b` differ), the query string, and the body — JSON is compared after sorting keys,
so a client that rebuilds its payload on retry still matches; a multipart upload is compared by
its fields and each file's name, type and bytes. The key may be sent bare or as a quoted
structured-field string (`"5b0e…"`); both are the same key.

- **Scope.** A key is scoped to the authenticated user *and* the route (`POST /api/v3/memory` is
  a different namespace from `DELETE /api/v3/memory/{id}`). Two users sending the same key never
  see each other's responses or collide.
- **What is stored.** A response the route *returned* with a status below 500. A 5xx, or an error
  the route *raised* (a typed `4xx` such as `404 not_found`, a `422` body-validation error, a
  `429`, a `503`), is not stored: the key is released and a retry runs the operation again.
- **Lifetime.** A completed key replays for `AURA_IDEMPOTENCY_TTL_HOURS` (default 24); after that
  the same key is a new key. If a request's process dies mid-flight, its claim is taken over by a
  retry after `AURA_IDEMPOTENCY_LEASE_S` (default 900 s) rather than answering 409 for the whole
  TTL. Expired rows are deleted hourly by the `purge-idempotency-keys` beat task.
- **Durability.** With `AURA_DOCUMENT_BACKEND=postgres` keys live in `aura_idempotency_keys`
  (Alembic `0006`), shared by every gateway worker and surviving restarts; the in-memory backend
  is per-process.

Routes that honour the header:

| Route | Why |
| --- | --- |
| `POST /api/v3/chat` | A retried turn would re-run planning and any confirmed side-effecting tool |
| `POST /api/v3/knowledge/documents`, `POST /api/v3/knowledge/documents/upload` | A retry otherwise re-embeds (and, if the content changed in between, versions) |
| `DELETE /api/v3/knowledge/documents/{id}` | The retry gets the first answer instead of `chunks_removed: 0` |
| `POST`/`DELETE /api/v3/knowledge/documents/{id}/cag-pin` | |
| `POST /api/v3/memory`, `DELETE /api/v3/memory/{id}` | A retried delete otherwise answers 404 |
| `POST /api/v3/feedback` | Each call otherwise records a separate rating |
| `DELETE /api/v3/engineering/workspaces/{id}` | |
| `POST /api/v3/admin/index/rebuild`, `POST /api/v3/admin/sources`, `POST /api/v3/admin/approvals/{id}/decide`, `POST /api/v3/admin/learning/request-approval`, `POST /api/v3/admin/learning/jobs`, `POST /api/v3/admin/models/import`, `POST /api/v3/admin/models/{id}/request-promotion` | Expensive or once-only operator actions |

**Not covered, deliberately:** `POST /api/v3/chat/stream` and the WebSocket routes
(`/voice/live`, `/host/agent`) ignore the header. A stream is delivered incrementally to a caller
that has already rendered it; there is no single response to store, and replaying a recorded
stream would re-render text the user already saw. Use `POST /api/v3/chat` with a key when a turn
must run at most once. `POST /api/v3/sync` (cursor sync) does not need it: each mutation carries a
client-minted UUID and a `client_updated_at`, so a replayed batch reports `conflict` per row
instead of writing twice.

---

## Chat

### `POST /api/v3/chat`

One complete turn.

```json
{ "message": "How many days of annual leave do I get?",
  "session_id": "s-42",
  "use_knowledge": true,
  "use_memory": true,
  "use_tools": true,
  "require_local": false,
  "max_output_tokens": 1024,
  "temperature": 0.3 }
```

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `message` | string | — | 1–32000 characters |
| `session_id` | string | none | Omit for a stateless turn. Ownership is re-checked on load; a session id is not a capability. |
| `agent` | string | `orchestrator` | An unknown name falls back to the orchestrator prompt rather than running with none |
| `use_knowledge` / `use_memory` / `use_tools` | bool | `true` | Opt out per turn |
| `plan` | bool | auto | Forces planning on or off. The default answer is *no*: decomposing a simple question wastes a generation. |
| `strategy` | string | auto | `direct`, `chain_of_thought`, `self_consistency`, `tree_of_thoughts` |
| `routing_policy` | string | deployment default | `local_first`, `cheapest`, `fastest`, `quality_first`, `balanced` |
| `require_local` | bool | `false` | Forces on-premise inference regardless of policy |
| `approval_ids` | string[] | `[]` | The `approval_id`s, from one turn's `pending_tool_calls`, of the calls the user approved — see `needs_confirmation`. (`confirmed_tools`/`confirmed_call_ids` are no longer read.) |
| `max_output_tokens` | int | 1024 | 16–8192 |
| `temperature` | float | 0.3 | 0.0–2.0. Only `0.0` is cacheable. |
| `context` | `{id, text, source}[]` | `[]` | Caller-supplied evidence, at most 16 entries. Carried on the DATA channel, never as instructions — see below |
| `response_schema` | object | none | Ask for one JSON value matching this schema. `/chat` only — see below |

Response:

```json
{ "message": "Permanent staff receive 21 days of annual leave per year.",
  "citations": ["handbook@v1 (Employee Handbook > Leave > Annual Leave)"],
  "confidence": 0.82,
  "tools_used": [],
  "needs_confirmation": [],
  "plan": [],
  "correlation_id": "c-7f3a…",
  "turn_id": "t-9b21…",
  "usage": { "prompt_tokens": 412, "completion_tokens": 38, "cost_usd": 0.0 },
  "provider": "local",
  "model": "qwen2.5:7b-instruct-q4_K_M",
  "cached": false,
  "degraded": [] }
```

Four fields are worth handling deliberately:

**`citations`** — empty means the answer is not grounded in the user's documents. Render that
difference; it is the difference between a quoted fact and a plausible one.

**`needs_confirmation`** — a side-effecting tool was selected in a turn whose context contained
quarantined content ([ADR-006](../architecture/006-safety-pipeline.md)). It did **not** run.
`pending_tool_calls` lists each held call (`call_id`, `tool`, `arguments`, `diff_preview`,
`approval_id`). Show the user the arguments, then resend in the same `session_id` with the
approved calls' ids in `approval_ids`. The server resumes the turn it stored — it does not
re-plan — and runs only a call identical to an approved one, once. An unknown, expired, reused, or
another session's id is a `409 confirmation_invalid`; ask the turn again. Auto-confirming defeats
the control that breaks the prompt-injection chain.

**`degraded`** — a list of what fell back this turn (`dense_retrieval`, `memory`, `reranker`, …).
The answer is still usable; the list says what it was produced without.

**`confidence`** — calibrated from observable signals (groundedness, verification, retrieval
strength, self-consistency), not by asking the model how sure it is. Below ~0.45 is worth
surfacing to the user.

#### Caller context (`context`)

Send the evidence an answer must rest on here, never in `message`. `message` is the USER channel
and is read as instructions.

```json
{ "message": "Interpret analysis 01J8ZQ….",
  "context": [
    { "id": "ev_01J8ZQ3K", "source": "flame/evidence", "text": "FLEX-042: n-heptane, O2 0.142, extinction at 41.2 s" }
  ] }
```

| Field | Rules |
| --- | --- |
| `id` | 1–128 of `A-Z a-z 0-9 _ - . :`, unique within the turn. Becomes the citation. |
| `text` | 1–2000 characters. Sanitised: NFKC, no control or bidi characters. |
| `source` | Optional label, ≤ 128 of `A-Z a-z 0-9 _ - . : /` and space. |

Each entry is handled like a retrieved chunk:

- **Guarded:** it is scanned by the same context guards, and a quarantined entry is dropped.
- **Fenced:** it is fenced with the turn's nonce, and an entry that does not fit the budget is
  dropped whole.
- **Taints the turn:** sending any context taints the turn, so a side-effecting tool goes to
  `needs_confirmation` instead of running.
- **Cited by id:** `citations` contains an entry's `id` verbatim when it reached the context. An id
  you did not send can never appear.

`citations` from `POST /chat` are user-facing only. They name documents and your context ids,
never internal `memory:`, `tool:` or `goal:` provenances.

#### Structured output (`response_schema`)

```json
{ "message": "Interpret analysis 01J8ZQ….", "plan": false, "use_tools": false, "max_output_tokens": 400,
  "response_schema": { "type": "object", "required": ["statements"], "properties": {
    "statements": { "type": "array", "maxItems": 5, "items": { "type": "object",
      "required": ["text", "claim_class", "evidence_ids"],
      "properties": {
        "text": { "type": "string" },
        "claim_class": { "type": "string", "enum": ["inferred", "hypothesized", "unsupported"] },
        "evidence_ids": { "type": "array", "items": { "type": "string" } } } } } } } }
```

**The schema.** It must use the subset in `domain/output_schema.py` ([ADR-025](../architecture/025-tool-output-schema.md)):

- `type`, `properties`, `required`, boolean `additionalProperties`;
- `items`, `maxItems`;
- `enum`, `format`, `description`.

The top level must be an object, and the schema may be at most 8000 characters. Anything else is
a 422 before any generation.

**On success** the response gains `data`, the parsed value that matched the schema. `message`
still carries the raw text.

**On a mismatch** AURA repairs once. The repair is told the error paths only, and it is skipped
if the turn's generation budget is spent. If the answer is still invalid:

```json
{ "error": "output_invalid", "retryable": false,
  "details": { "errors": ["$.statements[0].claim_class: not one of the declared values"], "repaired": true } }
```

with status **422**. Errors name paths, never values. Unvalidated JSON is never returned as
`data`.

**Cost.** A structured turn can cost two generations. On a CPU deployment, keep
`max_output_tokens` small so both fit in your timeout.

`POST /chat/stream` refuses `response_schema` with a 422, because a streamed answer reaches you
before it can be checked.

### `POST /api/v3/chat/stream`

Same request body. Returns `text/event-stream` with **typed** frames, so the client never has to
string-split a marker out of a token stream.

```
data: {"type":"citations","citations":["handbook@v1 (…)"]}
data: {"type":"token","text":"Permanent "}
data: {"type":"token","text":"staff receive "}
data: {"type":"done","tainted":false,"guards_complete":true,"citations":[…],"turn_id":"t-9b21…","correlation_id":"c-7f3a…","confidence":null,"degraded":false,"usage":{"prompt_tokens":412,"completion_tokens":38,"cost_usd":0.0},"needs_confirmation":[]}
```

| Frame | Fields | Meaning |
| --- | --- | --- |
| `citations` | `citations` | Emitted before the first token, so sources can render immediately |
| `token` | `text` | Append |
| `tool_calls` | `calls` | Tools the model selected (informational; not executed) |
| `step` | `step_id`, `status`, `description` | Plan progress on an agentic turn |
| `actions` | `actions` | Personal-OS proposals (`add_task`, `add_event`, …) for client confirmation |
| `revision` | `reason`, `text` | The answer was redacted *after* streaming — replace what you displayed |
| `done` | `tainted`, `guards_complete`, `citations`, `needs_confirmation`, `turn_id`, `correlation_id`, `confidence`, `degraded`, `usage` | Terminal, success. `confidence` is null on the stream path (the reasoner does not run). Use `turn_id` for `POST /feedback`. |
| `error` | `error`, `message`, `retryable`, `post_hoc` | Terminal, failure |

> **A limitation stated in the contract, not hidden.** Guards that need the complete answer —
> system-prompt leakage, groundedness — cannot run before the first token is emitted. They run at
> the end, and `guards_complete` on the `done` frame reports whether they did. Guards over a
> bounded window (secrets) run per chunk. A `revision` frame or an `error` with `post_hoc: true`
> means something was caught after display. A deployment that cannot accept post-hoc detection
> should use `POST /chat` instead.

---

## Knowledge

### `POST /api/v3/knowledge/documents`

```json
{ "title": "Employee Handbook", "content": "# Leave\n…", "document_id": "handbook" }
```

Returns `{ "document_id", "version", "chunks", "created", "quarantined_chunks", "skipped_reason" }`.

`created: false` with `chunks: 0` means identical content was already ingested — re-ingestion is
a no-op keyed on a content hash, which keeps nightly refreshes from churning the index. The hash
is compared against the caller's *active* documents only, so re-uploading a deleted document, or
reverting a document to earlier content, indexes it again.

Without `document_id`, the id is derived deterministically from the caller and the title, so the
same upload retried on another worker or after a restart lands on the same document (and
overwrites any partially written chunks) instead of creating a duplicate. A `document_id` that
already belongs to another user is refused with `409 conflict`.

`quarantined > 0` means chunks contained instruction-shaped content and were fenced. They are
still retrievable; they cannot issue instructions, and a turn that retrieves one cannot run a
side-effecting tool unconfirmed.

### `POST /api/v3/knowledge/search`

```json
{ "query": "annual leave policy", "limit": 6 }
```

```json
{ "query": "annual leave policy",
  "strategy": "hybrid",
  "candidates_considered": 24,
  "results": [ { "content": "…", "citation": "handbook@v1 (…)",
                 "score": 0.87, "document_id": "handbook", "version": 1 } ],
  "searched_but_empty": false }
```

**`searched_but_empty: true` with `results: []` is a real answer**: the corpus was searched and
nothing cleared the relevance floor. Render it as "not found", not as an error. `strategy:
"unavailable"` with an empty list is different — nothing was searched, because no index was
ready.

### `GET /api/v3/knowledge/documents`

Owner-scoped library. Query: `limit` (1–100, default 50), `offset`.

```json
{ "documents": [ { "document_id": "handbook", "title": "Employee Handbook",
                   "version": 1, "status": "active", "created_at": "2026-08-23T08:00:00+00:00" } ] }
```

### `DELETE /api/v3/knowledge/documents/{id}`

Returns `{ "deleted": true, "chunks_removed": N }`. Deleting an unknown document returns
`chunks_removed: 0` rather than 404 — the end state the caller wanted is reached either way.

---

## Memory

### `GET /api/v3/memory?query=&limit=`

Omit `query` to list recent records; supply one to search semantically.

### `POST /api/v3/memory`

```json
{ "content": "The user is vegetarian.", "subject": "diet", "kind": "semantic" }
```

Kinds: `working`, `episodic`, `semantic`, `entity`, `procedural`, `reflection`
([ADR-004](../architecture/004-memory-taxonomy.md)). A near-identical existing record is returned
rather than duplicated.

### `DELETE /api/v3/memory/{id}`

Owner-scoped. Deleting another user's record returns a failure, not a silent success.

To honour a broader deletion request, `memory.forget_source(owner_id=…, source=…)` removes
everything learned from one source — possible only because every write carries provenance.

---

## Feedback

### `POST /api/v3/feedback`

```json
{ "turn_id": "t-9b21…", "rating": 1, "correction": "It is 21 days, not 20." }
```

`rating` is 1–5. A **correction** is the highest-signal training example the system can obtain,
which is why the endpoint accepts one — it feeds the curation pipeline
([ADR-008](../architecture/008-human-in-the-loop-learning.md) Loop B). Nothing trains on it
without a recorded human approval.

---

## Introspection

`GET /api/v3/tools` — tools available to *this* caller, filtered by their capability grants.
`GET /api/v3/agents` — configured agents and their prompts.

---

## Admin

Admin+ routes require `Admin` or `SuperAdmin`. SuperAdmin-only routes require `SuperAdmin`.
Mutations write an audit record.

Browser operators exchange a Google ID token at `POST /api/v3/auth/web/token` (no device
attestation). That path is fail-closed: only Developer+ receive a session. Mobile
`POST /api/v3/auth/token` still requires Play Integrity / App Attest.

| Route | Role | Purpose |
| --- | --- | --- |
| `GET /admin/status` | Admin+ | Health, providers, prompts, tools, settings, learning slice, last refresh |
| `POST /admin/index/rebuild` | Admin+ | Build a candidate index and promote it if the gates pass |
| `POST /admin/index/rollback` | Admin+ | O(1) alias repoint to a previous generation |
| `GET /admin/indexes` | Admin+ | Listed generations and the active alias |
| `POST /admin/prompts/{id}/pin` | Admin+ | Pin a prompt version |
| `POST /admin/providers/{name}/reset-circuit` | Admin+ | Close a circuit breaker |
| `GET /admin/sources` | Admin+ | Approved knowledge sources |
| `PATCH /admin/sources/{id}` | Admin+ | Enable or disable a source |
| `POST /admin/sources` | SuperAdmin | Register a source |
| `DELETE /admin/sources/{id}` | SuperAdmin | Remove a source |
| `GET /admin/approvals` | Admin+ | Pending and historical approvals |
| `POST /admin/approvals/{id}/decide` | Admin+ | Approve or reject |
| `GET /admin/models` | Admin+ | Model registry |
| `POST /admin/models/{id}/transition` | Admin+ | Stage change (production still needs an approval) |
| `POST /admin/models/{id}/rollback` | Admin+ | Restore a previous production version |
| `GET /admin/local-models` | Admin+ | Installed Ollama weights |
| `POST /admin/local-models/pull` | SuperAdmin | Allowlisted pull |
| `DELETE /admin/local-models/{name}` | SuperAdmin | Delete installed weights |
| `GET /admin/audit` | Admin+ | Append-only audit browser |
| `GET /admin/refresh/latest` | Admin+ | Last nightly `RefreshReport` |
| `GET /admin/jobs/{task_id}` | Admin+ | Celery task state |
| `GET /admin/operators` | Admin+ | Operator directory (read) |
| `PATCH /admin/operators/{email}/role` | SuperAdmin | Assign a role |
| `POST /admin/operators/{email}/revoke-sessions` | SuperAdmin | Drop refresh tokens |
| `POST /admin/users/{subject}/forget` | SuperAdmin | GDPR erase; returns counts only |
| `GET /admin/me` | Developer+ | Resolved role for the console chrome |

### Service keys

SuperAdmin only. The token is in the `POST` response **once**. AURA stores only its SHA-256, and
no route, log or audit record can return it again.

| Route | Purpose |
| --- | --- |
| `POST /admin/service-keys` | Issue: `{name, routes?, rate_per_minute?}` → `201 {key, token}` |
| `GET /admin/service-keys` | List keys (id, name, routes, rate, created/revoked); never the token or digest |
| `POST /admin/service-keys/{key_id}/revoke` | Revoke; the key's next request is a 401 |

The fields of an issue request:

- **`name`:** a lower-case slug, such as `svc-flame`. The key acts as `service:<name>`.
- **`routes`:** drawn from `POST /api/v3/chat` and `POST /api/v3/knowledge/documents`. The
  default is chat only.
- **`rate_per_minute`:** 1–600 turns per minute, default 10. A key over its rate gets a 429 with
  `Retry-After`.

Every service chat turn is audited under the key's name with your `X-Correlation-Id`, so you can
query a turn back by the caller's own request id (`GET /admin/audit?actor=svc-flame`). Refused
routes, rate refusals and use of a revoked key are audited the same way.

`/admin/status` never contains an API key: `ProviderConfig` stores only the *name* of the
environment variable holding one, and `api_key` is a property rather than a field, so the
serialisation walk cannot reach it even accidentally. Each provider reports
`api_key_configured: true|false` instead.

`POST /admin/index/rebuild` returns `promoted: false` with `gate_failures` when a gate blocks the
new index. **Nothing broke** — the old index is still serving. See
[Administrator Guide → Reindexing](administrator-guide.md#reindexing).

Resetting an unknown provider circuit is a 422 naming the known providers, not a silent success:
an operator must not be able to believe a circuit was reset while the outage continues.

---

## Health and metrics

| Route | Auth | Purpose |
| --- | --- | --- |
| `GET /health/live` | none | Process liveness only. Deliberately checks nothing else — a liveness probe that fails during a dependency outage makes the orchestrator restart a healthy process. |
| `GET /health/ready` | none | Dependency readiness; 503 when not ready. **This** is what a load balancer should gate on. |
| `GET /metrics` | none, but never publicly routed | Prometheus exposition. Nginx returns 404 for it; Prometheus scrapes the gateway directly over the internal network. |

Health endpoints are never rate limited. A limiter that can starve the readiness probe turns load
into an outage.

---

Related: [Deployment Guide](deployment-guide.md) · [Administrator Guide](administrator-guide.md) ·
[Developer Guide](developer-guide.md)
