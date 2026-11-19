"""Architectural fitness functions.

These enforce ADR-001 mechanically. A layering rule that lives only in a document decays within
weeks; one that fails the build does not.

The layering (CLAUDE.md, ``aura_core/__init__.py``, ADR-001)::

    domain/  ports/      stdlib only (ports may also use domain)
    engines              domain + ports (+ the support packages and peer engines)
    adapters/  api/      vendor SDKs, imported lazily inside functions
    container.py         the only place concrete meets abstract

Every ``aura_core`` subpackage is *discovered*, not listed: a new package is an engine (the
strictest non-core ring) until someone deliberately classifies it otherwise below. Both relative
(``from ..adapters import x``) and absolute (``from aura_core.adapters import x``) imports are
resolved to the ``aura_core`` package they land in, and "module scope" means everything that runs
at import -- including imports nested in ``try``/``if``/class bodies, which a ``col_offset == 0``
check would miss (F-28 / AR-09).

Rules:

1. ``domain`` imports only the stdlib and ``domain``; ``ports`` only the stdlib, ``domain`` and
   ``ports`` -- at *any* scope. This is what lets the whole cognitive core be unit-tested on bare
   CPython.
2. Engines and support packages never import ``adapters``, ``api`` or ``container`` -- at any
   scope -- and import no third-party module at module scope. Policy must not depend on
   infrastructure, or the dependency inversion is decorative.
3. No ``aura_core`` module imports a vendor SDK (:data:`VENDOR_MODULES`) at module scope; adapters
   and api reach them lazily, inside the function that needs them.
4. Nothing in ``aura_core`` except ``container.py`` imports ``aura_core.container``.
5. ``aura_core`` never imports ``aura_backend.services`` or ``infrastructure`` (ADR-002).

Engines may import one another: the kernel composes planning, execution, tools and llm, and
the rule CLAUDE.md states is about the direction of the arrow (policy never reaches outward to
infrastructure), not about isolating engines from their peers.
"""

from __future__ import annotations

import ast
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

import pytest

CORE = Path(__file__).resolve().parents[2] / "aura_core"

#: Modules that ship with CPython. ``sys.stdlib_module_names`` is authoritative on 3.10+ and
#: avoids maintaining a hand-written list that silently rots.
STDLIB = frozenset(sys.stdlib_module_names) | {"__future__"}

#: The innermost ring, with the ``aura_core`` packages each may import besides the stdlib.
CORE_LAYERS: dict[str, frozenset[str]] = {
    "domain": frozenset({"domain"}),
    "ports": frozenset({"domain", "ports"}),
}

#: The outer ring: where vendor SDKs are permitted, lazily.
OUTER_PACKAGES = frozenset({"adapters", "api"})

#: Cross-cutting packages engines may use. ``config`` holds the typed settings and the hosted-
#: inference policy (``config/external_inference.py``) the llm registry enforces; ``observability``
#: turns the kernel's telemetry into metrics/logs/spans (ADR-009). Both are held to the engine
#: rules themselves, so letting engines import them cannot smuggle infrastructure in.
SUPPORT_PACKAGES = frozenset({"config", "observability"})

#: The composition root (``aura_core/container.py``).
COMPOSITION_ROOT = "container"

#: ``aura_core/__init__.py`` itself -- imported implicitly by every absolute import.
ROOT = "<root>"

#: The engines CLAUDE.md names explicitly. Asserted to be classified as engines, so a
#: reclassification that quietly exempts one fails loudly.
NAMED_ENGINES = frozenset({
    "prompts", "context", "memory", "knowledge", "tools", "safety",
    "planning", "reasoning", "execution", "llm",
})

#: Third-party top-level modules, i.e. vendor SDKs and libraries outside the stdlib. Derived from
#: ``requirements.lock`` / ``requirements-train.txt`` and from every non-stdlib import that
#: actually appears in ``aura_core`` (``test_vendor_list_covers_every_third_party_import`` keeps
#: it complete -- a new third-party import must be added here before it can land anywhere).
VENDOR_MODULES = frozenset({
    # storage / queues
    "asyncpg", "psycopg", "psycopg2", "sqlalchemy", "alembic", "qdrant_client", "redis",
    "celery", "kombu", "pymysql", "pytds", "pymongo",
    # HTTP / web / RPC
    "httpx", "httpcore", "requests", "urllib3", "aiohttp", "fastapi", "starlette",
    "sse_starlette", "uvicorn", "websockets", "grpc", "mcp", "playwright",
    # hosted vendors and cloud SDKs (inference vendors are also banned outright by
    # test_no_external_inference.py; listed here so even a lazy import would be classified)
    "openai", "anthropic", "google", "firebase_admin", "boto3", "botocore", "docker",
    "sentry_sdk",
    # ML / numerics / media
    "torch", "tensorflow", "transformers", "peft", "sentence_transformers", "tokenizers",
    "safetensors", "huggingface_hub", "tiktoken", "onnxruntime", "ctranslate2",
    "faster_whisper", "numpy", "scipy", "sklearn", "PIL", "pytesseract", "pypdf", "av",
    # observability
    "prometheus_client", "opentelemetry",
    # data / crypto / schema
    "pydantic", "pydantic_settings", "yaml", "jsonschema", "jinja2", "jwt", "cryptography",
    "msgpack", "cbor2", "regex",
})

#: The legacy tree (ADR-002) -- ``aura_core`` must never import it, at any scope.
LEGACY_MODULES = frozenset(
    {"services", "aura_backend", "main", "tasks", "celery_app", "infrastructure"}
)


@dataclass(frozen=True)
class Allowed:
    """One narrowly scoped, documented exception: a (rule, file, imported module) triple."""

    rule: str
    path: str
    module: str
    reason: str


#: Exceptions to the rules above. Each names one file and one imported module, never a package
#: or a whole rule. ``test_allowlist_has_no_stale_entries`` fails when one stops being needed.
ALLOWLIST: tuple[Allowed, ...] = (
    Allowed(
        "module-scope-vendor", "observability/metrics.py", "prometheus_client",
        "Optional dependency probed once at import inside try/except ImportError, exporting "
        "PROMETHEUS_AVAILABLE and falling back to in-process counters; the metric catalogue's "
        "types are needed at class-definition time, so the import cannot move into a function "
        "without changing the module's public surface. Never fails import when absent.",
    ),
    Allowed(
        "module-scope-vendor", "observability/tracing.py", "opentelemetry",
        "Optional dependency probed once at import inside try/except ImportError (OTEL_AVAILABLE); "
        "spans degrade to timing-only when absent. Never fails import when absent.",
    ),
)


# ---------------------------------------------------------------------------------------------
# The walker
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ImportRef:
    """One imported name, resolved to an absolute dotted path."""

    path: Path        # the importing file
    line: int
    target: str       # absolute dotted name, e.g. ``aura_core.adapters.coding_fakes.X``
    lazy: bool        # inside a function/lambda body: does not run at import
    type_only: bool   # inside ``if TYPE_CHECKING:``: never runs at all

    @property
    def head(self) -> str:
        return self.target.split(".")[0]

    @property
    def core_package(self) -> str | None:
        """The ``aura_core`` subpackage (or top-level module) imported, if any."""
        parts = self.target.split(".")
        if parts[0] != "aura_core":
            return None
        return parts[1] if len(parts) > 1 else ROOT

    @property
    def module_scope(self) -> bool:
        return not self.lazy and not self.type_only


def _resolve(path: Path, core: Path, node: ast.Import | ast.ImportFrom) -> list[str]:
    """Absolute dotted targets of one import statement, relative imports included.

    ``from X import a, b`` yields ``X.a`` and ``X.b`` so that ``from aura_core import container``
    and ``from .. import adapters`` resolve to the package actually reached.
    """
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if node.level == 0:
        base = node.module or ""
    else:
        package = list(path.relative_to(core.parent).with_suffix("").parts[:-1])
        if node.level > 1:
            package = package[: len(package) - (node.level - 1)]
        base = ".".join(package + ([node.module] if node.module else []))
    return [f"{base}.{alias.name}" if base else alias.name for alias in node.names]


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def imports_of(path: Path, core: Path = CORE) -> list[ImportRef]:
    """Every import in ``path``, at every scope, tagged with whether it runs at import time."""
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    found: list[ImportRef] = []

    def visit(node: ast.AST, lazy: bool, type_only: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                visit(child, True, type_only)
                continue
            if isinstance(child, ast.If) and _is_type_checking(child.test):
                for stmt in child.body:
                    visit(ast.Module(body=[stmt], type_ignores=[]), lazy, True)
                for stmt in child.orelse:
                    visit(ast.Module(body=[stmt], type_ignores=[]), lazy, type_only)
                continue
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                for target in _resolve(path, core, child):
                    found.append(ImportRef(path, child.lineno, target, lazy, type_only))
            visit(child, lazy, type_only)

    visit(tree, False, False)
    return found


def discover_packages(core: Path = CORE) -> list[str]:
    """Every subpackage of ``aura_core`` plus its top-level modules (``container``)."""
    names = {p.name for p in core.iterdir() if p.is_dir() and (p / "__init__.py").exists()}
    names |= {p.stem for p in core.glob("*.py") if p.stem != "__init__"}
    return sorted(names)


def engine_packages(core: Path = CORE) -> list[str]:
    """Everything that is not core, outer ring, support or the composition root."""
    excluded = set(CORE_LAYERS) | OUTER_PACKAGES | SUPPORT_PACKAGES | {COMPOSITION_ROOT}
    return [name for name in discover_packages(core) if name not in excluded]


def _modules(package: str, core: Path = CORE) -> list[Path]:
    if not package:
        root = core
    elif (core / package).is_dir():
        root = core / package
    else:
        return [core / f"{package}.py"]
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _allowed(rule: str, ref: ImportRef, core: Path, used: set[Allowed] | None) -> bool:
    rel = ref.path.relative_to(core).as_posix()
    for entry in ALLOWLIST:
        if entry.rule == rule and entry.path == rel and ref.head == entry.module:
            if used is not None:
                used.add(entry)
            return True
    return False


def _where(ref: ImportRef, core: Path) -> str:
    scope = "type-only" if ref.type_only else ("lazy" if ref.lazy else "module scope")
    return f"{ref.path.relative_to(core).as_posix()}:{ref.line} imports {ref.target} ({scope})"


# ---------------------------------------------------------------------------------------------
# The rules, as functions of a core root so the self-tests can run them on a synthetic tree
# ---------------------------------------------------------------------------------------------


def core_layer_violations(package: str, core: Path = CORE) -> list[str]:
    """Rule 1: domain/ports import only the stdlib and the inner layers they may see."""
    allowed_core = CORE_LAYERS[package]
    out: list[str] = []
    for module in _modules(package, core):
        for ref in imports_of(module, core):
            if ref.core_package is not None:
                if ref.core_package not in allowed_core:
                    out.append(_where(ref, core))
            elif ref.head not in STDLIB:
                out.append(_where(ref, core))
    return out


def engine_violations(
    package: str, core: Path = CORE, used: set[Allowed] | None = None
) -> list[str]:
    """Rule 2: an engine or support package never reaches outward, nor needs a vendor at import."""
    forbidden_core = OUTER_PACKAGES | {COMPOSITION_ROOT}
    if package in SUPPORT_PACKAGES:
        # Support packages sit beneath the engines they serve: they may not import them either.
        forbidden_core = forbidden_core | set(engine_packages(core))
    out: list[str] = []
    for module in _modules(package, core):
        for ref in imports_of(module, core):
            if ref.core_package is not None:
                if ref.core_package in forbidden_core:
                    out.append(_where(ref, core))
            elif (
                ref.head not in STDLIB
                and ref.module_scope
                and not _allowed("module-scope-vendor", ref, core, used)
            ):
                out.append(_where(ref, core))
    return out


def vendor_violations(core: Path = CORE, used: set[Allowed] | None = None) -> list[str]:
    """Rule 3: no vendor SDK at module scope anywhere in ``aura_core``."""
    out: list[str] = []
    for module in _modules("", core):
        for ref in imports_of(module, core):
            if (
                ref.head in VENDOR_MODULES
                and ref.module_scope
                and not _allowed("module-scope-vendor", ref, core, used)
            ):
                out.append(_where(ref, core))
    return out


def container_violations(core: Path = CORE) -> list[str]:
    """Rule 4: only the composition root may import the composition root."""
    out: list[str] = []
    for module in _modules("", core):
        if module == core / f"{COMPOSITION_ROOT}.py":
            continue
        for ref in imports_of(module, core):
            if ref.core_package == COMPOSITION_ROOT:
                out.append(_where(ref, core))
    return out


# ---------------------------------------------------------------------------------------------
# The fitness tests
# ---------------------------------------------------------------------------------------------


def test_every_package_is_classified() -> None:
    """Discovery found the tree, and every package lands in exactly one ring."""
    packages = discover_packages()
    assert set(CORE_LAYERS) | OUTER_PACKAGES | SUPPORT_PACKAGES | {COMPOSITION_ROOT} <= set(
        packages
    ), "a ring names a package that no longer exists -- update the classification"
    engines = set(engine_packages())
    missing = NAMED_ENGINES - engines
    assert not missing, f"CLAUDE.md names these as engines but they are not checked: {missing}"


@pytest.mark.parametrize("package", sorted(CORE_LAYERS))
def test_core_layers_import_only_stdlib(package: str) -> None:
    """The rule that makes the core testable without infrastructure."""
    violations = core_layer_violations(package)
    assert not violations, (
        "domain imports only the stdlib, ports only the stdlib and domain (ADR-001):\n  "
        + "\n  ".join(violations)
    )


@pytest.mark.parametrize("package", engine_packages() + sorted(SUPPORT_PACKAGES))
def test_engines_do_not_import_adapters(package: str) -> None:
    """Policy must not depend on infrastructure -- at any scope, absolute or relative."""
    violations = engine_violations(package)
    assert not violations, (
        f"{package} must not import adapters/api/container, nor a third-party module at "
        "module scope (ADR-001):\n  " + "\n  ".join(violations)
    )


def test_vendor_sdks_are_imported_lazily() -> None:
    """Adapters and api reach vendor SDKs inside the function that needs them, never at import.

    ADR-001: an unused adapter must never have to be installed, and importing ``aura_core``
    must never pull a driver into the process.
    """
    violations = vendor_violations()
    assert not violations, (
        "vendor SDKs must be imported lazily, inside a function (ADR-001):\n  "
        + "\n  ".join(violations)
    )


def test_only_the_composition_root_imports_the_container() -> None:
    violations = container_violations()
    assert not violations, (
        "nothing below container.py may import it -- it is the composition root:\n  "
        + "\n  ".join(violations)
    )


def test_vendor_list_covers_every_third_party_import() -> None:
    """:data:`VENDOR_MODULES` is complete, so the lazy-import rule cannot be dodged by novelty."""
    unclassified: list[str] = []
    for module in _modules(""):
        for ref in imports_of(module):
            if ref.core_package is not None or ref.head in STDLIB:
                continue
            if ref.head in VENDOR_MODULES or ref.head in LEGACY_MODULES:
                continue
            unclassified.append(_where(ref, CORE))
    assert not unclassified, (
        "third-party import not in VENDOR_MODULES -- classify it there:\n  "
        + "\n  ".join(unclassified)
    )


def test_allowlist_has_no_stale_entries() -> None:
    """An exception that is no longer needed is removed, not left to excuse a future regression."""
    used: set[Allowed] = set()
    vendor_violations(used=used)
    for package in engine_packages() + sorted(SUPPORT_PACKAGES):
        engine_violations(package, used=used)
    stale = [f"{a.rule}: {a.path} -> {a.module}" for a in ALLOWLIST if a not in used]
    assert not stale, "remove these ALLOWLIST entries, nothing needs them:\n  " + "\n  ".join(stale)


# ---------------------------------------------------------------------------------------------
# Self-tests: the walker catches what it claims to, in both import forms
# ---------------------------------------------------------------------------------------------


def _synthetic_core(tmp_path: Path, files: dict[str, str]) -> Path:
    core = tmp_path / "aura_core"
    for package in (*CORE_LAYERS, *OUTER_PACKAGES, *SUPPORT_PACKAGES, "kernel", "llm"):
        (core / package).mkdir(parents=True, exist_ok=True)
        (core / package / "__init__.py").write_text("")
    (core / "__init__.py").write_text("")
    (core / "container.py").write_text("from .adapters import thing\n")
    for rel, source in files.items():
        (core / rel).parent.mkdir(parents=True, exist_ok=True)
        (core / rel).write_text(textwrap.dedent(source))
    return core


@pytest.mark.parametrize(
    "statement",
    [
        "from aura_core.adapters.coding_fakes import Fake",
        "import aura_core.adapters.coding_fakes",
        "from aura_core import adapters",
        "from ..adapters.coding_fakes import Fake",
        "from .. import adapters",
        "from ..api import v3",
        "from aura_core.container import build",
        "from .. import container",
    ],
)
@pytest.mark.parametrize(
    "wrapping",
    [
        "{stmt}\n",
        "try:\n    {stmt}\nexcept ImportError:\n    pass\n",
        "class Engine:\n    {stmt}\n",
        "def build():\n    {stmt}\n",
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    {stmt}\n",
    ],
    ids=["module", "try", "class-body", "lazy", "type-checking"],
)
def test_walker_catches_engine_reaching_outward(
    tmp_path: Path, statement: str, wrapping: str
) -> None:
    core = _synthetic_core(tmp_path, {"kernel/bad.py": wrapping.format(stmt=statement)})
    assert engine_violations("kernel", core), f"missed: {statement!r} in {wrapping!r}"


@pytest.mark.parametrize(
    "statement",
    [
        "import qdrant_client",
        "from qdrant_client.models import PointStruct",
        "import asyncpg as pg",
    ],
)
@pytest.mark.parametrize(
    "wrapping",
    [
        "{stmt}\n",
        "try:\n    {stmt}\nexcept ImportError:\n    pass\n",
        "if True:\n    {stmt}\n",
        "class Store:\n    {stmt}\n",
    ],
    ids=["module", "try", "if", "class-body"],
)
def test_walker_catches_module_scope_vendor_import(
    tmp_path: Path, statement: str, wrapping: str
) -> None:
    core = _synthetic_core(tmp_path, {"adapters/store.py": wrapping.format(stmt=statement)})
    assert vendor_violations(core), f"missed: {statement!r} in {wrapping!r}"


def test_walker_permits_the_correct_patterns(tmp_path: Path) -> None:
    """Lazy vendor imports in adapters, type-only vendor imports, peer engines, inner layers."""
    core = _synthetic_core(
        tmp_path,
        {
            "adapters/store.py": """
                from typing import TYPE_CHECKING
                from ..domain import entities
                from aura_core.ports import storage
                if TYPE_CHECKING:
                    import asyncpg

                class Store:
                    async def _pool(self):
                        import asyncpg
                        return await asyncpg.create_pool()
            """,
            "kernel/ok.py": """
                import json
                from ..domain import entities
                from aura_core.ports import storage
                from ..llm import router
                from aura_core.observability import get_logger
                from aura_core import config

                def fetch():
                    import httpx
                    return httpx
            """,
            "ports/storage.py": "from ..domain import entities\nfrom aura_core.domain import x\n",
            "domain/entities.py": "import dataclasses\nfrom . import x\n",
        },
    )
    assert not engine_violations("kernel", core)
    assert not vendor_violations(core)
    assert not container_violations(core)
    assert not core_layer_violations("ports", core)
    assert not core_layer_violations("domain", core)


@pytest.mark.parametrize(
    ("package", "statement"),
    [
        ("domain", "from aura_core.ports import storage"),
        ("domain", "from ..ports import storage"),
        ("ports", "from aura_core.llm import router"),
        ("ports", "from ..adapters import store"),
        ("ports", "import pydantic"),
        ("domain", "def f():\n    import httpx\n"),
    ],
)
def test_walker_catches_core_layer_violation(tmp_path: Path, package: str, statement: str) -> None:
    core = _synthetic_core(tmp_path, {f"{package}/bad.py": statement + "\n"})
    assert core_layer_violations(package, core), f"missed: {statement!r} in {package}"


@pytest.mark.parametrize(
    "statement",
    ["from aura_core.container import build", "from ..container import build",
     "def f():\n    from aura_core import container\n"],
)
def test_walker_catches_container_import(tmp_path: Path, statement: str) -> None:
    core = _synthetic_core(tmp_path, {"api/bad.py": statement + "\n"})
    assert container_violations(core), f"missed: {statement!r}"


def test_discovery_treats_new_packages_as_engines(tmp_path: Path) -> None:
    core = _synthetic_core(tmp_path, {"brand_new/__init__.py": "", "brand_new/x.py": ""})
    assert "brand_new" in engine_packages(core)
    assert "container" not in engine_packages(core)
    assert "adapters" not in engine_packages(core)


def test_support_packages_may_not_import_engines(tmp_path: Path) -> None:
    core = _synthetic_core(tmp_path, {"config/bad.py": "from aura_core.kernel import AIKernel\n"})
    assert engine_violations("config", core)


def test_core_never_imports_legacy_services() -> None:
    """ADR-002: the strangler dependency is one-directional.

    AST-based rather than textual: several modules *reference* the legacy package in prose
    (explaining which defect they replace), and a grep-style check would flag those comments
    while still missing an aliased import.
    """
    forbidden = LEGACY_MODULES
    violations: list[str] = []

    for module in _modules(""):
        tree = ast.parse(module.read_text(encoding="utf-8"), str(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in forbidden:
                        violations.append(
                            f"{module.relative_to(CORE)}:{node.lineno} imports {alias.name}"
                        )
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                if node.module.split(".")[0] in forbidden:
                    violations.append(
                        f"{module.relative_to(CORE)}:{node.lineno} imports from {node.module}"
                    )
    assert not violations, (
        "aura_core must not import the legacy backend packages (ADR-002):\n  "
        + "\n  ".join(violations)
    )


def test_no_import_time_network_connections() -> None:
    """No module-level construction of clients or engines (TD-01).

    The legacy code opens a Qdrant connection at import (``services/rag.py:287``), which is why
    that process cannot start without its dependencies reachable and why its tests must
    monkey-patch globals. This asserts the pattern is not reintroduced.
    """
    suspicious = ("QdrantClient(", "create_async_engine(", "redis.Redis(", "httpx.AsyncClient(")
    violations: list[str] = []

    for module in _modules(""):
        source = module.read_text(encoding="utf-8")
        tree = ast.parse(source, str(module))
        for node in tree.body:  # module scope only
            if not isinstance(node, (ast.Assign, ast.Expr)):
                continue
            segment = ast.get_source_segment(source, node) or ""
            for marker in suspicious:
                if marker in segment:
                    violations.append(f"{module.relative_to(CORE)}:{node.lineno}: {marker}")
    assert not violations, (
        "clients must be constructed lazily, never at import time (TD-01):\n  "
        + "\n  ".join(violations)
    )


def test_ports_are_async() -> None:
    """Every I/O method on a port is a coroutine.

    The legacy concurrency ceiling of roughly four simultaneous requests comes entirely from
    synchronous clients called inside coroutines. An async-only port surface makes that mistake
    impossible above the adapter boundary.
    """
    violations: list[str] = []
    # Synchronous by design: pure accessors, the sync event-bus registration, the clock's
    # non-awaiting readers, and `stream`/`run_streaming`, which return an AsyncIterator rather
    # than awaiting (LLMProvider.stream, StreamingSandboxRunner.run_streaming).
    non_io = {"name", "model_id", "dimension", "max_batch_size", "capabilities", "spec",
              "subscribe", "now", "monotonic", "stream", "run_streaming"}

    for module in _modules("ports"):
        tree = ast.parse(module.read_text(encoding="utf-8"), str(module))
        for node in ast.walk(tree):
            # Only Protocol definitions are ports. Plain value objects in the same module
            # (AuditEvent, ToolContext) are data, and their serialisers are correctly sync.
            if not isinstance(node, ast.ClassDef):
                continue
            bases = {
                b.id if isinstance(b, ast.Name) else getattr(b, "attr", "") for b in node.bases
            }
            if "Protocol" not in bases:
                continue
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and not item.name.startswith("_"):
                    if item.name in non_io:
                        continue
                    decorators = {
                        d.id if isinstance(d, ast.Name) else getattr(d, "attr", "")
                        for d in item.decorator_list
                    }
                    if "property" in decorators or "staticmethod" in decorators:
                        continue
                    violations.append(f"{module.relative_to(CORE)}:{item.lineno} {node.name}.{item.name}")
    assert not violations, (
        "port I/O methods must be async (see docs/analysis/05-performance-report.md §2):\n  "
        + "\n  ".join(violations)
    )
