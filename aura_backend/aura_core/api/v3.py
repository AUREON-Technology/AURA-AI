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
import json
from datetime import datetime, timedelta
from typing import Any, AsyncIterator, Mapping

from ..domain.errors import (
    AuraError,
    AuthenticationError,
    AuthorizationError,
    DependencyUnavailableError,
    RateLimitedError,
    ValidationError,
)
from ..domain.identifiers import CorrelationId, DocumentId, SessionId, UserId
from ..domain.llm import RoutingPolicy
from ..domain.memory import MemoryKind, MemoryQuery
from ..domain.reasoning import ReasoningStrategy
from ..kernel.kernel import TurnRequest
from ..observability.logging import get_logger
from ..ports.runtime import AuditEvent
from ..security.rate_limit import extract_client_ip
from .admin import register_admin_ops

__all__ = ["build_router", "build_app", "Principal"]

logger = get_logger("api.v3")


class Principal:
    """The authenticated caller.

    Deliberately minimal: an identity, a role, and granted capabilities. Nothing about the
    transport, no raw token, no provider-specific claims -- so a change of identity provider
    touches only the authenticator.
    """

    __slots__ = ("user_id", "email", "role", "capabilities")

    def __init__(
        self,
        *,
        user_id: UserId,
        email: str = "",
        role: str = "User",
        capabilities: tuple[str, ...] = (),
    ) -> None:
        self.user_id = user_id
        self.email = email
        self.role = role
        self.capabilities = capabilities

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
        confirmed_tools: list[str] = Field(default_factory=list)
        confirmed_call_ids: list[str] = Field(default_factory=list)
        max_output_tokens: int = Field(default=1024, ge=16, le=8192)
        temperature: float = Field(default=0.3, ge=0.0, le=2.0)
        #: D8 (Desktop IDE only): ask the paired desktop daemon to isolate this task's writes into
        #: a dedicated git worktree keyed by `session_id`, rather than the user's live checkout.
        #: Silently a no-op for any caller without a live desktop pairing.
        desktop_task_isolation: bool = False

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
            return await authenticator(request)
        except AuraError as exc:
            raise HTTPException(status_code=exc.http_status, detail=exc.to_dict()) from exc

    # Optional `Idempotency-Key` support (roadmap P1.4; see api/idempotency.py). Set before any
    # route is declared: APIRouter reads `route_class` at decoration time. A route opts in with
    # `dependencies=idempotent`; without the header the route behaves exactly as before.
    from .idempotency import build_idempotency

    router.route_class, _idempotency_guard = build_idempotency(container, principal=principal)
    idempotent = [_idempotency_guard]

    async def enforce_rate_limit(request: Request, caller: Principal, route_class: str) -> None:
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

    async def to_turn(payload: ChatRequest, caller: Principal, request: Request) -> TurnRequest:
        # Phase I: shared transcript/message normalize before the kernel (same path live voice uses).
        from ..nlp import normalize_for_turn

        message = await normalize_for_turn(payload.message, engine=getattr(container, "nlp", None))
        if not message.strip():
            raise ValidationError("message contains nothing usable after normalization")
        return TurnRequest(
            message=message,
            user_id=caller.user_id,
            session_id=SessionId(payload.session_id) if payload.session_id else None,
            agent=payload.agent,
            correlation_id=CorrelationId(
                request.headers.get("x-correlation-id") or CorrelationId.new().value
            ),
            use_knowledge=payload.use_knowledge,
            use_memory=payload.use_memory,
            use_tools=payload.use_tools,
            plan=payload.plan,
            strategy=_parse_enum(ReasoningStrategy, payload.strategy),
            routing_policy=_parse_enum(RoutingPolicy, payload.routing_policy),
            require_local=payload.require_local,
            capabilities=caller.capabilities,
            confirmed_tools=tuple(payload.confirmed_tools),
            confirmed_call_ids=tuple(payload.confirmed_call_ids),
            max_output_tokens=payload.max_output_tokens,
            temperature=payload.temperature,
            desktop_task_isolation=payload.desktop_task_isolation,
        )

    # -- chat --------------------------------------------------------------------------

    @router.post("/chat", dependencies=idempotent)
    async def chat(
        request: Request,
        payload: ChatRequest,
        caller: Principal = Depends(principal),
    ) -> JSONResponse:
        """One complete turn. Use ``/chat/stream`` for incremental output."""
        await enforce_rate_limit(request, caller, "chat")
        result = await container.kernel.run(await to_turn(payload, caller, request))

        if result.telemetry.outcome.value != "success":
            # The kernel already produced telemetry for the failure; surface the domain error's
            # status rather than a generic 500.
            status = {"blocked": 400, "budget_exceeded": 429, "rate_limited": 429}.get(
                result.telemetry.outcome.value, 503
            )
            return JSONResponse(
                status_code=status,
                content={
                    "error": result.telemetry.error_kind or "turn_failed",
                    "message": "The turn did not complete.",
                    "retryable": status in (429, 503),
                    "details": {},
                    "correlation_id": result.telemetry.correlation_id.value,
                },
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
            },
            headers={"X-Correlation-Id": result.telemetry.correlation_id.value},
        )

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
            events(),
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
        result = await container.knowledge.ingest(
            content=payload.content,
            title=payload.title,
            document_id=DocumentId(payload.document_id) if payload.document_id else None,
            owner_id=caller.user_id,
            metadata=payload.metadata,
        )
        if container.cag is not None and result.document is not None:
            await container.cag.on_document_changed(
                result.document.id, owner_id=caller.user_id
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
            title=title, owner_id=caller.user_id, segments=extraction.blocks,
        )
        if container.cag is not None and result.document is not None:
            await container.cag.on_document_changed(
                result.document.id, owner_id=caller.user_id
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
            payload.query, owner_id=caller.user_id, limit=payload.limit
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
        doc_id = DocumentId(document_id)
        removed = await container.knowledge.delete_document(
            doc_id, owner_id=caller.user_id
        )
        if container.cag is not None:
            await container.cag.on_document_changed(doc_id, owner_id=caller.user_id)
        return JSONResponse(content={"deleted": True, "chunks_removed": removed})

    @router.post("/knowledge/documents/{document_id}/cag-pin", dependencies=idempotent)
    async def pin_cag_document(
        request: Request, document_id: str, caller: Principal = Depends(principal)
    ) -> JSONResponse:
        """Pin a user-owned document into the CAG prefix (ADR-010)."""
        await enforce_rate_limit(request, caller, "ingest")
        if container.cag is None:
            raise DependencyUnavailableError(
                "CAG is disabled on this deployment",
                dependency="cag",
            )
        document = await container.cag.set_pin(
            DocumentId(document_id), owner_id=caller.user_id, pinned=True
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
        if container.cag is None:
            raise DependencyUnavailableError(
                "CAG is disabled on this deployment",
                dependency="cag",
            )
        document = await container.cag.set_pin(
            DocumentId(document_id), owner_id=caller.user_id, pinned=False
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
        prefix = await container.cag.prefix_for(caller.user_id)
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
            owner_id=caller.user_id, limit=limit, offset=offset
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
        specs = container.tools.available(capabilities=caller.capabilities)
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
        granted = frozenset(caller.capabilities)
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
                        "usable": spec.required_capabilities.issubset(granted),
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

    from .voice_live import register_voice_live

    register_voice_live(
        router,
        container,
        authenticator=authenticator,
        rate_limiter=rate_limiter,
    )

    from .host_agent import register_host_agent

    register_host_agent(router, container, authenticator=authenticator)

    return router


def build_app(container: Any, *, authenticator: Any, rate_limiter: Any = None) -> Any:
    """Standalone FastAPI app exposing v3 plus health and metrics.

    Used by the CLI, tests, and any deployment that runs the AI gateway as its own service. The
    integrated deployment mounts :func:`build_router` on the existing app instead (ADR-002).
    """
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse, Response

    app = FastAPI(
        title="AURA AI Gateway",
        version="3.0.0",
        description="Enterprise AI platform gateway. See docs/architecture/00-overview.md",
    )
    app.include_router(build_router(container, authenticator=authenticator, rate_limiter=rate_limiter))

    @app.exception_handler(AuraError)
    async def domain_error_handler(request: Request, exc: AuraError) -> JSONResponse:
        """Map domain errors to status codes.

        Every error carries its own ``http_status`` and a stable ``kind`` slug, so clients
        branch on a machine-readable value rather than parsing prose.
        """
        if exc.http_status >= 500:
            logger.error("request failed", extra={"error": exc.kind, "details": exc.details})
        return JSONResponse(status_code=exc.http_status, content=exc.to_dict())

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
        return JSONResponse(
            status_code=200 if health["healthy"] else 503, content=health
        )

    @app.get(container.settings.observability.metrics_path)
    async def metrics() -> Response:
        return Response(
            content=container.metrics.render(), media_type=container.metrics.content_type
        )

    @app.on_event("startup")
    async def on_startup() -> None:
        await container.startup()

    @app.on_event("shutdown")
    async def on_shutdown() -> None:
        await container.shutdown()

    return app


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
