"""Readiness probe for the Alembic schema.

Phase A1 booted `deploy/docker-compose.prod.yml` for the first time and found that a gateway
answers `/health/ready` with 200 against a database that has never been migrated. Three tables
existed -- `aura_documents`, `aura_chunks`, `aura_memories` -- because `PostgresPool._ensure_schema`
creates those itself on first connect. The eight platform tables the v2/v3 sync and auth routers
read did not, and `alembic_version` did not either.

That combination is the failure this module exists to prevent: nginx routes traffic to a gateway
that reports itself ready, and the first `/api/v3/sync` call is the thing that discovers the
schema is missing. `docs/guides/provisioning-runbook.md` section 6 claimed the gateway "will not
start cleanly against an unmigrated database", which was not true; this makes it true rather than
merely documented.

Registered from `aura_service.create_app` and only when Postgres is the configured backend, so
in-memory deployments (tests, the CLI) are unaffected.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from aura_core.observability.logging import get_logger

logger = get_logger(__name__)

# Resolved once. The alembic directory ships in the image (`COPY . .`) and the runbook runs
# `alembic upgrade head` through this same image, so the revision files are always alongside us.
_ALEMBIC_DIR = Path(__file__).resolve().parent.parent / "alembic"


@lru_cache(maxsize=4)
def expected_head(alembic_dir: Path | None = None) -> str | None:
    """The head revision of the migration scripts packaged in this build.

    Read from the scripts rather than pinned to a constant, so adding a migration cannot leave a
    stale expectation behind. `script_location` is set explicitly instead of reading alembic.ini,
    which keeps the answer independent of the process's working directory.

    Memoised because the readiness probe runs on the container healthcheck interval and the
    packaged revisions cannot change without a new image; without this every probe walks the
    versions directory.
    """
    directory = alembic_dir or _ALEMBIC_DIR
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        config = Config()
        config.set_main_option("script_location", str(directory))
        return ScriptDirectory.from_config(config).get_current_head()
    except Exception:
        logger.exception("could not read the packaged alembic head")
        return None


async def current_revision(pool: Any) -> str | None:
    """The revision stamped in the database, or None when the table is absent.

    `to_regclass` answers without raising on a missing table, which keeps a never-migrated
    database distinguishable from a connection failure -- the two need opposite handling.
    """
    connection_pool = await pool._get_pool()
    async with connection_pool.acquire() as conn:
        if await conn.fetchval("SELECT to_regclass('public.alembic_version')") is None:
            return None
        return await conn.fetchval("SELECT version_num FROM alembic_version LIMIT 1")


def build_schema_check(pool: Any) -> Any:
    """An async readiness check comparing the stamped revision against the packaged head."""

    async def check() -> dict[str, Any]:
        head = expected_head()
        if head is None:
            return {"healthy": True, "detail": "packaged alembic head unreadable; not gating"}

        try:
            stamped = await current_revision(pool)
        except Exception as exc:
            # Deliberately non-gating. An unreachable database is transient and self-healing,
            # and the chat path still serves from Qdrant, Redis and the model -- evicting the
            # gateway over a blip would turn a partial degradation into a full outage. A stale
            # schema is the opposite: operator-caused, persistent, and silent until it corrupts
            # a response, so only that case below returns unhealthy.
            logger.warning("schema readiness could not reach Postgres: %s", exc)
            return {"healthy": True, "expected": head, "detail": f"unverified: {exc}"}

        if stamped == head:
            return {"healthy": True, "revision": stamped}

        detail = (
            "database has never been migrated; run 'alembic upgrade head'"
            if stamped is None
            else "database schema is behind the code in this image"
        )
        return {"healthy": False, "expected": head, "revision": stamped, "detail": detail}

    return check
