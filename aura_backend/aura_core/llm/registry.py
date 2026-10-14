"""Provider construction from configuration.

The one place that maps a :class:`~aura_core.config.settings.ProviderConfig` to a concrete
adapter. Adapter modules are imported **lazily inside each branch**, so a missing optional
dependency surfaces as a clear configuration error rather than an ImportError at startup.

This is what makes "add an inference node" a config change (ADR-003): nothing above this file
knows which runtimes exist.

**Every kind here is built for our own hardware.** There is no hosted-vendor branch, and adding one
would fail ``tests/architecture/test_no_external_inference.py``. ``openai_compatible`` covers
vLLM, llama.cpp's server, LocalAI and TGI; ``ollama`` covers Ollama's native API. The wire
format being shared with hosted services is why ``ProviderConfig`` also refuses their hostnames
-- see ``ProviderConfig._reject_external_base_url``. The one gated exception, hosted open-weight
APIs behind ``AURA_ALLOW_HOSTED_INFERENCE`` (ADR-030), still goes through ``openai_compatible``;
it is a base URL an operator chose, not a branch here.
"""

from __future__ import annotations

from typing import Sequence

from ..config.settings import LLMSettings, ProviderConfig
from ..domain.errors import ConfigurationError
from ..domain.llm import Modality, ModelPool, ProviderCapabilities
from ..ports.llm import LLMProvider
from .admission import PoolLimits
from .quota import QuotaLimits

__all__ = [
    "admission_limits_from_settings",
    "build_provider",
    "build_providers",
    "capabilities_from_config",
    "quota_limits_from_settings",
]


def capabilities_from_config(config: ProviderConfig) -> ProviderCapabilities:
    """Translate declared configuration into the capability descriptor the router filters on.

    Validation lives in :class:`ProviderCapabilities.__post_init__`, so an incoherent declaration
    (``max_output_tokens`` above ``context_window``) fails at startup rather than producing
    truncation surprises in production.
    """
    return ProviderCapabilities(
        model_id=config.model,
        context_window=config.context_window,
        max_output_tokens=config.max_output_tokens,
        supports_streaming=config.supports_streaming,
        supports_tools=config.supports_tools,
        supports_json_mode=config.supports_json_mode,
        is_local=config.is_local,
        quality_tier=config.quality_tier,
        cost_per_1k_input_usd=config.cost_per_1k_input_usd,
        cost_per_1k_output_usd=config.cost_per_1k_output_usd,
        max_concurrency=config.max_concurrency,
        pools=tuple(ModelPool(p) for p in config.pools),
        speed_tier=config.speed_tier,
        memory_cost=config.memory_cost,
        gpu_cost=config.gpu_cost,
        modalities=tuple(Modality(m) for m in config.modalities),
        supports_vision=Modality.IMAGE.value in config.modalities,
        supports_reasoning=config.supports_reasoning,
        languages=config.languages,
        model_revision=config.model_revision,
        capacity_group=config.capacity_group,
        max_data_classification=config.data_classification_ceiling,
        third_party=config.third_party,
        tokens_per_second=config.tokens_per_second,
    )


def quota_limits_from_settings(settings: LLMSettings) -> dict[str, QuotaLimits]:
    """Declared request/token limits per provider (ADR-030 Phase 2).

    Only providers that declare at least one limit appear; the rest are never refused by a
    budget, only benched by a ``429`` they actually send.
    """
    limits: dict[str, QuotaLimits] = {}
    for config in settings.enabled_providers:
        declared = QuotaLimits(
            rpm=config.rpm, rpd=config.rpd, tpm=config.tpm, tpd=config.tpd
        )
        if declared.declared:
            limits[config.name] = declared
    return limits


def admission_limits_from_settings(settings: LLMSettings) -> dict[str, PoolLimits]:
    """Per-capacity-group admission limits (P2.4) from provider declarations.

    A group's slot count is the smallest ``max_concurrency`` among its providers (they share one
    serving process, so the most conservative declaration is the true one); its queue bound and
    timeout come from the first provider that overrides them, else the LLM-wide defaults.
    """
    limits: dict[str, PoolLimits] = {}
    for config in settings.enabled_providers:
        group = config.capacity_group or config.name
        current = limits.get(group)
        cap = config.max_concurrency if current is None else min(
            current.max_concurrent, config.max_concurrency
        )
        max_queue = (
            config.max_queue if config.max_queue is not None
            else current.max_queue if current is not None
            else settings.admission_max_queue
        )
        timeout = (
            config.queue_timeout_s if config.queue_timeout_s is not None
            else current.queue_timeout_s if current is not None
            else settings.admission_queue_timeout_s
        )
        limits[group] = PoolLimits(max_concurrent=cap, max_queue=max_queue, queue_timeout_s=timeout)
    return limits


def build_provider(config: ProviderConfig, *, model_allowlist: Sequence[str] = ()) -> LLMProvider:
    capabilities = capabilities_from_config(config)

    if config.kind == "echo":
        from .providers.echo import EchoProvider

        return EchoProvider(name=config.name, model=config.model or "echo-1", capabilities=capabilities)

    if config.kind == "openai_compatible":
        from .providers.openai_compatible import OpenAICompatibleProvider

        return OpenAICompatibleProvider(
            name=config.name,
            base_url=config.base_url,
            model=config.model,
            api_key=config.api_key,
            capabilities=capabilities,
            timeout_s=config.timeout_s,
            health_timeout_s=config.health_timeout_s,
            health_path=config.health_path,
            extra_body=config.extra_body_fields,
        )

    if config.kind == "ollama":
        from .providers.ollama import OllamaProvider

        return OllamaProvider(
            name=config.name,
            base_url=config.base_url,
            model=config.model,
            capabilities=capabilities,
            timeout_s=config.timeout_s,
            health_timeout_s=config.health_timeout_s,
            model_allowlist=model_allowlist,
        )

    raise ConfigurationError(
        f"provider {config.name!r} has unsupported kind {config.kind!r}",
        details={
            "supported": ["openai_compatible", "ollama", "echo"],
            "note": (
                "hosted inference providers were removed deliberately; point `openai_compatible` "
                "at a local vLLM, llama.cpp, LocalAI or TGI server instead"
            ),
        },
    )


def build_providers(
    settings: LLMSettings, *, model_allowlist: Sequence[str] = ()
) -> list[LLMProvider]:
    providers = [
        build_provider(config, model_allowlist=model_allowlist)
        for config in settings.enabled_providers
    ]
    if not providers:
        raise ConfigurationError("no LLM providers are enabled")
    return providers
