"""Billing routes (SaaS P1-D1, spec 5.4).

* Owner: buy a plan (``checkout``, a Fawry reference code to pay), see the subscription, cancel.
* Fawry: ``POST /billing/fawry/notify`` -- public, but nothing happens without a valid
  ``messageSignature`` (403 otherwise, 400 for a malformed body, never a 500 or a state change).
  Acknowledged with 200 when handled *or* ignored, so Fawry stops retrying.
* In-app (SaaS P1-D2): the Owner of a personal workspace gets a binding token to hand to StoreKit
  / Play Billing (``store-token``). ``/billing/apple/notify`` (App Store Server Notifications V2)
  and ``/billing/google/notify`` (Pub/Sub push of Play RTDN) are public, but nothing happens
  without Apple's pinned signature or Google's push token (403), a malformed body is 400, and a
  store that is not configured is 503 -- never a 500 or a state change.
* SuperAdmin: run the renewal / grace / reconciliation cycle (the Railway cron runs
  ``scripts/run_billing_cycle.py`` instead).

Every route answers 503 ``billing_unavailable`` until ``AURA_BILLING_ENABLED`` is set.
"""

# No `from __future__ import annotations`: request models are defined inside the register
# function, and FastAPI must resolve their annotations at decoration time (as in v3.py).
from typing import Any, Callable

from ..domain.billing import BillingUnavailableError, PaymentStatus
from ..domain.errors import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    DependencyUnavailableError,
    RateLimitedError,
    ValidationError,
)
from ..domain.tenancy import WorkspaceRole

__all__ = ["register_billing_ops"]

MAX_NOTIFICATION_BYTES = 16 * 1024
CHECKOUTS_PER_MINUTE = 5
#: Apple's signed payloads carry three certificates twice (notification + transaction).
MAX_STORE_NOTIFICATION_BYTES = 64 * 1024


def register_billing_ops(
    router: Any, container: Any, *, principal: Callable, require_super_admin: Callable,
    Depends: Any, JSONResponse: Any, Request: Any, Response: Any,
) -> None:
    from pydantic import BaseModel, ConfigDict, Field

    from ..security.rate_limit import SlidingWindowRateLimiter

    billing = container.billing
    stores = container.store_billing
    limiter = SlidingWindowRateLimiter(container.cache_backend, policies=(), metrics=getattr(container, "metrics", None))

    class CheckoutRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")
        plan: str = Field(min_length=1, max_length=40)
        seats: int = Field(default=1, ge=1, le=10_000)

    def require_enabled() -> None:
        if not billing.settings.enabled:
            raise BillingUnavailableError("billing is not enabled on this deployment")

    @router.get("/workspaces/{workspace_id}/billing")
    async def billing_status(workspace_id: str, caller=Depends(principal)) -> JSONResponse:
        require_enabled()
        _ws, membership = await container.tenancy.resolve(str(caller.user_id), workspace_id)
        if membership.role not in (WorkspaceRole.OWNER, WorkspaceRole.ADMIN):
            raise AuthorizationError("only the workspace owner or an admin can see billing")
        body = await billing.status(workspace_id)
        # Which store product sells Pro, so the app reads it from AURA_STORE_PRODUCTS rather than
        # from its build (SaaS P1-F2b). A store with none is left out.
        body["store_products"] = {
            store: sorted(pid for pid, plan in (stores.products.get(store) or {}).items() if plan == "pro")
            for store in ("apple", "google") if any(plan == "pro" for plan in (stores.products.get(store) or {}).values())
        }
        return JSONResponse(content=body)

    # Not idempotency-keyed: each checkout is a new reference code, and a replay would hand back a
    # stale one.
    @router.post("/workspaces/{workspace_id}/billing/checkout")
    async def billing_checkout(workspace_id: str, payload: CheckoutRequest, caller=Depends(principal)) -> JSONResponse:
        require_enabled()
        if not getattr(caller, "email_verified", False):
            raise AuthorizationError("verify your email address before buying a plan")
        decision = await limiter.check_limit(key=str(caller.user_id), scope="billing_checkout", limit=CHECKOUTS_PER_MINUTE)
        if not decision.allowed:
            raise RateLimitedError("too many checkout requests", retry_after=decision.retry_after_s)
        result = await billing.checkout(str(caller.user_id), workspace_id, payload.plan, payload.seats,
                                        email=caller.email or None)
        return JSONResponse(status_code=201, content=result, headers={"Cache-Control": "no-store"})

    @router.post("/workspaces/{workspace_id}/billing/cancel")
    async def billing_cancel(workspace_id: str, caller=Depends(principal)) -> Response:
        require_enabled()
        await billing.cancel(str(caller.user_id), workspace_id)
        return Response(status_code=204)

    @router.post("/billing/fawry/notify")
    async def fawry_notify(request: Request) -> JSONResponse:
        require_enabled()
        body = await request.body()
        if len(body) > MAX_NOTIFICATION_BYTES:
            return JSONResponse(status_code=413, content={"error": "payload_too_large"})
        try:
            result = await billing.handle_notification(body)
        except AuthenticationError:
            return JSONResponse(status_code=403, content={"error": "invalid_signature"})
        except ValidationError:
            return JSONResponse(status_code=400, content={"error": "malformed_notification"})
        return JSONResponse(content={"ok": True, "result": result})

    @router.post("/workspaces/{workspace_id}/billing/store-token")
    async def billing_store_token(workspace_id: str, caller=Depends(principal)) -> JSONResponse:
        require_enabled()
        if not getattr(caller, "email_verified", False):
            raise AuthorizationError("verify your email address before buying a plan")
        decision = await limiter.check_limit(key=str(caller.user_id), scope="billing_checkout", limit=CHECKOUTS_PER_MINUTE)
        if not decision.allowed:
            raise RateLimitedError("too many checkout requests", retry_after=decision.retry_after_s)
        # A Fawry reference that can still be paid would, once paid, turn the store subscription
        # into a Fawry one while the store kept charging (SaaS P1-F2b review #2).
        now = billing.clock.now()
        for payment in await billing.payments.for_workspace(workspace_id, limit=10):
            waiting = payment.status is PaymentStatus.RECEIVED or (
                payment.status is PaymentStatus.PENDING and (payment.expires_at is None or payment.expires_at > now))
            if waiting:
                raise ConflictError("a payment is waiting for this workspace; try again once it is paid or has expired")
        token = await stores.issue_token(str(caller.user_id), workspace_id)
        if caller.email and billing.contacts is not None:
            await billing.contacts.put(workspace_id, caller.email, billing.clock.now(), set_by=str(caller.user_id))
        return JSONResponse(status_code=201, content={"token": token}, headers={"Cache-Control": "no-store"})

    async def _store_answer(handle) -> JSONResponse:
        try:
            result = await handle()
        except AuthenticationError:
            return JSONResponse(status_code=403, content={"error": "invalid_signature"})
        except ValidationError:
            return JSONResponse(status_code=400, content={"error": "malformed_notification"})
        except DependencyUnavailableError:
            # Google Play did not answer: 503 so Pub/Sub delivers the message again.
            return JSONResponse(status_code=503, content={"error": "store_unavailable"})
        return JSONResponse(content={"ok": True, "result": result})

    @router.post("/billing/apple/notify")
    async def apple_notify(request: Request) -> JSONResponse:
        require_enabled()
        if stores.apple is None:
            raise BillingUnavailableError("the App Store is not configured on this deployment")
        body = await request.body()
        if len(body) > MAX_STORE_NOTIFICATION_BYTES:
            return JSONResponse(status_code=413, content={"error": "payload_too_large"})
        try:
            import json

            signed = json.loads(body).get("signedPayload")
        except (ValueError, UnicodeDecodeError, RecursionError, AttributeError):
            signed = None
        if not isinstance(signed, str) or not signed:
            return JSONResponse(status_code=400, content={"error": "malformed_notification"})
        return await _store_answer(lambda: stores.handle_apple(signed))

    @router.post("/billing/google/notify")
    async def google_notify(request: Request) -> JSONResponse:
        require_enabled()
        if stores.google_push is None or stores.google_play is None:
            raise BillingUnavailableError("Google Play is not configured on this deployment")
        body = await request.body()
        if len(body) > MAX_STORE_NOTIFICATION_BYTES:
            return JSONResponse(status_code=413, content={"error": "payload_too_large"})
        authorization = request.headers.get("authorization")
        return await _store_answer(lambda: stores.handle_google(authorization, body))

    class GraceRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")
        days: int = Field(ge=1, le=30)

    @router.get("/admin/customers")
    async def list_customers(limit: int = 50, after: str | None = None, caller=Depends(principal)) -> JSONResponse:
        """The console's read-only Customers page (SaaS P1-F1)."""
        require_super_admin(caller)
        return JSONResponse(content=await billing.customers(max(1, min(limit, 200)), after))

    @router.post("/admin/customers/{workspace_id}/extend-grace")
    async def extend_grace(workspace_id: str, payload: GraceRequest, caller=Depends(principal)) -> JSONResponse:
        require_super_admin(caller)
        return JSONResponse(content=await billing.extend_grace(str(caller.user_id), workspace_id, payload.days))

    @router.post("/admin/billing/run-cycle")
    async def run_billing_cycle(caller=Depends(principal)) -> JSONResponse:
        require_super_admin(caller)
        require_enabled()
        return JSONResponse(content=await billing.run_cycle())
