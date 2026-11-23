"""A Cloudflare Workers AI provider, as the console's Cloudflare tab writes it (console plan, B2).

A provider is not a database row: it is a set of ``AURA_PROVIDER_<NAME>_*`` variables on the
gateway plus its name in ``AURA_LLM_PROVIDERS``, and the gateway builds a ``ProviderConfig`` from
them at boot. So this module is three small pure things:

* :class:`CloudflareProviderSpec` -- what a Super Admin may choose, validated on construction. It
  checks *shape* (a name, an ``@cf/...`` model id, sane numbers). It does not decide *policy*
  (whether a model is open-weight, whether hosted inference is consented to): that stays in
  ``ProviderConfig`` and ``config/external_inference.py``, the single source of those rules, and
  ``tests/unit/test_cloudflare_provider_spec.py`` pins that this module's output is accepted or
  refused by that real loader rather than by a copy of its logic.
* :func:`provider_variables` -- the exact variable set for a spec. The key's *value* is never part
  of it: only the name of the variable that holds it.
* :func:`providers_with` / :func:`providers_without` -- edits to the ``AURA_LLM_PROVIDERS`` list.

Stdlib only, like every domain module. No host is named here (ADR-030): the caller passes the API
root, which comes from ``config/external_inference.WORKERS_AI_API_ROOT``.

Two facts about Cloudflare shape the output, both probed on 2026-11-23:

* ``GET .../ai/v1/models`` answers **405**, so the router's default active health probe would mark
  every Cloudflare provider unhealthy. Every provider written here uses the passive probe, which
  reports the circuit breaker and calls nothing.
* The router never sends user images to a third-party provider (``third_party_image``, ADR-033),
  so declaring ``vision`` records what the model can do but does not make AURA send it images.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from .errors import NotFoundError, ValidationError

__all__ = [
    "RESERVED_PROVIDER_NAMES",
    "PROVIDER_REQUIRED_TAILS",
    "PROVIDER_OPTIONAL_TAILS",
    "CloudflareProviderSpec",
    "key_variable",
    "provider_prefix",
    "provider_variables",
    "providers_with",
    "providers_without",
]

# Lower-case, starts with a letter, no trailing hyphen, at most 32 characters. No underscore:
# ProviderConfig turns "-" into "_" when it builds the variable prefix, so "a-b" and "a_b" would
# share one prefix and silently overwrite each other.
_NAME = re.compile(r"[a-z](?:[a-z0-9-]{0,30}[a-z0-9])?")
#: Names the gateway already gives a meaning to. ``local`` is the Ollama fallback in every
#: ``AURA_LLM_PROVIDERS`` list and ``echo`` is the test provider.
RESERVED_PROVIDER_NAMES = frozenset({"local", "echo"})
# Workers AI catalog ids. ``@hf/`` (Hugging Face models Cloudflare used to host) is not offered.
_MODEL = re.compile(r"@cf/[A-Za-z0-9][A-Za-z0-9._/-]{0,150}")
_ACCOUNT = re.compile(r"[0-9a-f]{32}")
_KEY_ENV = re.compile(r"[A-Z][A-Z0-9_]{0,127}")
_API_ROOT = re.compile(r"https://[A-Za-z0-9.-]+(?::\d{1,5})?(?:/[A-Za-z0-9._~/-]*)?")

_MIN_CONTEXT, _MAX_CONTEXT = 512, 4_000_000
_MIN_OUTPUT, _MAX_OUTPUT = 16, 262_144
_MAX_TOKENS_PER_SECOND = 5000.0

#: Variables every provider written here carries, as the part after ``AURA_PROVIDER_<NAME>_``.
PROVIDER_REQUIRED_TAILS: tuple[str, ...] = (
    "KIND", "BASE_URL", "MODEL", "API_KEY_ENV", "QUALITY_TIER", "CONTEXT_WINDOW",
    "MAX_OUTPUT_TOKENS", "ENABLED", "HEALTH_PROBE", "SUPPORTS_JSON_MODE", "MODALITIES",
)
#: Written only when declared. A tail that is no longer declared must be deleted on Replace, so
#: the engine needs to know them: 0 means "undeclared" to the settings loader, never "none".
PROVIDER_OPTIONAL_TAILS: tuple[str, ...] = ("RPM", "RPD", "TPM", "TPD", "TOKENS_PER_SECOND")


def _bad(message: str, **details: Any) -> ValidationError:
    return ValidationError(message, details=details)


def _int(field: str, value: Any, minimum: int, maximum: int) -> int:
    # bool is an int subclass: True would otherwise pass as 1.
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise _bad(f"{field} must be a whole number from {minimum} to {maximum}", field=field)
    return value


def _flag(field: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise _bad(f"{field} must be true or false", field=field)
    return value


@dataclass(frozen=True, slots=True)
class CloudflareProviderSpec:
    """What the Add and Replace forms set. Validated on construction, so a spec that exists is
    one :func:`provider_variables` can turn into variables the settings loader will parse."""

    name: str
    model: str
    context_window: int
    quality_tier: int = 2
    max_output_tokens: int = 2048
    enabled: bool = True
    rpm: int = 0
    rpd: int = 0
    tpm: int = 0
    tpd: int = 0
    tokens_per_second: float = 0.0
    json_mode: bool = False
    vision: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _NAME.fullmatch(self.name) or self.name in RESERVED_PROVIDER_NAMES:
            raise _bad(
                "a provider name is 1 to 32 lower-case letters, digits or '-', starts with a letter, "
                "does not end with '-', and is not 'local' or 'echo'",
                field="name",
            )
        if not isinstance(self.model, str) or not _MODEL.fullmatch(self.model):
            raise _bad("the model must be a Workers AI model id such as @cf/openai/gpt-oss-20b", field="model")
        _int("quality_tier", self.quality_tier, 1, 3)
        _int("context_window", self.context_window, _MIN_CONTEXT, _MAX_CONTEXT)
        _int("max_output_tokens", self.max_output_tokens, _MIN_OUTPUT, _MAX_OUTPUT)
        if self.max_output_tokens > self.context_window:
            raise _bad("max_output_tokens cannot exceed the context window", field="max_output_tokens")
        for field in ("rpm", "rpd", "tpm", "tpd"):
            _int(field, getattr(self, field), 0, 2_000_000_000)
        speed = self.tokens_per_second
        if (
            isinstance(speed, bool)
            or not isinstance(speed, (int, float))
            or not math.isfinite(speed)
            or not 0 <= speed <= _MAX_TOKENS_PER_SECOND
        ):
            raise _bad(f"tokens_per_second must be a number from 0 to {int(_MAX_TOKENS_PER_SECOND)}", field="tokens_per_second")
        object.__setattr__(self, "tokens_per_second", float(speed))
        _flag("enabled", self.enabled)
        _flag("json_mode", self.json_mode)
        _flag("vision", self.vision)


def provider_prefix(name: str) -> str:
    """``AURA_PROVIDER_<NAME>``, exactly as ``ProviderConfig.from_env`` builds it."""
    return "AURA_PROVIDER_" + name.upper().replace("-", "_")


def key_variable(name: str) -> str:
    """The gateway variable that holds this provider's key: the Railway convention used by every
    key-bearing slot (``AURA_PROVIDER_HOSTED1_API_KEY_ENV=AURA_PROVIDER_HOSTED1_API_KEY``)."""
    return f"{provider_prefix(name)}_API_KEY"


def provider_variables(
    spec: CloudflareProviderSpec, *, api_root: str, account_id: str, key_env: str | None = None
) -> dict[str, str]:
    """Every variable ``spec`` needs, by name. Never the key's value, only its variable's name.

    ``api_root`` is ``https://<host>/client/v4`` (``WORKERS_AI_API_ROOT``); ``account_id`` is the
    Cloudflare account the provider's requests are scoped to. ``key_env`` names the variable that
    will hold the key and defaults to :func:`key_variable`.
    """
    if not isinstance(api_root, str) or not _API_ROOT.fullmatch(api_root.rstrip("/")):
        raise _bad("the Cloudflare API root must be a plain https URL", field="api_root")
    if not isinstance(account_id, str) or not _ACCOUNT.fullmatch(account_id):
        raise _bad("a Cloudflare account id is 32 lower-case hexadecimal characters", field="account_id")
    if key_env is None:
        key_env = key_variable(spec.name)
    elif not isinstance(key_env, str) or not _KEY_ENV.fullmatch(key_env):
        raise _bad("the key variable name must be upper-case letters, digits and '_'", field="key_env")

    p = provider_prefix(spec.name)
    env: dict[str, str] = {
        f"{p}_KIND": "openai_compatible",
        f"{p}_BASE_URL": f"{api_root.rstrip('/')}/accounts/{account_id}/ai/v1",
        f"{p}_MODEL": spec.model,
        f"{p}_API_KEY_ENV": key_env,
        f"{p}_QUALITY_TIER": str(spec.quality_tier),
        f"{p}_CONTEXT_WINDOW": str(spec.context_window),
        f"{p}_MAX_OUTPUT_TOKENS": str(spec.max_output_tokens),
        f"{p}_ENABLED": "true" if spec.enabled else "false",
        # GET .../ai/v1/models answers 405: an active probe would mark the provider unhealthy.
        f"{p}_HEALTH_PROBE": "passive",
        f"{p}_SUPPORTS_JSON_MODE": "true" if spec.json_mode else "false",
        f"{p}_MODALITIES": "text,image" if spec.vision else "text",
    }
    for tail in ("RPM", "RPD", "TPM", "TPD"):
        value = getattr(spec, tail.lower())
        if value:
            env[f"{p}_{tail}"] = str(value)
    if spec.tokens_per_second:
        env[f"{p}_TOKENS_PER_SECOND"] = str(spec.tokens_per_second)
    return env


def _names(current: str | None) -> list[str]:
    return [part.strip() for part in (current or "").split(",") if part.strip()]


def providers_with(current: str | None, name: str) -> str:
    """``AURA_LLM_PROVIDERS`` with ``name`` added: before ``local`` when it is listed (the local
    model stays the last resort, as in every example), else at the end. A duplicate is refused."""
    names = _names(current)
    if name in names:
        raise _bad(f"{name} is already in the provider list", field="name")
    if "local" in names:
        names.insert(names.index("local"), name)
    else:
        names.append(name)
    return ",".join(names)


def providers_without(current: str | None, name: str) -> str:
    """``AURA_LLM_PROVIDERS`` with ``name`` removed. Not listed is not found; removing the only
    provider is refused, because the gateway would have nothing to serve from."""
    names = _names(current)
    if name not in names:
        raise NotFoundError(f"{name} is not in the provider list")
    names.remove(name)
    if not names:
        raise _bad("this is the only provider; removing it would leave the gateway nothing to serve from", field="name")
    return ",".join(names)
