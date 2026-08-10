"""Fitness functions for the self-hosting rule.

The mission's non-negotiable constraint is that **no inference leaves our infrastructure**.
Deleting the Gemini and Anthropic adapters satisfied that once; these tests are what keep it
satisfied. A rule enforced by a deletion lasts until the next person needs a quick fallback.

Three independent layers, because no single one is sufficient:

1. **No hosted-inference SDK is importable anywhere in the backend.** Catches
   ``from google import genai`` reappearing in a service. Walks the full AST, so a lazy import
   inside a function -- the exact shape a "temporary fallback" takes -- is caught too.
2. **No hosted-inference endpoint appears in any source file.** Catches an SDK-free
   ``httpx.post("https://api.openai.com/...")``, which layer 1 would miss entirely.
3. **No hosted-inference package is declared as a dependency.** Catches the SDK being
   reinstalled before any code uses it.

Plus the runtime half: ``ProviderConfig`` refuses those hostnames at construction, tested here
so the config surface and the static scan stay in step.

Every list comes from :mod:`aura_core.config.external_inference`. The tests do not keep their own
copy -- when they did, the runtime check and the static scan drifted and the weaker of the two
became the real policy.

What these tests deliberately do **not** claim: they cannot stop an operator pointing ``base_url``
at a self-run proxy that forwards to a vendor. Nothing inside the process can -- that is what the
deployment's egress policy is for. These close the accidental path, not the determined one.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from aura_core.config.external_inference import (
    BANNED_DISTRIBUTIONS,
    BANNED_SDK_ROOTS,
    EXTERNAL_INFERENCE_HOSTS,
)

BACKEND = Path(__file__).resolve().parents[2]

#: Files that necessarily name the banned things. Both are the policy's own home: the registry
#: declares the lists, and this file imports and reports them. Everything else is scanned.
#:
#: Kept to exactly two paths on purpose -- each entry is a hole in the scan, so an exemption for
#: convenience would defeat the test it belongs to.
SELF_EXEMPT = frozenset({
    "aura_core/config/external_inference.py",
    "tests/architecture/test_no_external_inference.py",
})


def _sources() -> list[Path]:
    """Every Python source file in the backend, excluding caches and virtualenvs."""
    skip = {"__pycache__", "venv", ".venv", "node_modules", "build", "dist", ".git"}
    return sorted(p for p in BACKEND.rglob("*.py") if not skip & set(p.parts))


def _rel(path: Path) -> str:
    return path.relative_to(BACKEND).as_posix()


def _dotted_imports(tree: ast.AST) -> set[str]:
    """Every dotted import name in a module, at any nesting depth."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import; cannot reach a third-party package
                continue
            if node.module:
                names.add(node.module)
                # `from google import genai` -> module="google", names=["genai"]
                names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def _banned_root(dotted: str) -> str | None:
    for banned in BANNED_SDK_ROOTS:
        if dotted == banned or dotted.startswith(banned + "."):
            return banned
    return None


class TestNoHostedInferenceSDKs:
    def test_no_module_imports_a_hosted_inference_sdk(self) -> None:
        offenders: list[str] = []
        for path in _sources():
            rel = _rel(path)
            if rel in SELF_EXEMPT:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), rel)
            except SyntaxError as exc:  # pragma: no cover - a syntax error is its own failure
                pytest.fail(f"{rel} does not parse: {exc}")
            for dotted in sorted(_dotted_imports(tree)):
                banned = _banned_root(dotted)
                if banned:
                    offenders.append(f"{rel}: imports {dotted!r} (banned root {banned!r})")
        assert not offenders, (
            "Hosted inference SDKs must not be importable. All inference runs on our own "
            "infrastructure; use the `ollama` or `openai_compatible` provider kinds against a "
            "local runtime.\n  " + "\n  ".join(offenders)
        )


class TestNoHostedInferenceEndpoints:
    def test_no_source_file_references_a_hosted_inference_endpoint(self) -> None:
        offenders: list[str] = []
        for path in _sources():
            rel = _rel(path)
            if rel in SELF_EXEMPT:
                continue
            text = path.read_text(encoding="utf-8").lower()
            for host in EXTERNAL_INFERENCE_HOSTS:
                if host in text:
                    offenders.append(f"{rel}: references {host!r}")
        assert not offenders, (
            "A hosted inference endpoint appears in source. Removing the vendor SDKs does not "
            "help if the same API is reached with a raw HTTP call.\n  " + "\n  ".join(offenders)
        )


class TestNoHostedInferenceDependencies:
    def test_requirements_declare_no_hosted_inference_package(self) -> None:
        offenders: list[str] = []
        for name in ("requirements.txt", "requirements-dev.txt"):
            req = BACKEND / name
            if not req.exists():
                continue
            for lineno, raw in enumerate(req.read_text(encoding="utf-8").splitlines(), 1):
                line = raw.split("#", 1)[0].strip().lower()
                if not line:
                    continue
                # `openai[datalib]>=1.0` -> `openai`
                dist = line.split("[")[0]
                for sep in ("==", ">=", "<=", "~=", "!=", ">", "<", "="):
                    dist = dist.split(sep)[0]
                if dist.strip() in BANNED_DISTRIBUTIONS:
                    offenders.append(f"{name}:{lineno}: declares {dist.strip()!r}")
        assert not offenders, (
            "A hosted inference package is declared as a dependency. Even unused, it ships in "
            "the image and it reads as permission.\n  " + "\n  ".join(offenders)
        )


class TestProviderConfigRefusesHostedEndpoints:
    """The runtime half of the rule, sharing the registry with the scans above."""

    @pytest.mark.parametrize(
        "base_url",
        [
            "https://api.openai.com/v1",
            "https://api.anthropic.com/v1",
            "https://generativelanguage.googleapis.com/v1beta",
            "https://my-resource.openai.azure.com/openai/deployments/x",
            "https://api.groq.com/openai/v1",
            "https://openrouter.ai/api/v1",
            "https://api.together.xyz/v1",
            "https://api-inference.huggingface.co/models/x",
            "https://api.mistral.ai/v1",
            "https://bedrock-runtime.us-east-1.amazonaws.com",
        ],
    )
    def test_a_hosted_base_url_is_refused_at_construction(self, base_url: str) -> None:
        from aura_core.config.settings import ProviderConfig
        from aura_core.domain.errors import ConfigurationError

        with pytest.raises(ConfigurationError) as excinfo:
            ProviderConfig(name="sneaky", kind="openai_compatible", model="m", base_url=base_url)
        # The message must name the host: an operator reading a boot failure needs to know which
        # setting to change, not merely that configuration is invalid.
        assert "own infrastructure" in str(excinfo.value)

    @pytest.mark.parametrize(
        "base_url",
        [
            "http://localhost:11434/v1",
            "http://127.0.0.1:8000/v1",
            "http://ollama:11434/v1",  # docker network alias, as the prod compose uses
            "http://vllm.internal:8000/v1",
            "http://10.0.0.7:8080/v1",
            "https://llm.aura.internal/v1",
        ],
    )
    def test_a_local_base_url_is_accepted(self, base_url: str) -> None:
        """The guard must not be so broad that it blocks real self-hosted deployments."""
        from aura_core.config.settings import ProviderConfig

        config = ProviderConfig(
            name="local", kind="openai_compatible", model="qwen2.5:7b", base_url=base_url
        )
        assert config.base_url == base_url

    def test_the_removed_vendor_kinds_are_no_longer_accepted(self) -> None:
        from aura_core.config.settings import ProviderConfig
        from aura_core.domain.errors import ConfigurationError

        for kind in ("gemini", "anthropic", "openai", "bedrock"):
            with pytest.raises(ConfigurationError) as excinfo:
                ProviderConfig(name="p", kind=kind, model="m")
            assert "unknown kind" in str(excinfo.value)

    def test_the_supported_kinds_are_exactly_the_self_hosted_ones(self) -> None:
        """Pins the set, so adding a vendor kind cannot pass unnoticed."""
        from aura_core.config.settings import ProviderConfig

        assert set(ProviderConfig._KINDS) == {"openai_compatible", "ollama", "echo"}


class TestHostMatching:
    """Direct tests of the matcher, including the cases a naive substring check gets wrong."""

    def test_a_subdomain_of_a_banned_host_is_matched(self) -> None:
        from aura_core.config.external_inference import is_external_inference_host

        assert is_external_inference_host("eu.api.openai.com") == "api.openai.com"

    def test_a_trailing_dot_does_not_evade_the_check(self) -> None:
        """A fully-qualified name with the root label spelled out still resolves the same."""
        from aura_core.config.external_inference import is_external_inference_host

        assert is_external_inference_host("api.openai.com.") == "api.openai.com"

    def test_case_does_not_evade_the_check(self) -> None:
        from aura_core.config.external_inference import is_external_inference_host

        assert is_external_inference_host("API.OpenAI.COM") == "api.openai.com"

    @pytest.mark.parametrize(
        "host", ["localhost", "ollama", "127.0.0.1", "vllm.internal", "llm.aura.internal", ""]
    )
    def test_local_hosts_are_not_matched(self, host: str) -> None:
        from aura_core.config.external_inference import is_external_inference_host

        assert is_external_inference_host(host) is None
