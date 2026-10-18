"""PostgreSQL connector: reachability, privilege inspection (AP2) and discovery (AP3).

Templated reads (AP6) build on this module. ``health()`` answers the two questions activation
asks -- *can AURA reach the database with this credential*, and *could that credential change
anything* -- and ``discover()`` captures the catalog (``postgres_catalog.py``). The optional
``schemas`` option (comma-separated) narrows discovery to named schemas.

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
import datetime as dt
import decimal
import uuid
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from ...domain.connectors import CallerIdentity, ConnectorDescriptor, ConnectorHealth, Credential, HealthStatus
from ...domain.discovery import DiscoveryBudget, DiscoveryReport
from ...domain.errors import DependencyUnavailableError, ToolExecutionError
from ...domain.templates import ReadTemplate, TemplateKind, render_sql
from ...observability.logging import get_logger
from .postgres_catalog import artifacts_from_catalog, capture_catalog

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

    def _preflight(self) -> str | None:
        """A reason code if the descriptor cannot be used at all, before any driver call."""
        target = _target(self.descriptor.endpoint)
        if target is None or urlsplit(self.descriptor.endpoint).scheme not in _SCHEMES:
            return "invalid_endpoint"
        if target in self._forbidden:
            return "own_database"
        sslmode = str(self.descriptor.options.get("sslmode", "prefer"))
        user = self.descriptor.options.get("user")
        if sslmode not in _SSL_MODES or not isinstance(user, str) or not user:
            return "invalid_options"
        return None

    async def _connect(self) -> tuple[Any, str | None]:
        """(connection, None) or (None, reason code). Never raises for the target's failures."""
        reason = self._preflight()
        if reason is not None:
            return None, reason
        import asyncpg  # lazy: vendor driver stays out of import time (ADR-001)

        timeout = self.descriptor.timeout_s
        try:
            conn = await asyncpg.connect(
                dsn=self.descriptor.endpoint,
                user=self.descriptor.options["user"],
                password=self._credential.reveal() if self._credential else None,
                ssl=str(self.descriptor.options.get("sslmode", "prefer")),
                timeout=timeout,
                command_timeout=timeout,
                server_settings={
                    "application_name": "aura-connector",
                    "default_transaction_read_only": "on",
                },
            )
        except (asyncpg.InvalidPasswordError, asyncpg.InvalidAuthorizationSpecificationError):
            return None, "auth_failed"
        except asyncpg.InvalidCatalogNameError:
            return None, "unknown_database"
        except (OSError, asyncio.TimeoutError, asyncpg.CannotConnectNowError):
            return None, "unreachable"
        except asyncpg.PostgresError as exc:
            logger.info("postgres connector refused", extra={"connector": self.descriptor.id, "sqlstate": exc.sqlstate})
            return None, "connect_failed"
        return conn, None

    async def health(self) -> ConnectorHealth:
        started = time.monotonic()

        def result(status: HealthStatus, reason: str, **extra: Any) -> ConnectorHealth:
            return ConnectorHealth(
                status, reason, latency_ms=round((time.monotonic() - started) * 1000.0, 1), **extra
            )

        conn, reason = await self._connect()
        if conn is None:
            return result(HealthStatus.UNHEALTHY, reason or "connect_failed")
        import asyncpg

        try:
            async with conn.transaction(readonly=True):
                row = await conn.fetchrow(PRIVILEGE_INSPECTION_SQL)
        except asyncpg.PostgresError as exc:
            logger.info("postgres privilege inspection failed", extra={"connector": self.descriptor.id, "sqlstate": exc.sqlstate})
            return result(HealthStatus.UNHEALTHY, "inspection_failed")
        finally:
            await conn.close(timeout=self.descriptor.timeout_s)
        if row is None:  # current_user has no pg_roles row: cannot vouch for it
            return result(HealthStatus.UNHEALTHY, "inspection_failed")
        findings = tuple(name for name in _FINDINGS if row[name])
        return result(HealthStatus.HEALTHY, "ok", write_capable=bool(findings), findings=findings)

    async def discover(self, budget: DiscoveryBudget) -> DiscoveryReport:
        """Capture the catalog and a bounded profile, then map it (``postgres_catalog``)."""
        conn, reason = await self._connect()
        if conn is None:
            raise DependencyUnavailableError(
                "the connected database could not be reached for discovery",
                dependency="connector", details={"connector": self.descriptor.id, "reason": reason},
            )
        import asyncpg

        schemas = self.descriptor.options.get("schemas")
        try:
            snapshot = await capture_catalog(
                conn, budget=budget, timeout_s=self.descriptor.timeout_s,
                schemas=[s.strip() for s in schemas.split(",") if s.strip()] if isinstance(schemas, str) else None,
            )
        except asyncpg.PostgresError as exc:
            logger.info("postgres discovery failed", extra={"connector": self.descriptor.id, "sqlstate": exc.sqlstate})
            raise DependencyUnavailableError(
                "catalog discovery failed", dependency="connector",
                details={"connector": self.descriptor.id, "sqlstate": exc.sqlstate},
            ) from None
        finally:
            await conn.close(timeout=self.descriptor.timeout_s)
        return artifacts_from_catalog(self.descriptor.id, snapshot, budget)

    async def read(self, template: ReadTemplate, arguments: Mapping[str, Any],
                   identity: CallerIdentity | None = None) -> list[dict[str, Any]]:
        """Execute one compiled template (AP7): read-only transaction, statement timeout, the
        domain renderer only, rows converted to JSON types and capped at ``row_cap``."""
        if template.kind is not TemplateKind.SQL or template.connector != self.descriptor.id:
            raise ToolExecutionError("this template does not belong to this connector", tool=template.id)
        rendered = render_sql(template, arguments)
        conn, reason = await self._connect()
        if conn is None:
            raise ToolExecutionError(
                "the connected database is not reachable right now", tool=template.id, details={"reason": reason},
            )
        import asyncpg

        try:
            async with conn.transaction(readonly=True):
                # Literal SQL with a bound value (test_no_freeform_sql): transaction-local timeout.
                await conn.execute("SELECT set_config('statement_timeout', $1, true)",
                                   str(int(self.descriptor.timeout_s * 1000)))
                rows = await conn.fetch(rendered.text, *rendered.values)
        except asyncpg.QueryCanceledError:
            raise ToolExecutionError("the query took too long and was cancelled", tool=template.id) from None
        except (asyncpg.DataError, asyncpg.InvalidTextRepresentationError):
            raise ToolExecutionError("an argument does not fit the column it filters", tool=template.id) from None
        except asyncpg.PostgresError as exc:
            logger.info("postgres read failed", extra={"connector": self.descriptor.id, "sqlstate": exc.sqlstate})
            raise ToolExecutionError("the connected database refused the read", tool=template.id, details={"sqlstate": exc.sqlstate}) from None
        finally:
            await conn.close(timeout=self.descriptor.timeout_s)
        return [{key: json_value(value) for key, value in row.items()} for row in rows[: self.descriptor.row_cap]]

    async def aclose(self) -> None:
        """Nothing held between calls yet; each call opens and closes its own session."""
        return None


def json_value(value: Any) -> Any:
    """A driver value as JSON data: numbers stay numbers, times become ISO text."""
    if isinstance(value, decimal.Decimal):
        return float(value) if value.is_finite() else None
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return value.total_seconds()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return None  # binary is never handed to a model
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    return value
