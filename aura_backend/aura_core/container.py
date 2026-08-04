"""Composition root: builds the whole object graph from settings.

The one place in the system where concrete adapters meet ports (ADR-001). Everything above this
file receives its collaborators; nothing above this file constructs one.

Why that matters concretely: the legacy code resolves every dependency by importing a
module-level singleton that opens a network connection at import time (``services/rag.py:287``
and five more, TD-01). Consequences -- the process cannot start without Qdrant reachable, tests
must monkey-patch globals, and two configurations cannot coexist. Here, construction is explicit
and lazy, so a unit test builds a fully-working kernel with in-memory adapters in one call and
production swaps them by configuration.
"""

from __future__ import annotations

from typing import Any

from .adapters.memory_stores import (
    InMemoryDocumentStore,
    InMemoryGraphStore,
    InMemoryMemoryStore,
    InMemoryVectorStore,
)
from .adapters.runtime import InMemoryAuditSink, InMemoryEventBus, SystemClock
from .config.settings import AuraSettings, load_settings
from .context.engine import ContextEngine
from .context.tokenizer import get_tokenizer
from .domain.errors import ConfigurationError
from .execution.engine import ExecutionEngine
from .knowledge.chunking import ChunkingConfig
from .knowledge.engine import KnowledgeEngine
from .knowledge.extraction import RuleEntityExtractor
from .knowledge.index_manager import IndexManager, PromotionGate
from .kernel.kernel import AIKernel, KernelDependencies
from .llm.cache import InMemoryCache, LLMCache
from .llm.embeddings import HashEmbeddingProvider
from .llm.registry import build_providers
from .llm.router import ProviderRouter
from .memory.engine import MemoryEngine
from .memory.session import EpisodicMemory, build_session_summariser
from .observability.logging import configure_logging, get_logger
from .observability.metrics import get_metrics
from .observability.tracing import get_tracer
from .planning.engine import PlanningEngine
from .prompts import build_default_registry
from .reasoning.engine import ReasoningEngine
from .safety.pipeline import SafetyPipeline
from .tools.builtin import default_tools
from .tools.policy import ToolPolicy
from .tools.registry import ToolRegistry

__all__ = ["Container", "build_container"]

logger = get_logger("container")


class Container:
    """Owns the object graph and its lifecycle.

    Components are built eagerly in the constructor (so configuration errors surface at startup,
    not on the first request) but every *connection* is lazy (so building the graph needs no
    reachable infrastructure).
    """

    def __init__(self, settings: AuraSettings) -> None:
        self.settings = settings
        obs = settings.observability

        configure_logging(level=obs.log_level, fmt=obs.log_format, service=obs.service_name)
        self.metrics = get_metrics(enabled=obs.metrics_enabled)
        self.tracer = get_tracer(service_name=obs.service_name, enabled=obs.tracing_enabled)
        if obs.tracing_enabled and obs.otlp_endpoint:
            self.tracer.configure_exporter(obs.otlp_endpoint)

        # Publish which security bypasses are active, so `aura_insecure_mode != 0` is a one-line
        # alert rule. SEC-05 was invisible partly because nothing reported it.
        self.metrics.set_insecure_modes(
            {
                "debug": settings.debug,
                "mock_auth": settings.security.allow_mock_auth,
                "safety_disabled": not settings.safety.enabled,
                "rate_limit_disabled": not settings.security.rate_limit_enabled,
            }
        )

        self.clock = SystemClock()
        self.events = InMemoryEventBus(metrics=self.metrics)
        self.audit = InMemoryAuditSink(clock=self.clock)

        self.cache_backend = self._build_cache()
        self.embedder = self._build_embedder()
        self.vectors = self._build_vector_store()
        self.documents = self._build_document_store()
        self.memory_store = self._build_memory_store()
        self.graph = InMemoryGraphStore()

        self.providers = build_providers(
            settings.llm, model_allowlist=settings.security.model_allowlist
        )
        self.router = ProviderRouter(
            self.providers,
            default_policy=settings.llm.default_policy,
            max_fallbacks=settings.llm.max_fallbacks,
            circuit_failure_threshold=settings.llm.circuit_failure_threshold,
            circuit_open_seconds=settings.llm.circuit_open_seconds,
            clock=self.clock.monotonic,
            metrics=self.metrics,
            event_bus=self.events,
        )

        self.llm_cache = LLMCache(
            self.cache_backend,
            embedder=self.embedder,
            enabled=settings.llm.cache_enabled,
            semantic_enabled=settings.llm.semantic_cache_enabled,
            similarity_threshold=settings.llm.semantic_cache_threshold,
            ttl_s=settings.llm.cache_ttl_s,
            metrics=self.metrics,
        )

        self.prompts = build_default_registry()
        self.safety = SafetyPipeline.from_settings(
            settings.safety, metrics=self.metrics, event_bus=self.events, audit=self.audit
        )
        self.context = ContextEngine(
            tokenizer=get_tokenizer(model=self._primary_model()),
            metrics=self.metrics,
        )

        self.index_manager = IndexManager(
            vectors=self.vectors,
            documents=self.documents,
            embedder=self.embedder,
            alias=settings.knowledge.index_alias,
            schema_version=settings.knowledge.schema_version,
            gate=PromotionGate(
                max_recall_drop=settings.learning.gate_max_recall_drop,
                max_error_rate=settings.learning.gate_max_error_rate,
                max_count_delta=settings.learning.gate_max_count_delta,
            ),
            metrics=self.metrics,
            event_bus=self.events,
            batch_size=settings.llm.embedding_batch_size,
        )

        self.knowledge = KnowledgeEngine(
            documents=self.documents,
            vectors=self.vectors,
            embedder=self.embedder,
            index_manager=self.index_manager,
            safety=self.safety if settings.safety.context_guards_enabled else None,
            graph=self.graph if settings.knowledge.graph_enabled else None,
            extractor=RuleEntityExtractor() if settings.knowledge.graph_enabled else None,
            chunking=ChunkingConfig(
                target_tokens=settings.knowledge.chunk_target_tokens,
                max_tokens=settings.knowledge.chunk_max_tokens,
                overlap_sentences=settings.knowledge.chunk_overlap_sentences,
            ),
            top_k=settings.knowledge.retrieval_top_k,
            candidate_k=settings.knowledge.retrieval_candidate_k,
            score_threshold=settings.knowledge.score_threshold,
            hybrid_enabled=settings.knowledge.hybrid_enabled,
            rerank_enabled=settings.knowledge.rerank_enabled,
            rrf_k=settings.knowledge.rrf_k,
            mmr_lambda=settings.knowledge.mmr_lambda,
            max_document_bytes=settings.knowledge.max_document_bytes,
            metrics=self.metrics,
            event_bus=self.events,
        )

        self.memory = (
            MemoryEngine(
                store=self.memory_store,
                vectors=self.vectors,
                embedder=self.embedder,
                index_alias="aura_memory",
                schema_version=settings.knowledge.schema_version,
                metrics=self.metrics,
                event_bus=self.events,
                max_records_per_owner=settings.memory.max_records_per_user,
                duplicate_threshold=settings.memory.duplicate_threshold,
                clock=self.clock,
            )
            if settings.memory.enabled
            else None
        )

        self.episodic = EpisodicMemory(
            self.cache_backend,
            window=settings.memory.episodic_window,
            summarise_after=settings.memory.summarise_after_turns,
            summariser=build_session_summariser(self.router, self.prompts),
        )

        self.tools = self._build_tools()
        self.planner = (
            PlanningEngine(
                router=self.router,
                prompts=self.prompts,
                max_steps=settings.reasoning.max_plan_steps,
                max_replans=settings.reasoning.max_replans,
                enabled=settings.reasoning.planning_enabled,
                metrics=self.metrics,
            )
            if settings.reasoning.planning_enabled
            else None
        )
        self.reasoner = ReasoningEngine(
            router=self.router,
            prompts=self.prompts,
            auto_select=settings.reasoning.auto_select,
            self_consistency_samples=settings.reasoning.self_consistency_samples,
            tot_breadth=settings.reasoning.tot_breadth,
            tot_depth=settings.reasoning.tot_depth,
            tot_max_nodes=settings.reasoning.tot_max_nodes,
            verification_enabled=settings.reasoning.verification_enabled,
            metrics=self.metrics,
        )
        self.executor = ExecutionEngine(
            tools=self.tools,
            max_parallel=settings.tools.max_parallel,
            metrics=self.metrics,
            event_bus=self.events,
        )

        self.kernel = AIKernel(
            KernelDependencies(
                router=self.router,
                prompts=self.prompts,
                context=self.context,
                safety=self.safety if settings.safety.enabled else None,
                memory=self.memory,
                knowledge=self.knowledge,
                tools=self.tools if settings.tools.enabled else None,
                planner=self.planner,
                reasoner=self.reasoner,
                executor=self.executor,
                episodic=self.episodic,
                cache=self.llm_cache,
                metrics=self.metrics,
                event_bus=self.events,
                clock=self.clock,
                settings=settings,
            )
        )

        logger.info(
            "container built",
            extra={
                "environment": str(settings.environment),
                "providers": [p.name for p in self.providers],
                "embedder": self.embedder.model_id,
                "tools": len(self.tools),
                "prompts": len(self.prompts),
            },
        )

    # -- component builders ------------------------------------------------------------

    def _primary_model(self) -> str | None:
        enabled = self.settings.llm.enabled_providers
        return enabled[0].model if enabled else None

    def _build_cache(self) -> Any:
        backend = self.settings.infrastructure.cache_backend
        if backend == "redis":
            from .adapters.redis_cache import RedisCache

            return RedisCache(self.settings.infrastructure.redis_url, metrics=self.metrics)
        if backend != "memory":
            raise ConfigurationError(
                f"unknown cache backend {backend!r}", details={"supported": ["memory", "redis"]}
            )
        return InMemoryCache(max_entries=4096, default_ttl_s=self.settings.llm.cache_ttl_s)

    def _build_embedder(self) -> Any:
        name = self.settings.llm.embedding_provider
        model = self.settings.llm.embedding_model
        dimension = self.settings.llm.embedding_dimension

        if name == "local-embeddings":
            return HashEmbeddingProvider(dimension=dimension, model_id=model)
        if name == "sentence-transformers":
            from .llm.embeddings import SentenceTransformerProvider

            return SentenceTransformerProvider(
                model=model, dimension=dimension,
                max_batch_size=self.settings.llm.embedding_batch_size,
            )
        # Anything else is treated as the name of a configured provider whose base_url serves an
        # OpenAI-compatible /v1/embeddings endpoint.
        from .llm.embeddings import OpenAICompatibleEmbeddingProvider

        for provider in self.settings.llm.providers:
            if provider.name == name:
                return OpenAICompatibleEmbeddingProvider(
                    name=name, base_url=provider.base_url, model=model,
                    dimension=dimension, api_key=provider.api_key,
                    max_batch_size=self.settings.llm.embedding_batch_size,
                )
        raise ConfigurationError(
            f"embedding provider {name!r} is not configured",
            details={"known": [p.name for p in self.settings.llm.providers]},
        )

    def _build_vector_store(self) -> Any:
        backend = self.settings.infrastructure.vector_backend
        if backend == "qdrant":
            from .adapters.qdrant_store import QdrantVectorStore

            return QdrantVectorStore(
                url=self.settings.infrastructure.qdrant_url,
                api_key=self.settings.infrastructure.qdrant_api_key,
            )
        if backend != "memory":
            raise ConfigurationError(
                f"unknown vector backend {backend!r}", details={"supported": ["memory", "qdrant"]}
            )
        return InMemoryVectorStore()

    def _build_document_store(self) -> Any:
        backend = self.settings.infrastructure.document_backend
        if backend == "postgres":
            from .adapters.postgres_store import PostgresDocumentStore

            return PostgresDocumentStore(self.settings.infrastructure.database_url)
        if backend != "memory":
            raise ConfigurationError(f"unknown document backend {backend!r}")
        return InMemoryDocumentStore()

    def _build_memory_store(self) -> Any:
        backend = self.settings.infrastructure.memory_backend
        if backend == "postgres":
            from .adapters.postgres_store import PostgresMemoryStore

            return PostgresMemoryStore(self.settings.infrastructure.database_url)
        if backend != "memory":
            raise ConfigurationError(f"unknown memory backend {backend!r}")
        return InMemoryMemoryStore()

    def _build_tools(self) -> ToolRegistry:
        registry = ToolRegistry(
            policy=ToolPolicy.from_settings(self.settings.tools),
            allowlist=self.settings.tools.allowlist,
            denylist=self.settings.tools.denylist,
            max_parallel=self.settings.tools.max_parallel,
            default_timeout_s=self.settings.tools.default_timeout_s,
            metrics=self.metrics,
            event_bus=self.events,
            audit=self.audit,
        )
        registry.register_all(
            default_tools(knowledge_engine=self.knowledge, memory_engine=self.memory)
        )
        return registry

    # -- lifecycle ---------------------------------------------------------------------

    async def startup(self) -> None:
        """Prepare runtime state that needs I/O.

        Separate from ``__init__`` because it awaits: the index must exist before the first
        query, and the in-process lexical index is empty after a restart, which would silently
        degrade hybrid retrieval to dense-only.
        """
        await self.index_manager.ensure_ready()
        if self.settings.knowledge.hybrid_enabled:
            await self.knowledge.rebuild_lexical_index()
        logger.info("container startup complete")

    async def shutdown(self) -> None:
        await self.router.close()
        close = getattr(self.embedder, "close", None)
        if close is not None:
            await close()
        logger.info("container shutdown complete")

    async def health(self) -> dict[str, Any]:
        """Dependency health, for the readiness probe."""
        providers = await self.router.health()
        index = await self.index_manager.active_identity()
        return {
            "providers": providers,
            "index": index.physical_name if index else None,
            "tools": len(self.tools),
            "prompts": len(self.prompts),
            "environment": str(self.settings.environment),
            "healthy": any(p["healthy"] for p in providers.values()),
        }


def build_container(settings: AuraSettings | None = None) -> Container:
    """Build the graph from settings, loading and validating them if not supplied."""
    return Container(settings or load_settings())
