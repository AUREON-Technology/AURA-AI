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

from datetime import timedelta
from typing import Any

from .adapters.memory_stores import (
    InMemoryDocumentStore,
    InMemoryEngineerRunStore,
    InMemoryWorkspaceStore,
    InMemoryGitHubIdentityPort,
    InMemoryGoalsPort,
    InMemoryGraphStore,
    InMemoryInsightSink,
    InMemoryMemoryStore,
    InMemoryPersonalOsStore,
    InMemoryVectorStore,
)
from .adapters.runtime import (
    DurableEventBus,
    InMemoryAuditSink,
    InMemoryEventBus,
    InMemoryEventOutbox,
    InMemoryTelemetrySink,
    SystemClock,
)
from .cag import CagEngine
from .config.settings import AuraSettings, load_settings
from .context.engine import ContextEngine
from .context.tokenizer import get_tokenizer
from .domain.errors import ConfigurationError
from .execution.agentic_loop import AgenticExecutor
from .execution.engine import ExecutionEngine
from .knowledge.chunking import ChunkingConfig
from .knowledge.engine import KnowledgeEngine
from .knowledge.extraction import RuleEntityExtractor
from .knowledge.index_manager import IndexManager, PromotionGate
from .knowledge.reranking import build_reranker
from .kernel.kernel import AIKernel, KernelDependencies
from .llm.cache import InMemoryCache, LLMCache
from .llm.embeddings import HashEmbeddingProvider
from .llm.registry import build_providers
from .llm.router import ProviderRouter
from .agents import build_default_agent_registry
from .memory.engine import MemoryEngine
from .memory.session import EpisodicMemory, build_session_summariser
from .observability.logging import configure_logging, get_logger
from .observability.metrics import get_metrics
from .rag import StrategyFacade, build_strategies
from .observability.tracing import get_tracer
from .planning.engine import PlanningEngine
from .prompts import build_default_registry
from .reasoning.engine import ReasoningEngine
from .safety.pipeline import SafetyPipeline
from .tools.builtin import default_tools
from .tools.policy import ToolPolicy
from .tools.registry import ToolRegistry
from .triggers import DEFAULT_TRIGGER_SPECS, DURABLE_EVENT_TYPES, TriggerContext, TriggerEngine

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
                "mock_attestation": settings.security.allow_mock_attestation,
                "safety_disabled": not settings.safety.enabled,
                "rate_limit_disabled": not settings.security.rate_limit_enabled,
            }
        )

        self.clock = SystemClock()

        # Memoised by _postgres_pool(); declared before anything below asks for it.
        self._pg_pool: Any = None

        # Extra readiness probes contributed by the layer above (see register_readiness_check).
        self._readiness_checks: dict[str, Any] = {}

        # Phase E1/E2: a durable outbox for event types a live handler failure must not lose, and
        # the trigger engine -- the first real subscriber the event bus has ever had (every other
        # engine below only ever publishes to it). Wiring order matters: the trigger engine has to
        # exist before `self.events` does, since the durable bus wraps the same inner bus the
        # trigger engine subscribes to.
        self.event_outbox = self._build_event_outbox()
        self.insight_sink = self._build_insight_sink()
        self.triggers = TriggerEngine(DEFAULT_TRIGGER_SPECS)
        self.trigger_context = TriggerContext(insight_sink=self.insight_sink)
        events_inner = InMemoryEventBus(metrics=self.metrics)
        events_inner.subscribe_all(
            lambda event: self.triggers.handle(event, context=self.trigger_context)
        )
        self.events = DurableEventBus(
            inner=events_inner, outbox=self.event_outbox, durable_event_types=DURABLE_EVENT_TYPES,
        )

        self.cache_backend = self._build_cache()
        self.embedder = self._build_embedder()
        self.vectors = self._build_vector_store()
        self.documents = self._build_document_store()
        self.memory_store = self._build_memory_store()
        self.personal_os_store = self._build_personal_os_store()
        self.engineer_runs = self._build_engineer_run_store()
        self.workspaces = self._build_workspace_store()
        self.goals = self._build_goals_port()
        self.github_identities = self._build_github_identity_port()
        self.graph = self._build_graph_store()
        self.feedback = self._build_feedback_store()
        self.audit = self._build_audit_sink()
        self.telemetry_sink = self._build_telemetry_sink()
        self.sources = self._build_source_registry()

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
        self.agents = build_default_agent_registry()
        self.safety = SafetyPipeline.from_settings(
            settings.safety, metrics=self.metrics, event_bus=self.events, audit=self.audit
        )
        self.context = ContextEngine(
            tokenizer=get_tokenizer(model=self._primary_model()),
            metrics=self.metrics,
            history_turns=settings.memory.prompt_history_messages,
            max_prompt_tokens=settings.llm.max_prompt_tokens,
            knowledge_chunk_max_chars=settings.knowledge.prompt_chunk_max_chars,
            max_knowledge_items=settings.knowledge.retrieval_top_k,
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
            reranker=build_reranker(
                settings.knowledge.rerank_provider,
                model=settings.knowledge.rerank_model,
                metrics=self.metrics,
            ),
            rrf_k=settings.knowledge.rrf_k,
            mmr_lambda=settings.knowledge.mmr_lambda,
            max_document_bytes=settings.knowledge.max_document_bytes,
            metrics=self.metrics,
            event_bus=self.events,
        )
        cag_settings = settings.cag
        self.cag = (
            CagEngine(
                documents=self.documents,
                cache=self.cache_backend,
                llm_cache=self.llm_cache,
                tokenizer=get_tokenizer(model=self._primary_model()),
                enabled=cag_settings.enabled,
                max_tokens=cag_settings.max_tokens,
                global_tokens=cag_settings.global_tokens,
                user_tokens=cag_settings.user_tokens,
                max_global_docs=cag_settings.max_global_docs,
                max_user_docs=cag_settings.max_user_docs,
                metrics=self.metrics,
            )
            if cag_settings.enabled
            else None
        )
        # Phase F1: `OcrEngine` is applied at the extraction step, before `ingest()` -- see
        # `api/v3.py`'s `/knowledge/documents/upload` -- so it lives here as its own attribute
        # rather than as a `KnowledgeEngine` constructor argument.
        self.ocr_engine = self._build_ocr_engine()
        # Phase F2: neither speech port feeds an engine constructor either -- both are reached
        # directly from `api/v3.py`'s `/voice/*` routes.
        self.stt_engine = self._build_stt_engine()
        self.tts_engine = self._build_tts_engine()
        # Phase I: pure text NLP (normalize / shallow intent). Always on; no binary gate.
        from .nlp import NlpEngine

        self.nlp = NlpEngine()
        # Phase H live-voice info is built after `self.kernel` exists (see below).
        self.live_voice = None

        learning = settings.learning
        from .learning.knowledge_refresh import KnowledgeRefreshPipeline
        from .learning.sources import HttpSourceFetcher

        self.source_fetcher = HttpSourceFetcher()
        self.refresh_pipeline = KnowledgeRefreshPipeline(
            registry=self.sources,
            fetcher=self.source_fetcher,
            knowledge=self.knowledge,
            index_manager=self.index_manager,
            metrics=self.metrics,
            event_bus=self.events,
            max_documents=learning.refresh_max_documents,
            promote_requires_gates=learning.promote_requires_gates,
            index_retention_days=learning.index_retention_days,
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

        # The ten retrieval strategies, and a facade presenting the selected one with the
        # KnowledgeEngine.retrieve signature. The facade is what keeps this out of the kernel's hot
        # path: the kernel still calls `.retrieve(query, owner_id=...)` and does not know strategies
        # exist. With the default `simple` the call reaches KnowledgeEngine.retrieve exactly as
        # before, so selecting a strategy is opt-in rather than a change of behaviour on upgrade.
        self.strategies = build_strategies(
            knowledge=self.knowledge,
            router=self.router,
            memory=self.memory,
            graph=self.graph if settings.knowledge.graph_enabled else None,
            metrics=self.metrics,
            max_parallel=settings.knowledge.retrieval_max_parallel,
        )
        selected = settings.knowledge.retrieval_strategy
        if selected not in self.strategies:
            raise ConfigurationError(
                f"unknown retrieval strategy {selected!r}",
                details={"supported": sorted(self.strategies)},
            )
        self.retrieval = StrategyFacade(
            self.strategies[selected],
            max_generations=settings.knowledge.retrieval_max_generations,
        )

        from .adapters.host_agent import HostAgentRegistry

        self.host_agent_registry = HostAgentRegistry()
        self.sandbox_runner = self._build_sandbox_runner()
        # After the runner, never before: the manager needs it, and a None runner is the signal
        # that this deployment has no workspaces at all.
        self.workspace_manager = self._build_workspace_manager()
        self.host_git = self._build_host_git()
        self.github_client = self._build_github_client()
        self._github_clients_by_user: dict[str, Any] = {}
        self.github_client_resolver = self._build_github_client_resolver()
        self.mcp_client = self._build_mcp_client()
        self.tools = self._build_tools()
        self.planner = (
            PlanningEngine(
                router=self.router,
                prompts=self.prompts,
                max_steps=settings.reasoning.max_plan_steps,
                max_replans=settings.reasoning.max_replans,
                enabled=settings.reasoning.planning_enabled,
                metrics=self.metrics,
                # Same deadline as the kernel's own chat completions (kernel.py's
                # _completion_timeout_s reads this same setting) -- left at the
                # CompletionRequest default of 60s, a cold prefill on a CPU-only provider
                # silently downgrades every plan-worthy turn to a tool-less direct answer.
                request_timeout_s=settings.llm.request_timeout_s,
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
        # The engineer agent's bounded plan-execute-observe-replan loop (execution/agentic_loop.py)
        # -- `None` when planning itself is disabled, since there is nothing to loop.
        self.agentic_executor = (
            AgenticExecutor(planner=self.planner, executor=self.executor, metrics=self.metrics)
            if self.planner is not None
            else None
        )

        from .learning.approval import ApprovalService, InMemoryApprovalStore
        from .learning.curation import DatasetCurator
        from .learning.datasets import FileDatasetStore, InMemoryDatasetStore
        from .learning.finetune import (
            FineTuneJobEngine,
            FineTuneService,
            InMemoryJobStore,
            ScriptedTrainer,
        )
        from .mlops.experiments import ExperimentTracker, InMemoryExperimentStore
        from .mlops.model_registry import InMemoryModelStore, ModelRegistry

        use_postgres = settings.infrastructure.document_backend == "postgres"
        if use_postgres:
            from .adapters.postgres_approvals import PostgresApprovalStore
            from .adapters.postgres_experiments import PostgresExperimentStore
            from .adapters.postgres_jobs import PostgresJobStore
            from .adapters.postgres_models import PostgresModelStore

            approval_store = PostgresApprovalStore(
                settings.infrastructure.database_url, pool=self._postgres_pool()
            )
            model_store = PostgresModelStore(
                settings.infrastructure.database_url, pool=self._postgres_pool()
            )
            experiment_store = PostgresExperimentStore(
                settings.infrastructure.database_url, pool=self._postgres_pool()
            )
            job_store = PostgresJobStore(
                settings.infrastructure.database_url, pool=self._postgres_pool()
            )
            self.datasets = FileDatasetStore(settings.learning.dataset_dir)
        else:
            approval_store = InMemoryApprovalStore()
            model_store = InMemoryModelStore()
            experiment_store = InMemoryExperimentStore()
            job_store = InMemoryJobStore()
            self.datasets = InMemoryDatasetStore()

        self.approvals = ApprovalService(
            store=approval_store, audit=self.audit, event_bus=self.events
        )
        self.models = ModelRegistry(
            store=model_store,
            approvals=self.approvals,
            audit=self.audit,
            event_bus=self.events,
            metrics=self.metrics,
        )
        self.experiments = ExperimentTracker(store=experiment_store, metrics=self.metrics)

        base_model = self._primary_model() or ""
        self.finetune = FineTuneService(
            feedback=self.feedback,
            datasets=self.datasets,
            approvals=self.approvals,
            curator=DatasetCurator(
                min_examples=settings.learning.finetune_min_examples,
                min_distinct_users=settings.learning.finetune_min_distinct_users,
            ),
            base_model=base_model,
            estimated_train_hours_cpu=settings.learning.finetune_estimated_cpu_hours,
        )
        trainer = self._build_finetune_trainer()
        self.finetune_jobs = FineTuneJobEngine(
            approvals=self.approvals,
            datasets=self.datasets,
            models=self.models,
            trainer=trainer,
            jobs=job_store,
            experiments=self.experiments,
            model_id=settings.learning.finetune_model_id,
            allow_cpu=settings.learning.finetune_allow_cpu,
        )

        self.kernel = AIKernel(
            KernelDependencies(
                router=self.router,
                prompts=self.prompts,
                context=self.context,
                agents=self.agents,
                safety=self.safety if settings.safety.enabled else None,
                memory=self.memory,
                knowledge=self.knowledge,
                cag=self.cag,
                goals=self.goals,
                tools=self.tools if settings.tools.enabled else None,
                planner=self.planner,
                reasoner=self.reasoner,
                executor=self.executor,
                agentic_executor=self.agentic_executor,
                engineer_runs=self.engineer_runs,
                episodic=self.episodic,
                cache=self.llm_cache,
                metrics=self.metrics,
                event_bus=self.events,
                telemetry_sink=self.telemetry_sink,
                clock=self.clock,
                settings=settings,
                models=self.models,
            )
        )
        # After kernel exists, same reason as live_voice below: `delegate_to_agent`
        # (tools/delegation.py) recurses into `self.kernel.run()`, so it cannot be built inside
        # `_build_tools()` (which runs before the kernel does) -- the resolver defers the lookup
        # of `self.kernel` to call time, once it is guaranteed to exist.
        from .tools.delegation import build_delegation_tools

        self.tools.register_all(
            build_delegation_tools(
                agents=self.agents,
                kernel=lambda: self.kernel,
                max_depth=settings.reasoning.max_delegation_depth,
                deadline_s=settings.reasoning.delegation_deadline_s,
            )
        )

        # Phase H: after kernel exists so the live session can stream through it.
        self.live_voice = self._build_live_voice()

        logger.info(
            "container built",
            extra={
                "environment": str(settings.environment),
                "providers": [p.name for p in self.providers],
                "embedder": self.embedder.model_id,
                "tools": len(self.tools),
                "prompts": len(self.prompts),
                "agents": len(self.agents),
            },
        )

    # -- component builders ------------------------------------------------------------

    def _primary_model(self) -> str | None:
        enabled = self.settings.llm.enabled_providers
        return enabled[0].model if enabled else None

    def _ollama_provider(self) -> Any:
        from .llm.providers.ollama import OllamaProvider

        for provider in self.providers:
            if isinstance(provider, OllamaProvider):
                return provider
        return None

    def _build_finetune_trainer(self) -> Any:
        """Default to ScriptedTrainer in the gateway; LocalLoraTrainer only when opted in.

        Keeping torch out of the gateway image is a Phase A1 invariant. The training
        worker compose profile swaps ``AURA_FINETUNE_TRAINER=local``.
        """
        import os

        kind = (os.environ.get("AURA_FINETUNE_TRAINER") or "scripted").strip().lower()
        if kind == "local":
            from .adapters.local_lora_trainer import LocalLoraTrainer

            return LocalLoraTrainer(
                ollama=self._ollama_provider(),
                allow_cpu=self.settings.learning.finetune_allow_cpu,
                cpu_base_allowlist=self.settings.learning.finetune_cpu_base_allowlist,
            )
        from .learning.finetune import ScriptedTrainer

        return ScriptedTrainer()

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

    def _postgres_pool(self) -> Any:
        """One connection pool shared by every Postgres-backed store.

        Built on first request and memoised. Two stores against the same database with separate
        pools would double the connection count for no benefit -- the prod compose configures
        Postgres for a modest `max_connections`, and each gateway worker holding two pools is how a
        four-worker deployment exhausts it.
        """
        if self._pg_pool is None:
            from .adapters.postgres_store import PostgresPool

            self._pg_pool = PostgresPool(self.settings.infrastructure.database_url)
        return self._pg_pool

    def _build_document_store(self) -> Any:
        backend = self.settings.infrastructure.document_backend
        if backend == "postgres":
            from .adapters.postgres_store import PostgresDocumentStore

            return PostgresDocumentStore(
                self.settings.infrastructure.database_url, pool=self._postgres_pool()
            )
        if backend != "memory":
            raise ConfigurationError(f"unknown document backend {backend!r}")
        return InMemoryDocumentStore()

    def _build_memory_store(self) -> Any:
        backend = self.settings.infrastructure.memory_backend
        if backend == "postgres":
            from .adapters.postgres_store import PostgresMemoryStore

            return PostgresMemoryStore(
                self.settings.infrastructure.database_url, pool=self._postgres_pool()
            )
        if backend != "memory":
            raise ConfigurationError(f"unknown memory backend {backend!r}")
        return InMemoryMemoryStore()

    def _build_personal_os_store(self) -> Any:
        backend = self.settings.infrastructure.memory_backend
        if backend == "postgres":
            from .adapters.postgres_personal_os import PostgresPersonalOsStore

            return PostgresPersonalOsStore(
                self.settings.infrastructure.database_url, pool=self._postgres_pool()
            )
        if backend != "memory":
            raise ConfigurationError(f"unknown memory backend {backend!r}")
        return InMemoryPersonalOsStore()

    def _build_engineer_run_store(self) -> Any:
        backend = self.settings.infrastructure.memory_backend
        if backend == "postgres":
            from .adapters.postgres_engineer_runs import PostgresEngineerRunStore

            return PostgresEngineerRunStore(
                self.settings.infrastructure.database_url, pool=self._postgres_pool()
            )
        if backend != "memory":
            raise ConfigurationError(f"unknown memory backend {backend!r}")
        return InMemoryEngineerRunStore()

    async def _github_token_resolver(self, user_id: Any) -> str | None:
        """The caller's forge token, for host-side git only.

        A separate channel from `github_client_resolver` on purpose: the token is a credential,
        and reaching into a client's private attribute to find it would both couple the tool
        layer to one adapter and silently yield nothing for any other implementation of the port.
        """
        client = await self.github_client_resolver(user_id) if self.github_client_resolver else None
        if client is None:
            return None
        return getattr(client, "token", None) or getattr(client, "_token", None)

    def _build_host_git(self) -> Any:
        """Real `git`, run by *this* process against a workspace directory.

        Host-side so a private clone's token never enters the container: the workspace gets a
        complete checkout -- history, branches, tags -- and no credential in any file a
        model-chosen command could read. `None` without a sandbox runner, since there would be no
        workspace to run it against.

        `AURA_SANDBOX_IN_CONTAINER_GIT` is the opt-out for operators who would rather have git
        inside the container and accept that a command can then read the credential; it is off by
        default and deliberately not the recommended path.
        """
        if (
            getattr(self, "docker_sandbox_runner", None) is None
            or self.settings.sandbox.in_container_git
        ):
            return None
        from .adapters.host_git import HostGitService

        return HostGitService(timeout_s=self.settings.sandbox.timeout_s)

    def _build_workspace_manager(self) -> Any:
        """`None` unless a runner that actually implements the durable half exists.

        Capability-probed rather than merely non-None, because `sandbox_runner` is now always a
        `CompositeSandboxRunner`: it routes a task to the caller's desktop or to the server-side
        sandbox, and can pause or reap neither. Building a manager on it would produce a
        workspace that appears to persist and is never reclaimed -- worse than not offering the
        feature. The Docker runner is the one that can, so the manager is built against it
        directly.
        """
        runner = getattr(self, "docker_sandbox_runner", None)
        if runner is None or not hasattr(runner, "open_workspace"):
            return None
        from .tools.workspaces import WorkspaceManager

        sandbox = self.settings.sandbox
        return WorkspaceManager(
            runner=runner,
            store=self.workspaces,
            host_root=sandbox.host_root,
            memory_limit=sandbox.memory_limit,
            max_workspaces=sandbox.max_workspaces,
            max_running=sandbox.max_running,
            ttl_hours=sandbox.workspace_ttl_hours,
            hot_idle_s=sandbox.hot_idle_s,
            disk_quota_mb=sandbox.workspace_disk_quota_mb,
            min_free_memory_mb=sandbox.min_free_memory_mb,
            clock=self.clock,
            metrics=self.metrics,
        )

    def _build_workspace_store(self) -> Any:
        """Same backend switch as `_build_engineer_run_store`, deliberately: a deployment that
        keeps engineer-run history in Postgres and workspaces in memory would forget every
        workspace on restart while still claiming, in its own history, to have created them."""
        backend = self.settings.infrastructure.memory_backend
        if backend == "postgres":
            from .adapters.postgres_workspaces import PostgresWorkspaceStore

            return PostgresWorkspaceStore(
                self.settings.infrastructure.database_url, pool=self._postgres_pool()
            )
        if backend != "memory":
            raise ConfigurationError(f"unknown memory backend {backend!r}")
        return InMemoryWorkspaceStore()

    def _build_ocr_engine(self) -> Any:
        """`None` unless both the operator opted in (`ocr_enabled`) and the `tesseract` binary is
        actually on PATH -- `ports/documents.py`'s "sensible default is nothing" stays true
        wherever the binary genuinely is not there, e.g. this platform's own CI image."""
        if not self.settings.knowledge.ocr_enabled:
            return None
        from .adapters.tesseract_ocr import TesseractOcrEngine, tesseract_available

        if not tesseract_available():
            return None
        return TesseractOcrEngine(lang=self.settings.knowledge.ocr_lang)

    def _build_stt_engine(self) -> Any:
        """`None` unless both opted in (`voice.stt_enabled`) and the `faster-whisper` package is
        actually importable -- the model itself loads lazily on first real call, so this check
        costs nothing at container-build time."""
        if not self.settings.voice.stt_enabled:
            return None
        from .adapters.whisper_stt import FasterWhisperEngine, faster_whisper_available

        if not faster_whisper_available():
            return None
        return FasterWhisperEngine(model_size=self.settings.voice.stt_model)

    def _build_tts_engine(self) -> Any:
        """`None` unless opted in (`voice.tts_enabled`) and espeak-ng is on PATH.

        Piper is primary for both languages when the binary and that language's ONNX sit
        on disk. espeak-ng is last-resort only (English `en-us`, Arabic `-v ar`).
        """
        if not self.settings.voice.tts_enabled:
            return None
        from .adapters.espeak_tts import EspeakTtsEngine, espeak_available
        from .adapters.language_aware_tts import LanguageAwareTtsEngine
        from .adapters.piper_tts import PiperTtsEngine, piper_available

        if not espeak_available():
            return None
        voice = self.settings.voice
        english = EspeakTtsEngine(
            voice=voice.tts_voice,
            rate_wpm=voice.tts_rate_wpm,
            word_gap=voice.tts_word_gap,
            pitch=voice.tts_pitch,
            amplitude=voice.tts_amplitude,
        )
        arabic_fallback = EspeakTtsEngine(
            voice=voice.tts_ar_voice,
            rate_wpm=voice.tts_ar_rate_wpm,
            word_gap=voice.tts_word_gap,
            pitch=voice.tts_pitch,
            amplitude=voice.tts_amplitude,
        )
        prosody = {
            "length_scale": voice.piper_length_scale,
            "noise_scale": voice.piper_noise_scale,
            "noise_w": voice.piper_noise_w,
            "sentence_silence": voice.piper_sentence_silence,
        }
        piper_en = (
            PiperTtsEngine(model_path=voice.piper_en_model, **prosody)
            if piper_available(voice.piper_en_model)
            else None
        )
        piper_ar = (
            PiperTtsEngine(
                model_path=voice.piper_ar_model,
                tashkeel_model=voice.piper_tashkeel_model,
                **prosody,
            )
            if piper_available(voice.piper_ar_model)
            else None
        )
        return LanguageAwareTtsEngine(
            english=english,
            arabic_fallback=arabic_fallback,
            piper_en=piper_en,
            piper_ar=piper_ar,
            english_voice=voice.tts_voice,
            english_rate_wpm=voice.tts_rate_wpm,
            arabic_voice=voice.tts_ar_voice,
            arabic_rate_wpm=voice.tts_ar_rate_wpm,
        )

    def _build_live_voice(self) -> Any | None:
        """Return a factory callable ``() -> LiveVoiceSession kwargs dict`` or ``None``.

        Live voice is refused unless ``live_enabled`` and both speech engines exist — matching
        the HTTP routes' refusal to silently degrade.
        """
        voice = self.settings.voice
        if not voice.live_enabled:
            return None
        if self.stt_engine is None or self.tts_engine is None:
            return None

        from .voice import LiveVoiceConfig

        cfg = LiveVoiceConfig(
            sample_rate=voice.live_sample_rate,
            frame_ms=voice.live_frame_ms,
            silence_hangover_ms=voice.live_silence_hangover_ms,
            min_speech_ms=voice.live_min_speech_ms,
            barge_in_ms=voice.live_barge_in_ms,
            max_utterance_ms=voice.live_max_utterance_ms,
            max_output_tokens=voice.live_max_output_tokens,
            playback_sample_rate_hint=voice.live_playback_sample_rate,
            english_only_stt=voice.stt_model.endswith(".en"),
        )
        return {
            "enabled": True,
            "config": cfg,
            "stt": self.stt_engine,
            "tts": self.tts_engine,
            "nlp": self.nlp,
            "max_sessions": voice.live_max_sessions,
            "kernel": self.kernel,
            "cache": self.cache_backend,
            "safety_guards_enabled": self.settings.safety.output_guards_enabled,
        }

    def _build_goals_port(self) -> Any:
        """The `GoalsPort` (Phase D2): reads the legacy `goals` table's plaintext columns.

        Gated on `document_backend` like `_build_graph_store` -- both are optional read paths
        against the same Postgres database, not a backend of their own.
        """
        if self.settings.infrastructure.document_backend == "postgres":
            from .adapters.postgres_goals import PostgresGoalsPort

            return PostgresGoalsPort(
                self.settings.infrastructure.database_url, pool=self._postgres_pool()
            )
        return InMemoryGoalsPort()

    def _build_github_identity_port(self) -> Any:
        """The `GitHubIdentityPort`: per-user OAuth-connected GitHub identities, distinct from
        the single admin `AURA_GITHUB_TOKEN` `_build_github_client` wires. Gated on
        `document_backend` like `_build_goals_port` -- both are Postgres-only paths against the
        same database, not a backend of their own."""
        if self.settings.infrastructure.document_backend == "postgres":
            from .adapters.postgres_github_identity import PostgresGitHubIdentityPort

            return PostgresGitHubIdentityPort(
                self.settings.infrastructure.database_url, pool=self._postgres_pool()
            )
        return InMemoryGitHubIdentityPort()

    def _build_event_outbox(self) -> Any:
        """The `EventOutbox` (Phase E1): a new, aura_core-owned table -- gated the same way as
        the audit sink, since both need a real Postgres to survive a restart."""
        if self.settings.infrastructure.document_backend == "postgres":
            from .adapters.postgres_outbox import PostgresEventOutbox

            return PostgresEventOutbox(
                self.settings.infrastructure.database_url, pool=self._postgres_pool()
            )
        return InMemoryEventOutbox()

    def _build_insight_sink(self) -> Any:
        """The `InsightSink` (Phase E2): writes into the legacy `insights` table, the same
        read-a-legacy-table-via-raw-SQL pattern `_build_goals_port` uses to read `goals`."""
        if self.settings.infrastructure.document_backend == "postgres":
            from .adapters.postgres_insights import PostgresInsightSink

            return PostgresInsightSink(
                self.settings.infrastructure.database_url, pool=self._postgres_pool()
            )
        return InMemoryInsightSink()

    def _build_graph_store(self) -> Any:
        if self.settings.infrastructure.document_backend == "postgres":
            from .adapters.postgres_graph import PostgresGraphStore

            return PostgresGraphStore(
                self.settings.infrastructure.database_url, pool=self._postgres_pool()
            )
        return InMemoryGraphStore()

    def _build_feedback_store(self) -> Any:
        infra = self.settings.infrastructure
        if infra.document_backend == "postgres" or infra.memory_backend == "postgres":
            from .adapters.postgres_feedback import PostgresFeedbackStore

            return PostgresFeedbackStore(infra.database_url, pool=self._postgres_pool())
        from .learning.feedback import InMemoryFeedbackStore

        return InMemoryFeedbackStore()

    def _build_audit_sink(self) -> Any:
        if self.settings.infrastructure.document_backend == "postgres":
            from .adapters.postgres_audit import PostgresAuditSink

            return PostgresAuditSink(
                self.settings.infrastructure.database_url,
                pool=self._postgres_pool(),
                clock=self.clock,
            )
        return InMemoryAuditSink(clock=self.clock)

    def _build_telemetry_sink(self) -> Any:
        """Phase G1: `KernelDependencies.telemetry_sink` was wired nowhere before this -- every
        turn built a `TurnTelemetry` record and nothing kept it. Without a real history, "a live
        dashboard fed by real evaluation runs and TurnTelemetry" had nothing to read from."""
        if self.settings.infrastructure.document_backend == "postgres":
            from .adapters.postgres_telemetry import PostgresTelemetrySink

            return PostgresTelemetrySink(
                self.settings.infrastructure.database_url, pool=self._postgres_pool()
            )
        return InMemoryTelemetrySink()

    def _build_source_registry(self) -> Any:
        if self.settings.infrastructure.document_backend == "postgres":
            from .adapters.postgres_sources import PostgresSourceRegistry

            return PostgresSourceRegistry(
                self.settings.infrastructure.database_url, pool=self._postgres_pool()
            )
        from .learning.sources import InMemorySourceRegistry

        return InMemorySourceRegistry()

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
        github_default_repo = ""
        if self.settings.github.default_owner and self.settings.github.default_repo:
            github_default_repo = (
                f"{self.settings.github.default_owner}/{self.settings.github.default_repo}"
            )
        registry.register_all(
            default_tools(
                knowledge_engine=self.knowledge, memory_engine=self.memory,
                personal_os_store=self.personal_os_store,
                sandbox_runner=self.sandbox_runner,
                github_client_resolver=self.github_client_resolver,
                github_default_repo=github_default_repo,
                workspace_manager=self.workspace_manager,
                host_git=self.host_git,
                github_token_resolver=self._github_token_resolver,
            )
        )
        return registry

    def _build_sandbox_runner(self) -> Any:
        """Always a `CompositeSandboxRunner` (`adapters/host_agent.py`) wrapping the desktop
        host-agent bridge plus, optionally, the server-side Docker sandbox as a fallback.

        Unconditional (unlike the old Docker-only version this replaces) because
        `write_file`/`edit_file`/`run_command`/`git_*` register whenever `sandbox_runner is not
        None` (`tools/builtin.py::default_tools`), and a user who has paired their own desktop
        app must get those tools regardless of whether this deployment has opted into
        `sandbox.enabled` at all. `create_task` raises a clear, actionable error at call time if
        neither path is actually usable for the caller -- registered-but-unusable is a better
        failure than invisible.
        """
        from .adapters.host_agent import CompositeSandboxRunner, HostAgentSandboxRunner

        # Kept as its own attribute as well as the composite's fallback: durable workspaces are
        # a server-side feature and need the runner that implements them. The composite routes
        # per-task between a user's desktop and this, and cannot pause or reap anything.
        self.docker_sandbox_runner = self._build_docker_sandbox_runner()
        return CompositeSandboxRunner(
            host_runner=HostAgentSandboxRunner(self.host_agent_registry),
            fallback=self.docker_sandbox_runner,
        )

    def _build_docker_sandbox_runner(self) -> Any:
        """`None` unless both opted in (`sandbox.enabled`) and a Docker daemon is actually
        reachable -- the same opt-in-setting-plus-runtime-probe shape as `_build_ocr_engine`/
        `_build_stt_engine`. This is only the `CompositeSandboxRunner` fallback now; a `None`
        here just means no shared server-side sandbox, not "no engineer-agent tools" -- a
        connected desktop app still works."""
        if not self.settings.sandbox.enabled:
            return None
        from .adapters.docker_sandbox import DockerSandboxRunner, docker_available

        if not docker_available():
            logger.warning(
                "sandbox.enabled is set but no Docker daemon is reachable; "
                "the server-side sandbox fallback will not be available (a connected desktop "
                "app is unaffected)"
            )
            return None
        return DockerSandboxRunner(
            image=self.settings.sandbox.image,
            network=self.settings.sandbox.network,
            memory_limit=self.settings.sandbox.memory_limit,
            cpu_limit=self.settings.sandbox.cpu_limit,
            host_root=self.settings.sandbox.host_root,
            proxy_url=self.settings.sandbox.proxy_url,
        )

    def _build_github_client(self) -> Any:
        """`None` unless opted in (`github.enabled`) and a token is actually configured -- a
        GitHub client with no token would register tools that can only ever fail, which is the
        same "don't advertise a capability with nothing behind it" rule `build_http_tool` follows
        for an empty host allowlist."""
        if not self.settings.github.enabled:
            return None
        if not self.settings.github.token:
            logger.warning(
                "github.enabled is set but %s is unset; GitHub tools will not be registered",
                self.settings.github.token_env,
            )
            return None
        from .adapters.github_client import GitHubClient

        return GitHubClient(
            token=self.settings.github.token, allowed_repos=self.settings.github.allowed_repos
        )

    def _build_github_client_resolver(self) -> Any:
        """Per-user `GitHubClient` factory for the engineer agent's GitHub tools, keyed by
        `ToolContext.user_id` (`tools/coding.py::build_github_tools`).

        `None` unless a GitHub credential of *some* kind is configured -- matches the
        "don't advertise a capability with nothing behind it" rule `_build_github_client`
        already follows. Two paths, in preference order:

        * `github.oauth_enabled`: each call looks up that user's own connected identity
          (`self.github_identities`), refreshing an expired access token first when a refresh
          token was stored, and returns `None` (so the tool call surfaces "not connected") for a
          user who never went through `/api/v3/integrations/github/connect`.
        * otherwise, `github.enabled`: every user shares the one admin-configured `GitHubClient`
          `self.github_client` already is -- the pre-OAuth behaviour, unchanged, wrapped in the
          same resolver shape so `build_github_tools` never has to know which path is active.

        Constructed `GitHubClient`s are cached per user id (invalidated on refresh) rather than
        rebuilt every call, and closed in `shutdown()` alongside `self.github_client`.
        """
        if self.settings.github.oauth_enabled:
            from .adapters.github_client import GitHubClient
            from .adapters.github_oauth import GitHubOAuthError
            from .adapters.github_oauth import refresh_access_token as oauth_refresh
            from .domain.github_identity import GitHubIdentity

            identities = self.github_identities
            allowed_repos = self.settings.github.allowed_repos
            client_id = self.settings.github.oauth_client_id
            client_secret = self.settings.github.oauth_client_secret
            clock = self.clock

            async def resolve(user_id: Any) -> Any:
                identity = await identities.get(user_id)
                if identity is None:
                    return None

                now = clock.now()
                refreshed = False
                if identity.token_expires_at is not None and identity.token_expires_at <= now:
                    if not identity.refresh_token:
                        logger.info("a user's GitHub access token expired with no refresh token")
                        return None
                    try:
                        token = await oauth_refresh(
                            client_id=client_id, client_secret=client_secret,
                            refresh_token=identity.refresh_token,
                        )
                    except GitHubOAuthError as exc:
                        logger.warning("GitHub token refresh failed: %s", exc)
                        return None
                    identity = GitHubIdentity(
                        github_user_id=identity.github_user_id,
                        github_login=identity.github_login,
                        access_token=token.access_token,
                        refresh_token=token.refresh_token or identity.refresh_token,
                        token_expires_at=(
                            now + timedelta(seconds=token.expires_in)
                            if token.expires_in else None
                        ),
                        refresh_token_expires_at=(
                            now + timedelta(seconds=token.refresh_token_expires_in)
                            if token.refresh_token_expires_in
                            else identity.refresh_token_expires_at
                        ),
                        installation_id=identity.installation_id,
                        scopes=token.scope or identity.scopes,
                        connected_at=identity.connected_at,
                        updated_at=now,
                    )
                    await identities.upsert(user_id, identity)
                    refreshed = True

                cached = self._github_clients_by_user.get(user_id.value)
                if cached is not None and not refreshed:
                    return cached
                if cached is not None:
                    await cached.close()
                client = GitHubClient(token=identity.access_token, allowed_repos=allowed_repos)
                self._github_clients_by_user[user_id.value] = client
                return client

            return resolve

        if self.github_client is None:
            return None

        static_client = self.github_client

        async def resolve_static(user_id: Any) -> Any:
            return static_client

        return resolve_static

    def _build_mcp_client(self) -> Any:
        """`None` unless opted in (`mcp.enabled`) and at least one server is configured. Tool
        discovery is async I/O, so bridging happens in `startup()`, not here -- this only builds
        the (unconnected) client each configured server lazily connects through on first use."""
        if not self.settings.mcp.enabled or not self.settings.mcp.servers:
            return None
        from .adapters.mcp_client import McpClient

        return McpClient(servers=self.settings.mcp.servers)

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
        if self.workspace_manager is not None:
            # Durable workspaces outlive this process, so its in-memory view of them starts
            # empty and wrong. Reconciling here -- before the first request -- is what stops a
            # restart from leaking containers nobody can reach or resuming a workspace whose
            # container is gone. It never raises: a daemon that is not answering yet must not
            # keep the gateway from serving traffic that has nothing to do with sandboxes.
            try:
                await self.workspace_manager.reconcile()
            except Exception:
                logger.exception("workspace reconciliation failed; continuing startup")
        if self.mcp_client is not None:
            # Tool discovery is the one place registration happens after `_build_tools()` --
            # `list_tools()` is async I/O (may launch subprocesses / open connections), which
            # `Container.__init__` must never do. `ToolRegistry.register_all` is a plain dict
            # insert, so calling it here, once, before the first request is served, is safe.
            from .tools.coding import build_mcp_bridge_tools

            try:
                mcp_tools = await build_mcp_bridge_tools(
                    self.mcp_client, safe_tools=self.settings.mcp.safe_tools
                )
                self.tools.register_all(mcp_tools)
                logger.info("MCP tools registered", extra={"count": len(mcp_tools)})
            except Exception:
                logger.exception("MCP tool discovery failed; continuing without MCP tools")
        logger.info("container startup complete")

    async def shutdown(self) -> None:
        await self.router.close()
        close = getattr(self.embedder, "close", None)
        if close is not None:
            await close()
        if self.github_client is not None:
            await self.github_client.close()
        for client in self._github_clients_by_user.values():
            await client.close()
        if self.mcp_client is not None:
            await self.mcp_client.close()
        mark_dead = getattr(self.metrics, "mark_process_dead", None)
        if mark_dead is not None:
            mark_dead()
        logger.info("container shutdown complete")

    def postgres_pool(self) -> Any:
        """The shared Postgres pool, for callers above this package.

        Public so the layer above can probe the database without standing up a second pool --
        the connection budget this pool is memoised to protect is a global, not a per-caller one.
        Valid only when a Postgres backend is configured.
        """
        return self._postgres_pool()

    def register_readiness_check(self, name: str, check: Any) -> None:
        """Contribute an extra probe to ``health()``.

        The extension point exists so the layer above can gate readiness on things this package
        must not know about. The concrete case is the Alembic schema (ADR-002): the platform
        tables belong to the legacy tree, `aura_core` has no view of its migrations, and yet a
        gateway serving `/api/v3/sync` against an unmigrated database is not ready.

        ``check`` is an awaitable returning a mapping. A falsey ``healthy`` key makes the whole
        probe unhealthy; anything else in the mapping is passed through for the operator to read.
        """
        self._readiness_checks[name] = check

    async def health(self) -> dict[str, Any]:
        """Dependency health, for the readiness probe."""
        providers = await self.router.health()
        index = await self.index_manager.active_identity()
        healthy = any(p["healthy"] for p in providers.values())

        checks: dict[str, Any] = {}
        for name, check in self._readiness_checks.items():
            try:
                result = dict(await check())
            except Exception as exc:
                # A probe that throws is a defect in the probe, not evidence about the
                # dependency, so it is reported and does not gate. The alternative -- letting a
                # buggy check evict a working gateway from the load balancer -- is the same
                # trade the liveness probe refuses to make.
                logger.exception("readiness check failed", extra={"check": name})
                result = {"healthy": True, "detail": f"check raised {type(exc).__name__}: {exc}"}
            checks[name] = result
            if not result.get("healthy", True):
                healthy = False

        payload = {
            "providers": providers,
            "index": index.physical_name if index else None,
            "tools": len(self.tools),
            "prompts": len(self.prompts),
            "agents": len(self.agents),
            "voice": {
                "tts": getattr(self.tts_engine, "name", None) if self.tts_engine else None,
                "stt": getattr(self.stt_engine, "name", None) if self.stt_engine else None,
                "live": bool(self.live_voice),
            },
            "environment": str(self.settings.environment),
            "healthy": healthy,
        }
        if checks:
            payload["checks"] = checks
        return payload


def build_container(settings: AuraSettings | None = None) -> Container:
    """Build the graph from settings, loading and validating them if not supplied."""
    return Container(settings or load_settings())
