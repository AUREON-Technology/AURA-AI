"""SuperAdmin: set a workspace's plan by hand (SaaS P1-C, spec 5.4).

Until billing (Plan D) sets plans from subscriptions, this is how a customer gets Pro or Team:
the operator records it, the change is audited (``plan.set``), and it takes effect on the
workspace's next request. Billing will overwrite a ``manual`` entitlement with a ``subscription``
one.
"""

# No `from __future__ import annotations`: the request model is defined inside the register
# function, and FastAPI must resolve its annotation at decoration time (as in v3.py).
from typing import Any, Callable

__all__ = ["register_plan_ops"]


def register_plan_ops(
    router: Any, container: Any, *, principal: Callable, require_super_admin: Callable,
    idempotent: list, Depends: Any, JSONResponse: Any,
) -> None:
    from pydantic import BaseModel, ConfigDict, Field

    class SetPlanRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")

        plan: str = Field(min_length=1, max_length=40)
        seats: int = Field(ge=1, le=10_000)

    @router.put("/admin/workspaces/{workspace_id}/plan", dependencies=idempotent)
    async def set_workspace_plan(workspace_id: str, payload: SetPlanRequest, caller=Depends(principal)) -> JSONResponse:
        require_super_admin(caller)
        entitlement = await container.plans.set_plan(
            caller.email or str(caller.user_id), workspace_id, payload.plan, payload.seats
        )
        return JSONResponse(content={
            "workspace_id": workspace_id,
            "entitlement": {"plan": entitlement.plan_id, "seats": entitlement.seats, "source": entitlement.source},
            "usage": await container.plans.usage(workspace_id),
        })
