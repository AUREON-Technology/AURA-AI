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

import asyncio
from datetime import timedelta
from pathlib import Path
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
from .config.settings import (
    vector_payload_content_enabled,
    DATA_ENCRYPTION_KEYS_ENV,
    AuraSettings,
    data_encryption_keys,
    load_settings,
)
from .context.engine import ContextEngine
from .context.tokenizer import get_tokenizer, tokenizer_for
from .domain.errors import ConfigurationError
from .domain.identifiers import SessionId
from .execution.agentic_loop import AgenticExecutor
from .execution.critic import Critic, LLMCritic
from .execution.recovery import RecoveryPolicy
from .execution.stuck import StuckConfig
from .execution.confirmations import PendingConfirmationStore
from .execution.engine import ExecutionEngine
from .history import RunSearchIndex, UsageLedger
from .knowledge.chunking import ChunkingConfig
from .knowledge.engine import KnowledgeEngine
from .knowledge.extraction import RuleEntityExtractor
from .knowledge.index_manager import IndexManager, PromotionGate
from .knowledge.refresh_gate import RefreshContentGate
from .knowledge.reranking import build_reranker
from .kernel.kernel import AIKernel, KernelDependencies
from .kernel.turn_scope import current_turn_lookups
from .llm.cache import InMemoryCache, LLMCache, semantic_tier_useful
from .llm.embeddings import HashEmbeddingProvider
from .llm.quota import QuotaTracker
from .llm.registry import (
    admission_limits_from_settings,
    build_providers,
    quota_limits_from_settings,
)
from .llm.router import ProviderRouter
from .agents import build_default_agent_registry
from .memory.engine import MemoryEngine
from .memory.checkpoint import build_session_checkpointer
from .memory.session import EpisodicMemory, build_session_summariser
from .observability.logging import configure_logging, get_logger
from .observability.metrics import get_metrics
from .rag import StrategyFacade, build_strategies
from .tasks import AgentTaskQueue, AgentTaskStore, KernelTaskRunner
from .observability.tracing import Tracer, install_tracer
from .planning.engine import PlanningEngine
from .ports.runtime import Closeable
from .prompts import PIN_SYNC_INTERVAL_S, build_default_registry
from .reasoning.engine import ReasoningEngine
from .safety.pipeline import SafetyPipeline
from .security.owner_ref import owner_ref_maker
from .tools.builtin import default_tools
from .tools.policy import ToolPolicy
from .tools.registry import ToolRegistry
from .triggers import DEFAULT_TRIGGER_SPECS, DURABLE_EVENT_TYPES, TriggerContext, TriggerEngine

__all__ = ["Container", "build_container", "build_field_cipher", "SHUTDOWN_CLOSE_TIMEOUT_S"]

logger = get_logger("container")

#: Ceiling on any one close in ``Container.shutdown()``. The closes run concurrently, so this is
#: also the bound on the whole shutdown, and it has to end inside the margin F-25 left between
#: uvicorn's drain and the orchestrator's SIGKILL (``LIFESPAN_MARGIN_S`` in
#: tests/unit/test_deploy_robustness.py, 15 s). A close that takes longer is abandoned and
#: logged; the process is exiting either way, and the OS reclaims the socket.
SHUTDOWN_CLOSE_TIMEOUT_S = 5.0
#: How long ``Container.shutdown()`` lets streamed turns finish what they do after ``done``
#: (session append, telemetry; KAN-110) before closing the connections that work needs. Plus
#: the close ceiling above, still inside the 15 s margin.
POST_TURN_DRAIN_S = 5.0


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
        # Installed, not fetched: get_tracer() keeps whichever tracer was created first, so a
        # module that touched it before this point would leave tracing off whatever the setting.
        self.tracer = Tracer(service_name=obs.service_name, enabled=obs.tracing_enabled)
        if obs.tracing_enabled and obs.otlp_endpoint:
            if not self.tracer.configure_exporter(obs.otlp_endpoint):
                logger.warning("tracing is enabled but the OTLP exporter could not be configured")
        install_tracer(self.tracer)

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
        # Memoised by _field_cipher(); only built when a Postgres store that encrypts asks.
        self._cipher: Any = None
        # Set by shutdown(), cleared by startup(): a second shutdown is a no-op.
        self._shut_down = False

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
        # ADR-032: per-user data keys, then the agent's personal-OS store on the synced tables
        # (encrypted with those keys). The old server-only table stays reachable for the data
        # move script and for erasure until it is dropped.
        self.user_data_keys = self._build_user_data_keys()
        self.legacy_personal_os_store = self._build_legacy_personal_os_store()
        self.personal_os_store = self._build_personal_os_store()
        self.engineer_runs = self._build_engineer_run_store()
        # Module readiness D-03 (WP1): who agreed to the Engineer sending code to Anthropic.
        self.engineer_consent = self._build_engineer_consent_store()
        # GAP-024: the same store, under the name of the port it now fully implements
        # (`AgentRunStore` extends `EngineerRunStore`) -- one adapter, one pool, both ledgers.
        self.agent_runs = self.engineer_runs
        self.workspaces = self._build_workspace_store()
        self.goals = self._build_goals_port()
        self.github_identities = self._build_github_identity_port()
        self.graph = self._build_graph_store()
        self.feedback = self._build_feedback_store()
        self.audit = self._build_audit_sink()
        self.telemetry_sink = self._build_telemetry_sink()
        # Keyed one-way owner reference for per-turn telemetry (usage reads, erasure). The key
        # derives from the JWT secret read at call time; none means turns stay unowned.
        self.owner_ref = owner_ref_maker(lambda: self.settings.infrastructure.host_agent_relay_secret)
        self.sources = self._build_source_registry()
        self.idempotency = self._build_idempotency_store()
        self.service_keys = self._build_service_keys()
        self.tenancy = self._build_tenancy()
        self.plans = self._build_plans()
        self.tenancy.seat_limit = self.plans.seat_limit
        self.tenancy.plan_of = self.plans.plan_id
        self.sharing = self._build_sharing()
        self.email, self.notifier = self._build_email()
        self.tenancy.notifier = self.notifier
        self.billing = self._build_billing()
        # F41: an owner transfer forgets the old owner's saved card before ownership moves.
        self.tenancy.owner_changing = self.billing.forget_card
        self.store_billing = self._build_store_billing()

        self.providers = build_providers(
            settings.llm, model_allowlist=settings.security.model_allowlist
        )
        self.router = ProviderRouter(
            self.providers,
            default_policy=settings.llm.default_policy,
            default_timeout_s=settings.llm.request_timeout_s,
            stream_max_s=settings.llm.stream_max_s,
            max_fallbacks=settings.llm.max_fallbacks,
            circuit_failure_threshold=settings.llm.circuit_failure_threshold,
            circuit_open_seconds=settings.llm.circuit_open_seconds,
            clock=self.clock.monotonic,
            metrics=self.metrics,
            event_bus=self.events,
            # Admission control (P2.4): per-capacity-group caps/queues from provider config.
            group_limits=admission_limits_from_settings(settings.llm),
            user_max_concurrent=settings.llm.user_max_concurrent_generations,
            # Quotas (ADR-030 Phase 2): declared per-provider budgets and 429 cooldowns. Daily
            # counters persist through the shared cache so a restart does not forget them.
            quota=QuotaTracker(
                quota_limits_from_settings(settings.llm),
                wall=lambda: self.clock.now().timestamp(),
                default_cooldown_s=settings.llm.rate_limit_cooldown_s,
                store=self.cache_backend,
                metrics=self.metrics,
            ),
            # ADR-030 Phase 4: every generation a third-party provider serves is audited.
            audit=self.audit,
        )

        self.llm_cache = LLMCache(
            self.cache_backend,
            embedder=self.embedder,
            enabled=settings.llm.cache_enabled,
            # PP-08: off when the embedder is not semantic (the hash embedder), where it can never
            # hit but costs an embedding and a cache read on every single-turn miss.
            semantic_enabled=(
                settings.llm.semantic_cache_enabled and semantic_tier_useful(self.embedder)
            ),
            similarity_threshold=settings.llm.semantic_cache_threshold,
            ttl_s=settings.llm.cache_ttl_s,
            metrics=self.metrics,
        )

        # INT-004: pins live in a store every gateway worker reads, not in this worker's memory.
        self.advisory_store = self._build_advisory_store()
        self.push_notifier = self._build_push_notifier()
        self.prompt_pins = self._build_prompt_pins()
        self.prompts = build_default_registry(pins=self.prompt_pins)
        self._prompt_pin_sync_task: asyncio.Task[None] | None = None
        self.agents = build_default_agent_registry(
            engineer_token_budget=settings.reasoning.engineer_token_budget
        )
        if settings.adaptation.enabled:
            # Track AP7: the same registry interface, over a snapshot a blueprint deploy can swap
            # atomically. Wrapped here, before the kernel and the delegation tools capture it.
            from .adaptation.runtime import ActiveBlueprint

            self.agents = ActiveBlueprint(self.agents)
        self.blueprint_store = None
        self.blueprint_deployment = None
        self.project_knowledge = None
        self._blueprint_sync_task = None
        self._stt_warm_task = None

        self.safety = SafetyPipeline.from_settings(
            settings.safety, metrics=self.metrics, event_bus=self.events, audit=self.audit
        )
        self.context = ContextEngine(
            tokenizer=self._primary_tokenizer(),
            metrics=self.metrics,
            history_turns=settings.memory.prompt_history_messages,
            max_prompt_tokens=settings.llm.max_prompt_tokens,
            knowledge_chunk_max_chars=settings.knowledge.prompt_chunk_max_chars,
            max_knowledge_items=settings.knowledge.retrieval_top_k,
        )

        # Audit FSA-018: readable content in vector payloads only when the vector store is on-host.
        self.vector_payload_content = vector_payload_content_enabled(settings.knowledge, settings.infrastructure)
        self.index_manager = IndexManager(
            vectors=self.vectors,
            documents=self.documents,
            embedder=self.embedder,
            alias=settings.knowledge.index_alias,
            schema_version=settings.knowledge.schema_version,
            payload_content=self.vector_payload_content,
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
            extractor=self._build_entity_extractor() if settings.knowledge.graph_enabled else None,
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
                revision=settings.knowledge.rerank_model_revision,
            ),
            rrf_k=settings.knowledge.rrf_k,
            mmr_lambda=settings.knowledge.mmr_lambda,
            max_document_bytes=settings.knowledge.max_document_bytes,
            metrics=self.metrics,
            event_bus=self.events,
        )
        # F10: deleting a team erases its ``workspace:<id>`` corpus before the row goes.
        self.tenancy.erase_knowledge = self.knowledge.erase_owner
        cag_settings = settings.cag
        self.cag = (
            CagEngine(
                documents=self.documents,
                cache=self.cache_backend,
                llm_cache=self.llm_cache,
                tokenizer=self._primary_tokenizer(),
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
        # The developer tools in `tools/open/` read PDFs through one shared reader; handing it the same
        # engine means a scanned page is recognised there too instead of being reported as unread.
        # `None` (OCR disabled or tesseract missing) leaves them on the "name the unread pages" path.
        from .tools.open._input import configure_ocr_engine

        configure_ocr_engine(self.ocr_engine)
        # Phase F2: neither speech port feeds an engine constructor either -- both are reached
        # directly from `api/v3.py`'s `/voice/*` routes.
        self.stt_engine = self._build_stt_engine()
        self.tts_engine = self._build_tts_engine()
        # ADR-031: image understanding. The normalizer is what `/chat` runs every attached image
        # through; the engine is what the kernel asks to describe them. Both `None` with vision
        # off, so a text-only deployment builds nothing new.
        self.image_normalizer = self._build_image_normalizer()
        self.vision = self._build_vision_engine()
        # Phase I: pure text NLP (normalize / shallow intent). Always on; no binary gate.
        from .nlp import NlpEngine

        self.nlp = NlpEngine()
        # Phase H live-voice info is built after `self.kernel` exists (see below).
        self.live_voice = None
        self.live_vad_name: str | None = None

        learning = settings.learning
        from .learning.knowledge_refresh import KnowledgeRefreshPipeline
        from .domain.knowledge import SourceType
        from .learning.sources import CompositeFetcher, HttpSourceFetcher

        # One fetcher per source type (AP10). The web types go over the HTTPS policy; the default
        # keeps any other type a source was registered with fetched exactly as before. Track AP
        # adds the `connector` type below, once its connector registry exists.
        http_fetcher = HttpSourceFetcher()
        self.source_fetcher = CompositeFetcher(
            {SourceType.WEB: http_fetcher, SourceType.RSS: http_fetcher, SourceType.API: http_fetcher},
            default=http_fetcher,
        )
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
            content_gate=RefreshContentGate(
                max_quarantine_rate=learning.gate_max_quarantine_rate,
                max_shrink=learning.gate_max_shrink,
            ),
            allowed_licenses=settings.knowledge.allowed_licenses,
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
                payload_content=self.vector_payload_content,
            )
            if settings.memory.enabled
            else None
        )

        self.episodic = EpisodicMemory(
            self.cache_backend,
            window=settings.memory.episodic_window,
            summarise_after=settings.memory.summarise_after_turns,
            summariser=build_session_summariser(self.router, self.prompts),
            checkpointer=build_session_checkpointer(self.router),
            # Off (0) unless AURA_MEMORY_AUTO_CHECKPOINT_EVERY_TURNS: each one is a model call.
            auto_checkpoint_every=settings.memory.auto_checkpoint_every_turns,
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

        self.host_agent_registry = HostAgentRegistry(relay=self._build_host_agent_relay())
        self.sandbox_runner = self._build_sandbox_runner()
        # After the runner, never before: the manager needs it, and a None runner is the signal
        # that this deployment has no workspaces at all.
        self.workspace_manager = self._build_workspace_manager()
        self.host_git = self._build_host_git()
        self.github_client = self._build_github_client()
        self._github_clients_by_user: dict[str, Any] = {}
        self.github_client_resolver = self._build_github_client_resolver()
        self.mcp_client = self._build_mcp_client()
        # Filled by startup() once MCP tools are discovered; empty when MCP is off.
        self.mcp_tool_collisions: tuple[dict[str, str], ...] = ()
        # ADR-038: None unless AURA_ALLOW_EXTRACTION_INFERENCE is on. Before the tools, which
        # register ``extract_structured`` only when there is an extractor to call.
        self.structured_extractor = self._build_structured_extractor()
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
        # ADR-039: failure-kind-aware retries, only when the operator turns them on; the engine
        # otherwise keeps its original "retry any failed read-only step" rule.
        self.executor = ExecutionEngine(
            tools=self.tools,
            max_parallel=settings.tools.max_parallel,
            metrics=self.metrics,
            event_bus=self.events,
            recovery=(
                RecoveryPolicy(backoff_base_s=settings.reasoning.loop_recovery_backoff_s)
                if settings.reasoning.loop_recovery_policy_enabled else None
            ),
        )
        # The engineer agent's bounded plan-execute-observe-replan loop (execution/agentic_loop.py)
        # -- `None` when planning itself is disabled, since there is nothing to loop.
        reasoning = settings.reasoning
        self.agentic_executor = (
            AgenticExecutor(
                planner=self.planner, executor=self.executor, metrics=self.metrics,
                clock=self.clock, runs=self.agent_runs,
                # ADR-039. Each piece is settings-driven; the loop only applies them to a
                # persona that replans (today: engineer), so single-pass personas are untouched.
                stuck=StuckConfig(
                    enabled=reasoning.loop_stuck_detection_enabled,
                    identical_calls=reasoning.loop_stuck_identical_calls,
                    same_error=reasoning.loop_stuck_same_error,
                    no_progress=reasoning.loop_stuck_no_progress,
                ),
                recovery=(
                    RecoveryPolicy(backoff_base_s=reasoning.loop_recovery_backoff_s)
                    if reasoning.loop_recovery_policy_enabled else None
                ),
                critic=Critic(
                    enabled=reasoning.critic_enabled,
                    llm=(
                        LLMCritic(router=self.router, timeout_s=min(30.0, settings.llm.request_timeout_s))
                        if reasoning.critic_enabled and reasoning.critic_llm_enabled else None
                    ),
                ),
                test_signals=reasoning.loop_test_signals_enabled,
                pressure_threshold=reasoning.context_pressure_threshold,
            )
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

        # The console's Super-Admin control center (ADR-037). Always built: its routes are
        # SuperAdmin-only, and a platform is reachable only after its token is entered.
        self._build_control(use_postgres=use_postgres)

        # Track AP (ADR-018/021). None unless AURA_ADAPTATION_ENABLED; the admin routes answer 503.
        self.connectors = self._build_adaptation(use_postgres=use_postgres)
        self.discovery = None
        self.twins = None
        self.capability_planner = None
        self.blueprint_compiler = None
        if self.connectors is not None:
            from .adaptation.discovery import DiscoveryService

            self.discovery = DiscoveryService(
                registry=self.connectors, clock=self.clock, audit=self.audit, metrics=self.metrics
            )
        self.twins = self._build_twins(use_postgres=use_postgres)
        if self.connectors is not None:
            from .knowledge.documents import extract_document
            from .learning.sources import ConnectorSourceFetcher

            # Nightly refresh through an active connector: documents only, project-owned, untrusted.
            self.source_fetcher.register(SourceType.CONNECTOR, ConnectorSourceFetcher(
                open_connector=self.connectors.open_active, extract=extract_document,
            ))
        self.capability_planner = None
        self.blueprint_compiler = None
        if self.twins is not None:
            from .adaptation.planner import CapabilityRegistry, PlanningService

            self.capability_planner = PlanningService(
                registry=CapabilityRegistry.from_directory(), twins=self.twins, metrics=self.metrics
            )
            self.blueprint_compiler = self._build_compiler(use_postgres=use_postgres)
            self.blueprint_deployment = self._build_deployment()

        # ADR-040 (row 60): lesson extraction after Engineer turns. None unless
        # AURA_AUTO_LESSONS_ENABLED (and memory on): the kernel then does nothing extra.
        self.lesson_sink = self._build_lesson_sink()

        from .tools.autonomy import AutonomyStore

        # ADR-041: the user's review profile, in the cache port (see tools/autonomy.py for why).
        self.autonomy = AutonomyStore(self.cache_backend, default=settings.tools.autonomy_default)
        #: ``async (uid: str) -> list[dict]`` revoking the paired desktop devices ``uid`` owns,
        #: attached by ``aura_service`` because the device registry lives outside aura_core
        #: (ADR-002). ``None`` where it is not attached; ``stop-all`` then reports it unavailable.
        self.device_revoker = None
        # ADR-043: per-turn usage and the opt-in run-goal preview, both in the cache port (Redis
        # in production) -- no table. The kernel writes them; the usage and run-search routes read.
        usage = settings.usage
        self.usage_ledger = UsageLedger(
            self.cache_backend, enabled=usage.ledger_enabled, ttl_days=usage.ledger_ttl_days,
            max_per_user=usage.ledger_max_per_user, clock=self.clock,
        )
        self.run_index = RunSearchIndex(
            self.cache_backend, self.agent_runs, store_preview=usage.runs_store_preview,
            preview_ttl_s=usage.runs_preview_ttl_s, scan=usage.runs_search_scan,
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
                push_notifier=self.push_notifier,
                engineer_runs=self.engineer_runs,
                engineer_consent=self.engineer_consent,
                episodic=self.episodic,
                cache=self.llm_cache,
                session_marks=self.cache_backend,
                metrics=self.metrics,
                event_bus=self.events,
                telemetry_sink=self.telemetry_sink,
                owner_ref=self.owner_ref,
                clock=self.clock,
                settings=settings,
                models=self.models,
                host_agent_registry=self.host_agent_registry,
                sandbox_runner=self.sandbox_runner,
                # F-04: pending tool confirmations live in the shared cache (Redis in
                # production), so any replica can resume the exact turn the user approved.
                tool_confirmations=PendingConfirmationStore(self.cache_backend),
                vision=self.vision,
                lesson_sink=self.lesson_sink,
                autonomy=self.autonomy,
                usage_ledger=self.usage_ledger,
                run_index=self.run_index,
                audit=self.audit,
            )
        )
        # ADR-059: with a cache every worker shares (Redis), stop / pause / redirect reach a turn another
        # worker runs. An in-memory cache is one process's, so it is left unwired and the routes say so.
        if getattr(self.cache_backend, "shared_across_processes", False):
            self.kernel.active_turns.shared = self.cache_backend
        # ADR-043: user agent tasks. The queue runs each task through `self.kernel` -- the same
        # path as /chat -- so it can only exist once the kernel does. `None` is the kill switch
        # (AURA_AGENT_TASKS_ENABLED=false): the routes then answer 503.
        self.agent_task_store = AgentTaskStore(
            self.cache_backend, record_ttl_s=settings.agent_tasks.record_ttl_s,
            idempotency_ttl_s=settings.agent_tasks.idempotency_ttl_s,
        )
        self.task_queue = (
            AgentTaskQueue(
                store=self.agent_task_store, runner=KernelTaskRunner(self.kernel),
                settings=settings.agent_tasks, clock=self.clock,
            )
            if settings.agent_tasks.enabled else None
        )

        # After kernel exists, same reason as live_voice below: `delegate_to_agent`
        # (tools/delegation.py) recurses into `self.kernel.run()`, so it cannot be built inside
        # `_build_tools()` (which runs before the kernel does) -- the resolver defers the lookup
        # of `self.kernel` to call time, once it is guaranteed to exist.
        from .agents.handoff import HandoffStore
        from .agents.tasks import AgentTaskManager
        from .tools.delegation import build_delegation_tools

        # ADR-040, both off by default. Status and results of a background task, and handoff
        # records, live in the shared cache (Redis in production); the running task itself is an
        # asyncio task in this process (agents/tasks.py).
        reasoning = settings.reasoning
        self.agent_tasks = (
            AgentTaskManager(
                self.cache_backend,
                per_user=reasoning.agent_tasks_per_user,
                global_cap=reasoning.agent_tasks_global,
                ttl_s=reasoning.agent_task_ttl_s,
                deadline_s=reasoning.agent_task_deadline_s,
            )
            if reasoning.background_delegation_enabled else None
        )
        self.handoffs = (
            HandoffStore(self.cache_backend) if reasoning.structured_handoff_enabled else None
        )

        async def _parent_checkpoint(owner: Any, session_id: str) -> Any:
            return await self.episodic.fresh_checkpoint(SessionId(session_id), owner)

        self.tools.register_all(
            build_delegation_tools(
                agents=self.agents,
                kernel=lambda: self.kernel,
                max_depth=settings.reasoning.max_delegation_depth,
                deadline_s=settings.reasoning.delegation_deadline_s,
                tasks=self.agent_tasks,
                handoffs=self.handoffs,
                checkpoint_provider=_parent_checkpoint,
                structured_handoff=reasoning.structured_handoff_enabled,
            )
        )

        # ADR-046: `research_topic` composes other tools (search_knowledge, web_search, fetch_url) through
        # this same registry, as the caller, so it is registered once the registry is complete.
        if settings.open_tools.analysis_enabled:
            from .tools.research import build_research_tools

            self.tools.register_all(build_research_tools(lambda: self.tools))

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

    def _primary_tokenizer(self) -> Any:
        """The primary provider's tokenizer (KAN-103/104): the context engine budgets with the
        same counter the router and the quota use for that provider, so all three agree."""
        primary = self._primary_model()
        for provider in getattr(self, "providers", ()):
            caps = provider.capabilities
            if primary is not None and caps.model_id == primary:
                return tokenizer_for(caps.tokenizer, model_id=caps.model_id)
        return get_tokenizer(model=primary)

    def _ollama_provider(self) -> Any:
        from .llm.providers.ollama import OllamaProvider

        for provider in self.providers:
            if isinstance(provider, OllamaProvider):
                return provider
        return None

    def _build_finetune_trainer(self) -> Any:
        """Pick the Loop B trainer from ``AURA_FINETUNE_TRAINER``.

        ``scripted`` (default, and the only value the gateway services use): trains nothing and
        says so — every job ends ``NOT_TRAINED`` with no model version registered.
        ``local``: ``LocalLoraTrainer``, real PEFT LoRA on a transformers base model. It needs
        ``requirements-train.txt`` (peft), which only the ``worker-training`` image installs;
        anywhere else its jobs fail loudly with a ConfigurationError naming that file.

        Anything else is a configuration error rather than a silent fall back to ``scripted``:
        a typo here would otherwise read as "training is configured" while nothing trains.
        """
        import os

        kind = (os.environ.get("AURA_FINETUNE_TRAINER") or "scripted").strip().lower()
        if kind == "local":
            from .adapters.local_lora_trainer import LocalLoraTrainer

            # torch/transformers/peft are imported by the trainer on first use, not here: the
            # container is constructed during settings validation and in every test, and neither
            # may pull them in.
            return LocalLoraTrainer(
                allow_cpu=self.settings.learning.finetune_allow_cpu,
                cpu_base_allowlist=self.settings.learning.finetune_cpu_base_allowlist,
                work_dir=Path(os.environ.get("AURA_FINETUNE_WORK_DIR") or "/tmp/aura-finetune"),
            )
        if kind != "scripted":
            raise ConfigurationError(
                f"AURA_FINETUNE_TRAINER={kind!r} is not a trainer; use 'scripted' or 'local'"
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
                revision=self.settings.llm.embedding_model_revision,
                query_prefix=self.settings.llm.embedding_query_prefix,
                document_prefix=self.settings.llm.embedding_document_prefix,
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

    def _build_compiler(self, *, use_postgres: bool) -> Any:
        """The blueprint compiler (AP6): plans a stored twin and appends a ``proposed`` blueprint."""
        from .adaptation.compiler import CompilerService

        if use_postgres:
            from .adapters.postgres_adaptation import PostgresBlueprintStore

            store: Any = PostgresBlueprintStore(self.settings.infrastructure.database_url, pool=self._postgres_pool())
        else:
            from .adapters.memory_adaptation import InMemoryBlueprintStore

            store = InMemoryBlueprintStore()
        self.blueprint_store = store
        return CompilerService(
            twins=self.twins, planner=self.capability_planner, connectors=self.connectors, store=store,
            clock=self.clock, audit=self.audit, metrics=self.metrics,
        )

    def _build_deployment(self) -> Any:
        """Evaluate / approve / deploy / roll back blueprints onto the live agent and tool
        registries (AP7), behind the generated evaluation gate (AP8)."""
        from .adaptation.deployment import BlueprintDeployment
        from .adaptation.gate import BlueprintGate
        from .adaptation.knowledge import ProjectKnowledge
        from .adaptation.runtime import BlueprintToolMaterializer
        from .domain.gate import GateMode
        from .knowledge.documents import extract_document

        adaptation = self.settings.adaptation
        # AP10: the project's documents, ingested under the project's own owner on deploy and
        # searched by the blueprint's native `project_knowledge` tool.
        project_knowledge = ProjectKnowledge(
            project_id=self.twins.project_id, knowledge=self.knowledge, documents=self.documents,
            extract=extract_document, open_connector=self.connectors.open_active,
        )
        materializer = BlueprintToolMaterializer(
            tools=self.tools, open_connector=self.connectors.open_active,
            grace_s=adaptation.tool_grace_s, metrics=self.metrics, knowledge=project_knowledge,
        )
        # The gate's candidate runs in a throwaway registry under the *serving* policy (same
        # allow/deny lists), so what it refuses is what a live turn would refuse. No metrics,
        # events or audit sink: simulated calls are not user activity.
        tool_settings = self.settings.tools
        gate = BlueprintGate(
            open_connector=self.connectors.open_active,
            new_registry=lambda: ToolRegistry(
                policy=ToolPolicy.from_settings(tool_settings), allowlist=tool_settings.allowlist,
                denylist=tool_settings.denylist, default_timeout_s=tool_settings.default_timeout_s,
            ),
            clock=self.clock, mode=GateMode(adaptation.gate_mode),
            min_coverage=adaptation.gate_min_coverage, probe_seeds=adaptation.gate_probe_seeds,
            metrics=self.metrics,
        )
        deployment = BlueprintDeployment(
            project_id=self.twins.project_id, store=self.blueprint_store, approvals=self.approvals,
            agents=self.agents, materializer=materializer, clock=self.clock,
            gate=gate, twins=self.twins, audit=self.audit, metrics=self.metrics, knowledge=project_knowledge,
        )
        self.project_knowledge = project_knowledge
        self.register_readiness_check("blueprint", deployment.readiness)
        return deployment

    def _build_twins(self, *, use_postgres: bool) -> Any:
        """The Project Twin service (AP4), or ``None`` when Track AP is off.

        The labeling model is opt-in (``AURA_ADAPTATION_LABELER_ENABLED``) and goes through the
        same self-hosted router as every other completion; without it, twins carry humanised
        identifiers -- the complete degraded mode ADR-026 requires.
        """
        if self.connectors is None:
            return None
        from .adaptation.labeling import LabelingPass, RouterLabelingModel
        from .adaptation.twin import TwinService

        adaptation = self.settings.adaptation
        if use_postgres:
            from .adapters.postgres_adaptation import PostgresTwinStore

            store: Any = PostgresTwinStore(self.settings.infrastructure.database_url, pool=self._postgres_pool())
        else:
            from .adapters.memory_adaptation import InMemoryTwinStore

            store = InMemoryTwinStore()
        model = None
        if adaptation.labeler_enabled:
            model = RouterLabelingModel(self.router, timeout_s=max(1.0, adaptation.labeler_timeout_s - 10.0))
        return TwinService(
            project_id="p0",
            registry=self.connectors,
            discovery=self.discovery,
            store=store,
            labeling=LabelingPass(
                model, batch_size=adaptation.labeler_batch_size, timeout_s=adaptation.labeler_timeout_s,
                metrics=self.metrics,
            ),
            clock=self.clock,
            audit=self.audit,
            metrics=self.metrics,
        )

    def _build_control(self, *, use_postgres: bool) -> None:
        """The control center (ADR-037): store, vault, step-up and the platform verifiers.

        Platform tokens are sealed by the same vault as connector credentials, under
        ``AURA_SECRETS_ENCRYPTION_KEY``; with no key the page still opens and says so, and no
        token can be saved. Outside production-like environments the step-up and platform
        checks are the in-process adapters, so tests and a local gateway never call Google or a
        platform: register a re-sign-in token on ``control_reverifier`` to exercise them.
        """
        from .adapters.control_store import InMemoryControlStore, PostgresControlStore
        from .adapters.credential_vault import (
            SECRETS_KEYS_ENV,
            EncryptedCredentialVault,
            InMemoryCiphertextStore,
        )
        from .adapters.field_crypto import FieldCipher
        from .config.settings import secrets_encryption_keys
        from .control.service import ControlService, StepUpService
        from .control.wiring import cloudflare_api_root

        settings = self.settings
        url = settings.infrastructure.database_url
        if use_postgres:
            from .adapters.postgres_adaptation import PostgresCiphertextStore

            store: Any = PostgresControlStore(url, pool=self._postgres_pool())
            ciphertexts: Any = PostgresCiphertextStore(url, pool=self._postgres_pool())
        else:
            store = InMemoryControlStore()
            ciphertexts = InMemoryCiphertextStore()
        try:
            cipher = FieldCipher(secrets_encryption_keys(), setting=SECRETS_KEYS_ENV)
        except ConfigurationError as exc:
            # Built on every boot, unlike Track AP's vault (behind AURA_ADAPTATION_ENABLED): a
            # malformed key must cost the control center, not the gateway. The page then shows
            # the "set the vault key" warning and nothing can be connected.
            logger.error(
                "control center vault disabled: the secrets key is not usable",
                extra={"setting": SECRETS_KEYS_ENV, "error": str(exc)},
            )
            cipher = FieldCipher.disabled()
        vault = EncryptedCredentialVault(cipher=cipher, store=ciphertexts)
        control = settings.control
        if settings.environment.is_production_like:
            from .adapters.identity_reverify import GoogleReverifier
            from .adapters.platform_verify import HttpPlatformVerifier

            reverifier: Any = GoogleReverifier(client_ids=control.google_client_ids)
            verifier: Any = HttpPlatformVerifier(
                default_qdrant_url=settings.infrastructure.qdrant_url,
                cloudflare_api_root=cloudflare_api_root(settings.llm.providers),
            )
        else:
            from .adapters.identity_reverify import StaticReverifier
            from .adapters.platform_verify import StaticPlatformVerifier

            reverifier = StaticReverifier()
            verifier = StaticPlatformVerifier()
        self.control_store = store
        self.control_vault = vault
        self.control_reverifier = reverifier
        self.control_verifier = verifier
        self.step_up = StepUpService(
            store=store, reverifier=reverifier,
            max_age_s=control.step_up_max_age_s, ttl_s=control.step_up_ttl_s,
        )
        self.control = ControlService(
            store=store, vault=vault, verifier=verifier, writes_per_hour=control.writes_per_hour,
        )

        import os

        # Stage 2: Railway, reached with the project token stored above. Read from the vault on
        # every call, so connecting or replacing the token takes effect without a restart.
        from .control.railway import RailwayService
        from .control.service import Reconciler
        from .domain.control import Platform

        if settings.environment.is_production_like:
            from .adapters.railway_platform import RailwayPlatform
            from .adapters.settings_probe import SubprocessSettingsValidator

            async def railway_token() -> str:
                return (await self.control.token(Platform.RAILWAY)).reveal()

            railway_api: Any = RailwayPlatform(token_source=railway_token)
            validator: Any = SubprocessSettingsValidator()
        else:
            from .adapters.railway_platform import InMemoryRailway
            from .adapters.settings_probe import StaticSettingsValidator

            railway_api = InMemoryRailway()
            validator = StaticSettingsValidator()
        # Stage 3: the inference Space, reached with the stored HF token; the Space itself is the
        # one named when that token was connected.
        from .control.space import SpaceService

        if settings.environment.is_production_like:
            from .adapters.hf_space import HfSpaceHost

            async def hf_token() -> str:
                return (await self.control.token(Platform.HF_SPACE)).reveal()

            space_api: Any = HfSpaceHost(token_source=hf_token)
        else:
            from .adapters.hf_space import InMemorySpace

            space_api = InMemorySpace()
        self.space_api = space_api
        self.space = SpaceService(
            control=self.control, api=space_api, store=store, watch_s=control.space_watch_s,
        )
        self.railway_api = railway_api
        self.settings_validator = validator
        # Railway sets RAILWAY_SERVICE_ID in every service's container: the gateway's own id is
        # the service whose variables the AURA settings tab (and every key, stage 4) edits.
        self.railway = RailwayService(
            control=self.control, api=railway_api, validator=validator,
            self_service_id=(os.environ.get("RAILWAY_SERVICE_ID") or "").strip(),
        )

        # Stage 4: provider keys. A key is a gateway variable; the Cloudflare management token
        # (stored above) creates and revokes Workers AI keys at the API root derived from the
        # configured Workers AI provider.
        from .control.keys import KeysService
        from .control.wiring import key_slots

        if settings.environment.is_production_like:
            from .adapters.cloudflare_account import HttpCloudflareAccount
            from .adapters.cloudflare_tokens import HttpCloudflareTokens

            async def cloudflare_token() -> str:
                return (await self.control.token(Platform.CLOUDFLARE)).reveal()

            cloudflare_api: Any = HttpCloudflareTokens(
                token_source=cloudflare_token, api_root=cloudflare_api_root(settings.llm.providers),
            )
            cloudflare_account: Any = HttpCloudflareAccount(
                token_source=cloudflare_token, api_root=cloudflare_api_root(settings.llm.providers),
            )
        else:
            from .adapters.cloudflare_account import InMemoryCloudflareAccount
            from .adapters.cloudflare_tokens import InMemoryCloudflareTokens

            cloudflare_api = InMemoryCloudflareTokens()
            cloudflare_account = InMemoryCloudflareAccount()
        self.cloudflare_tokens = cloudflare_api
        self.cloudflare_account = cloudflare_account

        # Stage 5: Qdrant Cloud (the cluster named when its key was connected, or the gateway's
        # own) and Grafana Cloud (the stack named with its token).
        from .control.monitoring import MonitoringService
        from .control.vector import VectorService
        from .adapters.alert_rules import load_alert_groups

        qdrant_default = settings.infrastructure.qdrant_url
        if settings.environment.is_production_like:
            from .adapters.grafana_cloud import HttpGrafanaCloud
            from .adapters.qdrant_admin import HttpQdrantAdmin

            async def qdrant_key() -> str:
                return (await self.control.token(Platform.QDRANT)).reveal()

            async def qdrant_url() -> str:
                credential = await store.get_credential(Platform.QDRANT)
                return (credential.target if credential and credential.target else qdrant_default) or ""

            async def grafana_token() -> str:
                return (await self.control.token(Platform.GRAFANA)).reveal()

            async def grafana_stack_url() -> str:
                credential = await store.get_credential(Platform.GRAFANA)
                return credential.target if credential else ""

            vector_api: Any = HttpQdrantAdmin(token_source=qdrant_key, url_source=qdrant_url)
            grafana_api: Any = HttpGrafanaCloud(token_source=grafana_token, stack_source=grafana_stack_url)
        else:
            from .adapters.grafana_cloud import InMemoryGrafana
            from .adapters.qdrant_admin import InMemoryQdrant

            vector_api = InMemoryQdrant()
            grafana_api = InMemoryGrafana()
        self.vector_api = vector_api
        self.grafana_api = grafana_api
        self.vector = VectorService(control=self.control, api=vector_api, store=store, default_url=qdrant_default)
        self.monitoring = MonitoringService(
            control=self.control, store=store, api=grafana_api, rules=load_alert_groups,
            metrics_token_set=lambda: bool((os.environ.get("AURA_METRICS_TOKEN") or "").strip()),
        )
        self.keys = KeysService(
            control=self.control, store=store, railway=self.railway, space=self.space,
            space_api=space_api, cloudflare=cloudflare_api, slots=key_slots(settings.llm.providers),
            provider_health=lambda: self.router.stats(),
        )
        # Stage 6: the Cloudflare tab's engine (providers, model catalog). A removed provider's
        # token is revoked by the reconciler, and only once the change has stayed healthy.
        from .control.cloudflare import REVOKE_ACTION, CloudflareControl

        self.cloudflare_control = CloudflareControl(
            control=self.control, store=store, railway=self.railway, tokens=cloudflare_api,
            account=cloudflare_account, api_root=cloudflare_api_root(settings.llm.providers),
            provider_health=lambda: self.router.stats(),
        )
        self.control_reconciler = Reconciler(
            store=store, vault=vault,
            platforms={Platform.RAILWAY: railway_api, Platform.HF_SPACE: space_api},
            watch_s=control.rollback_watch_s,
            success_hooks={REVOKE_ACTION: self.cloudflare_control.revoke_after_success},
        )
        self._reconciler_task: Any = None

    def _build_adaptation(self, *, use_postgres: bool) -> Any:
        """The connector registry, its vault and its stores -- or ``None`` when Track AP is off.

        The vault uses the third-party-credentials key (``AURA_SECRETS_ENCRYPTION_KEY``), not the
        data key: losing it costs "re-enter every connector password", not every user's memories,
        so the two rotate independently (``adapters/field_crypto.py``). With no key configured the
        registry still builds -- connectors that need no credential work, and a credentialed
        registration is refused by the vault rather than stored in the clear.
        """
        if not self.settings.adaptation.enabled:
            return None
        from .adaptation.connectors import ConnectorRegistry
        from .adapters.connectors.files import FilesConnectorFactory
        from .adapters.connectors.git import GitConnectorFactory
        from .adapters.connectors.mssql import SqlServerConnectorFactory
        from .adapters.connectors.mysql import MySqlConnectorFactory
        from .adapters.connectors.openapi import OpenApiConnectorFactory
        from .adapters.connectors.postgres import PostgresConnectorFactory
        from .adapters.credential_vault import (
            SECRETS_KEYS_ENV,
            EncryptedCredentialVault,
            InMemoryCiphertextStore,
        )
        from .adapters.field_crypto import FieldCipher
        from .config.settings import secrets_encryption_keys
        from .domain.connectors import ConnectorKind

        url = self.settings.infrastructure.database_url
        if use_postgres:
            from .adapters.postgres_adaptation import PostgresCiphertextStore, PostgresConnectorStore

            store: Any = PostgresConnectorStore(url, pool=self._postgres_pool())
            ciphertexts: Any = PostgresCiphertextStore(url, pool=self._postgres_pool())
        else:
            from .adapters.memory_adaptation import InMemoryConnectorStore

            store = InMemoryConnectorStore()
            ciphertexts = InMemoryCiphertextStore()
        vault = EncryptedCredentialVault(
            cipher=FieldCipher(secrets_encryption_keys(), setting=SECRETS_KEYS_ENV), store=ciphertexts
        )
        factories: dict[Any, Any] = {
            ConnectorKind.POSTGRES: PostgresConnectorFactory(forbidden_targets=(url,)),
            ConnectorKind.OPENAPI: OpenApiConnectorFactory(),
            # Discovery only (AP10): their tables compile to no tool until each dialect has reads.
            ConnectorKind.MYSQL: MySqlConnectorFactory(),
            ConnectorKind.SQL_SERVER: SqlServerConnectorFactory(),
        }
        if self.settings.adaptation.files_root:
            # Offered only with an operator-chosen root: no root, no readable path on this host.
            factories[ConnectorKind.FILES] = FilesConnectorFactory(root=self.settings.adaptation.files_root)
        if self.settings.adaptation.git_root:
            # Read-only checkouts under an operator-chosen root (AP10): discovery only, never run.
            factories[ConnectorKind.GIT] = GitConnectorFactory(root=self.settings.adaptation.git_root)
        return ConnectorRegistry(
            store=store,
            vault=vault,
            approvals=self.approvals,
            factories=factories,
            clock=self.clock,
            audit=self.audit,
            metrics=self.metrics,
        )

    def _postgres_pool(self) -> Any:
        """One connection pool shared by every Postgres-backed store.

        Built on first request and memoised. Two stores against the same database with separate
        pools would double the connection count for no benefit -- the prod compose configures
        Postgres for a modest `max_connections`, and each gateway worker holding two pools is how a
        four-worker deployment exhausts it.
        """
        if self._pg_pool is None:
            from .adapters.postgres_store import PostgresPool

            infra = self.settings.infrastructure
            self._pg_pool = PostgresPool(
                infra.database_url,
                min_size=infra.postgres_pool_min_size,
                max_size=infra.postgres_pool_max_size,
                command_timeout_s=infra.postgres_command_timeout_s,
                max_inactive_connection_lifetime_s=infra.postgres_max_inactive_connection_lifetime_s,
            )
        return self._pg_pool

    def _field_cipher(self) -> Any:
        """The data-at-rest cipher shared by every store that encrypts a column. Memoised."""
        if self._cipher is None:
            self._cipher = build_field_cipher(self.settings, metrics=self.metrics)
        return self._cipher

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
                self.settings.infrastructure.database_url,
                pool=self._postgres_pool(),
                cipher=self._field_cipher(),
            )
        if backend != "memory":
            raise ConfigurationError(f"unknown memory backend {backend!r}")
        return InMemoryMemoryStore()

    def _build_user_data_keys(self) -> Any:
        """The `UserDataKeyStore` (ADR-032): per-user data keys wrapped by the field cipher's
        keys (`AURA_DATA_ENCRYPTION_KEYS`), so they rotate with it."""
        backend = self.settings.infrastructure.memory_backend
        if backend == "postgres":
            from .adapters.postgres_user_data_keys import PostgresUserDataKeyStore

            return PostgresUserDataKeyStore(
                self.settings.infrastructure.database_url,
                pool=self._postgres_pool(),
                cipher=self._field_cipher(),
            )
        if backend != "memory":
            raise ConfigurationError(f"unknown memory backend {backend!r}")
        from .adapters.user_data_keys import InMemoryUserDataKeyStore

        return InMemoryUserDataKeyStore()

    def _build_legacy_personal_os_store(self) -> Any:
        """`aura_personal_os_records`, the agent's store before ADR-032. Read by
        `scripts/move_personal_os_records.py` and cleared by erasure; nothing else writes it.
        None in memory mode, where there is no old data to move."""
        if self.settings.infrastructure.memory_backend == "postgres":
            from .adapters.postgres_personal_os import PostgresPersonalOsStore

            return PostgresPersonalOsStore(
                self.settings.infrastructure.database_url,
                pool=self._postgres_pool(),
                cipher=self._field_cipher(),
            )
        return None

    def _build_personal_os_store(self) -> Any:
        """The agent's personal-OS store (ADR-032): the app's synced tables in production, so
        the agent and the app share one store; in memory otherwise."""
        backend = self.settings.infrastructure.memory_backend
        if backend == "postgres":
            from .adapters.postgres_synced_personal_os import PostgresSyncedPersonalOsStore

            return PostgresSyncedPersonalOsStore(
                self.settings.infrastructure.database_url,
                pool=self._postgres_pool(),
                keys=self.user_data_keys,
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

    def _build_engineer_consent_store(self) -> Any:
        """Consent to the Engineer's Anthropic notice (D-03). Postgres with the other per-user
        stores, so an answer reaches every gateway worker and survives a restart."""
        backend = self.settings.infrastructure.memory_backend
        if backend == "postgres":
            from .adapters.postgres_engineer_consent import PostgresEngineerConsentStore

            return PostgresEngineerConsentStore(
                self.settings.infrastructure.database_url, pool=self._postgres_pool()
            )
        if backend != "memory":
            raise ConfigurationError(f"unknown memory backend {backend!r}")
        from .adapters.engineer_consent import InMemoryEngineerConsentStore

        return InMemoryEngineerConsentStore()

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

    def _build_image_normalizer(self) -> Any:
        """`None` unless vision is on and Pillow is importable (it is a declared dependency, so
        the second condition only fails on a broken install, and then loudly at startup)."""
        vision = self.settings.vision
        if not vision.enabled:
            return None
        from .adapters.pillow_images import PillowImageNormalizer

        return PillowImageNormalizer(
            max_bytes=vision.max_image_bytes,
            max_pixels=vision.max_pixels,
            max_edge_px=vision.max_edge_px,
        )

    def _build_vision_engine(self) -> Any:
        """The kernel's image perception step (ADR-031), or `None` with vision off. Built even
        when no provider declares the image modality: the engine then falls back to OCR or
        reports `dependency_unavailable`, which is the honest answer, rather than vanishing."""
        if not self.settings.vision.enabled:
            return None
        from .vision.engine import VisionEngine

        return VisionEngine(
            router=self.router,
            settings=self.settings.vision,
            ocr=self.ocr_engine,
            metrics=self.metrics,
        )

    def _build_stt_engine(self) -> Any:
        """`None` unless both opted in (`voice.stt_enabled`) and the `faster-whisper` package is
        actually importable -- the model itself loads lazily on first real call, so this check
        costs nothing at container-build time."""
        if not self.settings.voice.stt_enabled:
            return None
        from .adapters.whisper_stt import FasterWhisperEngine, faster_whisper_available

        if not faster_whisper_available():
            return None
        return FasterWhisperEngine(
            model_size=self.settings.voice.stt_model,
            revision=self.settings.voice.stt_model_revision,
        )

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
            "vad_factory": self._live_vad_factory(cfg.sample_rate),
            "stt": self.stt_engine,
            "tts": self.tts_engine,
            "nlp": self.nlp,
            "max_sessions": voice.live_max_sessions,
            "kernel": self.kernel,
            "cache": self.cache_backend,
            "safety_guards_enabled": self.settings.safety.output_guards_enabled,
        }

    def _live_vad_factory(self, sample_rate: int) -> Any:
        """A ``() -> VoiceActivityDetector | None`` that gives each live session its own
        detector, per ``AURA_VOICE_LIVE_VAD``. ``None`` from it means the energy default.

        Silero is tried once, here: if it cannot serve (wrong capture rate, model missing or
        failing to load) the factory logs why and hands out the energy detector for the life of
        the process instead of retrying on every connection.
        """
        voice = self.settings.voice
        if voice.live_vad != "silero":
            self.live_vad_name = "energy"
            return lambda: None
        from .adapters.silero_vad import build_silero_vad

        def build() -> Any:
            return build_silero_vad(
                sample_rate=sample_rate,
                threshold=voice.live_vad_threshold,
                barge_in_threshold=voice.live_vad_barge_in_threshold,
            )

        if build() is None:
            self.live_vad_name = "energy"
            return lambda: None
        self.live_vad_name = "silero"
        return build

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

    def _build_service_keys(self) -> Any:
        """Service keys for non-human callers (AR-4). Postgres-backed with the other stores, so a
        revocation reaches every gateway worker on its next request; in-memory otherwise."""
        from .security.service_keys import ServiceKeyService

        if self.settings.infrastructure.document_backend == "postgres":
            from .adapters.postgres_service_keys import PostgresServiceKeyStore

            store: Any = PostgresServiceKeyStore(
                self.settings.infrastructure.database_url, pool=self._postgres_pool()
            )
        else:
            from .adapters.service_keys import InMemoryServiceKeyStore

            store = InMemoryServiceKeyStore()
        return ServiceKeyService(store=store, clock=self.clock, audit=self.audit)

    def _build_tenancy(self) -> Any:
        """Workspaces and team membership (SaaS P1, ADR-034). Postgres-backed with the other
        stores, so removing a member reaches every gateway worker on its next request."""
        from .tenancy import TenancyService

        if self.settings.infrastructure.document_backend == "postgres":
            from .adapters.postgres_tenancy import PostgresTenancyStore

            store: Any = PostgresTenancyStore(
                self.settings.infrastructure.database_url, pool=self._postgres_pool()
            )
        else:
            from .adapters.tenancy import InMemoryTenancyStore

            store = InMemoryTenancyStore()
        return TenancyService(store=store, clock=self.clock, audit=self.audit, settings=self.settings.tenancy)

    def _build_plans(self) -> Any:
        """Plans, entitlements and usage metering (SaaS P1-C). Postgres-backed with the other
        stores, so every gateway worker shares one count; in-memory otherwise."""
        from .domain.plans import load_catalogue
        from .plans import PlanService

        if self.settings.infrastructure.document_backend == "postgres":
            from .adapters.postgres_plans import PostgresEntitlementStore, PostgresUsageMeter

            url = self.settings.infrastructure.database_url
            entitlements: Any = PostgresEntitlementStore(url, pool=self._postgres_pool())
            meter: Any = PostgresUsageMeter(url, pool=self._postgres_pool())
        else:
            from .adapters.plans import InMemoryEntitlementStore, InMemoryUsageMeter

            entitlements, meter = InMemoryEntitlementStore(), InMemoryUsageMeter()
        return PlanService(
            entitlements=entitlements, meter=meter, tenancy=self.tenancy, clock=self.clock,
            audit=self.audit, catalogue=load_catalogue(self.settings.tenancy.plan_catalogue_json),
        )

    def _build_billing(self) -> Any:
        """Subscriptions and Fawry payments (SaaS P1-D1). Fawry only when merchant credentials are
        set; otherwise the in-memory fake (local runs and tests -- production refuses to start
        with billing enabled and no credentials, ``AuraSettings.validate``)."""
        from .billing import BillingService
        from .domain.billing import load_prices

        billing = self.settings.billing
        if self.settings.infrastructure.document_backend == "postgres":
            from .adapters.postgres_billing import (
                PostgresBillingContactStore,
                PostgresBillingLocks,
                PostgresPaymentStore,
                PostgresSubscriptionStore,
            )

            url = self.settings.infrastructure.database_url
            subscriptions: Any = PostgresSubscriptionStore(url, pool=self._postgres_pool())
            payments: Any = PostgresPaymentStore(url, pool=self._postgres_pool())
            # Advisory locks: one settle per workspace and one cycle at a time across every
            # gateway worker and the cron process (review D1#2/#6).
            locks: Any = PostgresBillingLocks(url, pool=self._postgres_pool())
            contacts: Any = PostgresBillingContactStore(url, pool=self._postgres_pool())
        else:
            from .adapters.billing import (
                InMemoryBillingContactStore,
                InMemoryBillingLocks,
                InMemoryPaymentStore,
                InMemorySubscriptionStore,
            )

            subscriptions, payments, locks = InMemorySubscriptionStore(), InMemoryPaymentStore(), InMemoryBillingLocks()
            contacts = InMemoryBillingContactStore()
        if billing.fawry_configured:
            from .adapters.fawry import FawryPaymentProvider

            provider: Any = FawryPaymentProvider(billing.fawry_base_url, billing.fawry_merchant_code, billing.fawry_secure_key)
        else:
            from .adapters.billing import FakePaymentProvider
            from .config.settings import Environment
            from .domain.errors import ConfigurationError

            if billing.enabled and self.settings.environment is not Environment.LOCAL:
                # The fake settles anything signed with its own key: never a payment path outside a
                # developer's machine (review D1, minor).
                raise ConfigurationError("billing is enabled with no Fawry credentials outside a local run")
            provider = FakePaymentProvider()
        return BillingService(
            subscriptions=subscriptions, payments=payments, provider=provider, plans=self.plans,
            tenancy=self.tenancy, clock=self.clock, audit=self.audit, settings=billing,
            prices=load_prices(billing.prices_json), locks=locks, contacts=contacts, notifier=self.notifier,
            heartbeat=self.cache_backend,
        )

    def _build_sharing(self) -> Any:
        """Chats shared with a team (SaaS P1-F1)."""
        from .sharing import SharingService

        if self.settings.infrastructure.document_backend == "postgres":
            from .adapters.postgres_sharing import PostgresSharedConversationStore

            store: Any = PostgresSharedConversationStore(self.settings.infrastructure.database_url,
                                                         pool=self._postgres_pool())
        else:
            from .adapters.sharing import InMemorySharedConversationStore

            store = InMemorySharedConversationStore()
        return SharingService(store=store, tenancy=self.tenancy, clock=self.clock, audit=self.audit)

    def _build_email(self) -> tuple[Any, Any]:
        """Customer email (SaaS P1-F1). With email off, a sender that only logs, and no
        notifier at all -- nothing tries to send."""
        email = self.settings.email
        if not email.enabled:
            from .adapters.email import LogEmailSender

            return LogEmailSender(), None
        from .adapters.email import SmtpEmailSender
        from .notifications import Notifier

        sender = SmtpEmailSender(email.smtp_host, email.smtp_port, email.smtp_username, email.smtp_password,
                                 email.from_address, starttls=email.smtp_starttls)
        return sender, Notifier(sender=sender, settings=email, audit=self.audit, clock=self.clock, metrics=self.metrics)

    def _build_store_billing(self) -> Any:
        """App Store and Google Play subscriptions (SaaS P1-D2). Shares D1's subscription store,
        locks and audit; each store is wired only when its settings are complete."""
        from .billing import StoreBillingService
        from .domain.billing import load_store_products

        billing = self.settings.billing
        if self.settings.infrastructure.document_backend == "postgres":
            from .adapters.postgres_billing import PostgresStoreBindingStore, PostgresStoreEventLog

            url = self.settings.infrastructure.database_url
            bindings: Any = PostgresStoreBindingStore(url, pool=self._postgres_pool())
            events: Any = PostgresStoreEventLog(url, pool=self._postgres_pool())
        else:
            from .adapters.billing import InMemoryStoreBindingStore, InMemoryStoreEventLog

            bindings, events = InMemoryStoreBindingStore(), InMemoryStoreEventLog()
        apple = google_push = google_play = None
        if billing.apple_configured:
            from .adapters.apple_store import AppleNotificationVerifier

            # Production unless Sandbox is asked for: Sandbox purchases are free (D2 review #5).
            apple = AppleNotificationVerifier(billing.apple_bundle_id, environment=billing.apple_environment or "Production")
        if billing.google_configured:
            from .adapters.google_play import GooglePlayClient, GooglePushVerifier

            google_push = GooglePushVerifier(billing.google_push_audience, billing.google_push_service_account)
            google_play = GooglePlayClient(billing.google_package_name, billing.google_play_credentials)
        return StoreBillingService(
            bindings=bindings, events=events, subscriptions=self.billing.subscriptions, plans=self.plans,
            tenancy=self.tenancy, clock=self.clock, audit=self.audit, locks=self.billing.locks,
            settings=billing, products=load_store_products(billing.store_products_json, self.plans.catalogue),
            apple=apple, google_push=google_push, google_play=google_play,
        )

    def _build_advisory_store(self) -> Any:
        """The local OSV advisory copy (``domain/advisories.py``): read by the dependency scan, written
        by the scheduled sync. Postgres-backed like the other shared stores, so a worker's download is
        what every gateway process reads."""
        if self.settings.infrastructure.document_backend == "postgres":
            from .adapters.postgres_advisories import PostgresAdvisoryStore

            return PostgresAdvisoryStore(
                self.settings.infrastructure.database_url, pool=self._postgres_pool()
            )
        from .adapters.advisories_memory import InMemoryAdvisoryStore

        return InMemoryAdvisoryStore()

    def _build_push_notifier(self) -> Any:
        """Push notifications (ADR-060). Tokens are stored whenever the app registers one; a sender
        exists only when push is enabled *and* a Firebase service-account file is configured."""
        push = self.settings.push
        if self.settings.infrastructure.document_backend == "postgres":
            from .adapters.postgres_push import PostgresPushDeviceStore

            store: Any = PostgresPushDeviceStore(
                self.settings.infrastructure.database_url, pool=self._postgres_pool())
        else:
            from .adapters.push_memory import InMemoryPushDeviceStore

            store = InMemoryPushDeviceStore()
        sender: Any = None
        if push.enabled and push.fcm_credentials_file:
            from .adapters.fcm_push import FcmPushSender

            sender = FcmPushSender(push.fcm_credentials_file)
        from .push import PushNotifier

        return PushNotifier(store, sender, include_detail=push.include_detail)

    def _build_prompt_pins(self) -> Any:
        """Prompt-version pins (INT-004). Postgres-backed with the other stores, so a pin reaches
        every gateway worker within ``PIN_SYNC_INTERVAL_S`` and survives a restart."""
        if self.settings.infrastructure.document_backend == "postgres":
            from .adapters.postgres_prompt_pins import PostgresPromptPinStore

            return PostgresPromptPinStore(
                self.settings.infrastructure.database_url, pool=self._postgres_pool()
            )
        from .adapters.prompt_pins import InMemoryPromptPinStore

        return InMemoryPromptPinStore()

    def _build_idempotency_store(self) -> Any:
        """`Idempotency-Key` bookkeeping (roadmap P1.4). Gated like the outbox: replay only means
        something if it survives a restart and is shared by every gateway worker, which only the
        Postgres table gives. In-memory is correct for tests and a single-process local run."""
        if self.settings.infrastructure.document_backend == "postgres":
            from .adapters.postgres_idempotency import PostgresIdempotencyStore

            return PostgresIdempotencyStore(
                self.settings.infrastructure.database_url, pool=self._postgres_pool()
            )
        from .adapters.idempotency import InMemoryIdempotencyStore

        return InMemoryIdempotencyStore()

    def _build_insight_sink(self) -> Any:
        """The `InsightSink` (Phase E2): writes into the legacy `insights` table, the same
        read-a-legacy-table-via-raw-SQL pattern `_build_goals_port` uses to read `goals`."""
        if self.settings.infrastructure.document_backend == "postgres":
            from .adapters.postgres_insights import PostgresInsightSink

            return PostgresInsightSink(
                self.settings.infrastructure.database_url, pool=self._postgres_pool()
            )
        return InMemoryInsightSink()

    def _build_entity_extractor(self) -> Any:
        """Rules always; the model extractor only when explicitly enabled."""
        if not self.settings.knowledge.graph_llm_extraction:
            return RuleEntityExtractor()
        from .knowledge.extraction import CompositeExtractor, LLMEntityExtractor

        return CompositeExtractor(
            [RuleEntityExtractor(), LLMEntityExtractor(router=self.router, prompts=self.prompts)]
        )

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

    def _build_host_agent_relay(self) -> Any:
        """The cross-worker relay for desktop host-agent sessions, or ``None``.

        A daemon's socket lives in one gateway worker; without the relay, only that worker can
        reach it, so with ``--workers 4`` most Engineer turns found no desktop (ADR-011,
        "Deployment constraint"). Built whenever Redis is configured and a signing secret exists.
        Nothing connects here -- the relay subscribes on first use.
        """
        infra = self.settings.infrastructure
        reason = ""
        if not infra.host_agent_relay:
            reason = "disabled by AURA_HOST_AGENT_RELAY"
        elif not infra.redis_url:
            reason = "no AURA_REDIS_URL"
        elif not infra.host_agent_relay_secret:
            reason = "no JWT secret to sign relay messages with"
        if reason:
            if self.settings.environment.is_production_like:
                logger.warning(
                    "host-agent relay off: a paired desktop is reachable only from the gateway "
                    "worker holding its socket, so run one worker (AURA_WORKERS=1)",
                    extra={"reason": reason},
                )
            return None

        from .adapters.host_agent_relay import HostAgentRelay
        from .adapters.redis_relay_bus import RedisRelayBus

        return HostAgentRelay(RedisRelayBus(infra.redis_url), infra.host_agent_relay_secret)

    async def _host_routed(self, context) -> bool:
        """Whether this caller's sandbox calls go to their paired desktop: the same predicate
        ``CompositeSandboxRunner.create_task`` routes on (a live ``aura_hostd`` session).

        Reuses the turn's own answer only when it was *yes* (KAN-109): a desktop seen this turn
        means no relay round trip per tool call. A *no* is re-asked live. ``True`` here can only
        make policy stricter (a tainted or remote-device turn is held for confirmation), so a
        stale yes costs at most a confirmation; a stale no would let a desktop that connected
        mid-turn take a side effect under the sandbox's looser rule, so it is never reused.
        """
        lookups = current_turn_lookups()
        key = context.user_id.value if context.user_id is not None else None
        if lookups is not None and key is not None and lookups.desktop.get(key):
            return True
        routed = await self.host_agent_registry.resolve(context.user_id) is not None
        if routed and lookups is not None and key is not None:
            lookups.desktop[key] = True
        return routed

    async def _guard_tool_result(self, text: str, tool: str, context) -> Any:
        """Tool output through the safety pipeline's TOOL_RESULT stage (inventory row 34).

        Credentials are redacted and injection-looking output is withheld. ``SafetyPipeline`` is a
        pass-through when ``AURA_SAFETY_ENABLED`` is off, so this needs no flag of its own.
        """
        return await self.safety.guard_tool_result(text, tool=tool, user_id=context.user_id)

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
            host_routed=self._host_routed,
            result_guard=self._guard_tool_result,
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
                host_agent_registry=self.host_agent_registry,
                # Both default to empty (and weather_enabled to False), which registers nothing
                # (list_files/read_file/fetch_url/get_weather were implemented but could never be
                # switched on: audit T-06). An operator opts in with AURA_TOOLS_FILES_ROOT /
                # AURA_TOOLS_HTTP_ALLOWED_HOSTS / AURA_TOOLS_WEATHER_ENABLED; personas still need a grant.
                workspace=self.settings.tools.files_root or None,
                allowed_http_hosts=self.settings.tools.http_allowed_hosts,
                enable_weather=self.settings.tools.weather_enabled,
                web_search_url=self.settings.tools.web_search_url,
                web_search_timeout_s=self.settings.tools.web_search_timeout_s,
                web_search_engines=self.settings.tools.web_search_engines,
                web_search_language=self.settings.tools.web_search_language,
                ocr_engine=self.ocr_engine,
                vision_engine=self.vision,
                image_normalizer=self.image_normalizer,
                stt_engine=self.stt_engine,
                enable_analysis_tools=self.settings.open_tools.analysis_enabled,
                advisory_store=self.advisory_store,
                advisory_max_age_days=self.settings.advisories.max_age_days,
                ops=self.settings.ops,
                graph_store=self.graph if self.settings.knowledge.graph_enabled else None,
                enable_open_exec_tools=self.settings.open_tools.exec_enabled,
                enable_desktop_ops_tools=self.settings.open_tools.desktop_ops_enabled,
                enable_open_pure_tools=self.settings.open_tools.pure_enabled,  # ADR-044
                episodic_memory=self.episodic,
                # ADR-045 project_status: the owner-scoped run stores (None when not configured).
                engineer_runs=self.engineer_runs,
                agent_runs=self.agent_runs,
            )
        )
        # ADR-046 (open partial tools): the model-metric history lives in the Cache port, so these two
        # tools need the container's cache and cannot be built by `default_tools`.
        if self.settings.open_tools.analysis_enabled:
            from .tools.drift import build_drift_tools

            registry.register_all(build_drift_tools(self.cache_backend))
        if self.structured_extractor is not None:
            # ADR-038. Built here, not in ``default_tools``: it needs the audit sink and the clock,
            # and like every other opt-in resource it exists only when its adapter does.
            from .tools.extract import build_extract_tools

            extraction = self.settings.extraction
            registry.register_all(
                build_extract_tools(
                    extractor=self.structured_extractor,
                    audit=self.audit,
                    clock=self.clock,
                    min_confidence=extraction.min_confidence,
                    max_chars=extraction.max_chars,
                    timeout_s=extraction.timeout_s,
                    daily_call_cap=extraction.daily_call_cap,
                )
            )
        return registry

    def _build_lesson_sink(self) -> Any:
        """``async (LessonTurn) -> None`` queueing an Engineer turn's lessons for review, or ``None``.

        Candidates are screened with the same context guards retrieved chunks pass (an injection
        in a "lesson" is dropped, not stored), checked for secrets and duplicates, and written to
        the engineer scope's *pending* partition (``learning/jobs.py``). Nothing here promotes them.
        """
        if not self.settings.learning.auto_lessons_enabled or self.memory is None:
            return None
        from .learning.jobs import run_engineer_lesson_extraction

        safety = self.safety if self.settings.safety.enabled else None
        # ADR-047 (row 60): optional, default off. Local-only by construction (see the module).
        model_extractor = None
        if self.settings.learning.auto_lessons_model_enabled:
            from .learning.model_lessons import build_model_lesson_extractor

            model_extractor = build_model_lesson_extractor(self.router)

        async def screen(text: str) -> bool:
            if safety is None:
                return True
            _accepted, quarantined, _tainted = await safety.guard_context_items([("lesson", text)])
            return not quarantined

        async def sink(turn: Any) -> None:
            checkpoint = None
            if turn.session_id:
                # The saved one, never a fresh model call: this runs after every Engineer turn.
                checkpoint = await self.episodic.latest_checkpoint(SessionId(turn.session_id), turn.user_id)
            report = await run_engineer_lesson_extraction(
                self.memory, turn, checkpoint=checkpoint, enabled=True, screen=screen,
                model_extractor=model_extractor,
            )
            logger.info("engineer lessons", extra={"report": report})

        return sink


    def _build_structured_extractor(self) -> Any:
        """Cloudflare's ``clairic`` behind the extraction port, or ``None`` when it is off (ADR-038).

        Off is the default and means no adapter and no tool. On, the adapter is built here and
        connects lazily like every other: nothing touches the network until the first call. A bad
        account id or an empty credential raises ``ConfigurationError`` from the adapter itself,
        the same refusal ``AuraSettings.validate`` gives at startup, repeated for callers that
        loaded settings with ``validate=False``.
        """
        extraction = self.settings.extraction
        if not extraction.enabled:
            return None
        from .adapters.cloudflare_clairic import ClairicExtractor

        return ClairicExtractor(
            account_id=extraction.account_id,
            api_key=extraction.api_key,
            clock=self.clock,
            timeout_s=extraction.timeout_s,
        )

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
        # Re-arms shutdown(): a container started again after one (a test client re-entering
        # its lifespan, an evaluation run) reconnects lazily, and those clients must close too.
        self._shut_down = False
        if self.task_queue is not None:
            await self.task_queue.start()
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
                collisions: list[dict[str, str]] = []
                mcp_tools = await build_mcp_bridge_tools(
                    self.mcp_client,
                    safe_tools=self.settings.mcp.safe_tools,
                    collisions=collisions,
                )
                self.mcp_tool_collisions = tuple(collisions)
                self.tools.register_all(mcp_tools)
                logger.info(
                    "MCP tools registered",
                    extra={"count": len(mcp_tools), "collisions": len(collisions)},
                )
            except Exception:
                logger.exception("MCP tool discovery failed; continuing without MCP tools")
        if self.blueprint_deployment is not None:
            # Never raises: a blueprint that cannot load leaves the core personas serving.
            await self.blueprint_deployment.startup()
            self._blueprint_sync_task = asyncio.get_running_loop().create_task(self._sync_blueprints())
        # Pins made before this process started (or by another worker) apply before the first
        # turn; a store that is not answering must not stop the gateway from serving -- the
        # templates' default versions are a working configuration, and the sync retries.
        try:
            await self.prompts.refresh_pins()
        except Exception:
            logger.exception("prompt pin load failed; serving default prompt versions until sync")
        if self._prompt_pin_sync_task is None or self._prompt_pin_sync_task.done():
            self._prompt_pin_sync_task = asyncio.get_running_loop().create_task(self._sync_prompt_pins())
        if self._should_warm_stt():
            # In the background: readiness must not wait on a Hub download, but the first
            # live-voice turn of a fresh container should not pay for it either (audit FSA-110).
            self._stt_warm_task = asyncio.get_running_loop().create_task(self._warm_stt())
        logger.info("container startup complete")

    def _should_warm_stt(self) -> bool:
        engine = self.stt_engine
        if engine is None or not hasattr(engine, "warm"):
            return False
        explicit = self.settings.voice.stt_warm_on_startup
        return self.settings.environment.is_production_like if explicit is None else bool(explicit)

    async def _warm_stt(self) -> None:
        try:
            await self.stt_engine.warm()
            logger.info("speech-to-text model warmed", extra={"stt": getattr(self.stt_engine, "name", None)})
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("speech-to-text warm-up failed; the first transcription will load the model")

    async def _sync_blueprints(self) -> None:
        """Keep this worker on the store's active blueprint (a deploy swaps only its own process)."""
        interval = self.settings.adaptation.sync_s
        while True:
            await asyncio.sleep(interval)
            try:
                await self.blueprint_deployment.sync()
            except Exception:  # pragma: no cover - defensive; sync() already swallows store errors
                logger.exception("blueprint sync failed")

    async def _sync_prompt_pins(self) -> None:
        """Keep this worker on the shared prompt pins (INT-004): one store read per interval, so
        the per-turn ``prompts.get()`` stays an in-memory lookup."""
        while True:
            await asyncio.sleep(PIN_SYNC_INTERVAL_S)
            try:
                await self.prompts.refresh_pins()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("prompt pin sync failed; keeping the last known pins")

    def start_control_reconciler(self) -> bool:
        """Start the control center's rollback watcher in this process (the gateway's startup).

        Staging/production only, and only once per process. Every gateway worker runs one; the
        lease in Postgres lets exactly one act. Celery workers never call this.
        """
        settings = self.settings
        if (self._reconciler_task is not None or not settings.control.reconciler_enabled
                or not settings.environment.is_production_like):
            return False
        self._reconciler_task = asyncio.create_task(
            self.control_reconciler.run_forever(), name="control-reconciler"
        )
        return True

    async def _stop_control_reconciler(self) -> None:
        task, self._reconciler_task = self._reconciler_task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def shutdown(self) -> None:
        """Release every connection this container opened (R2.7). Idempotent; never raises.

        Called by the gateway's lifespan shutdown, which uvicorn runs only after its graceful
        drain (``--timeout-graceful-shutdown``) has let in-flight requests finish, and by each
        Celery worker process on exit, on that process's one event loop (``tasks.py``) -- the
        loop the asyncpg pool is bound to.

        Only what was actually constructed is closed. Every adapter here connects lazily, and
        each ``close`` returns at once when its client was never built; the Postgres pool is read
        from its memo rather than through ``_postgres_pool()``, which would create one. Building a
        client in order to close it would be a network call on the way out, often against the
        very dependency whose outage is why the process is stopping.

        The closes run concurrently, each bounded by ``SHUTDOWN_CLOSE_TIMEOUT_S``, and one
        failing or hanging is logged without stopping the rest.
        """
        if self._shut_down:
            return
        self._shut_down = True
        if getattr(self, "task_queue", None) is not None:
            # ADR-043 gateway queue: first, and on its own -- it cancels running tasks and writes
            # their final state to the cache, which must still be open while it does.
            await self._close_quietly("agent task queue", self.task_queue.stop)
        if getattr(self, "agent_tasks", None) is not None:
            # Background sub-turns live in this process: stop them and write their final records
            # first, before the cache they report to (and the providers they call) are closed.
            await self._close_quietly("agent tasks", self.agent_tasks.shutdown)
        await self._stop_control_reconciler()
        try:
            await self.kernel.wait_for_post_turn(timeout_s=POST_TURN_DRAIN_S)
        except Exception:  # pragma: no cover - defensive; shutdown never raises
            logger.warning("post-turn drain failed", exc_info=True)
        closers = self._shutdown_closers()
        await asyncio.gather(*(self._close_quietly(name, close) for name, close in closers))
        mark_dead = getattr(self.metrics, "mark_process_dead", None)
        if mark_dead is not None:
            mark_dead()
        logger.info("container shutdown complete", extra={"closed": [name for name, _ in closers]})

    def _shutdown_closers(self) -> list[tuple[str, Any]]:
        """``(name, zero-argument coroutine function)`` for each resource to release."""
        closers: list[tuple[str, Any]] = []

        warm, self._stt_warm_task = self._stt_warm_task, None
        if warm is not None and not warm.done():
            warm.cancel()
        pin_sync, self._prompt_pin_sync_task = self._prompt_pin_sync_task, None
        if pin_sync is not None:
            pin_sync.cancel()

            async def stop_prompt_pin_sync() -> None:
                try:
                    await pin_sync
                except asyncio.CancelledError:
                    pass

            closers.append(("prompt pin sync", stop_prompt_pin_sync))
        task, self._blueprint_sync_task = self._blueprint_sync_task, None
        if task is not None:
            task.cancel()

            async def stop_blueprint_sync() -> None:
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            closers.append(("blueprint sync", stop_blueprint_sync))

        # Each provider holds its own httpx client (openai_compatible, ollama).
        for provider in self.providers:
            if isinstance(provider, Closeable):
                closers.append((f"provider {provider.name}", provider.close))

        for name, adapter in (
            ("embedder", self.embedder),
            ("cache", self.cache_backend),  # Redis
            ("vector store", self.vectors),  # Qdrant
            ("docker sandbox", getattr(self, "docker_sandbox_runner", None)),
            ("host agent relay", getattr(getattr(self, "host_agent_registry", None), "relay", None)),
            ("github client", self.github_client),
            ("mcp client", self.mcp_client),
            ("structured extractor", getattr(self, "structured_extractor", None)),
        ):
            if adapter is not None and isinstance(adapter, Closeable):
                closers.append((name, adapter.close))

        user_clients = list(self._github_clients_by_user.values())
        self._github_clients_by_user.clear()
        for client in user_clients:
            closers.append(("github client (user)", client.close))

        # Every Postgres store shares this one pool, so it is closed here once, not per store.
        if self._pg_pool is not None:
            pool = self._pg_pool
            # The pool's own budget is shorter than the outer one, so a connection still busy
            # gets terminated rather than abandoned when the outer timeout cancels the close.
            graceful_s = SHUTDOWN_CLOSE_TIMEOUT_S * 0.8
            closers.append(("postgres pool", lambda: pool.close(timeout_s=graceful_s)))

        return closers

    @staticmethod
    async def _close_quietly(name: str, close: Any) -> None:
        timeout = SHUTDOWN_CLOSE_TIMEOUT_S
        try:
            await asyncio.wait_for(close(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("shutdown: %s did not close within %.1fs; abandoned", name, timeout)
        except Exception as exc:
            logger.warning(
                "shutdown: closing %s failed (%s: %s); continuing", name, type(exc).__name__, exc
            )

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
        knowledge_index = await self.index_manager.status()
        # Informational, never gates readiness: chat works without knowledge, and only an admin
        # rebuild repairs this, so evicting the gateway from the load balancer would not help.
        # The gauge is what an alert reads (AuraKnowledgeIndexMismatch).
        self.metrics.knowledge_index_mismatch.set(0 if knowledge_index["matches_embedder"] else 1)
        # A passive provider (health_probe=passive) was not called, so its "healthy" means only
        # "not known to be broken". It cannot carry readiness on its own while a provider that
        # *was* checked is down; it counts only when every provider is passive.
        probed = [p for p in providers.values() if p.get("probe") != "passive"]
        healthy = any(p["healthy"] for p in (probed or providers.values()))

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

        # ADR-038. Informational, never gates readiness: an optional tool, and the adapter has no
        # free endpoint to probe, so this reports configuration and its own cooldown, never a call.
        extractor = self.structured_extractor
        extraction: dict[str, Any] = {"enabled": extractor is not None}
        if extractor is not None:
            extraction["model"] = extractor.model_id
            extraction["cooling_down"] = not (await extractor.health()).healthy

        payload = {
            "providers": providers,
            "index": knowledge_index["published"],
            # Which index is published versus which the running embedder needs. A mismatch means
            # every retrieval returns nothing until an admin rebuilds (scripts/rebuild_index.py).
            "knowledge_index": knowledge_index,
            "tools": len(self.tools),
            "prompts": len(self.prompts),
            "agents": len(self.agents),
            "voice": {
                "tts": getattr(self.tts_engine, "name", None) if self.tts_engine else None,
                "stt": getattr(self.stt_engine, "name", None) if self.stt_engine else None,
                "live": bool(self.live_voice),
                # The detector live sessions actually get, after any fallback: "silero" only
                # when the model loaded, never just because it was asked for.
                "vad": self.live_vad_name if self.live_voice else None,
            },
            "environment": str(self.settings.environment),
            # Which embedder indexes and queries this deployment's knowledge and memory. The
            # hash embedder is refused in production by AuraSettings.validate(), but a reader
            # of this payload should not have to infer that: `semantic: false` names it
            # outright (audit FSA-031). Informational, never gates readiness.
            # Audit FSA-018: whether chunk text travels into vector payloads, decided by where the
            # vector store runs (settings.vector_payload_content_enabled). Informational.
            "vectors": {
                "backend": self.settings.infrastructure.vector_backend,
                "payload_content": self.vector_payload_content,
            },
            "embedding": {
                "provider": self.settings.llm.embedding_provider,
                "model": getattr(self.embedder, "model_id", None),
                "revision": self.settings.llm.embedding_model_revision or None,
                "dimension": getattr(self.embedder, "dimension", None),
                "semantic": not isinstance(self.embedder, HashEmbeddingProvider),
            },
            "extraction": extraction,
            "healthy": healthy,
            # Informational, never gates readiness: `healthy` needs any provider, but a turn sent
            # with `require_local` (service callers such as Flame default to it) can only use a
            # local one, so it fails while `healthy` is true unless this is too.
            "local_healthy": any(p["healthy"] and p.get("is_local") for p in providers.values()),
        }
        if checks:
            payload["checks"] = checks
        return payload


def build_field_cipher(settings: AuraSettings, *, metrics: Any = None) -> Any:
    """Load the data-at-rest keys and build the cipher (P1.2/P5.1).

    The only place ``AURA_DATA_ENCRYPTION_KEYS`` is turned into a key object -- the stores and
    ``scripts/reencrypt_fields.py`` both get their cipher from here, so there is one answer to
    "what happens without a key":

    * **staging/production**: refuse (``ConfigurationError``). ``AuraSettings.validate`` already
      refuses at startup; this repeats the check for callers that loaded settings with
      ``validate=False``, because the failure mode it prevents -- personal data silently written
      in plaintext -- is not one to leave to the caller remembering to validate.
    * **local/development/test**: plaintext passthrough with a warning. Existing encrypted rows
      are still refused on read (``FieldCipher.decrypt``), never returned as ciphertext.
    """
    from .adapters.field_crypto import FieldCipher

    keys = data_encryption_keys()
    if keys:
        cipher = FieldCipher(keys, metrics=metrics)
        logger.info(
            "data encryption at rest enabled",
            extra={"primary_key_id": cipher.primary_key_id, "key_count": len(keys)},
        )
        return cipher
    if settings.environment.is_production_like:
        raise ConfigurationError(
            f"{DATA_ENCRYPTION_KEYS_ENV} must be set in staging/production",
            details={"setting": DATA_ENCRYPTION_KEYS_ENV},
        )
    logger.warning(
        "data encryption at rest DISABLED: no data key configured, memory content and "
        "personal-OS record fields will be stored in plaintext",
        extra={"setting": DATA_ENCRYPTION_KEYS_ENV, "environment": str(settings.environment)},
    )
    return FieldCipher.disabled()


def build_container(settings: AuraSettings | None = None) -> Container:
    """Build the graph from settings, loading and validating them if not supplied."""
    return Container(settings or load_settings())
