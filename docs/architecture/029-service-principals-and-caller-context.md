# ADR-029 — Service keys, caller context and structured output

**Status:** Accepted (built, not proven live) · **Date:** 2026-10-05 · **Drives:** the first
external integration, Flame in Freefall (`FCDS-College/Nasa`,
`docs/AURA_INTEGRATION_REQUIREMENTS.md` AR-4/AR-5/AR-6) · **Related:** ADR-006 (trust channels),
ADR-022 (untrusted discovered text), ADR-025 (output schemas), AP12a (scoped API keys)

## Context

Flame wants to call AURA after an analysis is measured, pass it the evidence, and get back
interpretation statements it can check before storing them. That needs three things `/api/v3/chat`
did not have:

1. **A caller that is not a person.** The gateway accepted only user or device JWTs. Giving Flame
   a human account's token would make every Flame call that person's, with that person's memory
   and tools, and nothing to revoke but the person.
2. **A place for evidence that is not `message`.** `message` is the USER channel and is read as
   instructions. NASA-derived text (ISA metadata, PDF passages, table free text) is untrusted;
   putting it there bypasses the defence ADR-006 exists for.
3. **Output a program can rely on.** Flame would otherwise parse free text from a small CPU model.

## Decision

### 1. Service keys (a narrow AP12a)

- A key is `aurask_<key_id>_<secret>`, issued by a Super Admin
  (`POST /api/v3/admin/service-keys`) and shown once. Only a SHA-256 of the whole token is
  stored (`aura_service_keys`, Alembic 0013).
- The gateway authenticator recognises the prefix and verifies the key against AURA's own store,
  comparing digests in constant time. A service token never reaches the JWT path.
- **Routes:** each key carries an explicit allowlist drawn from a fixed set in
  `domain/service_keys.py`. Today that set is `POST /api/v3/chat` and
  `POST /api/v3/knowledge/documents`, and the default grant is chat alone.
  - The check runs in the `principal` dependency, so it covers every route that authenticates. A
    route added later is closed to services until someone adds it to the set.
  - The WebSocket routes authenticate per frame, outside that dependency, so they refuse service
    keys outright.
- **No personal state:** a service turn is forced to `use_memory=false`, `use_tools=false`, with
  an empty delegated tool grant and no session (`session_id` → 422).
- **Rate limit:** its own `rate_per_minute`, counted under scope `service`, always enforced.
- **Revocation:** `revoked_at` is read from the store on every request with no cache. A
  revocation therefore refuses the next call on every worker.
- **Audit:** issue, revoke, every chat turn (carrying the caller's correlation id), route
  refusals, rate refusals and use of a revoked key are all audited by key name. The token is
  never recorded.

### 2. Caller context is DATA, and taints the turn

- `ChatRequest.context` holds up to 16 entries of `{id, text ≤ 2000, source}`. Each is sanitised
  as `UntrustedText`.
- Entries go through the same `guard_context_items` scan as retrieved chunks; a quarantined
  entry is dropped. The rest are fenced with the turn nonce in the KNOWLEDGE section, above
  retrieved chunks.
- **Any context sent taints the turn**, whatever the guards found. A side-effecting tool cannot
  run on evidence a calling system supplied unless someone confirms it. Taint therefore does not
  depend on a guard catching a well-phrased injection.
- **Citations:** an entry's `id` is its citation. Citations come from what was actually
  assembled, so only an id the caller sent, and that survived guarding and budget, can be
  returned.

### 3. Structured output repairs once, then fails closed

- `ChatRequest.response_schema` takes the ADR-025 subset with an object at the top level, at most
  8000 characters. The contract is appended to the user turn, not the system prompt, because the
  schema is the caller's input.
- The answer, after output guards, must parse as JSON and match the schema. One surrounding code
  fence is tolerated and nothing else.
- On a mismatch, **one** repair generation sees only the error *paths*. The repair is skipped if
  the turn's generation or cost budget is spent.
  - Still invalid: `422 output_invalid` with those paths.
  - Valid: the response gains `data`.
- Provider JSON mode is requested only when a configured provider supports it. On a
  `CompletionRequest` a schema is a hard routing requirement, and AURA validates the answer
  itself either way.

## Alternatives rejected

- **A long-lived human account for Flame.** It carries memory and tools, it is audited as a
  person, and the only way to revoke it is to lock that person out.
- **Static keys in environment variables.** Revoking one needs a redeploy, so it is not
  immediate. The key would also live in plaintext in the deploy configuration.
- **Evidence in `message` with a "treat as data" instruction.** That is the USER channel. A
  prompt-level request is not a trust boundary (ADR-006).
- **Mirroring Flame's corpus into AURA's knowledge base (Option B).** Still available through the
  `knowledge/documents` route grant, but `context` removes the need for it on the interpretation
  path.
- **Returning best-effort JSON with a warning.** A caller that stores the result would store
  unvalidated output. Failing closed puts the decision with the caller.
- **Taint only when a guard fires.** That makes side-effect safety depend on guard recall.

## Consequences

- A second class of principal exists. `Principal.is_service` must be considered by any future
  code that reads `caller.user_id` as a person.
- A service's `UserId` is `service:<name>`, so it can never equal a person's subject. Knowledge it
  ingests (if granted) is owned by that id.
- Structured turns cost up to two generations. On the CPU deployment (5–10 tok/s) a caller should
  keep `max_output_tokens` small (≤ 400 for Flame, NR-1) so both fit inside the request timeout.
- **Not yet proven:** none of this has run against a live Flame instance or the dev stack.
  `docs/guides/flame-integration-runbook.md` is how that evidence gets produced.
