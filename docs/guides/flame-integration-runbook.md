# Flame in Freefall integration runbook

How to connect AURA to Flame in Freefall (`FCDS-College/Nasa`) and produce the evidence that each
link works. Flame's side of the contract is `docs/integration/AURA_INTEGRATION_REQUIREMENTS.md` in
that repository, and its current endpoint reference is `docs/integration/API_INTEGRATION_REFERENCE.md`; the AURA requirements it names (AR-1…AR-8) are built in this repository:

| AR | What | Where |
| --- | --- | --- |
| AR-1 | Typed OpenAPI output schemas | `adapters/connectors/openapi.py` (#154) |
| AR-2 | Optional query parameters on generated GET tools | ADR-022 notes |
| AR-3 | Credentialed read-only OpenAPI connectors (`read_only_attested`) | ADR-021 notes |
| AR-4 | Service keys (`svc-flame`) | ADR-029 §1, [API reference](api-reference.md#service-keys) |
| AR-5 | `context` on `/chat` (DATA channel) | ADR-029 §2 |
| AR-6 | `response_schema` on `/chat` | ADR-029 §3 |
| AR-7 | Live proof of Link 2 | **this runbook, §2 — run on Railway 2026-10-07, see `docs/reports/flame-link2-live.md`** |
| AR-8 | Re-discovery on contract change | this runbook, §4 |

**Built is not proven.** Link 2 (§2) has run once against a live Flame API, on Railway on
2026-10-07, and that report lacks items §2.6 asks for. Link 1 (§3) has not run. The integration
counts as proven only when complete reports for §2.6 and §3.4 exist.

All admin calls need a Super Admin token: `ADMIN="Authorization: Bearer $SUPERADMIN_JWT"`.

---

## 1. Before you start

**AURA settings** (both are off by default):

- `AURA_ADAPTATION_ENABLED=true`.
- `AURA_PROJECT_DATA_ROLES=<role>`, naming the role of the people who will ask Flame questions.
  Generated agents and tools need the `project_data` capability.

**Flame:**

- The read endpoints (FR-1) and `GET /api/openapi.json` must be reachable from the AURA gateway's
  network, **over https**: the connector sends its credential only on https.
- Flame requires a bearer token on every `/api/*` call except `GET /api/openapi.json`
  (`FLAME_API_AUTH_ENABLED` defaults to true). Use Flame's **read** token (`FLAME_READ_API_TOKEN`)
  as the connector `secret` (§2.1). Never give AURA the write token. Do not turn Flame's auth off
  to make the connector work.

**Offline check first.** From a Flame checkout, run the dry run against the AURA revision you
will deploy:

```bash
AURA_BACKEND=/path/to/AURA-AI/aura_backend python tests/integration/aura_blueprint_dryrun.py
```

It must exit 0 with all 10 tools `ACCEPTED`, report 3 operations excluded by `x-aura-exclude`, and
print no `not reachable by AURA` lines. Checked against Flame contract 0.3.0 and AURA `6417c43`.

## 2. Link 2: AURA reads Flame (AR-7)

### 2.1 Register the connector

```bash
curl -X POST "$AURA/api/v3/admin/connectors" -H "$ADMIN" -H "Content-Type: application/json" -d '{
  "id": "flame-api", "kind": "openapi", "identity_mode": "service_account",
  "display_name": "Flame in Freefall",
  "endpoint": "https://flame.internal/api/openapi.json",
  "options": {"base_url": "https://flame.internal", "allow_private_network": true}
}'
```

- `allow_private_network` is needed when Flame resolves to a private address. Loopback,
  link-local and metadata ranges stay refused even with it.
- **Add Flame's read token** (required, see §1):
  - add `"secret": "<token>"` to the body, and `"read_only_attested": true` to `options`;
  - the token is vaulted and never echoed;
  - the attestation is shown to the approver and bound into the approval digest.

  Without the attestation, a credentialed connector is refused at activation.

### 2.2 Approve and activate

A second person should decide the approval. The code does not refuse a self-approval (a sole
operator has no one else), but it records the requester in the approval payload
(`requested_by`) and marks a decision by that same person as `self_approved: true` in the audit
entry, so the record shows which kind of decision it was:

1. The approver runs `POST /api/v3/admin/approvals/{approval_id}/decide` with `{"approved": true}`.
2. Then call `POST /api/v3/admin/connectors/flame-api/activate` with `{"approval_id": "..."}`.
3. Expect `"state": "active"`.

With a token, the activation audit entry carries `findings: ["credential_scope_attested"]`.

### 2.3 Discover, compile, evaluate

```bash
curl -X POST "$AURA/api/v3/admin/twin/snapshot" -H "$ADMIN" -d '{"connector_ids": ["flame-api"]}'
curl -X POST "$AURA/api/v3/admin/twin/versions/$TWIN/compile" -H "$ADMIN"
curl -X POST "$AURA/api/v3/admin/blueprints/$BP/evaluate" -H "$ADMIN"
```

- The compiled blueprint should hold the same 10 tools as the dry run. `api_search` should take
  `q` plus the optional `type`, `source_id`, `experiment_id`, `domain`, `page` and `page_size`.
- A `tool_contract` case that cannot reach a replica is `not_run`, never a pass. Record the gate
  report as it is.

### 2.3a Operations Flame keeps out of discovery (`x-aura-exclude`)

From Flame contract 0.2.0, `docs/api/openapi.json` also describes `POST /api/chat` and Flame's
admin writes. Flame marks those operations with the vendor extension `x-aura-exclude: true`.
The OpenAPI connector's discovery (`aura_core/adapters/connectors/openapi.py`) skips any
operation, or any whole path item, that carries the literal `true`. Each skipped operation is
reported as a discovery issue `operation_excluded` with its `op:<METHOD> <path>` locator, so the
exclusion appears in the discovery report and the twin diff rather than vanishing silently.

The reason is that Flame's `/api/chat` itself calls AURA's `/api/v3/chat` (Link 1). A tool compiled
from it would let AURA call back into its own caller. Expect one `operation_excluded` issue per
marked operation. The 10 compiled read tools are `api_get_health`, `api_get_dashboard_counts`,
`api_search`, `api_list_documents`, `api_get_document`, `api_list_experiments`,
`api_get_experiment`, `api_get_evidence`, `api_list_analyses` and `api_get_analysis`. From
contract 0.3.0, `api_get_analysis` also returns video analyses (`analysis_type: flame_video`).

### 2.4 Approve and deploy

```bash
curl -X POST "$AURA/api/v3/admin/blueprints/$BP/request-approval" -H "$ADMIN"
# Ideally a second person decides the approval (a self-approval is audited as one), then:
curl -X POST "$AURA/api/v3/admin/blueprints/$BP/approve" -H "$ADMIN" -d '{"approval_id": "..."}'
curl -X POST "$AURA/api/v3/admin/blueprints/$BP/deploy" -H "$ADMIN"
curl "$AURA/api/v3/admin/blueprints/active" -H "$ADMIN"   # names the bp<N>_ agents and tools
```

### 2.5 Ask a question

As a person whose role is in `AURA_PROJECT_DATA_ROLES`, send:

```bash
curl -X POST "$AURA/api/v3/chat" -H "Authorization: Bearer $USER_JWT" -d '{
  "agent": "<bp agent id from /blueprints/active>", "plan": true,
  "message": "Which FLEX heptane tests extinguished below 0.15 O2? Cite evidence ids."
}'
```

The answer should come from a `bp<N>_api_search` (or `bp<N>_api_get_*`) call whose result
carries a Flame `evidence_id`.

### 2.6 Evidence to keep

Write `docs/reports/flame-link2-live.md` with the following:

- the AURA and Flame revisions;
- the connector, twin and blueprint ids and digests;
- the gate report;
- the deploy audit entries;
- the question;
- the tool call (from `GET /api/v3/admin/audit?action=tool.bp<N>_api_search`);
- the answer, with the Flame `evidence_id` it cites.

**That report is AR-7.** `docs/reports/flame-link2-live.md` (2026-10-07) is the first one. It is
missing items from the list above: the deploy audit entries, the tool-call audit record, full
digests and the full gate report. Until a run records all of them, Link 2 is built, not proven.

## 3. Link 1: Flame asks AURA to interpret (AR-4/5/6)

### 3.1 Issue the service key

```bash
curl -X POST "$AURA/api/v3/admin/service-keys" -H "$ADMIN" -d '{"name": "svc-flame", "rate_per_minute": 6}'
```

- Store the returned `token` in Flame's secret store as `FLAME_AURA_SERVICE_TOKEN`, and set
  `FLAME_AURA_ENABLED=true` on both the Flame API and worker. The token is shown once.
- The key reaches `POST /api/v3/chat` only. Add `"routes": ["POST /api/v3/chat",
  "POST /api/v3/knowledge/documents"]` only if you choose the corpus-mirror option.

### 3.2 The interpretation request (Flame's `AuraInterpreter`, FR-6)

```json
{ "message": "Interpret analysis <analysis ULID> for experiment <experiment ULID>.",
  "plan": false, "use_tools": false, "use_memory": false, "use_knowledge": false,
  "require_local": true, "max_output_tokens": 400, "temperature": 0.0,
  "context": [ { "id": "<evidence ULID>", "source": "flame/evidence", "text": "<measured value + condition>" } ],
  "response_schema": { "type": "object", "required": ["statements"], "additionalProperties": false,
    "properties": { "statements": { "type": "array", "maxItems": 8, "items": {
      "type": "object", "required": ["text", "claim_class", "evidence_ids"], "additionalProperties": false,
      "properties": {
        "text": { "type": "string" },
        "claim_class": { "type": "string", "enum": ["inferred", "hypothesized", "unsupported"] },
        "evidence_ids": { "type": "array", "items": { "type": "string" } } } } } } } }
```

- Send `X-Correlation-Id: <Flame request id>` on every call. AURA echoes it as `correlation_id`
  and audits the turn under `svc-flame` with that id (CR-2).
- NASA-derived text goes **only** in `context`, never in `message` (SR-1).
- The `enum` keeps AURA from emitting `observed`, `measured` or `estimated` (CR-3). Flame's own
  validator (FR-7) still has to check `evidence_ids` against its database.

### 3.3 Error mapping (CR-5)

| AURA | Flame |
| --- | --- |
| 200 with `data` | validate (FR-7), then store |
| 422 `output_invalid` | From Flame PR #21 (FCDS-College/Nasa), Flame's router falls back to its local model; if none answers, `interpretation.status="unavailable"` with `INTERPRETATION_UNAVAILABLE`. Not retried at AURA |
| 429 | `retryable=true`; honour `Retry-After` |
| 401 | key revoked or wrong; page an operator |
| 503 | `INTERPRETATION_UNAVAILABLE` |

Idempotency. Flame sends `Idempotency-Key: <analysis or request id>:<16 hex of the body's SHA-256>`
and `X-Correlation-Id: <id>`. An exact retry is deduplicated; a re-run with new evidence is a new
request rather than `422 idempotency_key_reused`. AURA does not store a 429 or a `retryable: true`
answer, so a retry after a refusal runs the turn again.

`require_local`. Flame sends `require_local: true` by default. `/health/ready` reports
`local_healthy`; when it is false every Flame call fails with 503 while `healthy` is still true.
Either run a provider marked local, or set Flame's `FLAME_AURA_REQUIRE_LOCAL=false` where AURA's
model is remote by design.

On the CPU deployment, 400 tokens at 5–10 tok/s plus one possible repair is roughly 40–160 s. That
is why Flame's 120 s timeout must stay off the dashboard path (NR-1).

### 3.4 Evidence to keep

Write `docs/reports/flame-link1-live.md` with the following:

- one analysis on the dev stack with `interpretation.status="completed"` and validated statements;
- the matching `service.chat` audit entry, found by correlation id;
- one run with AURA stopped, where the analysis still ends `completed` with
  `INTERPRETATION_UNAVAILABLE`.

### 3.5 Revoke

```bash
curl -X POST "$AURA/api/v3/admin/service-keys/$KEY_ID/revoke" -H "$ADMIN"
```

The key's next request gets a 401, on every worker.

## 4. When Flame's contract changes (AR-8)

Until AP11 (drift and impact) exists, contract drift is handled by hand:

1. **On every change to Flame's `docs/api/openapi.json`,** run the offline dry run (§1) against the
   deployed AURA revision.
   - A breaking change needs a semver bump of `info.version` (CR-4).
2. **When `info.version` changes,** run `POST /admin/twin/snapshot`, then `GET /admin/twin/diff`,
   then compile → evaluate → approve → deploy (§2.3–2.4).
3. **Between the change and the redeploy,** a generated tool whose response no longer matches its
   output schema fails closed with `invalid_output`. It never returns unchecked data. Treat that
   error on a `bp<N>_api_*` tool as the drift signal.
4. **Roll back** with `POST /admin/blueprints/rollback` if the new blueprint misbehaves.
