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

import base64
import json
from typing import Any, AsyncIterator, Mapping, Sequence

from ...domain.errors import ProviderError, TimeoutError as AuraTimeoutError
from ...domain.images import ImageInput
from ...domain.llm import (
    CompletionRequest,
    CompletionResponse,
    ProviderCapabilities,
    StreamChunk,
    TokenUsage,
)
from ...domain.messages import Conversation, Message, Role, TrustChannel
from ...ports.llm import HealthStatus
from ..quota import retry_after_seconds

__all__ = ["BaseProvider", "http_client", "WireMessage", "flatten_conversation", "attach_images"]


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


def attach_images(messages: list[WireMessage], images: Sequence[ImageInput], *, style: str) -> None:
    """Put ``images`` on the last user wire message, in the shape ``style`` names (ADR-031).

    * ``"openai"`` -- ``content`` becomes the OpenAI vision parts list: the text first, then one
      ``image_url`` part per image as a ``data:`` URL. Never an ``http(s)`` URL: AURA does not
      ask a model server to fetch anything, which keeps vLLM's media fetcher out of reach
      (SSRF), and Ollama's OpenAI endpoint accepts base64 only anyway.
    * ``"ollama"`` -- Ollama's native ``/api/chat`` shape: ``images`` beside the text, as bare
      base64.

    No images leaves ``messages`` untouched, so a text-only request is byte-identical to one made
    before images existed.
    """
    if not images:
        return
    target = next((m for m in reversed(messages) if m.get("role") == "user"), None)
    if target is None:
        target = WireMessage(role="user", content="")
        messages.append(target)
    encoded = [base64.b64encode(image.data).decode("ascii") for image in images]
    if style == "openai":
        parts: list[dict[str, Any]] = [{"type": "text", "text": target.get("content") or ""}]
        parts.extend(
            {"type": "image_url", "image_url": {"url": f"data:{image.media_type};base64,{data}"}}
            for image, data in zip(images, encoded)
        )
        target["content"] = parts
    elif style == "ollama":
        target["images"] = encoded
    else:  # pragma: no cover - programming error
        raise ValueError(f"unknown image wire style: {style}")


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

    def __init__(
        self,
        *,
        name: str,
        capabilities: ProviderCapabilities,
        timeout_s: float = 60.0,
        health_timeout_s: float = 5.0,
    ) -> None:
        self._name = name
        self._capabilities = capabilities
        self._timeout_s = timeout_s
        #: How long a health probe waits. Separate from `timeout_s` because the two answer
        #: different questions: `timeout_s` bounds a generation the caller is waiting on, while
        #: this bounds a liveness check that must return quickly enough to be polled.
        #:
        #: 5s is right for an endpoint that is always up. It is wrong for one that scales to
        #: zero -- a sleeping Hugging Face Space or a cold serverless GPU takes minutes to wake,
        #: so a 5s probe reports `ReadTimeout` every single time and can never observe the
        #: recovery, no matter how long the provider has been coming back. Measured in
        #: production: 20 consecutive probes failed at a flat ~5150ms over five and a half
        #: minutes, then 11 consecutive successes at 209-225ms once the endpoint had woken.
        #: Nothing about the provider changed at that boundary except that it had finished
        #: starting.
        #:
        #: Left at 5.0 so no existing deployment changes behaviour; raise it
        #: (`AURA_PROVIDER_<NAME>_HEALTH_TIMEOUT_S`) for a provider that sleeps.
        self._health_timeout_s = health_timeout_s
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
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        retryable = status is None or status >= 500 or status == 429
        return ProviderError(
            f"{self.name}: {operation} failed ({name})",
            provider=self.name,
            model=self._capabilities.model_id,
            cause=exc,
            retryable=retryable,
            details={
                "error_type": name,
                "operation": operation,
                **({"status": status} if status else {}),
                **_rate_limit_details(status, response),
            },
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
            details={
                "status": response.status_code,
                "body": body,
                "operation": operation,
                **_rate_limit_details(response.status_code, response),
            },
        )

    # -- helpers -----------------------------------------------------------------------

    def _cost(self, usage: TokenUsage) -> float:
        return usage.cost_usd(
            self._capabilities.cost_per_1k_input_usd, self._capabilities.cost_per_1k_output_usd
        )

    def _effective_max_output(self, request: CompletionRequest) -> int:
        """The output size to ask for -- see :meth:`ProviderCapabilities.output_budget`.

        The deadline is the shorter of the request's and this provider's own HTTP timeout:
        whichever fires first is the one the generation has to finish inside.
        """
        return self._capabilities.output_budget(
            request.sampling.max_output_tokens,
            request.preferred_max_output_tokens,
            min(request.timeout_s or self._timeout_s, self._timeout_s),
        )

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


def _rate_limit_details(status: int | None, response: Any) -> dict[str, Any]:
    """For a ``429``, how long the provider asked us to wait (ADR-030 Phase 2).

    Carried in ``ProviderError.details`` so the router can bench the provider until the reset
    the provider named, instead of guessing or counting the ``429`` as a health failure.
    """
    if status != 429:
        return {}
    seconds = retry_after_seconds(getattr(response, "headers", None))
    return {} if seconds is None else {"retry_after_s": round(seconds, 3)}


def system_message(content: str) -> Message:
    return Message(role=Role.SYSTEM, content=content, channel=TrustChannel.SYSTEM)


def timeout_error(provider: str, operation: str, seconds: float) -> AuraTimeoutError:
    return AuraTimeoutError(
        f"{provider}: {operation} exceeded {seconds:.1f}s",
        details={"provider": provider, "operation": operation, "timeout_s": seconds},
    )
