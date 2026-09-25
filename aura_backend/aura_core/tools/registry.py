"""Tool registry: declaration, schema validation, policy, and sandboxed dispatch.

Replaces the pattern at ``services/agent.py:108-288``, where tools are Python closures created
per request and dispatched by an ``if/elif`` chain on a model-chosen name, with model-supplied
arguments splatted into the callable -- no validation, no timeout, no authorisation, no audit
(SEC-07).

Four things happen between a model naming a tool and that tool running:

1. **Resolution** -- the name must be registered, enabled, and not denied by configuration.
2. **Validation** -- arguments are checked against the tool's JSON schema. Unknown keys are
   rejected rather than passed through, so a hallucinated parameter cannot reach a callable.
3. **Policy** -- capability grants, taint rules, side-effect confirmation.
4. **Sandbox** -- per-tool timeout, global concurrency limit, result size cap.

Every dispatch attempt is audited, including refusals. "The model tried to read your Drive and
policy stopped it" is exactly the event a reviewer needs to see.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Any, Iterable, Mapping, Sequence

from ..domain.errors import (
    ToolExecutionError,
    ToolNotFoundError,
    ToolTimeoutError,
    ValidationError,
)
from ..domain.events import ToolInvoked
from ..domain.messages import ToolCall, ToolResult
from ..domain.tools import ToolPolicyDecision, ToolSensitivity, ToolSpec
from ..observability.logging import get_logger
from ..ports.runtime import Tool, ToolContext
from .policy import ToolPolicy
from .schema import validate_arguments

__all__ = ["ToolRegistry", "ToolExecutionReport"]

logger = get_logger("tools")


class ToolExecutionReport:
    """Outcome of one dispatch, for telemetry and audit."""

    __slots__ = ("call_id", "tool", "allowed", "ok", "duration_ms", "error_kind",
                 "policy_reason", "result_chars", "needs_confirmation", "arguments", "diff_preview")

    def __init__(
        self,
        *,
        call_id: str,
        tool: str,
        allowed: bool,
        ok: bool,
        duration_ms: float,
        error_kind: str | None = None,
        policy_reason: str = "",
        result_chars: int = 0,
        needs_confirmation: bool = False,
        arguments: Mapping[str, Any] | None = None,
        diff_preview: str | None = None,
    ) -> None:
        self.call_id = call_id
        self.tool = tool
        self.allowed = allowed
        self.ok = ok
        self.duration_ms = duration_ms
        self.error_kind = error_kind
        self.policy_reason = policy_reason
        self.result_chars = result_chars
        self.needs_confirmation = needs_confirmation
        # Only ever populated on the confirmation-required path (see `execute()`'s matching
        # `finish()` call below) -- never for a call that already ran or was denied outright.
        # This deliberately does NOT follow `_audit_call`'s digest-only rule: that rule protects
        # a long-lived, widely-readable audit log from a user's private data; this instead rides
        # a same-user SSE/API response for a call that hasn't executed and is sitting in
        # confirm-required limbo -- the user is being asked right now to approve exactly these
        # arguments, so they must be able to see them. Different artefact, different lifetime,
        # different audience.
        self.arguments = dict(arguments) if arguments else {}
        # D6: a diff of what this call would change, produced by `spec.preview` (see
        # `ToolSpec.preview`'s docstring) -- same confirmation-required-only lifetime/audience
        # reasoning as `arguments` above. `None` for a tool with no `preview` hook, or if the hook
        # itself declined/failed to produce one.
        self.diff_preview = diff_preview


class ToolRegistry:
    """Holds tools and executes them under policy."""

    def __init__(
        self,
        *,
        policy: ToolPolicy | None = None,
        allowlist: Sequence[str] = (),
        denylist: Sequence[str] = (),
        max_parallel: int = 4,
        default_timeout_s: float = 15.0,
        metrics: Any = None,
        event_bus: Any = None,
        audit: Any = None,
    ) -> None:
        self._tools: dict[str, Tool] = {}
        self._policy = policy or ToolPolicy()
        # An allowlist, when non-empty, is exclusive: only listed tools are visible at all.
        self._allowlist = frozenset(allowlist)
        self._denylist = frozenset(denylist)
        self._semaphore = asyncio.Semaphore(max(1, max_parallel))
        self._default_timeout = default_timeout_s
        self._metrics = metrics
        self._events = event_bus
        self._audit = audit

    # -- registration ------------------------------------------------------------------

    def register(self, tool: Tool, *, overwrite: bool = False) -> None:
        spec = tool.spec
        if spec.name in self._tools and not overwrite:
            raise ValidationError(
                f"tool {spec.name!r} is already registered",
                details={"registered": sorted(self._tools)},
            )
        self._tools[spec.name] = tool
        logger.info(
            "tool registered",
            extra={"tool": spec.name, "category": str(spec.category),
                   "sensitivity": spec.sensitivity.name, "side_effecting": spec.side_effecting},
        )

    def register_all(self, tools: Iterable[Tool]) -> None:
        for tool in tools:
            self.register(tool)

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    # -- discovery ---------------------------------------------------------------------

    def _is_visible(self, spec: ToolSpec) -> bool:
        if spec.name in self._denylist:
            return False
        if self._allowlist and spec.name not in self._allowlist:
            return False
        return True

    def available(
        self,
        *,
        capabilities: Sequence[str] = (),
        max_sensitivity: ToolSensitivity = ToolSensitivity.SYSTEM,
    ) -> tuple[ToolSpec, ...]:
        """Specs this caller may actually use.

        Filtering here rather than at dispatch matters: a tool the caller cannot use should not
        appear in the model's tool list at all. Advertising it produces calls that are always
        refused, wasting a generation and teaching the model a capability it does not have.
        """
        granted = frozenset(capabilities)
        result = []
        for tool in self._tools.values():
            spec = tool.spec
            if not self._is_visible(spec):
                continue
            if spec.sensitivity > max_sensitivity:
                continue
            if spec.required_capability and spec.required_capability not in granted:
                continue
            result.append(spec)
        return tuple(sorted(result, key=lambda s: s.name))

    def declarations(
        self,
        *,
        capabilities: Sequence[str] = (),
        max_sensitivity: ToolSensitivity = ToolSensitivity.SYSTEM,
    ) -> tuple[dict[str, Any], ...]:
        """Vendor-neutral declarations for a :class:`CompletionRequest`."""
        return tuple(
            spec.declaration()
            for spec in self.available(capabilities=capabilities, max_sensitivity=max_sensitivity)
        )

    def get(self, name: str) -> Tool:
        tool = self._tools.get(name)
        if tool is None or not self._is_visible(tool.spec):
            raise ToolNotFoundError(
                f"tool {name!r} is not available",
                tool=name,
                details={"available": [s.name for s in self.available()]},
            )
        return tool

    def spec(self, name: str) -> ToolSpec:
        return self.get(name).spec

    # -- execution ---------------------------------------------------------------------

    async def execute(
        self,
        call: ToolCall,
        context: ToolContext,
        *,
        capabilities: Sequence[str] = (),
        confirmed: bool = False,
    ) -> tuple[ToolResult, ToolExecutionReport]:
        """Validate, authorise, and run one tool call.

        Returns a :class:`ToolResult` even on failure -- an error the model can read and react
        to ("the city name was invalid, try again") is more useful than an exception that ends
        the turn. Failures are still counted and audited; nothing is silent.
        """
        started = time.perf_counter()
        name = call.name
        arguments_digest = hashlib.sha256(
            json.dumps(dict(call.arguments), sort_keys=True, default=str).encode()
        ).hexdigest()[:16]

        def finish(
            *, ok: bool, content: str, error_kind: str | None = None, allowed: bool = True,
            policy_reason: str = "", needs_confirmation: bool = False,
            arguments: Mapping[str, Any] | None = None, diff_preview: str | None = None,
        ) -> tuple[ToolResult, ToolExecutionReport]:
            duration = (time.perf_counter() - started) * 1000.0
            result = ToolResult(
                call_id=call.id,
                name=name,
                ok=ok,
                content=content,
                duration_ms=duration,
                error_kind=error_kind,
                metadata={"allowed": allowed, "policy_reason": policy_reason},
            )
            report = ToolExecutionReport(
                call_id=call.id.value,
                tool=name,
                allowed=allowed,
                ok=ok,
                duration_ms=duration,
                error_kind=error_kind,
                policy_reason=policy_reason,
                result_chars=len(content),
                needs_confirmation=needs_confirmation,
                arguments=arguments,
                diff_preview=diff_preview,
            )
            return result, report

        # 1. Resolution
        try:
            tool = self.get(name)
        except ToolNotFoundError as exc:
            self._observe(name, outcome="not_found")
            await self._audit_call(name, context, "denied", arguments_digest, "tool_not_found")
            return finish(
                ok=False,
                content=f"Tool '{name}' is not available. Available tools: "
                        f"{', '.join(s.name for s in self.available(capabilities=capabilities))}.",
                error_kind=exc.kind,
                allowed=False,
                policy_reason="tool_not_found",
            )

        spec = tool.spec

        # 2. Argument validation against the declared schema
        try:
            arguments = validate_arguments(spec, call.arguments)
        except ValidationError as exc:
            self._observe(name, outcome="invalid_arguments")
            return finish(
                ok=False,
                # The model needs to know precisely what was wrong to correct itself.
                content=f"Invalid arguments for '{name}': {exc.message}. "
                        f"Expected schema: {json.dumps(spec.json_schema())}",
                error_kind=exc.kind,
                policy_reason="invalid_arguments",
            )

        # 3. Policy
        decision: ToolPolicyDecision = self._policy.evaluate(
            spec, context=context, capabilities=capabilities, confirmed=confirmed
        )
        if not decision.allowed:
            self._observe(name, outcome="denied", denied_reason=decision.reason)
            await self._audit_call(name, context, "denied", arguments_digest, decision.reason)
            await self._publish(name, context, allowed=False, ok=False, duration_ms=0.0,
                                error_kind=decision.reason)
            return finish(
                ok=False,
                content=f"The '{name}' tool was not permitted: {decision.reason}.",
                error_kind="policy_denied",
                allowed=False,
                policy_reason=decision.reason,
            )
        if decision.requires_confirmation and not confirmed:
            self._observe(name, outcome="needs_confirmation")
            # D6: give the human something to review beyond the raw arguments, when the tool can
            # produce one. A preview failure must never block confirmation itself -- the call is
            # already going to sit in confirm-required limbo either way, so this degrades to "no
            # diff" rather than turning a working confirmation flow into an error.
            diff_preview: str | None = None
            if spec.preview is not None:
                try:
                    diff_preview = await spec.preview(arguments, context)
                except Exception:
                    logger.exception("tool preview hook failed", extra={"tool": name})
            return finish(
                ok=False,
                content=f"The '{name}' tool requires the user's explicit confirmation before it "
                        f"can run ({decision.reason}). Ask the user to confirm.",
                error_kind="confirmation_required",
                policy_reason=decision.reason,
                needs_confirmation=True,
                arguments=arguments,
                diff_preview=diff_preview,
            )

        # 4. Sandboxed execution
        timeout = min(spec.timeout_s or self._default_timeout, context.deadline_s or spec.timeout_s)
        try:
            async with self._semaphore:
                raw = await asyncio.wait_for(tool.execute(arguments, context), timeout=timeout)
        except asyncio.TimeoutError:
            duration = (time.perf_counter() - started) * 1000.0
            self._observe(name, outcome="timeout", duration_ms=duration)
            await self._audit_call(name, context, "timeout", arguments_digest, "timeout")
            await self._publish(name, context, allowed=True, ok=False, duration_ms=duration,
                                error_kind="timeout")
            return finish(
                ok=False,
                content=f"The '{name}' tool timed out after {timeout:.0f}s.",
                error_kind=ToolTimeoutError.kind,
                policy_reason=decision.reason,
            )
        except ToolExecutionError as exc:
            duration = (time.perf_counter() - started) * 1000.0
            self._observe(name, outcome="error", duration_ms=duration)
            await self._audit_call(name, context, "error", arguments_digest, exc.kind)
            await self._publish(name, context, allowed=True, ok=False, duration_ms=duration,
                                error_kind=exc.kind)
            return finish(ok=False, content=f"The '{name}' tool failed: {exc.message}",
                          error_kind=exc.kind, policy_reason=decision.reason)
        except Exception as exc:
            # A tool implementation bug must not end the turn. It is reported to the model as a
            # failure and logged with a stack trace for the operator.
            duration = (time.perf_counter() - started) * 1000.0
            logger.exception("tool raised an unexpected exception", extra={"tool": name})
            self._observe(name, outcome="error", duration_ms=duration)
            await self._publish(name, context, allowed=True, ok=False, duration_ms=duration,
                                error_kind=type(exc).__name__)
            return finish(
                ok=False,
                content=f"The '{name}' tool failed unexpectedly ({type(exc).__name__}).",
                error_kind="tool_execution_error",
                policy_reason=decision.reason,
            )

        content = str(raw)
        if len(content) > spec.max_result_chars:
            # Truncation is marked so the model knows the result is partial rather than
            # believing it saw everything.
            content = (
                content[: spec.max_result_chars]
                + f"\n[…truncated at {spec.max_result_chars} characters]"
            )

        if "untrusted_content" in spec.tags:
            # This tool's output is attacker-influenceable external content that
            # deps.safety.guard_context_items never scanned (that only covers RAG
            # retrieval, run once up front) -- taint the shared ToolContext so any
            # PERSONAL-sensitivity side-effecting tool called later in this same
            # plan requires confirmation instead of auto-approving (M4). A call
            # scheduled concurrently with this one would run its policy check
            # before this mutation lands; the two batch schedulers
            # (`execute_many` below and `ExecutionEngine._taint_ordered`) defer
            # taint-gated calls until a tainting call in the same batch has
            # finished, so the check always observes it
            # (tests/security/test_concurrent_taint.py).
            context.tainted = True

        duration = (time.perf_counter() - started) * 1000.0
        self._observe(name, outcome="ok", duration_ms=duration)
        await self._audit_call(name, context, "success", arguments_digest, decision.reason)
        await self._publish(name, context, allowed=True, ok=True, duration_ms=duration)
        return finish(ok=True, content=content, policy_reason=decision.reason)

    async def execute_many(
        self,
        calls: Sequence[ToolCall],
        context: ToolContext,
        *,
        capabilities: Sequence[str] = (),
        confirmed: bool = False,
    ) -> list[tuple[ToolResult, ToolExecutionReport]]:
        """Run independent calls concurrently.

        Concurrency is bounded by the registry semaphore, so a model requesting twenty tools at
        once cannot exhaust the process. Ordering of results matches the input.
        """
        if not calls:
            return []

        async def run(batch: Sequence[ToolCall]) -> list[tuple[ToolResult, ToolExecutionReport]]:
            return list(await asyncio.gather(
                *(
                    self.execute(call, context, capabilities=capabilities, confirmed=confirmed)
                    for call in batch
                )
            ))

        # A taint-gated call (side-effecting, not workspace-local) must not share a concurrent
        # batch with an `untrusted_content` call: the taint lands when that call *returns*, after
        # the gated call's own policy check has already auto-approved it. Run everything else
        # concurrently first, then the gated calls one at a time, so each one's policy check
        # observes any taint produced before it. Only the mix is split; a clean batch keeps its
        # full concurrency. Mirrors `ExecutionEngine._taint_ordered`.
        specs = [self._spec_or_none(call.name) for call in calls]
        gated = [
            i for i, spec in enumerate(specs)
            if spec is not None and spec.side_effecting and not spec.workspace_local
        ]
        tainting = any(spec is not None and "untrusted_content" in spec.tags for spec in specs)
        if context.tainted or not tainting or not gated:
            return await run(calls)

        results: list[tuple[ToolResult, ToolExecutionReport] | None] = [None] * len(calls)
        first = [i for i in range(len(calls)) if i not in set(gated)]
        for index, outcome in zip(first, await run([calls[i] for i in first])):
            results[index] = outcome
        for index in gated:
            (results[index],) = await run([calls[index]])
        return [outcome for outcome in results if outcome is not None]

    def _spec_or_none(self, name: str) -> ToolSpec | None:
        tool = self._tools.get(name)
        return tool.spec if tool is not None and self._is_visible(tool.spec) else None

    # -- observation -------------------------------------------------------------------

    def _observe(
        self, name: str, *, outcome: str, duration_ms: float = 0.0, denied_reason: str = ""
    ) -> None:
        if self._metrics is None:
            return
        self._metrics.tool_calls_total.labels(tool=name, outcome=outcome).inc()
        if duration_ms:
            self._metrics.tool_duration.labels(tool=name).observe(duration_ms / 1000.0)
        if outcome == "denied":
            self._metrics.tool_denied_total.labels(tool=name, reason=denied_reason or "policy").inc()

    async def _publish(
        self, name: str, context: ToolContext, *, allowed: bool, ok: bool,
        duration_ms: float, error_kind: str | None = None,
    ) -> None:
        if self._events is None:
            return
        try:
            await self._events.publish(
                ToolInvoked(
                    tool=name,
                    allowed=allowed,
                    ok=ok,
                    duration_ms=duration_ms,
                    error_kind=error_kind,
                    user_id=context.user_id,
                    tainted_context=context.tainted,
                )
            )
        except Exception:  # pragma: no cover - defensive
            logger.debug("tool event publication failed", exc_info=True)

    async def _audit_call(
        self, name: str, context: ToolContext, outcome: str, digest: str, reason: str
    ) -> None:
        """Audit every attempt.

        Arguments are recorded as a digest, never verbatim: they can contain the user's private
        data, and an audit log is a long-lived, widely-readable artefact.
        """
        if self._audit is None:
            return
        from ..ports.runtime import AuditEvent

        try:
            await self._audit.record(
                AuditEvent(
                    action=f"tool.{name}",
                    actor=context.user_id.value,
                    resource=name,
                    outcome=outcome,
                    correlation_id=context.correlation_id,
                    detail={"arguments_digest": digest, "reason": reason,
                            "tainted": context.tainted},
                )
            )
        except Exception:  # pragma: no cover - defensive
            logger.debug("tool audit write failed", exc_info=True)

    def describe(self) -> list[dict[str, Any]]:
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "category": str(spec.category),
                "sensitivity": spec.sensitivity.name,
                "side_effecting": spec.side_effecting,
                "requires_confirmation": spec.requires_confirmation,
                "required_capability": spec.required_capability,
                "timeout_s": spec.timeout_s,
                "version": spec.version,
                "parameters": [p.name for p in spec.parameters],
            }
            for spec in sorted((t.spec for t in self._tools.values()), key=lambda s: s.name)
        ]
