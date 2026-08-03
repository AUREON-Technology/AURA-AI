"""LLM request/response value objects and provider capability descriptors.

These types are what the :class:`aura_core.ports.llm.LLMProvider` port speaks. They are vendor
neutral by construction: nothing here mentions Gemini, OpenAI, or Ollama. Adapters translate
in both directions, which is what makes ADR-003's "swap providers by configuration" real.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Sequence

from .messages import Conversation, ToolCall

__all__ = [
    "FinishReason",
    "RoutingPolicy",
    "TokenUsage",
    "SamplingParams",
    "ToolChoice",
    "ProviderCapabilities",
    "CompletionRequest",
    "CompletionResponse",
    "StreamChunk",
    "EmbeddingResult",
]


class FinishReason(str, enum.Enum):
    STOP = "stop"
    LENGTH = "length"
    TOOL_CALLS = "tool_calls"
    CONTENT_FILTER = "content_filter"
    ERROR = "error"

    def __str__(self) -> str:
        return self.value


class RoutingPolicy(str, enum.Enum):
    """How the router orders candidate providers (ADR-003).

    ``LOCAL_FIRST``   -- prefer on-premise inference; the privacy-preserving default for
                         prompts containing personal data.
    ``CHEAPEST``      -- lowest projected cost among providers meeting the capability floor.
    ``FASTEST``       -- lowest EWMA time-to-first-token.
    ``QUALITY_FIRST`` -- highest declared quality tier, cost secondary.
    ``BALANCED``      -- normalised blend of cost, latency, and quality.
    """

    LOCAL_FIRST = "local_first"
    CHEAPEST = "cheapest"
    FASTEST = "fastest"
    QUALITY_FIRST = "quality_first"
    BALANCED = "balanced"

    def __str__(self) -> str:
        return self.value


class ToolChoice(str, enum.Enum):
    AUTO = "auto"
    NONE = "none"
    REQUIRED = "required"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            self.prompt_tokens + other.prompt_tokens,
            self.completion_tokens + other.completion_tokens,
        )

    def cost_usd(self, input_per_1k: float, output_per_1k: float) -> float:
        return (self.prompt_tokens / 1000.0) * input_per_1k + (
            self.completion_tokens / 1000.0
        ) * output_per_1k


@dataclass(frozen=True, slots=True)
class SamplingParams:
    """Generation parameters, normalised across vendors.

    ``temperature == 0`` is meaningful beyond determinism: it is the gate for semantic response
    caching (ADR-003). Caching a sampled generation would make non-determinism *inconsistent*
    rather than absent, which is worse than not caching.
    """

    temperature: float = 0.7
    top_p: float = 1.0
    max_output_tokens: int | None = None
    stop: tuple[str, ...] = ()
    seed: int | None = None
    presence_penalty: float = 0.0
    frequency_penalty: float = 0.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.temperature <= 2.0:
            raise ValueError("temperature must be within [0, 2]")
        if not 0.0 < self.top_p <= 1.0:
            raise ValueError("top_p must be within (0, 1]")
        if self.max_output_tokens is not None and self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive when set")

    @property
    def is_deterministic(self) -> bool:
        return self.temperature == 0.0

    def cache_key_parts(self) -> tuple[Any, ...]:
        return (
            self.temperature,
            self.top_p,
            self.max_output_tokens,
            self.stop,
            self.seed,
            self.presence_penalty,
            self.frequency_penalty,
        )


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    """What a provider can do and what it costs.

    The router filters candidates on these before ordering them. Accuracy matters: a wrong
    ``context_window`` causes truncation surprises, so every adapter is required to pass the
    contract test suite that asserts these are declared and self-consistent.
    """

    model_id: str
    context_window: int
    max_output_tokens: int
    supports_streaming: bool = True
    supports_tools: bool = False
    supports_json_mode: bool = False
    supports_vision: bool = False
    supports_embeddings: bool = False
    embedding_dimension: int | None = None
    is_local: bool = False
    quality_tier: int = 2  # 1 = small/fast, 2 = mid, 3 = frontier
    cost_per_1k_input_usd: float = 0.0
    cost_per_1k_output_usd: float = 0.0
    max_concurrency: int = 8

    def __post_init__(self) -> None:
        if self.context_window <= 0:
            raise ValueError("context_window must be positive")
        if self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        if self.max_output_tokens > self.context_window:
            raise ValueError("max_output_tokens cannot exceed context_window")
        if self.supports_embeddings and not self.embedding_dimension:
            raise ValueError("providers declaring embedding support must declare a dimension")
        if not 1 <= self.quality_tier <= 3:
            raise ValueError("quality_tier must be 1, 2, or 3")

    def satisfies(
        self,
        *,
        needs_tools: bool = False,
        needs_streaming: bool = False,
        needs_json: bool = False,
        needs_vision: bool = False,
        min_context: int = 0,
        min_quality_tier: int = 1,
    ) -> bool:
        if needs_tools and not self.supports_tools:
            return False
        if needs_streaming and not self.supports_streaming:
            return False
        if needs_json and not self.supports_json_mode:
            return False
        if needs_vision and not self.supports_vision:
            return False
        if self.context_window < min_context:
            return False
        return self.quality_tier >= min_quality_tier

    def projected_cost_usd(self, prompt_tokens: int, expected_output_tokens: int) -> float:
        return TokenUsage(prompt_tokens, expected_output_tokens).cost_usd(
            self.cost_per_1k_input_usd, self.cost_per_1k_output_usd
        )


@dataclass(frozen=True, slots=True)
class CompletionRequest:
    """Everything a provider needs for one generation.

    ``tools`` holds JSON-schema tool declarations (produced by ``tools/schema.py``), not Python
    callables -- the provider must never be able to execute anything. Execution is the
    execution engine's job, behind policy checks.
    """

    conversation: Conversation
    model: str | None = None
    sampling: SamplingParams = field(default_factory=SamplingParams)
    tools: tuple[Mapping[str, Any], ...] = ()
    tool_choice: ToolChoice = ToolChoice.AUTO
    response_schema: Mapping[str, Any] | None = None
    # None means "use the router's configured default". A concrete value here is an
    # explicit per-request override -- defaulting to BALANCED would silently mask whatever
    # the operator configured, making AURA_LLM_ROUTING_POLICY dead configuration.
    policy: RoutingPolicy | None = None
    min_quality_tier: int = 1
    require_local: bool = False
    timeout_s: float = 60.0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def with_conversation(self, conversation: Conversation) -> "CompletionRequest":
        return replace(self, conversation=conversation)

    @property
    def needs_tools(self) -> bool:
        return bool(self.tools)

    @property
    def needs_json(self) -> bool:
        return self.response_schema is not None


@dataclass(frozen=True, slots=True)
class CompletionResponse:
    """A finished generation, with attribution.

    ``provider``/``model`` are always populated so the client and the telemetry record know
    which engine answered -- the current system gives no such signal, so a Gemini answer and an
    Ollama fallback are indistinguishable downstream.
    """

    text: str
    provider: str
    model: str
    finish_reason: FinishReason = FinishReason.STOP
    usage: TokenUsage = field(default_factory=TokenUsage)
    tool_calls: tuple[ToolCall, ...] = ()
    latency_ms: float = 0.0
    ttft_ms: float | None = None
    cached: bool = False
    cache_tier: str | None = None
    cost_usd: float = 0.0
    raw_metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


@dataclass(frozen=True, slots=True)
class StreamChunk:
    """One streamed increment.

    Streaming carries structure, not just text: ``tool_calls`` arrive as their own chunk kind so
    the gateway emits a typed SSE frame rather than asking the client to string-split an
    ``[ACTIONS]`` marker out of the token stream (TD-12).
    """

    delta: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: FinishReason | None = None
    usage: TokenUsage | None = None
    provider: str | None = None
    model: str | None = None

    @property
    def is_final(self) -> bool:
        return self.finish_reason is not None


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    """Embeddings plus the identity of the model that produced them.

    Carrying ``model_id`` and ``dimension`` here is what lets the index manager refuse to mix
    embedding spaces (ADR-007). Vectors are meaningless without knowing their model.
    """

    vectors: tuple[tuple[float, ...], ...]
    model_id: str
    dimension: int
    usage: TokenUsage = field(default_factory=TokenUsage)
    provider: str | None = None

    def __post_init__(self) -> None:
        for vector in self.vectors:
            if len(vector) != self.dimension:
                raise ValueError(
                    f"embedding dimension mismatch: declared {self.dimension}, got {len(vector)}"
                )

    def __len__(self) -> int:
        return len(self.vectors)

    @property
    def first(self) -> tuple[float, ...]:
        if not self.vectors:
            raise ValueError("EmbeddingResult is empty")
        return self.vectors[0]

    @classmethod
    def single(
        cls, vector: Sequence[float], *, model_id: str, provider: str | None = None
    ) -> "EmbeddingResult":
        vec = tuple(float(v) for v in vector)
        return cls((vec,), model_id=model_id, dimension=len(vec), provider=provider)
