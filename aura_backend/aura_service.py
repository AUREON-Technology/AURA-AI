"""Production ASGI entry point for the AURA AI platform.

Assembles the v3-only gateway: kernel routes from ``build_app``, plus the
leftover live platform adapters (auth, sync, insights, automation, learning).
Production does not load ``main.py`` (ADR-002: aura_core still does not import
``services/``; those adapters stay outside the core).

Run with::

    uvicorn aura_service:app --host 0.0.0.0 --port 8000 --workers 4 --proxy-headers
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from aura_core.api import Principal, build_app
from aura_core.config import load_settings
from aura_core.container import build_container
from aura_core.domain.errors import AuthenticationError, ConfigurationError
from aura_core.domain.identifiers import UserId
from aura_core.observability.logging import get_logger
from aura_core.security import SlidingWindowRateLimiter
from infrastructure.http_limits import PayloadSizeLimitMiddleware, RateLimitMiddleware
from infrastructure.secret_hydration import hydrate_secrets_from_files

logger = get_logger("service")


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


def _mount_platform_routers(app) -> None:
    from api.automation import router as automation_router
    from api.learning import router as learning_router
    from api.v2.auth import router as auth_router
    from api.v2.insights import router as insights_router
    from api.v2.sync import router as sync_router

    app.include_router(auth_router)
    app.include_router(sync_router)
    app.include_router(insights_router)
    app.include_router(automation_router)
    app.include_router(learning_router)


def _register_inference_budget_handler(app) -> None:
    from services.budget import InferenceBudgetExceededException

    @app.exception_handler(InferenceBudgetExceededException)
    async def inference_budget_exception_handler(request, exc: InferenceBudgetExceededException):
        return JSONResponse(
            status_code=429,
            content={
                "success": False,
                "error": exc.message,
                "retry_after": exc.retry_after,
            },
        )


def _apply_http_limits(app) -> None:
    app.add_middleware(PayloadSizeLimitMiddleware, max_bytes=5 * 1024 * 1024)
    app.add_middleware(RateLimitMiddleware, requests_per_minute=100)


def _apply_cors(app) -> None:
    from config import settings

    origins = [o.strip() for o in settings.allowed_origins.split(",") if o.strip()]
    allow_all = "*" in origins
    if allow_all:
        logger.warning(
            "ALLOWED_ORIGINS contains '*' — disabling allow_credentials to avoid "
            "reflecting arbitrary origins. Set an explicit origin list in production."
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=not allow_all,
        allow_methods=["*"],
        allow_headers=["*"],
    )


def create_app():
    """Build the v3-only ASGI application.

    Always uses ``build_app`` (health/live, health/ready, metrics). Does not
    import or mount ``main.py``.
    """
    hydrate_secrets_from_files()
    settings = load_settings()
    container = build_container(settings)
    authenticator = _build_authenticator(settings)

    rate_limiter = None
    if settings.security.rate_limit_enabled:
        rate_limiter = SlidingWindowRateLimiter.from_settings(
            container.cache_backend, settings.security, metrics=container.metrics
        )

    app = build_app(container, authenticator=authenticator, rate_limiter=rate_limiter)
    _mount_platform_routers(app)
    _register_inference_budget_handler(app)
    _apply_http_limits(app)
    _apply_cors(app)

    # Exposed so Celery tasks and the CLI can reach the same wired graph without rebuilding it.
    app.state.aura = container
    return app


app = create_app()
