# ADR-021 — Connector ports, identity modes and the credential vault

**Status:** Proposed · **Date:** 2026-10-01 · **Track:** [AP](../roadmaps/AURA_ADAPTIVE_PLATFORM_ROADMAP.md)

## Context

AURA has no connector framework, OpenAPI parsing or database introspection; the only external
integrations are GitHub, MCP and an HTTPS fetcher. Track AP needs to discover and read SQL
databases, OpenAPI services, files and Git repositories — holding credentials far more sensitive
than anything AURA stores today.

## Decision

1. **Ports** (`aura_core/ports/connectors.py`, async per the ports fitness test): `Connector`
   (descriptor, health), `DiscoveryConnector` (`discover()` → raw artefacts), `ReadConnector`
   (`read(QueryTemplate, params, identity)`). **There is no write method in v1.** Writes arrive
   later as a separate `WriteConnector` port behind argument-bound confirmation (SEC-01), so a v1
   connector cannot write by construction.
2. **Data access is federated:** structured data is read live through connectors, never bulk
   copied. Only documents and text are indexed into project knowledge.
3. **Identity mode per connector:**
   - `DELEGATED` — the end user's OIDC/OAuth token is forwarded per call; the target system
     enforces its own permissions; AURA RBAC is an additional filter. Tokens are never persisted.
   - `SERVICE_ACCOUNT` — a vaulted credential; discovered roles are mapped to AURA policies, and
     AURA is the enforcing authority. The health check refuses database roles holding write or DDL
     grants.
4. **Credential vault:** a `CredentialVault` port; connectors and every Track AP record hold only a
   `secret_ref`. Credentials are encrypted at rest with a per-stack key, and never appear in
   blueprints, twins, prompts, logs or telemetry (fitness and redaction tests).
5. Registering a connector requires a Super Admin approval (`ApprovalKind.CONNECTOR_REGISTRATION`).

## Alternatives rejected

- **Replicate target data into AURA stores.** Duplicates customer data and adds a consistency
  problem; reconsider only with evidence that live reads cannot meet latency SLOs.
- **Service account everywhere.** Makes AURA the sole permission authority even where the target
  can enforce its own.
- **Read/write connector from day one.** Writes depend on SEC-01, which is still open.

## Implementation notes (AP2, 2026-10-01)

- **Key.** The vault seals with `AURA_SECRETS_ENCRYPTION_KEY`, the third-party-credentials key
  (`secrets_crypto.py`). It is now accepted as a newest-first list for rotation. It does not use
  the data key.
- **Primitive.** The primitive is `FieldCipher`. Each ciphertext is bound to
  `connector_credential:<secret_ref>`, so moving a ciphertext to another reference fails
  authentication.
- **No plaintext.** With no key, `put` and `get` refuse. A plaintext value found at rest is
  refused, not used. Migration 0009 also enforces this with a CHECK on the `aura-enc:v1:`
  envelope.
- **Activation** (`ConnectorRegistry.activate`) requires all of:
  - an approved, unexpired `CONNECTOR_REGISTRATION` approval for this connector, bound to the
    descriptor's current digest, which covers `secret_ref`;
  - a healthy check;
  - `write_capable is False`.

  For PostgreSQL, "could write" means any of: superuser, `CREATEROLE`, `CREATEDB`, replication,
  `BYPASSRLS`, any table `INSERT`/`UPDATE`/`DELETE`/`TRUNCATE` (inherited grants included), schema
  `CREATE`, or database `CREATE`. The connector also refuses AURA's own database.
- **HTTP.** The register route moves `secret` into a redacting `Credential` before anything
  validates the body, because FastAPI's 422 responses echo input. It is not idempotency-keyed,
  because that store keeps a body hash, which would be an offline brute-force target for a weak
  password.

## Implementation notes (Flame integration AR-3, 2026-10-05)

- **Credentialed read-only OpenAPI connectors.** An OpenAPI connector with a credential reports
  `write_capable=None` (`credential_scope_unknown`). AURA cannot see a bearer token's scope, so
  activation refused such a connector, and only anonymous APIs could be connected.
- **The attestation.** A Super Admin may now attest read-only scope with the connector option
  `read_only_attested: true`:
  - Options are part of the descriptor, so the attestation is bound into the digest the approver
    signs. Flipping it after approval breaks that digest.
  - Health then reports `write_capable=False` with the finding `credential_scope_attested`, and
    the activation audit records that finding.
  - The option is boolean, and it applies to OpenAPI only. Any other kind refuses it rather than
    ignoring it.
- **What it is not.** The attestation is a recorded human claim, not a probe. On AURA's side the
  enforcement is unchanged: every generated template is a `GET`, and there is no write port.

## Implementation notes (AP10 identity modes, 2026-10-18)

- **The port carries identity.** `ReadConnector.read(template, arguments, identity=None)` takes a
  `CallerIdentity` (`user_id`, the source roles AURA bound the caller to, and the caller's
  delegated token for that connector only), as decision 1 always described.
  `GeneratedReadTool` builds it from the turn's `ToolContext`.
- **Delegated: how a token travels.**
  - A client sends `X-Aura-Connector-Token: <connector id>=<token>` on `/chat` or
    `/chat/stream`. It is a header rather than the body, so idempotency never hashes it and a
    body log never holds it.
  - The value is wrapped in `Credential` at parse time. Errors name the problem, never the
    token. A service key may not send one.
  - From there it is carried in memory only: `TurnRequest.connector_tokens`, then
    `ToolContext.credentials["connector:<id>"]`, then that one connector's read. `delegate_to_agent`
    sub-turns carry the same person's tokens.
- **Delegated reads (OpenAPI)** send the caller's token as the bearer, and are refused with a
  re-authentication hint (`details.reauthenticate: true`) in each of these cases:
  - **No token** (`delegated_token_missing`): refused before anything is sent.
  - **A plainly expired JWT** (`delegated_token_expired`): the `exp` claim is read without
    verification, with a 30 s leeway, and the request is refused before it is sent. An opaque
    token is left to the target.
  - **A 401/403 from the target** (`delegated_token_rejected`): refused once, never retried.

  No stored, anonymous or service credential ever stands in. A delegated connector that somehow
  holds a stored credential is refused outright (`delegated_misconfigured`), behind the
  registration rule that already forbids one.
- **The gate has no person, so it holds no token.** A delegated connector's contract cases are
  `not_run: delegated_identity`; they are never borrowed through another credential.
- **Service account: role bindings.** `Blueprint.security.role_bindings` (AURA role → AUSM ROLE
  id) is a compile input (`POST /admin/twin/versions/{v}/compile` with `{"role_bindings": {...}}`),
  checked against the twin's discovered roles. It is part of the approved digest. With bindings,
  `adaptation/access.py` maps them onto the existing enforcement, with no new enforcement point:
  - A tool that `permission_map` grants to any role needs `project_data:<capability>`. A tool
    whose capability no discovered role governs stays at `project_data`.
  - A role agent needs `project_role:<role>`.
  - The gateway adds those names to a `project_data` holder's capabilities from the blueprint
    this worker is serving (`BlueprintDeployment.caller_capabilities`), on chat, streaming, live
    voice, `/tools` and `/agents`. Look-alike names a caller could otherwise carry are stripped
    first.
  - `ToolPolicy` and `AgentRegistry.resolve` do the refusing. The gate acts with every grant the
    blueprint can give, so each agent is still held to its own tool grant.
- **Unchanged without bindings.** A blueprint compiled without bindings has the same digest and the
  same behaviour as before (`role_bindings` is omitted from the encoding while empty).

## Consequences

- Live reads put load on the target system, so per-connector concurrency limits and timeouts are
  part of the descriptor.
- An expired delegated token fails closed and is never silently retried with the service account.
