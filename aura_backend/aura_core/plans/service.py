"""What a workspace's plan allows, and what it has used (SaaS P1-C, spec §5.3).

The gateway asks this service before work that a plan limits:

* a chat turn reserves one ``messages`` unit (:meth:`admit_message`) and gives it back if the turn
  fails (:meth:`release`), so a failed turn never uses up the month;
* an Engineer turn needs a plan with ``engineer`` (:meth:`require_engineer`);
* an ingest reserves its bytes against ``storage_bytes`` (:meth:`admit_storage`), a delete gives
  them back (:meth:`free_storage`);
* a team's seats come from :meth:`seat_limit` (``TenancyService`` asks it at invite and accept).

Operators (platform role Developer and above) and service keys are unmetered: they are not
customers, and service keys have their own budget (ADR-029).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from ..domain.errors import ValidationError
from ..domain.plans import (
    DEFAULT_PLAN,
    STORAGE_PERIOD,
    Entitlement,
    PlanLimitError,
    PlanLimits,
    monthly_message_limit,
    period_for,
    period_resets_at,
)
from ..domain.tenancy import WorkspaceKind
from ..ports.runtime import AuditEvent

__all__ = ["PlanService", "Reservation"]

#: Platform roles that are never metered: the people who run AURA, not its customers. Mirrors
#: ``services.rbac`` at and above ``CONSOLE_MIN_ROLE`` (which aura_core may not import).
UNMETERED_ROLES = frozenset({"Developer", "Admin", "SuperAdmin"})
MESSAGES = "messages"
STORAGE = "storage_bytes"


@dataclass
class Reservation:
    workspace_id: str
    metric: str
    period: str
    amount: int
    released: bool = field(default=False)


@dataclass
class PlanService:
    entitlements: Any
    meter: Any
    tenancy: Any
    clock: Any
    audit: Any
    catalogue: Mapping[str, PlanLimits]

    async def entitlement(self, workspace_id: str) -> tuple[PlanLimits, int]:
        row = await self.entitlements.get(workspace_id)
        if row is None or row.plan_id not in self.catalogue:
            return self.catalogue[DEFAULT_PLAN], 1
        return self.catalogue[row.plan_id], row.seats

    async def plan_id(self, workspace_id: str) -> str:
        return (await self.entitlement(workspace_id))[0].id

    async def seat_limit(self, workspace_id: str) -> int:
        plan, seats = await self.entitlement(workspace_id)
        if plan.min_seats == plan.max_seats or plan.id == DEFAULT_PLAN:
            return plan.max_seats
        return min(max(seats, plan.min_seats), plan.max_seats)

    async def metered_workspace(self, caller: Any) -> str | None:
        if getattr(caller, "is_service", False) or getattr(caller, "role", "") in UNMETERED_ROLES:
            return None
        workspace = getattr(caller, "workspace", None)
        if workspace is not None:
            return workspace.workspace_id
        return (await self.tenancy.ensure_personal(str(caller.user_id))).id

    async def admit_message(self, workspace_id: str) -> Reservation:
        plan, seats = await self.entitlement(workspace_id)
        period = period_for(self.clock.now())
        allowed = monthly_message_limit(plan, seats)
        admitted, used = await self.meter.try_consume(workspace_id, MESSAGES, period, 1, allowed)
        if not admitted:
            raise PlanLimitError(
                "this workspace has used its messages for the month", limit=MESSAGES, plan=plan.id,
                used=used, allowed=allowed, resets_at=period_resets_at(period),
            )
        return Reservation(workspace_id, MESSAGES, period, 1)

    async def release(self, reservation: Reservation) -> None:
        if reservation.released:
            return
        reservation.released = True
        await self.meter.release(reservation.workspace_id, reservation.metric, reservation.period, reservation.amount)

    async def engineer_entitled(self, subject: str) -> bool:
        """Whether ``subject`` belongs to any workspace whose plan includes the Engineer (SaaS
        P1-E): the gate on a customer's desktop sign-in, pairing, refresh and daemon socket."""
        return bool(await self.engineer_workspaces(subject))

    async def engineer_workspaces(self, subject: str) -> list:
        """``subject``'s workspaces, with their memberships, whose plan includes the Engineer.

        Sign-in and pairing accept any of them, but a turn uses the *active* workspace; the app
        lists these so a team member whose own workspace has no Engineer can switch to one that
        does, instead of finding it locked with no way forward."""
        found = []
        for workspace, membership in await self.tenancy.list_for(subject):
            plan, _seats = await self.entitlement(workspace.id)
            if plan.engineer:
                found.append((workspace, membership))
        return found

    async def require_engineer(self, workspace_id: str) -> None:
        plan, _ = await self.entitlement(workspace_id)
        if not plan.engineer:
            raise PlanLimitError(
                "the Engineer agent is not part of this workspace's plan", limit="engineer",
                plan=plan.id, used=0, allowed=0,
            )

    async def admit_storage(self, workspace_id: str, nbytes: int) -> None:
        if nbytes <= 0:
            return
        plan, _ = await self.entitlement(workspace_id)
        admitted, used = await self.meter.try_consume(workspace_id, STORAGE, STORAGE_PERIOD, nbytes, plan.storage_bytes)
        if not admitted:
            raise PlanLimitError(
                "this workspace's knowledge storage is full", limit="storage", plan=plan.id,
                used=used, allowed=plan.storage_bytes,
            )

    async def free_storage(self, workspace_id: str, nbytes: int) -> None:
        if nbytes > 0:
            await self.meter.release(workspace_id, STORAGE, STORAGE_PERIOD, nbytes)

    async def usage(self, workspace_id: str) -> dict:
        plan, seats = await self.entitlement(workspace_id)
        period = period_for(self.clock.now())
        return {
            "plan": plan.id,
            "seats": seats,
            "period": period,
            "resets_at": period_resets_at(period).isoformat(),
            "messages_used": await self.meter.get(workspace_id, MESSAGES, period),
            "messages_allowed": monthly_message_limit(plan, seats),
            "storage_used": await self.meter.get(workspace_id, STORAGE, STORAGE_PERIOD),
            "storage_allowed": plan.storage_bytes,
            "engineer": plan.engineer,
            "seat_limit": await self.seat_limit(workspace_id),
        }

    async def set_plan(self, actor: str, workspace_id: str, plan_id: str, seats: int) -> Entitlement:
        """An operator setting a plan by hand (``source="manual"``)."""
        return await self.set_entitlement(actor, workspace_id, plan_id, seats, source="manual")

    async def validate_plan(self, workspace_id: str, plan_id: str, seats: int) -> PlanLimits:
        """Whether ``plan_id`` with ``seats`` fits this workspace; ValidationError otherwise."""
        plan = self.catalogue.get(plan_id)
        if plan is None:
            raise ValidationError(f"unknown plan {plan_id!r}", details={"plans": sorted(self.catalogue)})
        workspace = await self.tenancy.store.get_workspace(workspace_id)
        if workspace is None:
            raise ValidationError("no such workspace")
        if WorkspaceKind(workspace.kind) not in plan.workspace_kinds:
            raise ValidationError(f"plan {plan_id!r} is not for a {workspace.kind.value} workspace")
        if not plan.min_seats <= seats <= plan.max_seats:
            raise ValidationError(f"plan {plan_id!r} takes {plan.min_seats}-{plan.max_seats} seats")
        return plan

    async def set_entitlement(
        self, actor: str, workspace_id: str, plan_id: str, seats: int, *, source: str
    ) -> Entitlement:
        await self.validate_plan(workspace_id, plan_id, seats)
        entitlement = Entitlement(workspace_id, plan_id, seats, source, self.clock.now(), actor)
        await self.entitlements.put(entitlement)
        await self.audit.record(AuditEvent(
            action="plan.set", actor=actor, outcome="ok", resource=f"workspace:{workspace_id}",
            at=self.clock.now(), detail={"plan": plan_id, "seats": seats, "source": source},
        ))
        return entitlement
