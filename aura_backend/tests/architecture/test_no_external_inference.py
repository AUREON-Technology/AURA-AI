"""Fitness functions for the self-hosting rule.

The mission's non-negotiable constraint is that **no inference leaves our infrastructure**.
Deleting the Gemini and Anthropic adapters satisfied that once; these tests are what keep it
satisfied. A rule enforced by a deletion lasts until the next person needs a quick fallback.

Six independent layers, because no single one is sufficient:

1. **No hosted-inference SDK is importable anywhere in the backend.** Catches
   ``from google import genai`` reappearing in a service. Walks the full AST, so a lazy import
   inside a function -- the exact shape a "temporary fallback" takes -- is caught too.
2. **No hosted-inference endpoint appears in any source file.** Catches an SDK-free
   ``httpx.post("https://api.openai.com/...")``, which layer 1 would miss entirely.
3. **No hosted-inference package is declared as a Python dependency.** Catches the SDK being
   reinstalled before any code uses it.
4. **No hosted-inference or on-device package is declared in ``pubspec.yaml``.** (New.)
5. **No hosted-inference credential is provisioned by any deployment manifest.** (New.)
6. **No configuration template names a removed provider kind.** (New.)

Plus the runtime half: ``ProviderConfig`` refuses those hostnames at construction, tested here
so the config surface and the static scan stay in step.

ADR-030 added a third host category beside "banned" and "rented compute": hosted APIs serving
open-weight models (Groq, Workers AI, OpenRouter, ...). They are refused unless
``AURA_ALLOW_HOSTED_INFERENCE`` is set, only for open-weight model IDs, and never named in Python
source -- a hosted endpoint is configuration, not code. ``TestHostedOpenWeightIsGatedNotSilent``
pins all of that, including that neither opt-in unlocks the other or a banned vendor.

ADR-033 added the one closed-model exception: Anthropic's Claude, as provider kind ``claude``,
refused unless ``AURA_ALLOW_CLAUDE_INFERENCE`` is set. It is a hole in exactly three places, each
pinned by ``TestClaudeIsGatedNotSilent``: one module may import the ``anthropic`` SDK, the
``anthropic`` distribution may be declared, and the set of kinds includes ``claude``. The host
``api.anthropic.com`` stays banned as a ``base_url`` for every kind, ``claude`` included.

Layers 4-6 exist because layers 1-3 missed real violations, and it is worth being precise about
how. Every one of them opens only ``*.py`` files under ``aura_backend/`` and ``requirements*.txt``.
So:

* ``pubspec.yaml`` was read by **nothing**. Removing ``google_generative_ai`` and ``flutter_llama``
  from the Flutter client was enforced by memory alone -- re-adding either would have passed the
  full suite. This was the largest hole, and it was on the side of the system the rules talk about
  most (Rules 14-16: no model in the APK, none on the device, no inference on the device).
* ``infra/terraform/*.tf``, ``.github/workflows/*.yml`` and ``deploy/.env.example`` all still
  provisioned a ``GEMINI_API_KEY`` into a runtime where nothing read it. Not a call -- but the last
  step before one.
* ``deploy/.env.example`` documented ``AURA_PROVIDER_CLOUD_KIND=gemini`` as a supported option.
  It instructed the operator to do the forbidden thing, and it had additionally been broken since
  ``kind=gemini`` was removed, so anyone following it got a ``ConfigurationError`` at boot.

The general lesson, which is the reason these three layers are written as one commit rather than
patched in one by one: **a scan that reads one file type enforces the rule only in that file
type.** "No external AI APIs" is a property of the whole deployment, and a deployment is mostly
not Python.

Every list comes from :mod:`aura_core.config.external_inference`. The tests do not keep their own
copy -- when they did, the runtime check and the static scan drifted and the weaker of the two
became the real policy.

What these tests deliberately do **not** claim: they cannot stop an operator pointing ``base_url``
at a self-run proxy that forwards to a vendor. Nothing inside the process can -- that is what the
deployment's egress policy is for. These close the accidental path, not the determined one.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from aura_core.config.external_inference import (
    BANNED_CREDENTIAL_NAMES,
    BANNED_DART_PACKAGES,
    BANNED_DISTRIBUTIONS,
    BANNED_PROVIDER_KINDS,
    BANNED_SDK_ROOTS,
    EXTERNAL_INFERENCE_HOSTS,
    SANCTIONED_DISTRIBUTIONS,
    SANCTIONED_SDK_IMPORTERS,
    credential_name_variants,
)

BACKEND = Path(__file__).resolve().parents[2]
#: The repository root, one level above the backend. Layers 4-6 scan outside `aura_backend/`,
#: which is the whole reason they catch what layers 1-3 do not.
REPO = BACKEND.parent

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
                if banned and rel in SANCTIONED_SDK_IMPORTERS.get(banned, frozenset()):
                    continue  # ADR-033: pinned by TestClaudeIsGatedNotSilent
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


#: File types that configure a deployment. Markdown is excluded on purpose: the audit, the ADRs
#: and the readiness score all have to *name* the banned things in order to say they are banned,
#: and a scan that fails on its own documentation gets deleted rather than fixed.
_MANIFEST_GLOBS = (
    "*.yml", "*.yaml", "*.tf", "*.tfvars", "*.sh", "*.env", "*.env.example",
    "*.example", "*.conf", "*.ini", "*.toml", "Dockerfile*",
)

_MANIFEST_SKIP = {
    "__pycache__", "venv", ".venv", "node_modules", "build", "dist", ".git",
    ".dart_tool", "Pods", ".gradle", "ephemeral", ".claude",
}

#: An explicit, greppable per-line exemption, in the spirit of ``# noqa``.
#:
#: The first version of these layers exempted any line containing "removed", "used to" or "no
#: longer", so that a comment recording *why* a credential was deleted would not itself fail the
#: scan. That was wrong, and the test caught it on its own author: those words are ordinary
#: English, and ``GEMINI_API_KEY=$KEY  # the old key was removed`` would have passed. An exemption
#: has to be a deliberate act that a reviewer can see, not a side effect of prose.
#:
#: Legitimate uses are narrow: an operational note that must name the secret in order to tell the
#: operator which one to revoke, and this file. Anything else is the thing being banned.
SCAN_ALLOW_MARKER = "self-hosting-scan: allow"


def _manifests() -> list[Path]:
    """Every deployment-shaped file in the repository, Python and Markdown excluded."""
    found: list[Path] = []
    for pattern in _MANIFEST_GLOBS:
        for path in REPO.rglob(pattern):
            if _MANIFEST_SKIP & set(path.parts) or not path.is_file():
                continue
            found.append(path)
    return sorted(set(found))


def _repo_rel(path: Path) -> str:
    return path.relative_to(REPO).as_posix()


class TestClientDeclaresNoInferencePackage:
    """Layer 4: the Flutter client's dependencies.

    Before this, ``pubspec.yaml`` was read by no test in the repository. The client's compliance
    with Rules 8-16 rested entirely on the packages having been deleted once.
    """

    def _pubspecs(self) -> list[Path]:
        return [p for p in REPO.rglob("pubspec.yaml") if not _MANIFEST_SKIP & set(p.parts)]

    def test_a_pubspec_exists_to_scan(self) -> None:
        # Without this, the layer below passes vacuously the day someone moves the client.
        assert self._pubspecs(), (
            "no pubspec.yaml found anywhere in the repository. If the Flutter client moved, this "
            "layer is now scanning nothing and silently passing -- point it at the new location."
        )

    def test_pubspec_declares_no_inference_package(self) -> None:
        offenders: list[str] = []
        for spec in self._pubspecs():
            for lineno, raw in enumerate(spec.read_text(encoding="utf-8").splitlines(), 1):
                line = raw.split("#", 1)[0]
                if ":" not in line:
                    continue
                # A dependency entry is `  name: ^1.2.3` or `  name:` with a nested block. The
                # key is what matters, so compare on it rather than searching the whole line --
                # a comment or a description mentioning a package is not a dependency on it.
                key = line.split(":", 1)[0].strip().strip('"\'')
                if key in BANNED_DART_PACKAGES:
                    offenders.append(f"{_repo_rel(spec)}:{lineno}: declares {key!r}")
        assert not offenders, (
            "The Flutter client declares an inference package. The client is a client: no key, no "
            "model, no inference (Rules 8-16, 30). A hosted-API package puts a key on the device; "
            "an on-device runtime puts the model there.\n  " + "\n  ".join(offenders)
        )


class TestNoManifestProvisionsAnInferenceCredential:
    """Layer 5: deployment manifests.

    ``GEMINI_API_KEY`` lived in Terraform, a GitHub workflow and an env template while all three
    Python-facing layers reported a clean scan.
    """

    def test_no_manifest_names_a_hosted_inference_credential(self) -> None:
        variants: list[tuple[str, str]] = []
        for name in BANNED_CREDENTIAL_NAMES:
            for variant in credential_name_variants(name):
                variants.append((variant, name))

        offenders: list[str] = []
        for path in _manifests():
            rel = _repo_rel(path)
            for lineno, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if SCAN_ALLOW_MARKER in raw:
                    continue
                lowered = raw.lower()
                for variant, canonical in variants:
                    if variant in lowered:
                        offenders.append(f"{rel}:{lineno}: provisions {canonical}")
                        break
        assert not offenders, (
            "A deployment manifest provisions a hosted-inference credential. Nothing reads it "
            "today -- that is not the point. Minting the key is the last step before calling the "
            "API, and it reads to the next engineer as permission.\n  " + "\n  ".join(offenders)
        )

    def test_no_manifest_references_a_hosted_inference_endpoint(self) -> None:
        offenders: list[str] = []
        for path in _manifests():
            rel = _repo_rel(path)
            text = path.read_text(encoding="utf-8", errors="replace").lower()
            for host in EXTERNAL_INFERENCE_HOSTS:
                if host in text:
                    offenders.append(f"{rel}: references {host!r}")
        assert not offenders, (
            "A hosted inference endpoint appears in a deployment manifest.\n  "
            + "\n  ".join(offenders)
        )


class TestNoConfigTemplateOffersARemovedProviderKind:
    """Layer 6: configuration templates.

    ``deploy/.env.example`` told operators to set ``AURA_PROVIDER_CLOUD_KIND=gemini``. Templates
    are not parsed by anything, so a template can instruct the forbidden thing indefinitely -- and
    this one had also been broken since the kind was removed, so following it failed at boot.
    """

    #: ``KIND`` followed by ``=`` or ``:``, then a banned value that ends there. The trailing
    #: boundary is what keeps ``openai_compatible`` -- the kind used to reach a *local* vLLM --
    #: from matching the banned ``openai``. Getting this wrong would fail the platform's own
    #: configuration, which is a worse outcome than the hole it closes.
    _PATTERN = re.compile(
        r"kind\s*[:=]\s*[\"']?(" + "|".join(re.escape(k) for k in BANNED_PROVIDER_KINDS) + r")(?![\w-])",
        re.IGNORECASE,
    )

    def test_no_template_configures_a_removed_provider_kind(self) -> None:
        offenders: list[str] = []
        for path in _manifests():
            rel = _repo_rel(path)
            for lineno, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if SCAN_ALLOW_MARKER in raw:
                    continue
                match = self._PATTERN.search(raw)
                if match:
                    offenders.append(f"{rel}:{lineno}: offers kind={match.group(1)!r}")
        assert not offenders, (
            "A configuration template names a provider kind that no longer exists. Two problems "
            "at once: it instructs the operator to add external inference, and following it "
            "raises ConfigurationError at boot.\n  " + "\n  ".join(offenders)
        )

    def test_the_pattern_permits_the_local_kind(self) -> None:
        """The regression this pattern is most likely to cause, pinned as a test.

        ``openai_compatible`` is how the platform talks to its own vLLM. A matcher without a value
        boundary rejects it, which would fail every correct deployment.
        """
        assert self._PATTERN.search("AURA_PROVIDER_LOCAL_KIND=openai_compatible") is None
        assert self._PATTERN.search("AURA_PROVIDER_LOCAL_KIND: openai_compatible") is None
        assert self._PATTERN.search("kind: ollama") is None
        assert self._PATTERN.search("kind: echo") is None

    def test_the_pattern_catches_what_it_is_for(self) -> None:
        assert self._PATTERN.search("AURA_PROVIDER_CLOUD_KIND=gemini") is not None
        assert self._PATTERN.search('  kind: "anthropic"') is not None
        assert self._PATTERN.search("KIND = bedrock") is not None


class TestRemoteInferenceIsGatedNotSilent:
    """Rented infrastructure is a choice, and it has to be made on purpose.

    A Space runs *your* container and *your* weights on someone else's hardware. That is a
    genuinely different thing from calling a vendor's model, and the platform permits it -- but two
    of the mission's rules stop holding when you do, so it must never happen because a URL was
    pasted into an env file.

    These tests exist because ``*.hf.space`` was on no list at all. A Space URL would have passed
    all six static layers and the runtime check, and the platform's central claim would have become
    false while every test still reported it true.
    """

    REMOTE_URLS = [
        "https://anas-aura-inference.hf.space/v1",
        "https://aura-backend-abc123.run.app/v1",
        "https://aura.fly.dev/v1",
        "https://aura-production-a31f.up.railway.app/v1",
        "https://aura--infer.modal.run/v1",
    ]

    @pytest.mark.parametrize("base_url", REMOTE_URLS)
    def test_refused_without_the_opt_in(self, base_url: str, monkeypatch) -> None:
        from aura_core.config.settings import ProviderConfig
        from aura_core.domain.errors import ConfigurationError

        monkeypatch.delenv("AURA_ALLOW_REMOTE_INFERENCE", raising=False)
        with pytest.raises(ConfigurationError) as excinfo:
            ProviderConfig(name="remote", kind="openai_compatible", model="m", base_url=base_url)

        message = str(excinfo.value)
        # The error has to state the cost, not merely refuse. An operator who reads "invalid
        # configuration" changes the setting; one who reads what stops working makes a decision.
        assert "without internet" in message
        assert "leave infrastructure you control" in message
        assert "AURA_ALLOW_REMOTE_INFERENCE" in message

    @pytest.mark.parametrize("base_url", REMOTE_URLS)
    def test_permitted_with_the_opt_in(self, base_url: str, monkeypatch) -> None:
        from aura_core.config.settings import ProviderConfig

        monkeypatch.setenv("AURA_ALLOW_REMOTE_INFERENCE", "true")
        config = ProviderConfig(
            name="remote", kind="openai_compatible", model="m", base_url=base_url
        )
        assert config.base_url == base_url

    def test_the_opt_in_does_not_unlock_a_banned_vendor(self, monkeypatch) -> None:
        """The gate is for rented compute running your model, not for hosted APIs.

        If this ever passes, the escape hatch has become a way around the whole rule.
        """
        from aura_core.config.settings import ProviderConfig
        from aura_core.domain.errors import ConfigurationError

        monkeypatch.setenv("AURA_ALLOW_REMOTE_INFERENCE", "true")
        with pytest.raises(ConfigurationError) as excinfo:
            ProviderConfig(
                name="sneaky",
                kind="openai_compatible",
                model="m",
                base_url="https://api.openai.com/v1",
            )
        assert "own infrastructure" in str(excinfo.value)

    @pytest.mark.parametrize(
        "base_url",
        [
            "http://ollama:11434",
            "http://localhost:8000/v1",
            "http://gpu-host.internal:8000/v1",
            "http://10.0.0.5:8000/v1",
        ],
    )
    def test_a_host_you_own_needs_no_opt_in(self, base_url: str, monkeypatch) -> None:
        """The gate must not fire on ordinary self-hosted deployments."""
        from aura_core.config.settings import ProviderConfig

        monkeypatch.delenv("AURA_ALLOW_REMOTE_INFERENCE", raising=False)
        assert ProviderConfig(
            name="local", kind="openai_compatible", model="m", base_url=base_url
        ).base_url == base_url

    def test_suffix_match_does_not_fire_on_a_lookalike(self, monkeypatch) -> None:
        """`is_remote_inference_host` matches suffixes, not substrings.

        A substring rule would reject `hf.space.internal.mycorp.net`, which is a host you own.
        """
        from aura_core.config.external_inference import is_remote_inference_host

        assert is_remote_inference_host("anas-aura.hf.space") == "hf.space"
        assert is_remote_inference_host("hf.space") == "hf.space"
        assert is_remote_inference_host("hf.space.internal.mycorp.net") is None
        assert is_remote_inference_host("notreallyhf.space") is None
        assert is_remote_inference_host("") is None


class TestProviderConfigRefusesHostedEndpoints:
    """The runtime half of the rule, sharing the registry with the scans above."""

    @pytest.mark.parametrize(
        "base_url",
        [
            "https://api.openai.com/v1",
            "https://api.anthropic.com/v1",
            "https://generativelanguage.googleapis.com/v1beta",
            "https://my-resource.openai.azure.com/openai/deployments/x",
            "https://gateway.ai.cloudflare.com/v1/acct/gw/openai",
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

    def test_the_supported_kinds_are_the_self_hosted_ones_plus_claude(self) -> None:
        """Pins the set, so adding a vendor kind cannot pass unnoticed.

        ``claude`` is the single gated vendor kind ADR-033 sanctions.
        """
        from aura_core.config.settings import ProviderConfig

        assert set(ProviderConfig._KINDS) == {"openai_compatible", "ollama", "echo", "claude"}


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


class TestHostedOpenWeightIsGatedNotSilent:
    """ADR-030: hosted open-weight APIs are a third category, gated by their own flag.

    These hosts were on the banned list until ADR-030. Moving them off it is only safe if every
    property below holds at once: off by default, a separate consent from rented compute, open
    weights only (several of these keys also reach closed models), never mislabelled as local, and
    no banned vendor reachable through either flag.
    """

    HOSTED = [
        ("https://api.groq.com/openai/v1", "openai/gpt-oss-120b"),
        ("https://api.cloudflare.com/client/v4/accounts/abc123/ai/v1", "@cf/openai/gpt-oss-20b"),
        # A paid ID: a `:free` one also needs ALLOW_PROMPT_TRAINING (TestPromptTrainingConsent).
        ("https://openrouter.ai/api/v1", "google/gemma-4-31b-it"),
        ("https://router.huggingface.co/v1", "Qwen/Qwen3-32B"),
        ("https://integrate.api.nvidia.com/v1", "meta/llama-3.3-70b-instruct"),
    ]

    @staticmethod
    def _config(base_url: str, model: str, **kwargs):
        from aura_core.config.settings import ProviderConfig

        return ProviderConfig(
            name="hosted", kind="openai_compatible", model=model, base_url=base_url, **kwargs
        )

    @pytest.mark.parametrize(("base_url", "model"), HOSTED)
    def test_refused_without_the_opt_in(self, base_url: str, model: str, monkeypatch) -> None:
        from aura_core.domain.errors import ConfigurationError

        monkeypatch.delenv("AURA_ALLOW_HOSTED_INFERENCE", raising=False)
        with pytest.raises(ConfigurationError) as excinfo:
            self._config(base_url, model)
        message = str(excinfo.value)
        # As with rented compute, the refusal states the cost so the operator makes a decision.
        assert "AURA_ALLOW_HOSTED_INFERENCE" in message
        assert "readable by its operator" in message

    @pytest.mark.parametrize(("base_url", "model"), HOSTED)
    def test_permitted_with_the_opt_in(self, base_url: str, model: str, monkeypatch) -> None:
        monkeypatch.setenv("AURA_ALLOW_HOSTED_INFERENCE", "true")
        assert self._config(base_url, model).base_url == base_url

    @pytest.mark.parametrize(("base_url", "model"), HOSTED)
    def test_the_rented_compute_opt_in_does_not_unlock_it(
        self, base_url: str, model: str, monkeypatch
    ) -> None:
        """Consenting to your weights on rented hardware is not consenting to a third party."""
        from aura_core.domain.errors import ConfigurationError

        monkeypatch.delenv("AURA_ALLOW_HOSTED_INFERENCE", raising=False)
        monkeypatch.setenv("AURA_ALLOW_REMOTE_INFERENCE", "true")
        with pytest.raises(ConfigurationError):
            self._config(base_url, model)

    def test_the_hosted_opt_in_does_not_unlock_rented_compute(self, monkeypatch) -> None:
        from aura_core.domain.errors import ConfigurationError

        monkeypatch.setenv("AURA_ALLOW_HOSTED_INFERENCE", "true")
        monkeypatch.delenv("AURA_ALLOW_REMOTE_INFERENCE", raising=False)
        with pytest.raises(ConfigurationError) as excinfo:
            self._config("https://anas-aura-inference.hf.space/v1", "m")
        assert "AURA_ALLOW_REMOTE_INFERENCE" in str(excinfo.value)

    @pytest.mark.parametrize(
        "base_url",
        [
            "https://api.openai.com/v1",
            "https://api.anthropic.com/v1",
            "https://gateway.ai.cloudflare.com/v1/acct/gw/compat",
            "https://api.mistral.ai/v1",
        ],
    )
    def test_neither_opt_in_unlocks_a_banned_vendor(self, base_url: str, monkeypatch) -> None:
        """Even with an open-weight model ID, a banned host stays banned.

        Cloudflare's AI Gateway is the case that matters most: it shares a vendor with Workers AI
        and forwards to closed providers.
        """
        from aura_core.domain.errors import ConfigurationError

        monkeypatch.setenv("AURA_ALLOW_HOSTED_INFERENCE", "true")
        monkeypatch.setenv("AURA_ALLOW_REMOTE_INFERENCE", "true")
        with pytest.raises(ConfigurationError) as excinfo:
            self._config(base_url, "openai/gpt-oss-120b")
        assert "own infrastructure" in str(excinfo.value)

    @pytest.mark.parametrize(
        ("base_url", "model"),
        [
            ("https://openrouter.ai/api/v1", "openai/gpt-5"),
            ("https://openrouter.ai/api/v1", "anthropic/claude-sonnet-4"),
            ("https://openrouter.ai/api/v1", "google/gemini-2.5-flash"),
            ("https://openrouter.ai/api/v1", "qwen/qwen3-max"),
            ("https://api.cloudflare.com/client/v4/accounts/abc123/ai/v1", "openai/gpt-4.1"),
            ("https://router.huggingface.co/v1", "perplexity/llama-3.1-sonar-large"),
        ],
    )
    def test_a_closed_model_is_refused_even_with_the_opt_in(
        self, base_url: str, model: str, monkeypatch
    ) -> None:
        """Mixed catalogs are the reason the model ID is checked, not just the host."""
        from aura_core.domain.errors import ConfigurationError

        monkeypatch.setenv("AURA_ALLOW_HOSTED_INFERENCE", "true")
        with pytest.raises(ConfigurationError) as excinfo:
            self._config(base_url, model)
        assert "open-weight" in str(excinfo.value)

    def test_a_hosted_provider_cannot_claim_to_be_local(self, monkeypatch) -> None:
        """`is_local` steers local_first routing; a lie here would prefer a third party."""
        from aura_core.domain.errors import ConfigurationError

        monkeypatch.setenv("AURA_ALLOW_HOSTED_INFERENCE", "true")
        with pytest.raises(ConfigurationError) as excinfo:
            self._config("https://api.groq.com/openai/v1", "openai/gpt-oss-120b", is_local=True)
        assert "is_local" in str(excinfo.value)

    def test_the_hosted_and_banned_lists_do_not_overlap(self) -> None:
        """The banned check runs first and matches substrings, so an overlap would shadow a host."""
        from aura_core.config.external_inference import (
            HOSTED_OPEN_WEIGHT_HOSTS,
            is_external_inference_host,
            is_remote_inference_host,
        )

        for host in HOSTED_OPEN_WEIGHT_HOSTS:
            assert is_external_inference_host(host) is None, host
            assert is_remote_inference_host(host) is None, host

    def test_suffix_match_does_not_fire_on_a_lookalike(self) -> None:
        from aura_core.config.external_inference import is_hosted_open_weight_host

        assert is_hosted_open_weight_host("api.groq.com") == "api.groq.com"
        assert is_hosted_open_weight_host("API.Groq.com.") == "api.groq.com"
        assert is_hosted_open_weight_host("eu.openrouter.ai") == "openrouter.ai"
        assert is_hosted_open_weight_host("api.groq.com.internal.mycorp.net") is None
        assert is_hosted_open_weight_host("notopenrouter.ai") is None
        assert is_hosted_open_weight_host("") is None

    @pytest.mark.parametrize(
        "model",
        [
            "openai/gpt-oss-120b",
            "openai/gpt-oss-20b:free",
            "@cf/openai/gpt-oss-20b",
            "@cf/qwen/qwen3-30b-a3b-fp8",
            "@cf/meta/llama-3.1-8b-instruct",
            "@cf/mistral/mistral-7b-instruct-v0.1",
            "qwen/qwen3.8-27b",
            "Qwen/Qwen3-32B",
            "meta-llama/Llama-3.3-70B-Instruct:groq",
            "google/gemma-4-31b-it:free",
            "deepseek-ai/DeepSeek-V3.1",
            "nvidia/llama-3.1-nemotron-70b-instruct",
            "moonshotai/kimi-k2-instruct",
            "llama-3.3-70b-versatile",
        ],
    )
    def test_open_weight_models_are_recognised(self, model: str) -> None:
        from aura_core.config.external_inference import is_open_weight_model

        assert is_open_weight_model(model)

    @pytest.mark.parametrize(
        "model",
        [
            "",
            "openai/gpt-5",
            "openai/gpt-4.1",
            "google/gemini-2.5-flash",
            "anthropic/claude-sonnet-4",
            "x-ai/grok-4",
            "perplexity/llama-3.1-sonar-large",
            "qwen/qwen-max",
            "qwen/qwen-plus",
            "qwen/qwen-turbo",
            "qwen/qwen3-max",
            "mistralai/mistral-large",
            "mistralai/mistral-medium-3",
            "cohere/command-r",
        ],
    )
    def test_closed_models_are_not_recognised(self, model: str) -> None:
        from aura_core.config.external_inference import is_open_weight_model

        assert not is_open_weight_model(model)

    def test_no_source_file_hardcodes_a_hosted_open_weight_endpoint(self) -> None:
        """A hosted endpoint is configuration, chosen by an operator, never a default in code.

        Moving these hosts off the banned list also removed them from layer 2's scan. Without this
        test, a hardcoded ``https://api.groq.com/...`` default would pass every check and still be
        refused only at runtime, and only because the flag happened to be off.
        """
        from aura_core.config.external_inference import HOSTED_OPEN_WEIGHT_HOSTS

        offenders: list[str] = []
        for path in _sources():
            rel = _rel(path)
            if rel in SELF_EXEMPT:
                continue
            text = path.read_text(encoding="utf-8").lower()
            for host in HOSTED_OPEN_WEIGHT_HOSTS:
                if host in text:
                    offenders.append(f"{rel}: references {host!r}")
        assert not offenders, (
            "A hosted open-weight endpoint appears in Python source. Configure it through "
            "AURA_PROVIDER_<NAME>_BASE_URL instead (ADR-030).\n  " + "\n  ".join(offenders)
        )


class TestClaudeIsGatedNotSilent:
    """ADR-033: Claude is allowed only on purpose, only through ``kind=claude``, and narrowly.

    Every assertion here pins one edge of the exception, so widening it -- a second importer, a
    redirectable endpoint, an ambient key, a local flag -- fails a named test rather than passing
    quietly.
    """

    @staticmethod
    def _claude(**overrides):
        from aura_core.config.settings import ProviderConfig

        fields = {
            "name": "claude",
            "kind": "claude",
            "model": "claude-opus-5-5",
            "api_key_env": "AURA_CLAUDE_KEY",
            "quality_tier": 3,
            "pools": ("reasoning",),
        }
        fields.update(overrides)
        return ProviderConfig(**fields)

    def test_refused_without_the_opt_in(self, monkeypatch) -> None:
        from aura_core.domain.errors import ConfigurationError

        monkeypatch.delenv("AURA_ALLOW_CLAUDE_INFERENCE", raising=False)
        with pytest.raises(ConfigurationError) as excinfo:
            self._claude()
        assert "AURA_ALLOW_CLAUDE_INFERENCE" in str(excinfo.value)

    def test_neither_other_opt_in_unlocks_it(self, monkeypatch) -> None:
        from aura_core.domain.errors import ConfigurationError

        monkeypatch.delenv("AURA_ALLOW_CLAUDE_INFERENCE", raising=False)
        monkeypatch.setenv("AURA_ALLOW_HOSTED_INFERENCE", "true")
        monkeypatch.setenv("AURA_ALLOW_REMOTE_INFERENCE", "true")
        with pytest.raises(ConfigurationError):
            self._claude()

    def test_permitted_with_the_opt_in_as_a_third_party(self, monkeypatch) -> None:
        from aura_core.domain.llm import DataClassification

        monkeypatch.setenv("AURA_ALLOW_CLAUDE_INFERENCE", "true")
        config = self._claude()
        assert config.third_party
        assert not config.is_local
        assert config.data_classification_ceiling is DataClassification.INTERNAL

    def test_the_claude_opt_in_unlocks_no_other_vendor(self, monkeypatch) -> None:
        """Not the compat endpoint through ``openai_compatible``, not any other banned host."""
        from aura_core.config.settings import ProviderConfig
        from aura_core.domain.errors import ConfigurationError

        monkeypatch.setenv("AURA_ALLOW_CLAUDE_INFERENCE", "true")
        for base_url in ("https://api.anthropic.com/v1", "https://api.openai.com/v1"):
            with pytest.raises(ConfigurationError) as excinfo:
                ProviderConfig(
                    name="p", kind="openai_compatible", model="claude-opus-5-5", base_url=base_url
                )
            assert "hosted inference service" in str(excinfo.value)

    def test_the_endpoint_cannot_be_redirected(self, monkeypatch) -> None:
        from aura_core.domain.errors import ConfigurationError

        monkeypatch.setenv("AURA_ALLOW_CLAUDE_INFERENCE", "true")
        with pytest.raises(ConfigurationError) as excinfo:
            self._claude(base_url="https://proxy.example.com")
        assert "base_url" in str(excinfo.value)

    @pytest.mark.parametrize(
        "model", ["gpt-5", "anthropic/claude-sonnet-4", "llama-3.3-70b", "Claude-Opus", ""]
    )
    def test_only_a_claude_model_id_is_accepted(self, model: str, monkeypatch) -> None:
        from aura_core.domain.errors import ConfigurationError

        monkeypatch.setenv("AURA_ALLOW_CLAUDE_INFERENCE", "true")
        with pytest.raises(ConfigurationError):
            self._claude(model=model)

    def test_the_key_must_come_from_the_slot_variable(self, monkeypatch) -> None:
        from aura_core.domain.errors import ConfigurationError

        monkeypatch.setenv("AURA_ALLOW_CLAUDE_INFERENCE", "true")
        with pytest.raises(ConfigurationError) as excinfo:
            self._claude(api_key_env="")
        assert "API_KEY_ENV" in str(excinfo.value)

    def test_it_can_never_claim_to_be_local(self, monkeypatch) -> None:
        from aura_core.domain.errors import ConfigurationError

        monkeypatch.setenv("AURA_ALLOW_CLAUDE_INFERENCE", "true")
        with pytest.raises(ConfigurationError):
            self._claude(is_local=True)

    def test_exactly_one_module_may_import_the_sdk(self) -> None:
        assert SANCTIONED_SDK_IMPORTERS == {
            "anthropic": frozenset({"aura_core/llm/providers/claude.py"})
        }
        for root, modules in SANCTIONED_SDK_IMPORTERS.items():
            assert root in BANNED_SDK_ROOTS, "a sanctioned root stays banned everywhere else"
            for module in modules:
                assert (BACKEND / module).is_file(), f"{module} is sanctioned but does not exist"

    def test_the_sanctioned_module_imports_the_sdk_lazily(self) -> None:
        """Module scope would make ``aura_core`` unimportable without the SDK installed."""
        path = BACKEND / "aura_core/llm/providers/claude.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                assert "anthropic" not in _dotted_imports(ast.Module(body=[node], type_ignores=[]))

    def test_only_the_anthropic_distribution_is_sanctioned(self) -> None:
        assert SANCTIONED_DISTRIBUTIONS == frozenset({"anthropic"})
        assert not SANCTIONED_DISTRIBUTIONS & set(BANNED_DISTRIBUTIONS)
        assert "langchain-anthropic" in BANNED_DISTRIBUTIONS

    def test_what_stays_banned_stays_banned(self) -> None:
        assert "api.anthropic.com" in EXTERNAL_INFERENCE_HOSTS
        assert "anthropic" in BANNED_PROVIDER_KINDS
        assert "ANTHROPIC_API_KEY" in BANNED_CREDENTIAL_NAMES
        assert "ANTHROPIC_AUTH_TOKEN" in BANNED_CREDENTIAL_NAMES


class TestExtractionIsGatedNotSilent:
    """ADR-038: Cloudflare's ``clairic`` is reached only on purpose, only through one module, and
    without widening ADR-030's open-weight rule.

    Each assertion pins one edge of the exception, so widening it -- a second call site, a provider
    slot for the model, a banned host, a wider open-weight list -- fails a named test.
    """

    ADAPTER = "aura_core/adapters/cloudflare_clairic.py"

    def test_exactly_one_module_names_the_run_path(self) -> None:
        naming = sorted(
            _rel(p)
            for p in _sources()
            if _rel(p).startswith("aura_core/")
            and _rel(p) not in SELF_EXEMPT
            and "/ai/run" in p.read_text(encoding="utf-8")
        )
        assert naming == [self.ADAPTER], (
            "Only the sanctioned adapter may name Workers AI's /ai/run path (ADR-038). "
            f"Found: {naming}"
        )

    def test_the_adapter_imports_the_http_client_lazily(self) -> None:
        tree = ast.parse((BACKEND / self.ADAPTER).read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                assert "httpx" not in _dotted_imports(ast.Module(body=[node], type_ignores=[]))

    def test_the_host_stays_gated_and_is_never_a_banned_one(self) -> None:
        from aura_core.config.external_inference import (
            EXTRACTION_API_HOST,
            is_external_inference_host,
            is_hosted_open_weight_host,
        )

        assert is_hosted_open_weight_host(EXTRACTION_API_HOST)
        assert is_external_inference_host(EXTRACTION_API_HOST) is None

    def test_the_ai_gateway_proxy_stays_banned(self) -> None:
        assert "gateway.ai.cloudflare.com" in EXTERNAL_INFERENCE_HOSTS

    def test_the_open_weight_rule_was_not_widened_to_admit_the_model(self) -> None:
        from aura_core.config.external_inference import EXTRACTION_MODEL, is_open_weight_model

        assert not is_open_weight_model(EXTRACTION_MODEL), (
            "clairic is now recognised as open-weight. If Cloudflare has published its weights, "
            "retire ADR-038's exception rather than leaving both in place (ADR-038 O-2)."
        )

    def test_the_model_cannot_be_a_chat_provider_slot_even_with_the_hosted_opt_in(
        self, monkeypatch
    ) -> None:
        from aura_core.config.external_inference import EXTRACTION_MODEL
        from aura_core.config.settings import ProviderConfig
        from aura_core.domain.errors import ConfigurationError

        monkeypatch.setenv("AURA_ALLOW_HOSTED_INFERENCE", "true")
        with pytest.raises(ConfigurationError) as excinfo:
            ProviderConfig(
                name="hosted",
                kind="openai_compatible",
                model=EXTRACTION_MODEL,
                base_url="https://api.cloudflare.com/client/v4/accounts/abc123/ai/v1",
            )
        assert "open-weight" in str(excinfo.value)
