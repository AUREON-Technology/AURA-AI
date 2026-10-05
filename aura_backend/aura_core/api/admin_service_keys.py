"""Super Admin routes for service keys (AR-4).

Issuing a key creates a credential another system will hold, so every route here needs the
Super Admin role. The token is in the response to ``POST`` exactly once; nothing -- not the list,
not the audit trail, not the store -- can return it again, and the digest is never exposed.
"""

# No `from __future__ import annotations`: the request model is defined inside the register
# function, and FastAPI must resolve its annotation at decoration time (as in v3.py).
from typing import Any, Callable

from ..domain.errors import NotFoundError

__all__ = ["register_service_key_ops"]


def _payload(key: Any) -> dict[str, Any]:
    return {
        "key_id": key.key_id,
        "name": key.name,
        "principal": key.principal_id,
        "routes": sorted(key.routes),
        "rate_per_minute": key.rate_per_minute,
        "created_at": key.created_at.isoformat(),
        "created_by": key.created_by,
        "revoked_at": key.revoked_at.isoformat() if key.revoked_at else None,
        "revoked_by": key.revoked_by,
    }


def register_service_key_ops(
    router: Any,
    container: Any,
    *,
    principal: Callable,
    require_super_admin: Callable,
    Depends: Any,
    JSONResponse: Any,
) -> None:
    from pydantic import BaseModel, ConfigDict, Field

    class IssueRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")

        name: str = Field(min_length=2, max_length=48)
        routes: list[str] | None = Field(default=None, min_length=1, max_length=8)
        rate_per_minute: int = Field(default=10, ge=1, le=600)

    def actor(caller: Any) -> str:
        return caller.email or caller.user_id.value

    # Not idempotency-keyed, like connector registration: the stored replay would hold the token.
    @router.post("/admin/service-keys")
    async def issue_service_key(payload: IssueRequest, caller=Depends(principal)) -> JSONResponse:
        require_super_admin(caller)
        key, token = await container.service_keys.issue(
            name=payload.name, by=actor(caller), routes=payload.routes, rate_per_minute=payload.rate_per_minute,
        )
        return JSONResponse(
            status_code=201,
            content={"key": _payload(key), "token": token,
                     "note": "Store this token now. It is not stored by AURA and cannot be shown again."},
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/admin/service-keys")
    async def list_service_keys(caller=Depends(principal)) -> JSONResponse:
        require_super_admin(caller)
        return JSONResponse(content={"keys": [_payload(k) for k in await container.service_keys.list()]})

    @router.post("/admin/service-keys/{key_id}/revoke")
    async def revoke_service_key(key_id: str, caller=Depends(principal)) -> JSONResponse:
        require_super_admin(caller)
        key = await container.service_keys.revoke(key_id, by=actor(caller))
        if key is None:
            raise NotFoundError(f"no service key {key_id[:32]!r}")
        return JSONResponse(content={"key": _payload(key)})
