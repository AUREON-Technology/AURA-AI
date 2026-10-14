"""OpenAI-compatible chat provider -- the universal local-inference adapter (ADR-003).

vLLM, Ollama's ``/v1`` surface, text-generation-inference, llama.cpp's server, LM Studio,
Together, Groq, Fireworks, DeepInfra, OpenRouter, and OpenAI itself all expose
``POST /v1/chat/completions`` with the same request and SSE shapes. One well-tested adapter
therefore covers every local serving option and most cloud ones, which is precisely why the
protocol was chosen as the contract rather than inventing one.

Streaming contract (from ``ports/llm.py``): nothing is yielded until the upstream connection is
established. A connection failure must surface *before* the first chunk, because after that
point failover would duplicate output -- a real defect in the legacy code, which retries the
whole generation at ``services/agent.py:308`` after tokens have already been sent.
"""

from __future__ import annotations

import json
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
from ...domain.messages import ToolCall
from ...ports.llm import HealthStatus
from .base import BaseProvider, flatten_conversation

__all__ = ["OpenAICompatibleProvider"]

_FINISH_REASONS = {
    "stop": FinishReason.STOP,
    "length": FinishReason.LENGTH,
    "max_tokens": FinishReason.LENGTH,
    "tool_calls": FinishReason.TOOL_CALLS,
    "function_call": FinishReason.TOOL_CALLS,
    "content_filter": FinishReason.CONTENT_FILTER,
}


class OpenAICompatibleProvider(BaseProvider):
    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        model: str,
        api_key: str = "",
        capabilities: ProviderCapabilities | None = None,
        timeout_s: float = 60.0,
        health_timeout_s: float = 5.0,
        extra_headers: Mapping[str, str] | None = None,
        health_path: str = "/models",
        extra_body: Mapping[str, Any] | None = None,
    ) -> None:
        caps = capabilities or ProviderCapabilities(
            model_id=model,
            context_window=8192,
            max_output_tokens=2048,
            supports_streaming=True,
            supports_tools=True,
            supports_json_mode=True,
        )
        super().__init__(
            name=name, capabilities=caps, timeout_s=timeout_s,
            health_timeout_s=health_timeout_s,
        )
        self._url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._extra_headers = dict(extra_headers or {})
        self._health_path = health_path or "/models"
        #: Provider-specific body fields (``AURA_PROVIDER_<NAME>_EXTRA_BODY``). Reserved fields
        #: are refused at configuration time; merged *under* AURA's own, so AURA's win anyway.
        self._extra_body = dict(extra_body or {})

    def _base_url(self) -> str:
        return self._url

    def _headers(self) -> Mapping[str, str]:
        headers = {"Content-Type": "application/json", **self._extra_headers}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    # -- payload -----------------------------------------------------------------------

    def _payload(self, request: CompletionRequest, *, stream: bool) -> dict[str, Any]:
        _, messages = flatten_conversation(request.conversation)
        sampling = request.sampling
        payload: dict[str, Any] = {
            **self._extra_body,
            "model": request.model or self._model,
            "messages": messages,
            "temperature": sampling.temperature,
            "top_p": sampling.top_p,
            "max_tokens": self._effective_max_output(request),
            "stream": stream,
        }
        if sampling.stop:
            payload["stop"] = list(sampling.stop)
        if sampling.seed is not None:
            payload["seed"] = sampling.seed
        if sampling.presence_penalty:
            payload["presence_penalty"] = sampling.presence_penalty
        if sampling.frequency_penalty:
            payload["frequency_penalty"] = sampling.frequency_penalty
        if stream:
            # Without this, most servers omit usage entirely on streamed responses and cost
            # accounting silently falls back to estimates.
            payload["stream_options"] = {"include_usage": True}
        if request.tools and self._capabilities.supports_tools:
            payload["tools"] = list(self._tool_declarations(request))
            payload["tool_choice"] = (
                "auto" if request.tool_choice is ToolChoice.AUTO else str(request.tool_choice)
            )
        if request.response_schema and self._capabilities.supports_json_mode:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "aura_response",
                    "schema": dict(request.response_schema),
                    "strict": True,
                },
            }
        return payload

    def _upstream_error(self, error: Any, *, operation: str) -> ProviderError:
        """An ``{"error": ...}`` object a provider sent inside a 200 response or an SSE stream.

        OpenRouter does this when an upstream it routed to fails after the stream has started.
        Ignoring it -- what a parser that only looks at ``choices`` does -- turns a failed
        generation into an empty successful one. A numeric ``code`` is treated as the HTTP status
        it stands for, so an upstream 429 still benches the provider instead of tripping the
        breaker (ADR-030 Phase 2).
        """
        message = error.get("message") if isinstance(error, Mapping) else error
        code = error.get("code") if isinstance(error, Mapping) else None
        status = code if isinstance(code, int) and 100 <= code < 600 else None
        retryable = status is None or status >= 500 or status == 429
        return ProviderError(
            f"{self.name}: {operation} reported an upstream error",
            provider=self.name,
            model=self._model,
            retryable=retryable,
            details={
                "operation": operation,
                "upstream_error": str(message)[:300],
                **({"status": status} if status else {}),
            },
        )

    @staticmethod
    def _event_usage(event: Mapping[str, Any]) -> Mapping[str, Any] | None:
        """Usage from a response or stream event: standard ``usage``, else Groq's ``x_groq.usage``."""
        if event.get("usage"):
            return event["usage"]
        vendor = event.get("x_groq")
        if isinstance(vendor, Mapping) and vendor.get("usage"):
            return vendor["usage"]
        return None

    @staticmethod
    def _usage(raw: Mapping[str, Any] | None) -> TokenUsage | None:
        if not raw:
            return None
        return TokenUsage(
            prompt_tokens=int(raw.get("prompt_tokens", 0) or 0),
            completion_tokens=int(raw.get("completion_tokens", 0) or 0),
        )

    @staticmethod
    def _parse_tool_calls(raw_calls: Any) -> tuple[ToolCall, ...]:
        """Parse tool calls, tolerating malformed argument JSON.

        Small models emit invalid JSON often enough that raising here would make them unusable.
        The call is preserved with an ``_unparsed`` marker; the tool registry's schema validation
        rejects it with a clear message the model can act on, which is a far better failure mode
        than an exception inside a stream.
        """
        if not raw_calls:
            return ()
        calls: list[ToolCall] = []
        for index, raw in enumerate(raw_calls):
            function = raw.get("function", {}) if isinstance(raw, Mapping) else {}
            name = function.get("name") or ""
            if not name:
                continue
            raw_args = function.get("arguments") or "{}"
            if isinstance(raw_args, Mapping):
                arguments: dict[str, Any] = dict(raw_args)
            else:
                try:
                    parsed = json.loads(raw_args)
                    arguments = parsed if isinstance(parsed, dict) else {"_value": parsed}
                except (json.JSONDecodeError, TypeError):
                    arguments = {"_unparsed": str(raw_args)[:2000]}
            calls.append(
                ToolCall(
                    id=ToolCallId(str(raw.get("id") or f"call_{index}")),
                    name=name,
                    arguments=arguments,
                )
            )
        return tuple(calls)

    # -- completion --------------------------------------------------------------------

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        client = await self._get_client()
        started = time.perf_counter()
        try:
            response = await client.post("/chat/completions", json=self._payload(request, stream=False))
        except Exception as exc:
            raise self._translate(exc, operation="complete") from exc

        await self._raise_for_status(response, operation="complete")
        try:
            body = response.json()
        except Exception as exc:
            raise ProviderError(
                f"{self.name}: response was not valid JSON", provider=self.name, cause=exc
            ) from exc

        choices = body.get("choices") or []
        if not choices and body.get("error"):
            raise self._upstream_error(body["error"], operation="complete")
        if not choices:
            raise ProviderError(
                f"{self.name}: response contained no choices",
                provider=self.name,
                model=self._model,
                details={"body_keys": sorted(body.keys())},
            )
        choice = choices[0]
        message = choice.get("message") or {}
        text = message.get("content") or ""
        tool_calls = self._parse_tool_calls(message.get("tool_calls"))

        usage = self._usage(self._event_usage(body))
        estimated = usage is None
        if usage is None:
            usage = self._estimate_usage(request, text)

        return CompletionResponse(
            text=text,
            provider=self.name,
            model=body.get("model") or self._model,
            finish_reason=_FINISH_REASONS.get(
                choice.get("finish_reason") or "stop",
                FinishReason.TOOL_CALLS if tool_calls else FinishReason.STOP,
            ),
            usage=usage,
            tool_calls=tool_calls,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            cost_usd=self._cost(usage),
            raw_metadata={"usage_estimated": estimated},
        )

    # -- streaming ---------------------------------------------------------------------

    async def stream(self, request: CompletionRequest) -> AsyncIterator[StreamChunk]:
        client = await self._get_client()
        payload = self._payload(request, stream=True)

        # Open the connection before yielding anything: the port contract requires connection
        # failures to be raisable, so the router can still fail over.
        try:
            context = client.stream("POST", "/chat/completions", json=payload)
            response = await context.__aenter__()
        except Exception as exc:
            raise self._translate(exc, operation="stream") from exc

        try:
            await self._raise_for_status(response, operation="stream")

            # Tool-call fragments arrive spread across chunks and must be reassembled by index.
            pending: dict[int, dict[str, Any]] = {}
            usage: TokenUsage | None = None
            finish: FinishReason | None = None
            completion_chars = 0

            async for line in response.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    continue

                if event.get("error"):
                    raise self._upstream_error(event["error"], operation="stream")
                event_usage = self._event_usage(event)
                if event_usage:
                    usage = self._usage(event_usage)

                for choice in event.get("choices") or []:
                    delta = choice.get("delta") or {}
                    content = delta.get("content")
                    if content:
                        completion_chars += len(content)
                        yield StreamChunk(delta=content, provider=self.name, model=self._model)
                    for fragment in delta.get("tool_calls") or []:
                        index = int(fragment.get("index", 0))
                        slot = pending.setdefault(index, {"id": "", "function": {"name": "", "arguments": ""}})
                        if fragment.get("id"):
                            slot["id"] = fragment["id"]
                        function = fragment.get("function") or {}
                        if function.get("name"):
                            slot["function"]["name"] = function["name"]
                        if function.get("arguments"):
                            slot["function"]["arguments"] += function["arguments"]
                    if choice.get("finish_reason"):
                        finish = _FINISH_REASONS.get(choice["finish_reason"], FinishReason.STOP)

            tool_calls = self._parse_tool_calls([pending[k] for k in sorted(pending)])
            if usage is None:
                usage = TokenUsage(
                    prompt_tokens=request.conversation.approx_tokens(),
                    completion_tokens=max(1, (completion_chars + 3) // 4),
                )
            yield StreamChunk(
                tool_calls=tool_calls,
                finish_reason=finish or (FinishReason.TOOL_CALLS if tool_calls else FinishReason.STOP),
                usage=usage,
                provider=self.name,
                model=self._model,
            )
        except ProviderError:
            raise
        except Exception as exc:
            raise self._translate(exc, operation="stream") from exc
        finally:
            await context.__aexit__(None, None, None)

    # -- health ------------------------------------------------------------------------

    async def health(self) -> HealthStatus:
        """Probe ``/models``. Cheap, universally implemented, and consumes no generation quota.

        The timeout is ``health_timeout_s`` rather than a literal, because a provider that
        scales to zero needs a longer one than a provider that is always up -- see the field's
        own comment in ``base.py`` for the production measurement behind that.

        The path is configurable (``AURA_PROVIDER_<NAME>_HEALTH_PATH``) for a hosted API that
        serves chat but not ``/models`` at the same base URL. A health probe must never be a
        completion: on a free tier that would spend quota just to ask whether quota is left.
        """
        started = time.perf_counter()
        try:
            client = await self._get_client()
            response = await client.get(self._health_path, timeout=self._health_timeout_s)
            elapsed = (time.perf_counter() - started) * 1000.0
            if response.status_code >= 400:
                return HealthStatus(False, f"HTTP {response.status_code}", elapsed)
            return HealthStatus(True, "ok", elapsed)
        except Exception as exc:
            return HealthStatus(False, type(exc).__name__, (time.perf_counter() - started) * 1000.0)
