"""PostgreSQL connector: reachability and privilege inspection (AP2).

Discovery (AP3) and templated reads (AP6) build on this module; for now it answers the two
questions activation asks: *can AURA reach the database with this credential*, and *could that
credential change anything*.

Read-only is enforced twice, independently:

* the session is opened with ``default_transaction_read_only=on`` and the inspection runs inside
  an explicit read-only transaction, so even a mistake here cannot write; and
* the role is inspected, and any capability to write is reported as a finding. Activation refuses
  a connector with any finding (``ConnectorHealth.write_capable``), so a v1 connector runs under a
  role that *could not* write even if every other layer failed.

The findings are deliberately strict. On PostgreSQL < 15 every role may ``CREATE`` in the
``public`` schema by default; that is reported (``schema_create``) and the fix is the documented
one: ``REVOKE CREATE ON SCHEMA public FROM PUBLIC``. Security-definer functions that write are not
detected; a dedicated read-only role (``GRANT pg_read_all_data`` or per-table ``SELECT``) avoids
them.

The connector also refuses AURA's own database (``own_database``): a Super Admin pointing the
adaptation layer at the platform's memories and audit log is never what was meant.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Sequence
from urllib.parse import urlsplit

from ...domain.connectors import ConnectorDescriptor, ConnectorHealth, Credential, HealthStatus
from ...observability.logging import get_logger

__all__ = ["PostgresConnectorFactory", "PostgresConnector", "PRIVILEGE_INSPECTION_SQL"]

logger = get_logger("adapters.connectors.postgres")

_SCHEMES = {"postgres", "postgresql"}
_SSL_MODES = {"disable", "prefer", "require", "verify-ca", "verify-full"}

#: One row: role attributes plus whether any write path exists. ``has_*_privilege`` follows role
#: membership (INHERIT), so a grant through a group role is found too.
PRIVILEGE_INSPECTION_SQL = """
SELECT
    r.rolsuper       AS superuser,
    r.rolcreaterole  AS createrole,
    r.rolcreatedb    AS createdb,
    r.rolreplication AS replication,
    r.rolbypassrls   AS bypassrls,
    EXISTS (
        SELECT 1
          FROM pg_catalog.pg_class c
          JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
         WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
           AND n.nspname NOT IN ('pg_catalog', 'information_schema')
           AND n.nspname NOT LIKE 'pg\\_toast%'
           AND n.nspname NOT LIKE 'pg\\_temp%'
           AND (   has_table_privilege(c.oid, 'INSERT')
                OR has_table_privilege(c.oid, 'UPDATE')
                OR has_table_privilege(c.oid, 'DELETE')
                OR has_table_privilege(c.oid, 'TRUNCATE'))
    ) AS table_write,
    EXISTS (
        SELECT 1
          FROM pg_catalog.pg_namespace n
         WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
           AND n.nspname NOT LIKE 'pg\\_toast%'
           AND n.nspname NOT LIKE 'pg\\_temp%'
           AND has_schema_privilege(n.oid, 'CREATE')
    ) AS schema_create,
    has_database_privilege(current_database(), 'CREATE') AS database_create
  FROM pg_catalog.pg_roles r
 WHERE r.rolname = current_user
"""

_FINDINGS = (
    "superuser", "createrole", "createdb", "replication", "bypassrls",
    "table_write", "schema_create", "database_create",
)


def _target(url: str) -> tuple[str, int, str] | None:
    """(host, port, database) of a postgres URL, lower-cased; ``None`` if it is not one."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in _SCHEMES and not parts.scheme.startswith("postgresql+"):
        return None
    try:
        port = parts.port or 5432
    except ValueError:
        return None
    return ((parts.hostname or "localhost").lower(), port, parts.path.lstrip("/").lower())


class PostgresConnectorFactory:
    def __init__(self, *, forbidden_targets: Sequence[str] = ()) -> None:
        self._forbidden = {t for t in (_target(url) for url in forbidden_targets if url) if t}

    async def open(self, descriptor: ConnectorDescriptor, credential: Credential | None) -> "PostgresConnector":
        return PostgresConnector(descriptor, credential, forbidden=frozenset(self._forbidden))


class PostgresConnector:
    def __init__(
        self,
        descriptor: ConnectorDescriptor,
        credential: Credential | None,
        *,
        forbidden: frozenset[tuple[str, int, str]] = frozenset(),
    ) -> None:
        self.descriptor = descriptor
        self._credential = credential
        self._forbidden = forbidden

    async def health(self) -> ConnectorHealth:
        started = time.monotonic()

        def result(status: HealthStatus, reason: str, **extra: Any) -> ConnectorHealth:
            return ConnectorHealth(
                status, reason, latency_ms=round((time.monotonic() - started) * 1000.0, 1), **extra
            )

        target = _target(self.descriptor.endpoint)
        if target is None or urlsplit(self.descriptor.endpoint).scheme not in _SCHEMES:
            return result(HealthStatus.UNHEALTHY, "invalid_endpoint")
        if target in self._forbidden:
            return result(HealthStatus.UNHEALTHY, "own_database")
        sslmode = str(self.descriptor.options.get("sslmode", "prefer"))
        user = self.descriptor.options.get("user")
        if sslmode not in _SSL_MODES or not isinstance(user, str) or not user:
            return result(HealthStatus.UNHEALTHY, "invalid_options")

        import asyncpg  # lazy: vendor driver stays out of import time (ADR-001)

        timeout = self.descriptor.timeout_s
        try:
            conn = await asyncpg.connect(
                dsn=self.descriptor.endpoint,
                user=user,
                password=self._credential.reveal() if self._credential else None,
                ssl=sslmode,
                timeout=timeout,
                command_timeout=timeout,
                server_settings={
                    "application_name": "aura-connector",
                    "default_transaction_read_only": "on",
                },
            )
        except (asyncpg.InvalidPasswordError, asyncpg.InvalidAuthorizationSpecificationError):
            return result(HealthStatus.UNHEALTHY, "auth_failed")
        except asyncpg.InvalidCatalogNameError:
            return result(HealthStatus.UNHEALTHY, "unknown_database")
        except (OSError, asyncio.TimeoutError, asyncpg.CannotConnectNowError):
            return result(HealthStatus.UNHEALTHY, "unreachable")
        except asyncpg.PostgresError as exc:
            logger.info("postgres connector refused", extra={"connector": self.descriptor.id, "sqlstate": exc.sqlstate})
            return result(HealthStatus.UNHEALTHY, "connect_failed")
        try:
            async with conn.transaction(readonly=True):
                row = await conn.fetchrow(PRIVILEGE_INSPECTION_SQL)
        except asyncpg.PostgresError as exc:
            logger.info("postgres privilege inspection failed", extra={"connector": self.descriptor.id, "sqlstate": exc.sqlstate})
            return result(HealthStatus.UNHEALTHY, "inspection_failed")
        finally:
            await conn.close(timeout=timeout)
        if row is None:  # current_user has no pg_roles row: cannot vouch for it
            return result(HealthStatus.UNHEALTHY, "inspection_failed")
        findings = tuple(name for name in _FINDINGS if row[name])
        return result(HealthStatus.HEALTHY, "ok", write_capable=bool(findings), findings=findings)

    async def aclose(self) -> None:
        """Nothing held between calls yet; each health check opens and closes its own session."""
        return None
