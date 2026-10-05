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

## Consequences

- Live reads put load on the target system, so per-connector concurrency limits and timeouts are
  part of the descriptor.
- An expired delegated token fails closed and is never silently retried with the service account.
