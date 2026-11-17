"""Customer workspace routes: teams, members, roles and invites (SaaS phase 1, ADR-034).

Every rule lives in :class:`~aura_core.tenancy.TenancyService`; these routes only translate. The
service raises domain errors (403 not a member / role too low, 409 conflict, 422 bad input) and
the app's ``AuraError`` handler maps them, so a stranger gets the same 403 for a real workspace
and an invented one.

* People only: a service key is refused by the gateway's route gate before any handler runs.
* An invite's token is in the ``POST`` response exactly once (``Cache-Control: no-store``); no
  route ever returns a digest.
* Invite creation and acceptance share an always-on per-person budget, independent of the
  general rate-limit switch, so the accept route cannot be used to probe invite ids at volume.
"""

# No `from __future__ import annotations`: request models are defined inside the register
# function, and FastAPI must resolve their annotations at decoration time (as in v3.py).
from typing import Any, Callable

from ..domain.errors import RateLimitedError, ValidationError
from ..domain.tenancy import Invite, Membership, Workspace, WorkspaceRole

__all__ = ["register_workspace_ops"]

#: Invite creations plus acceptances per person per minute.
INVITE_BUDGET_PER_MINUTE = 20


def _workspace(ws: Workspace, role: WorkspaceRole) -> dict[str, Any]:
    return {"id": ws.id, "kind": ws.kind.value, "name": ws.name, "role": role.value}


def _member(m: Membership) -> dict[str, Any]:
    return {"user_id": m.user_id, "role": m.role.value, "joined_at": m.joined_at.isoformat()}


def _invite(i: Invite) -> dict[str, Any]:
    return {
        "invite_id": i.invite_id, "email": i.email, "role": i.role.value,
        "created_by": i.created_by, "created_at": i.created_at.isoformat(),
        "expires_at": i.expires_at.isoformat(),
    }


def _role(value: str) -> WorkspaceRole:
    try:
        return WorkspaceRole(value)
    except ValueError as exc:
        raise ValidationError("role is 'admin' or 'member'") from exc


def register_workspace_ops(
    router: Any,
    container: Any,
    *,
    principal: Callable,
    idempotent: list,
    Depends: Any,
    JSONResponse: Any,
    Response: Any,
) -> None:
    from pydantic import BaseModel, ConfigDict, Field

    from ..security.rate_limit import SlidingWindowRateLimiter

    tenancy = container.tenancy
    limiter = SlidingWindowRateLimiter(container.cache_backend, policies=(), metrics=getattr(container, "metrics", None))

    class CreateRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")
        name: str = Field(max_length=200)

    class InviteRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")
        email: str = Field(max_length=320)
        role: str = Field(default="member", max_length=16)

    class RoleRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")
        role: str = Field(max_length=16)

    class TransferRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")
        user_id: str = Field(min_length=1, max_length=256)

    class AcceptRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")
        token: str = Field(max_length=200)

    def person(caller: Any) -> str:
        return str(caller.user_id)

    async def invite_budget(caller: Any) -> None:
        decision = await limiter.check_limit(
            key=person(caller), scope="workspace_invite", limit=INVITE_BUDGET_PER_MINUTE
        )
        if not decision.allowed:
            raise RateLimitedError("too many invite requests", retry_after=decision.retry_after_s)

    @router.get("/workspaces")
    async def list_workspaces(caller=Depends(principal)) -> JSONResponse:
        pairs = await tenancy.list_for(person(caller))
        return JSONResponse(content={"workspaces": [_workspace(ws, m.role) for ws, m in pairs]})

    @router.post("/workspaces", dependencies=idempotent)
    async def create_workspace(payload: CreateRequest, caller=Depends(principal)) -> JSONResponse:
        ws = await tenancy.create_team(person(caller), payload.name)
        return JSONResponse(status_code=201, content=_workspace(ws, WorkspaceRole.OWNER))

    @router.delete("/workspaces/{workspace_id}", dependencies=idempotent)
    async def delete_workspace(workspace_id: str, caller=Depends(principal)) -> Response:
        await tenancy.delete_team(person(caller), workspace_id)
        return Response(status_code=204)

    @router.get("/workspaces/{workspace_id}/members")
    async def list_members(workspace_id: str, caller=Depends(principal)) -> JSONResponse:
        members = await tenancy.list_members(person(caller), workspace_id)
        return JSONResponse(content={"members": [_member(m) for m in members]})

    @router.patch("/workspaces/{workspace_id}/members/{user_id}", dependencies=idempotent)
    async def change_role(workspace_id: str, user_id: str, payload: RoleRequest, caller=Depends(principal)) -> Response:
        await tenancy.change_role(person(caller), workspace_id, user_id, _role(payload.role))
        return Response(status_code=204)

    @router.delete("/workspaces/{workspace_id}/members/{user_id}", dependencies=idempotent)
    async def remove_member(workspace_id: str, user_id: str, caller=Depends(principal)) -> Response:
        await tenancy.remove(person(caller), workspace_id, user_id)
        return Response(status_code=204)

    @router.post("/workspaces/{workspace_id}/transfer", dependencies=idempotent)
    async def transfer(workspace_id: str, payload: TransferRequest, caller=Depends(principal)) -> Response:
        await tenancy.transfer(person(caller), workspace_id, payload.user_id)
        return Response(status_code=204)

    # Not idempotency-keyed: a stored replay would hold the token (as with service keys).
    @router.post("/workspaces/{workspace_id}/invites")
    async def create_invite(workspace_id: str, payload: InviteRequest, caller=Depends(principal)) -> JSONResponse:
        await invite_budget(caller)
        invite, token = await tenancy.invite(person(caller), workspace_id, payload.email, _role(payload.role))
        return JSONResponse(
            status_code=201,
            content={"invite": _invite(invite), "token": token,
                     "note": "Share this token with the invitee now. It cannot be shown again."},
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/workspaces/{workspace_id}/invites")
    async def list_invites(workspace_id: str, caller=Depends(principal)) -> JSONResponse:
        invites = await tenancy.list_invites(person(caller), workspace_id)
        return JSONResponse(content={"invites": [_invite(i) for i in invites]})

    @router.delete("/workspaces/{workspace_id}/invites/{invite_id}", dependencies=idempotent)
    async def revoke_invite(workspace_id: str, invite_id: str, caller=Depends(principal)) -> Response:
        await tenancy.revoke_invite(person(caller), workspace_id, invite_id)
        return Response(status_code=204)

    @router.post("/workspaces/invites/accept")
    async def accept_invite(payload: AcceptRequest, caller=Depends(principal)) -> JSONResponse:
        await invite_budget(caller)
        member = await tenancy.accept(person(caller), payload.token)
        ws, _ = await tenancy.resolve(person(caller), member.workspace_id)
        return JSONResponse(content={"workspace": _workspace(ws, member.role), "role": member.role.value})
