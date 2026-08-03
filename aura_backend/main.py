import logging
import uvicorn
import time
import json
from fastapi import FastAPI, Request, Response, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any, Optional

from config import settings
from services.ai import ai_service
from services.rag import rag_service
from services.automation import automation_service
from services.agent import agent_orchestrator
from services.memory import memory_service
from services.security import verify_token
from services.rbac import require_role
from services.drive import check_google_drive_configuration
from services.learning_pipeline import learning_pipeline
from api.v2.sync import router as sync_v2_router
from api.v2.insights import router as insights_v2_router
from api.v2.chat import router as chat_v2_router
from api.v2.auth import router as auth_v2_router
from infrastructure.observability import configure_logging, configure_sentry, configure_tracing

# Logging setup (Doc/V2_BLUEPRINT.md §10) — text by default, structured
# JSON when LOG_FORMAT=json (set this on any real deployment target).
configure_logging()
configure_sentry()
logger = logging.getLogger("aura_backend")

# ----------------------------------------------------
# Security Middlewares
# ----------------------------------------------------

# Rate limiting is Redis-backed so counts are shared across the Dockerfile's
# 4 uvicorn workers (and across horizontal replicas). Falls back to an
# in-process dict if Redis is unreachable. Shared connection —
# infrastructure/redis_client.py — same one used by the attestation nonce
# store and the refresh-token store (services/jwt_auth.py).
from infrastructure.redis_client import get_redis_client
_rate_limit_redis = get_redis_client()


class RateLimitMiddleware(BaseHTTPMiddleware):
    # In-process fallback history; also what test suites clear/reset between tests.
    request_history: Dict[str, List[float]] = {}

    def __init__(self, app, requests_per_minute: int = 100):
        super().__init__(app)
        self.requests_per_minute = requests_per_minute

    def _is_rate_limited_redis(self, client_ip: str) -> bool:
        window = int(time.time() // 60)
        key = f"ratelimit:{client_ip}:{window}"
        count = _rate_limit_redis.incr(key)
        if count == 1:
            _rate_limit_redis.expire(key, 60)
        return count > self.requests_per_minute

    def _is_rate_limited_local(self, client_ip: str) -> bool:
        now = time.time()
        if client_ip not in RateLimitMiddleware.request_history:
            RateLimitMiddleware.request_history[client_ip] = []

        # Keep only timestamps within the last 60 seconds
        RateLimitMiddleware.request_history[client_ip] = [
            ts for ts in RateLimitMiddleware.request_history[client_ip] if now - ts < 60
        ]

        if len(RateLimitMiddleware.request_history[client_ip]) >= self.requests_per_minute:
            return True

        RateLimitMiddleware.request_history[client_ip].append(now)
        return False

    async def dispatch(self, request: Request, call_next):
        # Exempt health check from rate limiting
        if request.url.path == "/health":
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"

        if _rate_limit_redis is not None:
            try:
                limited = self._is_rate_limited_redis(client_ip)
            except Exception as e:
                logger.warning(f"Redis rate limit check failed, falling back to in-process: {e}")
                limited = self._is_rate_limited_local(client_ip)
        else:
            limited = self._is_rate_limited_local(client_ip)

        if limited:
            return Response(
                content="Rate limit exceeded. Maximum 100 requests per minute.",
                status_code=429
            )

        return await call_next(request)


class PayloadSizeLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_bytes: int = 5 * 1024 * 1024): # 5MB limit
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH"):
            content_length = request.headers.get("content-length")
            if content_length:
                try:
                    size = int(content_length)
                    if size > self.max_bytes:
                        return Response(
                            content="Payload Too Large. Maximum size is 5MB.",
                            status_code=413
                        )
                except ValueError:
                    return Response(
                        content="Invalid Content-Length header.",
                        status_code=400
                    )
        return await call_next(request)


app = FastAPI(title="AURA AI OS Backend", version="1.0")
configure_tracing(app)

from services.budget import GeminiBudgetExceededException
from fastapi.responses import JSONResponse

@app.exception_handler(GeminiBudgetExceededException)
async def gemini_budget_exception_handler(request: Request, exc: GeminiBudgetExceededException):
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

# Configure CORS origins
origins = [o.strip() for o in settings.allowed_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
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

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "gemini_configured": bool(settings.gemini_api_key),
        "qdrant_configured": bool(settings.qdrant_host)
    }

@app.post("/api/chat/stream")
async def stream_chat(request: ChatRequest, user_info: dict = Depends(verify_token)):
    """Streams responses from Gemini 1.5 Flash."""
    formatted_messages = [{"role": m.role, "content": m.content} for m in request.messages]
    
    if not formatted_messages:
        raise HTTPException(status_code=400, detail="Messages list cannot be empty")

    async def event_generator():
        try:
            async for chunk in ai_service.generate_chat_response_stream(
                messages=formatted_messages, 
                use_rag=request.use_rag,
                user_id=user_info["uid"]
            ):
                yield chunk
        except Exception as e:
            logger.error(f"Error in stream_chat event generation for user {user_info.get('email')}: {e}")
            yield f"\n[Internal Server Error during stream: {e}]"

    return StreamingResponse(event_generator(), media_type="text/plain")

@app.post("/api/rag/ingest")
async def ingest_document(request: IngestRequest, user_info: dict = Depends(verify_token)):
    """Ingests text content into Qdrant Cloud via background task."""
    metadata = request.metadata or {}
    metadata["user_id"] = user_info["uid"]
    try:
        from tasks import ingest_document_task
        task = ingest_document_task.delay(request.doc_id, request.content, metadata)
        return {
            "status": "success", 
            "message": f"Document {request.doc_id} ingestion queued in background.", 
            "task_id": task.id
        }
    except Exception as e:
        logger.error(f"Error queuing ingest_document for user {user_info.get('email')}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/rag/query")
async def query_document(request: QueryRequest, user_info: dict = Depends(verify_token)):
    """Queries Qdrant vector store directly."""
    try:
        results = rag_service.query_knowledge(
            query=request.query, 
            limit=request.limit, 
            user_id=user_info["uid"]
        )
        return {"results": results}
    except Exception as e:
        logger.error(f"Error in query_document for user {user_info.get('email')}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

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
    except GeminiBudgetExceededException as ge:
        raise ge
    except Exception as e:
        logger.error(f"Error in analyze_email for user {user_info.get('email')}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/automation/analyze-issue")
async def analyze_issue(request: IssueAnalyzeRequest, user_info: dict = Depends(verify_token)):
    """Analyzes GitHub issue content and returns structured checklist details."""
    try:
        result = automation_service.analyze_github_issue(
            title=request.title,
            body=request.body
        )
        return result
    except GeminiBudgetExceededException as ge:
        raise ge
    except Exception as e:
        logger.error(f"Error in analyze_issue for user {user_info.get('email')}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/chat/agent")
async def agent_chat(request: AgentChatRequest, user_info: dict = Depends(verify_token)):
    """Streams responses from a classified/selected specialized agent persona."""
    formatted_messages = [{"role": m.role, "content": m.content} for m in request.messages]
    
    if not formatted_messages:
        raise HTTPException(status_code=400, detail="Messages list cannot be empty")

    async def event_generator():
        try:
            async for chunk in agent_orchestrator.execute_agent_stream(
                agent_id=request.agent_id,
                messages=formatted_messages,
                custom_instructions=request.custom_instructions,
                use_rag=request.use_rag,
                user_id=user_info["uid"],
                google_access_token=request.google_access_token
            ):
                yield chunk
        except Exception as e:
            logger.error(f"Error in agent_chat stream generation for user {user_info.get('email')}: {e}")
            yield f"\n[Internal Server Agent Error during stream: {e}]"

    return StreamingResponse(event_generator(), media_type="text/plain")

@app.post("/api/memory/extract")
async def extract_memory(request: MemoryExtractRequest, user_info: dict = Depends(verify_token)):
    """Extracts long-term user facts from recent messages and saves them."""
    try:
        formatted_messages = [{"role": m.role, "content": m.content} for m in request.messages]
        facts = memory_service.extract_memories(formatted_messages)
        saved_facts = []
        for fact in facts:
            if memory_service.save_memory(fact, user_id=user_info["uid"]):
                saved_facts.append(fact)
        return {"status": "success", "extracted_facts": saved_facts}
    except Exception as e:
        logger.error(f"Error in extract_memory for user {user_info.get('email')}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/memory/save")
async def save_memory(request: MemorySaveRequest, user_info: dict = Depends(verify_token)):
    """Manually saves a user memory fact."""
    try:
        success = memory_service.save_memory(request.fact, user_id=user_info["uid"])
        if not success:
            raise HTTPException(status_code=500, detail="Failed to save memory point")
        return {"status": "success"}
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error in save_memory for user {user_info.get('email')}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/memory/list")
async def list_memories(user_info: dict = Depends(verify_token)):
    """Lists all user memories stored in Qdrant Cloud."""
    try:
        return memory_service.list_all_memories(user_id=user_info["uid"])
    except Exception as e:
        logger.error(f"Error in list_memories for user {user_info.get('email')}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/memory/delete/{point_id}")
async def delete_memory(point_id: int, user_info: dict = Depends(verify_token)):
    """Deletes a user memory by Qdrant point ID."""
    try:
        success = memory_service.delete_memory(point_id)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to delete memory point")
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error in delete_memory for user {user_info.get('email')}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ----------------------------------------------------
# Telemetry & Learning Pipeline Endpoints
# ----------------------------------------------------

@app.post("/api/telemetry/log")
async def log_telemetry(request: TelemetryLogSchema, user_info: dict = Depends(verify_token)):
    """Logs telemetry metadata in append-only JSONL format."""
    try:
        telemetry_file = "telemetry_logs.jsonl"
        with open(telemetry_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(request.dict(), ensure_ascii=False) + "\n")
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Failed to save telemetry: {e}")
        raise HTTPException(status_code=500, detail=str(e))

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
    except Exception as e:
        logger.error(f"Export dataset failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

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
    except Exception as e:
        logger.error(f"Build dataset failed to queue: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/learning/evaluate")
async def evaluate_dataset(user_info: dict = Depends(require_role("Developer"))):
    """Retrieves telemetry evaluation statistics regarding learning loops."""
    try:
        metrics = learning_pipeline.evaluate_metrics()
        return metrics
    except Exception as e:
        logger.error(f"Evaluate learning logs failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

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
