"""The v3 AI gateway (FastAPI adapter).

A thin adapter, deliberately. It authenticates, applies cost-weighted rate limits, translates
HTTP into :class:`~aura_core.kernel.kernel.TurnRequest`, and translates domain errors into
status codes. Every decision of substance happens in the kernel, which is why the same kernel
runs unchanged from a Celery worker and a CLI.

Mounted alongside v1/v2 (ADR-002): no route here replaces one there, and nothing in
``aura_core`` imports from ``aura_backend.services``. The Flutter client migrates one feature at
a time behind a flag.
"""

# NOTE: deliberately no ``from __future__ import annotations`` here. FastAPI resolves handler
# annotations with ``typing.get_type_hints``, which cannot see the request models defined inside
# ``build_router``'s scope; with postponed evaluation every one of them would be misread as a
# query parameter. Runtime annotations keep the models resolvable.
import asyncio
import base64
import binascii
import json
from datetime import datetime, timedelta
from typing import Any, AsyncIterator, Mapping

from ..domain.errors import (
    AuraError,
    AuthenticationError,
    AuthorizationError,
    DependencyUnavailableError,
    OverloadedError,
    RateLimitedError,
    ValidationError,
)
from ..domain.caller_context import MAX_CALLER_CONTEXT, CallerContext
from ..domain.connectors import CONNECTOR_ID_PATTERN, Credential
from ..domain.identifiers import CorrelationId, DocumentId, SessionId, UserId
from ..domain.llm import RoutingPolicy
from ..domain.memory import MemoryKind, MemoryQuery
from ..domain.output_schema import check_output_schema
from ..domain.reasoning import ReasoningStrategy
from ..domain.tenancy import WorkspaceContext, WorkspaceKind, workspace_scope
from ..domain.tenancy import can as can_in_workspace
from ..domain.untrusted import MAX_UNTRUSTED_CHARS
from ..kernel.kernel import TurnRequest
from ..observability.logging import get_logger
from ..ports.runtime import AuditEvent
from ..security.rate_limit import extract_client_ip
from .admin import register_admin_ops

__all__ = ["build_router", "build_app", "Principal"]

logger = get_logger("api.v3")

#: The role a service-key principal carries (AR-4). Never an admin role, so every admin check
#: refuses it before the route allowlist even has to.
SERVICE_ROLE = "Service"
#: Bound on a caller's response_schema (AR-6): it is rendered into the prompt, so it costs tokens.
_MAX_RESPONSE_SCHEMA_CHARS = 8000


#: A caller's own token for one ``DELEGATED`` connector (AP10, ADR-021): ``<connector id>=<token>``,
#: repeated (or comma-separated) for several. A header rather than the body, so it is never part
#: of what idempotency hashes or what a request log might keep, and it lives for this turn only.
CONNECTOR_TOKEN_HEADER = "x-aura-connector-token"
_MAX_CONNECTOR_TOKENS = 8
_MAX_CONNECTOR_TOKEN_CHARS = 8192


def _connector_tokens(request: Any) -> dict[str, Credential]:
    """Parse ``X-Aura-Connector-Token``; every error names the problem, never the token."""
    tokens: dict[str, Credential] = {}
    for header in request.headers.getlist(CONNECTOR_TOKEN_HEADER):
        for entry in header.split(","):
            entry = entry.strip()
            if not entry:
                continue
            connector_id, sep, token = entry.partition("=")
            connector_id, token = connector_id.strip(), token.strip()
            if not sep or not CONNECTOR_ID_PATTERN.match(connector_id):
                raise ValidationError(f"{CONNECTOR_TOKEN_HEADER} entries are <connector id>=<token>")
            if not token or len(token) > _MAX_CONNECTOR_TOKEN_CHARS or any(c.isspace() for c in token):
                raise ValidationError("a connector token is empty, too long or contains whitespace",
                                      details={"connector": connector_id})
            if connector_id in tokens:
                raise ValidationError("one token per connector", details={"connector": connector_id})
            tokens[connector_id] = Credential(token)
            if len(tokens) > _MAX_CONNECTOR_TOKENS:
                raise ValidationError("too many connector tokens", details={"limit": _MAX_CONNECTOR_TOKENS})
    return tokens


#: How often a quiet stream sends an SSE comment. nginx closes a proxied response after 300 s
#: with no bytes (``proxy_read_timeout``), and planning plus time-to-first-token on the CPU stack
#: can be silent for longer than that (audit F-22).
SSE_KEEPALIVE_S = 15.0


async def with_keepalive(frames: AsyncIterator[bytes], *, interval_s: float) -> AsyncIterator[bytes]:
    """Re-yield ``frames``, inserting an SSE comment line whenever ``interval_s`` passes without
    one. Comment lines (``: ...``) are part of the SSE format and ignored by every compliant
    parser, the client's ``SseFrameParser`` included, so they keep the connection alive without
    becoming events. The producer runs as its own task so a slow frame can be waited on with a
    timeout without cancelling it; it is cancelled if the client goes away."""
    queue: asyncio.Queue = asyncio.Queue()
    done = object()

    async def pump() -> None:
        try:
            async for frame in frames:
                await queue.put(frame)
        finally:
            await queue.put(done)

    producer = asyncio.create_task(pump())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=interval_s)
            except asyncio.TimeoutError:
                yield b": keepalive\n\n"
                continue
            if item is done:
                break
            yield item
        await producer
    finally:
        if not producer.done():
            producer.cancel()


class Principal:
    """The authenticated caller.

    Deliberately minimal: an identity, a role, and granted capabilities. Nothing about the
    transport, no raw token, no provider-specific claims -- so a change of identity provider
    touches only the authenticator.
    """

    __slots__ = ("user_id", "email", "role", "capabilities", "service_key", "workspace")

    def __init__(
        self,
        *,
        user_id: UserId,
        email: str = "",
        role: str = "User",
        capabilities: tuple[str, ...] = (),
        service_key: Any = None,
        workspace: WorkspaceContext | None = None,
    ) -> None:
        self.user_id = user_id
        self.email = email
        self.role = role
        self.capabilities = capabilities
        # AR-4: set only for a non-human caller authenticated by a service key. Its routes,
        # rate and the absence of memory, session and tools are enforced by this module.
        self.service_key = service_key
        # ADR-034: the workspace this request acts in, membership already checked against the
        # store by the authenticator. ``None`` is the caller's personal scope. It carries a
        # *workspace* role only -- never a platform role or a capability.
        self.workspace = workspace

    @property
    def knowledge_owner(self) -> UserId:
        """The owner for shareable data (knowledge, graph): the team's scope inside a team
        workspace, the person's own subject everywhere else. Personal-life data (memory,
        Personal-OS, feedback, runs) always uses :attr:`user_id` instead."""
        if self.workspace is not None and self.workspace.kind is WorkspaceKind.TEAM:
            return UserId(workspace_scope(self.workspace.workspace_id))
        return self.user_id

    @classmethod
    def for_service(cls, key: Any) -> "Principal":
        return cls(user_id=UserId(key.principal_id), role=SERVICE_ROLE, service_key=key)

    @property
    def is_service(self) -> bool:
        return self.service_key is not None

    @property
    def is_admin(self) -> bool:
        return self.role in ("Admin", "SuperAdmin")

    @property
    def is_super_admin(self) -> bool:
        return self.role == "SuperAdmin"


def build_router(container: Any, *, authenticator: Any, rate_limiter: Any = None) -> Any:
    """Construct the ``/api/v3`` router.

    ``authenticator`` is a callable ``(request) -> Principal``, injected rather than imported so
    the gateway works with the existing Firebase/JWT verification during migration and with
    whatever replaces it afterwards.
    """
    try:
        from fastapi import (
            APIRouter,
            Body,
            Depends,
            File,
            Form,
            HTTPException,
            Query,
            Request,
            Response,
            UploadFile,
        )
        from fastapi.responses import JSONResponse, StreamingResponse
        from pydantic import BaseModel, Field
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("FastAPI and pydantic are required for the HTTP gateway") from exc

    router = APIRouter(prefix="/api/v3", tags=["aura v3"])
    settings = container.settings

    # -- request models ----------------------------------------------------------------

    class ContextEntry(BaseModel):
        #: AR-5: one piece of caller-supplied evidence. Shape is re-checked by `CallerContext.of`.
        id: str = Field(min_length=1, max_length=128)
        text: str = Field(min_length=1, max_length=MAX_UNTRUSTED_CHARS)
        source: str = Field(default="", max_length=128)

    class ImageAttachment(BaseModel):
        #: ADR-031: one attached image, base64 (a ``data:...;base64,`` prefix is tolerated).
        #: ``media_type`` is the client's claim and is not trusted: the normalizer sniffs bytes.
        media_type: str = Field(default="", max_length=64)
        data_base64: str = Field(min_length=4)

    class ChatRequest(BaseModel):
        message: str = Field(min_length=1, max_length=32_000)
        session_id: str | None = None
        agent: str = "orchestrator"
        use_knowledge: bool = True
        use_memory: bool = True
        use_tools: bool = True
        plan: bool | None = None
        strategy: str | None = None
        routing_policy: str | None = None
        require_local: bool = False
        #: F-04 (ADR-006): the `approval_id`s, from one turn's `pending_tool_calls`, of the held
        #: calls the user approved. The request then resumes *that* turn -- its stored plan, its
        #: own message -- and runs only calls identical to the approved ones, once. Unknown,
        #: expired, reused, or someone else's ids are a 409 `confirmation_invalid`. The former
        #: `confirmed_tools`/`confirmed_call_ids` fields confirmed by name or position; they are
        #: no longer read (an old client's field is ignored, so its call is simply held again).
        approval_ids: list[str] = Field(default_factory=list, max_length=32)
        max_output_tokens: int = Field(default=1024, ge=16, le=8192)
        temperature: float = Field(default=0.3, ge=0.0, le=2.0)
        #: D8 (Desktop IDE only): ask the paired desktop daemon to isolate this task's writes into
        #: a dedicated git worktree keyed by `session_id`, rather than the user's live checkout.
        #: Silently a no-op for any caller without a live desktop pairing.
        desktop_task_isolation: bool = False
        #: Desktop IDE only (audit FSA-006): the workspace root the IDE currently shows, in the
        #: user's machine's own path syntax. Forwarded verbatim to the paired daemon as the `cwd`
        #: of every `run_command`, which runs there only if it lies inside a granted root. Ignored
        #: by the server-side sandbox; absent, the daemon uses its first granted root as before.
        workspace_root: str | None = Field(default=None, max_length=1024)
        #: AR-5: evidence for this turn, carried on the DATA channel (fenced, guarded, tainting)
        #: -- never in `message`, which is the USER channel and is read as instructions.
        context: list[ContextEntry] = Field(default_factory=list, max_length=MAX_CALLER_CONTEXT)
        #: AR-6: ask for one JSON value matching this schema (the ADR-025 subset; top level an
        #: object). Validated, repaired at most once, else 422 `output_invalid`. `/chat` only.
        response_schema: dict[str, Any] | None = None
        #: ADR-031: images to understand alongside `message`. Described by the vision pool and
        #: given to the answer as DATA evidence (never USER text); never stored. The count and
        #: size limits are `AURA_VISION_*` settings, checked before anything is decoded.
        images: list[ImageAttachment] = Field(default_factory=list, max_length=8)

    class IngestRequest(BaseModel):
        title: str = Field(min_length=1, max_length=500)
        content: str = Field(min_length=1)
        document_id: str | None = None
        metadata: dict[str, Any] = Field(default_factory=dict)

    class SpeakRequest(BaseModel):
        text: str = Field(min_length=1, max_length=4000)
        voice: str | None = None

    class CodeCompletionRequest(BaseModel):
        """D6 inline code completion (explicit-trigger, not continuous ghost-text -- see
        docs/architecture/015-desktop-ide.md's D6 plan, decision 3). ``prefix``/``suffix`` are the
        file content immediately before/after the cursor, not the whole file: bounding them here
        (rather than trusting the client) keeps a worst-case request bounded regardless of what a
        client sends."""

        prefix: str = Field(default="", max_length=8_000)
        suffix: str = Field(default="", max_length=4_000)
        language: str | None = Field(default=None, max_length=40)
        max_output_tokens: int = Field(default=64, ge=8, le=256)

    class SearchRequest(BaseModel):
        query: str = Field(min_length=1, max_length=1000)
        limit: int = Field(default=6, ge=1, le=25)

    class MemoryWriteRequest(BaseModel):
        content: str = Field(min_length=1, max_length=2000)
        subject: str | None = Field(default=None, max_length=64)
        kind: str = "semantic"

    class FeedbackRequest(BaseModel):
        turn_id: str
        rating: int = Field(ge=1, le=5)
        correction: str | None = Field(default=None, max_length=8000)
        comment: str | None = Field(default=None, max_length=2000)
        prompt: str | None = Field(default=None, max_length=32_000)
        response: str | None = Field(default=None, max_length=32_000)

    # -- dependencies ------------------------------------------------------------------

    async def principal(request: Request) -> Principal:
        try:
            caller = await authenticator(request)
        except AuraError as exc:
            raise HTTPException(status_code=exc.http_status, detail=exc.to_dict()) from exc
        if caller.is_service:
            # AR-4: a service key reaches only the routes it was issued for -- checked here, once,
            # for every route that authenticates, so a new route is closed to services by default.
            route = request.scope.get("route")
            path = getattr(route, "path", request.url.path)
            if not caller.service_key.allows(request.method, path):
                await audit_service(request, caller, "service.denied", "refused", {"route": f"{request.method} {path}"})
                raise HTTPException(
                    status_code=403,
                    detail=AuthorizationError("this service key is not permitted on this route").to_dict(),
                )
        return caller

    async def audit_service(
        request: Request, caller: Principal, action: str, outcome: str, detail: Mapping[str, Any],
        *, correlation_id: str = "",
    ) -> None:
        key = caller.service_key
        try:
            await container.audit.record(AuditEvent(
                action=action, actor=key.name, actor_role=SERVICE_ROLE, outcome=outcome,
                resource=f"service_key:{key.key_id}",
                correlation_id=correlation_id or request.headers.get("x-correlation-id") or "",
                source_ip=request.client.host if request.client else None,
                detail=dict(detail),
            ))
        except Exception:
            logger.warning("service audit write failed", extra={"action": action}, exc_info=True)

    def require_knowledge_write(caller: Principal) -> None:
        """Inside a team only Owners and Admins change the shared corpus (ADR-034); a Member
        reads it. Outside a team this is the caller's own corpus and nothing is checked."""
        workspace = caller.workspace
        if workspace is not None and workspace.kind is WorkspaceKind.TEAM and not can_in_workspace(
            workspace.role, "manage_knowledge"
        ):
            raise HTTPException(
                status_code=403,
                detail=AuthorizationError("only a workspace owner or admin can change its knowledge").to_dict(),
            )

    # Optional `Idempotency-Key` support (roadmap P1.4; see api/idempotency.py). Set before any
    # route is declared: APIRouter reads `route_class` at decoration time. A route opts in with
    # `dependencies=idempotent`; without the header the route behaves exactly as before.
    from .idempotency import build_idempotency

    router.route_class, _idempotency_guard = build_idempotency(container, principal=principal)
    idempotent = [_idempotency_guard]

    # A service key's own budget applies whether or not per-user limiting is enabled (AR-4).
    from ..security.rate_limit import ROUTE_COSTS, SlidingWindowRateLimiter

    service_limiter = rate_limiter or SlidingWindowRateLimiter(
        container.cache_backend, policies=(), metrics=getattr(container, "metrics", None)
    )

    async def enforce_rate_limit(request: Request, caller: Principal, route_class: str) -> None:
        if caller.is_service:
            cost = ROUTE_COSTS.get(route_class, 1)
            key = caller.service_key
            decision = await service_limiter.check_limit(
                key=key.key_id, scope="service", limit=key.rate_per_minute * max(cost, 1), cost=cost
            )
            if not decision.allowed:
                await audit_service(request, caller, "service.rate_limited", "refused", {"route_class": route_class})
                raise HTTPException(
                    status_code=429,
                    detail=RateLimitedError("rate limit exceeded", retry_after=decision.retry_after_s).to_dict(),
                    headers={"Retry-After": str(decision.retry_after_s)},
                )
            return
        if rate_limiter is None or not settings.security.rate_limit_enabled:
            return
        decision = await rate_limiter.check_all(
            user_key=caller.user_id.value,
            ip_key=extract_client_ip(
                request.headers,
                peer=request.client.host if request.client else None,
                trusted_proxies=settings.security.trusted_proxy_count,
            ),
            route_class=route_class,
        )
        if not decision.allowed:
            raise HTTPException(
                status_code=429,
                detail=RateLimitedError(
                    "rate limit exceeded", retry_after=decision.retry_after_s
                ).to_dict(),
                headers={"Retry-After": str(decision.retry_after_s)},
            )

    def require_admin(caller: Principal) -> None:
        if not caller.is_admin:
            raise HTTPException(
                status_code=403,
                detail=AuthorizationError(
                    "this endpoint requires an administrator role"
                ).to_dict(),
            )

    def require_super_admin(caller: Principal) -> None:
        if not caller.is_super_admin:
            raise HTTPException(
                status_code=403,
                detail=AuthorizationError(
                    "this endpoint requires a SuperAdmin role"
                ).to_dict(),
            )

    async def audit_admin(
        request: Request,
        caller: Principal,
        action: str,
        resource: str,
        outcome: str,
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        try:
            await container.audit.record(
                AuditEvent(
                    action=action,
                    actor=caller.email or caller.user_id.value,
                    actor_role=caller.role,
                    resource=resource,
                    outcome=outcome,
                    correlation_id=request.headers.get("x-correlation-id") or "",
                    source_ip=request.client.host if request.client else None,
                    detail=detail,
                )
            )
        except Exception:
            logger.debug("admin audit write failed", extra={"action": action}, exc_info=True)

    def granted(caller: Principal) -> tuple[str, ...]:
        """The caller's capabilities, plus what the live blueprint binds their role to (AP10)."""
        deployment = getattr(container, "blueprint_deployment", None)
        if deployment is None or caller.is_service:
            return tuple(caller.capabilities)
        return deployment.caller_capabilities(tuple(caller.capabilities), caller.role)

    async def to_turn(payload: ChatRequest, caller: Principal, request: Request) -> TurnRequest:
        # Phase I: shared transcript/message normalize before the kernel (same path live voice uses).
        from ..nlp import normalize_for_turn

        message = await normalize_for_turn(payload.message, engine=getattr(container, "nlp", None))
        if not message.strip():
            raise ValidationError("message contains nothing usable after normalization")
        if caller.is_service:
            # AR-4: a service has no memory, no conversation and no tools. Forced here rather
            # than trusted from the body, so no request shape can make a key write personal state.
            if payload.session_id:
                raise ValidationError("a service key cannot use a session; send each turn on its own")
            payload = payload.model_copy(update={
                "use_memory": False, "use_tools": False, "approval_ids": [],
            })
        if payload.response_schema is not None:
            check_output_schema(payload.response_schema)
            if payload.response_schema.get("type") != "object":
                raise ValidationError("response_schema must describe a JSON object at the top level")
            if len(json.dumps(payload.response_schema)) > _MAX_RESPONSE_SCHEMA_CHARS:
                raise ValidationError("response_schema is too large",
                                      details={"limit_chars": _MAX_RESPONSE_SCHEMA_CHARS})
        connector_tokens = _connector_tokens(request)
        if caller.is_service and connector_tokens:
            raise ValidationError("a service key cannot act with a person's connector sign-in")
        caller_context = tuple(CallerContext.of(id=e.id, text=e.text, source=e.source) for e in payload.context)
        if len({e.id for e in caller_context}) != len(caller_context):
            raise ValidationError("context ids must be unique within a turn")
        images = await normalize_images(payload.images, caller) if payload.images else ()
        return TurnRequest(
            message=message,
            user_id=caller.user_id,
            knowledge_owner=caller.knowledge_owner,
            session_id=SessionId(payload.session_id) if payload.session_id else None,
            agent=payload.agent,
            correlation_id=CorrelationId(
                request.headers.get("x-correlation-id") or CorrelationId.new().value
            ),
            use_knowledge=payload.use_knowledge,
            # Spec §5.2 / ADR-034: a team turn draws on the team's corpus, never on the person's
            # private memory, which a teammate reading the answer must not see.
            use_memory=payload.use_memory and caller.knowledge_owner == caller.user_id,
            use_tools=payload.use_tools,
            plan=payload.plan,
            strategy=_parse_enum(ReasoningStrategy, payload.strategy),
            routing_policy=_parse_enum(RoutingPolicy, payload.routing_policy),
            require_local=payload.require_local,
            capabilities=granted(caller),
            approval_ids=tuple(payload.approval_ids),
            max_output_tokens=payload.max_output_tokens,
            temperature=payload.temperature,
            desktop_task_isolation=payload.desktop_task_isolation,
            workspace_root=payload.workspace_root,
            # Defence in depth behind use_tools=False: an empty grant intersects to no tools.
            delegated_tool_grant=frozenset() if caller.is_service else None,
            caller_context=caller_context,
            response_schema=payload.response_schema,
            connector_tokens=connector_tokens or None,
            images=images,
        )

    def vision_status() -> dict[str, bool]:
        engine = getattr(container, "vision", None)
        return {
            "enabled": engine is not None and engine.enabled,
            "available": engine is not None and engine.available,
        }

    def reject(reason: str) -> None:
        if container.metrics is not None:
            container.metrics.vision_rejections.labels(reason=reason).inc()

    async def normalize_images(attachments: list[Any], caller: Principal) -> tuple[Any, ...]:
        """Decode and normalize a turn's images (ADR-031), refusing before decoding when it can.

        Order matters: availability first (no point decoding for a 503), then the count and the
        *estimated* decoded total from the base64 length, so an oversized body costs no decode.
        """
        vision = settings.vision
        normalizer = getattr(container, "image_normalizer", None)
        engine = getattr(container, "vision", None)
        if engine is None or normalizer is None:
            raise DependencyUnavailableError(
                "image understanding is not enabled on this deployment", dependency="vision"
            )
        if caller.is_service:
            # A key with the chat route would otherwise turn the vision pool into a free OCR
            # service for whoever holds it; a per-key image grant is a deliberate follow-up.
            raise ValidationError("a service key cannot attach images",
                                  details={"reason": "service_images_not_allowed"})
        if len(attachments) > vision.max_images:
            raise ValidationError("too many images in one turn",
                                  details={"reason": "too_many_images", "limit": vision.max_images})
        payloads = [_strip_data_url(a.data_base64) for a in attachments]
        estimated = sum(len(p) * 3 // 4 for p in payloads)
        if estimated > vision.max_request_image_bytes:
            raise ValidationError("the attached images exceed the size limit",
                                  details={"reason": "request_too_large",
                                           "limit_bytes": vision.max_request_image_bytes})
        images = []
        for index, (attachment, data) in enumerate(zip(attachments, payloads)):
            try:
                raw = base64.b64decode(data, validate=True)
            except (binascii.Error, ValueError) as exc:
                reject("invalid_base64")
                raise ValidationError("an image is not valid base64",
                                      details={"reason": "invalid_base64", "index": index}) from exc
            try:
                images.append(await normalizer.normalize(raw, declared_type=attachment.media_type or None))
            except ValidationError as exc:
                reject(str(exc.details.get("reason", "invalid")))
                raise ValidationError(exc.message, details={**exc.details, "index": index}) from exc
        return tuple(images)

    # -- chat --------------------------------------------------------------------------

    @router.post("/chat", dependencies=idempotent)
    async def chat(
        request: Request,
        payload: ChatRequest,
        caller: Principal = Depends(principal),
    ) -> JSONResponse:
        """One complete turn. Use ``/chat/stream`` for incremental output."""
        await enforce_rate_limit(request, caller, "chat")
        turn = await to_turn(payload, caller, request)
        result = await container.kernel.run(turn)
        if caller.is_service:
            # Chat turns are not audited for people; a service's are, so one interpretation can be
            # traced from the calling system's correlation id to the turn that produced it.
            await audit_service(request, caller, "service.chat", result.telemetry.outcome.value,
                                {"turn_id": result.telemetry.turn_id.value},
                                correlation_id=result.telemetry.correlation_id.value)

        if result.telemetry.error_kind == "output_invalid":
            # AR-6: the model never produced JSON matching the caller's schema. Not retryable as
            # sent; the error paths are safe to return (they never carry values).
            return JSONResponse(
                status_code=422,
                content={
                    "error": "output_invalid",
                    "message": "The answer did not match the requested response_schema.",
                    "retryable": False,
                    "details": dict(result.error_details or {}),
                    "correlation_id": result.telemetry.correlation_id.value,
                },
            )
        if result.telemetry.outcome.value != "success":
            # The kernel already produced telemetry for the failure; surface the domain error's
            # status rather than a generic 500.
            status = {"blocked": 400, "budget_exceeded": 429, "rate_limited": 429}.get(
                result.telemetry.outcome.value, 503
            )
            if result.telemetry.error_kind == "confirmation_invalid":
                # F-04: a confirmation naming no pending call this caller may resume. One-shot,
                # so not retryable as sent -- the turn has to be asked again.
                status = 409
            # Admission refusals (P2.4) carry a Retry-After: 503 for a saturated pool, 429 for
            # a user over their concurrent-generation quota (outcome rate_limited above).
            retry_after = result.retry_after_s
            return JSONResponse(
                status_code=status,
                content={
                    "error": result.telemetry.error_kind or "turn_failed",
                    "message": "The turn did not complete.",
                    "retryable": status in (429, 503),
                    "details": {"retry_after": retry_after} if retry_after else {},
                    "correlation_id": result.telemetry.correlation_id.value,
                },
                headers={"Retry-After": str(retry_after)} if retry_after else None,
            )

        return JSONResponse(
            content={
                "message": result.text,
                "citations": list(result.citations),
                "confidence": result.confidence,
                "tools_used": list(result.tool_calls),
                "needs_confirmation": list(result.needs_confirmation),
                "pending_tool_calls": list(result.pending_tool_calls),
                "plan": list(result.plan_summary),
                "resolved_agent": result.resolved_agent,
                "agent_run_id": result.agent_run_id,
                "correlation_id": result.telemetry.correlation_id.value,
                "turn_id": result.telemetry.turn_id.value,
                "usage": {
                    "prompt_tokens": result.telemetry.prompt_tokens,
                    "completion_tokens": result.telemetry.completion_tokens,
                    "cost_usd": round(result.telemetry.cost_usd, 6),
                },
                "provider": result.telemetry.provider,
                "model": result.telemetry.model,
                "cached": result.telemetry.cache.hit,
                "degraded": result.degraded,
                **({"data": result.data} if payload.response_schema is not None else {}),
                **({"vision": result.vision,
                    "images_used": [{k: v for k, v in i.describe().items() if k != "bytes"}
                                    for i in turn.images]} if turn.images else {}),
            },
            headers={"X-Correlation-Id": result.telemetry.correlation_id.value},
        )

    @router.get("/capabilities")
    async def capabilities(caller: Principal = Depends(principal)) -> JSONResponse:
        """What optional features this deployment can serve right now, for any signed-in
        caller -- the client hides an attach-image button rather than offering one that 503s
        (ADR-031). ``available`` reflects configuration and provider declarations, not a probe.
        """
        return JSONResponse(content={"vision": {**vision_status(),
                                                "max_images": settings.vision.max_images,
                                                "max_image_bytes": settings.vision.max_image_bytes}})

    @router.post("/chat/stream")
    async def chat_stream(
        request: Request,
        payload: ChatRequest,
        caller: Principal = Depends(principal),
    ) -> StreamingResponse:
        """Server-sent events with typed frames.

        Frames are ``token``, ``citations``, ``tool_calls``, ``step``, ``actions``, ``revision``,
        ``done``, and ``error`` -- typed rather than a raw token stream the client must
        string-split for an ``[ACTIONS]`` marker, which is the v1 protocol's fragility (TD-12).
        """
        if payload.response_schema is not None:
            # A streamed answer reaches the client before it can be validated (AR-6).
            raise ValidationError("response_schema is supported on POST /api/v3/chat only, not on the stream")
        await enforce_rate_limit(request, caller, "chat")
        turn = await to_turn(payload, caller, request)

        async def events() -> AsyncIterator[bytes]:
            try:
                async for frame in container.kernel.stream(turn):
                    event = frame.get("type", "message")
                    yield f"event: {event}\ndata: {json.dumps(frame)}\n\n".encode()
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("stream failed")
                payload_ = {"type": "error", "error": "internal_error", "message": type(exc).__name__}
                yield f"event: error\ndata: {json.dumps(payload_)}\n\n".encode()

        return StreamingResponse(
            with_keepalive(events(), interval_s=SSE_KEEPALIVE_S),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                # Without this, Nginx buffers the whole response and the stream arrives at once.
                "X-Accel-Buffering": "no",
                "X-Correlation-Id": (turn.correlation_id or CorrelationId.new()).value,
            },
        )

    @router.post("/complete")
    async def complete_code(
        request: Request,
        payload: CodeCompletionRequest,
        caller: Principal = Depends(principal),
    ) -> JSONResponse:
        """D6 inline code completion -- one-shot, not a chat turn. Bypasses the kernel entirely
        (no retrieval/memory/tool-planning/safety-guard-output pass) and calls the router
        directly, the same low-level call ``PlanningEngine.plan()`` makes -- there is no session,
        history, or citation for a fill-in-the-blank completion to carry, and a smaller unguarded
        surface keeps this call's latency budget honest against the tighter deadline a human is
        actively waiting on (see the timeout comment below).
        """
        from ..domain.llm import CompletionRequest as LlmCompletionRequest
        from ..domain.llm import SamplingParams
        from ..domain.messages import Conversation, Message

        await enforce_rate_limit(request, caller, "complete")

        language = payload.language or "code"
        prompt = (
            f"Complete the following {language} at <CURSOR>. Return only the code to insert "
            "at <CURSOR> -- no explanation, no markdown code fences, no repetition of the "
            "surrounding code.\n\n"
            f"{payload.prefix}<CURSOR>{payload.suffix}"
        )

        try:
            response = await container.router.complete(
                LlmCompletionRequest(
                    conversation=Conversation.of([Message.user(prompt)]),
                    sampling=SamplingParams(temperature=0.2, max_output_tokens=payload.max_output_tokens),
                    require_local=True,
                    # Generous over the ~1-10s target the client's explicit-trigger UX promises
                    # (see D6 plan Part D.1/D.2), not the 60-180s a full chat turn gets -- this is
                    # a fill-in-the-blank, not a conversation, and a human is actively waiting.
                    timeout_s=20.0,
                )
            )
        except OverloadedError as exc:
            # No capacity (P2.4): tell the editor when to try again rather than a bare 503.
            return JSONResponse(
                status_code=exc.http_status,
                content=exc.to_dict(),
                headers={"Retry-After": str(exc.retry_after)},
            )
        except Exception as exc:
            logger.warning("inline completion failed", extra={"error": type(exc).__name__})
            return JSONResponse(
                status_code=503,
                content={"error": "completion_unavailable", "message": "Could not generate a completion."},
            )

        text = response.text.strip()
        # Small instruct models reliably wrap output in a code fence even when told not to --
        # strip one defensively rather than showing the fence markers as part of the suggestion.
        if text.startswith("```"):
            text = text.split("\n", 1)[-1] if "\n" in text else text.lstrip("`")
            if text.endswith("```"):
                text = text[: -3]
            text = text.strip("\n")

        return JSONResponse(
            content={
                "text": text,
                "truncated": len(text) >= payload.max_output_tokens * 4,  # rough chars-per-token heuristic
                "provider": response.provider,
                "model": response.model,
            }
        )

    # -- knowledge ---------------------------------------------------------------------

    @router.post("/knowledge/documents", dependencies=idempotent)
    async def ingest_document(
        request: Request, payload: IngestRequest, caller: Principal = Depends(principal)
    ) -> JSONResponse:
        await enforce_rate_limit(request, caller, "ingest")
        require_knowledge_write(caller)
        result = await container.knowledge.ingest(
            content=payload.content,
            title=payload.title,
            document_id=DocumentId(payload.document_id) if payload.document_id else None,
            owner_id=caller.knowledge_owner,
            metadata=payload.metadata,
        )
        if container.cag is not None and result.document is not None:
            await container.cag.on_document_changed(
                result.document.id, owner_id=caller.knowledge_owner
            )
        return JSONResponse(
            content={
                "document_id": result.document.id.value if result.document else None,
                "version": result.document.version if result.document else None,
                "chunks": result.chunks,
                "created": result.created,
                "quarantined_chunks": result.quarantined,
                "skipped_reason": result.skipped_reason,
            }
        )

    @router.post("/knowledge/documents/upload", dependencies=idempotent)
    async def upload_document(
        request: Request,
        file: UploadFile = File(...),
        title: str = Form(..., min_length=1, max_length=500),
        caller: Principal = Depends(principal),
    ) -> JSONResponse:
        """Accept a raw PDF upload -- text and scanned pages alike (Phase F1).

        The one HTTP entry point that was missing to reach `knowledge/documents.py`'s
        page-aware extraction (`extract_pdf` already tells a text page from a scanned one and
        keeps page numbers) and `apply_ocr`: both existed and were tested, but nothing in this
        router ever called them. `keep_images` is only requested when Pillow is actually present
        (`image_extraction_available()`), and OCR only runs when the container built a real
        `OcrEngine`; a deployment with neither still ingests every text page and marks scanned
        ones with the same honest "OCR is not configured" note `extract_pdf` always produces.
        """
        await enforce_rate_limit(request, caller, "ingest")
        require_knowledge_write(caller)
        data = await file.read()
        if len(data) > settings.knowledge.max_document_bytes:
            raise ValidationError(
                "uploaded file exceeds the configured size limit",
                details={"limit_bytes": settings.knowledge.max_document_bytes},
            )

        from ..knowledge.documents import apply_ocr, extract_pdf, image_extraction_available

        extraction = extract_pdf(data, title=title, keep_images=image_extraction_available())
        if extraction.unreadable_pages and container.ocr_engine is not None:
            extraction = await apply_ocr(extraction, container.ocr_engine)

        result = await container.knowledge.ingest(
            title=title, owner_id=caller.knowledge_owner, segments=extraction.blocks,
        )
        if container.cag is not None and result.document is not None:
            await container.cag.on_document_changed(
                result.document.id, owner_id=caller.knowledge_owner
            )
        return JSONResponse(
            content={
                "document_id": result.document.id.value if result.document else None,
                "version": result.document.version if result.document else None,
                "chunks": result.chunks,
                "created": result.created,
                "quarantined_chunks": result.quarantined,
                "skipped_reason": result.skipped_reason,
                "pages": extraction.pages,
                "unreadable_pages": list(extraction.unreadable_pages),
                "blank_pages": list(extraction.blank_pages),
                "ocr_used": container.ocr_engine.name if container.ocr_engine is not None else None,
            }
        )

    @router.post("/knowledge/search")
    async def search(
        request: Request, payload: SearchRequest, caller: Principal = Depends(principal)
    ) -> JSONResponse:
        await enforce_rate_limit(request, caller, "search")
        result = await container.knowledge.retrieve(
            payload.query, owner_id=caller.knowledge_owner, limit=payload.limit
        )
        return JSONResponse(
            content={
                "query": payload.query,
                "strategy": result.strategy,
                "candidates_considered": result.candidates_considered,
                "results": [
                    {
                        "content": c.chunk.content,
                        "citation": c.citation(),
                        "score": round(c.score, 4),
                        "document_id": c.chunk.document_id.value,
                        "version": c.chunk.document_version,
                    }
                    for c in result.chunks
                ],
                # An empty result with candidates considered is the honest "I searched and found
                # nothing relevant" signal, and the client should render it as such.
                "searched_but_empty": result.is_empty_but_searched,
            }
        )

    @router.delete("/knowledge/documents/{document_id}", dependencies=idempotent)
    async def delete_document(
        request: Request, document_id: str, caller: Principal = Depends(principal)
    ) -> JSONResponse:
        await enforce_rate_limit(request, caller, "ingest")
        require_knowledge_write(caller)
        doc_id = DocumentId(document_id)
        removed = await container.knowledge.delete_document(
            doc_id, owner_id=caller.knowledge_owner
        )
        if container.cag is not None:
            await container.cag.on_document_changed(doc_id, owner_id=caller.knowledge_owner)
        return JSONResponse(content={"deleted": True, "chunks_removed": removed})

    @router.post("/knowledge/documents/{document_id}/cag-pin", dependencies=idempotent)
    async def pin_cag_document(
        request: Request, document_id: str, caller: Principal = Depends(principal)
    ) -> JSONResponse:
        """Pin a user-owned document into the CAG prefix (ADR-010)."""
        await enforce_rate_limit(request, caller, "ingest")
        require_knowledge_write(caller)
        if container.cag is None:
            raise DependencyUnavailableError(
                "CAG is disabled on this deployment",
                dependency="cag",
            )
        document = await container.cag.set_pin(
            DocumentId(document_id), owner_id=caller.knowledge_owner, pinned=True
        )
        return JSONResponse(
            content={
                "document_id": document.id.value,
                "version": document.version,
                "cag_pinned": document.cag_pinned,
            }
        )

    @router.delete("/knowledge/documents/{document_id}/cag-pin", dependencies=idempotent)
    async def unpin_cag_document(
        request: Request, document_id: str, caller: Principal = Depends(principal)
    ) -> JSONResponse:
        """Remove a document from the CAG prefix."""
        await enforce_rate_limit(request, caller, "ingest")
        require_knowledge_write(caller)
        if container.cag is None:
            raise DependencyUnavailableError(
                "CAG is disabled on this deployment",
                dependency="cag",
            )
        document = await container.cag.set_pin(
            DocumentId(document_id), owner_id=caller.knowledge_owner, pinned=False
        )
        return JSONResponse(
            content={
                "document_id": document.id.value,
                "version": document.version,
                "cag_pinned": document.cag_pinned,
            }
        )

    @router.get("/knowledge/cag")
    async def cag_preview(
        request: Request, caller: Principal = Depends(principal)
    ) -> JSONResponse:
        """Preview the caller's current CAG prefix (budget, docs, clipped text)."""
        await enforce_rate_limit(request, caller, "read")
        if container.cag is None:
            return JSONResponse(
                content={
                    "enabled": False,
                    "tokens": 0,
                    "budget": 0,
                    "documents": [],
                }
            )
        prefix = await container.cag.prefix_for(caller.knowledge_owner)
        if prefix is None:
            return JSONResponse(
                content={
                    "enabled": True,
                    "tokens": 0,
                    "budget": container.cag.max_tokens,
                    "global_budget": container.cag.global_tokens,
                    "user_budget": container.cag.user_tokens,
                    "documents": [],
                    "identity": None,
                    "text": "",
                }
            )
        return JSONResponse(content={"enabled": True, **prefix.preview()})

    @router.get("/knowledge/documents")
    async def list_documents(
        request: Request,
        caller: Principal = Depends(principal),
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> JSONResponse:
        await enforce_rate_limit(request, caller, "read")
        documents = await container.documents.list_active(
            owner_id=caller.knowledge_owner, limit=limit, offset=offset
        )
        return JSONResponse(
            content={
                "documents": [
                    {
                        "document_id": d.id.value,
                        "title": d.title,
                        "version": d.version,
                        "status": str(d.status),
                        "cag_pinned": d.cag_pinned,
                        "created_at": d.created_at.isoformat(),
                    }
                    for d in documents
                ]
            }
        )

    # -- knowledge graph (Phase 11) --------------------------------------------------------

    def _entity_json(entity: Any) -> dict[str, Any]:
        return {
            "key": entity.key,
            "name": entity.name,
            "type": entity.type,
            "aliases": list(entity.aliases),
            "confidence": round(entity.confidence, 4),
            "mention_count": entity.mention_count,
            "sources": list(entity.sources),
            "valid_from": entity.valid_from.isoformat() if entity.valid_from else None,
            "valid_to": entity.valid_to.isoformat() if entity.valid_to else None,
        }

    def _relation_json(relation: Any) -> dict[str, Any]:
        return {
            "subject": relation.subject_key,
            "predicate": relation.predicate,
            "object": relation.object_key,
            "confidence": round(relation.confidence, 4),
            "sources": list(relation.sources),
            "valid_from": relation.valid_from.isoformat() if relation.valid_from else None,
            "valid_to": relation.valid_to.isoformat() if relation.valid_to else None,
        }

    @router.get("/knowledge/graph")
    async def knowledge_graph(
        request: Request,
        caller: Principal = Depends(principal),
        query: str = Query(default="", max_length=200),
        type: str | None = Query(default=None, max_length=40),
        limit: int = Query(default=50, ge=1, le=200),
        include_expired: bool = Query(default=False),
    ) -> JSONResponse:
        """The caller's own entities and the relations between them.

        Owner-scoped by construction: every store call takes ``caller.user_id`` and nothing from
        the request can name another owner. Global (``owner_id=None``) facts are not listed --
        this is "what AURA has learned from *my* documents".
        """
        await enforce_rate_limit(request, caller, "read")
        enabled = container.settings.knowledge.graph_enabled
        entities = await container.graph.find_entities(
            owner_id=caller.knowledge_owner, name=query or None, type=type, limit=limit,
            include_expired=include_expired,
        )
        relations = await container.graph.list_relations(
            owner_id=caller.knowledge_owner,
            entity_keys=[e.key for e in entities] if query or type else None,
            limit=limit * 2,
            include_expired=include_expired,
        )
        return JSONResponse(
            content={
                "enabled": enabled,
                "entities": [_entity_json(e) for e in entities],
                "relations": [_relation_json(r) for r in relations],
            }
        )

    @router.get("/knowledge/graph/entities/{entity_key}")
    async def knowledge_graph_entity(
        request: Request,
        entity_key: str,
        caller: Principal = Depends(principal),
        depth: int = Query(default=1, ge=1, le=3),
    ) -> JSONResponse:
        """One of the caller's entities, its provenance, and its current neighbourhood.

        Another user's key is reported exactly like an absent one (404).
        """
        await enforce_rate_limit(request, caller, "read")
        entity = await container.graph.get_entity(entity_key, owner_id=caller.knowledge_owner)
        if entity is None:
            raise HTTPException(status_code=404, detail={"error": "not_found"})
        neighbours, relations = await container.graph.neighbours(
            entity_key, owner_id=caller.knowledge_owner, depth=depth, limit=100
        )
        provenance = await container.graph.provenance(
            owner_id=caller.knowledge_owner, entity_key=entity_key
        )
        return JSONResponse(
            content={
                "entity": _entity_json(entity),
                "provenance": [
                    {
                        "source": p.source_id,
                        "document_id": p.document_id,
                        "document_version": p.document_version,
                        "extractor": p.extractor,
                        "confidence": round(p.confidence, 4),
                        "retired_at": p.retired_at.isoformat() if p.retired_at else None,
                    }
                    for p in provenance
                ],
                "neighbours": [_entity_json(e) for e in neighbours if e.key != entity_key],
                "relations": [_relation_json(r) for r in relations],
            }
        )

    # -- voice (Phase F2) ----------------------------------------------------------------

    #: A voice note is short by nature; this is generous headroom, not a target -- unlike
    #: `knowledge.max_document_bytes` there is no per-deployment setting for it yet.
    MAX_AUDIO_BYTES = 25 * 1024 * 1024

    @router.post("/voice/transcribe")
    async def transcribe_audio(
        request: Request, file: UploadFile = File(...), caller: Principal = Depends(principal),
    ) -> JSONResponse:
        """Speech to text via the container's `SpeechToText` engine (self-hosted
        `faster-whisper`, Phase F2). 503s with a clear `dependency_unavailable` body when no
        engine is configured, rather than pretending an empty transcript is a real answer."""
        await enforce_rate_limit(request, caller, "voice")
        if container.stt_engine is None:
            raise DependencyUnavailableError(
                "no speech-to-text engine is configured on this deployment",
                dependency="faster-whisper",
            )
        data = await file.read()
        if len(data) > MAX_AUDIO_BYTES:
            raise ValidationError(
                "uploaded audio exceeds the size limit", details={"limit_bytes": MAX_AUDIO_BYTES}
            )
        text = await container.stt_engine.transcribe(data)
        return JSONResponse(content={"text": text, "engine": container.stt_engine.name})

    @router.post("/voice/speak")
    async def synthesize_speech(
        request: Request,
        payload: SpeakRequest,
        caller: Principal = Depends(principal),
    ) -> Response:
        """Text to speech via the container's `TextToSpeech` engine (self-hosted Piper
        Arabic + espeak-ng English, Phase F2). Returns WAV bytes directly rather than
        base64-in-JSON -- the client plays the response body as-is."""
        await enforce_rate_limit(request, caller, "voice")
        if container.tts_engine is None:
            raise DependencyUnavailableError(
                "no text-to-speech engine is configured on this deployment", dependency="espeak-ng",
            )
        audio = await container.tts_engine.synthesize(payload.text, voice=payload.voice)
        if not audio:
            raise ValidationError("text contains nothing speakable")
        return Response(
            content=audio, media_type="audio/wav",
            headers={"X-Engine": container.tts_engine.name},
        )

    # -- memory ------------------------------------------------------------------------

    @router.get("/memory")
    async def list_memories(
        request: Request,
        caller: Principal = Depends(principal),
        query: str = Query(default="", max_length=500),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> JSONResponse:
        await enforce_rate_limit(request, caller, "read")
        if container.memory is None:
            return JSONResponse(content={"memories": [], "enabled": False})
        if query:
            hits = await container.memory.recall(
                MemoryQuery(owner_id=caller.user_id, text=query, limit=limit)
            )
            records = [h.record for h in hits]
        else:
            records = await container.memory_store.list_for_owner(caller.user_id, limit=limit)
        return JSONResponse(
            content={
                "enabled": True,
                "memories": [
                    {
                        "id": r.id.value,
                        "kind": str(r.kind),
                        "content": r.content,
                        "subject": r.subject,
                        "confidence": round(r.confidence, 3),
                        "source": r.provenance.source,
                        "created_at": r.created_at.isoformat(),
                    }
                    for r in records
                ],
            }
        )

    @router.post("/memory", dependencies=idempotent)
    async def write_memory(
        request: Request, payload: MemoryWriteRequest, caller: Principal = Depends(principal)
    ) -> JSONResponse:
        await enforce_rate_limit(request, caller, "read")
        if container.memory is None:
            raise HTTPException(status_code=503, detail={"error": "memory_disabled"})
        record = await container.memory.remember(
            owner_id=caller.user_id,
            content=payload.content,
            subject=payload.subject,
            kind=_parse_enum(MemoryKind, payload.kind) or MemoryKind.SEMANTIC,
            source=f"api:v3:{caller.user_id.value}",
        )
        return JSONResponse(content={"id": record.id.value, "content": record.content})

    @router.delete("/memory/{memory_id}", dependencies=idempotent)
    async def delete_memory(
        request: Request, memory_id: str, caller: Principal = Depends(principal)
    ) -> JSONResponse:
        await enforce_rate_limit(request, caller, "read")
        if container.memory is None:
            raise HTTPException(status_code=503, detail={"error": "memory_disabled"})
        from ..domain.identifiers import MemoryId

        deleted = await container.memory.forget(MemoryId(memory_id), owner_id=caller.user_id)
        if not deleted:
            raise HTTPException(status_code=404, detail={"error": "not_found"})
        return JSONResponse(content={"deleted": True})

    # -- personal data key (ADR-032) ---------------------------------------------------

    #: Own budget, independent of the general limiter (which may be disabled): a DEK changes
    #: rarely, and a client fetches it once per install plus once per rotation.
    data_key_limiter = SlidingWindowRateLimiter(
        container.cache_backend, policies=(), metrics=getattr(container, "metrics", None)
    )
    data_key_headers = {"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"}

    @router.get("/keys/data")
    async def personal_data_key(
        request: Request, caller: Principal = Depends(principal)
    ) -> JSONResponse:
        """The caller's own data keys, for encrypting the synced personal-OS fields (ADR-032).

        Always the authenticated subject's keys -- nothing in the request can name another user.
        Never cached (``no-store``), never logged (only the key ids reach the audit log), refused
        to service keys, and refused over plain HTTP in staging/production.
        """
        if caller.is_service:
            await audit_service(request, caller, "data_key.denied", "refused", {})
            raise HTTPException(
                status_code=403,
                detail=AuthorizationError("data keys are issued to users only").to_dict(),
                headers=data_key_headers,
            )
        forwarded = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
        if settings.environment.is_production_like and (forwarded or request.url.scheme) == "http":
            raise HTTPException(
                status_code=403,
                detail={"error": "tls_required", "message": "data keys are only served over TLS"},
                headers=data_key_headers,
            )
        decision = await data_key_limiter.check_limit(
            key=caller.user_id.value, scope="data_key", limit=10
        )
        if not decision.allowed:
            raise HTTPException(
                status_code=429,
                detail=RateLimitedError(
                    "rate limit exceeded", retry_after=decision.retry_after_s
                ).to_dict(),
                headers={**data_key_headers, "Retry-After": str(decision.retry_after_s)},
            )
        keyring = await container.user_data_keys.keyring(caller.user_id, create=True)
        try:
            await container.audit.record(AuditEvent(
                action="data_key.read", actor=caller.email or caller.user_id.value,
                actor_role=caller.role, resource=f"user:{caller.user_id.value}", outcome="ok",
                correlation_id=request.headers.get("x-correlation-id") or "",
                source_ip=request.client.host if request.client else None,
                detail={"kids": [k.kid for k in keyring.keys], "primary": keyring.primary.kid},
            ))
        except Exception:
            logger.warning("data key audit write failed", exc_info=False)
        return JSONResponse(
            content={
                "format": "gcm2",
                "primary": keyring.primary.kid,
                "keys": [
                    {
                        "kid": k.kid,
                        "key": base64.b64encode(k.key).decode("ascii"),
                        "created_at": k.created_at.isoformat(),
                    }
                    for k in keyring.keys
                ],
            },
            headers=data_key_headers,
        )

    # -- sessions ----------------------------------------------------------------------

    @router.delete("/sessions/{session_id}", dependencies=idempotent)
    async def delete_session(
        request: Request, session_id: str, caller: Principal = Depends(principal)
    ) -> JSONResponse:
        """Forget the server's transcript of one conversation (audit F-07).

        Deleting a chat on the client removed its local rows and tombstoned their sync copies, but
        the episodic transcript the kernel injects into every later turn of that session (cache,
        30-day TTL) stayed: the assistant kept "remembering" a conversation the user had deleted.
        Owner-scoped like every episodic call, so naming another user's session id clears nothing
        of theirs; idempotent, so a client retrying a delete it could not confirm gets 200 again.
        """
        await enforce_rate_limit(request, caller, "read")
        try:
            session = SessionId(session_id)
        except ValueError as exc:
            raise ValidationError("session id is not a valid identifier") from exc
        await container.episodic.clear(session, caller.user_id)
        return JSONResponse(content={"deleted": True})

    # -- feedback ----------------------------------------------------------------------

    @router.post("/feedback", dependencies=idempotent)
    async def submit_feedback(
        request: Request, payload: FeedbackRequest, caller: Principal = Depends(principal)
    ) -> JSONResponse:
        """Record a rating or correction.

        Feeds the curation pipeline (ADR-008 Loop B). A correction is the highest-signal
        training example the system can obtain, which is why the endpoint accepts one.
        """
        await enforce_rate_limit(request, caller, "read")
        from datetime import datetime, timezone
        from uuid import uuid4

        from ..learning.curation import FeedbackRecord

        record = FeedbackRecord(
            id=str(uuid4()),
            prompt=payload.prompt or "",
            response=payload.response or "",
            rating=payload.rating,
            correction=payload.correction,
            user_ref=caller.user_id.value,
            created_at=datetime.now(timezone.utc),
            metadata={
                "turn_id": payload.turn_id,
                "comment": payload.comment or "",
            },
        )
        await container.feedback.put(record, owner_id=caller.user_id)
        container.metrics.feedback_total.labels(rating=str(payload.rating)).inc()
        logger.info(
            "feedback recorded",
            extra={"turn_id": payload.turn_id, "rating": payload.rating,
                   "has_correction": bool(payload.correction)},
        )
        return JSONResponse(content={"recorded": True})

    # -- introspection -----------------------------------------------------------------

    @router.get("/tools")
    async def list_tools(caller: Principal = Depends(principal)) -> JSONResponse:
        specs = container.tools.available(capabilities=granted(caller))
        return JSONResponse(
            content={
                "tools": [
                    {
                        "name": s.name,
                        "description": s.description,
                        "category": str(s.category),
                        "side_effecting": s.side_effecting,
                        "requires_confirmation": s.requires_confirmation,
                        "parameters": s.json_schema(),
                    }
                    for s in specs
                ]
            }
        )

    @router.get("/agents")
    async def list_agents(caller: Principal = Depends(principal)) -> JSONResponse:
        caps = frozenset(granted(caller))
        return JSONResponse(
            content={
                "agents": [
                    {
                        "id": spec.id,
                        "name": spec.name,
                        "prompt": spec.prompt_template_id,
                        "version": container.prompts.get(spec.prompt_template_id).version,
                        "description": container.prompts.get(spec.prompt_template_id).description,
                        # None means "no restriction beyond your own capabilities" (the
                        # orchestrator); a list is this persona's actual tool grant (Phase C1).
                        "allowed_tools": (
                            sorted(spec.allowed_tools) if spec.allowed_tools is not None else None
                        ),
                        "risk_ceiling": spec.risk_ceiling.name,
                        "required_capabilities": sorted(spec.required_capabilities),
                        # Whether *this* caller can actually select this persona -- a persona
                        # under-privileged for the caller silently resolves to the orchestrator
                        # at turn time (AgentRegistry.resolve), so surfacing it here lets a client
                        # grey it out instead of offering a choice that quietly does something else.
                        "usable": spec.required_capabilities.issubset(caps),
                    }
                    for spec in container.agents
                ]
            }
        )

    @router.get("/engineering/workspaces")
    async def list_agent_workspaces(caller: Principal = Depends(principal)) -> JSONResponse:
        """The caller's persistent engineer-agent workspaces.

        Read on demand rather than synced: a workspace is live host state, and a client replica
        would be stale the moment the reaper ran. `expires_at` is computed from the deployment's
        configured TTL rather than stored, so changing the TTL does not leave every existing row
        advertising the old one.
        """
        if container.workspace_manager is None:
            return JSONResponse(content={"workspaces": [], "enabled": False})
        ttl_hours = container.settings.sandbox.workspace_ttl_hours
        rows = await container.workspace_manager.list_for_user(caller.user_id)
        return JSONResponse(
            content={
                "enabled": True,
                "ttl_hours": ttl_hours,
                "workspaces": [
                    {
                        "id": w.id.value,
                        "repo": w.repo,
                        "status": w.status,
                        "branch": w.branch,
                        "created_at": w.created_at.isoformat(),
                        "last_active_at": w.last_active_at.isoformat(),
                        "expires_at": (
                            w.last_active_at + timedelta(hours=ttl_hours)
                        ).isoformat(),
                    }
                    for w in rows
                ],
            }
        )

    @router.delete("/engineering/workspaces/{workspace_id}", dependencies=idempotent)
    async def delete_agent_workspace(
        workspace_id: str, caller: Principal = Depends(principal)
    ) -> JSONResponse:
        """Delete one of the caller's workspaces and everything in it.

        Ownership is checked against the caller rather than trusted from the path: a workspace id
        is guessable enough that treating it as a capability would let one user delete another's
        checkout. A workspace belonging to someone else is reported as absent, not forbidden --
        the same answer either way tells an unauthorised caller nothing about what exists.
        """
        if container.workspace_manager is None:
            raise HTTPException(status_code=404, detail="workspaces are not enabled")
        mine = await container.workspace_manager.list_for_user(caller.user_id)
        target = next((w for w in mine if w.id.value == workspace_id), None)
        if target is None:
            raise HTTPException(status_code=404, detail="workspace not found")
        await container.workspace_manager.destroy(target)
        return JSONResponse(content={"deleted": workspace_id, "repo": target.repo})

    @router.get("/engineering/runs")
    async def list_engineer_runs(
        caller: Principal = Depends(principal), since: str | None = Query(default=None),
    ) -> JSONResponse:
        """Durable history of engineer-agent tool outcomes for the caller (Engineer Task History).

        Not part of the generic `/api/v3/sync` cursor protocol: `EngineerRun` rows are keyed by
        the `UserId` value object's string form, not the v2 relational `users.id` UUID every
        `SYNCABLE_MODELS` table assumes -- see `adapters/postgres_engineer_runs.py`.
        """
        if container.engineer_runs is None:
            return JSONResponse(content={"runs": []})
        since_dt = None
        if since:
            try:
                since_dt = datetime.fromisoformat(since)
            except ValueError:
                raise HTTPException(
                    status_code=422,
                    detail=ValidationError("`since` must be an ISO-8601 timestamp").to_dict(),
                )
        runs = await container.engineer_runs.list_for_user(caller.user_id, since=since_dt)
        return JSONResponse(
            content={
                "runs": [
                    {
                        "id": r.id.value,
                        "turn_id": r.turn_id,
                        "correlation_id": r.correlation_id,
                        "persona": r.persona,
                        "tool": r.tool,
                        "call_id": r.call_id,
                        "status": r.status,
                        "pr_url": r.pr_url,
                        "branch": r.branch,
                        "commit_sha": r.commit_sha,
                        "sandbox_transcript": r.sandbox_transcript,
                        "created_at": r.created_at.isoformat(),
                        "updated_at": r.updated_at.isoformat(),
                    }
                    for r in runs
                ]
            }
        )

    # -- agent runs (GAP-024) -----------------------------------------------------------

    @router.get("/agents/runs")
    async def list_agent_runs(
        request: Request,
        caller: Principal = Depends(principal),
        agent: str | None = Query(default=None, max_length=64),
        status: str | None = Query(default=None, max_length=32),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> JSONResponse:
        """The caller's own agent runs, newest first, with run-level metrics. Owner-scoped in
        the store (``user_id`` is a required argument of every read), not filtered here."""
        await enforce_rate_limit(request, caller, "read")
        from ..domain.agent_runs import run_metrics

        store = getattr(container, "agent_runs", None)
        if store is None:
            return JSONResponse(content={"runs": []})
        runs = await store.list_runs(caller.user_id, agent_id=agent, status=status, limit=limit)
        return JSONResponse(
            content={"runs": [{**r.as_dict(), "metrics": run_metrics(r)} for r in runs]}
        )

    @router.get("/agents/runs/{run_id}")
    async def get_agent_run(
        request: Request, run_id: str, caller: Principal = Depends(principal),
    ) -> JSONResponse:
        """One of the caller's runs with its steps. Another user's run id reads as 404 --
        indistinguishable from an id that never existed."""
        await enforce_rate_limit(request, caller, "read")
        from ..domain.agent_runs import run_metrics
        from ..domain.identifiers import AgentRunId

        store = getattr(container, "agent_runs", None)
        try:
            ident = AgentRunId(run_id)
        except ValueError:
            raise HTTPException(status_code=404, detail="run not found")
        run = await store.get_run(ident, user_id=caller.user_id) if store is not None else None
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        steps = await store.list_steps(ident, user_id=caller.user_id)
        return JSONResponse(
            content={
                **run.as_dict(),
                "metrics": run_metrics(run),
                "steps": [s.as_dict() for s in steps],
            }
        )

    # -- admin -------------------------------------------------------------------------

    @router.get("/admin/status")
    async def admin_status(caller: Principal = Depends(principal)) -> JSONResponse:
        require_admin(caller)
        report = getattr(container.refresh_pipeline, "last_report", None)
        extras = getattr(container, "status_extras", None)
        extra_payload = extras() if callable(extras) else {}
        return JSONResponse(
            content={
                "health": await container.health(),
                "providers": container.router.stats(),
                "prompts": container.prompts.describe(),
                "tools": container.tools.describe(),
                "settings": container.settings.as_dict(),
                "learning": extra_payload.get("learning") or {},
                "refresh": report.as_dict() if report else None,
                "vision": vision_status(),
            }
        )

    @router.post("/admin/index/rebuild", dependencies=idempotent)
    async def rebuild_index(
        request: Request, caller: Principal = Depends(principal)
    ) -> JSONResponse:
        """Build a candidate index and promote it if the gates pass (ADR-007)."""
        require_admin(caller)
        identity = container.index_manager.identity_for_current_embedder()
        report = await container.index_manager.build(identity=identity)
        gate = await container.index_manager.evaluate_promotion(identity, report=report)
        promoted = False
        if gate.passed:
            await container.index_manager.promote(identity, gate=gate, reason="manual_rebuild")
            promoted = True
        await audit_admin(
            request, caller, "admin.index.rebuild", identity.physical_name,
            "promoted" if promoted else "gated",
            detail={"gate_failures": list(gate.failures)},
        )
        return JSONResponse(
            content={
                "index": identity.physical_name,
                "documents": report.documents,
                "chunks": report.chunks,
                "failures": report.failures,
                "promoted": promoted,
                "gate_failures": list(gate.failures),
                "measurements": dict(gate.measurements),
            }
        )

    @router.post("/admin/prompts/{template_id}/pin")
    async def pin_prompt(
        request: Request,
        template_id: str,
        version: str = Body(embed=True),
        caller: Principal = Depends(principal),
    ) -> JSONResponse:
        """Pin a prompt version -- the rollback mechanism for a bad prompt change (TD-11)."""
        require_admin(caller)
        container.prompts.pin(template_id, version)
        await audit_admin(request, caller, "admin.prompt.pin", f"{template_id}@{version}", "ok")
        return JSONResponse(content={"pinned": f"{template_id}@{version}"})

    @router.post("/admin/providers/{name}/reset-circuit")
    async def reset_circuit(
        request: Request, name: str, caller: Principal = Depends(principal)
    ) -> JSONResponse:
        require_admin(caller)
        container.router.reset_circuit(name)
        await audit_admin(request, caller, "admin.provider.reset_circuit", name, "ok")
        return JSONResponse(content={"reset": name})

    register_admin_ops(
        router,
        container,
        principal=principal,
        require_admin=require_admin,
        require_super_admin=require_super_admin,
        audit_admin=audit_admin,
        idempotent=idempotent,
        Body=Body,
        Depends=Depends,
        HTTPException=HTTPException,
        JSONResponse=JSONResponse,
        Query=Query,
        Request=Request,
    )

    from .admin_adaptation import register_adaptation_ops

    register_adaptation_ops(
        router,
        container,
        principal=principal,
        require_super_admin=require_super_admin,
        idempotent=idempotent,
        Depends=Depends,
        HTTPException=HTTPException,
        JSONResponse=JSONResponse,
        Query=Query,
        Request=Request,
    )

    from .admin_service_keys import register_service_key_ops

    register_service_key_ops(
        router,
        container,
        principal=principal,
        require_super_admin=require_super_admin,
        Depends=Depends,
        JSONResponse=JSONResponse,
    )

    from .workspaces import register_workspace_ops

    register_workspace_ops(
        router,
        container,
        principal=principal,
        idempotent=idempotent,
        Depends=Depends,
        JSONResponse=JSONResponse,
        Response=Response,
    )

    # The WebSocket routes authenticate per frame without the `principal` dependency, so the
    # service-key route gate never sees them. A service key opens neither (AR-4).
    async def person_authenticator(request: Any) -> Principal:
        caller = await authenticator(request)
        if caller.is_service:
            raise AuthenticationError("a service key cannot open this connection")
        return caller

    from .voice_live import register_voice_live

    register_voice_live(
        router,
        container,
        authenticator=person_authenticator,
        rate_limiter=rate_limiter,
    )

    from .host_agent import register_host_agent

    register_host_agent(router, container, authenticator=person_authenticator)

    return router


def build_app(
    container: Any, *, authenticator: Any, rate_limiter: Any = None, http_metrics: bool = True
) -> Any:
    """Standalone FastAPI app exposing v3 plus health and metrics.

    Used by the CLI, tests, and any deployment that runs the AI gateway as its own service. The
    integrated deployment mounts :func:`build_router` on the existing app instead (ADR-002).

    ``http_metrics=False`` leaves the per-route request metrics to the caller, which must then
    add :class:`~aura_core.api.http_metrics.HttpMetricsMiddleware` *last* (outermost) itself --
    ``aura_service`` does, so the requests its own outer middlewares refuse are counted too.
    """
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse, Response

    # The interactive docs and the schema are development tooling. Served in production they are
    # a complete route map (every admin route, every request field) for anyone who asks -- found
    # live on the Railway target on 2026-11-07, where `/docs` and `/openapi.json` answered 200
    # anonymously (tests/unit/test_openapi_exposure.py). The routes behind them keep their auth;
    # the map itself is what goes.
    publish_schema = not container.settings.environment.is_production_like
    app = FastAPI(
        title="AURA AI Gateway",
        version="3.0.0",
        description="Enterprise AI platform gateway. See docs/architecture/00-overview.md",
        docs_url="/docs" if publish_schema else None,
        redoc_url="/redoc" if publish_schema else None,
        openapi_url="/openapi.json" if publish_schema else None,
    )
    app.include_router(build_router(container, authenticator=authenticator, rate_limiter=rate_limiter))

    # The header floor and the metrics gate live here, not only in nginx: build_app is the whole
    # edge on any target that runs the gateway as its own service (Railway, the CLI), and a live
    # probe of the Railway Control Plane found no security headers at all and /metrics readable by
    # anyone. See aura_core/api/security_headers.py for why CSP is not sent from here.
    from .security_headers import MetricsAuthMiddleware, SecurityHeadersMiddleware

    # Fail closed where the app may be the edge: a production-like deployment without a token
    # hides the endpoint (token=None answers 404 to everyone) instead of publishing it.
    metrics_token = container.settings.observability.metrics_token
    if metrics_token or container.settings.environment.is_production_like:
        app.add_middleware(
            MetricsAuthMiddleware,
            path=container.settings.observability.metrics_path,
            token=metrics_token or None,
        )
    if container.settings.security.security_headers_enabled:
        app.add_middleware(
            SecurityHeadersMiddleware, csp=container.settings.security.content_security_policy
        )
    if http_metrics:
        from .http_metrics import HttpMetricsMiddleware

        app.add_middleware(HttpMetricsMiddleware, metrics=container.metrics)

    @app.exception_handler(AuraError)
    async def domain_error_handler(request: Request, exc: AuraError) -> JSONResponse:
        """Map domain errors to status codes.

        Every error carries its own ``http_status`` and a stable ``kind`` slug, so clients
        branch on a machine-readable value rather than parsing prose.
        """
        if exc.http_status >= 500 and not isinstance(exc, OverloadedError):
            logger.error("request failed", extra={"error": exc.kind, "details": exc.details})
        retry_after = getattr(exc, "retry_after", None)
        return JSONResponse(
            status_code=exc.http_status,
            content=exc.to_dict(),
            headers={"Retry-After": str(retry_after)} if retry_after else None,
        )

    from fastapi.exception_handlers import http_exception_handler
    from starlette.exceptions import HTTPException as StarletteHTTPException

    @app.exception_handler(StarletteHTTPException)
    async def wrapped_domain_error_handler(request: Request, exc: StarletteHTTPException) -> Response:
        """One error envelope for errors raised from dependencies (auth, service-key scope, rate
        limit, idempotency), which carry ``AuraError.to_dict()`` in ``HTTPException.detail``.

        The domain fields go at the top level, as ``domain_error_handler`` and ``/chat`` already
        send them, so a caller reads ``error``/``retryable`` in one place. ``detail`` keeps the same
        dict because the Flutter client and the console read it there today (both accept either).
        Any other ``HTTPException`` keeps FastAPI's own body.
        """
        detail = exc.detail
        if isinstance(detail, dict) and isinstance(detail.get("error"), str):
            return JSONResponse(
                status_code=exc.status_code, content={**detail, "detail": detail},
                headers=getattr(exc, "headers", None),
            )
        return await http_exception_handler(request, exc)

    @app.get("/health/live")
    async def liveness() -> JSONResponse:
        """Process is running. Deliberately checks nothing else -- a liveness probe that fails
        on a dependency outage causes the orchestrator to restart a healthy process, turning a
        degradation into an outage."""
        return JSONResponse(content={"status": "alive"})

    @app.get("/health/ready")
    async def readiness() -> JSONResponse:
        """Dependencies are usable. This is what a load balancer should gate traffic on."""
        health = await container.health()
        # Carried on the readiness payload too, so an operator reading a 503 can tell which
        # build produced it without a second request.
        health["release"] = container.settings.release.to_public_dict()
        return JSONResponse(
            status_code=200 if health["healthy"] else 503, content=health
        )

    @app.get("/health/version")
    async def version() -> JSONResponse:
        """Which build is answering (BLOCK-03).

        Unauthenticated and dependency-free on purpose: a deploy's smoke test has to be able to
        assert "the SHA I just shipped is the SHA now serving" before any credential exists, and
        it must keep answering while a dependency is down -- the moment you most need to know
        what is running. Serves only what ReleaseSettings.to_public_dict() allows, which is
        build provenance and no configuration.
        """
        return JSONResponse(content=container.settings.release.to_public_dict())

    @app.get(container.settings.observability.metrics_path)
    async def metrics() -> Response:
        await refresh_scrape_gauges(container)
        return Response(
            content=container.metrics.render(), media_type=container.metrics.content_type
        )

    @app.on_event("startup")
    async def on_startup() -> None:
        await container.startup()

    @app.on_event("shutdown")
    async def on_shutdown() -> None:
        # uvicorn sends lifespan shutdown only after its graceful drain
        # (--timeout-graceful-shutdown) has let in-flight requests and streams finish, so closing
        # the pools and clients here cannot pull them out from under a live turn (R2.7).
        await container.shutdown()

    return app


#: A scrape must answer even when the outbox's database is slow or down; the gauges then keep
#: their previous values for one more interval, which is the honest reading.
SCRAPE_REFRESH_TIMEOUT_S = 2.0


async def refresh_scrape_gauges(container: Any) -> None:
    """Gauges read from storage when Prometheus scrapes, rather than pushed by a worker.

    The outbox is drained by the Celery worker, a different process (on the VPS, a different
    container) from the gateway Prometheus scrapes, so a gauge the worker set would not be in
    this process's exposition. The backlog is a fact of the database, so it is read here.
    """
    outbox = getattr(container, "event_outbox", None)
    stats = getattr(outbox, "stats", None)
    if not callable(stats):
        return
    try:
        snapshot = await asyncio.wait_for(stats(), timeout=SCRAPE_REFRESH_TIMEOUT_S)
        container.metrics.set_outbox(
            pending=int(snapshot["pending"]),
            dead_lettered=int(snapshot["dead_lettered"]),
            oldest_age_s=float(snapshot["oldest_age_s"]),
        )
    except Exception:
        logger.debug("outbox gauge refresh failed", exc_info=True)


def _strip_data_url(value: str) -> str:
    """The base64 payload of ``value``, tolerating a ``data:<type>;base64,`` prefix and the
    whitespace some encoders wrap lines with."""
    if value.startswith("data:"):
        _, _, value = value.partition(",")
    return "".join(value.split())


def _parse_enum(enum_type: Any, value: str | None) -> Any:
    """Tolerant enum parsing: an unknown value falls back to the default rather than 422-ing.

    A client sending a strategy name this build does not know should get a sensible answer, not
    an error -- the field is an optimisation hint, not a semantic requirement.
    """
    if not value:
        return None
    try:
        return enum_type(value.lower())
    except (ValueError, AttributeError):
        logger.debug("ignoring unknown enum value", extra={"type": enum_type.__name__})
        return None


def mapping_or_empty(value: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(value or {})
