"""Production ASGI entry point for the AURA AI platform.

Assembles the v3 gateway on top of the existing v1/v2 application (ADR-002, strangler
migration). Nothing here contains business logic: it builds the container, resolves an
authenticator, mounts the router, and wires lifecycle.

Run with::

    uvicorn aura_service:app --host 0.0.0.0 --port 8000 --workers 4 --proxy-headers

The legacy ``main:app`` still works unchanged for deployments mid-migration; this module is
what ``deploy/docker-compose.prod.yml`` runs.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import HTTPException, Response

from aura_core.api import Principal, build_app, build_router
from aura_core.config import load_settings
from aura_core.container import build_container
from aura_core.domain.errors import AuthenticationError, ConfigurationError
from aura_core.domain.identifiers import UserId
from aura_core.observability.logging import get_logger
from aura_core.security import SlidingWindowRateLimiter

logger = get_logger("service")


def _read_secret_file(env_var: str) -> str | None:
    """Read a value from a Docker secret file named by ``<VAR>_FILE``.

    Secrets arrive as files rather than environment values so they are not visible in
    ``docker inspect``, process listings, or a crash dump's environment block (SEC-08).
    """
    path = os.environ.get(f"{env_var}_FILE")
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as handle:
        return handle.read().strip()


def _hydrate_secrets_from_files() -> None:
    """Populate settings variables from their ``*_FILE`` counterparts before settings load."""
    password = _read_secret_file("AURA_DATABASE_URL")
    if password and not os.environ.get("AURA_DATABASE_URL"):
        host = os.environ.get("POSTGRES_HOST", "postgres")
        os.environ["AURA_DATABASE_URL"] = (
            f"postgresql+asyncpg://aura:{password}@{host}:5432/aura"
        )

    redis_password = _read_secret_file("AURA_REDIS_PASSWORD")
    if redis_password and not os.environ.get("AURA_REDIS_URL"):
        host = os.environ.get("REDIS_HOST", "redis")
        os.environ["AURA_REDIS_URL"] = f"redis://:{redis_password}@{host}:6379/0"

    for variable in ("AURA_QDRANT_API_KEY", "AURA_JWT_SECRET"):
        value = _read_secret_file(variable)
        if value and not os.environ.get(variable):
            os.environ[variable] = value

    # aura_core reads AURA_* names; legacy config.py / jwt_auth / sync read the
    # unprefixed pydantic-settings names (JWT_SECRET, DATABASE_URL, …). Without
    # this copy, each uvicorn worker mints a random JWT secret and Postgres
    # sync raises RuntimeError → 500 even though the Docker secrets are present.
    _mirror_legacy_secret_env()


def _mirror_legacy_secret_env() -> None:
    """Copy AURA_* secrets onto the unprefixed names legacy Settings expects."""
    for aura_name, legacy_name in (
        ("AURA_JWT_SECRET", "JWT_SECRET"),
        ("AURA_DATABASE_URL", "DATABASE_URL"),
        ("AURA_QDRANT_API_KEY", "QDRANT_API_KEY"),
        ("AURA_REDIS_URL", "REDIS_URL"),
    ):
        value = os.environ.get(aura_name)
        if value and not os.environ.get(legacy_name):
            os.environ[legacy_name] = value


def _build_authenticator(settings: Any):
    """Resolve the callable that turns a request into a :class:`Principal`.

    During the migration this delegates to the existing v1/v2 verification so v3 accepts the
    tokens the shipped Flutter client already holds. It is injected rather than imported by the
    gateway, so replacing the identity provider later touches only this function.
    """

    async def authenticate(request) -> Principal:
        if settings.security.allow_mock_auth:
            # Reachable only in local/development: production settings validation refuses this
            # combination, and the metric `aura_insecure_mode{bypass="mock_auth"}` reports it.
            logger.warning("mock authentication is enabled; this must never run in production")
            return Principal(user_id=UserId("mock-user"), email="mock@local", role="Admin")

        header = request.headers.get("authorization", "")
        if not header.lower().startswith("bearer "):
            raise AuthenticationError("a bearer token is required")
        token = header[7:].strip()
        if not token:
            raise AuthenticationError("a bearer token is required")

        try:
            # Imported lazily and by name so aura_core never depends on the legacy package
            # (ADR-002, enforced by tests/architecture/test_dependency_rules.py).
            from services.jwt_auth import verify_access_token  # type: ignore

            from fastapi.security import HTTPAuthorizationCredentials

            claims = await verify_access_token(
                request,
                HTTPAuthorizationCredentials(scheme="Bearer", credentials=token),
            )
        except ImportError as exc:  # pragma: no cover - only when v2 is absent
            raise ConfigurationError(
                "no authenticator is available: the v2 JWT module could not be imported and "
                "no replacement is configured"
            ) from exc
        except HTTPException:
            # jwt_auth raises FastAPI HTTPException (401 invalid/expired). Do not
            # collapse that into a generic "invalid or expired credentials" —
            # the client uses the detail to decide whether to re-exchange.
            raise
        except AuthenticationError:
            raise
        except Exception as exc:
            raise AuthenticationError("invalid or expired credentials") from exc

        subject = claims.get("uid")
        if not subject:
            raise AuthenticationError("token carries no subject claim")

        role = claims.get("role") or "User"
        return Principal(
            user_id=UserId(str(subject)),
            email=claims.get("email") or "",
            role=role,
            # Capability grants are role-derived for now. When per-user grants land they are
            # read here; the tool policy already fails closed on a missing capability.
            capabilities=("admin.act",) if role in settings.security.admin_roles else (),
        )

    return authenticate


def create_app(*, standalone: bool = False):
    """Build the ASGI application.

    ``standalone=True`` returns a v3-only app (used by tests and by a deployment that runs the
    AI gateway as its own service). The default mounts v3 onto the existing application so v1
    and v2 keep serving the shipped client.
    """
    _hydrate_secrets_from_files()
    settings = load_settings()
    container = build_container(settings)
    authenticator = _build_authenticator(settings)

    rate_limiter = None
    if settings.security.rate_limit_enabled:
        rate_limiter = SlidingWindowRateLimiter.from_settings(
            container.cache_backend, settings.security, metrics=container.metrics
        )

    if standalone:
        return build_app(container, authenticator=authenticator, rate_limiter=rate_limiter)

    try:
        from main import app as legacy_app  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on legacy import health
        logger.error(
            "the legacy application could not be imported; serving v3 only",
            extra={"error": type(exc).__name__},
        )
        return build_app(container, authenticator=authenticator, rate_limiter=rate_limiter)

    legacy_app.include_router(
        build_router(container, authenticator=authenticator, rate_limiter=rate_limiter)
    )

    @legacy_app.get("/health/live", tags=["health"])
    async def liveness() -> dict[str, str]:
        """Process liveness only.

        Deliberately checks nothing else: a liveness probe that fails during a dependency
        outage makes the orchestrator restart a healthy process, turning degradation into an
        outage.
        """
        return {"status": "alive"}

    @legacy_app.get("/health/ready", tags=["health"])
    async def readiness(response: Response) -> dict[str, Any]:
        """Dependency readiness. This is what a load balancer should gate traffic on."""
        health = await container.health()
        response.status_code = 200 if health["healthy"] else 503
        return health

    @legacy_app.get(settings.observability.metrics_path, tags=["health"])
    async def metrics() -> Response:
        return Response(
            content=container.metrics.render(), media_type=container.metrics.content_type
        )

    @legacy_app.on_event("startup")
    async def _startup() -> None:
        await container.startup()
        logger.info(
            "AURA v3 gateway mounted",
            extra={
                "environment": str(settings.environment),
                "providers": [p.name for p in container.providers],
            },
        )

    @legacy_app.on_event("shutdown")
    async def _shutdown() -> None:
        await container.shutdown()

    # Exposed so Celery tasks and the CLI can reach the same wired graph without rebuilding it.
    legacy_app.state.aura = container
    return legacy_app


app = create_app()
