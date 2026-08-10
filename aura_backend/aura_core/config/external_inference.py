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
    "is_external_inference_host",
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
