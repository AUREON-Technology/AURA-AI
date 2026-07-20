"""SSE chat protocol (Doc/V2_BLUEPRINT.md §7) — replaces v1's
`/api/chat/agent` raw `text/plain` stream, where tool-call proposals are
embedded as a trailing `[ACTIONS]<json>` suffix the client has to
string-split out of the token stream (fragile: a chunk boundary can split
the marker itself, and any parse failure silently drops the actions with
no client-visible error). Emits typed SSE frames instead:

  event: token       {"text": "..."}       — visible response text
  event: tool_calls  {"actions": [...]}     — same action-proposal shape
                                              v1 already used; the client's
                                              existing approve/reject UI
                                              (pendingActionsProvider in
                                              chat_repository.dart) is
                                              unchanged, only the transport is
  event: done         {}
  event: error        {"message": "..."}

Wraps the same `agent_orchestrator.execute_agent_stream` pipeline v1 uses
(intent routing, sentiment, memory/RAG injection, Gemini retry, offline
fallback) rather than duplicating any of that logic — only the framing of
the output changes. The marker-boundary buffering itself lives in
services/sse_marker_splitter.py, unit tested in test_sse_marker_splitter.py.
"""
import json
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from services.agent import agent_orchestrator
from services.jwt_auth import verify_access_token
from services.sse_marker_splitter import MarkerStreamSplitter

router = APIRouter(prefix="/api/v2", tags=["chat (v2)"])


class MessageSchemaV2(BaseModel):
    role: str
    content: str


class AgentChatRequestV2(BaseModel):
    agent_id: str
    messages: List[MessageSchemaV2]
    custom_instructions: Optional[Dict[str, str]] = None
    use_rag: Optional[bool] = True
    google_access_token: Optional[str] = None


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.post("/chat")
async def agent_chat_v2(request: AgentChatRequestV2, user_info: dict = Depends(verify_access_token)):
    formatted_messages = [{"role": m.role, "content": m.content} for m in request.messages]
    if not formatted_messages:
        raise HTTPException(status_code=400, detail="Messages list cannot be empty")

    async def event_generator():
        splitter = MarkerStreamSplitter()
        try:
            async for chunk in agent_orchestrator.execute_agent_stream(
                agent_id=request.agent_id,
                messages=formatted_messages,
                custom_instructions=request.custom_instructions,
                use_rag=request.use_rag,
                user_id=user_info["uid"],
                google_access_token=request.google_access_token,
            ):
                for event, data in splitter.feed(chunk):
                    yield _sse(event, data)

            for event, data in splitter.finish():
                yield _sse(event, data)

            yield _sse("done", {})
        except Exception as e:
            yield _sse("error", {"message": str(e)})

    return StreamingResponse(event_generator(), media_type="text/event-stream")
