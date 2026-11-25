"""Models a person can use, and their standing preferences for how chats are answered.

    GET    /models                  what this deployment can serve, with each model's capabilities
    GET    /models/{model_id}       one of them
    GET    /me/model-settings       the caller's preferences, and what applies when they name nothing
    PUT    /me/model-settings       replace them (a field left out means "no preference")
    DELETE /me/model-settings       forget them

**A person chooses a routing policy, not a model.** Which model may see a piece of content is decided
by its data classification (ADR-030): a hosted provider receives only ``internal`` content however it
is ranked, and self-hosting is a tested invariant. A routing policy (``local_first``, ``cheapest``,
``fastest``, ``quality_first``, ``balanced``) and ``require_local`` steer the router *inside* those
limits; pinning one named model per chat would let a preference step around them, so it is not here.
``GET /models`` tells the person what each model can do and who runs it (``privacy.third_party``,
``max_data_classification``), so the choice of policy is an informed one.

**Preferences are defaults.** A chat request that names ``routing_policy``, ``require_local``,
``temperature`` or ``max_output_tokens`` wins over them; a field left unset there falls back to the
stored preference, then to the server default. ``require_local: true`` is refused while no local
model is configured, so a preference can never make every chat fail.

**What the catalog leaves out:** endpoints, keys, costs, capacity and pool settings (administrators'
``/admin/status``). ``state`` and ``available`` are this worker's circuit view, like that route's.

No ``from __future__ import annotations``: the request model below is resolved by FastAPI by name.
"""

from datetime import datetime, timezone
from typing import Any, Callable

from ..domain.errors import NotFoundError, ValidationError
from ..domain.model_settings import (
    MAX_OUTPUT_TOKENS, MAX_TEMPERATURE, MIN_OUTPUT_TOKENS, MIN_TEMPERATURE, ROUTING_POLICIES, SERVER_DEFAULTS,
    ModelSettings,
)

__all__ = ["register_model_ops"]


def register_model_ops(
    router: Any,
    container: Any,
    *,
    principal: Callable,
    idempotent: list,
    Depends: Any,
    JSONResponse: Any,
    enforce_rate_limit: Callable,
    Request: Any,
) -> None:
    # Imported here, not at module scope: vendor SDKs are reached lazily (ADR-001, enforced by
    # tests/architecture/test_dependency_rules.py).
    from pydantic import BaseModel, Field

    class ModelSettingsRequest(BaseModel):
        routing_policy: str | None = Field(default=None, description="One of the five routing policies, or null.")
        require_local: bool | None = None
        temperature: float | None = Field(default=None, ge=MIN_TEMPERATURE, le=MAX_TEMPERATURE)
        max_output_tokens: int | None = Field(default=None, ge=MIN_OUTPUT_TOKENS, le=MAX_OUTPUT_TOKENS)

    def catalog() -> list[dict[str, Any]]:
        return container.router.catalog()

    def store() -> Any:
        found = getattr(container, "model_settings", None)
        if found is None:
            raise NotFoundError("model settings are not stored on this deployment")
        return found

    def person(caller: Any) -> Any:
        if caller.is_service:
            raise NotFoundError("a service key has no model settings")
        return caller.user_id

    def effective(settings: ModelSettings) -> dict[str, Any]:
        return {
            key: (getattr(settings, key) if getattr(settings, key) is not None else default)
            for key, default in SERVER_DEFAULTS.items()
        }

    @router.get("/models")
    async def list_models(request: Request, caller: Any = Depends(principal)) -> Any:
        await enforce_rate_limit(request, caller, "read")
        return JSONResponse(content={
            "models": catalog(),
            "routing_policies": list(ROUTING_POLICIES),
            "state_scope": "worker",
        })

    @router.get("/models/{model_id}")
    async def get_model(request: Request, model_id: str, caller: Any = Depends(principal)) -> Any:
        await enforce_rate_limit(request, caller, "read")
        for entry in catalog():
            if entry["id"] == model_id:
                return JSONResponse(content=entry)
        raise NotFoundError("model not found", details={"model_id": model_id})

    @router.get("/me/model-settings")
    async def get_settings(request: Request, caller: Any = Depends(principal)) -> Any:
        await enforce_rate_limit(request, caller, "read")
        owner = person(caller)
        stored = await store().get(owner) or ModelSettings()
        return JSONResponse(content={
            "settings": stored.as_dict(),
            "effective": effective(stored),
            "defaults": dict(SERVER_DEFAULTS),
            "routing_policies": list(ROUTING_POLICIES),
        })

    @router.put("/me/model-settings", dependencies=idempotent)
    async def put_settings(
        request: Request, payload: ModelSettingsRequest, caller: Any = Depends(principal)
    ) -> Any:
        """Replace the caller's preferences. Fields left out are cleared, not kept."""
        await enforce_rate_limit(request, caller, "read")
        owner = person(caller)
        wanted = ModelSettings(
            routing_policy=payload.routing_policy, require_local=payload.require_local,
            temperature=payload.temperature, max_output_tokens=payload.max_output_tokens,
        )
        if wanted.require_local and not any(m["local"] for m in catalog()):
            raise ValidationError(
                "require_local needs a local model, and none is configured on this deployment",
                details={"field": "require_local"},
            )
        now = container.clock.now() if getattr(container, "clock", None) else datetime.now(timezone.utc)
        stored = await store().put(owner, wanted, at=now)
        return JSONResponse(content={"settings": stored.as_dict(), "effective": effective(stored)})

    @router.delete("/me/model-settings", dependencies=idempotent)
    async def delete_settings(request: Request, caller: Any = Depends(principal)) -> Any:
        await enforce_rate_limit(request, caller, "read")
        removed = await store().delete(person(caller))
        return JSONResponse(content={"deleted": removed, "effective": effective(ModelSettings())})
