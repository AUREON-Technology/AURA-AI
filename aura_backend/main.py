import logging
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Dict, Any, Optional

from config import settings
from services.ai import ai_service
from services.rag import rag_service
from services.automation import automation_service

# Logging Setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("aura_backend")

app = FastAPI(title="AURA AI OS Backend", version="1.0")

# CORS Middleware for client connectivity
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
