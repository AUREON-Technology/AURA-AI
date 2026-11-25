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
from typing import Any, Awaitable, Callable, Iterable, Mapping, Sequence

from ..domain.errors import (
    ToolExecutionError,
    ToolNotFoundError,
    ToolTimeoutError,
    ValidationError,
)
from ..domain.events import ToolInvoked
from ..domain.llm import DataClassification
from ..domain.messages import ToolCall, ToolResult
from ..domain.output_schema import output_errors
from ..domain.tools import NESTED_DISPATCH_TAG, ToolPolicyDecision, ToolSensitivity, ToolSpec
from ..llm.sensitivity import escalate
from ..observability.logging import get_logger
from ..ports.coding import TOOL_CALL_CONFIRMED
from ..ports.runtime import Tool, ToolContext
from .policy import ToolPolicy
from .schema import validate_arguments

#: The daemon waits up to five minutes for a human to approve a host-routed call
#: (aura_hostd/lib/src/approval_queue.dart's approvalTimeout) -- this is that window plus slack,
#: the floor on a host-routed tool's server-side budget (audit FSA-041). The Engineer loop's
#: deadline must cover it on top of the longest tool run (settings.reasoning.engineer_loop_deadline_s).
HOST_APPROVAL_WINDOW_S = 330.0

#: The most a tool's ``preview`` hook may take. It runs before the human is even asked, outside the
#: tool's own timeout and concurrency slot, and the one that exists reads the forge once per file;
#: a hook that cannot finish in this long degrades to "no diff" like one that raised.
PREVIEW_TIMEOUT_S = 15.0

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
        host_routed: Callable[[ToolContext], Awaitable[bool]] | None = None,
        result_guard: Callable[[str, str, ToolContext], Awaitable[Any]] | None = None,
    ) -> None:
        self._tools: dict[str, Tool] = {}
        # ``await result_guard(content, tool_name, context)`` -> a ``SafetyDecision`` (the container
        # passes ``SafetyPipeline.guard_tool_result``). Every successful tool result goes through it
        # before the model, a UI or the transcript sees it: credentials are redacted, and output
        # that looks like an injection or cannot be checked is withheld. ``None`` = unguarded.
        self._result_guard = result_guard
        self._policy = policy or ToolPolicy()
        # An allowlist, when non-empty, is exclusive: only listed tools are visible at all.
        self._allowlist = frozenset(allowlist)
        self._denylist = frozenset(denylist)
        self._semaphore = asyncio.Semaphore(max(1, max_parallel))
        self._default_timeout = default_timeout_s
        self._metrics = metrics
        self._events = event_bus
        self._audit = audit
        # Whether a call for this context runs on the user's paired desktop rather than in the
        # sandbox; None when no desktop routing exists. Only asked for workspace_local side
        # effects, the one place the answer changes the policy (ToolPolicy.evaluate) and the
        # timeout (call_timeout) -- the latter for confirmed calls too (tool audit T-02).
        self._host_routed = host_routed
        #: The least a host-routed call may wait: the daemon's approval window plus slack.
        self.host_approval_window_s = HOST_APPROVAL_WINDOW_S

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

    def names(self) -> tuple[str, ...]:
        """Every registered tool name, sorted (no capability filtering -- see ``available``)."""
        return tuple(sorted(self._tools))

    # -- budgets -----------------------------------------------------------------------

    def call_timeout(self, spec: ToolSpec, context: ToolContext, *, host_routed: bool) -> float:
        """Server-side seconds one call of ``spec`` may take for this caller.

        The tool's own budget, capped by the caller's per-call ``deadline_s`` (set per persona by
        ``AIKernel._tool_context``). On the user's own machine the daemon may first hold the call
        for up to its approval window (aura_hostd approval_queue.dart) and only then run it, so
        that wait is added on top. It used to be ``max(budget, window)``, which left a command
        approved after a minute four minutes to run, not its declared ten (tool audit T-01). The
        older bug was worse: cutting the call off at 15 s told the model a write had failed
        while the daemon kept waiting, and a late Approve then executed with nothing upstream
        expecting it (FSA-041).
        """
        timeout = min(spec.timeout_s or self._default_timeout, context.deadline_s or spec.timeout_s)
        if host_routed:
            timeout += self.host_approval_window_s
        return timeout

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
        arguments_digest = _arguments_digest(call.arguments)

        def finish(
            *, ok: bool, content: str, error_kind: str | None = None, allowed: bool = True,
            policy_reason: str = "", needs_confirmation: bool = False,
            arguments: Mapping[str, Any] | None = None, diff_preview: str | None = None,
            truncation: Mapping[str, Any] | None = None,
        ) -> tuple[ToolResult, ToolExecutionReport]:
            duration = (time.perf_counter() - started) * 1000.0
            metadata: dict[str, Any] = {"allowed": allowed, "policy_reason": policy_reason}
            if truncation:
                metadata["truncation"] = dict(truncation)
            result = ToolResult(
                call_id=call.id,
                name=name,
                ok=ok,
                content=content,
                duration_ms=duration,
                error_kind=error_kind,
                metadata=metadata,
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
            # The name is whatever the model wrote. It must not become a metrics label (every
            # distinct value would be a new time series, kept for the life of the process), nor an
            # unbounded string in the audit log or in the reply.
            shown = name if len(name) <= _MAX_NAME_SHOWN else name[:_MAX_NAME_SHOWN] + "..."
            self._observe(_UNKNOWN_TOOL_LABEL, outcome="not_found")
            await self._audit_call(shown, context, "denied", arguments_digest, "tool_not_found")
            return finish(
                ok=False,
                content=f"Tool '{shown}' is not available. Available tools: "
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
            await self._audit_call(name, context, "denied", arguments_digest, "invalid_arguments")
            # Argument values are not logged (they can carry user content); the keys and the
            # validator's own message are enough to tell a bad name from a bad value.
            logger.warning(
                "tool arguments rejected",
                extra={"tool": name, "argument_keys": sorted(map(str, call.arguments)) if isinstance(call.arguments, Mapping) else None,
                       "reason": exc.message[:300]},
            )
            return finish(
                ok=False,
                # The model needs to know precisely what was wrong to correct itself.
                content=f"Invalid arguments for '{name}': {exc.message}. "
                        f"Expected schema: {json.dumps(spec.json_schema())}",
                error_kind=exc.kind,
                policy_reason="invalid_arguments",
            )

        # 3. Policy
        # Where the call runs and what policy is told are two questions (tool audit T-02).
        # ``on_desktop`` is routing: it sizes the timeout and keeps the call off the sandbox
        # semaphore. A server-side confirmation does not change it -- the daemon still applies
        # its own command policy and may hold the call for a local human's approval, so a
        # confirmed call needs the approval window as much as any other (otherwise FSA-041
        # reopens: the server gives up at 15 s while the daemon keeps waiting). The policy input
        # keeps its old meaning, False once confirmed, so no decision or audit record changes.
        on_desktop = bool(
            self._host_routed is not None
            and spec.workspace_local
            and spec.side_effecting
            and await self._host_routed(context)
        )
        host_routed = on_desktop and not confirmed
        decision: ToolPolicyDecision = self._policy.evaluate(
            spec, context=context, capabilities=capabilities, confirmed=confirmed,
            host_routed=host_routed, arguments=arguments,
        )
        if not decision.allowed:
            self._observe(name, outcome="denied", denied_reason=decision.reason)
            await self._audit_call(name, context, "denied", arguments_digest, decision.reason,
                                   decision=decision)
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
            # A held call is an attempt like a refused one: "a tainted turn tried to write and was
            # stopped for confirmation" is the event a reviewer looks for, and until now it left
            # only a metric and a same-user response.
            await self._audit_call(name, context, "needs_confirmation", arguments_digest,
                                   decision.reason, decision=decision)
            # D6: give the human something to review beyond the raw arguments, when the tool can
            # produce one. A preview failure must never block confirmation itself -- the call is
            # already going to sit in confirm-required limbo either way, so this degrades to "no
            # diff" rather than turning a working confirmation flow into an error.
            diff_preview: str | None = None
            if spec.preview is not None:
                try:
                    diff_preview = await asyncio.wait_for(
                        spec.preview(arguments, context), timeout=PREVIEW_TIMEOUT_S
                    )
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
        timeout = self.call_timeout(spec, context, host_routed=on_desktop)
        confirmed_token = TOOL_CALL_CONFIRMED.set(bool(confirmed))
        try:
            if on_desktop or NESTED_DISPATCH_TAG in spec.tags:
                # The work happens on the user's own machine, and most of the wait is a human
                # deciding. Holding one of this process's few sandbox slots for that would let a
                # handful of pending approvals stall every other user's tool calls here.
                # A tool that dispatches further calls through this registry (delegation) is held
                # to the same rule for a different reason: its children need slots, and each
                # parent holding one while it waits is a deadlock once `max_parallel` of them run.
                raw = await asyncio.wait_for(tool.execute(arguments, context), timeout=timeout)
            else:
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
            await self._audit_call(name, context, "error", arguments_digest, "tool_execution_error")
            await self._publish(name, context, allowed=True, ok=False, duration_ms=duration,
                                error_kind=type(exc).__name__)
            return finish(
                ok=False,
                content=f"The '{name}' tool failed unexpectedly ({type(exc).__name__}).",
                error_kind="tool_execution_error",
                policy_reason=decision.reason,
            )
        finally:
            TOOL_CALL_CONFIRMED.reset(confirmed_token)

        content = str(raw)
        # ADR-030 Phase 4: tool output is whatever the tool reached -- a calendar, a file, a
        # connected system -- so from here on this turn's generations are SENSITIVE and stay
        # off any provider that may not see that. Every tool, not a list of the private ones: a
        # list is the thing that goes stale when the next tool is added.
        escalate(DataClassification.SENSITIVE, "tool_output")
        if spec.output_schema is not None:
            # ADR-025: a result that claims a shape is held to it before the model or a UI sees it.
            problems = _output_problems(spec.output_schema, content)
            if problems:
                duration = (time.perf_counter() - started) * 1000.0
                self._observe(name, outcome="invalid_output", duration_ms=duration)
                await self._audit_call(name, context, "error", arguments_digest, "invalid_output")
                await self._publish(name, context, allowed=True, ok=False, duration_ms=duration,
                                    error_kind="invalid_output")
                return finish(
                    ok=False,
                    content=f"The '{name}' tool returned a result that does not match its declared "
                            f"output schema ({'; '.join(problems)}).",
                    error_kind="invalid_output",
                    policy_reason=decision.reason,
                )
        truncation: dict[str, Any] | None = None
        if len(content) > spec.max_result_chars and spec.output_schema is not None:
            # A result that claims a shape cannot be cut mid-JSON: the model, a UI view and the
            # AP8 gate would all receive something that no longer parses. Drop whole list items
            # instead -- the schema subset has no minItems, so a shorter list still matches it --
            # and flag it (ADR-025): in the result itself when its schema admits a `truncated`
            # field, and always in the ToolResult metadata.
            try:
                fitted = _fit_json(content, spec.max_result_chars, spec.output_schema)
            except RecursionError:
                fitted = None  # nested past what the interpreter can walk: too large, not a crash
            if fitted is None:
                duration = (time.perf_counter() - started) * 1000.0
                self._observe(name, outcome="result_too_large", duration_ms=duration)
                await self._audit_call(name, context, "error", arguments_digest, "result_too_large")
                await self._publish(name, context, allowed=True, ok=False, duration_ms=duration,
                                    error_kind="result_too_large")
                return finish(
                    ok=False,
                    content=f"The '{name}' tool's result is larger than {spec.max_result_chars} "
                            "characters even with its lists shortened; narrow the request.",
                    error_kind="result_too_large",
                    policy_reason=decision.reason,
                )
            content, truncation = fitted
            if truncation:
                logger.info("structured tool result shortened to fit", extra={"tool": name, **truncation})
        elif len(content) > spec.max_result_chars:
            # Truncation is marked so the model knows the result is partial rather than
            # believing it saw everything.
            content = (
                content[: spec.max_result_chars]
                + f"\n[…truncated at {spec.max_result_chars} characters]"
            )

        if self._result_guard is not None:
            try:
                verdict = await self._result_guard(content, name, context)
            except Exception:
                # Fails CLOSED, like every exfiltration guard (ADR-006): output that could not be
                # checked for credentials is not handed on.
                logger.exception("tool result guard failed", extra={"tool": name})
                duration = (time.perf_counter() - started) * 1000.0
                self._observe(name, outcome="guard_error", duration_ms=duration)
                await self._audit_call(name, context, "error", arguments_digest, "output_guard_error")
                await self._publish(name, context, allowed=True, ok=False, duration_ms=duration,
                                    error_kind="output_withheld")
                return finish(
                    ok=False,
                    content=f"The '{name}' tool's output could not be checked and was withheld.",
                    error_kind="output_withheld",
                    policy_reason=decision.reason,
                )
            if not verdict.allowed:
                reasons = ", ".join(verdict.report.reasons()) or "safety guard"
                if getattr(verdict, "tainted", False):
                    context.tainted = True  # suspected injection: later side effects need confirmation
                duration = (time.perf_counter() - started) * 1000.0
                self._observe(name, outcome="output_withheld", duration_ms=duration)
                await self._audit_call(name, context, "error", arguments_digest, "output_withheld")
                await self._publish(name, context, allowed=True, ok=False, duration_ms=duration,
                                    error_kind="output_withheld")
                return finish(
                    ok=False,
                    content=f"The '{name}' tool's output was withheld by a safety guard ({reasons}).",
                    error_kind="output_withheld",
                    policy_reason=decision.reason,
                )
            if verdict.text != content:
                logger.info("tool result redacted by the output guard", extra={"tool": name})
                if spec.output_schema is not None:
                    # The shape check ran before the guard, and the guard rewrites text: NFKC
                    # folds a full-width quote into a real one (a string value then closes itself
                    # and adds keys), and the size guard cuts mid-document. What goes on has to
                    # still be the result that was checked, so check again, and fail closed.
                    problems = _rewrite_problems(spec.output_schema, content, verdict.text)
                    if problems:
                        duration = (time.perf_counter() - started) * 1000.0
                        self._observe(name, outcome="invalid_output", duration_ms=duration)
                        await self._audit_call(name, context, "error", arguments_digest,
                                               "invalid_output")
                        await self._publish(name, context, allowed=True, ok=False,
                                            duration_ms=duration, error_kind="invalid_output")
                        return finish(
                            ok=False,
                            content=f"The '{name}' tool's result no longer matches its declared "
                                    "output schema after safety processing "
                                    f"({'; '.join(problems)}), so it was withheld.",
                            error_kind="invalid_output",
                            policy_reason=decision.reason,
                        )
                content = verdict.text

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
        await self._audit_call(name, context, "success", arguments_digest, decision.reason,
                               decision=decision)
        await self._publish(name, context, allowed=True, ok=True, duration_ms=duration)
        return finish(ok=True, content=content, policy_reason=decision.reason, truncation=truncation)

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
        self, name: str, context: ToolContext, outcome: str, digest: str, reason: str,
        decision: ToolPolicyDecision | None = None,
    ) -> None:
        """Audit every attempt.

        Arguments are recorded as a digest, never verbatim: they can contain the user's private
        data, and an audit log is a long-lived, widely-readable artefact.
        """
        if self._audit is None:
            return
        from ..ports.runtime import AuditEvent

        detail: dict[str, Any] = {"arguments_digest": digest, "reason": reason,
                                  "tainted": context.tainted}
        if decision is not None:
            # Which declarative rule fired and under which review profile (ADR-041); the rule's
            # id and the profile name are operator-authored, never argument values.
            for key in ("rule", "autonomy"):
                if key in decision.audit_fields:
                    detail[key] = decision.audit_fields[key]
        try:
            await self._audit.record(
                AuditEvent(
                    action=f"tool.{name}",
                    actor=context.user_id.value,
                    resource=name,
                    outcome=outcome,
                    correlation_id=context.correlation_id,
                    detail=detail,
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


#: What a model-chosen tool name is cut to before it is logged, audited or echoed back, and the
#: one metrics label every unknown name shares.
_MAX_NAME_SHOWN = 120
_UNKNOWN_TOOL_LABEL = "_unknown"


def _arguments_digest(arguments: Any) -> str:
    """Digest of the call's arguments for the audit log; never raises.

    Computed before anything else in ``execute``, so a value that cannot be serialized (not an
    object, keys of mixed types, nesting past the interpreter's recursion limit) used to escape
    as an exception ahead of validation, audit and the reply -- the model's own malformed call
    ending the turn. It is digested as unserializable instead and refused by validation.
    """
    try:
        encoded = json.dumps(dict(arguments), sort_keys=True, default=str)
    except (TypeError, ValueError, RecursionError):
        encoded = f"<unserializable {type(arguments).__name__}>"
    return hashlib.sha256(encoded.encode()).hexdigest()[:16]


def _output_problems(schema: Mapping[str, Any], content: str) -> list[str]:
    """Why ``content`` is not a JSON value matching ``schema`` (empty when it is)."""
    try:
        value = json.loads(content)
    except RecursionError:
        # `RecursionError` is not a `ValueError`. A connected system's result nested a few
        # thousand levels deep used to escape `execute` here, after the tool had already acted.
        return ["result is nested too deeply"]
    except ValueError:
        return ["result is not JSON"]
    return output_errors(schema, value)


def _rewrite_problems(schema: Mapping[str, Any], before: str, after: str) -> list[str]:
    """Why ``after`` (``before`` rewritten by the output guard) may not stand in for it.

    Empty when ``after`` still matches ``schema`` *and* has the same structure as ``before``
    (same keys, same list lengths, strings still strings): a guard may redact or normalise a
    string value, never add a key or cut the document.
    """
    problems = _output_problems(schema, after)
    if problems:
        return problems
    try:
        same = _same_shape(json.loads(before), json.loads(after))
    except (ValueError, RecursionError):
        return ["result could not be compared after safety processing"]
    return [] if same else ["its structure was changed"]


def _same_shape(before: Any, after: Any) -> bool:
    """Whether two parsed JSON values differ at most in their string contents (iterative)."""
    stack: list[tuple[Any, Any]] = [(before, after)]
    while stack:
        left, right = stack.pop()
        if isinstance(left, dict):
            if not isinstance(right, dict) or left.keys() != right.keys():
                return False
            stack.extend((left[key], right[key]) for key in left)
        elif isinstance(left, list):
            if not isinstance(right, list) or len(left) != len(right):
                return False
            stack.extend(zip(left, right))
        elif isinstance(left, str):
            if not isinstance(right, str):
                return False
        elif type(left) is not type(right) or not (left == right or left != left):
            return False  # (``left != left``: a NaN is the same NaN)
    return True


#: Work budget for shortening one structured result: how many lists may be shortened before it
#: is declared too large. Generous -- each pass empties or shortens a whole list -- but bounded,
#: since the result came from a connected system and its shape is not ours to trust.
_FIT_PASSES = 256
#: How deep into a result lists are looked for. Deeper than any accepted output schema (6), so a
#: declared shape is always fully reachable; the bound only stops a pathological payload.
_FIT_DEPTH = 32


def _fit_json(content: str, limit: int, schema: Mapping[str, Any]) -> tuple[str, dict[str, Any] | None] | None:
    """``content`` with trailing list items dropped until it serializes within ``limit``.

    The largest list (by serialized size) is shortened first -- the rows of a search, not the
    envelope around them -- and a binary search finds how many of its items still fit. A top-level
    object whose schema admits it gets ``"truncated": true``. Returns the text and a summary
    (``truncated``, items ``kept`` and ``total`` across the shortened lists), or ``None`` when the
    result is not JSON or cannot fit even with every list emptied. A result that fits once
    re-serialized compactly is returned whole, with no summary: nothing was dropped.
    """
    try:
        value = json.loads(content)
    except ValueError:
        return None

    def dump(data: Any) -> str:
        return json.dumps(data, ensure_ascii=False)

    whole = dump(value)
    if len(whole) <= limit:
        return whole, None
    if isinstance(value, dict) and _admits_truncated_flag(schema):
        value["truncated"] = True

    kept = total = 0
    for _ in range(_FIT_PASSES):
        text = dump(value)
        if len(text) <= limit:
            return text, {"truncated": True, "kept": kept, "total": total}
        target = _largest_list(value)
        if target is None:
            return None
        items = list(target)
        keep_low, keep_high = 0, len(items) - 1  # at least one item goes, or this pass did nothing
        while keep_low < keep_high:
            middle = (keep_low + keep_high + 1) // 2
            target[:] = items[:middle]
            if len(dump(value)) <= limit:
                keep_low = middle
            else:
                keep_high = middle - 1
        target[:] = items[:keep_low]
        kept += keep_low
        total += len(items)
    return None


def _admits_truncated_flag(schema: Mapping[str, Any]) -> bool:
    declared = (schema.get("properties") or {}).get("truncated")
    if declared is not None:
        types = declared.get("type")
        return types == "boolean" or (isinstance(types, (list, tuple)) and "boolean" in types)
    return schema.get("additionalProperties") is not False


def _largest_list(value: Any) -> list | None:
    """The non-empty list with the longest serialization in ``value`` (``value`` itself included)."""
    best: list | None = None
    best_size = -1
    stack: list[tuple[Any, int]] = [(value, 0)]
    while stack:
        node, level = stack.pop()
        if isinstance(node, list):
            if node:
                size = len(json.dumps(node, ensure_ascii=False))
                if size > best_size:
                    best, best_size = node, size
            children = node
        elif isinstance(node, dict):
            children = list(node.values())
        else:
            continue
        if level < _FIT_DEPTH:
            stack.extend((child, level + 1) for child in children if isinstance(child, (list, dict)))
    return best
