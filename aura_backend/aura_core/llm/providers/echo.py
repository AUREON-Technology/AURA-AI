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
    Modality,
    ProviderCapabilities,
    StreamChunk,
    TokenUsage,
)
from ...domain.messages import Role, ToolCall
from ...ports.llm import HealthStatus
from .base import BaseProvider

__all__ = ["EchoProvider", "ScriptedTurn", "ContextGroundedProvider"]

_DATA_BLOCK = re.compile(
    # The opening tag's ``source="..."`` attribute can itself contain a literal ``>`` (a
    # section heading path like "kernel > Context" -- see aura_core/domain/context.py), which
    # a ``[^>]*>>>`` header pattern would stop at prematurely. Matching up to the first literal
    # ``>>>`` triple instead is what actually terminates the fence.
    r"<<<AURA-DATA:[0-9a-f]+.*?>>>\s*(.*?)\s*<<<END-AURA-DATA:[0-9a-f]+>>>", re.S
)


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
            # Image-capable by default: echo is a test/dev provider and a pure function of its
            # input, images included. A test that needs a text-only model passes capabilities.
            modalities=(Modality.TEXT, Modality.IMAGE),
        )
        super().__init__(name=name, capabilities=caps, timeout_s=5.0)
        self._model = model
        self._script = list(script)
        self._cursor = 0
        self._prefix = prefix
        self._chunk_size = max(1, chunk_size)
        self._healthy = healthy
        self.calls: list[CompletionRequest] = []
        #: How many times readiness called :meth:`health` (a passive provider must stay at 0).
        self.health_calls = 0

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
        text = f"{self._prefix}{' '.join(body.split())}".strip()
        # Images (ADR-031) echo as a deterministic description, so a test can assert which
        # images reached the model without any model at all.
        seen = " ".join(
            f"[image {n}: {i.width}x{i.height} {i.media_type} sha256:{i.sha256[:12]}]"
            for n, i in enumerate(request.images, start=1)
        )
        return f"{text} {seen}".strip() if seen else text

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
            # Derived from declared capabilities rather than hard-coded to zero, so a test can
            # give this provider a price and exercise cost budgeting for real.
            cost_usd=self._cost(usage),
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
        self.health_calls += 1
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


class ContextGroundedProvider(EchoProvider):
    """An :class:`EchoProvider` variant that answers from retrieved context, not the query.

    ``EchoProvider`` echoes the user's own message, which can never exercise
    :class:`~aura_core.evaluation.harness.AnswerEvaluator`'s groundedness or citation-precision
    scoring: retrieved chunks are rendered into a *system*-channel ``<<<AURA-DATA:...>>>`` fence
    (``aura_core/domain/context.py``), never into the echoed message, so with plain
    ``EchoProvider`` those scores are either undefined (no citation-bearing sentence ever appears)
    or an artifact of incidental word overlap between the query and the source -- not a signal a
    regression could move. This provider is still a scripted stand-in, not a model -- it does no
    reasoning -- but it stands in for a model that actually reads what it was shown: it pulls
    content words from the fenced blocks and answers with those, so a real regression in
    retrieval, context assembly, or citation wiring produces a real, measurable drop instead of a
    number that can never change either way.

    The excerpt is deliberately broken into short runs rather than quoted as whole sentences.
    ``SystemPromptLeakGuard`` (``aura_core/safety/guards.py``) flags an answer that shares two or
    more identical 8-word runs with the system prompt -- correctly so in general, since a model
    that parrots its context back verbatim is indistinguishable from one leaking its instructions
    -- and the fenced knowledge this provider reads *is* part of that same system prompt. Capping
    each run at six words keeps every real regression this provider is meant to catch (retrieval
    returning nothing, citations not resolving, fencing breaking) visible in the score, without
    tripping a guard that exists to catch something else.
    """

    def _derive(self, request: CompletionRequest) -> str:
        fenced = "\n".join(
            m.content for m in request.conversation.system_messages
            if "<<<AURA-DATA:" in m.content
        )
        # Every run goes into one flat list joined by the same "[...]" break, including the
        # boundary *between* sentences -- two adjacent sentences from the same source are
        # themselves a long real run, so joining their word-chunks with a plain space would
        # silently reconstruct one and trip the leak guard anyway.
        runs: list[str] = []
        for block in _DATA_BLOCK.findall(fenced)[:2]:
            sentences = [
                s.strip() for s in re.split(r"(?<=[.!?])\s+", block.strip()) if s.strip()
            ]
            for sentence in sentences[:2]:
                words = sentence.split()
                runs.extend(" ".join(words[i : i + 6]) for i in range(0, len(words), 6))
        if not runs:
            return super()._derive(request)
        return f"{self._prefix}{' [...] '.join(runs)}".strip()
