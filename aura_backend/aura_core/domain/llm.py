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
    "ModelPool",
    "Modality",
    "WorkloadClass",
    "WorkloadHint",
    "GenerationTiming",
    "DataClassification",
    "GENERATIVE_POOLS",
    "FALLBACK_POOLS",
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


class ModelPool(str, enum.Enum):
    """A specialised serving pool a provider belongs to (roadmap P2.3).

    A *pool* is a job a model is deployed for, not a vendor or a host: one vLLM server running a
    coder model is the ``coder`` pool, whichever box it sits on. A provider may declare several
    pools (a mid-size instruct model is reasonably both ``general-fast`` and ``reasoning``), and
    declaring none keeps it a generalist -- which is what every provider configured before pools
    existed is, so their routing is unchanged.

    ``embedding``/``reranker``/``asr``/``tts`` are declared so the whole serving topology can be
    described as data in one place; they are not text-generation pools, so the chat router never
    sends a completion to a provider that declares only those.
    """

    GENERAL_FAST = "general-fast"
    GENERAL_QUALITY = "general-quality"
    REASONING = "reasoning"
    CODER = "coder"
    VISION = "vision"
    EMBEDDING = "embedding"
    RERANKER = "reranker"
    ASR = "asr"
    TTS = "tts"

    def __str__(self) -> str:
        return self.value


#: Pools that serve chat-style text generation. A provider declaring *only* pools outside this
#: set (an embedding server, a TTS server) is never a completion candidate.
GENERATIVE_POOLS = frozenset(
    {
        ModelPool.GENERAL_FAST,
        ModelPool.GENERAL_QUALITY,
        ModelPool.REASONING,
        ModelPool.CODER,
        ModelPool.VISION,
    }
)

#: Pools whose providers may stand in for any other generative pool when that pool has no
#: eligible member. A general instruct model answering an Engineer turn is worse than the coder
#: model; it is much better than a 503 while the coder pool restarts.
FALLBACK_POOLS = frozenset({ModelPool.GENERAL_FAST, ModelPool.GENERAL_QUALITY})


class Modality(str, enum.Enum):
    """Input modality a model can consume."""

    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"

    def __str__(self) -> str:
        return self.value


class WorkloadClass(str, enum.Enum):
    """Who is waiting on a generation, which decides its admission priority (roadmap P2.4).

    Priorities are lower-is-sooner. Interactive chat and voice share the top class because a
    person is waiting on both; background work yields to them whenever capacity frees.
    """

    INTERACTIVE = "interactive"
    VOICE = "voice"
    ENGINEER = "engineer"
    BACKGROUND_INSIGHT = "background_insight"
    RAG_INDEXING = "rag_indexing"
    TRAINING = "training"

    def __str__(self) -> str:
        return self.value

    @property
    def priority(self) -> int:
        return _WORKLOAD_PRIORITY[self]


class DataClassification(str, enum.Enum):
    """How private the content of a generation is, which decides who may see it (ADR-030 §4).

    Ordered: a provider that accepts ``INTERNAL`` also accepts ``PUBLIC``, never ``SENSITIVE``.
    Self-hosted providers accept everything; a hosted open-weight provider accepts ``INTERNAL``
    unless the operator raises it.

    * ``PUBLIC`` -- nothing of the user's in the prompt (evaluation fixtures, canned text).
    * ``INTERNAL`` -- the user's own message and an untainted conversation.
    * ``SENSITIVE`` -- anything drawn from what the user stored or connected: documents, memory,
      goals, tool output, Engineer workspaces, a calling system's evidence. Also what an
      unclassified generation is treated as, so background work over user data stays local by
      default.
    """

    PUBLIC = "public"
    INTERNAL = "internal"
    SENSITIVE = "sensitive"

    def __str__(self) -> str:
        return self.value

    @property
    def rank(self) -> int:
        return _CLASSIFICATION_RANK[self]

    def permits(self, content: "DataClassification") -> bool:
        """Whether a provider whose ceiling is ``self`` may receive ``content``."""
        return content.rank <= self.rank


_CLASSIFICATION_RANK = {
    DataClassification.PUBLIC: 0,
    DataClassification.INTERNAL: 1,
    DataClassification.SENSITIVE: 2,
}


#: Share of a generation's deadline counted as decode time when sizing its output; the rest is
#: prefill and network. See :meth:`ProviderCapabilities.finishes_within`.
_DECODE_SHARE_OF_DEADLINE = 0.8


_WORKLOAD_PRIORITY = {
    WorkloadClass.INTERACTIVE: 1,
    WorkloadClass.VOICE: 1,
    WorkloadClass.ENGINEER: 2,
    WorkloadClass.BACKGROUND_INSIGHT: 3,
    WorkloadClass.RAG_INDEXING: 4,
    WorkloadClass.TRAINING: 5,
}


@dataclass(frozen=True, slots=True)
class WorkloadHint:
    """Caller-declared requirements and priority for one generation.

    A hint rather than a classifier: the caller already knows what it is (the kernel knows the
    turn is an Engineer turn; the ingestion pipeline knows it is indexing), so guessing it back
    from the prompt would add latency and a new way to be wrong. Every field defaults to "no
    requirement", so a request without a hint routes exactly as it did before hints existed.

    ``user_key`` is an opaque per-user key for the concurrent-generation quota. It never becomes
    a metric label (see ``observability/metrics.py``'s cardinality rules).
    """

    workload: WorkloadClass = WorkloadClass.INTERACTIVE
    pool: ModelPool | None = None
    #: Refuse to fall back to a generalist when no provider in ``pool`` is eligible.
    strict_pool: bool = False
    modalities: tuple[Modality, ...] = ()
    #: ISO-639-1 code (``"ar"``, ``"en"``); ``None`` means no language requirement.
    language: str | None = None
    needs_reasoning: bool = False
    #: Minimum total context the provider must offer, independent of the router's own
    #: prompt-plus-output estimate.
    min_context: int = 0
    user_key: str | None = None
    #: Explicit override of the workload class's priority (1 = most urgent).
    priority_override: int | None = None

    def __post_init__(self) -> None:
        if self.priority_override is not None and not 1 <= self.priority_override <= 9:
            raise ValueError("priority_override must be within [1, 9]")
        if self.min_context < 0:
            raise ValueError("min_context cannot be negative")

    @property
    def priority(self) -> int:
        return self.priority_override or self.workload.priority

    @property
    def pool_label(self) -> str:
        """Bounded metric-label value for the requested pool."""
        return self.pool.value if self.pool is not None else "default"


@dataclass(frozen=True, slots=True)
class GenerationTiming:
    """Latency decomposition of one provider attempt (roadmap P2.5).

    Produced by the router and handed to the metric catalogue as a value rather than emitted
    piecemeal, per ADR-009: the component that measures returns a record, the observability
    layer records it. Carries no prompt or response content -- durations, token counts, and
    bounded labels only.

    ``ttft_ms``/``decode_ms`` exist only for streamed generations; a non-streaming call reports
    its whole provider time as ``service_ms``.
    """

    provider: str
    model: str
    pool: str
    priority: int
    outcome: str
    streamed: bool
    queue_wait_ms: float
    service_ms: float
    ttft_ms: float | None = None
    decode_ms: float | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_ms(self) -> float:
        return self.queue_wait_ms + self.service_ms

    @property
    def tokens_per_second(self) -> float | None:
        """Output throughput over the decode phase (the whole call when not streamed)."""
        window_ms = self.decode_ms if self.streamed and self.decode_ms else self.service_ms
        if not self.completion_tokens or not window_ms or window_ms <= 0:
            return None
        return self.completion_tokens / (window_ms / 1000.0)


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
    #: Whether the serving stack can reuse a KV prefix for a stable prompt head (ADR-010).
    #: Declared for a later vLLM/llama.cpp adapter; unused on the current Ollama path.
    supports_prefix_cache: bool = False
    embedding_dimension: int | None = None
    is_local: bool = False
    quality_tier: int = 2  # 1 = small/fast, 2 = mid, 3 = frontier
    cost_per_1k_input_usd: float = 0.0
    cost_per_1k_output_usd: float = 0.0
    max_concurrency: int = 8
    # -- model-level metadata (roadmap P2.3). Every default means "undeclared", and an
    # undeclared field never excludes a provider, so configurations written before these existed
    # route exactly as they did. ``context_window`` is the model's context capacity and
    # ``supports_tools`` its tool support; neither is duplicated here.
    #: Serving pools this provider belongs to; empty = generalist.
    pools: tuple[ModelPool, ...] = ()
    #: 1 = slow, 2 = typical, 3 = fast. Seeds the latency prior before anything is measured.
    speed_tier: int = 2
    #: Relative resource cost (unitless, operator-assigned). Lets CHEAPEST/BALANCED prefer the
    #: cheaper of two $0 self-hosted models -- a 7B on one GPU over a 70B on four.
    memory_cost: float = 0.0
    gpu_cost: float = 0.0
    modalities: tuple[Modality, ...] = (Modality.TEXT,)
    supports_reasoning: bool = False
    #: ISO-639-1 codes the model is trusted in; empty = no declared restriction.
    languages: tuple[str, ...] = ()
    #: The exact weights revision served (a Hugging Face commit SHA for vLLM ``--revision``).
    #: Informational -- the router does not filter on it -- but it is what an incident review
    #: needs in order to know which weights actually answered.
    model_revision: str = ""
    #: Providers sharing a group share one admission queue and concurrency cap -- two model
    #: names served by the same Ollama process are one capacity, not two. Empty = own group.
    capacity_group: str = ""
    #: The most private content this provider may receive (ADR-030 §4). Defaults to everything:
    #: a self-hosted provider sees what the user stored by design.
    max_data_classification: DataClassification = DataClassification.SENSITIVE
    #: A third party runs this provider and can read its prompts (a hosted open-weight API).
    #: Each generation it serves is audited with its classification and token counts.
    third_party: bool = False
    #: The operator accepted that this provider may train on what it receives (ADR-030 §3).
    #: Never set without ``third_party``; recorded on every audited generation.
    may_train_on_prompts: bool = False
    #: Sustained decode throughput, in output tokens per second (ADR-030 Phase 3). 0 means
    #: undeclared. Used only to decide whether a request's *preferred* output size can finish
    #: inside its deadline (see :meth:`output_budget`); an undeclared provider never gets more
    #: than the request's plain ``max_output_tokens``. That is the conservative default the
    #: CPU-only provider's planner sizing depends on.
    tokens_per_second: float = 0.0

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
        if not 1 <= self.speed_tier <= 3:
            raise ValueError("speed_tier must be 1, 2, or 3")
        if self.memory_cost < 0 or self.gpu_cost < 0:
            raise ValueError("memory_cost and gpu_cost cannot be negative")
        if not self.modalities:
            raise ValueError("a provider must declare at least one input modality")
        if self.tokens_per_second < 0:
            raise ValueError("tokens_per_second cannot be negative")

    @property
    def context_capacity(self) -> int:
        """The P2.3 metadata name for ``context_window``."""
        return self.context_window

    @property
    def effective_modalities(self) -> frozenset[Modality]:
        mods = set(self.modalities)
        if self.supports_vision:
            mods.add(Modality.IMAGE)
        return frozenset(mods)

    @property
    def is_generative(self) -> bool:
        """False for a provider that declares only non-chat pools (embedding, TTS, ...)."""
        return not self.pools or any(p in GENERATIVE_POOLS for p in self.pools)

    @property
    def resource_cost(self) -> float:
        """The two operator-assigned resource costs as one relative number."""
        return self.gpu_cost + 0.5 * self.memory_cost

    def serves_language(self, language: str | None) -> bool:
        if not language or not self.languages:
            return True
        wanted = language.split("-")[0].lower()
        return any(code.split("-")[0].lower() == wanted for code in self.languages)

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

    def finishes_within(self, output_tokens: int, seconds: float) -> bool:
        """Whether ``output_tokens`` decode inside ``seconds`` at the declared throughput.

        Only 80% of the deadline is counted as decode time; the rest covers prefill and the
        network. False when throughput is undeclared: an unknown speed proves nothing.
        """
        if self.tokens_per_second <= 0 or seconds <= 0:
            return False
        return output_tokens <= self.tokens_per_second * seconds * _DECODE_SHARE_OF_DEADLINE

    def output_budget(
        self, requested: int | None, preferred: int | None = None, deadline_s: float | None = None
    ) -> int:
        """How many output tokens this provider should be asked for.

        ``requested`` is the size that is safe everywhere. ``preferred`` is what the caller
        would rather have, granted only by a provider whose declared speed finishes it inside
        ``deadline_s``. Both are capped at ``max_output_tokens``. The payload and the quota
        estimate both call this one method. The estimate passes the request's own deadline,
        which is never shorter than the one the provider applies, so it can only over-count
        what a request may cost, never under-count it.
        """
        budget = requested or self.max_output_tokens
        if preferred and preferred > budget and deadline_s and self.finishes_within(preferred, deadline_s):
            budget = preferred
        return min(budget, self.max_output_tokens)

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
    #: Token count of a stable prompt prefix (CAG). Unused until a provider with
    #: ``supports_prefix_cache`` is wired; kept so adapters need no domain change.
    prefix_tokens: int | None = None
    #: Provider-specific cache-control hint (e.g. OpenAI/Anthropic/vLLM). Unused today.
    cache_control: Mapping[str, Any] | None = None
    #: Pool/priority/capability hint (P2.3/P2.4). ``None`` falls back to the ambient hint the
    #: kernel sets for the turn (``aura_core.llm.workload``), then to "no requirement".
    workload: WorkloadHint | None = None
    #: A larger output size the caller would prefer, granted only by a provider that declares a
    #: decode speed fast enough to finish it inside ``timeout_s`` (ADR-030 Phase 3). The planner
    #: asks for 700 tokens, which a CPU provider finishes inside 180s, and prefers 1200, which a
    #: hosted GPU provider finishes in seconds. ``None`` means ``sampling.max_output_tokens``
    #: everywhere, which is the behaviour before this field existed.
    preferred_max_output_tokens: int | None = None
    #: How private this request's content is (ADR-030 §4). Combined with the ambient
    #: classification the kernel sets for the turn, and the stricter of the two wins; with
    #: neither, the request counts as ``SENSITIVE``. See ``aura_core.llm.sensitivity``.
    data_classification: DataClassification | None = None

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
    #: Time spent waiting for an admission slot before the provider was called (P2.5).
    queue_wait_ms: float = 0.0

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
