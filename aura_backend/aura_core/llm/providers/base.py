"""Shared provider machinery.

:class:`BaseProvider` holds what every adapter needs and nothing vendor-specific: capability
declaration, a lazily-created HTTP client, error translation, and conversation flattening.

The lazy client matters. Constructing an ``httpx.AsyncClient`` at ``__init__`` binds it to
whatever event loop happens to be current, which breaks when the same provider object is used
from a Celery worker's loop after being built during import. Creating it on first use inside the
running loop is the only correct pattern -- and it is the direct fix for TD-01, where every
legacy service opens a connection at module import.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Mapping, Sequence

from ...domain.errors import ProviderError, TimeoutError as AuraTimeoutError
from ...domain.llm import (
    CompletionRequest,
    CompletionResponse,
    ProviderCapabilities,
    StreamChunk,
    TokenUsage,
)
from ...domain.messages import Conversation, Message, Role, TrustChannel
from ...ports.llm import HealthStatus

__all__ = ["BaseProvider", "http_client", "WireMessage", "flatten_conversation"]


class WireMessage(dict):
    """A ``{"role": ..., "content": ...}`` dict in the OpenAI-ish shape most vendors accept."""


def flatten_conversation(
    conversation: Conversation, *, system_as_message: bool = True
) -> tuple[str, list[WireMessage]]:
    """Split a conversation into (system_prompt, messages).

    Trust channels are collapsed here because the wire has no notion of them -- by this point
    the context engine has already rendered untrusted material into fenced blocks inside message
    content (ADR-006), so flattening is safe. Doing the fencing here instead would mean every
    adapter had to remember to do it.
    """
    system_parts: list[str] = []
    messages: list[WireMessage] = []

    for message in conversation:
        if message.role is Role.SYSTEM:
            system_parts.append(message.content)
            continue
        if message.role is Role.TOOL:
            messages.append(
                WireMessage(
                    role="tool",
                    content=message.content,
                    tool_call_id=message.tool_call_id.value if message.tool_call_id else "",
                    name=message.name or "",
                )
            )
            continue
        role = "assistant" if message.role is Role.ASSISTANT else "user"
        entry = WireMessage(role=role, content=message.content)
        if message.tool_calls:
            entry["tool_calls"] = [
                {
                    "id": call.id.value,
                    "type": "function",
                    "function": {"name": call.name, "arguments": json.dumps(dict(call.arguments))},
                }
                for call in message.tool_calls
            ]
        messages.append(entry)

    system_prompt = "\n\n".join(p for p in system_parts if p.strip())
    if system_prompt and system_as_message:
        messages.insert(0, WireMessage(role="system", content=system_prompt))
    return system_prompt, messages


def http_client(*, base_url: str = "", timeout_s: float = 60.0, headers: Mapping[str, str] | None = None):
    """Create an ``httpx.AsyncClient``, with a clear error if httpx is not installed.

    Imported lazily so ``aura_core`` remains importable -- and the whole domain/engine layer
    testable -- without any HTTP dependency.
    """
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise ProviderError(
            "httpx is required for HTTP-based providers; install it or use a non-HTTP provider",
            provider="http",
        ) from exc

    limits = httpx.Limits(max_connections=64, max_keepalive_connections=32)
    return httpx.AsyncClient(
        base_url=base_url,
        timeout=httpx.Timeout(timeout_s, connect=min(10.0, timeout_s)),
        headers=dict(headers or {}),
        limits=limits,
    )


class BaseProvider:
    """Common behaviour for concrete providers.

    Subclasses implement ``complete``, ``stream``, and ``health``; everything else -- naming,
    capabilities, client lifecycle, error translation, cost accounting -- is inherited.
    """

    def __init__(self, *, name: str, capabilities: ProviderCapabilities, timeout_s: float = 60.0) -> None:
        self._name = name
        self._capabilities = capabilities
        self._timeout_s = timeout_s
        self._client: Any = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    # -- client lifecycle --------------------------------------------------------------

    def _base_url(self) -> str:  # pragma: no cover - overridden by HTTP providers
        return ""

    def _headers(self) -> Mapping[str, str]:  # pragma: no cover - overridden
        return {}

    async def _get_client(self) -> Any:
        if self._client is None:
            self._client = http_client(
                base_url=self._base_url(), timeout_s=self._timeout_s, headers=self._headers()
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- error translation -------------------------------------------------------------

    def _translate(self, exc: BaseException, *, operation: str) -> ProviderError:
        """Turn a vendor/transport exception into a domain error.

        Distinguishing timeouts from other failures is what lets the router treat "this node is
        saturated" (try the next one immediately) differently from "this request was malformed"
        (trying another provider will fail identically).
        """
        name = type(exc).__name__
        if "Timeout" in name:
            return ProviderError(
                f"{self.name}: {operation} timed out after {self._timeout_s:.0f}s",
                provider=self.name,
                model=self._capabilities.model_id,
                cause=exc,
                retryable=True,
                details={"error_type": name, "operation": operation},
            )
        status = getattr(getattr(exc, "response", None), "status_code", None)
        retryable = status is None or status >= 500 or status == 429
        return ProviderError(
            f"{self.name}: {operation} failed ({name})",
            provider=self.name,
            model=self._capabilities.model_id,
            cause=exc,
            retryable=retryable,
            details={"error_type": name, "operation": operation, **({"status": status} if status else {})},
        )

    async def _raise_for_status(self, response: Any, *, operation: str) -> None:
        if response.status_code < 400:
            return
        # Read a bounded slice of the body: enough to diagnose, not enough to dump a model's
        # entire echoed prompt into logs.
        try:
            body = response.text[:512]
        except Exception:  # pragma: no cover - defensive
            body = ""
        retryable = response.status_code >= 500 or response.status_code == 429
        raise ProviderError(
            f"{self.name}: {operation} returned HTTP {response.status_code}",
            provider=self.name,
            model=self._capabilities.model_id,
            retryable=retryable,
            details={"status": response.status_code, "body": body, "operation": operation},
        )

    # -- helpers -----------------------------------------------------------------------

    def _cost(self, usage: TokenUsage) -> float:
        return usage.cost_usd(
            self._capabilities.cost_per_1k_input_usd, self._capabilities.cost_per_1k_output_usd
        )

    def _effective_max_output(self, request: CompletionRequest) -> int:
        requested = request.sampling.max_output_tokens or self._capabilities.max_output_tokens
        return min(requested, self._capabilities.max_output_tokens)

    def _tool_declarations(self, request: CompletionRequest) -> Sequence[Mapping[str, Any]]:
        """Tools in OpenAI function-calling shape. Vendors that differ override this."""
        return [{"type": "function", "function": dict(tool)} for tool in request.tools]

    def _estimate_usage(self, request: CompletionRequest, text: str) -> TokenUsage:
        """Fallback when a provider does not report usage.

        Marked in metadata by callers so cost dashboards can distinguish measured from
        estimated spend -- an estimate presented as a measurement is worse than no number.
        """
        return TokenUsage(
            prompt_tokens=request.conversation.approx_tokens(),
            completion_tokens=max(1, (len(text) + 3) // 4),
        )

    async def health(self) -> HealthStatus:  # pragma: no cover - overridden
        return HealthStatus(True, "no health probe implemented")

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        raise NotImplementedError

    def stream(self, request: CompletionRequest) -> AsyncIterator[StreamChunk]:
        raise NotImplementedError


def system_message(content: str) -> Message:
    return Message(role=Role.SYSTEM, content=content, channel=TrustChannel.SYSTEM)


def timeout_error(provider: str, operation: str, seconds: float) -> AuraTimeoutError:
    return AuraTimeoutError(
        f"{provider}: {operation} exceeded {seconds:.1f}s",
        details={"provider": provider, "operation": operation, "timeout_s": seconds},
    )
