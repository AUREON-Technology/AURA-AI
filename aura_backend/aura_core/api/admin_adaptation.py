"""Super Admin routes for Track AP connectors (AP2, ADR-021).

Every route requires the Super Admin role: connecting AURA to another system is the authority
boundary the whole track rests on. The routes are always mounted -- so the route-auth suite keeps
enumerating them -- and answer 503 when ``AURA_ADAPTATION_ENABLED`` is off.

Two things these routes never do:

* **Echo a credential.** The register body is read as raw JSON and ``secret`` is moved into a
  :class:`~aura_core.domain.connectors.Credential` *before* anything validates the rest. FastAPI's
  default 422 response echoes request input (a "missing field" error echoes the whole body), so
  a credential must never reach a pydantic model. Responses carry ``has_credential``, never the
  credential or its vault reference.
* **Activate without an approval.** Registration only requests one; activation consumes it
  (``ConnectorRegistry.activate``). Decisions go through the existing approvals routes.
"""

# No `from __future__ import annotations`: FastAPI resolves `Request` (passed in, not imported)
# from the closure at definition time, the same reason `admin.py` omits it.

from typing import Any, Callable

from ..domain.connectors import (
    ConnectorDescriptor,
    ConnectorHealth,
    ConnectorKind,
    ConnectorRecord,
    ConnectorState,
    Credential,
    IdentityMode,
)
from ..domain.errors import ValidationError

__all__ = ["register_adaptation_ops"]

_REGISTER_FIELDS = {
    "id", "kind", "identity_mode", "display_name", "endpoint", "options",
    "max_concurrency", "timeout_s", "row_cap", "secret",
}
_MAX_SECRET_CHARS = 4096


def register_adaptation_ops(
    router: Any,
    container: Any,
    *,
    principal: Callable,
    require_super_admin: Callable,
    Depends: Any,
    HTTPException: Any,
    JSONResponse: Any,
    Query: Any,
    Request: Any,
    idempotent: Any = (),
) -> None:
    from pydantic import BaseModel, Field

    class ActivateRequest(BaseModel):
        approval_id: str = Field(min_length=1, max_length=128)

    def registry() -> Any:
        connectors = getattr(container, "connectors", None)
        if connectors is None:
            raise HTTPException(
                status_code=503,
                detail="the adaptation layer is disabled on this install (AURA_ADAPTATION_ENABLED)",
            )
        return connectors

    def actor(caller: Any) -> str:
        return caller.email or caller.user_id.value

    @router.get("/admin/connectors")
    async def list_connectors(
        caller=Depends(principal), state: str | None = Query(default=None)
    ) -> JSONResponse:
        require_super_admin(caller)
        try:
            state_enum = ConnectorState(state) if state else None
        except ValueError as exc:
            raise ValidationError("unknown connector state", details={"state": state}) from exc
        records = await registry().list(state=state_enum)
        return JSONResponse(
            content={
                "connectors": [_record_payload(r) for r in records],
                "supported_kinds": sorted(str(k) for k in registry().supported_kinds),
            }
        )

    # Deliberately *not* idempotency-keyed: the idempotency store keeps a SHA-256 of the request
    # body, and this body carries the credential -- an unsalted hash of it is an offline
    # brute-force target for a weak password. A retried registration gets 409 (the id exists).
    @router.post("/admin/connectors")
    async def register_connector(request: Request, caller=Depends(principal)) -> JSONResponse:
        require_super_admin(caller)
        connectors = registry()
        body = await _json_object(request)
        secret = _take_secret(body)
        descriptor = _descriptor(body)
        record, approval = await connectors.register(descriptor, secret=secret, requested_by=actor(caller))
        return JSONResponse(
            status_code=201,
            content={"connector": _record_payload(record), "approval": approval.as_dict()},
        )

    @router.get("/admin/connectors/{connector_id}")
    async def get_connector(connector_id: str, caller=Depends(principal)) -> JSONResponse:
        require_super_admin(caller)
        return JSONResponse(content={"connector": _record_payload(await registry().get(connector_id))})

    @router.post("/admin/connectors/{connector_id}/health")
    async def check_connector(connector_id: str, caller=Depends(principal)) -> JSONResponse:
        """Test the connection. Works in any state, so a pending connector can be checked
        before anyone approves it."""
        require_super_admin(caller)
        return JSONResponse(content={"health": _health_payload(await registry().health(connector_id))})

    @router.post("/admin/connectors/{connector_id}/activate", dependencies=list(idempotent))
    async def activate_connector(
        connector_id: str, payload: ActivateRequest, caller=Depends(principal)
    ) -> JSONResponse:
        require_super_admin(caller)
        record = await registry().activate(connector_id, approval_id=payload.approval_id, by=actor(caller))
        return JSONResponse(content={"connector": _record_payload(record)})

    @router.post("/admin/connectors/{connector_id}/disable")
    async def disable_connector(connector_id: str, caller=Depends(principal)) -> JSONResponse:
        require_super_admin(caller)
        record = await registry().disable(connector_id, by=actor(caller))
        return JSONResponse(content={"connector": _record_payload(record)})

    @router.delete("/admin/connectors/{connector_id}")
    async def remove_connector(connector_id: str, caller=Depends(principal)) -> JSONResponse:
        require_super_admin(caller)
        return JSONResponse(content={"deleted": await registry().remove(connector_id, by=actor(caller))})


async def _json_object(request: Any) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception as exc:
        raise ValidationError("request body must be a JSON object") from exc
    if not isinstance(body, dict):
        raise ValidationError("request body must be a JSON object")
    unknown = sorted(set(body) - _REGISTER_FIELDS)
    if unknown:
        raise ValidationError("unknown fields", details={"fields": unknown})
    return body


def _take_secret(body: dict[str, Any]) -> Credential | None:
    """Remove the credential from the body before anything else can see (or echo) it."""
    raw = body.pop("secret", None)
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str) or len(raw) > _MAX_SECRET_CHARS:
        raise ValidationError(f"secret must be a string of at most {_MAX_SECRET_CHARS} characters")
    return Credential(raw)


def _descriptor(body: dict[str, Any]) -> ConnectorDescriptor:
    def text(name: str) -> str:
        value = body.get(name)
        if not isinstance(value, str):
            raise ValidationError(f"{name} must be a string", details={"field": name})
        return value

    def number(name: str, default: Any, kind: type) -> Any:
        value = body.get(name, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValidationError(f"{name} must be a number", details={"field": name})
        return kind(value)

    try:
        kind = ConnectorKind(text("kind"))
        mode = IdentityMode(body.get("identity_mode", IdentityMode.SERVICE_ACCOUNT.value))
    except ValueError as exc:
        raise ValidationError(
            "unknown connector kind or identity mode",
            details={
                "kinds": [k.value for k in ConnectorKind],
                "identity_modes": [m.value for m in IdentityMode],
            },
        ) from exc
    options = body.get("options", {})
    if not isinstance(options, dict):
        raise ValidationError("options must be an object", details={"field": "options"})
    return ConnectorDescriptor(
        id=text("id"),
        kind=kind,
        identity_mode=mode,
        display_name=text("display_name"),
        endpoint=text("endpoint"),
        options=options,
        max_concurrency=number("max_concurrency", 4, int),
        timeout_s=number("timeout_s", 15.0, float),
        row_cap=number("row_cap", 1000, int),
    )


def _record_payload(record: ConnectorRecord) -> dict[str, Any]:
    d = record.descriptor
    return {
        "id": d.id,
        "kind": str(d.kind),
        "identity_mode": str(d.identity_mode),
        "display_name": d.display_name,
        "endpoint": d.endpoint,
        "options": dict(d.options),
        "has_credential": d.secret_ref is not None,
        "max_concurrency": d.max_concurrency,
        "timeout_s": d.timeout_s,
        "row_cap": d.row_cap,
        "digest": d.digest,
        "state": str(record.state),
        "approval_id": record.approval_id,
        "registered_by": record.registered_by,
        "registered_at": record.registered_at.isoformat(),
        "activated_by": record.activated_by,
        "activated_at": record.activated_at.isoformat() if record.activated_at else None,
    }


def _health_payload(health: ConnectorHealth) -> dict[str, Any]:
    return {
        "status": str(health.status),
        "reason": health.reason,
        "write_capable": health.write_capable,
        "findings": list(health.findings),
        "latency_ms": health.latency_ms,
    }
