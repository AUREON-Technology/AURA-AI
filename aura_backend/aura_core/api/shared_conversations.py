"""Chats shared with a team (SaaS P1-F1): ``/workspaces/{id}/shared-conversations``.

Every rule lives in :class:`~aura_core.sharing.SharingService`; these routes only translate.
"""

# No `from __future__ import annotations`: request models are defined inside the register
# function, and FastAPI must resolve their annotations at decoration time (as in v3.py).
from typing import Any, Callable

__all__ = ["register_sharing_ops"]


def _summary(c: Any) -> dict:
    return {"id": c.id, "title": c.title, "shared_by": c.shared_by, "source_id": c.source_id,
            "created_at": c.created_at.isoformat()}


def register_sharing_ops(router: Any, container: Any, *, principal: Callable, Depends: Any,
                         JSONResponse: Any, Response: Any, idempotent: Any = ()) -> None:
    from pydantic import BaseModel, ConfigDict, Field

    sharing = container.sharing

    class ShareRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")
        source_id: str = Field(min_length=1, max_length=200)
        title: str = Field(default="", max_length=500)
        messages: list[dict] = Field(max_length=10_000)

    @router.post("/workspaces/{workspace_id}/shared-conversations", dependencies=idempotent)
    async def share_conversation(workspace_id: str, payload: ShareRequest, caller=Depends(principal)) -> JSONResponse:
        shared = await sharing.share(str(caller.user_id), workspace_id, payload.source_id, payload.title, payload.messages)
        return JSONResponse(status_code=201, content=_summary(shared))

    @router.get("/workspaces/{workspace_id}/shared-conversations")
    async def list_shared_conversations(workspace_id: str, caller=Depends(principal)) -> JSONResponse:
        rows = await sharing.list(str(caller.user_id), workspace_id)
        return JSONResponse(content={"conversations": [_summary(c) for c in rows]})

    @router.get("/workspaces/{workspace_id}/shared-conversations/{conversation_id}")
    async def get_shared_conversation(workspace_id: str, conversation_id: str, caller=Depends(principal)) -> JSONResponse:
        c = await sharing.get(str(caller.user_id), workspace_id, conversation_id)
        return JSONResponse(content={**_summary(c), "messages": [{"role": m.role, "text": m.text} for m in c.messages]})

    @router.delete("/workspaces/{workspace_id}/shared-conversations/{conversation_id}", dependencies=idempotent)
    async def unshare_conversation(workspace_id: str, conversation_id: str, caller=Depends(principal)) -> Any:
        await sharing.unshare(str(caller.user_id), workspace_id, conversation_id)
        return Response(status_code=204)
