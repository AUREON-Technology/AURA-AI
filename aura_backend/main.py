import logging
import uvicorn
import time
from fastapi import FastAPI, Request, Response, HTTPException
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

# Logging Setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("aura_backend")

# ----------------------------------------------------
# Security Middlewares
# ----------------------------------------------------

class RateLimitMiddleware(BaseHTTPMiddleware):
    # Class-level history to allow test suites to clear/reset rate limiting state
    request_history: Dict[str, List[float]] = {}

    def __init__(self, app, requests_per_minute: int = 100):
        super().__init__(app)
        self.requests_per_minute = requests_per_minute

    async def dispatch(self, request: Request, call_next):
        # Exempt health check from rate limiting
        if request.url.path == "/health":
            return await call_next(request)
        
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        
        if client_ip not in RateLimitMiddleware.request_history:
            RateLimitMiddleware.request_history[client_ip] = []
            
        # Keep only timestamps within the last 60 seconds
        RateLimitMiddleware.request_history[client_ip] = [
            ts for ts in RateLimitMiddleware.request_history[client_ip] if now - ts < 60
        ]
        
        if len(RateLimitMiddleware.request_history[client_ip]) >= self.requests_per_minute:
            return Response(
                content="Rate limit exceeded. Maximum 100 requests per minute.",
                status_code=429
            )
            
        RateLimitMiddleware.request_history[client_ip].append(now)
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

class MemoryExtractRequest(BaseModel):
    messages: List[MessageSchema]

class MemorySaveRequest(BaseModel):
    fact: str

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
async def stream_chat(request: ChatRequest):
    """Streams responses from Gemini 1.5 Flash."""
    formatted_messages = [{"role": m.role, "content": m.content} for m in request.messages]
    
    if not formatted_messages:
        raise HTTPException(status_code=400, detail="Messages list cannot be empty")

    async def event_generator():
        async for chunk in ai_service.generate_chat_response_stream(
            messages=formatted_messages, 
            use_rag=request.use_rag
        ):
            yield chunk

    return StreamingResponse(event_generator(), media_type="text/plain")

@app.post("/api/rag/ingest")
async def ingest_document(request: IngestRequest):
    """Ingests text content into Qdrant Cloud."""
    success = rag_service.ingest_document(
        doc_id=request.doc_id,
        content=request.content,
        metadata=request.metadata or {}
    )
    if not success:
        raise HTTPException(status_code=500, detail="Failed to ingest document to Qdrant")
    return {"status": "success", "message": f"Document {request.doc_id} ingested."}

@app.post("/api/rag/query")
async def query_document(request: QueryRequest):
    """Queries Qdrant vector store directly."""
    results = rag_service.query_knowledge(query=request.query, limit=request.limit)
    return {"results": results}

@app.post("/api/automation/analyze-email")
async def analyze_email(request: EmailAnalyzeRequest):
    """Analyzes email content and returns structured JSON details."""
    result = automation_service.analyze_email(
        subject=request.subject,
        sender=request.sender,
        body=request.body
    )
    return result

@app.post("/api/automation/analyze-issue")
async def analyze_issue(request: IssueAnalyzeRequest):
    """Analyzes GitHub issue content and returns structured checklist details."""
    result = automation_service.analyze_github_issue(
        title=request.title,
        body=request.body
    )
    return result

@app.post("/api/chat/agent")
async def agent_chat(request: AgentChatRequest):
    """Streams responses from a classified/selected specialized agent persona."""
    formatted_messages = [{"role": m.role, "content": m.content} for m in request.messages]
    
    if not formatted_messages:
        raise HTTPException(status_code=400, detail="Messages list cannot be empty")

    async def event_generator():
        async for chunk in agent_orchestrator.execute_agent_stream(
            agent_id=request.agent_id,
            messages=formatted_messages,
            custom_instructions=request.custom_instructions,
            use_rag=request.use_rag
        ):
            yield chunk

    return StreamingResponse(event_generator(), media_type="text/plain")

@app.post("/api/memory/extract")
async def extract_memory(request: MemoryExtractRequest):
    """Extracts long-term user facts from recent messages and saves them."""
    formatted_messages = [{"role": m.role, "content": m.content} for m in request.messages]
    facts = memory_service.extract_memories(formatted_messages)
    saved_facts = []
    for fact in facts:
        if memory_service.save_memory(fact):
            saved_facts.append(fact)
    return {"status": "success", "extracted_facts": saved_facts}

@app.post("/api/memory/save")
async def save_memory(request: MemorySaveRequest):
    """Manually saves a user memory fact."""
    success = memory_service.save_memory(request.fact)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to save memory point")
    return {"status": "success"}

@app.get("/api/memory/list")
async def list_memories():
    """Lists all user memories stored in Qdrant Cloud."""
    return memory_service.list_all_memories()

@app.delete("/api/memory/delete/{point_id}")
async def delete_memory(point_id: int):
    """Deletes a user memory by Qdrant point ID."""
    success = memory_service.delete_memory(point_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete memory point")
    return {"status": "success"}

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
