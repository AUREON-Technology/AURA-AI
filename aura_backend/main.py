import asyncio
import logging
import uvicorn
import time
import uuid
from sqlalchemy import text as sa_text
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any, Optional

from config import settings
from services.ollama import ollama_service
from services.rag import rag_service
from services.automation import automation_service
from services.security import verify_token
from services.rbac import require_role
from services.drive import check_google_drive_configuration
from services.learning_pipeline import learning_pipeline
from api.v2.sync import router as sync_v2_router
from api.v2.insights import router as insights_v2_router
from api.v2.chat import router as chat_v2_router
from api.v2.auth import router as auth_v2_router
from infrastructure.db.base import get_session_factory, is_configured as is_db_configured
from infrastructure.observability import configure_logging, configure_sentry, configure_tracing

# Logging setup (Doc/V2_BLUEPRINT.md §10) — text by default, structured
# JSON when LOG_FORMAT=json (set this on any real deployment target).
configure_logging()
configure_sentry()
logger = logging.getLogger("aura_backend")

_INTERNAL_ERROR = "internal_error"
_V1_AI_GONE_DETAIL = {
    "error": "gone",
    "message": (
        "This endpoint has been retired. Use /api/v3/chat, /api/v3/knowledge, "
        "or /api/v3/memory."
    ),
}


def _v1_ai_gone() -> None:
    """Phase 4 lock: v1 chat/RAG/memory/telemetry after the Flutter cutover."""
    raise HTTPException(status_code=410, detail=_V1_AI_GONE_DETAIL)


def _http_internal_error(
    exc: Exception, *, where: str, user_email: str | None = None
) -> HTTPException:
    """Log the real exception with a correlation id; never put ``str(e)`` on the wire."""
    correlation_id = uuid.uuid4().hex
    logger.error(
        "%s failed correlation_id=%s user=%s: %s",
        where,
        correlation_id,
        user_email,
        exc,
        exc_info=True,
    )
    return HTTPException(status_code=500, detail=_INTERNAL_ERROR)

# ----------------------------------------------------
# Security Middlewares
# ----------------------------------------------------

# Rate limiting is Redis-backed so counts are shared across the Dockerfile's
# 4 uvicorn workers (and across horizontal replicas). Falls back to an
# in-process dict if Redis is unreachable. Shared connection —
# infrastructure/redis_client.py — same one used by the attestation nonce
# store and the refresh-token store (services/jwt_auth.py).
from infrastructure.redis_client import get_redis_client
from infrastructure.http_limits import (  # noqa: F401 — keep unused main importable
    PayloadSizeLimitMiddleware,
    RateLimitMiddleware,
    _rate_limit_redis,
)


app = FastAPI(title="AURA AI OS Backend", version="1.0")
configure_tracing(app)

from services.budget import InferenceBudgetExceededException
from fastapi.responses import JSONResponse

@app.exception_handler(InferenceBudgetExceededException)
async def inference_budget_exception_handler(request: Request, exc: InferenceBudgetExceededException):
    return JSONResponse(
        status_code=429,
        content={
            "success": False,
            "error": exc.message,
            "retry_after": exc.retry_after
        }
    )

@app.on_event("startup")
def startup_event():
    check_google_drive_configuration()

# Register Security Middlewares (Executed in reverse order of registration)
app.add_middleware(PayloadSizeLimitMiddleware, max_bytes=5 * 1024 * 1024)
app.add_middleware(RateLimitMiddleware, requests_per_minute=100)

# Configure CORS origins.
#
# `allow_origins=["*"]` together with `allow_credentials=True` is not the
# permissive-but-harmless combination it looks like: Starlette cannot send a
# literal `Access-Control-Allow-Origin: *` alongside credentials (browsers
# reject that pair), so it instead *reflects the caller's own Origin header*
# back with `Allow-Credentials: true`. The practical effect is the opposite of
# the intent — every origin on the internet becomes individually trusted for
# credentialed cross-origin requests.
#
# This API authenticates with Bearer tokens, not cookies, and Bearer auth does
# not need `allow_credentials`. So when a wildcard is configured we keep the
# wildcard and drop credentials (the spec-compliant reading) rather than
# silently shipping origin reflection.
origins = [o.strip() for o in settings.allowed_origins.split(",") if o.strip()]
_allow_all_origins = "*" in origins
if _allow_all_origins:
    logger.warning(
        "ALLOWED_ORIGINS contains '*' — disabling allow_credentials to avoid "
        "reflecting arbitrary origins. Set an explicit origin list in production."
    )
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=not _allow_all_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# V2 cursor-sync + insights APIs (Doc/V2_BLUEPRINT.md §7/§8). Both return a
# clean 503 on every route if DATABASE_URL isn't configured, so this is
# safe to mount unconditionally without breaking v1 deployments that don't
# run Postgres yet.
app.include_router(sync_v2_router)
app.include_router(insights_v2_router)
app.include_router(chat_v2_router)
app.include_router(auth_v2_router)

# ----------------------------------------------------
# Request / Response Schemas
# ----------------------------------------------------

class MessageSchema(BaseModel):
    role: str # user, assistant
    content: str

class ChatRequest(BaseModel):
    messages: List[MessageSchema]
    use_rag: Optional[bool] = True

class IngestRequest(BaseModel):
    doc_id: str
    content: str
    metadata: Optional[Dict[str, Any]] = None

class QueryRequest(BaseModel):
    query: str
    limit: Optional[int] = 3

class EmailAnalyzeRequest(BaseModel):
    subject: str
    sender: str
    body: str

class IssueAnalyzeRequest(BaseModel):
    title: str
    body: str

class AgentChatRequest(BaseModel):
    agent_id: str
    messages: List[MessageSchema]
    custom_instructions: Optional[Dict[str, str]] = None
    use_rag: Optional[bool] = True
    google_access_token: Optional[str] = None

class MemoryExtractRequest(BaseModel):
    messages: List[MessageSchema]

class MemorySaveRequest(BaseModel):
    fact: str

class LearningLogSchema(BaseModel):
    id: str
    prompt: str
    response: str
    rating: int
    correction: Optional[str] = None
    model: Optional[str] = None
    timestamp: str

class TelemetryLogSchema(BaseModel):
    id: str
    event_type: str
    latency_ms: float
    agent_used: str
    model_used: str
    is_offline: bool
    error_message: Optional[str] = None
    feedback_rating: Optional[int] = None
    timestamp: str

# ----------------------------------------------------
# Endpoints
# ----------------------------------------------------

# Dependency probes are cached briefly so that a load balancer or uptime
# monitor polling /health every few seconds doesn't turn into a synchronous
# round-trip against Qdrant/Postgres/Redis on every single hit.
_HEALTH_CACHE_TTL_SECONDS = 10
_health_cache: Dict[str, Any] = {"checked_at": 0.0, "payload": None}


async def _probe_dependencies() -> Dict[str, Any]:
    """Actually contacts each dependency rather than asserting a config
    string is non-empty.

    The previous version reported `qdrant_configured: bool(settings.qdrant_host)`
    and called the service healthy on that basis — which stayed green through a
    total Qdrant outage (the cloud cluster 404ing) because the *setting* was
    still populated. A check that cannot fail cannot detect anything.
    """
    deps: Dict[str, Any] = {}

    # Qdrant — get_collections() is the cheapest real round-trip available.
    if not settings.qdrant_host:
        deps["qdrant"] = {"configured": False, "reachable": False}
    else:
        try:
            await asyncio.to_thread(rag_service.client.get_collections)
            deps["qdrant"] = {"configured": True, "reachable": True}
        except Exception as e:
            deps["qdrant"] = {"configured": True, "reachable": False, "error": str(e)[:200]}

    # Postgres — SELECT 1 through the same async engine the app uses.
    if not is_db_configured():
        deps["postgres"] = {"configured": False, "reachable": False}
    else:
        try:
            async with get_session_factory()() as session:
                await session.execute(sa_text("SELECT 1"))
            deps["postgres"] = {"configured": True, "reachable": True}
        except Exception as e:
            deps["postgres"] = {"configured": True, "reachable": False, "error": str(e)[:200]}

    # Redis — degraded rather than fatal; callers fall back in-process.
    redis_client = get_redis_client()
    if redis_client is None:
        deps["redis"] = {"configured": True, "reachable": False}
    else:
        try:
            await asyncio.to_thread(redis_client.ping)
            deps["redis"] = {"configured": True, "reachable": True}
        except Exception as e:
            deps["redis"] = {"configured": True, "reachable": False, "error": str(e)[:200]}

    # Ollama — the inference backend that serves every AI request, and until now
    # the one dependency this endpoint said nothing about. It reported a
    # `gemini` entry instead, which was unprobed by construction ("verifying it
    # costs a real generation call") and, after external inference was removed,
    # simply false: there is no Gemini in this system.
    #
    # Local inference has no quota and no per-call cost, and `/api/tags` lists
    # loaded models without generating anything, so the reason not to probe
    # evaporated along with the provider. This is a real round trip.
    try:
        reachable = await ollama_service.check_health()
        deps["ollama"] = {"configured": True, "reachable": bool(reachable)}
    except Exception as e:
        deps["ollama"] = {"configured": True, "reachable": False, "error": str(e)[:200]}

    return deps


@app.get("/health")
async def health_check():
    """Liveness + dependency readiness.

    Returns 200 even when a dependency is down, by design: this endpoint is
    what nginx and the container runtime use to decide the process is alive,
    and flapping it to 5xx during a Qdrant outage would pull the entire API —
    including the many routes that never touch Qdrant — out of service.
    Degradation is reported in the body for monitoring to alert on.
    """
    now = time.time()
    if _health_cache["payload"] is not None and now - _health_cache["checked_at"] < _HEALTH_CACHE_TTL_SECONDS:
        return _health_cache["payload"]

    deps = await _probe_dependencies()
    # "degraded" if anything that is configured is not actually reachable.
    degraded = [
        name for name, state in deps.items()
        if state.get("configured") and state.get("reachable") is False
    ]
    payload = {
        "status": "degraded" if degraded else "healthy",
        "degraded": degraded,
        "dependencies": deps,
    }

    _health_cache["checked_at"] = now
    _health_cache["payload"] = payload
    return payload

@app.post("/api/chat/stream")
async def stream_chat(request: ChatRequest, user_info: dict = Depends(verify_token)):
    """Retired: use ``POST /api/v3/chat/stream``."""
    _v1_ai_gone()

@app.post("/api/rag/ingest")
async def ingest_document(request: IngestRequest, user_info: dict = Depends(verify_token)):
    """Retired: use ``POST /api/v3/knowledge/documents``."""
    _v1_ai_gone()

@app.post("/api/rag/query")
async def query_document(request: QueryRequest, user_info: dict = Depends(verify_token)):
    """Retired: use ``POST /api/v3/knowledge/search``."""
    _v1_ai_gone()

@app.post("/api/automation/analyze-email")
async def analyze_email(request: EmailAnalyzeRequest, user_info: dict = Depends(verify_token)):
    """Analyzes email content and returns structured JSON details."""
    try:
        result = automation_service.analyze_email(
            subject=request.subject,
            sender=request.sender,
            body=request.body
        )
        return result
    except InferenceBudgetExceededException as ge:
        raise ge
    except HTTPException:
        raise
    except Exception as e:
        raise _http_internal_error(
            e, where="analyze_email", user_email=user_info.get("email")
        )

@app.post("/api/automation/analyze-issue")
async def analyze_issue(request: IssueAnalyzeRequest, user_info: dict = Depends(verify_token)):
    """Analyzes GitHub issue content and returns structured checklist details."""
    try:
        result = automation_service.analyze_github_issue(
            title=request.title,
            body=request.body
        )
        return result
    except InferenceBudgetExceededException as ge:
        raise ge
    except HTTPException:
        raise
    except Exception as e:
        raise _http_internal_error(
            e, where="analyze_issue", user_email=user_info.get("email")
        )

@app.post("/api/chat/agent")
async def agent_chat(request: AgentChatRequest, user_info: dict = Depends(verify_token)):
    """Retired: use ``POST /api/v3/chat/stream``."""
    _v1_ai_gone()

@app.post("/api/memory/extract")
async def extract_memory(request: MemoryExtractRequest, user_info: dict = Depends(verify_token)):
    """Retired: v3 kernel memory runs on the turn; use ``POST /api/v3/memory`` to write."""
    _v1_ai_gone()

@app.post("/api/memory/save")
async def save_memory(request: MemorySaveRequest, user_info: dict = Depends(verify_token)):
    """Retired: use ``POST /api/v3/memory``."""
    _v1_ai_gone()

@app.get("/api/memory/list")
async def list_memories(user_info: dict = Depends(verify_token)):
    """Retired: use ``GET /api/v3/memory``."""
    _v1_ai_gone()

@app.delete("/api/memory/delete/{point_id}")
async def delete_memory(point_id: int, user_info: dict = Depends(verify_token)):
    """Retired: use ``DELETE /api/v3/memory/{memory_id}``."""
    _v1_ai_gone()

# ----------------------------------------------------
# Telemetry & Learning Pipeline Endpoints
# ----------------------------------------------------

@app.post("/api/telemetry/log")
async def log_telemetry(request: TelemetryLogSchema, user_info: dict = Depends(verify_token)):
    """Retired: turn telemetry is recorded by the v3 kernel."""
    _v1_ai_gone()

@app.post("/api/learning/log")
async def log_learning_record(request: LearningLogSchema, user_info: dict = Depends(verify_token)):
    """Logs a learning record on the backend."""
    success = learning_pipeline.log_feedback_record(request.dict())
    if not success:
        raise HTTPException(status_code=500, detail="Failed to log learning record")
    return {"status": "success"}

@app.get("/api/learning/export")
async def export_learning_dataset(format: str = "alpaca", user_info: dict = Depends(require_role("Admin"))):
    """Exports learning dataset in selected format (alpaca, chatml, openai)."""
    try:
        dataset = learning_pipeline.build_lora_dataset(format)
        return dataset
    except HTTPException:
        raise
    except Exception as e:
        raise _http_internal_error(e, where="export_learning_dataset", user_email=user_info.get("email"))

@app.post("/api/learning/build_dataset")
async def build_dataset(user_info: dict = Depends(require_role("Admin"))):
    """Processes learning logs and compiles local lora training datasets in the background."""
    try:
        from tasks import build_lora_dataset_task
        task = build_lora_dataset_task.delay(user_info["uid"])
        return {
            "status": "success", 
            "message": "LoRA dataset build queued in background.", 
            "task_id": task.id
        }
    except HTTPException:
        raise
    except Exception as e:
        raise _http_internal_error(e, where="build_dataset", user_email=user_info.get("email"))

@app.get("/api/learning/evaluate")
async def evaluate_dataset(user_info: dict = Depends(require_role("Developer"))):
    """Retrieves telemetry evaluation statistics regarding learning loops."""
    try:
        metrics = learning_pipeline.evaluate_metrics()
        return metrics
    except HTTPException:
        raise
    except Exception as e:
        raise _http_internal_error(e, where="evaluate_dataset", user_email=user_info.get("email"))

# ----------------------------------------------------
# Removed: unauthenticated Ollama proxy (SEC-01)
# ----------------------------------------------------
#
# `/api/tags`, `/api/chat`, `/api/pull`, and `/api/delete` previously forwarded to the
# configured Ollama host with **no authentication dependency** -- the only four routes in this
# module without one. Any internet caller could:
#
#   * POST /api/pull   -> make the server download arbitrary multi-gigabyte model weights
#                         (disk exhaustion, and a supply-chain vector: weights are executable
#                         artefacts fetched from a third-party registry),
#   * POST /api/chat   -> run unmetered inference on the operator's hardware,
#   * POST /api/delete -> delete the deployed model and take offline inference down.
#
# The only mitigation was a 100 req/min per-IP rate limit, which permits 100 model pulls a
# minute. See docs/analysis/06-security-report.md SEC-01.
#
# These were deleted rather than deprecated: a deprecation window for an open door is not a
# trade-off worth making. Their replacements:
#
#   inference       -> POST /api/v3/chat (authenticated, routed, budgeted, guarded)
#   model listing   -> GET  /api/v3/admin/status
#   pull / delete   -> admin-only, allowlisted, and audited via the Ollama adapter's
#                      model-lifecycle methods (aura_core/llm/providers/ollama.py)
#
# The Flutter client's local-model management moves to those endpoints; see
# docs/guides/migration-guide.md.

# ----------------------------------------------------
# Server Start
# ----------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(
        "main:app", 
        host="0.0.0.0", 
        port=settings.port, 
        reload=True
    )
