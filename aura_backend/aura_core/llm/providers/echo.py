"""Deterministic in-process provider for tests, local development, and CI.

This is what makes the pipeline testable end-to-end with no network, no API key, and no GPU --
the entire kernel, router, cache, safety, and tool loop can be exercised in a unit test.

It is **not** a fallback. The legacy system falls through to hard-coded strings that read like
real assistant answers (``services/agent.py:349``), which is indistinguishable from a working
system to anyone downstream. ``EchoProvider`` is only ever reachable when explicitly configured,
and ``AuraSettings.validate()`` refuses to let it run in staging or production.

Scripted behaviour lets a test say "the model will request this tool, then answer" without
mocking internals:

    provider = EchoProvider(script=[
        ScriptedTurn(tool_calls=[("get_weather", {"city": "Cairo"})]),
        ScriptedTurn(text="It is 34C in Cairo."),
    ])
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any, AsyncIterator, Mapping, Sequence

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

__all__ = ["EchoProvider", "ScriptedTurn"]


@dataclass(frozen=True, slots=True)
class ScriptedTurn:
    """One pre-programmed model response."""

    text: str = ""
    tool_calls: Sequence[tuple[str, Mapping[str, Any]]] = ()
    finish_reason: FinishReason | None = None
    delay_ms: float = 0.0
    raise_error: BaseException | None = None


class EchoProvider(BaseProvider):
    """A provider whose output is a pure function of its input (or of a script)."""

    def __init__(
        self,
        *,
        name: str = "echo",
        model: str = "echo-1",
        script: Sequence[ScriptedTurn] = (),
        prefix: str = "",
        capabilities: ProviderCapabilities | None = None,
        chunk_size: int = 16,
        healthy: bool = True,
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
            max_concurrency=32,
        )
        super().__init__(name=name, capabilities=caps, timeout_s=5.0)
        self._model = model
        self._script = list(script)
        self._cursor = 0
        self._prefix = prefix
        self._chunk_size = max(1, chunk_size)
        self._healthy = healthy
        self.calls: list[CompletionRequest] = []

    # -- scripting ---------------------------------------------------------------------

    def queue(self, *turns: ScriptedTurn) -> "EchoProvider":
        self._script.extend(turns)
        return self

    def reset(self) -> None:
        self._cursor = 0
        self.calls.clear()

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def _next_turn(self, request: CompletionRequest) -> ScriptedTurn:
        if self._cursor < len(self._script):
            turn = self._script[self._cursor]
            self._cursor += 1
            return turn
        return ScriptedTurn(text=self._derive(request))

    def _derive(self, request: CompletionRequest) -> str:
        """Default behaviour: echo the last user message.

        Deterministic *and* observable -- a test can assert what the model was actually shown,
        which catches context-assembly bugs that a canned string would hide.
        """
        last = request.conversation.last_user_message
        body = last.content if last else ""
        # Collapse fenced untrusted blocks so echoed output stays readable in test failures.
        body = re.sub(r"<<<AURA-DATA:[0-9a-f]+[^>]*>>>", "[data]", body)
        body = re.sub(r"<<<END-AURA-DATA:[0-9a-f]+>>>", "", body)
        return f"{self._prefix}{' '.join(body.split())}".strip()

    # -- provider surface --------------------------------------------------------------

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.calls.append(request)
        turn = self._next_turn(request)
        if turn.delay_ms:
            await asyncio.sleep(turn.delay_ms / 1000.0)
        if turn.raise_error is not None:
            raise turn.raise_error

        tool_calls = tuple(
            ToolCall(id=ToolCallId(f"echo_call_{i}"), name=name, arguments=dict(args))
            for i, (name, args) in enumerate(turn.tool_calls)
        )
        usage = TokenUsage(
            prompt_tokens=request.conversation.approx_tokens(),
            completion_tokens=max(1, (len(turn.text) + 3) // 4),
        )
        return CompletionResponse(
            text=turn.text,
            provider=self.name,
            model=self._model,
            finish_reason=turn.finish_reason
            or (FinishReason.TOOL_CALLS if tool_calls else FinishReason.STOP),
            usage=usage,
            tool_calls=tool_calls,
            latency_ms=turn.delay_ms,
            cost_usd=0.0,
        )

    async def stream(self, request: CompletionRequest) -> AsyncIterator[StreamChunk]:
        self.calls.append(request)
        turn = self._next_turn(request)
        if turn.raise_error is not None:
            # Raised before any yield, honouring the port's "no partial output before failure"
            # contract so failover tests exercise the real code path.
            raise turn.raise_error
        if turn.delay_ms:
            await asyncio.sleep(turn.delay_ms / 1000.0)

        text = turn.text
        for start in range(0, len(text), self._chunk_size):
            yield StreamChunk(
                delta=text[start : start + self._chunk_size], provider=self.name, model=self._model
            )

        tool_calls = tuple(
            ToolCall(id=ToolCallId(f"echo_call_{i}"), name=name, arguments=dict(args))
            for i, (name, args) in enumerate(turn.tool_calls)
        )
        yield StreamChunk(
            tool_calls=tool_calls,
            finish_reason=turn.finish_reason
            or (FinishReason.TOOL_CALLS if tool_calls else FinishReason.STOP),
            usage=TokenUsage(
                prompt_tokens=request.conversation.approx_tokens(),
                completion_tokens=max(1, (len(text) + 3) // 4),
            ),
            provider=self.name,
            model=self._model,
        )

    async def health(self) -> HealthStatus:
        return HealthStatus(self._healthy, "scripted", 0.0)

    async def close(self) -> None:
        return None

    def last_system_prompt(self) -> str:
        """What the model was actually instructed with on the most recent call.

        Used by safety tests to assert that retrieved content never reaches the system channel.
        """
        if not self.calls:
            return ""
        return "\n\n".join(
            m.content for m in self.calls[-1].conversation if m.role is Role.SYSTEM
        )
