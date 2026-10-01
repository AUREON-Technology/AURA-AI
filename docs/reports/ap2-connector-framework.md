# AP2: Connector Framework and Credential Vault (evidence)

**Phase:** AP2 ([Track AP roadmap](../roadmaps/AURA_ADAPTIVE_PLATFORM_ROADMAP.md))
**Gap:** GAP-050 · **ADR:** 021 · **Date:** 2026-10-01 · **Base:** `af5b460` (main)

**Maturity reached:** IMPLEMENTED. Tests were executed locally, including against a real
PostgreSQL 16.

Exit criteria still open:

- [ ] Backend CI green on `main` with these tests (needs the CI baseline fixes; see the AP1
  report).
- [ ] One Postgres connector registered → approved → activated on the **dev stack**, with
  `AURA_ADAPTATION_ENABLED=true` and a read-only role, recorded here.

## What was built

**Domain** (`aura_core/domain/connectors.py`)

- `ConnectorDescriptor` is non-secret by construction:
  - an endpoint that embeds `user:pass@` or a credential-looking query is refused;
  - an option whose key looks like a secret is refused;
  - raw sources (Postgres, MySQL, SQL Server, files, native) must use a service account.
- Its digest covers `secret_ref`, so swapping the vaulted credential after review invalidates the
  approval.
- Supporting types: `ConnectorRecord`, `ConnectorHealth` (low-cardinality reason codes,
  `write_capable`, findings), and `CallerIdentity`, whose delegated token is redacted.
- `Credential` redacts itself in `repr`, `str` and `format`. `reveal()` is the only way to the
  value.

**Ports** (`aura_core/ports/connectors.py`)

- `Connector`, `ConnectorFactory`, `CredentialVault` and `ConnectorStore`, all async.
- There is no discovery or read port yet (AP3/AP6), and no write port at all (AP12c).

**Engine** (`aura_core/adaptation/connectors.py`): `ConnectorRegistry`.

- **Register.** The credential is vaulted and a `CONNECTOR_REGISTRATION` approval is requested
  against the descriptor digest. If registration fails, the vaulted credential is rolled back.
- **Activate.** Requires all of:
  - an approved and unexpired approval;
  - of the right kind, for this connector, and bound to the descriptor's *current* digest;
  - a healthy check;
  - `write_capable is False`.

  Refusals are audited.
- **Disable and remove.** Removing a connector deletes its credential. `open_active()` hands out
  active connectors only.
- **Health.** Health checks are bounded by a timeout. A connector that raises is reported by
  exception type only, never by message. A credential that can't be read makes the connector
  `credential_unavailable`; there is no plaintext fallback.

**Vault** (`aura_core/adapters/credential_vault.py`)

- Built on `FieldCipher` under `AURA_SECRETS_ENCRYPTION_KEY` (newest-first list, for rotation).
- Each ciphertext is context-bound to its reference.
- With no key, it refuses both storing and reading. It never stores or returns plaintext.

**PostgreSQL** (`aura_core/adapters/connectors/postgres.py`)

- Connects with `default_transaction_read_only=on` and inspects the role inside a read-only
  transaction.
- Findings: `superuser`, `createrole`, `createdb`, `replication`, `bypassrls`, `table_write`
  (grants inherited through roles included), `schema_create`, `database_create`.
- Refuses AURA's own database.
- The driver is imported lazily.

**Storage**

- `aura_core/adapters/postgres_adaptation.py`, plus migration `0009`.
- The adapter issues no runtime DDL. Migration 0009 is the only schema authority.
- `aura_connector_credentials` has a CHECK constraint that refuses anything without the
  `aura-enc:v1:` envelope.

**HTTP** (`aura_core/api/admin_adaptation.py`): Super Admin only.

- Routes: `GET`/`POST /admin/connectors`, `GET`/`DELETE /admin/connectors/{id}`, and `POST`
  `/health`, `/activate` and `/disable` on a connector.
- They answer 503 when the flag is off and stay mounted, so the route-auth suite covers them.
- `secret` is removed from the body before anything validates it, because FastAPI's 422 responses
  echo input.
- Responses carry `has_credential`, never the credential or its reference.

**Operations**

- `AURA_ADAPTATION_ENABLED` is forwarded in **both** compose files (parity rule) and documented in
  `deploy/.env.example`.
- New metric: `aura_connector_health{connector,kind}`.
- `GAP_REGISTER.md` and `docs/guides/api-reachability.md` are regenerated.

## Verification

```
cd aura_backend
pytest                                    # 3012 passed, 56 skipped
TESTING=True python -m unittest discover -p "test_*.py"     # Ran 100, OK (skipped=4)
AURA_TEST_DATABASE_URL=postgresql://aura:***@localhost:55436/postgres \
  pytest tests/load/test_adaptation_postgres.py tests/load/test_knowledge_graph_postgres.py -m load
                                          # 21 passed (12 new + 9 existing)
```

**Baseline.** On clean `main` (`af5b460`) the same environment gave 2920 passed, 0 failed. The
92 extra tests break down as:

- 57 in `tests/unit/test_adaptation_connectors.py`;
- 27 in `tests/architecture/test_connector_secrets.py`;
- 7 route-auth cases, enumerated from the 7 new routes;
- 1 new layering parametrization for the `adaptation` package.

Nothing that passed before fails.

The real-database run used a throwaway PostgreSQL 16.14 cluster with scram-sha-256 host
authentication. It covered:

- **Migration:** `0009` upgrades and downgrades.
- **Storage:** the plaintext CHECK holds; JSONB and TIMESTAMPTZ round-trip through the stores; a
  missing table names `alembic upgrade head`.
- **Privilege findings:** a read-only role is healthy with no findings. A direct `INSERT` grant,
  an `UPDATE` grant inherited through a group role, schema `CREATE` and the superuser are each
  found.
- **Failure codes:** `auth_failed`, `unknown_database` and `unreachable`.
- **End to end:** the registry, over the Postgres stores, activates a read-only role and refuses
  a role holding `DELETE`.

**Mutation check.** I added an `api_key` field to `ConnectorDescriptor`; the secret-field fitness
test failed on it. I then reverted it.

Two existing tests had to change because a migration was added. Both pin the Alembic head on
purpose:

- `tests/unit/test_schema_readiness.py` now expects `0009`.
- `tests/load/test_knowledge_graph_postgres.py` now compares with `alembic heads` rather than a
  constant.

## Residual risks, stated

- **Security-definer functions that write are not detected.** Use a dedicated role with `SELECT`
  only.
- **PostgreSQL < 15 grants `CREATE` on `public` to everyone,** so such a role reports
  `schema_create` until `REVOKE CREATE ON SCHEMA public FROM PUBLIC` is run. This is intended.
- **Admin-supplied endpoints may point at any host the gateway can reach.** That is necessary,
  because target databases are often on private networks. Only AURA's own database is refused.
  SEC-01..03 must land before this surface is exposed in production (roadmap AB-2).
