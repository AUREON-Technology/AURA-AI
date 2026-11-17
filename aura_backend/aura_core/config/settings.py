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

import base64
import binascii
import json
import logging
import os
import re
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from typing import ClassVar, Any, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

from ..domain.errors import ConfigurationError
from ..domain.llm import DataClassification, Modality, ModelPool, RoutingPolicy
from .external_inference import (
    CLAUDE_PROVIDER_KIND,
    is_claude_model,
    is_external_inference_host,
    is_hosted_open_weight_host,
    is_open_weight_model,
    is_remote_inference_host,
)

# Stdlib logging rather than aura_core.observability: settings is the lowest layer here and
# the observability module reads configuration, so importing it would risk a cycle for the
# sake of one warning.
logger = logging.getLogger("aura_core.config")

__all__ = [
    "Environment",
    "AuraSettings",
    "TenancySettings",
    "BillingSettings",
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
    "VisionSettings",
    "McpServerConfig",
    "McpSettings",
    "GitHubSettings",
    "SandboxSettings",
    "DATA_ENCRYPTION_KEYS_ENV",
    "data_encryption_keys",
    "SECRETS_ENCRYPTION_KEYS_ENV",
    "secrets_encryption_keys",
    "AdaptationSettings",
    "load_settings",
]

_TRUE = {"1", "true", "yes", "on"}

#: Keys for server-side encryption at rest of memory content and personal-OS record fields
#: (``adapters/field_crypto.py``). Comma- or whitespace-separated Fernet keys, **newest first**:
#: the first encrypts, all decrypt. Like ``GitHubSettings.token`` it is read straight from the
#: environment and never stored on a settings dataclass, so ``as_dict()`` cannot leak it.
#: Required in staging/production (see ``AuraSettings.validate``); optional elsewhere, where its
#: absence means plaintext storage and a startup warning (``container.build_field_cipher``).
DATA_ENCRYPTION_KEYS_ENV = "AURA_DATA_ENCRYPTION_KEYS"


def data_encryption_keys() -> list[str]:
    """The configured data keys, newest first. Empty when none is configured."""
    raw = os.environ.get(DATA_ENCRYPTION_KEYS_ENV, "") or ""
    return [part for part in re.split(r"[\s,]+", raw.strip()) if part]


#: Keys for third-party credentials at rest -- the GitHub OAuth tokens (``secrets_crypto.py``)
#: and connector credentials (``adapters/credential_vault.py``). One key for the former; the
#: vault also accepts a comma/whitespace-separated list, newest first, to rotate.
SECRETS_ENCRYPTION_KEYS_ENV = "AURA_SECRETS_ENCRYPTION_KEY"


def secrets_encryption_keys() -> list[str]:
    """The configured third-party-credential keys, newest first. Empty when none is set."""
    raw = os.environ.get(SECRETS_ENCRYPTION_KEYS_ENV, "") or ""
    return [part for part in re.split(r"[\s,]+", raw.strip()) if part]


def _is_fernet_key(key: str) -> bool:
    """The same check ``cryptography.fernet.Fernet`` makes, done with the stdlib so the config
    layer stays free of vendor imports: urlsafe base64 decoding to exactly 32 bytes."""
    try:
        return len(base64.urlsafe_b64decode(key.encode("ascii"))) == 32
    except (binascii.Error, ValueError):
        return False
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


def _choice(key: str, default: str, accepted: Sequence[str]) -> str:
    raw = _env(key)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value not in accepted:
        raise ConfigurationError(f"AURA_{key} must be one of {', '.join(accepted)}",
                                 details={"value": raw, "accepted": list(accepted)})
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
    kind: str  # openai_compatible | ollama | echo | claude (ADR-033, gated)
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
    #: How long the liveness probe waits, separate from `timeout_s`. Raise it for an
    #: endpoint that scales to zero: a 5s probe can never observe a sleeping Hugging Face
    #: Space or cold serverless GPU waking, because waking takes minutes.
    health_timeout_s: float = 5.0
    weight: int = 1
    enabled: bool = True
    # Model-level metadata (roadmap P2.3) -- AURA_PROVIDER_<NAME>_{POOLS,SPEED_TIER,MEMORY_COST,
    # GPU_COST,MODALITIES,SUPPORTS_REASONING,LANGUAGES,MODEL_REVISION,CAPACITY_GROUP}. Every
    # default is "undeclared", which never excludes a provider: an env file written before these
    # existed routes exactly as it did.
    pools: tuple[str, ...] = ()
    speed_tier: int = 2
    memory_cost: float = 0.0
    gpu_cost: float = 0.0
    modalities: tuple[str, ...] = ("text",)
    supports_reasoning: bool = False
    languages: tuple[str, ...] = ()
    model_revision: str = ""
    capacity_group: str = ""
    # Admission (roadmap P2.4) -- AURA_PROVIDER_<NAME>_{MAX_QUEUE,QUEUE_TIMEOUT_S}. None = inherit
    # AURA_LLM_ADMISSION_MAX_QUEUE / AURA_LLM_ADMISSION_QUEUE_TIMEOUT_S. The concurrency cap is
    # the existing max_concurrency.
    max_queue: int | None = None
    queue_timeout_s: float | None = None
    # Quotas (ADR-030 Phase 2) -- AURA_PROVIDER_<NAME>_{RPM,RPD,TPM,TPD}: the provider's published
    # requests/tokens per minute/day. 0 = undeclared, which never refuses a request; a hosted
    # free tier declares them so the router skips it *before* sending rather than learning from
    # a 429. See aura_core/llm/quota.py.
    rpm: int = 0
    rpd: int = 0
    tpm: int = 0
    tpd: int = 0
    #: The liveness probe's path under base_url. ``/models`` is universal for OpenAI-compatible
    #: servers; override it for a hosted API that does not serve it at the chat base URL.
    health_path: str = "/models"
    #: The most private content this provider may receive -- public | internal | sensitive
    #: (ADR-030 §4). Empty = the category default: ``internal`` for a hosted open-weight API,
    #: ``sensitive`` (everything) for anything else. AURA_PROVIDER_<NAME>_MAX_DATA_CLASSIFICATION.
    max_data_classification: str = ""
    #: Declared decode throughput, output tokens/s (ADR-030 Phase 3). 0 = undeclared: the
    #: provider is never given a request's larger *preferred* output size. Set it for a fast
    #: hosted API; leave it unset for a CPU provider. AURA_PROVIDER_<NAME>_TOKENS_PER_SECOND.
    tokens_per_second: float = 0.0
    #: Extra top-level fields merged into every OpenAI-compatible request body, as a JSON object
    #: -- e.g. ``{"reasoning_effort": "low"}`` so a reasoning model does not spend its output
    #: budget, and its per-minute token quota, on hidden reasoning. Fields AURA itself sets
    #: (model, messages, max_tokens, tools, response_format, ...) are refused: those decide what
    #: the request is, and a provider override there would make the router's capability checks
    #: untrue. AURA_PROVIDER_<NAME>_EXTRA_BODY.
    extra_body: str = ""
    #: The operator accepts that this hosted provider may train on, or retain, the prompts it
    #: receives (ADR-030 §3, as amended). Off by default, and required for a ``:free`` model ID:
    #: aggregators serve free endpoints under a "free model training" data policy. A provider with
    #: this consent is capped below ``sensitive`` and is named as such in every audit record.
    #: AURA_PROVIDER_<NAME>_ALLOW_PROMPT_TRAINING.
    allow_prompt_training: bool = False

    #: Request-body fields an ``extra_body`` may not set. See :attr:`extra_body`.
    _RESERVED_BODY_FIELDS: ClassVar[frozenset[str]] = frozenset({
        "model", "messages", "stream", "stream_options", "max_tokens", "max_completion_tokens",
        "tools", "tool_choice", "functions", "function_call", "response_format", "n",
    })
    #: Further fields reserved for ``kind=claude`` (ADR-033), whose request shape differs. Its
    #: ``extra_body`` may carry ``output_config`` (effort), ``fallbacks``, ``inference_geo`` and
    #: the like; it may not decide what the request is.
    _CLAUDE_RESERVED_BODY_FIELDS: ClassVar[frozenset[str]] = frozenset({
        "system", "thinking", "betas", "stop_sequences", "cache_control", "container",
        "mcp_servers", "thread",
    })

    # Self-hosted runtimes, plus one gated vendor. `ollama` and `openai_compatible` between them
    # cover Ollama, vLLM, llama.cpp's server, LocalAI, TGI and anything else exposing the OpenAI
    # wire format, all of which run on our own hardware. `claude` is the single closed-model kind
    # ADR-033 sanctions; `_gate_claude` refuses it unless AURA_ALLOW_CLAUDE_INFERENCE=true.
    _KINDS = frozenset({"openai_compatible", "ollama", "echo", CLAUDE_PROVIDER_KIND})

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
        if self.kind == CLAUDE_PROVIDER_KIND:
            self._gate_claude()
        self._validate_model_metadata()
        if not self.health_path.startswith("/"):
            raise ConfigurationError(
                f"provider {self.name!r} health_path must start with '/'",
                details={"provider": self.name, "health_path": self.health_path},
            )
        self._validate_extra_body()
        known_classes = {c.value for c in DataClassification}
        if self.max_data_classification and self.max_data_classification not in known_classes:
            raise ConfigurationError(
                f"provider {self.name!r} has unknown max_data_classification "
                f"{self.max_data_classification!r}",
                details={"supported": sorted(known_classes)},
            )
        self._reject_external_base_url()
        if self.allow_prompt_training and not self.third_party:
            raise ConfigurationError(
                f"provider {self.name!r} sets allow_prompt_training but is not a hosted "
                f"open-weight API; the consent only means something for a third party.",
                details={"provider": self.name, "base_url": self.base_url},
            )

    def _validate_model_metadata(self) -> None:
        """Fail at boot on a pool or modality name that would otherwise silently never match."""
        known_pools = {p.value for p in ModelPool}
        unknown = [p for p in self.pools if p not in known_pools]
        if unknown:
            raise ConfigurationError(
                f"provider {self.name!r} declares unknown pool(s) {unknown}",
                details={"supported": sorted(known_pools)},
            )
        known_mods = {m.value for m in Modality}
        bad_mods = [m for m in self.modalities if m not in known_mods]
        if bad_mods or not self.modalities:
            raise ConfigurationError(
                f"provider {self.name!r} declares unknown or empty modalities {bad_mods}",
                details={"supported": sorted(known_mods)},
            )

    def _reject_external_base_url(self) -> None:
        """Refuse banned hosts; gate hosted open-weight APIs and rented compute behind opt-ins.

        ``openai_compatible`` speaks the same protocol as several hosted APIs, so deleting the
        vendor adapters was not on its own sufficient -- a base_url is all it would take to route
        every turn back out to a third party. The host lists are shared with the architecture
        fitness tests so the runtime check and the static scan cannot disagree.

        Three categories, and the difference matters:

        * A **banned** host runs somebody else's model. Always refused, no override.
        * A **hosted open-weight** host runs a model with published weights on the provider's
          servers (ADR-030). Refused unless ``AURA_ALLOW_HOSTED_INFERENCE`` is true, and even
          then only for an open-weight model ID and never as ``is_local``. The flag is separate
          from the rented-compute one because it is a separate consent: here a third party
          reads every prompt.
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

        hosted = is_hosted_open_weight_host(host)
        if hosted:
            self._gate_hosted_open_weight(host, hosted)
            return

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

    def _validate_extra_body(self) -> None:
        if not self.extra_body:
            return
        try:
            parsed = json.loads(self.extra_body)
        except json.JSONDecodeError as exc:
            raise ConfigurationError(
                f"provider {self.name!r} extra_body is not valid JSON",
                details={"provider": self.name, "error": str(exc)},
            ) from exc
        if not isinstance(parsed, dict):
            raise ConfigurationError(
                f"provider {self.name!r} extra_body must be a JSON object",
                details={"provider": self.name},
            )
        reserved_fields = self._RESERVED_BODY_FIELDS
        if self.kind == CLAUDE_PROVIDER_KIND:
            reserved_fields = reserved_fields | self._CLAUDE_RESERVED_BODY_FIELDS
            output_config = parsed.get("output_config")
            if output_config is not None and (
                not isinstance(output_config, dict) or "format" in output_config
            ):
                raise ConfigurationError(
                    f"provider {self.name!r} extra_body.output_config must be an object without "
                    f"'format': AURA sets the response format from the request's schema",
                    details={"provider": self.name},
                )
        reserved = sorted(set(parsed) & reserved_fields)
        if reserved:
            raise ConfigurationError(
                f"provider {self.name!r} extra_body may not set {reserved}: AURA sets those, and "
                f"the router's capability checks depend on them being what AURA sent",
                details={"provider": self.name, "reserved": reserved},
            )

    @property
    def extra_body_fields(self) -> dict[str, Any]:
        """:attr:`extra_body` parsed (validated at construction)."""
        return json.loads(self.extra_body) if self.extra_body else {}

    @property
    def third_party(self) -> bool:
        """Whether a third party runs this provider and can read its prompts (ADR-030, ADR-033)."""
        if self.kind == CLAUDE_PROVIDER_KIND:
            return True
        if not self.base_url:
            return False
        return is_hosted_open_weight_host(urlsplit(self.base_url).hostname or "") is not None

    @property
    def data_classification_ceiling(self) -> DataClassification:
        """The declared ceiling, or the category default when none is declared."""
        if self.max_data_classification:
            return DataClassification(self.max_data_classification)
        return DataClassification.INTERNAL if self.third_party else DataClassification.SENSITIVE

    def _gate_claude(self) -> None:
        """ADR-033: Anthropic's Claude, allowed only on purpose and only through the gated path.

        Every refusal names its own reason, because each has a different fix. What this keeps
        true even with the opt-in set:

        * the endpoint is the SDK's own -- no ``base_url`` may redirect the key or the prompts;
        * the key comes only from the per-slot ``AURA_PROVIDER_<NAME>_API_KEY_ENV`` variable, so
          the SDK can never fall back to an ambient credential it finds lying around;
        * the provider is third-party (``third_party`` is true for this kind), which gives it
          ADR-030's ``internal`` data ceiling by default, the router's refusal of user images,
          and an audit record per generation;
        * it is never ``is_local``, so ``local_first`` and ``require_local`` never pick it.
        """
        details = {"provider": self.name, "kind": self.kind, "model": self.model}
        if not _bool("ALLOW_CLAUDE_INFERENCE", False):
            raise ConfigurationError(
                f"provider {self.name!r} is kind {CLAUDE_PROVIDER_KIND!r}: Anthropic's hosted Claude "
                f"models. This is allowed, but not by accident: every prompt, and any retrieved "
                f"content the data ceiling admits, is sent to Anthropic, and these turns stop "
                f"working without internet. Set AURA_ALLOW_CLAUDE_INFERENCE=true to accept that "
                f"(ADR-033). AURA_ALLOW_HOSTED_INFERENCE does not cover it.",
                details=details,
            )
        if self.base_url:
            raise ConfigurationError(
                f"provider {self.name!r} is kind {CLAUDE_PROVIDER_KIND!r} and must not set a "
                f"base_url: it always uses the Anthropic SDK's own endpoint, so a configuration "
                f"value can never redirect its key or its prompts elsewhere (ADR-033).",
                details={**details, "base_url": self.base_url},
            )
        if not is_claude_model(self.model):
            raise ConfigurationError(
                f"provider {self.name!r} names model {self.model!r}, which is not a Claude model ID "
                f"(expected e.g. 'claude-opus-5-5', exactly as Anthropic's API names it).",
                details=details,
            )
        if not self.api_key_env:
            raise ConfigurationError(
                f"provider {self.name!r} must name the variable holding its key: set "
                f"AURA_PROVIDER_{self.name.upper()}_API_KEY_ENV. The SDK's ambient credential "
                f"lookup is never used (ADR-033).",
                details=details,
            )
        if self.is_local:
            raise ConfigurationError(
                f"provider {self.name!r} is a hosted Claude model but declares is_local=true. A "
                f"hosted provider is never local; the flag would let local_first prefer it and let "
                f"local-only routing send it data it must not see.",
                details=details,
            )
        if self.allow_prompt_training and (
            self.data_classification_ceiling is DataClassification.SENSITIVE
        ):
            raise ConfigurationError(
                f"provider {self.name!r} may train on its prompts and is also allowed SENSITIVE "
                f"content. Lower its MAX_DATA_CLASSIFICATION, or drop ALLOW_PROMPT_TRAINING.",
                details=details,
            )
        logger.warning(
            "Claude inference permitted by AURA_ALLOW_CLAUDE_INFERENCE (ADR-033); prompts within "
            "this provider's data ceiling are sent to Anthropic",
            extra={**details, "max_data_classification": self.data_classification_ceiling.value},
        )
        if self.data_classification_ceiling is DataClassification.SENSITIVE:
            logger.warning(
                "Claude provider may receive SENSITIVE content (documents, memory, tool output, "
                "Engineer workspaces): AURA_PROVIDER_<NAME>_MAX_DATA_CLASSIFICATION=sensitive",
                extra=details,
            )

    def _gate_hosted_open_weight(self, host: str, matched: str) -> None:
        """ADR-030: a hosted open-weight API is allowed only on purpose, and only for open weights.

        Each refusal names its own reason, because each one has a different fix.
        """
        details = {"provider": self.name, "host": host, "matched": matched, "model": self.model}
        if not _bool("ALLOW_HOSTED_INFERENCE", False):
            raise ConfigurationError(
                f"provider {self.name!r} points at {host!r}, a hosted API that runs open-weight "
                f"models on its own servers. This is allowed, but not by accident: every prompt, "
                f"retrieved document passage and memory snippet sent to this provider is readable "
                f"by its operator, and the system stops working without internet. Set "
                f"AURA_ALLOW_HOSTED_INFERENCE=true to accept that (ADR-030), or point at a host "
                f"you own.",
                details=details,
            )
        if not is_open_weight_model(self.model):
            raise ConfigurationError(
                f"provider {self.name!r} names model {self.model!r} on {host!r}, which is not a "
                f"recognised open-weight model. Hosted providers are permitted for open-weight "
                f"models only; the same key often reaches closed models too (ADR-030).",
                details={**details, "see": "aura_core/config/external_inference.py"},
            )
        if self.is_local:
            raise ConfigurationError(
                f"provider {self.name!r} points at {host!r} but declares is_local=true. A hosted "
                f"provider is never local; the flag would let the local_first policy prefer it "
                f"and let local-only routing send it data it must not see.",
                details=details,
            )
        if self.model.lower().endswith(":free") and not self.allow_prompt_training:
            # An aggregator's free endpoints are served under a "free model training" data policy:
            # the upstream may train on, or keep, what it is sent. ADR-030 §3 admits that only as
            # an explicit, per-slot consent, never as a side effect of picking a free model.
            raise ConfigurationError(
                f"provider {self.name!r} names {self.model!r}, a free endpoint whose upstream may "
                f"train on the prompts it receives. Set "
                f"AURA_PROVIDER_{self.name.upper()}_ALLOW_PROMPT_TRAINING=true to accept that, or "
                f"use a paid endpoint with a no-training data policy (ADR-030 §3).",
                details=details,
            )
        if self.allow_prompt_training and (
            self.data_classification_ceiling is DataClassification.SENSITIVE
        ):
            raise ConfigurationError(
                f"provider {self.name!r} may train on its prompts and is also allowed SENSITIVE "
                f"content. Documents, memory, tool output and Engineer workspaces must never reach "
                f"a provider that may train on them: lower its MAX_DATA_CLASSIFICATION, or drop "
                f"ALLOW_PROMPT_TRAINING (ADR-030 §3).",
                details=details,
            )
        # Warned on every boot, as the rented-compute opt-in is: whoever runs the system later
        # should see the trade-off without having been the person who accepted it.
        logger.warning(
            "hosted open-weight inference permitted by AURA_ALLOW_HOSTED_INFERENCE; prompts and "
            "retrieved content are sent to this provider",
            extra={**details, "max_data_classification": self.data_classification_ceiling.value},
        )
        if self.allow_prompt_training:
            logger.warning(
                "hosted provider may train on the prompts it receives: "
                "AURA_PROVIDER_<NAME>_ALLOW_PROMPT_TRAINING=true (ADR-030 §3)",
                extra={**details, "max_data_classification": self.data_classification_ceiling.value},
            )
        if self.data_classification_ceiling is DataClassification.SENSITIVE:
            # The default keeps documents, memory, tool output and Engineer workspaces local.
            # Raising it is allowed, and is the one setting that sends all of them out, so it
            # gets its own line rather than hiding in the one above.
            logger.warning(
                "hosted provider may receive SENSITIVE content (documents, memory, tool output, "
                "Engineer workspaces): AURA_PROVIDER_<NAME>_MAX_DATA_CLASSIFICATION=sensitive",
                extra=details,
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
            health_timeout_s=_float(f"{p}_HEALTH_TIMEOUT_S", 5.0, minimum=0.5),
            weight=_int(f"{p}_WEIGHT", 1, minimum=1),
            enabled=_bool(f"{p}_ENABLED", True),
            pools=tuple(v.lower() for v in _list(f"{p}_POOLS")),
            speed_tier=_int(f"{p}_SPEED_TIER", 2, minimum=1, maximum=3),
            memory_cost=_float(f"{p}_MEMORY_COST", 0.0, minimum=0.0),
            gpu_cost=_float(f"{p}_GPU_COST", 0.0, minimum=0.0),
            modalities=tuple(v.lower() for v in _list(f"{p}_MODALITIES", ("text",))),
            supports_reasoning=_bool(f"{p}_SUPPORTS_REASONING", False),
            languages=tuple(v.lower() for v in _list(f"{p}_LANGUAGES")),
            model_revision=_env(f"{p}_MODEL_REVISION", "") or "",
            capacity_group=_env(f"{p}_CAPACITY_GROUP", "") or "",
            max_queue=(
                _int(f"{p}_MAX_QUEUE", -1, minimum=-1) if _env(f"{p}_MAX_QUEUE") else None
            ),
            queue_timeout_s=(
                _float(f"{p}_QUEUE_TIMEOUT_S", 0.0, minimum=0.0)
                if _env(f"{p}_QUEUE_TIMEOUT_S")
                else None
            ),
            rpm=_int(f"{p}_RPM", 0, minimum=0),
            rpd=_int(f"{p}_RPD", 0, minimum=0),
            tpm=_int(f"{p}_TPM", 0, minimum=0),
            tpd=_int(f"{p}_TPD", 0, minimum=0),
            health_path=_env(f"{p}_HEALTH_PATH", "/models") or "/models",
            max_data_classification=(_env(f"{p}_MAX_DATA_CLASSIFICATION", "") or "").lower(),
            tokens_per_second=_float(f"{p}_TOKENS_PER_SECOND", 0.0, minimum=0.0),
            extra_body=(_env(f"{p}_EXTRA_BODY", "") or "").strip(),
            allow_prompt_training=_bool(f"{p}_ALLOW_PROMPT_TRAINING", False),
        )


@dataclass(frozen=True, slots=True)
class LLMSettings:
    providers: tuple[ProviderConfig, ...] = ()
    default_policy: RoutingPolicy = RoutingPolicy.BALANCED
    embedding_provider: str = "local-embeddings"
    embedding_model: str = "hash-embed-v1"
    #: Hub commit of the embedding weights (audit FSA-110). Part of the embedder's ``model_id``
    #: and so of the index identity: a new revision is a new physical index (ADR-007).
    embedding_model_revision: str = ""
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
    #: Admission defaults (P2.4), per capacity group unless a provider overrides them.
    #: -1 = unbounded queue and 0 = no queue timeout: the pre-P2.4 behaviour, so nothing changes
    #: until an operator opts in (the production compose file does).
    admission_max_queue: int = -1
    admission_queue_timeout_s: float = 0.0
    #: How many processes share each capacity group's declared cap (audit FSA-025): the gateway's
    #: ``AURA_WORKERS``. Each process gets the cap divided by this (floored at one slot), so the
    #: sum over the gateway's workers is the cap the model server was declared to bear. 1 keeps
    #: the per-process behaviour. ``AURA_LLM_ADMISSION_PROCESSES`` sets it; unset, it follows
    #: ``AURA_WORKERS`` (the image's uvicorn worker count), so Railway divides too.
    admission_processes: int = 1
    #: Concurrent generations one user may hold across all pools (and queue as many more);
    #: 0 disables the quota.
    user_max_concurrent_generations: int = 0
    #: How long a provider is benched after a ``429`` that names no reset (ADR-030 Phase 2).
    #: When the response carries ``Retry-After`` or ``x-ratelimit-reset*``, that wins.
    rate_limit_cooldown_s: float = 30.0
    #: Route interactive turns whose message scores as complex to the ``reasoning`` pool
    #: (aura_core/llm/complexity.py). Off by default, so an existing deployment routes exactly as
    #: before; ADR-033 pairs it with a Claude provider declaring that pool.
    #: AURA_LLM_COMPLEXITY_ROUTING / AURA_LLM_COMPLEXITY_THRESHOLD.
    complexity_routing: bool = False
    complexity_threshold: float = 0.5
    #: Wall-clock budget for all of one interactive turn's generations, fallbacks included
    #: (PP-06, ``llm/deadline.py``). 0 = no budget: each attempt is bounded only by its own
    #: timeout, which is the behaviour before this existed. AURA_LLM_TURN_GENERATION_BUDGET_S.
    turn_generation_budget_s: float = 0.0
    #: The most a stream may run after its first chunk (PP-07). 0 = no total limit; each chunk
    #: is still bounded by the request timeout. AURA_LLM_STREAM_MAX_S.
    stream_max_s: float = 0.0

    #: Used when nothing is configured, so ``aura_core`` is importable and testable out of the
    #: box. ``validate()`` refuses to let it run in staging/production.
    #: Declares the image modality (ADR-031) because echo is a pure function of its input, images
    #: included: it is what lets the default test suite exercise image turns with no model.
    ECHO_PROVIDER = ProviderConfig(
        name="echo", kind="echo", model="echo-1", context_window=8192, max_output_tokens=2048,
        modalities=("text", "image"),
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
            embedding_model_revision=_env("EMBEDDING_MODEL_REVISION", "") or "",
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
            admission_max_queue=_int("LLM_ADMISSION_MAX_QUEUE", -1, minimum=-1),
            admission_queue_timeout_s=_float("LLM_ADMISSION_QUEUE_TIMEOUT_S", 0.0, minimum=0.0),
            # Unset, the gateway's own worker count: Railway runs the image's
            # `--workers ${AURA_WORKERS:-4}` with no compose file to set the division.
            admission_processes=_int(
                "LLM_ADMISSION_PROCESSES", _int("WORKERS", 1, minimum=1), minimum=1, maximum=256
            ),
            user_max_concurrent_generations=_int(
                "LLM_USER_MAX_CONCURRENT_GENERATIONS", 0, minimum=0
            ),
            rate_limit_cooldown_s=_float("LLM_RATE_LIMIT_COOLDOWN_S", 30.0, minimum=1.0),
            complexity_routing=_bool("LLM_COMPLEXITY_ROUTING", False),
            complexity_threshold=_float(
                "LLM_COMPLEXITY_THRESHOLD", 0.5, minimum=0.05, maximum=1.0
            ),
            turn_generation_budget_s=_float("LLM_TURN_GENERATION_BUDGET_S", 0.0, minimum=0.0),
            stream_max_s=_float("LLM_STREAM_MAX_S", 0.0, minimum=0.0),
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
    #: Hub commit of the cross-encoder weights (audit FSA-110); empty loads the repo's head.
    rerank_model_revision: str = ""
    mmr_lambda: float = 0.7
    graph_enabled: bool = True
    #: Adds the model-backed extractor after the rule extractor. Off by default: it costs one
    #: generation per ingested document, and its output is only ever a candidate that the graph
    #: pipeline validates like any other (ADR-005, "Knowledge graph").
    graph_llm_extraction: bool = False
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
    #: Whether chunk text (and a memory's ``subject``) is copied into the vector payload (audit
    #: FSA-018 / CB-22). ``None`` decides by where the vector store runs
    #: (:func:`vector_payload_content_enabled`): on-host stores keep it for speed; an off-host
    #: Qdrant never sees readable content, and retrieval resolves the text from the owner-scoped
    #: document store instead. ``True``/``False`` override the rule.
    vector_payload_content: bool | None = None

    @classmethod
    def from_env(cls) -> "KnowledgeSettings":
        return cls(
            vector_payload_content=(
                None if not _env("KNOWLEDGE_VECTOR_PAYLOAD_CONTENT", "")
                else _bool("KNOWLEDGE_VECTOR_PAYLOAD_CONTENT", True)
            ),
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
            rerank_model_revision=_env("KNOWLEDGE_RERANK_MODEL_REVISION", "") or "",
            rerank_model=_env("KNOWLEDGE_RERANK_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
            or "cross-encoder/ms-marco-MiniLM-L-6-v2",
            mmr_lambda=_float("KNOWLEDGE_MMR_LAMBDA", 0.7, minimum=0.0, maximum=1.0),
            graph_enabled=_bool("KNOWLEDGE_GRAPH_ENABLED", True),
            graph_llm_extraction=_bool("KNOWLEDGE_GRAPH_LLM_EXTRACTION", False),
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
    #: pass's own `deadline_s` -- this bounds the whole multi-iteration turn. At least the
    #: longest host-routed tool (run_command, 605 s) plus the daemon's approval window (330 s,
    #: tools/registry.py HOST_APPROVAL_WINDOW_S), or an approved command is cut off mid-run
    #: (audit FSA-041).
    engineer_loop_deadline_s: float = 960.0
    #: How many `delegate_to_agent` hops a call tree may contain (`aura_core/tools/delegation.py`).
    #: `1` means a delegating persona's own sub-turn cannot itself delegate further -- the default
    #: ceiling, combined with only `orchestrator` holding the tool, caps recursion at exactly one
    #: hop by construction.
    max_delegation_depth: int = 1
    #: Tool-call deadline for a persona whose grant includes `delegate_to_agent`, in place of the
    #: flat 60s every other tool gets (`AIKernel._tool_context`) -- a delegated sub-turn is a full
    #: recursive `kernel.run()`, not a simple API call, and needs room for its own budgets.
    delegation_deadline_s: float = 180.0

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
                "REASONING_ENGINEER_LOOP_DEADLINE_S", 960.0, minimum=10.0, maximum=3600.0
            ),
            max_delegation_depth=_int(
                "REASONING_MAX_DELEGATION_DEPTH", 1, minimum=0, maximum=4
            ),
            delegation_deadline_s=_float(
                "REASONING_DELEGATION_DEADLINE_S", 180.0, minimum=10.0, maximum=3600.0
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
    #: Bearer token required to read ``metrics_path``. Empty leaves the path open, which is
    #: correct only where something in front restricts it -- nginx answers `deny all; return 404`
    #: for it on the VPS. On a target where the app is the edge (Railway), set this: a live probe
    #: found /metrics world-readable there, publishing which safety bypasses are on and which
    #: dependencies are down.
    metrics_token: str = ""
    #: How long audit events and turn telemetry are kept (audit FSA-016). Both hold personal
    #: data (the audit actor is an email or subject, with a source IP; telemetry names a user's
    #: conversations) and were kept forever. ``0`` keeps rows indefinitely. The daily
    #: ``purge_audit_and_telemetry_task`` applies these; erasure pseudonymises the audit actor
    #: and drops the user's conversations' telemetry regardless of age.
    audit_retention_days: int = 365
    telemetry_retention_days: int = 90

    @classmethod
    def from_env(cls) -> "ObservabilitySettings":
        return cls(
            audit_retention_days=_int("AUDIT_RETENTION_DAYS", 365, minimum=0),
            telemetry_retention_days=_int("TELEMETRY_RETENTION_DAYS", 90, minimum=0),
            log_level=(_env("LOG_LEVEL", "INFO") or "INFO").upper(),
            log_format=(_env("LOG_FORMAT", "json") or "json").lower(),
            metrics_enabled=_bool("METRICS_ENABLED", True),
            metrics_path=_env("METRICS_PATH", "/metrics") or "/metrics",
            tracing_enabled=_bool("TRACING_ENABLED", False),
            otlp_endpoint=_env("OTLP_ENDPOINT", "") or "",
            service_name=_env("SERVICE_NAME", "aura-core") or "aura-core",
            telemetry_persist=_bool("TELEMETRY_PERSIST", True),
            trace_content_sampling=_float("TRACE_CONTENT_SAMPLING", 0.0, minimum=0.0, maximum=1.0),
            metrics_token=(_env("METRICS_TOKEN", "") or "").strip(),
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
    #: SaaS P1-E: the Engineer agent for customers whose workspace plan includes it, on their own
    #: computer through a paired desktop app (ADR-011, "Customers"). Off by default; startup
    #: refuses it beside anything a customer must never reach (``AuraSettings.validate``).
    engineer_for_customers: bool = False
    #: Roles granted the "project_data" capability -- the gate on every agent and tool compiled
    #: from a deployed Project Blueprint (Track AP7): reading the connected organisation's own
    #: system. Empty by default for the same reason as `engineer_agent_roles`: which staff may
    #: query that system through AURA is a per-deployment decision.
    project_data_roles: tuple[str, ...] = ()
    #: Send the response header floor from the application itself. On by default: the nginx
    #: template is not in the path on every deploy target, and a Railway probe found the gateway
    #: serving no HSTS, XCTO, XFO, Referrer-Policy or Permissions-Policy at all. nginx strips the
    #: app's copies with proxy_hide_header so the VPS still sees exactly one of each.
    security_headers_enabled: bool = True
    #: Content-Security-Policy, if the app should send one. Empty by default and left to the
    #: edge: this origin also fronts the operator console and Grafana, whose policies are far
    #: looser than the API's `default-src 'none'`, so one blanket value would either break them
    #: or be meaningless.
    content_security_policy: str = ""
    rate_limit_enabled: bool = True
    rate_limit_per_user_per_minute: int = 60
    rate_limit_per_ip_per_minute: int = 120
    llm_cost_weight: int = 10
    daily_cost_cap_usd: float = 0.0  # 0 disables
    audit_enabled: bool = True
    trusted_proxy_count: int = 1
    model_allowlist: tuple[str, ...] = ()
    #: `Idempotency-Key` (roadmap P1.4): how long a completed key replays its response, and how
    #: long an in-progress claim holds before a retry may assume its holder died and re-run.
    idempotency_ttl_hours: int = 24
    idempotency_lease_s: int = 900

    @classmethod
    def from_env(cls) -> "SecuritySettings":
        return cls(
            require_auth=_bool("REQUIRE_AUTH", True),
            allow_mock_auth=_bool("ALLOW_MOCK_AUTH", False),
            allow_mock_attestation=_bool_aliased("ALLOW_MOCK_ATTESTATION", False),
            admin_roles=_list("ADMIN_ROLES", ("Admin", "SuperAdmin")),
            engineer_agent_roles=_list("ENGINEER_AGENT_ROLES", ()),
            engineer_for_customers=_bool("ENGINEER_FOR_CUSTOMERS", False),
            project_data_roles=_list("PROJECT_DATA_ROLES", ()),
            security_headers_enabled=_bool("SECURITY_HEADERS_ENABLED", True),
            content_security_policy=(_env("CONTENT_SECURITY_POLICY", "") or "").strip(),
            rate_limit_enabled=_bool("RATE_LIMIT_ENABLED", True),
            rate_limit_per_user_per_minute=_int("RATE_LIMIT_USER_PER_MIN", 60, minimum=1),
            rate_limit_per_ip_per_minute=_int("RATE_LIMIT_IP_PER_MIN", 120, minimum=1),
            llm_cost_weight=_int("RATE_LIMIT_LLM_COST", 10, minimum=1),
            daily_cost_cap_usd=_float("DAILY_COST_CAP_USD", 0.0, minimum=0.0),
            audit_enabled=_bool("AUDIT_ENABLED", True),
            trusted_proxy_count=_int("TRUSTED_PROXY_COUNT", 1, minimum=0, maximum=5),
            model_allowlist=_list("MODEL_ALLOWLIST"),
            idempotency_ttl_hours=_int("IDEMPOTENCY_TTL_HOURS", 24, minimum=1, maximum=24 * 30),
            idempotency_lease_s=_int("IDEMPOTENCY_LEASE_S", 900, minimum=30, maximum=86_400),
        )


def _vector_store_is_on_host(url: str) -> bool:
    """Whether a vector-store URL points at this deployment's own network: empty (in-memory), a
    loopback or private address, a bare Docker service name, or a ``.internal``/``.local`` name.
    A dotted public hostname (Qdrant Cloud, any SaaS) is off-host."""
    import ipaddress
    from urllib.parse import urlsplit

    if not url:
        return True
    host = (urlsplit(url if "//" in url else f"//{url}").hostname or "").lower()
    if not host or host in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(host).is_private or ipaddress.ip_address(host).is_loopback
    except ValueError:
        pass
    if "." not in host:
        return True
    return host.endswith((".internal", ".local", ".localdomain"))


def vector_payload_content_enabled(knowledge: "KnowledgeSettings", infrastructure: "InfrastructureSettings") -> bool:
    """The recorded decision per target (audit FSA-018): readable content travels into vector
    payloads only when the vector store is on-host, unless the operator says otherwise."""
    if knowledge.vector_payload_content is not None:
        return bool(knowledge.vector_payload_content)
    if infrastructure.vector_backend != "qdrant":
        return True
    return _vector_store_is_on_host(infrastructure.qdrant_url)


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
    #: Relay desktop host-agent calls between gateway workers over Redis
    #: (``adapters/host_agent_relay.py``). On whenever ``redis_url`` is set; ``False`` is the
    #: kill switch back to single-process behaviour (only the worker holding a daemon's socket
    #: can reach it).
    host_agent_relay: bool = True
    #: The one asyncpg pool every aura_core store in a process shares (``PostgresPool``), and
    #: the legacy SQLAlchemy engine beside it (``infrastructure/db/base.py``). Per *process*:
    #: every uvicorn worker and every Celery prefork child holds its own, so a deployment's
    #: connection budget is ``processes x postgres_connections_per_process`` and must fit under
    #: Postgres's ``max_connections`` with headroom (audit FSA-073;
    #: tests/unit/test_postgres_connection_budget.py computes it for both compose files).
    postgres_pool_min_size: int = 2
    postgres_pool_max_size: int = 10
    #: A statement that runs longer than this is cancelled and its connection returned; without
    #: it a hung query held a pooled connection forever.
    postgres_command_timeout_s: float = 60.0
    #: Idle pooled connections are closed after this long, so a burst does not pin its peak.
    postgres_max_inactive_connection_lifetime_s: float = 300.0
    legacy_db_pool_size: int = 5
    legacy_db_max_overflow: int = 10

    @classmethod
    def from_env(cls) -> "InfrastructureSettings":
        pool_min = _int("POSTGRES_POOL_MIN_SIZE", 2, minimum=0)
        pool_max = _int("POSTGRES_POOL_MAX_SIZE", 10, minimum=1)
        if pool_min > pool_max:
            raise ConfigurationError(
                "AURA_POSTGRES_POOL_MIN_SIZE must not exceed AURA_POSTGRES_POOL_MAX_SIZE",
                details={"min": pool_min, "max": pool_max},
            )
        return cls(
            database_url=_env("DATABASE_URL", "") or "",
            redis_url=_env("REDIS_URL", "") or "",
            qdrant_url=_env("QDRANT_URL", "") or "",
            qdrant_api_key_env=_env("QDRANT_API_KEY_ENV", "AURA_QDRANT_API_KEY") or "AURA_QDRANT_API_KEY",
            vector_backend=(_env("VECTOR_BACKEND", "memory") or "memory").lower(),
            document_backend=(_env("DOCUMENT_BACKEND", "memory") or "memory").lower(),
            memory_backend=(_env("MEMORY_BACKEND", "memory") or "memory").lower(),
            cache_backend=(_env("CACHE_BACKEND", "memory") or "memory").lower(),
            host_agent_relay=_bool("HOST_AGENT_RELAY", True),
            postgres_pool_min_size=pool_min,
            postgres_pool_max_size=pool_max,
            postgres_command_timeout_s=_float("POSTGRES_COMMAND_TIMEOUT_S", 60.0, minimum=1.0),
            postgres_max_inactive_connection_lifetime_s=_float(
                "POSTGRES_MAX_INACTIVE_CONNECTION_LIFETIME_S", 300.0, minimum=0.0
            ),
            legacy_db_pool_size=_int("LEGACY_DB_POOL_SIZE", 5, minimum=0),
            legacy_db_max_overflow=_int("LEGACY_DB_MAX_OVERFLOW", 10, minimum=0),
        )

    @property
    def postgres_connections_per_process(self) -> int:
        """The most Postgres connections one process can hold with every pool full."""
        return self.postgres_pool_max_size + self.legacy_db_pool_size + self.legacy_db_max_overflow

    @property
    def qdrant_api_key(self) -> str:
        return os.environ.get(self.qdrant_api_key_env, "")

    @property
    def host_agent_relay_secret(self) -> str:
        """The key the host-agent relay signs with: the deployment's JWT secret, read at call
        time (it is hydrated from a secret file after settings load, as ``services/jwt_auth``
        notes). Every gateway worker must share it already, or a token one worker minted would
        not verify on another."""
        return os.environ.get("AURA_JWT_SECRET") or os.environ.get("JWT_SECRET") or ""


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
    #: Hugging Face repo ids (prefix match with a trailing ``*``). LocalLoraTrainer loads
    #: transformers checkpoints, so Ollama tags (``name:tag``) can never match a trainable base;
    #: they used to be listed here. 3B-class bases are deliberately absent: fp32 LoRA on a 3B
    #: model does not fit worker-training's 4G memory limit, and CPU speed makes it moot.
    finetune_cpu_base_allowlist: tuple[str, ...] = (
        "HuggingFaceTB/SmolLM2-135M*",
        "Qwen/Qwen2.5-0.5B*",
        "hf-internal-testing/tiny-random-*",
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
                    "HuggingFaceTB/SmolLM2-135M*",
                    "Qwen/Qwen2.5-0.5B*",
                    "hf-internal-testing/tiny-random-*",
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


def _parse_probe_seeds(raw: str) -> Mapping[str, str]:
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigurationError("AURA_ADAPTATION_GATE_PROBE_SEEDS must be valid JSON") from exc
    if not isinstance(parsed, Mapping) or not all(
        isinstance(k, str) and isinstance(v, str) and 0 < len(v) <= 200 for k, v in parsed.items()
    ):
        raise ConfigurationError(
            'AURA_ADAPTATION_GATE_PROBE_SEEDS must be a JSON object of short strings, e.g. {"q": "heptane"}'
        )
    return dict(parsed)


@dataclass(frozen=True, slots=True)
class AdaptationSettings:
    """Track AP: the Adaptive Intelligence Layer (``docs/roadmaps/AURA_ADAPTIVE_PLATFORM_ROADMAP.md``).

    Off by default. With it off the container builds no connector registry and every
    ``/api/v3/admin/connectors`` route answers 503 -- the routes stay mounted so the route-auth
    suite keeps covering them. Turning it on also needs ``AURA_SECRETS_ENCRYPTION_KEY`` before any
    credentialed connector can be registered: the vault never falls back to plaintext.
    """

    enabled: bool = False
    #: The evaluation gate (AP8): ``enforce`` (a version that fails a gating suite stays
    #: ``proposed``) or ``advisory`` (it runs and is recorded on the version and its approval
    #: request, but does not block). AP7's ungated bypass is gone: every evaluation runs the gate.
    gate_mode: str = "enforce"
    #: The share of a gating suite's cases that must actually run (the rest had no reachable
    #: replica or no value to call with) for the suite to count.
    gate_min_coverage: float = 0.8
    #: Values for required parameters a generated tool cannot get from a SQL replica (an HTTP
    #: tool's ``q``): ``{"q": "heptane"}``. The gate calls such a tool with the seed, then reuses
    #: identifiers found in what it returned (``document_id`` ...) to call the tools that need
    #: one. Operator-chosen search terms, not record ids, so they survive a re-ingest.
    gate_probe_seeds: Mapping[str, str] = field(default_factory=dict)
    #: Seconds the previous version's generated tools stay registered after a swap, so a turn
    #: that resolved them just before the swap can still finish.
    tool_grace_s: float = 300.0
    #: How often each gateway worker reconciles with the store's active blueprint pointer.
    sync_s: float = 15.0
    #: The optional labeling model (AP4, ADR-026). Off: twins keep humanised identifiers.
    labeler_enabled: bool = False
    labeler_batch_size: int = 8
    labeler_timeout_s: float = 180.0
    #: Directory under which ``files`` connectors may read CSV/JSON exports (AP10). Each connector's
    #: endpoint is a sub-directory of it; empty disables the ``files`` kind entirely, so no path on
    #: this host is readable through a connector unless an operator chose this root.
    files_root: str = ""
    #: Directory under which ``git`` connectors may read repository checkouts (AP10), on the same
    #: terms: each connector's endpoint is a sub-directory; empty disables the ``git`` kind.
    git_root: str = ""

    @classmethod
    def from_env(cls) -> "AdaptationSettings":
        return cls(
            enabled=_bool("ADAPTATION_ENABLED", False),
            gate_mode=_choice("ADAPTATION_GATE_MODE", "enforce", ("enforce", "advisory")),
            gate_min_coverage=_float("ADAPTATION_GATE_MIN_COVERAGE", 0.8, minimum=0.0, maximum=1.0),
            gate_probe_seeds=_parse_probe_seeds(_env("ADAPTATION_GATE_PROBE_SEEDS", "") or ""),
            tool_grace_s=max(0.0, _float("ADAPTATION_TOOL_GRACE_S", 300.0)),
            sync_s=max(1.0, _float("ADAPTATION_SYNC_S", 15.0)),
            labeler_enabled=_bool("ADAPTATION_LABELER_ENABLED", False),
            labeler_batch_size=max(1, min(64, _int("ADAPTATION_LABELER_BATCH_SIZE", 8))),
            labeler_timeout_s=max(5.0, _float("ADAPTATION_LABELER_TIMEOUT_S", 180.0)),
            files_root=(_env("ADAPTATION_FILES_ROOT", "") or "").strip(),
            git_root=(_env("ADAPTATION_GIT_ROOT", "") or "").strip(),
        )


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
    """Docker sandbox for the engineer agent's ``run_command``/``write_file`` tools.

    ``enabled`` alone is not enough to get a runner: ``Container._build_sandbox_runner`` also
    probes that the ``docker`` package is importable and a daemon is actually reachable, the same
    opt-in-setting-plus-runtime-probe pattern as ``_build_ocr_engine``/``_build_stt_engine``.

    ``host_root`` is the one setting a Dockerised deployment cannot skip, and the reason is not
    obvious: the gateway asks the *host's* Docker daemon to bind-mount a sandbox workspace, and
    the daemon resolves that source path on the **host**, not inside the gateway container. A
    path that exists only in the gateway's own filesystem (what ``tempfile.mkdtemp`` returns)
    therefore names a *different, empty* directory once the daemon creates it, and files written
    through ``write_file`` become invisible to ``run_command``. Setting this to a path that is
    bind-mounted into the gateway at the **same absolute path** (see
    ``deploy/docker-compose.prod.yml``'s ``gateway`` volumes) is what makes the two agree. Left
    empty -- the local/test case, where the runner and the daemon share a filesystem -- scratch
    directories fall back to ``tempfile.mkdtemp``.

    The workspace knobs below bound what persistent workspaces (see
    ``aura_core/ports/coding.py``'s ``WorkspaceStore``) may consume on a single-VPS deployment.
    ``max_running`` is the one that bounds *memory*: a stopped workspace costs nothing but disk,
    so peak RAM is ``max_running * memory_limit`` regardless of how many workspaces exist. On a
    16 GB host sharing the box with the inference tier, ``max_running=1``/``max_workspaces=2``
    is the safe setting; the defaults here assume the documented 24 GB target host.
    """

    enabled: bool = False
    image: str = "aura-sandbox:latest"
    network: str = "sandbox_egress"
    memory_limit: str = "512m"
    cpu_limit: float = 1.0
    timeout_s: float = 120.0
    allowed_egress_hosts: tuple[str, ...] = ()
    host_root: str = ""
    workspace_ttl_hours: float = 72.0
    hot_idle_s: float = 900.0
    max_workspaces: int = 4
    max_running: int = 2
    workspace_disk_quota_mb: int = 2048
    min_free_memory_mb: int = 1024
    in_container_git: bool = False
    proxy_url: str = ""

    def __post_init__(self) -> None:
        # A ceiling on running workspaces above the ceiling on workspaces that may exist is not a
        # harmless contradiction: it reads as "2 may run" while only 1 can ever be provisioned,
        # which hides a mis-set variable until someone wonders why the second one never starts.
        if self.max_running > self.max_workspaces:
            raise ConfigurationError(
                "AURA_SANDBOX_MAX_RUNNING must be <= AURA_SANDBOX_MAX_WORKSPACES",
                details={"max_running": self.max_running, "max_workspaces": self.max_workspaces},
            )

    @classmethod
    def from_env(cls) -> "SandboxSettings":
        return cls(
            enabled=_bool("SANDBOX_ENABLED", False),
            image=_env("SANDBOX_IMAGE", "aura-sandbox:latest") or "aura-sandbox:latest",
            network=_env("SANDBOX_NETWORK", "sandbox_egress") or "sandbox_egress",
            memory_limit=_env("SANDBOX_MEMORY_LIMIT", "512m") or "512m",
            cpu_limit=_float("SANDBOX_CPU_LIMIT", 1.0, minimum=0.1, maximum=8.0),
            timeout_s=_float("SANDBOX_TIMEOUT_S", 120.0, minimum=1.0, maximum=1800.0),
            allowed_egress_hosts=_list("SANDBOX_ALLOWED_EGRESS_HOSTS"),
            host_root=_env("SANDBOX_HOST_ROOT", "") or "",
            workspace_ttl_hours=_float(
                "SANDBOX_WORKSPACE_TTL_HOURS", 72.0, minimum=0.25, maximum=8760.0
            ),
            hot_idle_s=_float("SANDBOX_HOT_IDLE_S", 900.0, minimum=30.0, maximum=86400.0),
            max_workspaces=_int("SANDBOX_MAX_WORKSPACES", 4, minimum=1, maximum=64),
            max_running=_int("SANDBOX_MAX_RUNNING", 2, minimum=1, maximum=64),
            workspace_disk_quota_mb=_int(
                "SANDBOX_WORKSPACE_DISK_QUOTA_MB", 2048, minimum=16, maximum=1_048_576
            ),
            min_free_memory_mb=_int("SANDBOX_MIN_FREE_MEMORY_MB", 1024, minimum=0, maximum=1_048_576),
            in_container_git=_bool("SANDBOX_IN_CONTAINER_GIT", False),
            proxy_url=_env("SANDBOX_PROXY_URL", "") or "",
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
    #: Hub commit of the Whisper weights (audit FSA-110); empty loads the repo's head.
    stt_model_revision: str = ""
    #: Load Whisper at container startup instead of on the first transcription of every fresh
    #: container. ``None`` means "in production-like environments only", which keeps a local or
    #: test container from ever pulling weights.
    stt_warm_on_startup: bool | None = None
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
            stt_model_revision=_env("VOICE_STT_MODEL_REVISION", "") or "",
            stt_warm_on_startup=(
                None if not _env("VOICE_STT_WARM_ON_STARTUP", "") else _bool("VOICE_STT_WARM_ON_STARTUP", True)
            ),
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


@dataclass(frozen=True, slots=True)
class VisionSettings:
    """Image understanding (ADR-031). Off unless an operator turns it on.

    ``enabled`` is necessary, not sufficient: an image turn also needs a provider declaring
    ``AURA_PROVIDER_<NAME>_MODALITIES=text,image`` (or the OCR fallback), or it fails with a 503
    ``dependency_unavailable`` rather than answering as if the image had been seen.

    The byte limits are checked twice, in order: ``max_request_image_bytes`` against the decoded
    total before any image is opened, then ``max_image_bytes`` per image. With the defaults, four
    images in base64 stay under nginx's 20 MB body cap (``deploy/nginx/templates``). Raising
    them past that needs nginx raised too, in both compose stacks.

    ``perception_max_tokens`` bounds the description the vision model writes per turn. At the
    CPU stack's 5-10 tok/s, 400 tokens finish inside ``perception_timeout_s``; see
    ``PlanningEngine.plan()`` for why that sizing matters.
    """

    enabled: bool = False
    max_images: int = 4
    max_image_bytes: int = 10 * 1024 * 1024
    max_request_image_bytes: int = 12 * 1024 * 1024
    max_pixels: int = 50_000_000
    max_edge_px: int = 1536
    perception_max_tokens: int = 400
    perception_timeout_s: float = 120.0
    ocr_fallback: bool = True

    @classmethod
    def from_env(cls) -> "VisionSettings":
        return cls(
            enabled=_bool("VISION_ENABLED", False),
            max_images=_int("VISION_MAX_IMAGES", 4, minimum=1, maximum=8),
            max_image_bytes=_int(
                "VISION_MAX_IMAGE_BYTES", 10 * 1024 * 1024, minimum=64 * 1024, maximum=20 * 1024 * 1024,
            ),
            max_request_image_bytes=_int(
                "VISION_MAX_REQUEST_IMAGE_BYTES", 12 * 1024 * 1024,
                minimum=64 * 1024, maximum=14 * 1024 * 1024,
            ),
            max_pixels=_int("VISION_MAX_PIXELS", 50_000_000, minimum=1_000_000, maximum=200_000_000),
            max_edge_px=_int("VISION_MAX_EDGE_PX", 1536, minimum=256, maximum=4096),
            perception_max_tokens=_int(
                "VISION_PERCEPTION_MAX_TOKENS", 400, minimum=64, maximum=2048,
            ),
            perception_timeout_s=_float(
                "VISION_PERCEPTION_TIMEOUT_S", 120.0, minimum=5.0, maximum=600.0,
            ),
            ocr_fallback=_bool("VISION_OCR_FALLBACK", True),
        )


@dataclass(frozen=True, slots=True)
class TenancySettings:
    """Workspaces (SaaS phase 1, ADR-034).

    ``default_team_seats`` caps a team's members plus open invites until plan entitlements exist
    (SaaS P1 plan C replaces it with the workspace's plan).
    """

    default_team_seats: int = 5
    #: ``AURA_PLAN_CATALOGUE``: JSON replacing the built-in placeholder plans
    #: (``aura_core/domain/plans.py``, SaaS P1-C). Empty keeps the defaults.
    plan_catalogue_json: str = ""

    @classmethod
    def from_env(cls) -> "TenancySettings":
        return cls(
            default_team_seats=_int("TEAM_DEFAULT_SEATS", 5, minimum=2, maximum=1000),
            plan_catalogue_json=(_env("PLAN_CATALOGUE", "") or "").strip(),
        )


FAWRY_SANDBOX_URL = "https://atfawry.fawrystaging.com/ECommerceWeb/Fawry/"


@dataclass(frozen=True, slots=True)
class BillingSettings:
    """Subscriptions paid through Fawry (SaaS P1-D1). Off until the owner turns it on; every
    billing route answers 503 until then.

    The secure key is **not a field**: it is read from the environment variable named by
    ``fawry_secure_key_env`` through a property, like provider API keys, so ``as_dict`` (served on
    ``/admin/status``) can never walk into it."""

    enabled: bool = False
    fawry_base_url: str = FAWRY_SANDBOX_URL
    fawry_merchant_code: str = ""
    fawry_secure_key_env: str = "AURA_FAWRY_SECURE_KEY"
    #: ``AURA_PLAN_PRICES``: JSON plan -> piastres per month (per seat for team).
    prices_json: str = ""
    renewal_lead_days: int = 3
    grace_days: int = 7
    reference_expiry_hours: int = 72
    #: In-app subscriptions (SaaS P1-D2). Each store is off until its fields are set.
    apple_bundle_id: str = ""
    #: ``Production`` (also when empty) or ``Sandbox``. Sandbox purchases are free: testing only.
    apple_environment: str = ""
    google_package_name: str = ""
    #: The audience the Pub/Sub push subscription puts in its OIDC token (the notify URL).
    google_push_audience: str = ""
    google_push_service_account: str = ""
    #: Env var holding the Play service-account JSON. Read through a property, never a field.
    google_play_credentials_env: str = "AURA_GOOGLE_PLAY_CREDENTIALS_JSON"
    #: ``AURA_STORE_PRODUCTS``: ``{"apple": {productId: plan}, "google": {productId: plan}}``.
    store_products_json: str = ""

    @property
    def google_play_credentials(self) -> dict | None:
        raw = (os.environ.get(self.google_play_credentials_env, "") or "").strip()
        if not raw:
            return None
        try:
            value = json.loads(raw)
        except ValueError:
            return None
        return value if isinstance(value, dict) else None

    @property
    def apple_configured(self) -> bool:
        return bool(self.apple_bundle_id)

    @property
    def google_configured(self) -> bool:
        return bool(self.google_package_name and self.google_push_audience and self.google_push_service_account
                    and self.google_play_credentials)

    @property
    def fawry_secure_key(self) -> str:
        return (os.environ.get(self.fawry_secure_key_env, "") or "").strip()

    @property
    def fawry_configured(self) -> bool:
        return bool(self.fawry_merchant_code and self.fawry_secure_key)

    @classmethod
    def from_env(cls) -> "BillingSettings":
        return cls(
            enabled=_bool("BILLING_ENABLED", False),
            fawry_base_url=(_env("FAWRY_BASE_URL", "") or "").strip() or FAWRY_SANDBOX_URL,
            fawry_merchant_code=(_env("FAWRY_MERCHANT_CODE", "") or "").strip(),
            prices_json=(_env("PLAN_PRICES", "") or "").strip(),
            renewal_lead_days=_int("BILLING_RENEWAL_LEAD_DAYS", 3, minimum=1, maximum=14),
            grace_days=_int("BILLING_GRACE_DAYS", 7, minimum=0, maximum=60),
            reference_expiry_hours=_int("FAWRY_REFERENCE_EXPIRY_HOURS", 72, minimum=1, maximum=720),
            apple_bundle_id=(_env("APPLE_BUNDLE_ID", "") or "").strip(),
            apple_environment=(_env("APPLE_ENVIRONMENT", "") or "").strip(),
            google_package_name=(_env("GOOGLE_PACKAGE_NAME", "") or "").strip(),
            google_push_audience=(_env("GOOGLE_PUSH_AUDIENCE", "") or "").strip(),
            google_push_service_account=(_env("GOOGLE_PUSH_SERVICE_ACCOUNT", "") or "").strip(),
            store_products_json=(_env("STORE_PRODUCTS", "") or "").strip(),
        )


@dataclass(frozen=True, slots=True)
class EmailSettings:
    """Customer email over SMTP (SaaS P1-F1): invites, receipts, renewal and past-due notices.
    Off until configured; any transactional provider's SMTP endpoint works. The password is read
    from the env var named by ``smtp_password_env`` through a property, never a field."""

    enabled: bool = False
    #: e.g. ``AURA <billing@aureon-ai.tech>``; the domain needs SPF/DKIM for the provider.
    from_address: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password_env: str = "AURA_SMTP_PASSWORD"
    smtp_starttls: bool = True
    #: Where the customer app is served, for links in email (``/invite?token=...``).
    app_base_url: str = ""

    @property
    def smtp_password(self) -> str:
        return (os.environ.get(self.smtp_password_env, "") or "").strip()

    @classmethod
    def from_env(cls) -> "EmailSettings":
        return cls(
            enabled=_bool("EMAIL_ENABLED", False),
            from_address=(_env("EMAIL_FROM", "") or "").strip(),
            smtp_host=(_env("SMTP_HOST", "") or "").strip(),
            smtp_port=_int("SMTP_PORT", 587, minimum=1, maximum=65535),
            smtp_username=(_env("SMTP_USERNAME", "") or "").strip(),
            smtp_starttls=_bool("SMTP_STARTTLS", True),
            app_base_url=(_env("APP_BASE_URL", "") or "").strip(),
        )


@dataclass(frozen=True, slots=True)
class ReleaseSettings:
    """Which build this process actually is (BLOCK-03).

    Every field is injected at image build time and is immutable thereafter -- the point is to
    make "what revision is live?" answerable from a request rather than inferred from artefacts
    like a stale config key. A production audit dated the deployed build by noticing `/health`
    still advertised a settings field that had been deleted weeks earlier; that guesswork is
    what this replaces.

    Empty strings, not `None`, when nothing was injected: a build that forgot to pass the args
    must still boot (a local `docker build` or a `pip install` checkout has no SHA to give), and
    `validate()` is what refuses the unknown build in production instead. `/health/version` then
    reports `"revision": "unknown"` rather than a plausible-looking lie.

    Nothing secret belongs here. This is served unauthenticated so a deploy's own smoke test can
    assert the SHA it just shipped is the SHA now answering -- see deploy-backend.yml.
    """

    #: Full 40-character git commit SHA of the source the artefact was built from.
    git_sha: str = ""
    #: The ref that built it, e.g. "refs/heads/main". Informational.
    git_ref: str = ""
    #: RFC3339 build timestamp, as produced by the builder, not this process's clock.
    build_time: str = ""
    #: Opaque per-deployment id (CI run id). Distinguishes two deploys of one SHA.
    deployment_id: str = ""
    #: The HTTP surface this build serves. Bumped only by a breaking API change.
    api_version: str = "v3"
    #: Application version, kept in step with build_app's FastAPI `version`.
    app_version: str = "3.0.0"
    #: Contract version the Control Plane requires of an Inference Plane it talks to.
    inference_contract: str = "openai-compatible/v1"

    #: Commit-SHA variables set by a PaaS build, tried in order when no build arg was passed.
    #: These are read from the bare environment, not through the AURA_ prefix, because the
    #: platform sets them and does not know about ours. Railway's git integration is the case
    #: that matters here: it builds the Dockerfile directly, never passes --build-arg, and
    #: exporting RAILWAY_GIT_COMMIT_SHA is the only identity it offers.
    PLATFORM_SHA_VARS: ClassVar[tuple[str, ...]] = (
        "RAILWAY_GIT_COMMIT_SHA",
        "RENDER_GIT_COMMIT",
        "HEROKU_SLUG_COMMIT",
        "FLY_MACHINE_VERSION",
        "VERCEL_GIT_COMMIT_SHA",
        "SOURCE_VERSION",
        "GITHUB_SHA",
    )
    PLATFORM_REF_VARS: ClassVar[tuple[str, ...]] = (
        "RAILWAY_GIT_BRANCH",
        "RENDER_GIT_BRANCH",
        "VERCEL_GIT_COMMIT_REF",
        "GITHUB_REF",
    )
    PLATFORM_DEPLOY_VARS: ClassVar[tuple[str, ...]] = (
        "RAILWAY_DEPLOYMENT_ID",
        "RENDER_SERVICE_ID",
        "HEROKU_RELEASE_VERSION",
        "FLY_ALLOC_ID",
        "GITHUB_RUN_ID",
    )

    @staticmethod
    def _first_set(names: tuple[str, ...]) -> str:
        import os

        for name in names:
            value = (os.environ.get(name) or "").strip()
            if value:
                return value
        return ""

    @classmethod
    def from_env(cls) -> "ReleaseSettings":
        return cls(
            # An explicit build arg wins; a platform variable is the fallback, so a deploy path
            # nobody wired still reports a real revision instead of "unknown".
            git_sha=(_env("GIT_SHA", "") or "").strip() or cls._first_set(cls.PLATFORM_SHA_VARS),
            git_ref=(_env("GIT_REF", "") or "").strip() or cls._first_set(cls.PLATFORM_REF_VARS),
            build_time=(_env("BUILD_TIME", "") or "").strip(),
            deployment_id=(
                (_env("DEPLOYMENT_ID", "") or "").strip()
                or cls._first_set(cls.PLATFORM_DEPLOY_VARS)
            ),
            api_version=_env("API_VERSION", "v3") or "v3",
            app_version=_env("APP_VERSION", "3.0.0") or "3.0.0",
            inference_contract=(
                _env("INFERENCE_CONTRACT", "openai-compatible/v1") or "openai-compatible/v1"
            ),
        )

    @property
    def identified(self) -> bool:
        """True when this build can name its own source commit."""
        return bool(self.git_sha)

    @property
    def revision(self) -> str:
        """Short, human-facing revision, or the honest admission that there isn't one."""
        return self.git_sha[:12] if self.git_sha else "unknown"

    def to_public_dict(self) -> dict[str, str | bool]:
        """The payload `/health/version` serves. Deliberately contains no secret."""
        return {
            "app_version": self.app_version,
            "api_version": self.api_version,
            "revision": self.revision,
            "git_sha": self.git_sha,
            "git_ref": self.git_ref,
            "build_time": self.build_time,
            "deployment_id": self.deployment_id,
            "inference_contract": self.inference_contract,
            "identified": self.identified,
        }


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
    vision: VisionSettings = field(default_factory=VisionSettings)
    mcp: McpSettings = field(default_factory=McpSettings)
    adaptation: AdaptationSettings = field(default_factory=AdaptationSettings)
    github: GitHubSettings = field(default_factory=GitHubSettings)
    sandbox: SandboxSettings = field(default_factory=SandboxSettings)
    release: ReleaseSettings = field(default_factory=ReleaseSettings)
    tenancy: TenancySettings = field(default_factory=TenancySettings)
    billing: BillingSettings = field(default_factory=BillingSettings)
    email: EmailSettings = field(default_factory=EmailSettings)

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
            vision=VisionSettings.from_env(),
            mcp=McpSettings.from_env(),
            adaptation=AdaptationSettings.from_env(),
            github=GitHubSettings.from_env(),
            sandbox=SandboxSettings.from_env(),
            release=ReleaseSettings.from_env(),
            tenancy=TenancySettings.from_env(),
            billing=BillingSettings.from_env(),
            email=EmailSettings.from_env(),
        )

    # -- validation --------------------------------------------------------------------

    def validate(self) -> None:
        """Fail fast on unsafe or incoherent configuration.

        Called once at startup. Every check here corresponds to a real finding in
        ``docs/analysis/06-security-report.md``; a running process should never be in a state
        where one of these is violated.
        """
        problems: list[str] = []

        if self.security.engineer_for_customers:
            # SaaS P1-E: a customer's Engineer runs on their own computer only. Each of these
            # would hand customers something that belongs to the operator or is not built for them.
            if self.sandbox.enabled:
                problems.append("AURA_ENGINEER_FOR_CUSTOMERS needs AURA_SANDBOX_ENABLED=false: the server "
                                "sandbox is not open to customers yet (phase 1b)")
            if self.github.enabled and not self.github.oauth_enabled:
                problems.append("AURA_ENGINEER_FOR_CUSTOMERS needs AURA_GITHUB_OAUTH_ENABLED (or GitHub off): "
                                "AURA_GITHUB_ENABLED alone shares the operator's token with every customer")
            if self.mcp.enabled:
                problems.append("AURA_ENGINEER_FOR_CUSTOMERS needs AURA_MCP_ENABLED=false: MCP servers are "
                                "the operator's and would be reachable by every customer's Engineer")
            if "User" in self.security.engineer_agent_roles:
                problems.append("AURA_ENGINEER_FOR_CUSTOMERS cannot be combined with User in "
                                "AURA_ENGINEER_AGENT_ROLES: that grant would skip every plan check")
            if not self.tools.require_confirmation_for_side_effects:
                problems.append("AURA_ENGINEER_FOR_CUSTOMERS needs AURA_TOOLS_REQUIRE_CONFIRMATION=true: "
                                "per-call confirmation stays mandatory for customers (ADR-006)")

        if self.email.enabled and not (self.email.smtp_host and self.email.from_address and self.email.app_base_url):
            problems.append("AURA_EMAIL_ENABLED needs AURA_SMTP_HOST, AURA_EMAIL_FROM and AURA_APP_BASE_URL")

        if self.billing.apple_environment not in ("", "Production", "Sandbox"):
            problems.append("billing.apple_environment must be Production, Sandbox or empty")
        if self.billing.google_package_name and not self.billing.google_configured:
            # SaaS P1-D2: half a Play configuration would 503 every RTDN push, and Pub/Sub would
            # retry them for a week before dropping them.
            problems.append("billing.google_package_name needs AURA_GOOGLE_PUSH_AUDIENCE, "
                            "AURA_GOOGLE_PUSH_SERVICE_ACCOUNT and the Play credentials JSON")

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
            if self.billing.enabled and not self.billing.fawry_configured:
                # SaaS P1-D1: billing on with no merchant credentials would answer every checkout
                # with the in-memory fake provider -- payments nobody can actually make.
                problems.append("billing.enabled needs AURA_FAWRY_MERCHANT_CODE and AURA_FAWRY_SECURE_KEY")
            if not self.security.rate_limit_enabled:
                problems.append("security.rate_limit_enabled must be true in staging/production")
            if not self.security.audit_enabled:
                problems.append("security.audit_enabled must be true in staging/production")
            # The hash embedder (llm/embeddings.py::HashEmbeddingProvider) is not semantic: two
            # paraphrases with no shared character n-grams score near zero, so retrieval and
            # memory recall "work" while returning the wrong chunks. Its own docstring has
            # always said this check exists; until audit FSA-031 nothing enforced it. It is the
            # default so the no-model test suite runs, which is exactly why production must
            # name a real embedder explicitly (AURA_EMBEDDING_PROVIDER=sentence-transformers,
            # or a configured OpenAI-compatible provider name).
            if self.llm.embedding_provider == "local-embeddings" or (
                self.llm.embedding_model == "hash-embed-v1"
            ):
                problems.append(
                    "llm.embedding_provider 'local-embeddings' / model 'hash-embed-v1' is the "
                    "non-semantic hash embedder and must not run in staging/production: set "
                    "AURA_EMBEDDING_PROVIDER (sentence-transformers or a configured provider) "
                    "and AURA_EMBEDDING_MODEL"
                )
            # BLOCK-03: a production process that cannot name its own source commit makes every
            # later question harder -- "is the fix deployed?", "what is this incident running?",
            # "did the rollback take?".
            #
            # This was briefly a boot failure, and that was wrong. It took down a working
            # deployment: Railway builds this Dockerfile through its own git integration and
            # never passes the build args, so AURA_GIT_SHA was empty, validate() raised, and the
            # container crash-looped until Railway gave up and served 502 to every request.
            #
            # The severity does not justify it. Every other check in this block prevents a
            # security or data-loss outcome that is *worse* than not serving -- mock auth,
            # plaintext secrets, an in-memory system of record. An unidentified build is a
            # diagnosability problem, and serving users from a build you cannot name beats
            # serving nobody. A feature whose whole purpose is to make incidents easier to
            # diagnose must never be able to cause one.
            if not self.release.identified:
                logger.warning(
                    "release identity is not injected: this process cannot report which commit "
                    "it was built from, so /health/version will read 'unknown' and a deploy "
                    "cannot be verified. Pass --build-arg GIT_SHA=$(git rev-parse HEAD), or set "
                    "AURA_GIT_SHA. On Railway/Render/Heroku/Fly the platform's own commit "
                    "variable is picked up automatically -- see ReleaseSettings.from_env.",
                    extra={"environment": str(self.environment)},
                )
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
            # P1.2/P5.1: server-side encryption at rest is fail-closed here. Without a key the
            # stores would silently write memories and personal-OS records in plaintext -- the
            # exact state this control exists to prevent -- so a missing key refuses to start
            # rather than degrading. (Dev/test run without one; see container.build_field_cipher.)
            if not data_encryption_keys():
                problems.append(
                    f"{DATA_ENCRYPTION_KEYS_ENV} must be set in staging/production -- memory "
                    "content and personal-OS record fields are encrypted at rest with it "
                    "(deploy/scripts/bootstrap-secrets.sh generates one)"
                )
            if self.observability.trace_content_sampling > 0.0:
                problems.append(
                    "observability.trace_content_sampling must be 0 in staging/production "
                    "unless explicitly reviewed -- it persists prompt and output text"
                )

        for position, key in enumerate(data_encryption_keys()):
            if not _is_fernet_key(key):
                problems.append(
                    f"{DATA_ENCRYPTION_KEYS_ENV} entry {position} is not a valid Fernet key "
                    "(32 url-safe base64-encoded bytes)"
                )
        if len(set(data_encryption_keys())) != len(data_encryption_keys()):
            problems.append(f"{DATA_ENCRYPTION_KEYS_ENV} lists the same key more than once")

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
