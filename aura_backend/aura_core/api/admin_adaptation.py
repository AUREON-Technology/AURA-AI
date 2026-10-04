"""Super Admin routes for Track AP connectors (AP2, ADR-021), the Project Twin (AP4)
and blueprints (AP6).

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
from ..domain.discovery import DiscoveryBudget
from ..domain.errors import ValidationError
from ..domain.serialization import to_data

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

    class DiscoverRequest(BaseModel):
        max_objects: int = Field(default=5000, ge=1, le=100_000)
        sample_rows: int = Field(default=1000, ge=0, le=100_000)
        max_profiled_columns: int = Field(default=64, ge=0, le=512)
        max_seconds: float = Field(default=120.0, ge=1.0, le=3600.0)
        sample_values: bool = False

    class BlueprintApproveRequest(BaseModel):
        approval_id: str = Field(min_length=1, max_length=128)

    class RollbackRequest(BaseModel):
        version: int | None = Field(default=None, ge=1)

    class SnapshotRequest(DiscoverRequest):
        connector_ids: list[str] | None = Field(default=None, max_length=32)

    def twins() -> Any:
        service = getattr(container, "twins", None)
        if service is None:
            registry()  # the same 503 as every other route here
        return service

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

    @router.post("/admin/connectors/{connector_id}/discover")
    async def discover_connector(
        connector_id: str, caller=Depends(principal), payload: DiscoverRequest | None = None
    ) -> JSONResponse:
        """Describe an *active* connector's system (AP3). Returns the full report; everything
        human-readable in it is the target system's own text and must be rendered as text."""
        require_super_admin(caller)
        service = getattr(container, "discovery", None)
        if service is None:
            registry()  # raises the same 503 as every other route here
        budget = DiscoveryBudget(**(payload or DiscoverRequest()).model_dump())
        report = await service.discover(connector_id, budget=budget, by=actor(caller))
        return JSONResponse(content={
            "summary": {
                "source_id": report.source_id, "digest": report.digest, "counts": report.counts(),
                "truncated": report.truncated, "issues": [to_data(i) for i in report.issues],
            },
            "report": to_data(report),
        })

    @router.post("/admin/twin/snapshot")
    async def snapshot_twin(caller=Depends(principal), payload: SnapshotRequest | None = None) -> JSONResponse:
        """Discover the active connectors, build the Project Twin and append a version (AP4).

        Returns the version record and the diff from the previous version; an unchanged system
        returns the latest version with ``created: false`` rather than a duplicate.
        """
        require_super_admin(caller)
        service = twins()
        body = payload or SnapshotRequest()
        budget = DiscoveryBudget(**body.model_dump(exclude={"connector_ids"}))
        result = await service.snapshot(by=actor(caller), budget=budget, connector_ids=body.connector_ids)
        return JSONResponse(content={
            "created": result.created, "version": to_data(result.version),
            "diff": {"summary": result.diff.summary(), **to_data(result.diff)},
        })

    @router.get("/admin/twin/versions")
    async def list_twin_versions(
        caller=Depends(principal), limit: int = Query(default=50, ge=1, le=200)
    ) -> JSONResponse:
        require_super_admin(caller)
        return JSONResponse(content={"versions": [to_data(v) for v in await twins().versions(limit=limit)]})

    @router.get("/admin/twin/versions/{version}")
    async def get_twin_version(
        version: int, caller=Depends(principal), kind: str | None = Query(default=None, max_length=32)
    ) -> JSONResponse:
        """One stored version with its elements. Labels and descriptions are the connected
        system's own text (or a model's proposal about it) and must be rendered as text."""
        require_super_admin(caller)
        found = await twins().get(version)
        if found is None:
            raise HTTPException(status_code=404, detail="twin version not found")
        meta, twin = found
        elements = twin.elements
        if kind is not None:
            elements = tuple(e for e in elements if e.kind.value == kind)
        return JSONResponse(content={
            "version": to_data(meta), "digest": twin.digest, "elements": to_data(elements),
        })

    @router.get("/admin/twin/versions/{version}/plan")
    async def plan_twin_version(version: int, caller=Depends(principal)) -> JSONResponse:
        """The capability plan for one twin version (AP5): what AURA could do on this system,
        with the evidence for each selection, the reasons for each rejection and the composed
        agent proposals. Recomputed on request; nothing in it executes."""
        require_super_admin(caller)
        planner = getattr(container, "capability_planner", None)
        if planner is None:
            registry()  # the same 503 as every other route here
        plan = await planner.plan(version)
        return JSONResponse(content={"summary": plan.summary(), "digest": plan.digest, "plan": to_data(plan)})

    def compiler() -> Any:
        service = getattr(container, "blueprint_compiler", None)
        if service is None:
            registry()  # the same 503 as every other route here
        return service

    @router.post("/admin/twin/versions/{version}/compile")
    async def compile_twin_version(version: int, caller=Depends(principal)) -> JSONResponse:
        """Plan and compile one twin version into a new ``proposed`` blueprint (AP6). Nothing
        becomes active: evaluation, approval and deployment are separate, later steps."""
        require_super_admin(caller)
        record = await compiler().compile(version, by=actor(caller))
        return JSONResponse(status_code=201, content={"summary": record.summary()})

    # Registered before "/admin/blueprints/{version}", which would otherwise match "active".
    @router.get("/admin/blueprints/active")
    async def active_blueprint(caller=Depends(principal)) -> JSONResponse:
        require_super_admin(caller)
        service = deployment()
        return JSONResponse(content={"active": _pointer(await service.active()), **(await service.readiness())})

    @router.get("/admin/blueprints")
    async def list_blueprints(
        caller=Depends(principal), limit: int = Query(default=50, ge=1, le=200)
    ) -> JSONResponse:
        require_super_admin(caller)
        service = compiler()
        records = await service.versions(container.twins.project_id, limit=limit)
        return JSONResponse(content={"blueprints": [r.summary() for r in records]})

    @router.get("/admin/blueprints/{version}")
    async def get_blueprint(version: int, caller=Depends(principal)) -> JSONResponse:
        """One blueprint version in full: its templates, tools, agents, views, generated eval
        suites and every dropped item with its reason."""
        require_super_admin(caller)
        service = compiler()
        record = await service.get(container.twins.project_id, version)
        if record is None:
            raise HTTPException(status_code=404, detail="blueprint version not found")
        return JSONResponse(content={"summary": record.summary(), "record": to_data(record)})

    def deployment() -> Any:
        service = getattr(container, "blueprint_deployment", None)
        if service is None:
            registry()  # the same 503 as every other route here
        return service

    def _pointer(pointer: Any) -> Any:
        return to_data(pointer) if pointer is not None else None

    @router.post("/admin/blueprints/{version}/evaluate")
    async def evaluate_blueprint(version: int, caller=Depends(principal)) -> JSONResponse:
        """``proposed`` -> ``evaluated``. Until AP8's gate exists this is refused unless the
        install allows ungated deploys, and the record then says so permanently."""
        require_super_admin(caller)
        record = await deployment().evaluate(version, by=actor(caller))
        return JSONResponse(content={"summary": record.summary()})

    @router.post("/admin/blueprints/{version}/request-approval")
    async def request_blueprint_approval(version: int, caller=Depends(principal)) -> JSONResponse:
        """Open a ``blueprint_deployment`` approval bound to this version's digest. Deciding it
        goes through the existing approvals routes, by a named person."""
        require_super_admin(caller)
        approval = await deployment().request_approval(version, by=actor(caller))
        return JSONResponse(status_code=201, content={"approval": approval.as_dict()})

    @router.post("/admin/blueprints/{version}/approve")
    async def approve_blueprint(version: int, payload: BlueprintApproveRequest,
                                caller=Depends(principal)) -> JSONResponse:
        """``evaluated`` -> ``approved`` under a granted approval for exactly this digest."""
        require_super_admin(caller)
        record = await deployment().approve(version, approval_id=payload.approval_id, by=actor(caller))
        return JSONResponse(content={"summary": record.summary()})

    @router.post("/admin/blueprints/{version}/deploy")
    async def deploy_blueprint(version: int, caller=Depends(principal)) -> JSONResponse:
        """Make an approved version live: its tools, then the pointer, then the agents."""
        require_super_admin(caller)
        pointer = await deployment().deploy(version, by=actor(caller))
        return JSONResponse(content={"active": _pointer(pointer)})

    @router.post("/admin/blueprints/rollback")
    async def rollback_blueprint(caller=Depends(principal), payload: RollbackRequest | None = None) -> JSONResponse:
        """Return to the previous (or a named) superseded version under its own approval."""
        require_super_admin(caller)
        pointer = await deployment().rollback(by=actor(caller), version=(payload or RollbackRequest()).version)
        return JSONResponse(content={"active": _pointer(pointer)})

    @router.get("/admin/twin/diff")
    async def diff_twin_versions(
        caller=Depends(principal),
        from_version: int = Query(ge=1, alias="from"),
        to_version: int = Query(ge=1, alias="to"),
    ) -> JSONResponse:
        require_super_admin(caller)
        diff = await twins().diff(from_version, to_version)
        return JSONResponse(content={"summary": diff.summary(), **to_data(diff)})

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
