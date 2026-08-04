"""Google Gemini adapter.

Implemented against the REST API with ``httpx`` rather than ``google-genai``. The SDK's async
surface has changed shape several times, it pulls in a large transitive dependency tree, and its
exceptions would have to be translated at this boundary regardless. The REST contract for
``generateContent``/``streamGenerateContent`` is small, stable, and documented, so owning ~200
lines here buys independence from SDK churn -- the same reasoning as ADR-003's rejection of
LangChain.

Gemini's wire format differs from OpenAI's in three ways this class absorbs:
  * roles are ``user``/``model``, not ``user``/``assistant``;
  * the system prompt is a separate ``systemInstruction`` field, not a message;
  * tool results are ``functionResponse`` parts, not a ``tool`` role.
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
)
from ...domain.messages import Role, ToolCall
from ...ports.llm import HealthStatus
from .base import BaseProvider

__all__ = ["GeminiProvider"]

_FINISH = {
    "STOP": FinishReason.STOP,
    "MAX_TOKENS": FinishReason.LENGTH,
    "SAFETY": FinishReason.CONTENT_FILTER,
    "RECITATION": FinishReason.CONTENT_FILTER,
    "PROHIBITED_CONTENT": FinishReason.CONTENT_FILTER,
}


class GeminiProvider(BaseProvider):
    def __init__(
        self,
        *,
        name: str = "gemini",
        model: str = "gemini-2.5-flash",
        api_key: str = "",
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        capabilities: ProviderCapabilities | None = None,
        timeout_s: float = 60.0,
    ) -> None:
        caps = capabilities or ProviderCapabilities(
            model_id=model,
            context_window=1_048_576,
            max_output_tokens=8192,
            supports_streaming=True,
            supports_tools=True,
            supports_json_mode=True,
            supports_vision=True,
            quality_tier=2,
            cost_per_1k_input_usd=0.0003,
            cost_per_1k_output_usd=0.0025,
            max_concurrency=16,
        )
        super().__init__(name=name, capabilities=caps, timeout_s=timeout_s)
        self._url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key

    def _base_url(self) -> str:
        return self._url

    def _headers(self) -> Mapping[str, str]:
        # Header rather than a query parameter: query strings end up in access logs and proxy
        # traces, and an API key in a log is an API key in a backup.
        return {"Content-Type": "application/json", "x-goog-api-key": self._api_key}

    # -- translation -------------------------------------------------------------------

    def _build_body(self, request: CompletionRequest) -> dict[str, Any]:
        system_parts: list[str] = []
        contents: list[dict[str, Any]] = []

        for message in request.conversation:
            if message.role is Role.SYSTEM:
                system_parts.append(message.content)
                continue
            if message.role is Role.TOOL:
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": message.name or "tool",
                                    "response": {"result": message.content},
                                }
                            }
                        ],
                    }
                )
                continue
            role = "model" if message.role is Role.ASSISTANT else "user"
            parts: list[dict[str, Any]] = []
            if message.content:
                parts.append({"text": message.content})
            for call in message.tool_calls:
                parts.append({"functionCall": {"name": call.name, "args": dict(call.arguments)}})
            if parts:
                contents.append({"role": role, "parts": parts})

        sampling = request.sampling
        body: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": sampling.temperature,
                "topP": sampling.top_p,
                "maxOutputTokens": self._effective_max_output(request),
            },
        }
        if sampling.stop:
            body["generationConfig"]["stopSequences"] = list(sampling.stop)
        if system_parts:
            body["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
        if request.tools and self._capabilities.supports_tools:
            body["tools"] = [{"functionDeclarations": [dict(t) for t in request.tools]}]
        if request.response_schema and self._capabilities.supports_json_mode:
            body["generationConfig"]["responseMimeType"] = "application/json"
            body["generationConfig"]["responseSchema"] = dict(request.response_schema)
        return body

    @staticmethod
    def _usage(body: Mapping[str, Any]) -> TokenUsage:
        raw = body.get("usageMetadata") or {}
        return TokenUsage(
            prompt_tokens=int(raw.get("promptTokenCount", 0) or 0),
            completion_tokens=int(raw.get("candidatesTokenCount", 0) or 0),
        )

    @staticmethod
    def _extract(candidate: Mapping[str, Any]) -> tuple[str, tuple[ToolCall, ...]]:
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for index, part in enumerate((candidate.get("content") or {}).get("parts") or []):
            if "text" in part:
                text_parts.append(part["text"])
            elif "functionCall" in part:
                fn = part["functionCall"]
                calls.append(
                    ToolCall(
                        id=ToolCallId(f"gemini_call_{index}"),
                        name=fn.get("name", ""),
                        arguments=dict(fn.get("args") or {}),
                    )
                )
        return "".join(text_parts), tuple(c for c in calls if c.name)

    # -- provider surface --------------------------------------------------------------

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        client = await self._get_client()
        model = request.model or self._model
        started = time.perf_counter()
        try:
            response = await client.post(
                f"/models/{model}:generateContent", json=self._build_body(request)
            )
        except Exception as exc:
            raise self._translate(exc, operation="complete") from exc

        await self._raise_for_status(response, operation="complete")
        body = response.json()
        candidates = body.get("candidates") or []
        if not candidates:
            # A prompt blocked by Gemini's own safety filter returns no candidates. Surfacing it
            # as a typed error beats returning an empty string that reads like a model choosing
            # to say nothing.
            reason = (body.get("promptFeedback") or {}).get("blockReason")
            raise ProviderError(
                f"gemini returned no candidates{f' (blocked: {reason})' if reason else ''}",
                provider=self.name,
                model=model,
                retryable=False,
                details={"block_reason": reason},
            )

        candidate = candidates[0]
        text, tool_calls = self._extract(candidate)
        usage = self._usage(body)
        if usage.total_tokens == 0:
            usage = self._estimate_usage(request, text)

        return CompletionResponse(
            text=text,
            provider=self.name,
            model=model,
            finish_reason=_FINISH.get(
                candidate.get("finishReason", "STOP"),
                FinishReason.TOOL_CALLS if tool_calls else FinishReason.STOP,
            ),
            usage=usage,
            tool_calls=tool_calls,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            cost_usd=self._cost(usage),
        )

    async def stream(self, request: CompletionRequest) -> AsyncIterator[StreamChunk]:
        client = await self._get_client()
        model = request.model or self._model
        try:
            context = client.stream(
                "POST",
                f"/models/{model}:streamGenerateContent",
                params={"alt": "sse"},
                json=self._build_body(request),
            )
            response = await context.__aenter__()
        except Exception as exc:
            raise self._translate(exc, operation="stream") from exc

        try:
            await self._raise_for_status(response, operation="stream")
            usage = TokenUsage()
            calls: tuple[ToolCall, ...] = ()
            finish: FinishReason | None = None
            emitted = 0

            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if event.get("usageMetadata"):
                    usage = self._usage(event)
                for candidate in event.get("candidates") or []:
                    text, chunk_calls = self._extract(candidate)
                    if text:
                        emitted += len(text)
                        yield StreamChunk(delta=text, provider=self.name, model=model)
                    if chunk_calls:
                        calls = calls + chunk_calls
                    if candidate.get("finishReason"):
                        finish = _FINISH.get(candidate["finishReason"], FinishReason.STOP)

            if usage.total_tokens == 0:
                usage = TokenUsage(
                    prompt_tokens=request.conversation.approx_tokens(),
                    completion_tokens=max(1, (emitted + 3) // 4),
                )
            yield StreamChunk(
                tool_calls=calls,
                finish_reason=finish or (FinishReason.TOOL_CALLS if calls else FinishReason.STOP),
                usage=usage,
                provider=self.name,
                model=model,
            )
        except ProviderError:
            raise
        except Exception as exc:
            raise self._translate(exc, operation="stream") from exc
        finally:
            await context.__aexit__(None, None, None)

    async def health(self) -> HealthStatus:
        started = time.perf_counter()
        try:
            client = await self._get_client()
            response = await client.get(f"/models/{self._model}", timeout=5.0)
            elapsed = (time.perf_counter() - started) * 1000.0
            if response.status_code >= 400:
                return HealthStatus(False, f"HTTP {response.status_code}", elapsed)
            return HealthStatus(True, "ok", elapsed)
        except Exception as exc:
            return HealthStatus(False, type(exc).__name__, (time.perf_counter() - started) * 1000.0)
