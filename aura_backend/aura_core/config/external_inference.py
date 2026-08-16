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

__all__ = [
    "EXTERNAL_INFERENCE_HOSTS",
    "BANNED_SDK_ROOTS",
    "BANNED_DISTRIBUTIONS",
    "BANNED_DART_PACKAGES",
    "BANNED_CREDENTIAL_NAMES",
    "BANNED_PROVIDER_KINDS",
    "REMOTE_INFERENCE_HOSTS",
    "is_external_inference_host",
    "is_remote_inference_host",
    "credential_name_variants",
]


#: Hostnames and suffixes belonging to hosted inference services. Matched against a URL's host,
#: so a suffix entry such as ``openai.com`` also covers ``api.openai.com``.
#:
#: Deliberately excludes bare vendor words that collide with legitimate local usage: matching
#: "openai" alone would reject the ``openai_compatible`` provider kind, which is precisely how we
#: talk to a *local* vLLM or LocalAI server.
EXTERNAL_INFERENCE_HOSTS: tuple[str, ...] = (
    "api.openai.com",
    "api.anthropic.com",
    "generativelanguage.googleapis.com",
    "aiplatform.googleapis.com",
    "openai.azure.com",
    "bedrock-runtime",
    "api.groq.com",
    "openrouter.ai",
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
BANNED_DISTRIBUTIONS: tuple[str, ...] = (
    "openai",
    "anthropic",
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
