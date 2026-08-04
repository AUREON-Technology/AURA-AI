"""Anthropic Claude adapter (Messages API).

Wire-format differences this class absorbs:
  * the system prompt is a top-level ``system`` field, not a message;
  * messages must strictly alternate user/assistant, so consecutive same-role messages are
    merged rather than rejected mid-request;
  * content is a list of typed blocks (``text``, ``tool_use``, ``tool_result``);
  * ``max_tokens`` is required, not optional.

Model ids are configuration, never literals in this file -- the mistake the legacy code makes
with ``"gemini-2.5-flash"`` hard-coded at three call sites.
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
from ...domain.messages import Role, ToolCall
from ...ports.llm import HealthStatus
from .base import BaseProvider

__all__ = ["AnthropicProvider"]

_STOP_REASONS = {
    "end_turn": FinishReason.STOP,
    "stop_sequence": FinishReason.STOP,
    "max_tokens": FinishReason.LENGTH,
    "tool_use": FinishReason.TOOL_CALLS,
    "refusal": FinishReason.CONTENT_FILTER,
}


class AnthropicProvider(BaseProvider):
    def __init__(
        self,
        *,
        name: str = "anthropic",
        model: str,
        api_key: str = "",
        base_url: str = "https://api.anthropic.com/v1",
        api_version: str = "2023-06-01",
        capabilities: ProviderCapabilities | None = None,
        timeout_s: float = 120.0,
    ) -> None:
        caps = capabilities or ProviderCapabilities(
            model_id=model,
            context_window=200_000,
            max_output_tokens=8192,
            supports_streaming=True,
            supports_tools=True,
            supports_json_mode=False,  # structured output is achieved via a tool, not a mode
            supports_vision=True,
            quality_tier=3,
            max_concurrency=8,
        )
        super().__init__(name=name, capabilities=caps, timeout_s=timeout_s)
        self._url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._api_version = api_version

    def _base_url(self) -> str:
        return self._url

    def _headers(self) -> Mapping[str, str]:
        return {
            "Content-Type": "application/json",
            "x-api-key": self._api_key,
            "anthropic-version": self._api_version,
        }

    # -- translation -------------------------------------------------------------------

    def _build_body(self, request: CompletionRequest, *, stream: bool) -> dict[str, Any]:
        system_parts: list[str] = []
        messages: list[dict[str, Any]] = []

        def append(role: str, block: Mapping[str, Any]) -> None:
            """Append a content block, merging into the previous message when roles match.

            The API rejects consecutive same-role messages. Merging here means a conversation
            assembled from memory + history + tool results never has to know that rule.
            """
            if messages and messages[-1]["role"] == role:
                messages[-1]["content"].append(dict(block))
            else:
                messages.append({"role": role, "content": [dict(block)]})

        for message in request.conversation:
            if message.role is Role.SYSTEM:
                system_parts.append(message.content)
            elif message.role is Role.TOOL:
                append(
                    "user",
                    {
                        "type": "tool_result",
                        "tool_use_id": message.tool_call_id.value if message.tool_call_id else "",
                        "content": message.content,
                        "is_error": not message.metadata.get("ok", True),
                    },
                )
            elif message.role is Role.ASSISTANT:
                if message.content:
                    append("assistant", {"type": "text", "text": message.content})
                for call in message.tool_calls:
                    append(
                        "assistant",
                        {
                            "type": "tool_use",
                            "id": call.id.value,
                            "name": call.name,
                            "input": dict(call.arguments),
                        },
                    )
            else:
                append("user", {"type": "text", "text": message.content})

        sampling = request.sampling
        body: dict[str, Any] = {
            "model": request.model or self._model,
            "messages": messages,
            "max_tokens": self._effective_max_output(request),
            "temperature": sampling.temperature,
            "stream": stream,
        }
        # top_p and temperature together are discouraged by the API; send top_p only when it is
        # actually constraining.
        if sampling.top_p < 1.0:
            body["top_p"] = sampling.top_p
        if sampling.stop:
            body["stop_sequences"] = list(sampling.stop)
        if system_parts:
            body["system"] = "\n\n".join(system_parts)
        if request.tools and self._capabilities.supports_tools:
            body["tools"] = [
                {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "input_schema": tool.get("parameters", {"type": "object", "properties": {}}),
                }
                for tool in request.tools
            ]
            if request.tool_choice is ToolChoice.REQUIRED:
                body["tool_choice"] = {"type": "any"}
            elif request.tool_choice is ToolChoice.NONE:
                body.pop("tools", None)
        return body

    @staticmethod
    def _usage(raw: Mapping[str, Any] | None) -> TokenUsage:
        raw = raw or {}
        return TokenUsage(
            prompt_tokens=int(raw.get("input_tokens", 0) or 0),
            completion_tokens=int(raw.get("output_tokens", 0) or 0),
        )

    # -- provider surface --------------------------------------------------------------

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        client = await self._get_client()
        started = time.perf_counter()
        try:
            response = await client.post("/messages", json=self._build_body(request, stream=False))
        except Exception as exc:
            raise self._translate(exc, operation="complete") from exc

        await self._raise_for_status(response, operation="complete")
        body = response.json()

        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in body.get("content") or []:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                calls.append(
                    ToolCall(
                        id=ToolCallId(str(block.get("id", f"call_{len(calls)}"))),
                        name=block.get("name", ""),
                        arguments=dict(block.get("input") or {}),
                    )
                )

        usage = self._usage(body.get("usage"))
        text = "".join(text_parts)
        if usage.total_tokens == 0:
            usage = self._estimate_usage(request, text)

        return CompletionResponse(
            text=text,
            provider=self.name,
            model=body.get("model") or self._model,
            finish_reason=_STOP_REASONS.get(body.get("stop_reason") or "end_turn", FinishReason.STOP),
            usage=usage,
            tool_calls=tuple(c for c in calls if c.name),
            latency_ms=(time.perf_counter() - started) * 1000.0,
            cost_usd=self._cost(usage),
        )

    async def stream(self, request: CompletionRequest) -> AsyncIterator[StreamChunk]:
        client = await self._get_client()
        try:
            context = client.stream("POST", "/messages", json=self._build_body(request, stream=True))
            response = await context.__aenter__()
        except Exception as exc:
            raise self._translate(exc, operation="stream") from exc

        try:
            await self._raise_for_status(response, operation="stream")
            usage = TokenUsage()
            finish: FinishReason | None = None
            # tool_use inputs arrive as incremental JSON fragments keyed by block index.
            partial_tools: dict[int, dict[str, Any]] = {}
            emitted = 0

            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload:
                    continue
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    continue

                kind = event.get("type")
                if kind == "message_start":
                    usage = self._usage((event.get("message") or {}).get("usage"))
                elif kind == "content_block_start":
                    block = event.get("content_block") or {}
                    if block.get("type") == "tool_use":
                        partial_tools[int(event.get("index", 0))] = {
                            "id": block.get("id", ""),
                            "name": block.get("name", ""),
                            "json": "",
                        }
                elif kind == "content_block_delta":
                    delta = event.get("delta") or {}
                    if delta.get("type") == "text_delta" and delta.get("text"):
                        emitted += len(delta["text"])
                        yield StreamChunk(delta=delta["text"], provider=self.name, model=self._model)
                    elif delta.get("type") == "input_json_delta":
                        slot = partial_tools.get(int(event.get("index", 0)))
                        if slot is not None:
                            slot["json"] += delta.get("partial_json", "")
                elif kind == "message_delta":
                    stop = (event.get("delta") or {}).get("stop_reason")
                    if stop:
                        finish = _STOP_REASONS.get(stop, FinishReason.STOP)
                    incremental = self._usage(event.get("usage"))
                    if incremental.completion_tokens:
                        usage = TokenUsage(usage.prompt_tokens, incremental.completion_tokens)
                elif kind == "error":
                    raise ProviderError(
                        f"anthropic stream error: {(event.get('error') or {}).get('type', 'unknown')}",
                        provider=self.name,
                        model=self._model,
                        details={"error": event.get("error")},
                    )

            calls = []
            for index in sorted(partial_tools):
                slot = partial_tools[index]
                try:
                    arguments = json.loads(slot["json"]) if slot["json"] else {}
                except json.JSONDecodeError:
                    arguments = {"_unparsed": slot["json"][:2000]}
                if slot["name"]:
                    calls.append(
                        ToolCall(
                            id=ToolCallId(slot["id"] or f"call_{index}"),
                            name=slot["name"],
                            arguments=arguments if isinstance(arguments, dict) else {"_value": arguments},
                        )
                    )

            if usage.total_tokens == 0:
                usage = TokenUsage(
                    prompt_tokens=request.conversation.approx_tokens(),
                    completion_tokens=max(1, (emitted + 3) // 4),
                )
            yield StreamChunk(
                tool_calls=tuple(calls),
                finish_reason=finish or (FinishReason.TOOL_CALLS if calls else FinishReason.STOP),
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

    async def health(self) -> HealthStatus:
        """A one-token generation.

        Anthropic has no free model-metadata endpoint, so the probe costs a fraction of a cent.
        Health checks run on an interval, not per request, which keeps that negligible.
        """
        started = time.perf_counter()
        try:
            client = await self._get_client()
            response = await client.post(
                "/messages",
                json={
                    "model": self._model,
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "ping"}],
                },
                timeout=10.0,
            )
            elapsed = (time.perf_counter() - started) * 1000.0
            if response.status_code >= 400:
                return HealthStatus(False, f"HTTP {response.status_code}", elapsed)
            return HealthStatus(True, "ok", elapsed)
        except Exception as exc:
            return HealthStatus(False, type(exc).__name__, (time.perf_counter() - started) * 1000.0)
