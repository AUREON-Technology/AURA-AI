"""Provider construction from configuration.

The one place that maps a :class:`~aura_core.config.settings.ProviderConfig` to a concrete
adapter. Adapter modules are imported **lazily inside each branch**, so a missing optional
dependency surfaces as a clear configuration error rather than an ImportError at startup.

This is what makes "add an inference node" a config change (ADR-003): nothing above this file
knows which runtimes exist.

**Every kind here runs on our own hardware.** There is no hosted-vendor branch, and adding one
would fail ``tests/architecture/test_no_external_inference.py``. ``openai_compatible`` covers
vLLM, llama.cpp's server, LocalAI and TGI; ``ollama`` covers Ollama's native API. The wire
format being shared with hosted services is why ``ProviderConfig`` also refuses their hostnames
-- see ``ProviderConfig._reject_external_base_url``.
"""

from __future__ import annotations

from typing import Sequence

from ..config.settings import LLMSettings, ProviderConfig
from ..domain.errors import ConfigurationError
from ..domain.llm import ProviderCapabilities
from ..ports.llm import LLMProvider

__all__ = ["build_provider", "build_providers", "capabilities_from_config"]


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
    )


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
        )

    if config.kind == "ollama":
        from .providers.ollama import OllamaProvider

        return OllamaProvider(
            name=config.name,
            base_url=config.base_url,
            model=config.model,
            capabilities=capabilities,
            timeout_s=config.timeout_s,
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
