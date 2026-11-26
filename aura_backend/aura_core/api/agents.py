"""``/api/v3/agents``: a person's own agents, on top of the built-in personas.

The built-in agents are code (``aura_core/agents/registry.py``); they are read-only here. A
*custom* agent is a built-in persona **plus** extra instructions, a narrower tool list and a lower
risk ceiling, owned by one person (``aura_core/domain/custom_agents.py``). It can narrow its base
persona and never widen it: ``aura_core.agents.custom.apply_custom_agent`` joins the two on every
turn, against the base as it is then, and a tool outside the base's grant is refused here at write
time too, so a bad list is told at once rather than silently trimmed later.

Routes added to the existing ``GET /agents`` and ``GET /agents/runs`` pair::

    GET    /agents/{id}           a built-in or the caller's own custom agent
    POST   /agents                create a custom agent on a built-in persona
    PATCH  /agents/{id}           update name, description, instruction, tools, risk ceiling
    PUT    /agents/{id}/config    replace the behaviour settings (instruction, tools, ceiling)
    DELETE /agents/{id}
    POST   /agents/{id}/clone     copy a built-in (as a new custom agent) or one's own custom agent

A custom agent runs by naming its id as ``agent`` on ``/chat`` or ``/chat/stream`` (resolved in
``v3.to_turn``, under the caller's ownership).

Ownership: every read and write is scoped to the caller's user id. Another person's agent id is
indistinguishable from one that does not exist (404). A service key has no person to own an agent
and reaches none of these routes by default (AR-4). Only the person's own account creates agents;
there is no sharing in this version.

Planning and execution are not here. They are the kernel's, as for every agent; this surface only
defines which persona, instructions and tools a turn is run with.

No ``from __future__ import annotations``: the request models are defined inside the register
function and FastAPI must see the real classes, not strings (same as ``workspaces.py``).
"""

from datetime import datetime, timezone
from typing import Any, Callable

from ..domain.custom_agents import (
    FORBIDDEN_BASE_AGENTS,
    MAX_CUSTOM_AGENTS_PER_OWNER,
    MAX_NAME_CHARS,
    RISK_NAMES,
    CustomAgent,
    CustomAgentConflict,
    CustomAgentError,
    clean_description,
    clean_instruction,
    clean_name,
    clean_tools,
    is_custom_agent_id,
    new_custom_agent_id,
    unknown_tools,
)
from ..domain.errors import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    RateLimitedError,
    ValidationError,
)
from ..domain.tools import ToolSensitivity

#: Writes per person per minute. Small on purpose: a person edits an agent by hand.
WRITES_PER_MINUTE = 30
_COPY_SUFFIX_TRIES = 20


def register_agent_ops(
    router: Any,
    container: Any,
    *,
    principal: Callable,
    idempotent: list,
    Depends: Any,
    JSONResponse: Any,
) -> None:
    from pydantic import BaseModel, ConfigDict, Field

    from ..security.rate_limit import SlidingWindowRateLimiter

    store = container.custom_agents
    limiter = SlidingWindowRateLimiter(container.cache_backend, policies=(), metrics=getattr(container, "metrics", None))

    class CreateAgent(BaseModel):
        model_config = ConfigDict(extra="forbid")
        name: str = Field(max_length=MAX_NAME_CHARS * 4)
        description: str | None = Field(default=None, max_length=2000)
        base_agent: str = Field(default="orchestrator", max_length=64)
        instruction: str | None = Field(default=None, max_length=20000)
        allowed_tools: list[str] | None = Field(default=None, max_length=1000)
        risk_ceiling: str | None = Field(default=None, max_length=32)

    class UpdateAgent(BaseModel):
        """Only what changes. ``null`` clears instruction, tools or ceiling; the base persona is
        not changeable (make a new agent, or clone)."""

        model_config = ConfigDict(extra="forbid")
        name: str | None = Field(default=None, max_length=MAX_NAME_CHARS * 4)
        description: str | None = Field(default=None, max_length=2000)
        instruction: str | None = Field(default=None, max_length=20000)
        allowed_tools: list[str] | None = Field(default=None, max_length=1000)
        risk_ceiling: str | None = Field(default=None, max_length=32)

    class ConfigureAgent(BaseModel):
        """The whole behaviour of the agent, replaced. Anything omitted is cleared."""

        model_config = ConfigDict(extra="forbid")
        instruction: str | None = Field(default=None, max_length=20000)
        allowed_tools: list[str] | None = Field(default=None, max_length=1000)
        risk_ceiling: str | None = Field(default=None, max_length=32)

    class CloneAgent(BaseModel):
        model_config = ConfigDict(extra="forbid")
        name: str | None = Field(default=None, max_length=MAX_NAME_CHARS * 4)

    # -- helpers ---------------------------------------------------------------------------

    def person_only(caller: Any) -> Any:
        if caller.is_service:
            raise AuthorizationError("a service key has no agents of its own")
        return caller.user_id

    async def write_budget(caller: Any) -> None:
        decision = await limiter.check_limit(key=str(caller.user_id), scope="agent_write", limit=WRITES_PER_MINUTE)
        if not decision.allowed:
            raise RateLimitedError("too many agent changes; try again in a minute", retry_after=decision.retry_after_s)

    def builtin(agent_id: str) -> Any:
        return next((spec for spec in container.agents if spec.id == agent_id), None)

    def check_field(call: Callable[[], Any]) -> Any:
        try:
            return call()
        except CustomAgentError as exc:
            raise ValidationError(exc.message, details={"field": exc.field}) from None

    def check_base(base_id: str) -> Any:
        if base_id in FORBIDDEN_BASE_AGENTS:
            raise ValidationError(
                f"no custom agent can be built on {base_id!r}: it runs code under consent and plan gates "
                "that a copy must not carry", details={"field": "base_agent"},
            )
        spec = builtin(base_id)
        if spec is None:
            raise ValidationError(
                f"{base_id!r} is not a built-in agent", details={"field": "base_agent",
                                                                  "built_in": sorted(s.id for s in container.agents)},
            )
        return spec

    def check_tools(base: Any, tools: tuple[str, ...] | None) -> None:
        """A list names real tools, all inside what the base persona permits. Told at write time;
        the turn re-intersects against the base anyway, so this is never the only guard."""
        if tools is None:
            return
        unknown = unknown_tools(tools, container.tools.names())
        if unknown:
            raise ValidationError("these are not tools this server has", details={"field": "allowed_tools", "tools": unknown})
        outside = sorted(name for name in tools if not base.permits_tool(name))
        if outside:
            raise ValidationError(
                f"the {base.id!r} persona does not have these tools, and a custom agent can only narrow it",
                details={"field": "allowed_tools", "tools": outside},
            )

    def check_risk(base: Any, ceiling: str | None) -> str | None:
        if ceiling is None:
            return None
        if ceiling not in RISK_NAMES:
            raise ValidationError(f"risk_ceiling is one of {', '.join(RISK_NAMES)}", details={"field": "risk_ceiling"})
        if ToolSensitivity[ceiling] > base.risk_ceiling:
            raise ValidationError(
                f"the {base.id!r} persona's ceiling is {base.risk_ceiling.name}; a custom agent can only lower it",
                details={"field": "risk_ceiling"},
            )
        return ceiling

    def builtin_view(spec: Any) -> dict[str, Any]:
        template = container.prompts.get(spec.prompt_template_id)
        return {
            "id": spec.id,
            "kind": "builtin",
            "name": spec.name,
            "prompt": spec.prompt_template_id,
            "version": template.version,
            "description": template.description,
            "allowed_tools": sorted(spec.allowed_tools) if spec.allowed_tools is not None else None,
            "allowed_tool_prefixes": sorted(spec.allowed_tool_prefixes),
            "risk_ceiling": spec.risk_ceiling.name,
            "required_capabilities": sorted(spec.required_capabilities),
            "read_only": True,
        }

    async def owned(caller: Any, agent_id: str) -> CustomAgent:
        """The caller's custom agent, or the one 404 that also answers another person's id."""
        user = person_only(caller)
        agent = await store.get(user, agent_id) if is_custom_agent_id(agent_id) else None
        if agent is None:
            raise NotFoundError("agent not found", details={"agent_id": agent_id})
        return agent

    def refuse_builtin(agent_id: str) -> None:
        if builtin(agent_id) is not None:
            raise AuthorizationError(
                f"{agent_id!r} is a built-in agent and cannot be changed or deleted; clone it to make your own",
                details={"agent_id": agent_id},
            )

    async def save_new(agent: CustomAgent) -> CustomAgent:
        try:
            return await store.create(agent, limit=MAX_CUSTOM_AGENTS_PER_OWNER)
        except CustomAgentConflict as exc:
            raise ConflictError(exc.message, details={"reason": exc.reason}) from None

    # -- routes ----------------------------------------------------------------------------
    # Registered after `/agents/runs`, which is a literal path and must keep winning over `{agent_id}`.

    @router.get("/agents/{agent_id}")
    async def get_agent(agent_id: str, caller: Any = Depends(principal)) -> Any:
        spec = builtin(agent_id)
        if spec is not None:
            return JSONResponse(content=builtin_view(spec))
        agent = await owned(caller, agent_id)
        return JSONResponse(content=agent.as_dict())

    @router.post("/agents", status_code=201, dependencies=idempotent)
    async def create_agent(payload: CreateAgent, caller: Any = Depends(principal)) -> Any:
        user = person_only(caller)
        await write_budget(caller)
        base = check_base(payload.base_agent)
        name = check_field(lambda: clean_name(payload.name))
        description = check_field(lambda: clean_description(payload.description))
        instruction = check_field(lambda: clean_instruction(payload.instruction))
        tools = check_field(lambda: clean_tools(payload.allowed_tools))
        check_tools(base, tools)
        risk = check_risk(base, payload.risk_ceiling)
        now = datetime.now(timezone.utc)
        agent = await save_new(CustomAgent(
            id=new_custom_agent_id(), owner_id=user, name=name, description=description,
            base_agent_id=base.id, instruction=instruction, allowed_tools=tools, risk_ceiling=risk,
            created_at=now, updated_at=now,
        ))
        return JSONResponse(status_code=201, content=agent.as_dict())

    async def rewrite(caller: Any, agent_id: str, *, fields: dict[str, Any]) -> Any:
        """Apply ``fields`` (already the new values) to the caller's custom agent and store it."""
        refuse_builtin(agent_id)
        user = person_only(caller)
        await write_budget(caller)
        current = await owned(caller, agent_id)
        base = builtin(current.base_agent_id)
        if base is None:  # the persona it was built on no longer exists: it cannot run, so it cannot be edited into health
            raise ConflictError("this agent's base persona no longer exists", details={"base_agent": current.base_agent_id})
        merged = {
            "name": current.name, "description": current.description, "instruction": current.instruction,
            "allowed_tools": current.allowed_tools, "risk_ceiling": current.risk_ceiling,
        }
        merged.update(fields)
        check_tools(base, merged["allowed_tools"])
        merged["risk_ceiling"] = check_risk(base, merged["risk_ceiling"])
        replacement = CustomAgent(
            id=current.id, owner_id=user, base_agent_id=current.base_agent_id,
            created_at=current.created_at, updated_at=datetime.now(timezone.utc), **merged,
        )
        try:
            stored = await store.update(replacement)
        except CustomAgentConflict as exc:
            raise ConflictError(exc.message, details={"reason": exc.reason}) from None
        if stored is None:
            raise NotFoundError("agent not found", details={"agent_id": agent_id})
        return JSONResponse(content=stored.as_dict())

    @router.patch("/agents/{agent_id}", dependencies=idempotent)
    async def update_agent(agent_id: str, payload: UpdateAgent, caller: Any = Depends(principal)) -> Any:
        sent = payload.model_fields_set
        if not sent:
            raise ValidationError("send at least one field", details={"field": None})
        fields: dict[str, Any] = {}
        if "name" in sent:
            fields["name"] = check_field(lambda: clean_name(payload.name))
        if "description" in sent:
            fields["description"] = check_field(lambda: clean_description(payload.description))
        if "instruction" in sent:
            fields["instruction"] = check_field(lambda: clean_instruction(payload.instruction))
        if "allowed_tools" in sent:
            fields["allowed_tools"] = check_field(lambda: clean_tools(payload.allowed_tools))
        if "risk_ceiling" in sent:
            fields["risk_ceiling"] = payload.risk_ceiling
        return await rewrite(caller, agent_id, fields=fields)

    @router.put("/agents/{agent_id}/config", dependencies=idempotent)
    async def configure_agent(agent_id: str, payload: ConfigureAgent, caller: Any = Depends(principal)) -> Any:
        return await rewrite(caller, agent_id, fields={
            "instruction": check_field(lambda: clean_instruction(payload.instruction)),
            "allowed_tools": check_field(lambda: clean_tools(payload.allowed_tools)),
            "risk_ceiling": payload.risk_ceiling,
        })

    @router.delete("/agents/{agent_id}", dependencies=idempotent)
    async def delete_agent(agent_id: str, caller: Any = Depends(principal)) -> Any:
        refuse_builtin(agent_id)
        user = person_only(caller)
        await write_budget(caller)
        if not (is_custom_agent_id(agent_id) and await store.delete(user, agent_id)):
            raise NotFoundError("agent not found", details={"agent_id": agent_id})
        return JSONResponse(content={"deleted": True, "id": agent_id})

    @router.post("/agents/{agent_id}/clone", status_code=201, dependencies=idempotent)
    async def clone_agent(agent_id: str, payload: CloneAgent | None = None, caller: Any = Depends(principal)) -> Any:
        user = person_only(caller)
        await write_budget(caller)
        requested = check_field(lambda: clean_name(payload.name)) if payload is not None and payload.name is not None else None
        source = builtin(agent_id)
        if source is not None:
            check_base(source.id)
            template = dict(base_agent_id=source.id, description="", instruction="", allowed_tools=None, risk_ceiling=None)
            default_name = source.name
        else:
            original = await owned(caller, agent_id)
            template = dict(
                base_agent_id=original.base_agent_id, description=original.description,
                instruction=original.instruction, allowed_tools=original.allowed_tools,
                risk_ceiling=original.risk_ceiling,
            )
            default_name = original.name

        now = datetime.now(timezone.utc)
        if requested is not None:
            candidates = [requested]
        else:
            stem = default_name[: MAX_NAME_CHARS - len(" (copy 99)")]
            candidates = [f"{stem} (copy)"] + [f"{stem} (copy {n})" for n in range(2, _COPY_SUFFIX_TRIES + 1)]
        for index, name in enumerate(candidates):
            try:
                created = await store.create(
                    CustomAgent(id=new_custom_agent_id(), owner_id=user, name=name, created_at=now, updated_at=now, **template),
                    limit=MAX_CUSTOM_AGENTS_PER_OWNER,
                )
            except CustomAgentConflict as exc:
                if exc.reason == "name" and index + 1 < len(candidates):
                    continue  # a default name that is taken: try the next suffix
                raise ConflictError(exc.message, details={"reason": exc.reason}) from None
            return JSONResponse(status_code=201, content=created.as_dict())
        raise ConflictError("could not find a free name for the copy; send one", details={"reason": "name"})
