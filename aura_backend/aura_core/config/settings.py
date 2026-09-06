"""Typed configuration with fail-fast validation.

Deliberately built on dataclasses and ``os.environ`` rather than pydantic-settings. Two reasons:

1. ``aura_core`` stays importable with zero third-party packages, which is what lets the whole
   cognitive core be unit-tested on bare CPython.
2. The validation this system needs is cross-field and environment-aware ("``debug`` must be
   false when ``environment`` is production"), which is imperative logic either way.

Every setting is read from ``AURA_``-prefixed environment variables. The production profile
**refuses to start** on insecure combinations -- the direct fix for SEC-05, where a single
``debug_mode`` flag silently turns every endpoint into an unauthenticated one.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

from ..domain.errors import ConfigurationError
from ..domain.llm import RoutingPolicy
from .external_inference import is_external_inference_host, is_remote_inference_host

# Stdlib logging rather than aura_core.observability: settings is the lowest layer here and
# the observability module reads configuration, so importing it would risk a cycle for the
# sake of one warning.
logger = logging.getLogger("aura_core.config")

__all__ = [
    "Environment",
    "AuraSettings",
    "LLMSettings",
    "ProviderConfig",
    "KnowledgeSettings",
    "CagSettings",
    "MemorySettings",
    "SafetySettings",
    "ToolSettings",
    "ReasoningSettings",
    "ObservabilitySettings",
    "SecuritySettings",
    "InfrastructureSettings",
    "LearningSettings",
    "VoiceSettings",
    "McpServerConfig",
    "McpSettings",
    "GitHubSettings",
    "SandboxSettings",
    "load_settings",
]

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


class Environment(str, Enum):
    LOCAL = "local"
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"

    def __str__(self) -> str:
        return self.value

    @property
    def is_production_like(self) -> bool:
        return self in {Environment.STAGING, Environment.PRODUCTION}


def _env(key: str, default: str | None = None) -> str | None:
    return os.environ.get(f"AURA_{key}", default)


def _bool(key: str, default: bool) -> bool:
    raw = _env(key)
    if raw is None:
        return default
    lowered = raw.strip().lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise ConfigurationError(
        f"AURA_{key} must be a boolean", details={"value": raw, "accepted": sorted(_TRUE | _FALSE)}
    )


def _bool_aliased(key: str, default: bool) -> bool:
    """Read ``AURA_{key}``, then the unprefixed ``{key}``.

    Compose historically exported a handful of security flags without the
    ``AURA_`` prefix (``ALLOW_MOCK_ATTESTATION``). Prefer the prefixed name
    when both are set.
    """
    raw = os.environ.get(f"AURA_{key}")
    if raw is None:
        raw = os.environ.get(key)
    if raw is None:
        return default
    lowered = raw.strip().lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise ConfigurationError(
        f"{key} must be a boolean", details={"value": raw, "accepted": sorted(_TRUE | _FALSE)}
    )


def _int(key: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    raw = _env(key)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"AURA_{key} must be an integer", details={"value": raw}) from exc
    if minimum is not None and value < minimum:
        raise ConfigurationError(f"AURA_{key} must be >= {minimum}", details={"value": value})
    if maximum is not None and value > maximum:
        raise ConfigurationError(f"AURA_{key} must be <= {maximum}", details={"value": value})
    return value


def _float(key: str, default: float, *, minimum: float | None = None, maximum: float | None = None) -> float:
    raw = _env(key)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"AURA_{key} must be a number", details={"value": raw}) from exc
    if minimum is not None and value < minimum:
        raise ConfigurationError(f"AURA_{key} must be >= {minimum}", details={"value": value})
    if maximum is not None and value > maximum:
        raise ConfigurationError(f"AURA_{key} must be <= {maximum}", details={"value": value})
    return value


def _list(key: str, default: Sequence[str] = ()) -> tuple[str, ...]:
    raw = _env(key)
    if raw is None:
        return tuple(default)
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _redact_url_userinfo(value: str) -> str:
    """Replace username/password in a URL so DSNs can be shown without leaking secrets.

    ``postgresql+asyncpg://aura:secret@postgres/aura`` becomes
    ``postgresql+asyncpg://***@postgres/aura``. Strings that are not URLs, or URLs
    with no userinfo, are returned unchanged.
    """
    if "://" not in value:
        return value
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value
    if parsed.username is None and parsed.password is None:
        return value
    hostname = parsed.hostname or ""
    if ":" in hostname:
        host = f"[{hostname}]"
    else:
        host = hostname
    if parsed.port:
        host = f"{host}:{parsed.port}" if host else str(parsed.port)
    netloc = f"***@{host}" if host else "***"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


# --------------------------------------------------------------------------------------
# Sections
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    """One configured model endpoint.

    Providers are declared in ``AURA_LLM_PROVIDERS`` as a comma-separated list of names, each
    configured by ``AURA_PROVIDER_<NAME>_*`` variables. Declaring them as data rather than code
    is what makes "add an inference node" a config change (ADR-003).
    """

    name: str
    kind: str  # openai_compatible | ollama | echo
    base_url: str = ""
    model: str = ""
    api_key_env: str = ""
    is_local: bool = False
    quality_tier: int = 2
    context_window: int = 8192
    max_output_tokens: int = 2048
    supports_tools: bool = True
    supports_json_mode: bool = False
    supports_streaming: bool = True
    cost_per_1k_input_usd: float = 0.0
    cost_per_1k_output_usd: float = 0.0
    max_concurrency: int = 8
    timeout_s: float = 60.0
    weight: int = 1
    enabled: bool = True

    # Self-hosted runtimes only. There is deliberately no vendor kind here: `ollama` and
    # `openai_compatible` between them cover Ollama, vLLM, llama.cpp's server, LocalAI, TGI and
    # anything else exposing the OpenAI wire format, all of which run on our own hardware.
    _KINDS = frozenset({"openai_compatible", "ollama", "echo"})

    def __post_init__(self) -> None:
        if self.kind not in self._KINDS:
            raise ConfigurationError(
                f"provider {self.name!r} has unknown kind {self.kind!r}",
                details={
                    "supported": sorted(self._KINDS),
                    "note": "external inference providers are not supported; run the model locally",
                },
            )
        if self.kind != "echo" and not self.model:
            raise ConfigurationError(f"provider {self.name!r} must declare a model")
        if self.kind in {"openai_compatible", "ollama"} and not self.base_url:
            raise ConfigurationError(f"provider {self.name!r} must declare a base_url")
        self._reject_external_base_url()

    def _reject_external_base_url(self) -> None:
        """Refuse a base_url on a hosted inference service; gate one on rented compute.

        ``openai_compatible`` speaks the same protocol as several hosted APIs, so deleting the
        vendor adapters was not on its own sufficient -- a base_url is all it would take to route
        every turn back out to a third party. The host lists are shared with the architecture
        fitness tests so the runtime check and the static scan cannot disagree.

        Two categories, and the difference matters:

        * A **banned** host runs somebody else's model. Always refused, no override.
        * A **gated** host runs *your* container and *your* weights on rented infrastructure --
          a Hugging Face Space, Cloud Run, Fly. Refused *unless* ``AURA_ALLOW_REMOTE_INFERENCE``
          is true, because it is a topology an operator may legitimately choose and should have
          to choose on purpose. Two rules stop holding when they do, and the error says which.
        """
        if not self.base_url:
            return
        host = urlsplit(self.base_url).hostname or ""

        banned = is_external_inference_host(host)
        if banned:
            raise ConfigurationError(
                f"provider {self.name!r} points at {host!r}, which is a hosted inference "
                f"service. All inference must run on our own infrastructure.",
                details={"provider": self.name, "host": host, "matched": banned},
            )

        gated = is_remote_inference_host(host)
        if gated and not _bool("ALLOW_REMOTE_INFERENCE", False):
            raise ConfigurationError(
                f"provider {self.name!r} points at {host!r}, which is rented infrastructure "
                f"rather than your own. This is allowed, but not by accident: inference there "
                f"means the system stops working without internet, and prompts plus retrieved "
                f"document content leave infrastructure you control. Set "
                f"AURA_ALLOW_REMOTE_INFERENCE=true to accept that, or point at a host you own.",
                details={"provider": self.name, "host": host, "matched": gated},
            )
        if gated:
            # Logged at warning on every boot, deliberately. A trade-off accepted once should
            # stay visible to whoever operates the system afterwards, who may not have been the
            # person who accepted it.
            logger.warning(
                "remote inference host permitted by AURA_ALLOW_REMOTE_INFERENCE",
                extra={"provider": self.name, "host": host, "matched": gated},
            )

    @property
    def api_key(self) -> str:
        """Resolved at read time from the named environment variable.

        The key is never stored on the settings object, so it cannot be dumped by
        ``settings.as_dict()``, logged in a config echo, or captured in a Sentry breadcrumb.
        """
        return os.environ.get(self.api_key_env, "") if self.api_key_env else ""

    @classmethod
    def from_env(cls, name: str) -> "ProviderConfig":
        p = f"PROVIDER_{name.upper().replace('-', '_')}"
        return cls(
            name=name,
            kind=_env(f"{p}_KIND", "openai_compatible") or "openai_compatible",
            base_url=_env(f"{p}_BASE_URL", "") or "",
            model=_env(f"{p}_MODEL", "") or "",
            api_key_env=_env(f"{p}_API_KEY_ENV", "") or "",
            is_local=_bool(f"{p}_IS_LOCAL", False),
            quality_tier=_int(f"{p}_QUALITY_TIER", 2, minimum=1, maximum=3),
            context_window=_int(f"{p}_CONTEXT_WINDOW", 8192, minimum=512),
            max_output_tokens=_int(f"{p}_MAX_OUTPUT_TOKENS", 2048, minimum=16),
            supports_tools=_bool(f"{p}_SUPPORTS_TOOLS", True),
            supports_json_mode=_bool(f"{p}_SUPPORTS_JSON_MODE", False),
            supports_streaming=_bool(f"{p}_SUPPORTS_STREAMING", True),
            cost_per_1k_input_usd=_float(f"{p}_COST_INPUT", 0.0, minimum=0.0),
            cost_per_1k_output_usd=_float(f"{p}_COST_OUTPUT", 0.0, minimum=0.0),
            max_concurrency=_int(f"{p}_MAX_CONCURRENCY", 8, minimum=1),
            timeout_s=_float(f"{p}_TIMEOUT_S", 60.0, minimum=1.0),
            weight=_int(f"{p}_WEIGHT", 1, minimum=1),
            enabled=_bool(f"{p}_ENABLED", True),
        )


@dataclass(frozen=True, slots=True)
class LLMSettings:
    providers: tuple[ProviderConfig, ...] = ()
    default_policy: RoutingPolicy = RoutingPolicy.BALANCED
    embedding_provider: str = "local-embeddings"
    embedding_model: str = "hash-embed-v1"
    embedding_dimension: int = 384
    embedding_batch_size: int = 64
    request_timeout_s: float = 60.0
    max_fallbacks: int = 2
    circuit_failure_threshold: int = 5
    circuit_open_seconds: float = 30.0
    cache_enabled: bool = True
    cache_ttl_s: int = 3600
    semantic_cache_enabled: bool = True
    semantic_cache_threshold: float = 0.97
    max_generations_per_turn: int = 8
    #: Assembled prompt cap (prefill). Independent of the provider context window so a local
    #: 8k model does not ingest 1k+ token RAG prompts that CPU Ollama cannot evaluate in time.
    max_prompt_tokens: int = 400

    #: Used when nothing is configured, so ``aura_core`` is importable and testable out of the
    #: box. ``validate()`` refuses to let it run in staging/production.
    ECHO_PROVIDER = ProviderConfig(
        name="echo", kind="echo", model="echo-1", context_window=8192, max_output_tokens=2048
    )

    @classmethod
    def from_env(cls) -> "LLMSettings":
        names = _list("LLM_PROVIDERS")
        providers = (
            tuple(ProviderConfig.from_env(n) for n in names) if names else (cls.ECHO_PROVIDER,)
        )
        policy_raw = (_env("LLM_ROUTING_POLICY", "balanced") or "balanced").lower()
        try:
            policy = RoutingPolicy(policy_raw)
        except ValueError as exc:
            raise ConfigurationError(
                "AURA_LLM_ROUTING_POLICY is not a known policy",
                details={"value": policy_raw, "supported": [p.value for p in RoutingPolicy]},
            ) from exc
        return cls(
            providers=providers,
            default_policy=policy,
            embedding_provider=_env("EMBEDDING_PROVIDER", "local-embeddings") or "local-embeddings",
            embedding_model=_env("EMBEDDING_MODEL", "hash-embed-v1") or "hash-embed-v1",
            embedding_dimension=_int("EMBEDDING_DIMENSION", 384, minimum=16),
            embedding_batch_size=_int("EMBEDDING_BATCH_SIZE", 64, minimum=1, maximum=1024),
            request_timeout_s=_float("LLM_TIMEOUT_S", 60.0, minimum=1.0),
            max_fallbacks=_int("LLM_MAX_FALLBACKS", 2, minimum=0, maximum=5),
            circuit_failure_threshold=_int("LLM_CIRCUIT_THRESHOLD", 5, minimum=1),
            circuit_open_seconds=_float("LLM_CIRCUIT_OPEN_S", 30.0, minimum=1.0),
            cache_enabled=_bool("LLM_CACHE_ENABLED", True),
            cache_ttl_s=_int("LLM_CACHE_TTL_S", 3600, minimum=1),
            semantic_cache_enabled=_bool("LLM_SEMANTIC_CACHE_ENABLED", True),
            semantic_cache_threshold=_float("LLM_SEMANTIC_CACHE_THRESHOLD", 0.97, minimum=0.5, maximum=1.0),
            max_generations_per_turn=_int("LLM_MAX_GENERATIONS_PER_TURN", 8, minimum=1, maximum=64),
            max_prompt_tokens=_int("LLM_MAX_PROMPT_TOKENS", 400, minimum=64, maximum=32768),
        )

    @property
    def enabled_providers(self) -> tuple[ProviderConfig, ...]:
        return tuple(p for p in self.providers if p.enabled)


@dataclass(frozen=True, slots=True)
class KnowledgeSettings:
    index_alias: str = "aura_knowledge"
    schema_version: int = 1
    chunk_target_tokens: int = 320
    chunk_overlap_sentences: int = 1
    chunk_max_tokens: int = 512
    retrieval_top_k: int = 2
    retrieval_candidate_k: int = 24
    score_threshold: float = 0.35
    hybrid_enabled: bool = True
    rrf_k: int = 60
    rerank_enabled: bool = True
    #: "heuristic" (default) or "cross_encoder". The cross-encoder scores query/passage pairs
    #: jointly and ranks materially better on vocabulary mismatch, but adds 100-300 ms per
    #: retrieval on CPU -- a real cost on a box also running a 7B generator, which is why the
    #: zero-dependency heuristic remains the default.
    rerank_provider: str = "heuristic"
    rerank_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    mmr_lambda: float = 0.7
    graph_enabled: bool = True
    max_document_bytes: int = 8 * 1024 * 1024
    #: Character cap applied when a chunk is *injected into the prompt*. Stored chunks stay at
    #: ``chunk_max_tokens``; this is the citation budget the model actually sees.
    prompt_chunk_max_chars: int = 280

    #: Which of the ten retrieval strategies serves a turn. "simple" is hybrid retrieval with no
    #: extra generations and is the default because it is the only one whose latency is bounded by
    #: retrieval alone -- the expansion and verification strategies each cost at least one local
    #: generation, which on CPU is seconds.
    #:
    #: "adaptive" is the recommended upgrade once an operator has capacity for it: it routes per
    #: query, so simple questions stay on the simple path.
    retrieval_strategy: str = "simple"
    #: Generations a single retrieval may spend. 0 forces every strategy to degrade to plain
    #: retrieval, which is the setting to reach for when the inference host is saturated.
    retrieval_max_generations: int = 2
    #: How many strategies the adaptive router may run in parallel for an ambiguous query.
    retrieval_max_parallel: int = 2
    #: Phase F1. The engine itself is only built when the `tesseract` binary is actually on
    #: PATH -- this flag lets an operator opt out even when it is, e.g. to keep CPU free for the
    #: generator; it can never opt *in* to an engine that was never present.
    ocr_enabled: bool = True
    ocr_lang: str = "eng"

    @classmethod
    def from_env(cls) -> "KnowledgeSettings":
        return cls(
            index_alias=_env("KNOWLEDGE_INDEX_ALIAS", "aura_knowledge") or "aura_knowledge",
            schema_version=_int("KNOWLEDGE_SCHEMA_VERSION", 1, minimum=1),
            chunk_target_tokens=_int("KNOWLEDGE_CHUNK_TOKENS", 320, minimum=32, maximum=2048),
            chunk_overlap_sentences=_int("KNOWLEDGE_CHUNK_OVERLAP_SENTENCES", 1, minimum=0, maximum=8),
            chunk_max_tokens=_int("KNOWLEDGE_CHUNK_MAX_TOKENS", 512, minimum=32, maximum=4096),
            retrieval_top_k=_int("KNOWLEDGE_TOP_K", 2, minimum=1, maximum=50),
            retrieval_candidate_k=_int("KNOWLEDGE_CANDIDATE_K", 24, minimum=1, maximum=200),
            score_threshold=_float("KNOWLEDGE_SCORE_THRESHOLD", 0.35, minimum=0.0, maximum=1.0),
            hybrid_enabled=_bool("KNOWLEDGE_HYBRID_ENABLED", True),
            retrieval_strategy=(_env("KNOWLEDGE_RETRIEVAL_STRATEGY", "simple") or "simple").lower(),
            retrieval_max_generations=_int("KNOWLEDGE_RETRIEVAL_MAX_GENERATIONS", 2, minimum=0, maximum=8),
            retrieval_max_parallel=_int("KNOWLEDGE_RETRIEVAL_MAX_PARALLEL", 2, minimum=1, maximum=5),
            rrf_k=_int("KNOWLEDGE_RRF_K", 60, minimum=1),
            rerank_enabled=_bool("KNOWLEDGE_RERANK_ENABLED", True),
            rerank_provider=(_env("KNOWLEDGE_RERANK_PROVIDER", "heuristic") or "heuristic").lower(),
            rerank_model=_env("KNOWLEDGE_RERANK_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
            or "cross-encoder/ms-marco-MiniLM-L-6-v2",
            mmr_lambda=_float("KNOWLEDGE_MMR_LAMBDA", 0.7, minimum=0.0, maximum=1.0),
            graph_enabled=_bool("KNOWLEDGE_GRAPH_ENABLED", True),
            max_document_bytes=_int("KNOWLEDGE_MAX_DOC_BYTES", 8 * 1024 * 1024, minimum=1024),
            prompt_chunk_max_chars=_int("KNOWLEDGE_PROMPT_CHUNK_CHARS", 280, minimum=64, maximum=4000),
            ocr_enabled=_bool("KNOWLEDGE_OCR_ENABLED", True),
            ocr_lang=_env("KNOWLEDGE_OCR_LANG", "eng") or "eng",
        )

    def __post_init__(self) -> None:
        if self.chunk_target_tokens > self.chunk_max_tokens:
            raise ConfigurationError(
                "chunk_target_tokens cannot exceed chunk_max_tokens",
                details={"target": self.chunk_target_tokens, "max": self.chunk_max_tokens},
            )
        if self.retrieval_top_k > self.retrieval_candidate_k:
            raise ConfigurationError(
                "retrieval_top_k cannot exceed retrieval_candidate_k -- reranking would have "
                "nothing to select from",
                details={"top_k": self.retrieval_top_k, "candidate_k": self.retrieval_candidate_k},
            )


@dataclass(frozen=True, slots=True)
class CagSettings:
    """Cache-Augmented Generation prefix (ADR-010).

    Reserves a slice of the 400-token prefill budget for a byte-stable knowledge prefix.
    Does not raise ``max_prompt_tokens``.
    """

    enabled: bool = True
    max_tokens: int = 80
    global_tokens: int = 40
    user_tokens: int = 40
    max_global_docs: int = 2
    max_user_docs: int = 3

    @classmethod
    def from_env(cls) -> "CagSettings":
        return cls(
            enabled=_bool("CAG_ENABLED", True),
            max_tokens=_int("CAG_MAX_TOKENS", 80, minimum=0, maximum=400),
            global_tokens=_int("CAG_GLOBAL_TOKENS", 40, minimum=0, maximum=400),
            user_tokens=_int("CAG_USER_TOKENS", 40, minimum=0, maximum=400),
            max_global_docs=_int("CAG_MAX_GLOBAL_DOCS", 2, minimum=0, maximum=10),
            max_user_docs=_int("CAG_MAX_USER_DOCS", 3, minimum=0, maximum=10),
        )

    def __post_init__(self) -> None:
        if self.global_tokens + self.user_tokens > self.max_tokens and self.max_tokens > 0:
            raise ConfigurationError(
                "cag global_tokens + user_tokens cannot exceed max_tokens",
                details={
                    "global_tokens": self.global_tokens,
                    "user_tokens": self.user_tokens,
                    "max_tokens": self.max_tokens,
                },
            )


@dataclass(frozen=True, slots=True)
class MemorySettings:
    enabled: bool = True
    recall_limit: int = 3
    recall_min_score: float = 0.25
    token_budget: int = 80
    episodic_window: int = 12
    summarise_after_turns: int = 20
    extraction_min_confidence: float = 0.7
    consolidation_enabled: bool = True
    duplicate_threshold: float = 0.94
    max_records_per_user: int = 20_000
    #: Verbatim messages injected into the model. Stored session history is unchanged.
    prompt_history_messages: int = 4

    @classmethod
    def from_env(cls) -> "MemorySettings":
        return cls(
            enabled=_bool("MEMORY_ENABLED", True),
            recall_limit=_int("MEMORY_RECALL_LIMIT", 3, minimum=1, maximum=100),
            recall_min_score=_float("MEMORY_RECALL_MIN_SCORE", 0.25, minimum=0.0, maximum=1.0),
            token_budget=_int("MEMORY_TOKEN_BUDGET", 80, minimum=0),
            episodic_window=_int("MEMORY_EPISODIC_WINDOW", 12, minimum=0, maximum=200),
            summarise_after_turns=_int("MEMORY_SUMMARISE_AFTER", 20, minimum=2),
            extraction_min_confidence=_float("MEMORY_EXTRACTION_MIN_CONFIDENCE", 0.7, minimum=0.0, maximum=1.0),
            consolidation_enabled=_bool("MEMORY_CONSOLIDATION_ENABLED", True),
            duplicate_threshold=_float("MEMORY_DUPLICATE_THRESHOLD", 0.94, minimum=0.5, maximum=1.0),
            max_records_per_user=_int("MEMORY_MAX_RECORDS_PER_USER", 20_000, minimum=100),
            prompt_history_messages=_int("MEMORY_PROMPT_HISTORY_MESSAGES", 4, minimum=0, maximum=64),
        )


@dataclass(frozen=True, slots=True)
class SafetySettings:
    enabled: bool = True
    input_guards_enabled: bool = True
    context_guards_enabled: bool = True
    output_guards_enabled: bool = True
    injection_threshold: float = 0.6
    jailbreak_threshold: float = 0.7
    pii_redact_in_output: bool = True
    pii_redact_in_memory: bool = True
    block_on_secret_leak: bool = True
    max_input_chars: int = 32_000
    groundedness_min: float = 0.4

    @classmethod
    def from_env(cls) -> "SafetySettings":
        return cls(
            enabled=_bool("SAFETY_ENABLED", True),
            input_guards_enabled=_bool("SAFETY_INPUT_GUARDS", True),
            context_guards_enabled=_bool("SAFETY_CONTEXT_GUARDS", True),
            output_guards_enabled=_bool("SAFETY_OUTPUT_GUARDS", True),
            injection_threshold=_float("SAFETY_INJECTION_THRESHOLD", 0.6, minimum=0.0, maximum=1.0),
            jailbreak_threshold=_float("SAFETY_JAILBREAK_THRESHOLD", 0.7, minimum=0.0, maximum=1.0),
            pii_redact_in_output=_bool("SAFETY_PII_REDACT_OUTPUT", True),
            pii_redact_in_memory=_bool("SAFETY_PII_REDACT_MEMORY", True),
            block_on_secret_leak=_bool("SAFETY_BLOCK_ON_SECRET_LEAK", True),
            max_input_chars=_int("SAFETY_MAX_INPUT_CHARS", 32_000, minimum=100),
            groundedness_min=_float("SAFETY_GROUNDEDNESS_MIN", 0.4, minimum=0.0, maximum=1.0),
        )


@dataclass(frozen=True, slots=True)
class ToolSettings:
    enabled: bool = True
    allowlist: tuple[str, ...] = ()
    denylist: tuple[str, ...] = ()
    max_calls_per_turn: int = 6
    default_timeout_s: float = 15.0
    max_parallel: int = 4
    require_confirmation_for_side_effects: bool = True

    @classmethod
    def from_env(cls) -> "ToolSettings":
        return cls(
            enabled=_bool("TOOLS_ENABLED", True),
            allowlist=_list("TOOLS_ALLOWLIST"),
            denylist=_list("TOOLS_DENYLIST"),
            max_calls_per_turn=_int("TOOLS_MAX_CALLS_PER_TURN", 6, minimum=0, maximum=50),
            default_timeout_s=_float("TOOLS_DEFAULT_TIMEOUT_S", 15.0, minimum=0.5),
            max_parallel=_int("TOOLS_MAX_PARALLEL", 4, minimum=1, maximum=32),
            require_confirmation_for_side_effects=_bool("TOOLS_REQUIRE_CONFIRMATION", True),
        )


@dataclass(frozen=True, slots=True)
class ReasoningSettings:
    default_strategy: str = "direct"
    auto_select: bool = True
    planning_enabled: bool = True
    max_plan_steps: int = 12
    max_replans: int = 2
    self_consistency_samples: int = 3
    tot_breadth: int = 3
    tot_depth: int = 3
    tot_max_nodes: int = 12
    verification_enabled: bool = True
    #: Wall-clock cap on the engineer agent's plan-act-observe-replan loop
    #: (`aura_core/execution/agentic_loop.py`), independent of any single execution
    #: pass's own `deadline_s` -- this bounds the whole multi-iteration turn.
    engineer_loop_deadline_s: float = 600.0

    @classmethod
    def from_env(cls) -> "ReasoningSettings":
        return cls(
            default_strategy=(_env("REASONING_DEFAULT_STRATEGY", "direct") or "direct").lower(),
            auto_select=_bool("REASONING_AUTO_SELECT", True),
            planning_enabled=_bool("PLANNING_ENABLED", True),
            max_plan_steps=_int("PLANNING_MAX_STEPS", 12, minimum=1, maximum=64),
            max_replans=_int("PLANNING_MAX_REPLANS", 2, minimum=0, maximum=10),
            self_consistency_samples=_int("REASONING_SELF_CONSISTENCY_SAMPLES", 3, minimum=2, maximum=9),
            tot_breadth=_int("REASONING_TOT_BREADTH", 3, minimum=2, maximum=6),
            tot_depth=_int("REASONING_TOT_DEPTH", 3, minimum=1, maximum=6),
            tot_max_nodes=_int("REASONING_TOT_MAX_NODES", 12, minimum=2, maximum=64),
            verification_enabled=_bool("REASONING_VERIFICATION_ENABLED", True),
            engineer_loop_deadline_s=_float(
                "REASONING_ENGINEER_LOOP_DEADLINE_S", 600.0, minimum=10.0, maximum=3600.0
            ),
        )


@dataclass(frozen=True, slots=True)
class ObservabilitySettings:
    log_level: str = "INFO"
    log_format: str = "json"
    metrics_enabled: bool = True
    metrics_path: str = "/metrics"
    tracing_enabled: bool = False
    otlp_endpoint: str = ""
    service_name: str = "aura-core"
    telemetry_persist: bool = True
    trace_content_sampling: float = 0.0  # fraction of turns that persist prompt/output text

    @classmethod
    def from_env(cls) -> "ObservabilitySettings":
        return cls(
            log_level=(_env("LOG_LEVEL", "INFO") or "INFO").upper(),
            log_format=(_env("LOG_FORMAT", "json") or "json").lower(),
            metrics_enabled=_bool("METRICS_ENABLED", True),
            metrics_path=_env("METRICS_PATH", "/metrics") or "/metrics",
            tracing_enabled=_bool("TRACING_ENABLED", False),
            otlp_endpoint=_env("OTLP_ENDPOINT", "") or "",
            service_name=_env("SERVICE_NAME", "aura-core") or "aura-core",
            telemetry_persist=_bool("TELEMETRY_PERSIST", True),
            trace_content_sampling=_float("TRACE_CONTENT_SAMPLING", 0.0, minimum=0.0, maximum=1.0),
        )


@dataclass(frozen=True, slots=True)
class SecuritySettings:
    require_auth: bool = True
    allow_mock_auth: bool = False
    allow_mock_attestation: bool = False
    admin_roles: tuple[str, ...] = ("Admin", "SuperAdmin")
    #: Roles granted the "engineer_agent" capability -- the gate on both the `engineer` persona
    #: (aura_core/agents/registry.py) and every tool in tools/coding.py (sandboxed
    #: filesystem/command/git, GitHub read+write). Empty by default: unlike `admin_roles`, there
    #: is no safe default role here, since this persona can run shell commands and push
    #: commits/PRs (risk_ceiling=SYSTEM) -- granting it is a deliberate per-deployment decision,
    #: not a side effect of upgrading, matching how AURA_SANDBOX_ENABLED/AURA_GITHUB_ENABLED
    #: default off for the same reason.
    engineer_agent_roles: tuple[str, ...] = ()
    rate_limit_enabled: bool = True
    rate_limit_per_user_per_minute: int = 60
    rate_limit_per_ip_per_minute: int = 120
    llm_cost_weight: int = 10
    daily_cost_cap_usd: float = 0.0  # 0 disables
    audit_enabled: bool = True
    trusted_proxy_count: int = 1
    model_allowlist: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> "SecuritySettings":
        return cls(
            require_auth=_bool("REQUIRE_AUTH", True),
            allow_mock_auth=_bool("ALLOW_MOCK_AUTH", False),
            allow_mock_attestation=_bool_aliased("ALLOW_MOCK_ATTESTATION", False),
            admin_roles=_list("ADMIN_ROLES", ("Admin", "SuperAdmin")),
            engineer_agent_roles=_list("ENGINEER_AGENT_ROLES", ()),
            rate_limit_enabled=_bool("RATE_LIMIT_ENABLED", True),
            rate_limit_per_user_per_minute=_int("RATE_LIMIT_USER_PER_MIN", 60, minimum=1),
            rate_limit_per_ip_per_minute=_int("RATE_LIMIT_IP_PER_MIN", 120, minimum=1),
            llm_cost_weight=_int("RATE_LIMIT_LLM_COST", 10, minimum=1),
            daily_cost_cap_usd=_float("DAILY_COST_CAP_USD", 0.0, minimum=0.0),
            audit_enabled=_bool("AUDIT_ENABLED", True),
            trusted_proxy_count=_int("TRUSTED_PROXY_COUNT", 1, minimum=0, maximum=5),
            model_allowlist=_list("MODEL_ALLOWLIST"),
        )


@dataclass(frozen=True, slots=True)
class InfrastructureSettings:
    """Connection strings. Empty means "use the in-memory adapter".

    In-memory adapters are correct for local development and tests, and *refused* in production
    (see :meth:`AuraSettings.validate`) -- an in-memory vector store in production silently
    loses every document on restart.
    """

    database_url: str = ""
    redis_url: str = ""
    qdrant_url: str = ""
    qdrant_api_key_env: str = "AURA_QDRANT_API_KEY"
    vector_backend: str = "memory"  # memory | qdrant
    document_backend: str = "memory"  # memory | postgres
    memory_backend: str = "memory"  # memory | postgres
    cache_backend: str = "memory"  # memory | redis

    @classmethod
    def from_env(cls) -> "InfrastructureSettings":
        return cls(
            database_url=_env("DATABASE_URL", "") or "",
            redis_url=_env("REDIS_URL", "") or "",
            qdrant_url=_env("QDRANT_URL", "") or "",
            qdrant_api_key_env=_env("QDRANT_API_KEY_ENV", "AURA_QDRANT_API_KEY") or "AURA_QDRANT_API_KEY",
            vector_backend=(_env("VECTOR_BACKEND", "memory") or "memory").lower(),
            document_backend=(_env("DOCUMENT_BACKEND", "memory") or "memory").lower(),
            memory_backend=(_env("MEMORY_BACKEND", "memory") or "memory").lower(),
            cache_backend=(_env("CACHE_BACKEND", "memory") or "memory").lower(),
        )

    @property
    def qdrant_api_key(self) -> str:
        return os.environ.get(self.qdrant_api_key_env, "")


@dataclass(frozen=True, slots=True)
class LearningSettings:
    """The nightly refresh and the fine-tune recommendation loop (ADR-008)."""

    refresh_enabled: bool = True
    refresh_cron: str = "0 2 * * *"
    refresh_max_documents: int = 5000
    promote_requires_gates: bool = True
    gate_max_recall_drop: float = 0.02
    gate_max_error_rate: float = 0.05
    gate_max_quarantine_rate: float = 0.10
    gate_max_count_delta: float = 0.40
    index_retention_days: int = 7
    finetune_min_examples: int = 500
    finetune_min_distinct_users: int = 5
    finetune_auto_launch: bool = False  # never true; see validate()
    #: Where digest-addressed ChatML datasets are written (volume in production).
    dataset_dir: str = "/var/lib/aura/datasets"
    #: Registry model_id used for LoRA artefacts (Ollama tag prefix aura-ft:vN).
    finetune_model_id: str = "aura-ft"
    #: Allow LocalLoraTrainer on CPU — still limited to a small-model allowlist.
    finetune_allow_cpu: bool = False
    finetune_cpu_base_allowlist: tuple[str, ...] = (
        "smollm2:135m",
        "qwen2.5:3b-instruct-q4_K_M",
        "qwen2.5:3b",
    )
    #: Soft estimate shown in the recommendation packet (hours on CPU).
    finetune_estimated_cpu_hours: float | None = None
    #: Max substring-coverage drop vs base before canary is blocked.
    finetune_canary_max_coverage_drop: float = 0.05

    @classmethod
    def from_env(cls) -> "LearningSettings":
        cpu_hours_raw = _env("LEARNING_FINETUNE_CPU_HOURS", "")
        cpu_hours = float(cpu_hours_raw) if cpu_hours_raw else None
        return cls(
            refresh_enabled=_bool("LEARNING_REFRESH_ENABLED", True),
            refresh_cron=_env("LEARNING_REFRESH_CRON", "0 2 * * *") or "0 2 * * *",
            refresh_max_documents=_int("LEARNING_REFRESH_MAX_DOCS", 5000, minimum=1),
            promote_requires_gates=_bool("LEARNING_PROMOTE_REQUIRES_GATES", True),
            gate_max_recall_drop=_float("LEARNING_GATE_RECALL_DROP", 0.02, minimum=0.0, maximum=1.0),
            gate_max_error_rate=_float("LEARNING_GATE_ERROR_RATE", 0.05, minimum=0.0, maximum=1.0),
            gate_max_quarantine_rate=_float("LEARNING_GATE_QUARANTINE_RATE", 0.10, minimum=0.0, maximum=1.0),
            gate_max_count_delta=_float("LEARNING_GATE_COUNT_DELTA", 0.40, minimum=0.0, maximum=10.0),
            index_retention_days=_int("LEARNING_INDEX_RETENTION_DAYS", 7, minimum=0),
            finetune_min_examples=_int("LEARNING_FINETUNE_MIN_EXAMPLES", 500, minimum=1),
            finetune_min_distinct_users=_int("LEARNING_FINETUNE_MIN_USERS", 5, minimum=1),
            finetune_auto_launch=_bool("LEARNING_FINETUNE_AUTO_LAUNCH", False),
            dataset_dir=_env("LEARNING_DATASET_DIR", "/var/lib/aura/datasets")
            or "/var/lib/aura/datasets",
            finetune_model_id=_env("LEARNING_FINETUNE_MODEL_ID", "aura-ft") or "aura-ft",
            finetune_allow_cpu=_bool("LEARNING_FINETUNE_ALLOW_CPU", False),
            finetune_cpu_base_allowlist=tuple(
                _list("LEARNING_FINETUNE_CPU_BASES")
                or (
                    "smollm2:135m",
                    "qwen2.5:3b-instruct-q4_K_M",
                    "qwen2.5:3b",
                )
            ),
            finetune_estimated_cpu_hours=cpu_hours,
            finetune_canary_max_coverage_drop=_float(
                "LEARNING_FINETUNE_CANARY_MAX_COVERAGE_DROP", 0.05, minimum=0.0, maximum=1.0
            ),
        )


@dataclass(frozen=True, slots=True)
class McpServerConfig:
    """One configured MCP server. ``transport='stdio'`` launches ``command``/``args`` as a
    subprocess and speaks MCP over its stdin/stdout; ``transport='sse'`` connects to ``url``."""

    name: str
    transport: str = "stdio"
    command: str = ""
    args: tuple[str, ...] = ()
    url: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            raise ConfigurationError("an MCP server entry must declare a name")
        if self.transport not in {"stdio", "sse"}:
            raise ConfigurationError(
                f"MCP server {self.name!r} has unknown transport {self.transport!r}",
                details={"supported": ["stdio", "sse"]},
            )
        if self.transport == "stdio" and not self.command:
            raise ConfigurationError(f"MCP server {self.name!r} (stdio) must declare a command")
        if self.transport == "sse" and not self.url:
            raise ConfigurationError(f"MCP server {self.name!r} (sse) must declare a url")


def _parse_mcp_servers(raw: str) -> tuple[McpServerConfig, ...]:
    if not raw.strip():
        return ()
    try:
        items = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigurationError(
            "AURA_MCP_SERVERS_JSON must be valid JSON", details={"value": raw}
        ) from exc
    if not isinstance(items, list):
        raise ConfigurationError("AURA_MCP_SERVERS_JSON must be a JSON array of server objects")
    servers = []
    for item in items:
        if not isinstance(item, Mapping):
            raise ConfigurationError("each AURA_MCP_SERVERS_JSON entry must be a JSON object")
        servers.append(
            McpServerConfig(
                name=str(item.get("name", "")),
                transport=str(item.get("transport", "stdio")),
                command=str(item.get("command", "")),
                args=tuple(str(a) for a in item.get("args", [])),
                url=str(item.get("url", "")),
            )
        )
    return tuple(servers)


@dataclass(frozen=True, slots=True)
class McpSettings:
    """Model Context Protocol client configuration.

    Every discovered tool is bridged into the tool registry at ``CONFIDENTIAL`` sensitivity,
    ``side_effecting=True`` (so it always requires confirmation) and gated behind the
    ``engineer_agent`` capability, *unless* its fully qualified name (``mcp_{server}_{tool}``)
    appears in ``safe_tools`` -- see ``tools/coding.py::build_mcp_bridge_tools``. An MCP server an
    operator has not reviewed is therefore inert by default, not merely opt-in.
    """

    enabled: bool = False
    servers: tuple[McpServerConfig, ...] = ()
    safe_tools: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> "McpSettings":
        return cls(
            enabled=_bool("MCP_ENABLED", False),
            servers=_parse_mcp_servers(_env("MCP_SERVERS_JSON", "") or ""),
            safe_tools=_list("MCP_SAFE_TOOLS"),
        )


@dataclass(frozen=True, slots=True)
class GitHubSettings:
    """GitHub access for the engineer agent's read/write tools -- two independent paths.

    ``token_env`` (the original path) names the environment variable holding one
    deployment-wide PAT rather than storing the token itself, matching
    :class:`ProviderConfig.api_key_env` -- the secret is a property, never a dataclass field, so
    it cannot be reached by :meth:`AuraSettings.as_dict`'s introspection. Every user of the
    deployment shares this one token.

    ``oauth_*`` (per-user GitHub App OAuth, ``api/v2/github_integration.py``) lets each user
    connect their own GitHub account instead, so tool calls run against exactly the repos *that
    user* granted during installation. ``oauth_client_secret_env`` follows the same
    name-the-env-var-not-the-value pattern as ``token_env``. The two paths are not mutually
    exclusive to configure, but :func:`Container._build_github_client_resolver` prefers
    ``oauth_enabled`` when both are set, since a per-user grant is strictly narrower than a
    shared deployment-wide one.

    ``allowed_repos`` is defence in depth for *either* path: even a repo-scoped credential should
    only ever be used against the repos an operator explicitly named, not merely whatever the
    model's arguments claim.
    """

    enabled: bool = False
    token_env: str = "AURA_GITHUB_TOKEN"
    default_owner: str = ""
    default_repo: str = ""
    allowed_repos: tuple[str, ...] = ()

    oauth_enabled: bool = False
    oauth_client_id: str = ""
    oauth_client_secret_env: str = "AURA_GITHUB_OAUTH_CLIENT_SECRET"
    #: Where GitHub redirects after a user authorizes the app, e.g.
    #: ``https://api.example.com/api/v3/integrations/github/callback``. Must exactly match the
    #: callback URL configured on the GitHub App itself.
    oauth_callback_url: str = ""

    @classmethod
    def from_env(cls) -> "GitHubSettings":
        return cls(
            enabled=_bool("GITHUB_ENABLED", False),
            token_env=_env("GITHUB_TOKEN_ENV", "AURA_GITHUB_TOKEN") or "AURA_GITHUB_TOKEN",
            default_owner=_env("GITHUB_DEFAULT_OWNER", "") or "",
            default_repo=_env("GITHUB_DEFAULT_REPO", "") or "",
            allowed_repos=_list("GITHUB_ALLOWED_REPOS"),
            oauth_enabled=_bool("GITHUB_OAUTH_ENABLED", False),
            oauth_client_id=_env("GITHUB_OAUTH_CLIENT_ID", "") or "",
            oauth_client_secret_env=(
                _env("GITHUB_OAUTH_CLIENT_SECRET_ENV", "AURA_GITHUB_OAUTH_CLIENT_SECRET")
                or "AURA_GITHUB_OAUTH_CLIENT_SECRET"
            ),
            oauth_callback_url=_env("GITHUB_OAUTH_CALLBACK_URL", "") or "",
        )

    @property
    def token(self) -> str:
        return os.environ.get(self.token_env, "")

    @property
    def oauth_client_secret(self) -> str:
        return os.environ.get(self.oauth_client_secret_env, "")


@dataclass(frozen=True, slots=True)
class SandboxSettings:
    """Per-task Docker sandbox for the engineer agent's ``run_command``/``write_file`` tools.

    ``enabled`` alone is not enough to get a runner: ``Container._build_sandbox_runner`` also
    probes that the ``docker`` package is importable and a daemon is actually reachable, the same
    opt-in-setting-plus-runtime-probe pattern as ``_build_ocr_engine``/``_build_stt_engine``.
    """

    enabled: bool = False
    image: str = "python:3.12-slim"
    network: str = "sandbox_egress"
    memory_limit: str = "512m"
    cpu_limit: float = 1.0
    timeout_s: float = 120.0
    allowed_egress_hosts: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> "SandboxSettings":
        return cls(
            enabled=_bool("SANDBOX_ENABLED", False),
            image=_env("SANDBOX_IMAGE", "python:3.12-slim") or "python:3.12-slim",
            network=_env("SANDBOX_NETWORK", "sandbox_egress") or "sandbox_egress",
            memory_limit=_env("SANDBOX_MEMORY_LIMIT", "512m") or "512m",
            cpu_limit=_float("SANDBOX_CPU_LIMIT", 1.0, minimum=0.1, maximum=8.0),
            timeout_s=_float("SANDBOX_TIMEOUT_S", 120.0, minimum=1.0, maximum=1800.0),
            allowed_egress_hosts=_list("SANDBOX_ALLOWED_EGRESS_HOSTS"),
        )


@dataclass(frozen=True, slots=True)
class VoiceSettings:
    """Self-hosted speech recognition and synthesis (Phase F2) -- both engines are only ever
    built when their binary/package is actually present (see
    `Container._build_stt_engine`/`_build_tts_engine`); these flags let an operator opt out of
    an engine that is present, never opt into one that is not.

    TTS rate/gap/pitch apply only to the espeak-ng last-resort fallback (stock is 175 wpm,
    no word gap). Primary speech is Piper: ``en_US-lessac-medium`` / ``ar_JO-kareem-medium``
    with ``piper_length_scale`` (slightly slower than Piper's stock 1.0). Missing a language's
    ONNX falls through to espeak ``en-us`` / ``ar``.

    Phase H live-voice fields gate the WebSocket session. ``live_enabled`` alone is not enough
    -- both STT and TTS engines must also exist or the session factory returns ``None``.
    """

    stt_enabled: bool = True
    stt_model: str = "tiny.en"
    tts_enabled: bool = True
    tts_voice: str = "en-us"
    tts_rate_wpm: int = 142
    tts_word_gap: int = 6
    tts_pitch: int = 45
    tts_amplitude: int = 88
    tts_ar_voice: str = "ar"
    tts_ar_rate_wpm: int = 130
    piper_en_model: str = "/opt/piper/voices/en_US-lessac-medium.onnx"
    piper_ar_model: str = "/opt/piper/voices/ar_JO-kareem-medium.onnx"
    piper_length_scale: float = 1.08
    piper_noise_scale: float = 0.72
    piper_noise_w: float = 0.9
    piper_sentence_silence: float = 0.45
    piper_tashkeel_model: str = "/opt/piper/libtashkeel_model.ort"
    # Phase H live duplex
    live_enabled: bool = False
    live_max_sessions: int = 8
    live_sample_rate: int = 16_000
    live_frame_ms: int = 20
    live_silence_hangover_ms: int = 600
    live_min_speech_ms: int = 240
    live_barge_in_ms: int = 280
    live_max_utterance_ms: int = 15_000
    live_max_output_tokens: int = 400
    live_playback_sample_rate: int = 22_050

    @classmethod
    def from_env(cls) -> "VoiceSettings":
        return cls(
            stt_enabled=_bool("VOICE_STT_ENABLED", True),
            stt_model=_env("VOICE_STT_MODEL", "tiny.en") or "tiny.en",
            tts_enabled=_bool("VOICE_TTS_ENABLED", True),
            tts_voice=_env("VOICE_TTS_VOICE", "en-us") or "en-us",
            tts_rate_wpm=_int("VOICE_TTS_RATE_WPM", 142, minimum=80, maximum=250),
            tts_word_gap=_int("VOICE_TTS_WORD_GAP", 6, minimum=0, maximum=20),
            tts_pitch=_int("VOICE_TTS_PITCH", 45, minimum=0, maximum=99),
            tts_amplitude=_int("VOICE_TTS_AMPLITUDE", 88, minimum=20, maximum=200),
            tts_ar_voice=_env("VOICE_TTS_AR_VOICE", "ar") or "ar",
            tts_ar_rate_wpm=_int("VOICE_TTS_AR_RATE_WPM", 130, minimum=80, maximum=250),
            piper_en_model=_env(
                "VOICE_PIPER_EN_MODEL",
                "/opt/piper/voices/en_US-lessac-medium.onnx",
            )
            or "/opt/piper/voices/en_US-lessac-medium.onnx",
            piper_ar_model=_env(
                "VOICE_PIPER_AR_MODEL",
                "/opt/piper/voices/ar_JO-kareem-medium.onnx",
            )
            or "/opt/piper/voices/ar_JO-kareem-medium.onnx",
            piper_length_scale=_float(
                "VOICE_PIPER_LENGTH_SCALE", 1.08, minimum=0.5, maximum=2.0,
            ),
            # Bounds, not preferences: past roughly 1.0 noise_w slurs and past 0.8 noise_scale
            # artefacts, so an operator tuning by ear cannot reach a voice that is worse than
            # the stock one in a new way.
            piper_noise_scale=_float(
                "VOICE_PIPER_NOISE_SCALE", 0.72, minimum=0.0, maximum=1.0,
            ),
            piper_noise_w=_float("VOICE_PIPER_NOISE_W", 0.9, minimum=0.0, maximum=1.5),
            piper_sentence_silence=_float(
                "VOICE_PIPER_SENTENCE_SILENCE", 0.45, minimum=0.0, maximum=2.0,
            ),
            piper_tashkeel_model=_env(
                "VOICE_PIPER_TASHKEEL_MODEL", "/opt/piper/libtashkeel_model.ort",
            )
            or "/opt/piper/libtashkeel_model.ort",
            live_enabled=_bool("VOICE_LIVE_ENABLED", False),
            live_max_sessions=_int("VOICE_LIVE_MAX_SESSIONS", 8, minimum=1, maximum=64),
            live_sample_rate=_int("VOICE_LIVE_SAMPLE_RATE", 16_000, minimum=8_000, maximum=48_000),
            live_frame_ms=_int("VOICE_LIVE_FRAME_MS", 20, minimum=10, maximum=60),
            live_silence_hangover_ms=_int(
                "VOICE_LIVE_SILENCE_HANGOVER_MS", 600, minimum=100, maximum=3_000,
            ),
            live_min_speech_ms=_int("VOICE_LIVE_MIN_SPEECH_MS", 240, minimum=60, maximum=2_000),
            live_barge_in_ms=_int("VOICE_LIVE_BARGE_IN_MS", 280, minimum=80, maximum=2_000),
            live_max_utterance_ms=_int(
                "VOICE_LIVE_MAX_UTTERANCE_MS", 15_000, minimum=1_000, maximum=60_000,
            ),
            live_max_output_tokens=_int(
                "VOICE_LIVE_MAX_OUTPUT_TOKENS", 400, minimum=64, maximum=2_048,
            ),
            live_playback_sample_rate=_int(
                "VOICE_LIVE_PLAYBACK_SAMPLE_RATE", 22_050, minimum=8_000, maximum=48_000,
            ),
        )


# --------------------------------------------------------------------------------------
# Root
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AuraSettings:
    environment: Environment = Environment.LOCAL
    debug: bool = False
    llm: LLMSettings = field(default_factory=LLMSettings)
    knowledge: KnowledgeSettings = field(default_factory=KnowledgeSettings)
    cag: CagSettings = field(default_factory=CagSettings)
    memory: MemorySettings = field(default_factory=MemorySettings)
    safety: SafetySettings = field(default_factory=SafetySettings)
    tools: ToolSettings = field(default_factory=ToolSettings)
    reasoning: ReasoningSettings = field(default_factory=ReasoningSettings)
    observability: ObservabilitySettings = field(default_factory=ObservabilitySettings)
    security: SecuritySettings = field(default_factory=SecuritySettings)
    infrastructure: InfrastructureSettings = field(default_factory=InfrastructureSettings)
    learning: LearningSettings = field(default_factory=LearningSettings)
    voice: VoiceSettings = field(default_factory=VoiceSettings)
    mcp: McpSettings = field(default_factory=McpSettings)
    github: GitHubSettings = field(default_factory=GitHubSettings)
    sandbox: SandboxSettings = field(default_factory=SandboxSettings)

    @classmethod
    def from_env(cls) -> "AuraSettings":
        raw_env = (_env("ENVIRONMENT", "local") or "local").lower()
        try:
            environment = Environment(raw_env)
        except ValueError as exc:
            raise ConfigurationError(
                "AURA_ENVIRONMENT is not a known environment",
                details={"value": raw_env, "supported": [e.value for e in Environment]},
            ) from exc
        return cls(
            environment=environment,
            # Unprefixed DEBUG_MODE is the legacy v1 flag (config.Settings).
            # Honour it here so production boot fails closed even when only
            # that name is set — AURA_DEBUG alone used to leave the v1 mock
            # user path open.
            debug=_bool("DEBUG", False) or _bool_aliased("DEBUG_MODE", False),
            llm=LLMSettings.from_env(),
            knowledge=KnowledgeSettings.from_env(),
            cag=CagSettings.from_env(),
            memory=MemorySettings.from_env(),
            safety=SafetySettings.from_env(),
            tools=ToolSettings.from_env(),
            reasoning=ReasoningSettings.from_env(),
            observability=ObservabilitySettings.from_env(),
            security=SecuritySettings.from_env(),
            infrastructure=InfrastructureSettings.from_env(),
            learning=LearningSettings.from_env(),
            voice=VoiceSettings.from_env(),
            mcp=McpSettings.from_env(),
            github=GitHubSettings.from_env(),
            sandbox=SandboxSettings.from_env(),
        )

    # -- validation --------------------------------------------------------------------

    def validate(self) -> None:
        """Fail fast on unsafe or incoherent configuration.

        Called once at startup. Every check here corresponds to a real finding in
        ``docs/analysis/06-security-report.md``; a running process should never be in a state
        where one of these is violated.
        """
        problems: list[str] = []

        if self.environment.is_production_like:
            # SEC-05: a single debug flag currently turns every endpoint into an
            # unauthenticated one operating as a fixed mock user.
            if self.debug:
                problems.append("debug must be false in staging/production")
            if not self.security.require_auth:
                problems.append("security.require_auth must be true in staging/production")
            if self.security.allow_mock_auth:
                problems.append("security.allow_mock_auth must be false in staging/production")
            if self.security.allow_mock_attestation:
                problems.append(
                    "security.allow_mock_attestation must be false in staging/production"
                )
            if not self.safety.enabled:
                problems.append("safety.enabled must be true in staging/production")
            if not self.security.rate_limit_enabled:
                problems.append("security.rate_limit_enabled must be true in staging/production")
            if not self.security.audit_enabled:
                problems.append("security.audit_enabled must be true in staging/production")
            # In-memory stores lose all data on restart -- never acceptable as a system of record.
            for label, backend in (
                ("vector_backend", self.infrastructure.vector_backend),
                ("document_backend", self.infrastructure.document_backend),
                ("memory_backend", self.infrastructure.memory_backend),
            ):
                if backend == "memory":
                    problems.append(
                        f"infrastructure.{label} must not be 'memory' in staging/production "
                        "(data would be lost on every restart)"
                    )
            if self.observability.trace_content_sampling > 0.0:
                problems.append(
                    "observability.trace_content_sampling must be 0 in staging/production "
                    "unless explicitly reviewed -- it persists prompt and output text"
                )

        # ADR-008: this must never be enabled. The setting exists so its absence is explicit and
        # testable rather than merely undocumented.
        if self.learning.finetune_auto_launch:
            problems.append(
                "learning.finetune_auto_launch is not permitted: promoting a fine-tune requires "
                "a recorded human approval (see docs/architecture/008-human-in-the-loop-learning.md)"
            )

        enabled = self.llm.enabled_providers
        if not enabled:
            problems.append("at least one LLM provider must be configured and enabled")
        names = [p.name for p in self.llm.providers]
        if len(names) != len(set(names)):
            problems.append("LLM provider names must be unique")
        if self.environment.is_production_like and any(p.kind == "echo" for p in enabled):
            problems.append("the 'echo' test provider must not be enabled in staging/production")

        for provider in enabled:
            # api_key_env stays supported: a self-hosted vLLM or LocalAI instance can require a
            # bearer token, and that token still must not live in settings. What is no longer
            # possible is a provider whose *purpose* is a third-party key.
            if provider.api_key_env and not os.environ.get(provider.api_key_env):
                problems.append(
                    f"provider {provider.name!r} references {provider.api_key_env} which is unset"
                )

        # Note on what is deliberately *not* checked here: there is no "every provider must be
        # local" gate. `is_local` is a routing hint consumed by the local_first policy and by
        # per-request require_local -- it defaults to false, so gating boot on it would reject
        # every correctly configured deployment that had not set an extra flag, and a control that
        # breaks the correct configuration is a control that gets switched off. Self-hosting is
        # enforced where it can be enforced without false positives: the vendor adapters are gone,
        # the SDKs fail the architecture tests, and ProviderConfig refuses a hosted base_url.

        if self.infrastructure.vector_backend == "qdrant" and not self.infrastructure.qdrant_url:
            problems.append("vector_backend='qdrant' requires AURA_QDRANT_URL")
        if self.infrastructure.cache_backend == "redis" and not self.infrastructure.redis_url:
            problems.append("cache_backend='redis' requires AURA_REDIS_URL")
        if (
            self.infrastructure.document_backend == "postgres"
            or self.infrastructure.memory_backend == "postgres"
        ) and not self.infrastructure.database_url:
            problems.append("postgres backends require AURA_DATABASE_URL")

        if self.observability.tracing_enabled and not self.observability.otlp_endpoint:
            problems.append("tracing_enabled requires AURA_OTLP_ENDPOINT")

        if problems:
            raise ConfigurationError(
                f"invalid configuration for environment={self.environment}",
                details={"problems": problems},
            )

    # -- introspection -----------------------------------------------------------------

    def as_dict(self, *, redact: bool = True) -> dict[str, Any]:
        """Serialisable view for the admin config endpoint and startup logs.

        Secrets are never present: ``ProviderConfig`` stores only the *name* of the environment
        variable holding a key, and ``api_key`` is a property that is not a dataclass field, so
        it cannot be reached by this walk even accidentally. With ``redact=True`` (the default),
        URL userinfo is stripped so a DSN password cannot appear in ``/admin/status``.
        Pass ``redact=False`` when a local diff needs the raw URLs.
        """

        def encode(value: Any) -> Any:
            if is_dataclass(value) and not isinstance(value, type):
                return {f.name: encode(getattr(value, f.name)) for f in fields(value)}
            if isinstance(value, Enum):
                return value.value
            if isinstance(value, tuple):
                return [encode(v) for v in value]
            if isinstance(value, Mapping):
                return {k: encode(v) for k, v in value.items()}
            if redact and isinstance(value, str):
                return _redact_url_userinfo(value)
            return value

        payload = encode(self)
        if redact:
            for provider in payload.get("llm", {}).get("providers", []):
                provider["api_key_configured"] = bool(os.environ.get(provider.get("api_key_env", "")))
        return payload


_cached: AuraSettings | None = None


def load_settings(*, refresh: bool = False, validate: bool = True) -> AuraSettings:
    """Load (and cache) settings from the environment.

    Cached because reading ~90 environment variables per request would be wasteful, and because
    configuration must not change under a running process -- a mid-flight settings change is a
    class of bug that is very hard to reason about. Tests pass ``refresh=True``.
    """
    global _cached
    if _cached is None or refresh:
        settings = AuraSettings.from_env()
        if validate:
            settings.validate()
        _cached = settings
    return _cached
