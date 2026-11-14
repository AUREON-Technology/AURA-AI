"""Anthropic Claude provider -- the one sanctioned closed-model adapter (ADR-033).

Everything about *whether* this provider may run lives elsewhere: ``ProviderConfig._gate_claude``
refuses it unless ``AURA_ALLOW_CLAUDE_INFERENCE=true``, and the router keeps it to what its data
ceiling admits (``internal`` by default, so documents, memory, tool output and Engineer
workspaces stay local), never sends it a user's image, and audits every generation it serves.
This module only translates AURA's request shape into Anthropic's Messages API.

Built on Anthropic's official SDK, imported lazily inside :meth:`ClaudeProvider._get_client`, so
``aura_core`` stays importable without it and the architecture tests can confine the import to
this one file (``SANCTIONED_SDK_IMPORTERS``).

Choices that differ from the OpenAI-compatible adapter, and why:

* **The key is passed explicitly, and an empty one is refused.** The SDK would otherwise resolve
  a credential from the environment or an ``ant auth login`` profile on its own, which would
  bypass the per-slot ``AURA_PROVIDER_<NAME>_API_KEY_ENV`` path the gate is built around.
* **No SDK retries** (``max_retries=0``). The router owns retry and failover: it benches a
  provider on ``429`` for the reset the response names and trips its circuit on repeated 5xx. An
  SDK retrying underneath would multiply wall-clock time against the turn's deadline.
* **Every call streams**, including :meth:`complete`, which collects the final message. A long
  hard-task generation would otherwise risk the HTTP read timeout.
* **No ``temperature``/``top_p``.** Current Claude models reject sampling parameters; quality is
  steered with ``output_config.effort`` instead (``high`` by default here, since ADR-033 routes
  only demanding turns to this provider). Override it with
  ``AURA_PROVIDER_<NAME>_EXTRA_BODY='{"output_config": {"effort": "xhigh"}}'``.
* **Thinking headroom.** Current models think adaptively and thinking tokens count against
  ``max_tokens``. AURA's output budgets were sized for the visible answer on a CPU model, so the
  request asks for that budget *plus* :data:`THINKING_HEADROOM_TOKENS`, capped at the provider's
  declared ``max_output_tokens``. Without it, a 700-token planner budget could be spent entirely
  on thinking and return nothing.
* **Refusal fallback on by default.** A safety-classifier decline is retried server-side on a
  model Anthropic chooses (``fallbacks: "default"``), inside the same call. Disable it with
  ``AURA_PROVIDER_<NAME>_EXTRA_BODY='{"fallbacks": null}'``.
* **Prompt caching on.** AURA's system prompt is stable across turns, so automatic caching cuts
  the repeated-prefix cost of every follow-up.
"""

from __future__ import annotations

import re
import time
from typing import Any, AsyncIterator, Mapping

from ...domain.errors import ProviderError
from ...domain.identifiers import ToolCallId
from ...domain.llm import (
    CompletionRequest,
    CompletionResponse,
    FinishReason,
    ProviderCapabilities,
    StreamChunk,
    TokenUsage,
    ToolChoice,
)
from ...domain.messages import Conversation, Role, ToolCall
from ...ports.llm import HealthStatus
from .base import BaseProvider

__all__ = ["ClaudeProvider", "THINKING_HEADROOM_TOKENS", "to_claude_messages"]

#: Added to the visible-answer budget so adaptive thinking cannot starve the answer.
THINKING_HEADROOM_TOKENS = 8000

#: Beta that enables server-side refusal fallback with ``fallbacks: "default"``.
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

_DEFAULT_EFFORT = "high"

_STOP_REASONS = {
    "end_turn": FinishReason.STOP,
    "stop_sequence": FinishReason.STOP,
    "pause_turn": FinishReason.STOP,
    "max_tokens": FinishReason.LENGTH,
    "model_context_window_exceeded": FinishReason.LENGTH,
    "tool_use": FinishReason.TOOL_CALLS,
    "refusal": FinishReason.CONTENT_FILTER,
}

_TOOL_ID_RE = re.compile(r"[^a-zA-Z0-9_-]")


def _tool_id(raw: str, index: int) -> str:
    """Anthropic requires ``^[a-zA-Z0-9_-]+$``; ids minted by other providers may not match."""
    cleaned = _TOOL_ID_RE.sub("_", raw or "")
    return cleaned or f"call_{index}"


def to_claude_messages(conversation: Conversation) -> tuple[str, list[dict[str, Any]]]:
    """Split a conversation into (system, messages) in the Messages API shape.

    * System messages are joined into the top-level ``system`` field.
    * An assistant message with tool calls becomes text plus ``tool_use`` blocks.
    * Tool results become ``tool_result`` blocks in a user message, merged when consecutive --
      but only for a call this conversation actually declared. A tool message with no matching
      ``tool_use`` (the kernel's planner runs tools itself, ADR-006) is rendered as fenced text
      instead, because the API rejects an orphaned ``tool_result``.
    * The first message must be a user message; a leading assistant turn gets a minimal one.

    Trust channels collapse here exactly as in :func:`base.flatten_conversation`: the context
    engine already rendered untrusted material into nonce-fenced blocks inside the content.
    """
    system_parts: list[str] = []
    messages: list[dict[str, Any]] = []
    declared: set[str] = set()

    def append(role: str, blocks: list[dict[str, Any]]) -> None:
        if not blocks:
            return
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"].extend(blocks)
        else:
            messages.append({"role": role, "content": blocks})

    for index, message in enumerate(conversation):
        if message.role is Role.SYSTEM:
            if message.content.strip():
                system_parts.append(message.content)
            continue
        if message.role is Role.TOOL:
            call_id = _tool_id(message.tool_call_id.value if message.tool_call_id else "", index)
            if call_id in declared:
                append("user", [{
                    "type": "tool_result",
                    "tool_use_id": call_id,
                    "content": message.content or "(empty result)",
                }])
            else:
                label = message.name or "tool"
                append("user", [{"type": "text", "text": f"[{label} result]\n{message.content}"}])
            continue
        if message.role is Role.ASSISTANT:
            blocks: list[dict[str, Any]] = []
            if message.content.strip():
                blocks.append({"type": "text", "text": message.content})
            for call_index, call in enumerate(message.tool_calls):
                call_id = _tool_id(call.id.value, call_index)
                declared.add(call_id)
                blocks.append({
                    "type": "tool_use",
                    "id": call_id,
                    "name": call.name,
                    "input": dict(call.arguments),
                })
            append("assistant", blocks)
            continue
        if message.content.strip():
            append("user", [{"type": "text", "text": message.content}])

    if not messages or messages[0]["role"] != "user":
        messages.insert(0, {"role": "user", "content": [{"type": "text", "text": "Continue."}]})
    return "\n\n".join(system_parts), messages


class ClaudeProvider(BaseProvider):
    def __init__(
        self,
        *,
        name: str,
        model: str,
        api_key: str,
        capabilities: ProviderCapabilities,
        timeout_s: float = 60.0,
        health_timeout_s: float = 5.0,
        extra_body: Mapping[str, Any] | None = None,
        http_client: Any = None,
    ) -> None:
        super().__init__(
            name=name, capabilities=capabilities, timeout_s=timeout_s,
            health_timeout_s=health_timeout_s,
        )
        self._model = model
        self._api_key = api_key
        extra = dict(extra_body or {})
        self._output_config: dict[str, Any] = dict(extra.pop("output_config", None) or {})
        self._output_config.setdefault("effort", _DEFAULT_EFFORT)
        self._fallbacks: Any = extra.pop("fallbacks", "default")
        self._extra_body = extra
        #: An ``httpx2.AsyncClient`` for the SDK to use instead of its own -- the tests pass one
        #: with a mock transport so the real SDK's request building and SSE parsing are exercised.
        self._http_client = http_client

    # -- client ------------------------------------------------------------------------

    async def _get_client(self) -> Any:
        if self._client is None:
            if not self._api_key:
                raise ProviderError(
                    f"{self.name}: no API key -- the variable named by "
                    f"AURA_PROVIDER_{self.name.upper()}_API_KEY_ENV is empty or unset",
                    provider=self.name,
                    model=self._model,
                    retryable=False,
                    details={"operation": "configure"},
                )
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - environment dependent
                raise ProviderError(
                    f"{self.name}: the 'anthropic' package is required for kind=claude",
                    provider=self.name,
                    retryable=False,
                ) from exc
            options: dict[str, Any] = {}
            if self._http_client is not None:
                options["http_client"] = self._http_client
            self._client = anthropic.AsyncAnthropic(
                api_key=self._api_key, timeout=self._timeout_s, max_retries=0, **options
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    # -- payload -----------------------------------------------------------------------

    def _max_tokens(self, request: CompletionRequest) -> int:
        answer = self._effective_max_output(request)
        return min(answer + THINKING_HEADROOM_TOKENS, self._capabilities.max_output_tokens)

    def _kwargs(self, request: CompletionRequest) -> dict[str, Any]:
        system, messages = to_claude_messages(request.conversation)
        output_config = dict(self._output_config)
        if request.response_schema and self._capabilities.supports_json_mode:
            output_config["format"] = {
                "type": "json_schema",
                "schema": dict(request.response_schema),
            }
        kwargs: dict[str, Any] = {
            "model": request.model or self._model,
            "max_tokens": self._max_tokens(request),
            "messages": messages,
            "output_config": output_config,
            "cache_control": {"type": "ephemeral"},
        }
        if system:
            kwargs["system"] = system
        if request.sampling.stop:
            kwargs["stop_sequences"] = list(request.sampling.stop)
        if request.tools and self._capabilities.supports_tools:
            kwargs["tools"] = [self._tool(t) for t in request.tools]
            # Current models reject forced tool use; REQUIRED degrades to auto, and the
            # kernel never executes a model's tool calls anyway (ADR-006).
            kwargs["tool_choice"] = {
                "type": "none" if request.tool_choice is ToolChoice.NONE else "auto"
            }
        if self._fallbacks is not None:
            kwargs["fallbacks"] = self._fallbacks
            kwargs["betas"] = [_FALLBACK_BETA]
        if self._extra_body:
            kwargs["extra_body"] = dict(self._extra_body)
        return kwargs

    @staticmethod
    def _tool(declaration: Mapping[str, Any]) -> dict[str, Any]:
        """An OpenAI-shaped function declaration (``tools/schema.py``) in Anthropic's shape."""
        function = declaration.get("function", declaration)
        tool: dict[str, Any] = {
            "name": function.get("name", ""),
            "input_schema": dict(
                function.get("parameters") or {"type": "object", "properties": {}}
            ),
        }
        if function.get("description"):
            tool["description"] = function["description"]
        return tool

    # -- response ----------------------------------------------------------------------

    def _parse(self, message: Any) -> tuple[str, tuple[ToolCall, ...], TokenUsage | None, FinishReason]:
        texts: list[str] = []
        calls: list[ToolCall] = []
        for block in getattr(message, "content", None) or ():
            kind = getattr(block, "type", "")
            if kind == "text":
                texts.append(block.text)
            elif kind == "tool_use":
                arguments = block.input if isinstance(block.input, Mapping) else {"_value": block.input}
                calls.append(ToolCall(id=ToolCallId(block.id), name=block.name, arguments=dict(arguments)))
            # thinking / fallback / server-tool blocks carry nothing the caller shows.
        usage_raw = getattr(message, "usage", None)
        usage = None
        if usage_raw is not None:
            prompt = (
                int(getattr(usage_raw, "input_tokens", 0) or 0)
                + int(getattr(usage_raw, "cache_read_input_tokens", 0) or 0)
                + int(getattr(usage_raw, "cache_creation_input_tokens", 0) or 0)
            )
            usage = TokenUsage(
                prompt_tokens=prompt,
                completion_tokens=int(getattr(usage_raw, "output_tokens", 0) or 0),
            )
        stop = getattr(message, "stop_reason", None) or "end_turn"
        finish = _STOP_REASONS.get(stop, FinishReason.STOP)
        if calls and finish is FinishReason.STOP:
            finish = FinishReason.TOOL_CALLS
        return "".join(texts), tuple(calls), usage, finish

    # -- completion --------------------------------------------------------------------

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        started = time.perf_counter()
        client = await self._get_client()
        try:
            async with client.beta.messages.stream(**self._kwargs(request)) as stream:
                message = await stream.get_final_message()
        except ProviderError:
            raise
        except Exception as exc:
            raise self._translate(exc, operation="complete") from exc

        text, tool_calls, usage, finish = self._parse(message)
        estimated = usage is None
        if usage is None:
            usage = self._estimate_usage(request, text)
        return CompletionResponse(
            text=text,
            provider=self.name,
            model=getattr(message, "model", None) or self._model,
            finish_reason=finish,
            usage=usage,
            tool_calls=tool_calls,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            cost_usd=self._cost(usage),
            raw_metadata={
                "usage_estimated": estimated,
                "stop_reason": getattr(message, "stop_reason", None),
            },
        )

    # -- streaming ---------------------------------------------------------------------

    async def stream(self, request: CompletionRequest) -> AsyncIterator[StreamChunk]:
        client = await self._get_client()
        # Open the connection before yielding anything: the port contract requires connection
        # and HTTP failures to raise here, while the router can still fail over.
        try:
            manager = client.beta.messages.stream(**self._kwargs(request))
            stream = await manager.__aenter__()
        except Exception as exc:
            raise self._translate(exc, operation="stream") from exc

        try:
            async for event in stream:
                if getattr(event, "type", "") == "text":
                    if event.text:
                        yield StreamChunk(delta=event.text, provider=self.name, model=self._model)
            message = await stream.get_final_message()
            _, tool_calls, usage, finish = self._parse(message)
            if usage is None:
                usage = TokenUsage(
                    prompt_tokens=request.conversation.approx_tokens(),
                    completion_tokens=1,
                )
            yield StreamChunk(
                tool_calls=tool_calls,
                finish_reason=finish,
                usage=usage,
                provider=self.name,
                model=getattr(message, "model", None) or self._model,
            )
        except ProviderError:
            raise
        except Exception as exc:
            raise self._translate(exc, operation="stream") from exc
        finally:
            await manager.__aexit__(None, None, None)

    # -- health ------------------------------------------------------------------------

    async def health(self) -> HealthStatus:
        """Look the configured model up in the Models API: authenticated, and spends no tokens."""
        started = time.perf_counter()
        try:
            client = await self._get_client()
            await client.models.retrieve(self._model, timeout=self._health_timeout_s)
            return HealthStatus(True, "ok", (time.perf_counter() - started) * 1000.0)
        except ProviderError:
            return HealthStatus(False, "no API key", 0.0)
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            detail = f"HTTP {status}" if status else type(exc).__name__
            return HealthStatus(False, detail, (time.perf_counter() - started) * 1000.0)
