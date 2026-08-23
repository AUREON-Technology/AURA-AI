# API Reference

The `/api/v3` gateway. Every route requires a bearer token; admin routes additionally require an
`Admin` or `SuperAdmin` role and write an audit record.

```bash
AURA=https://aura.example.com
AUTH="Authorization: Bearer $TOKEN"
```

Production (`aura_service`) serves `/api/v3` only. Unversioned and `/api/v2` paths are not
mounted. New work must target v3.

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
| `confirmed_tools` | string[] | `[]` | Names the user has explicitly authorised for this turn — see `needs_confirmation` |
| `max_output_tokens` | int | 1024 | 16–8192 |
| `temperature` | float | 0.3 | 0.0–2.0. Only `0.0` is cacheable. |

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
quarantined content ([ADR-006](../architecture/006-safety-pipeline.md)). It did **not** run. Ask
the user, then resend with the tool named in `confirmed_tools`. Auto-confirming defeats the
control that breaks the prompt-injection chain.

**`degraded`** — a list of what fell back this turn (`dense_retrieval`, `memory`, `reranker`, …).
The answer is still usable; the list says what it was produced without.

**`confidence`** — calibrated from observable signals (groundedness, verification, retrieval
strength, self-consistency), not by asking the model how sure it is. Below ~0.45 is worth
surfacing to the user.

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
a no-op keyed on a content hash, which keeps nightly refreshes from churning the index.

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
