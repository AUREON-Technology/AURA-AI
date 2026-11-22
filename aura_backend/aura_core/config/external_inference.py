"""The single registry of what "external inference" means.

Three consumers read these lists and none of them owns a copy:

* :class:`aura_core.config.settings.ProviderConfig` refuses a ``base_url`` on a banned host.
* ``tests/architecture/test_no_external_inference.py`` scans the whole backend for the SDK
  imports, the endpoints, and the declared dependencies.
* Documentation quotes them rather than restating them.

Keeping one copy is the point. When the runtime check and the static scan each kept their own
list, the weaker of the two silently became the real policy -- so this module exists to make that
impossible rather than merely discouraged.

Nothing here is a security boundary on its own. An operator can still stand up a proxy on a
permitted host and forward to a vendor; no in-process check can detect that. These lists close
the accidental path -- a hurried fallback, a copied snippet, a reinstalled SDK. The enforced
boundary is the deployment's egress policy.
"""

from __future__ import annotations

import re

__all__ = [
    "EXTERNAL_INFERENCE_HOSTS",
    "BANNED_SDK_ROOTS",
    "BANNED_DISTRIBUTIONS",
    "BANNED_DART_PACKAGES",
    "BANNED_CREDENTIAL_NAMES",
    "BANNED_PROVIDER_KINDS",
    "REMOTE_INFERENCE_HOSTS",
    "HOSTED_OPEN_WEIGHT_HOSTS",
    "WORKERS_AI_API_ROOT",
    "OPEN_WEIGHT_MODEL_PATTERNS",
    "is_external_inference_host",
    "is_remote_inference_host",
    "is_hosted_open_weight_host",
    "is_open_weight_model",
    "credential_name_variants",
    "SANCTIONED_SDK_IMPORTERS",
    "SANCTIONED_DISTRIBUTIONS",
    "CLAUDE_PROVIDER_KIND",
    "is_claude_model",
]


#: Hostnames and suffixes belonging to hosted inference services. Matched against a URL's host,
#: so a suffix entry such as ``openai.com`` also covers ``api.openai.com``.
#:
#: Deliberately excludes bare vendor words that collide with legitimate local usage: matching
#: "openai" alone would reject the ``openai_compatible`` provider kind, which is precisely how we
#: talk to a *local* vLLM or LocalAI server.
#:
#: ``api.groq.com`` and ``openrouter.ai`` used to be here. ADR-030 moved them to
#: :data:`HOSTED_OPEN_WEIGHT_HOSTS`, which is gated rather than banned. Every host still on this
#: list is refused with no override. ``gateway.ai.cloudflare.com`` was added at the same time:
#: Cloudflare's AI Gateway forwards to OpenAI, Anthropic and other closed vendors under one URL
#: shape, so allowing Workers AI without banning the gateway would have opened a proxy route.
EXTERNAL_INFERENCE_HOSTS: tuple[str, ...] = (
    "api.openai.com",
    "api.anthropic.com",
    "generativelanguage.googleapis.com",
    "aiplatform.googleapis.com",
    "openai.azure.com",
    "bedrock-runtime",
    "gateway.ai.cloudflare.com",
    "api.together.xyz",
    "api.together.ai",
    "api.replicate.com",
    "api-inference.huggingface.co",
    "endpoints.huggingface.cloud",
    "api.cohere.ai",
    "api.cohere.com",
    "api.mistral.ai",
    "api.perplexity.ai",
    "api.deepseek.com",
    "api.fireworks.ai",
    "api.deepinfra.com",
    "api.anyscale.com",
)


#: Dotted import roots for hosted-inference client libraries. An import of one of these, at any
#: nesting depth, fails the build.
#:
#: ``boto3`` is listed because in this tree its only plausible use is ``bedrock-runtime``; nothing
#: else here touches AWS. Should that change, narrow the entry rather than deleting it.
BANNED_SDK_ROOTS: frozenset[str] = frozenset({
    "openai",
    "anthropic",
    "google.generativeai",
    "google.genai",
    "cohere",
    "replicate",
    "groq",
    "together",
    "mistralai",
    "boto3",
    "litellm",
    "langchain_openai",
    "langchain_anthropic",
    "langchain_google_genai",
    "vertexai",
})


#: Distribution names that must not appear in any requirements file. An unused dependency still
#: ships in the image and still reads as permission.
#:
#: ``anthropic`` is absent on purpose: ADR-033 sanctions it as the one closed-model SDK, for the
#: one module that may import it. See :data:`SANCTIONED_DISTRIBUTIONS`.
BANNED_DISTRIBUTIONS: tuple[str, ...] = (
    "openai",
    "google-genai",
    "google-generativeai",
    "cohere",
    "replicate",
    "groq",
    "litellm",
    "mistralai",
    "langchain-openai",
    "langchain-anthropic",
    "langchain-google-genai",
)


#: Dart/Flutter packages that perform or broker inference. The client is a client: it must hold
#: no key, no model and no inference path (Rules 8-16, 30).
#:
#: This list exists because deleting ``google_generative_ai`` and ``flutter_llama`` from
#: ``pubspec.yaml`` was, until now, enforced by nothing at all. The Python side had three static
#: layers; the Dart side had none, so re-adding either package would have been caught only by
#: someone remembering. ``flutter_llama`` is on-device inference rather than a hosted API, but it
#: violates the same rule from the other direction and belongs in the same gate.
BANNED_DART_PACKAGES: tuple[str, ...] = (
    "google_generative_ai",
    "googleai_dart",
    "firebase_vertexai",
    "firebase_ai",
    "openai_dart",
    "dart_openai",
    "anthropic_dart",
    "langchain_openai",
    "flutter_llama",
    "llama_cpp_dart",
    "fllama",
    "flutter_gemma",
    "tflite_flutter",
    "onnxruntime",
)


#: Environment-variable and secret names whose only purpose is to hold a hosted-inference
#: credential. Scanned across deployment manifests -- YAML, HCL, shell, env templates -- which no
#: other layer reads.
#:
#: This list is the direct product of a miss. ``GEMINI_API_KEY`` survived in
#: ``infra/terraform``, ``.github/workflows/deploy-backend.yml`` and ``deploy/.env.example``
#: through every one of the other checks, because none of them opens a file that is not Python or
#: a requirements list. Provisioning a credential is not calling an API, but it is the last step
#: before one, and it reads to the next engineer as permission.
#:
#: Deliberately excludes ambiguous names. ``GOOGLE_API_KEY`` is not here: this product legitimately
#: uses Google Drive and Calendar, so that name has an innocent meaning and banning it would
#: produce a failure an engineer learns to suppress. ``HF_TOKEN`` is excluded for the same reason --
#: it also authenticates model *weight* downloads, which are permitted and separately gated
#: (SEC-01). A list that cries wolf stops being read.
BANNED_CREDENTIAL_NAMES: tuple[str, ...] = (
    "GEMINI_API_KEY",
    "GOOGLE_GENAI_API_KEY",
    "OPENAI_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    # The Anthropic SDK reads both of these ambiently. ADR-033's provider passes its key
    # explicitly, from the per-slot AURA_PROVIDER_<NAME>_API_KEY_ENV variable, so the only
    # sanctioned route for a Claude key is the gated one; provisioning either ambient name would
    # bypass it.
    "ANTHROPIC_AUTH_TOKEN",
    "GROQ_API_KEY",
    "OPENROUTER_API_KEY",
    "TOGETHER_API_KEY",
    "REPLICATE_API_TOKEN",
    "COHERE_API_KEY",
    "MISTRAL_API_KEY",
    "PERPLEXITY_API_KEY",
    "DEEPSEEK_API_KEY",
    "FIREWORKS_API_KEY",
    "HUGGINGFACEHUB_API_TOKEN",
)


#: Provider ``kind`` values that no longer exist. ``ProviderConfig._KINDS`` rejects these at
#: runtime, but configuration *templates* are not parsed by anything, so a template could -- and
#: did -- instruct an operator to set one.
#:
#: Note what is absent: ``openai_compatible``, which is how we reach a *local* vLLM or LocalAI.
#: Any matcher over this list must therefore respect value boundaries, or it will reject the one
#: kind the platform depends on.
BANNED_PROVIDER_KINDS: tuple[str, ...] = (
    "gemini",
    "anthropic",
    "openai",
    "azure",
    "azure_openai",
    "bedrock",
    "vertex",
    "vertexai",
    "groq",
    "openrouter",
    "together",
    "replicate",
    "cohere",
    "mistral",
    "huggingface",
)


#: Hosts that run *your* container and *your* weights, on infrastructure you do not own.
#:
#: These are a genuinely different category from :data:`EXTERNAL_INFERENCE_HOSTS`. Calling
#: ``api.openai.com`` sends your prompt to somebody else's model; deploying a llama.cpp server to a
#: Hugging Face Space sends it to your own model running on rented compute. The first is what the
#: platform forbids outright. The second is a deployment topology with real costs -- the system
#: stops working without internet, and prompts plus retrieved document content leave infrastructure
#: you control -- that an operator may nonetheless choose knowingly.
#:
#: So these are not banned. They are *gated*: :meth:`ProviderConfig._reject_external_base_url`
#: refuses them unless ``AURA_ALLOW_REMOTE_INFERENCE=true`` is set, which makes the choice a
#: deliberate, greppable act rather than something that happens because a URL was pasted into an
#: env file.
#:
#: This list exists because the alternative was worse. ``*.hf.space`` was not on the banned list --
#: only ``api-inference.huggingface.co`` and ``endpoints.huggingface.cloud`` were -- so a Space URL
#: would have sailed through all six static layers and the runtime check without anyone noticing.
#: Quietly using that gap would have made the platform's central claim false while every test still
#: reported it true.
REMOTE_INFERENCE_HOSTS: tuple[str, ...] = (
    "hf.space",
    "run.app",           # Cloud Run
    "azurecontainerapps.io",
    "onrender.com",
    "railway.app",
    "fly.dev",
    "modal.run",
)


def is_remote_inference_host(host: str) -> str | None:
    """Return the matching gated entry for ``host``, or ``None``.

    Suffix match, so ``anas-aura-inference.hf.space`` matches ``hf.space``. Deliberately not a
    substring match: ``hf.space`` appearing inside a longer unrelated label should not trip this,
    and a substring rule here would fire on hostnames that merely contain the string.
    """
    if not host:
        return None
    host = host.lower().rstrip(".")
    for gated in REMOTE_INFERENCE_HOSTS:
        if host == gated or host.endswith("." + gated):
            return gated
    return None


#: Hosted APIs that serve **open-weight** models on someone else's servers (ADR-030).
#:
#: This is a third category, separate from the other two:
#:
#: * :data:`EXTERNAL_INFERENCE_HOSTS` run somebody else's model. Banned, with no override.
#: * :data:`REMOTE_INFERENCE_HOSTS` run *your* weights in *your* container on rented hardware.
#:   Gated by ``AURA_ALLOW_REMOTE_INFERENCE``.
#: * These run published open weights that you could self-host, but on the provider's servers.
#:   The provider sees every prompt. Gated by ``AURA_ALLOW_HOSTED_INFERENCE``, a separate flag:
#:   agreeing that rented hardware may run your model does not agree that a third party may read
#:   your prompts.
#:
#: Each host was admitted on its own terms, recorded here so the reason survives. A host belongs
#: here only if its free or default tier does **not** train on API inputs:
#:
#: * ``api.groq.com``: states it does not train on customer data. I/O logs kept up to 30 days
#:   for abuse and reliability; operators can opt out in Data Controls.
#: * ``api.cloudflare.com``: Workers AI's OpenAI-compatible path,
#:   ``/client/v4/accounts/<id>/ai/v1``. Third-party closed models reachable on the same path
#:   (``openai/gpt-4.1``) fail :func:`is_open_weight_model`. That check, not a path match, is
#:   what keeps the shared host safe.
#: * ``openrouter.ai``: a mixed catalog; closed models are refused by :func:`is_open_weight_model`.
#:   Some free endpoints are served only to accounts that allow prompt training. The operator
#:   keeps that setting **off**, and such endpoints then refuse the request upstream.
#: * ``router.huggingface.co``: Hugging Face Inference Providers, also a mixed catalog.
#: * ``integrate.api.nvidia.com``: the NVIDIA API catalog. Trial terms; evaluation use.
#:
#: Deliberately absent, so adding them takes a new decision rather than an edit: Mistral's free
#: plan and Google AI Studio's free tier both train on inputs. Cerebras and GitHub Models are no
#: longer free, and the remaining hosts are already on the banned list.
HOSTED_OPEN_WEIGHT_HOSTS: tuple[str, ...] = (
    "api.groq.com",
    "api.cloudflare.com",
    "openrouter.ai",
    "router.huggingface.co",
    "integrate.api.nvidia.com",
)

#: The Cloudflare API root the control center's Cloudflare tab talks to (console plan, stage 6): the
#: token API, the Workers AI model catalog and a new provider's ``base_url`` are all under it. It
#: lives here, in the one file allowed to name a hosted host, so that the *first* Workers AI
#: provider can be added from the console. Before this, the root was derived from an
#: already-configured provider, so nothing could connect while none existed. Its host must stay on
#: :data:`HOSTED_OPEN_WEIGHT_HOSTS` (pinned by ``tests/unit/test_cloudflare_provider_spec.py``).
WORKERS_AI_API_ROOT = "https://api.cloudflare.com/client/v4"


#: Model families whose weights are published, matched against a normalised model ID (see
#: :func:`is_open_weight_model`). Every hosted open-weight provider must name a model matching
#: one of these, or boot fails.
#:
#: Each pattern is anchored and ties the family to its publisher, which is how the same key is
#: kept from reaching a closed model. ``openai/gpt-oss-120b`` matches but ``openai/gpt-5`` does
#: not. ``google/gemma-4-31b-it`` matches but ``google/gemini-2.5-flash`` does not. An unknown
#: publisher matches nothing (``perplexity/llama-3.1-sonar`` is a closed online service built on
#: Llama). The publisher segment is optional only because some hosts, such as Groq's older IDs,
#: omit it.
#:
#: A family match is not enough on its own, because some publishers sell closed API tiers under
#: an open family's name. Qwen's ``qwen-max``, ``qwen-plus``, ``qwen-turbo`` and ``qwen3-max`` have
#: never had weights released. :data:`_CLOSED_TIER_RE` refuses any ID carrying one of those tier
#: words. It errs toward refusing: an open model that happened to use one would need an explicit
#: decision to admit.
OPEN_WEIGHT_MODEL_PATTERNS: tuple[str, ...] = (
    r"(openai/)?gpt-oss-",
    r"(google/)?gemma-",
    r"(meta-llama/|meta/)?(meta-)?llama-",
    r"(qwen/)?(qwen|qwq)",
    r"(deepseek-ai/|deepseek/)?deepseek-",
    r"nvidia/[\w.-]*nemotron",
    r"(mistralai/|mistral/)?(mistral-small|mistral-nemo|mistral-7b|mixtral|ministral)",
    r"(microsoft/)?phi-",
    r"(moonshotai/)?kimi-k2",
    r"(zai-org/|z-ai/|thudm/)?glm-",
)

_OPEN_WEIGHT_RE = re.compile("|".join(f"(?:{p})" for p in OPEN_WEIGHT_MODEL_PATTERNS))
_CLOSED_TIER_RE = re.compile(r"(?:^|[-/._])(?:max|plus|turbo)(?:[-._]|$)")


def is_open_weight_model(model: str) -> bool:
    """True when ``model`` names a model from a family with published weights.

    Normalises the spellings hosts use for the same model before matching:

    * case: ``Qwen/Qwen3-32B`` on Hugging Face is ``qwen/qwen3-32b`` on Groq;
    * Cloudflare's ``@cf/`` and ``@hf/`` namespace prefixes;
    * a ``:variant`` suffix, such as OpenRouter's ``:free`` or Hugging Face's ``:<provider>``.
    """
    normalized = (model or "").strip().lower()
    for prefix in ("@cf/", "@hf/"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
    normalized = normalized.split(":", 1)[0]
    if not normalized or _CLOSED_TIER_RE.search(normalized):
        return False
    return _OPEN_WEIGHT_RE.match(normalized) is not None


def is_hosted_open_weight_host(host: str) -> str | None:
    """Return the matching entry of :data:`HOSTED_OPEN_WEIGHT_HOSTS`, or ``None``.

    A suffix match, the same rule as :func:`is_remote_inference_host`, so that
    ``api.groq.com.internal.mycorp.net``, a host you own, does not match.
    """
    if not host:
        return None
    host = host.lower().rstrip(".")
    for hosted in HOSTED_OPEN_WEIGHT_HOSTS:
        if host == hosted or host.endswith("." + hosted):
            return hosted
    return None


def credential_name_variants(name: str) -> tuple[str, ...]:
    """Spellings of a credential name that mean the same thing to a scanner.

    An env var is ``GEMINI_API_KEY``; the Secret Manager resource holding it is
    ``aura-gemini-api-key``; a settings field is ``gemini_api_key``. All three are the same
    provisioning act, and a scan that only knows the first misses the other two -- which is
    exactly what happened.
    """
    lower = name.lower()
    return (lower, lower.replace("_", "-"), lower.replace("_", ""))


def is_external_inference_host(host: str) -> str | None:
    """Return the matching banned entry for ``host``, or ``None``.

    Returns the entry rather than a bool so callers can name it in the error -- an operator
    reading a boot failure needs to know *which* host was rejected, not just that one was.
    """
    if not host:
        return None
    host = host.lower().rstrip(".")
    for banned in EXTERNAL_INFERENCE_HOSTS:
        if host == banned or host.endswith("." + banned) or banned in host:
            return banned
    return None


# --------------------------------------------------------------------------------------
# ADR-033: the one sanctioned closed-model provider
# --------------------------------------------------------------------------------------
#
# Everything above stays banned. ADR-033 makes a single, named exception -- Anthropic's Claude,
# reached through Anthropic's official SDK -- and keeps it as narrow as the static scans can make
# it. Each of the three constants below is one hole in one layer, and each is pinned by a test in
# ``tests/architecture/test_no_external_inference.py`` so that widening it is a visible diff:
#
# * the SDK root ``anthropic`` stays in :data:`BANNED_SDK_ROOTS`; exactly one module may import it;
# * the distribution ``anthropic`` may be declared, nothing else that ``BANNED_DISTRIBUTIONS``
#   used to cover (``langchain-anthropic`` stays banned);
# * ``api.anthropic.com`` stays in :data:`EXTERNAL_INFERENCE_HOSTS`: no ``base_url`` may name it,
#   for any kind. The ``claude`` kind takes no ``base_url`` at all and uses the SDK's own endpoint.
#
# The runtime half lives in ``ProviderConfig._gate_claude``: refused unless
# ``AURA_ALLOW_CLAUDE_INFERENCE=true``, never local, always third-party (so it gets ADR-030's
# ``internal`` data ceiling, image refusal and audit record), and only for a ``claude-*`` model.

#: The provider ``kind`` ADR-033 adds. Deliberately not ``anthropic``, which stays in
#: :data:`BANNED_PROVIDER_KINDS` as the name of the adapter that was removed.
CLAUDE_PROVIDER_KIND = "claude"

#: SDK root -> the only backend modules (paths relative to ``aura_backend/``) allowed to import it.
SANCTIONED_SDK_IMPORTERS: dict[str, frozenset[str]] = {
    "anthropic": frozenset({"aura_core/llm/providers/claude.py"}),
}

#: Distributions that may be declared even though their SDK root is banned everywhere else.
SANCTIONED_DISTRIBUTIONS: frozenset[str] = frozenset({"anthropic"})

#: Claude model IDs as Anthropic's first-party API names them (``claude-opus-5-5``,
#: ``claude-sonnet-5-5``). Anchored, lower-case, no publisher prefix and no ``:variant``.
_CLAUDE_MODEL_RE = re.compile(r"^claude-[a-z0-9][a-z0-9.-]*$")


def is_claude_model(model: str) -> bool:
    """True when ``model`` is a first-party Claude model ID."""
    return _CLAUDE_MODEL_RE.match((model or "").strip()) is not None
