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
from aura_core.domain.errors import AuthenticationError, AuthorizationError, ConfigurationError
from aura_core.domain.identifiers import UserId, is_reserved_principal
from aura_core.domain.service_keys import looks_like_service_token
from aura_core.domain.tenancy import WorkspaceContext
from aura_core.observability.logging import get_logger
from aura_core.security import SlidingWindowRateLimiter
from aura_core.security.role_capabilities import capabilities_for_role
from infrastructure.http_limits import PayloadSizeLimitMiddleware
from infrastructure.secret_hydration import hydrate_secrets_from_files

logger = get_logger("service")


#: Names the workspace a request acts in (ADR-034). Absent: the caller's personal scope.
WORKSPACE_HEADER = "x-aura-workspace"


def _build_authenticator(settings: Any, *, service_keys: Any = None, tenancy: Any = None):
    """Resolve the callable that turns a request into a :class:`Principal`.

    During the migration this delegates to the existing v1/v2 verification so v3 accepts the
    tokens the shipped Flutter client already holds. It is injected rather than imported by the
    gateway, so replacing the identity provider later touches only this function.
    """

    async def authenticate(request) -> Principal:
        caller = await _identify(request)
        requested = (request.headers.get(WORKSPACE_HEADER) or "").strip()
        if not requested:
            return caller
        # Checked against the store on every request, with no cache, so a removed member's next
        # call is refused. "Unknown" and "not a member" are one indistinguishable 403.
        if caller.is_service or tenancy is None:
            raise AuthorizationError("not a member of this workspace")
        workspace, membership = await tenancy.resolve(str(caller.user_id), requested)
        caller.workspace = WorkspaceContext(
            workspace_id=workspace.id, kind=workspace.kind, role=membership.role
        )
        return caller

    async def _identify(request) -> Principal:
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

        # AR-4: a service key (another system, not a person) is recognised by its prefix and
        # verified against AURA's own store -- never handed to the JWT path, and vice versa.
        if looks_like_service_token(token):
            if service_keys is None:
                raise AuthenticationError("service keys are not enabled on this install")
            return Principal.for_service(await service_keys.authenticate(token))

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
        if is_reserved_principal(str(subject)):
            # A person cannot act as the global corpus, a service key or a project (AP10).
            raise AuthenticationError("token subject is in a reserved namespace")

        from services.rbac import get_user_role  # type: ignore

        role = get_user_role(claims)
        # One rule shared with the pairing approval route (audit INT-003), so a daemon can only
        # be paired to an account the host-agent socket will accept.
        return Principal(
            user_id=UserId(str(subject)),
            email=claims.get("email") or "",
            role=role,
            capabilities=capabilities_for_role(settings.security, role),
        )

    return authenticate


def _mount_platform_routers(app) -> None:
    from api.account import router as account_router
    from api.admin_platform import router as admin_platform_router
    from api.automation import router as automation_router
    from api.learning import router as learning_router
    from api.v2.auth import router as auth_router
    from api.v2.github_integration import router as github_integration_router
    from api.v2.insights import router as insights_router
    from api.v2.sync import router as sync_router

    app.include_router(auth_router)
    app.include_router(sync_router)
    app.include_router(insights_router)
    app.include_router(automation_router)
    app.include_router(learning_router)
    app.include_router(admin_platform_router)
    app.include_router(account_router)
    app.include_router(github_integration_router)


def _warn_if_no_console_operator(settings) -> None:
    """Say at boot that nobody can reach the console, rather than at the first login attempt.

    A fresh deployment grants no console role to anyone. Discovered the hard way: an operator's
    own first sign-in was refused with a message about their resolved role, which sent them
    looking at authentication rather than at three unset environment variables. A startup line
    costs nothing and puts the answer where someone debugging a deploy is already looking.

    A warning, not a validation failure: the API itself is perfectly usable without an operator
    console, so refusing to boot over it would turn a missing convenience into an outage -- the
    mistake made with release identity earlier in this repository's history.
    """
    from services.rbac import CONSOLE_ROLE_ENV_VARS, console_operators_configured

    if console_operators_configured():
        return
    logger.warning(
        "no console operator is configured: every operator-console login will be refused with "
        "403 until one of %s names an email address (the AURA_-prefixed spellings are accepted "
        "too). The v3 API is unaffected.",
        ", ".join(CONSOLE_ROLE_ENV_VARS),
        extra={"environment": str(settings.environment)},
    )


#: Below this, HS256's key is shorter than its own output (RFC 7518 §3.2).
_MIN_JWT_SECRET_BYTES = 32


def _require_jwt_secret(settings) -> None:
    """Refuse to serve staging/production without a configured JWT signing secret.

    Without one, ``services/jwt_auth.py`` falls back to a random secret generated per process.
    With ``--workers 4`` that is four different keys: a token issued by one worker is rejected
    by the other three, so sign-in "works" and then fails at random, and every restart signs
    everyone out. Unlike an unidentified release (see ``Settings.validate``), that is broken
    authentication, not a diagnosability gap, so it stops the boot rather than warning.

    A secret that is merely short is warned about, not refused: the deployed value cannot be
    inspected from here, and taking a working deployment down over key length would trade a
    weakness for an outage. ``bootstrap-secrets.sh`` generates 64 characters.

    Called after ``hydrate_secrets_from_files``: the secret arrives as a Docker secret file.
    """
    if not settings.environment.is_production_like:
        return
    from services.jwt_auth import configured_secret

    secret = configured_secret()
    if not secret:
        raise ConfigurationError(
            "no JWT signing secret is configured (AURA_JWT_SECRET, JWT_SECRET, or "
            "AURA_JWT_SECRET_FILE): staging/production refuses to start with the per-process "
            "random fallback, which breaks sign-in across workers and restarts"
        )
    if len(secret.encode()) < _MIN_JWT_SECRET_BYTES:
        logger.warning(
            "the JWT signing secret is %d bytes; use at least %d (bootstrap-secrets.sh "
            "generates 64 characters)",
            len(secret.encode()),
            _MIN_JWT_SECRET_BYTES,
        )


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


#: Multipart framing (boundaries, part headers, the filename) on top of the document itself.
_UPLOAD_FRAMING_BYTES = 1024 * 1024


#: The text part of a chat turn (message, history hints, approval ids) beside its images.
_CHAT_TEXT_BYTES = 1024 * 1024


def chat_body_limit(vision) -> int | None:
    """The body ceiling for ``/api/v3/chat`` and ``/api/v3/chat/stream`` when images are on.

    Images travel base64-encoded inside the JSON body (ADR-031), a third larger than the bytes
    ``/capabilities`` advertises (``max_image_bytes``, ``max_request_image_bytes``). Under the
    flat 5MB cap one ordinary phone photo above ~3.7MB was refused with a 413 the vision engine
    would have accepted, and the app showed a Retry that could never succeed. ``None`` while
    vision is off: images are refused anyway, so chat keeps the default cap.
    """
    if not vision.enabled:
        return None
    encoded = (vision.max_request_image_bytes * 4 + 2) // 3
    return encoded + _CHAT_TEXT_BYTES


def _apply_http_limits(app, settings) -> None:
    # Document upload has its own ceiling, ``knowledge.max_document_bytes`` (8MB by default); the
    # flat 5MB cap used to refuse every document between the two with a 413 the knowledge
    # engine would have accepted. nginx's ``client_max_body_size 20m`` stays above both, and
    # above the chat ceiling with vision's defaults (12MB of images -> ~17MB of body).
    upload_limit = settings.knowledge.max_document_bytes + _UPLOAD_FRAMING_BYTES
    route_limits = {"/api/v3/knowledge/documents/upload": upload_limit}
    chat_limit = chat_body_limit(settings.vision)
    if chat_limit is not None:
        # A prefix match: covers /api/v3/chat and /api/v3/chat/stream.
        route_limits["/api/v3/chat"] = chat_limit
    app.add_middleware(
        PayloadSizeLimitMiddleware,
        max_bytes=5 * 1024 * 1024,
        route_limits=route_limits,
    )
    # No second, legacy per-IP limiter here (audit FSA-005). `infrastructure.http_limits.
    # RateLimitMiddleware` keyed on `request.client.host`, which behind Railway's edge (and
    # behind nginx on the VPS, before uvicorn trusted forwarded headers) is the proxy address,
    # so every user shared one 100 req/min bucket. The one limiter is the aura_core
    # SlidingWindowRateLimiter wired into the v3 router below, keyed on `extract_client_ip`
    # with `security.trusted_proxy_count` (and on the caller once authenticated).


def _apply_cors(app) -> None:
    from config import settings

    origins = [o.strip() for o in settings.allowed_origins.split(",") if o.strip()]
    allow_all = "*" in origins
    if not origins:
        # The production-like default (config.default_allowed_origins). Native clients are
        # unaffected; a Flutter web bundle needs its origin named in ALLOWED_ORIGINS.
        logger.warning("ALLOWED_ORIGINS is empty: no browser origin may call this API until one is named.")
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


def _register_schema_readiness(container) -> None:
    """Gate readiness on the Alembic schema when the platform tables are actually in play.

    Only for Postgres deployments: with the in-memory backends there are no migrations to be
    behind, and the routers that need them are not serving real data. See
    ``services/schema_readiness`` for why an unmigrated database otherwise reports ready.

    Reads the container's own settings rather than taking them as an argument, so the backend
    this consults and the pool it probes can never come from two different configurations.
    """
    infra = container.settings.infrastructure
    if "postgres" not in (infra.document_backend, infra.memory_backend):
        return

    from services.schema_readiness import build_schema_check

    container.register_readiness_check("schema", build_schema_check(container.postgres_pool()))


def create_app():
    """Build the v3-only ASGI application.

    Always uses ``build_app`` (health/live, health/ready, metrics). Does not
    import or mount ``main.py``.
    """
    hydrate_secrets_from_files()
    settings = load_settings()
    _require_jwt_secret(settings)
    container = build_container(settings)
    authenticator = _build_authenticator(
        settings, service_keys=container.service_keys, tenancy=container.tenancy
    )

    rate_limiter = None
    if settings.security.rate_limit_enabled:
        rate_limiter = SlidingWindowRateLimiter.from_settings(
            container.cache_backend, settings.security, metrics=container.metrics
        )

    _register_schema_readiness(container)

    def _status_extras() -> dict:
        try:
            from services.learning_pipeline import learning_pipeline

            return {"learning": learning_pipeline.evaluate_metrics()}
        except Exception:
            return {"learning": {}}

    container.status_extras = _status_extras

    app = build_app(
        container, authenticator=authenticator, rate_limiter=rate_limiter, http_metrics=False
    )
    _mount_platform_routers(app)
    _warn_if_no_console_operator(settings)
    _register_inference_budget_handler(app)
    _apply_http_limits(app, settings)
    _apply_cors(app)
    # Last, so outermost: a request the size limit, rate limiter or CORS refuses is counted too.
    from aura_core.api.http_metrics import HttpMetricsMiddleware

    app.add_middleware(HttpMetricsMiddleware, metrics=container.metrics)

    # Exposed so Celery tasks and the CLI can reach the same wired graph without rebuilding it.
    app.state.aura = container
    # The legacy SQLAlchemy engine (api/v2 sync, insights) lives outside the container and was
    # never disposed at shutdown (audit FSA-073). Registered after build_app's own handler, so
    # it runs after the container's pools close, once uvicorn's graceful drain has finished.
    from infrastructure.db.base import dispose_engine

    app.on_event("shutdown")(dispose_engine)
    return app


app = create_app()
