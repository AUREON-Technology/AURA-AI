"""Ollama adapter -- native API, for the operations the OpenAI surface cannot express.

Ollama exposes an OpenAI-compatible ``/v1`` endpoint, and for plain chat that is what
:class:`OpenAICompatibleProvider` should be pointed at. This adapter exists for the *model
lifecycle* operations Phase 5 requires and which have no OpenAI equivalent: listing installed
models with their sizes and quantisation, pulling and deleting weights, loading a model into
memory ahead of first use, and reading what is currently resident.

Every one of those is an **admin** operation here. In the legacy system they are unauthenticated
public routes (``main.py:470-553``, SEC-01): anyone on the internet can make the server download
arbitrary model weights until the disk fills, or delete the deployed model. This class is only
reachable through ``/api/v3/admin/models``, which requires an Admin role, checks an allowlist,
and writes an audit record.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Mapping, Sequence

from ...domain.errors import AuthorizationError, ProviderError
from ...domain.llm import (
    CompletionRequest,
    CompletionResponse,
    FinishReason,
    ProviderCapabilities,
    StreamChunk,
    TokenUsage,
)
from ...ports.llm import HealthStatus
from .base import BaseProvider, flatten_conversation

__all__ = ["OllamaProvider", "LocalModelInfo"]


@dataclass(frozen=True, slots=True)
class LocalModelInfo:
    """What the model registry shows an administrator about a locally-installed model."""

    name: str
    size_bytes: int = 0
    parameter_size: str = ""
    quantization: str = ""
    family: str = ""
    modified_at: str = ""
    loaded: bool = False

    @property
    def size_gb(self) -> float:
        return round(self.size_bytes / (1024**3), 2)


class OllamaProvider(BaseProvider):
    def __init__(
        self,
        *,
        name: str = "ollama",
        base_url: str = "http://localhost:11434",
        model: str = "llama3.1:8b",
        capabilities: ProviderCapabilities | None = None,
        timeout_s: float = 120.0,
        model_allowlist: Sequence[str] = (),
    ) -> None:
        caps = capabilities or ProviderCapabilities(
            model_id=model,
            context_window=8192,
            max_output_tokens=2048,
            supports_streaming=True,
            supports_tools=True,
            supports_json_mode=True,
            is_local=True,
            quality_tier=1,
            cost_per_1k_input_usd=0.0,
            cost_per_1k_output_usd=0.0,
            max_concurrency=2,  # a single local GPU/CPU saturates fast; queue rather than thrash
        )
        super().__init__(name=name, capabilities=caps, timeout_s=timeout_s)
        self._url = base_url.rstrip("/")
        self._model = model
        # Empty means "no pulls permitted". Fail closed: an unset allowlist must not mean
        # "anything goes" for an operation that downloads and executes third-party artefacts.
        self._allowlist = tuple(model_allowlist)

    def _base_url(self) -> str:
        return self._url

    def _headers(self) -> Mapping[str, str]:
        return {"Content-Type": "application/json"}

    # -- chat --------------------------------------------------------------------------

    def _payload(self, request: CompletionRequest, *, stream: bool) -> dict[str, Any]:
        _, messages = flatten_conversation(request.conversation)
        sampling = request.sampling
        payload: dict[str, Any] = {
            "model": request.model or self._model,
            "messages": messages,
            "stream": stream,
            "options": {
                "temperature": sampling.temperature,
                "top_p": sampling.top_p,
                "num_predict": self._effective_max_output(request),
            },
        }
        if sampling.stop:
            payload["options"]["stop"] = list(sampling.stop)
        if sampling.seed is not None:
            payload["options"]["seed"] = sampling.seed
        if request.tools and self._capabilities.supports_tools:
            payload["tools"] = list(self._tool_declarations(request))
        if request.response_schema and self._capabilities.supports_json_mode:
            payload["format"] = dict(request.response_schema)
        return payload

    @staticmethod
    def _usage(body: Mapping[str, Any]) -> TokenUsage:
        return TokenUsage(
            prompt_tokens=int(body.get("prompt_eval_count", 0) or 0),
            completion_tokens=int(body.get("eval_count", 0) or 0),
        )

    def _tool_calls_from(self, message: Mapping[str, Any]):
        from ...domain.identifiers import ToolCallId
        from ...domain.messages import ToolCall

        calls = []
        for index, raw in enumerate(message.get("tool_calls") or []):
            function = raw.get("function") or {}
            name = function.get("name")
            if not name:
                continue
            arguments = function.get("arguments") or {}
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    arguments = {"_unparsed": arguments[:2000]}
            calls.append(
                ToolCall(id=ToolCallId(f"ollama_call_{index}"), name=name, arguments=dict(arguments))
            )
        return tuple(calls)

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        client = await self._get_client()
        started = time.perf_counter()
        try:
            response = await client.post("/api/chat", json=self._payload(request, stream=False))
        except Exception as exc:
            raise self._translate(exc, operation="complete") from exc

        await self._raise_for_status(response, operation="complete")
        body = response.json()
        message = body.get("message") or {}
        text = message.get("content") or ""
        usage = self._usage(body)
        if usage.total_tokens == 0:
            usage = self._estimate_usage(request, text)

        return CompletionResponse(
            text=text,
            provider=self.name,
            model=body.get("model") or self._model,
            finish_reason=FinishReason.STOP,
            usage=usage,
            tool_calls=self._tool_calls_from(message),
            latency_ms=(time.perf_counter() - started) * 1000.0,
            cost_usd=0.0,
        )

    async def stream(self, request: CompletionRequest) -> AsyncIterator[StreamChunk]:
        client = await self._get_client()
        try:
            context = client.stream("POST", "/api/chat", json=self._payload(request, stream=True))
            response = await context.__aenter__()
        except Exception as exc:
            raise self._translate(exc, operation="stream") from exc

        try:
            await self._raise_for_status(response, operation="stream")
            usage = TokenUsage()
            tool_calls: tuple = ()
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                message = event.get("message") or {}
                content = message.get("content")
                if content:
                    yield StreamChunk(delta=content, provider=self.name, model=self._model)
                if message.get("tool_calls"):
                    tool_calls = self._tool_calls_from(message)
                if event.get("done"):
                    usage = self._usage(event)
            yield StreamChunk(
                tool_calls=tool_calls,
                finish_reason=FinishReason.TOOL_CALLS if tool_calls else FinishReason.STOP,
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
        started = time.perf_counter()
        try:
            client = await self._get_client()
            response = await client.get("/api/tags", timeout=5.0)
            elapsed = (time.perf_counter() - started) * 1000.0
            if response.status_code >= 400:
                return HealthStatus(False, f"HTTP {response.status_code}", elapsed)
            names = {m.get("name", "") for m in (response.json().get("models") or [])}
            # A configured model that is not installed is an operational fault worth surfacing:
            # the first request would otherwise trigger a multi-GB download inline.
            if self._model and not any(self._model in n or n in self._model for n in names):
                return HealthStatus(False, f"model {self._model!r} not installed", elapsed)
            return HealthStatus(True, "ok", elapsed)
        except Exception as exc:
            return HealthStatus(False, type(exc).__name__, (time.perf_counter() - started) * 1000.0)

    # -- model lifecycle (admin only) --------------------------------------------------

    async def list_models(self) -> tuple[LocalModelInfo, ...]:
        client = await self._get_client()
        try:
            response = await client.get("/api/tags", timeout=10.0)
            await self._raise_for_status(response, operation="list_models")
            installed = response.json().get("models") or []

            loaded_names: set[str] = set()
            try:
                running = await client.get("/api/ps", timeout=5.0)
                if running.status_code < 400:
                    loaded_names = {m.get("name", "") for m in (running.json().get("models") or [])}
            except Exception:
                # /api/ps is newer than /api/tags; its absence must not fail the listing.
                pass

            result = []
            for entry in installed:
                details = entry.get("details") or {}
                result.append(
                    LocalModelInfo(
                        name=entry.get("name", ""),
                        size_bytes=int(entry.get("size", 0) or 0),
                        parameter_size=details.get("parameter_size", ""),
                        quantization=details.get("quantization_level", ""),
                        family=details.get("family", ""),
                        modified_at=entry.get("modified_at", ""),
                        loaded=entry.get("name", "") in loaded_names,
                    )
                )
            return tuple(result)
        except ProviderError:
            raise
        except Exception as exc:
            raise self._translate(exc, operation="list_models") from exc

    def _assert_allowed(self, model: str) -> None:
        """Allowlist check for weight downloads.

        Fails closed on an empty allowlist. Model weights are executable artefacts fetched from
        a third-party registry; "pull whatever the caller names" is a supply-chain vector, which
        is exactly the shape of SEC-01.
        """
        if not self._allowlist:
            raise AuthorizationError(
                "model pulls are disabled: AURA_MODEL_ALLOWLIST is empty",
                details={"model": model},
            )
        if model not in self._allowlist:
            raise AuthorizationError(
                "model is not in the configured allowlist",
                details={"model": model, "allowlist": list(self._allowlist)},
            )

    async def pull_model(self, model: str) -> AsyncIterator[Mapping[str, Any]]:
        """Download weights, yielding progress events.

        Streamed rather than awaited because a pull takes minutes; the admin endpoint relays
        progress so an operator can see it working instead of watching a request hang.
        """
        self._assert_allowed(model)
        client = await self._get_client()
        try:
            context = client.stream("POST", "/api/pull", json={"name": model}, timeout=3600.0)
            response = await context.__aenter__()
        except Exception as exc:
            raise self._translate(exc, operation="pull_model") from exc
        try:
            await self._raise_for_status(response, operation="pull_model")
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
        finally:
            await context.__aexit__(None, None, None)

    async def delete_model(self, model: str) -> bool:
        client = await self._get_client()
        try:
            response = await client.request("DELETE", "/api/delete", json={"name": model}, timeout=30.0)
            await self._raise_for_status(response, operation="delete_model")
            return True
        except ProviderError:
            raise
        except Exception as exc:
            raise self._translate(exc, operation="delete_model") from exc

    async def warm_model(self, model: str | None = None) -> bool:
        """Load weights into memory without generating.

        A cold model adds seconds to the first request. Warming after a deploy or a hot swap is
        what keeps a model change invisible to users.
        """
        client = await self._get_client()
        try:
            response = await client.post(
                "/api/generate",
                json={"model": model or self._model, "prompt": "", "keep_alive": "30m"},
                timeout=600.0,
            )
            return response.status_code < 400
        except Exception as exc:
            raise self._translate(exc, operation="warm_model") from exc
