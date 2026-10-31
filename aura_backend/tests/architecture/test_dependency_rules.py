"""Architectural fitness functions.

These enforce ADR-001 mechanically. A layering rule that lives only in a document decays within
weeks; one that fails the build does not.

The arrow CLAUDE.md states, and which this module enforces over **every** ``aura_core``
subpackage (discovered from the filesystem, so a new package is covered the day it lands)::

    domain/  ports/      stdlib only, and only each other
    config/  observability/
                         stdlib, domain, ports and each other; optional third-party only via
                         the explicit allowlist below
    engines              every other subpackage (llm, kernel, knowledge, memory, tools, ...):
                         domain, ports, config, observability and other engines -- never
                         adapters, api or container, at any scope
    adapters/  api/      anything internal except the composition root; vendor SDKs only lazily
                         inside functions, never at module scope
    container.py         the only wiring point: nothing inside aura_core imports it

Plus ``aura_core`` never imports ``aura_backend.services`` or ``infrastructure`` (ADR-002's
one-directional rule during the strangler migration; v2 SQLAlchemy stays outside the core).

F-28 / AR-09 (audit 2026-10-26): the earlier version only looked at *relative* imports, only
treated ``col_offset == 0`` as module scope (so a ``try:``/``if:`` import escaped), never checked
engines for vendor SDKs, and used a hand-written engine list that omitted ``llm`` among others.
Every import here is resolved to its absolute dotted name first, so ``from ..adapters import x``,
``from aura_core.adapters.x import y`` and ``from aura_core import adapters`` are the same edge.
The self-tests at the bottom plant each of those shapes in a scratch tree and prove they fail.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

CORE = Path(__file__).resolve().parents[2] / "aura_core"

#: Modules that ship with CPython. ``sys.stdlib_module_names`` is authoritative on 3.10+ and
#: avoids maintaining a hand-written list that silently rots.
STDLIB = set(sys.stdlib_module_names) | {"__future__"}

PURE_LAYERS = frozenset({"domain", "ports"})
SUPPORT_LAYERS = frozenset({"config", "observability"})
WIRING_LAYERS = frozenset({"adapters", "api", "container"})
ROOT_LAYER = "__init__"  # aura_core/__init__.py -- version string only

#: Vendor SDKs and frameworks that may never be imported at module scope anywhere in
#: ``aura_core`` -- not in engines, and not in adapters/api/container either, where CLAUDE.md
#: requires them to be imported lazily inside the function that first needs them. Importing one
#: at module scope makes the whole package unimportable without that dependency installed and,
#: for the network clients, is one step away from an import-time connection (TD-01).
VENDOR_SDKS = frozenset({
    # storage and messaging
    "asyncpg", "psycopg", "psycopg2", "redis", "qdrant_client", "sqlalchemy", "alembic",
    "celery", "kombu", "boto3", "botocore", "firebase_admin", "google",
    # HTTP clients and servers
    "httpx", "requests", "aiohttp", "urllib3", "websockets", "fastapi", "starlette", "uvicorn",
    "pydantic", "grpc",
    # model runtimes and hosted-model SDKs
    "openai", "anthropic", "cohere", "mistralai", "groq", "ollama", "torch", "torchvision",
    "transformers", "sentence_transformers", "tiktoken", "vllm", "llama_cpp", "whisper",
    "faster_whisper", "piper",
    # sandbox, documents, media
    "docker", "pypdf", "PIL", "pytesseract", "cryptography", "jwt", "github", "yaml",
})

#: Third-party imports that may run at module scope. Each entry is ``(path relative to
#: aura_core, top-level module) -> reason``. Everything not listed fails, whether or not it is
#: in VENDOR_SDKS -- the denylist names the dangerous ones explicitly, this catches the rest.
MODULE_SCOPE_THIRD_PARTY_ALLOWLIST: dict[tuple[str, str], str] = {
    ("observability/metrics.py", "prometheus_client"): (
        "optional dependency inside try/except ImportError; metrics degrade to no-ops "
        "without it (AR-09 'not violations')"
    ),
    ("observability/tracing.py", "opentelemetry"): (
        "optional dependency inside try/except ImportError; tracing degrades to no-ops "
        "without it (AR-09 'not violations')"
    ),
}

#: Internal layering exceptions: ``(path relative to aura_core, imported aura_core module)``
#: -> reason. Each entry is debt with a finding id, not a sanctioned pattern.
LAYER_ALLOWLIST: dict[tuple[str, str], str] = {
    ("engineering2/pipeline.py", "aura_core.adapters.coding_fakes"): (
        "F-28/AR-09: EngineerExecutionPipeline defaults its sandbox runner and GitHub client to "
        "the in-memory fakes. engineering2 has zero importers and is slated for deletion "
        "(F-29/AR-10, CB-13); changing its constructor contract here would be churn on dead "
        "code. Delete this entry together with the package."
    ),
}


@dataclass(frozen=True)
class ImportEdge:
    source: str  # path relative to the core root, posix
    layer: str  # layer of the importing file
    target: str  # fully resolved dotted module name
    line: int
    scope: str  # "module", "lazy" (inside a function) or "type_checking"

    @property
    def top(self) -> str:
        return self.target.split(".")[0]

    def __str__(self) -> str:
        return f"{self.source}:{self.line} imports {self.target} ({self.scope} scope)"


def _layer_of(core: Path, path: Path) -> str:
    rel = path.relative_to(core)
    return rel.parts[0] if len(rel.parts) > 1 else rel.stem


def _target_layer(core: Path, dotted: str) -> str | None:
    """The aura_core layer a dotted name points into, or None if it is not aura_core."""
    parts = dotted.split(".")
    if parts[0] != core.name:
        return None
    return parts[1] if len(parts) > 1 else ROOT_LAYER


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _resolve(core: Path, path: Path, node: ast.Import | ast.ImportFrom) -> list[str]:
    """Absolute dotted names for one import statement."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if node.level == 0:
        base = node.module or ""
    else:
        # The package a relative import is anchored at: the file's own directory, for both a
        # plain module and an ``__init__.py``.
        package = list(path.relative_to(core.parent).with_suffix("").parts[:-1])
        anchor = package[: len(package) - (node.level - 1)]
        base = ".".join(anchor + ([node.module] if node.module else []))
    # ``from aura_core import adapters`` / ``from .. import adapters`` name the submodule only
    # in the alias list; expand those so the edge points at the real layer.
    if base == core.name or node.module is None:
        return [f"{base}.{alias.name}" for alias in node.names]
    return [base]


def import_edges(core: Path, path: Path) -> list[ImportEdge]:
    """Every import in ``path``, resolved, with the scope it executes in.

    Module scope means "runs when the module is imported": that includes ``try``/``if``/``with``
    bodies and class bodies, not just column-0 statements. Function and lambda bodies are lazy
    (the pattern CLAUDE.md requires for vendor SDKs); ``if TYPE_CHECKING:`` bodies never run.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    rel = path.relative_to(core).as_posix()
    layer = _layer_of(core, path)
    edges: list[ImportEdge] = []

    def visit(node: ast.AST, scope: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                visit(child, "lazy" if scope == "module" else scope)
                continue
            if isinstance(child, ast.If) and _is_type_checking(child.test):
                for stmt in child.body:
                    visit(ast.Module(body=[stmt], type_ignores=[]), "type_checking")
                for stmt in child.orelse:
                    visit(ast.Module(body=[stmt], type_ignores=[]), scope)
                continue
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                for target in _resolve(core, path, child):
                    edges.append(ImportEdge(rel, layer, target, child.lineno, scope))
            visit(child, scope)

    visit(tree, "module")
    return edges


def _modules(core: Path, package: str = "") -> list[Path]:
    root = core / package if package else core
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def all_edges(core: Path) -> list[ImportEdge]:
    return [edge for module in _modules(core) for edge in import_edges(core, module)]


def layers(core: Path) -> dict[str, str]:
    """Every layer present under ``core`` mapped to its kind. Engines are whatever is left."""
    found = {_layer_of(core, p) for p in _modules(core)}
    kinds: dict[str, str] = {}
    for name in found:
        if name in PURE_LAYERS:
            kinds[name] = "pure"
        elif name in SUPPORT_LAYERS:
            kinds[name] = "support"
        elif name in WIRING_LAYERS:
            kinds[name] = "wiring"
        elif name == ROOT_LAYER:
            kinds[name] = "root"
        else:
            kinds[name] = "engine"
    return kinds


def layering_violations(core: Path) -> list[ImportEdge]:
    """Internal edges that point the wrong way."""
    kinds = layers(core)
    violations: list[ImportEdge] = []
    for edge in all_edges(core):
        target = _target_layer(core, edge.target)
        if target is None:
            continue
        source_kind = kinds.get(edge.layer, "engine")
        target_kind = kinds.get(target, "engine" if target not in WIRING_LAYERS else "wiring")
        if target == ROOT_LAYER:
            continue
        if target == "container" and edge.layer != "container":
            violations.append(edge)  # the composition root is imported only by entry points
        elif source_kind == "pure" and target_kind != "pure":
            violations.append(edge)
        elif source_kind == "support" and target_kind not in ("pure", "support"):
            violations.append(edge)
        elif source_kind == "engine" and target_kind == "wiring":
            violations.append(edge)
        elif edge.layer == "adapters" and target == "api":
            violations.append(edge)  # infrastructure never depends on the HTTP surface
    return violations


def third_party_edges(core: Path) -> list[ImportEdge]:
    return [
        e for e in all_edges(core)
        if e.top not in STDLIB and _target_layer(core, e.target) is None
    ]


def pure_layer_violations(core: Path) -> list[ImportEdge]:
    """domain/ports importing anything but the stdlib, at any scope (even lazily)."""
    kinds = layers(core)
    return [e for e in third_party_edges(core) if kinds.get(e.layer) == "pure"]


def vendor_sdk_violations(core: Path) -> list[ImportEdge]:
    """A denylisted vendor SDK imported at module scope anywhere."""
    return [
        e for e in third_party_edges(core) if e.scope == "module" and e.top in VENDOR_SDKS
    ]


def module_scope_third_party(core: Path) -> list[ImportEdge]:
    """Any third-party import that runs at import time."""
    return [e for e in third_party_edges(core) if e.scope == "module"]


# --------------------------------------------------------------------------------------------
# The rules, applied to the real tree
# --------------------------------------------------------------------------------------------


def _fmt(edges: list[ImportEdge]) -> str:
    return "\n  ".join(str(e) for e in edges)


def test_layer_discovery_covers_the_whole_tree() -> None:
    """Guard against the check passing because it scanned nothing (a moved path, a typo)."""
    kinds = layers(CORE)
    assert {"domain", "ports", "adapters", "api", "container"} <= set(kinds)
    # CLAUDE.md lists llm as an engine; the old hand-written list omitted it (AR-09 gap 4).
    for engine in ("llm", "kernel", "knowledge", "memory", "tools", "agents", "triggers"):
        assert kinds.get(engine) == "engine", engine
    assert sum(1 for k in kinds.values() if k == "engine") >= 20
    assert len(all_edges(CORE)) > 500


def test_core_layers_import_only_stdlib() -> None:
    """The rule that makes the core testable without infrastructure (any scope)."""
    violations = pure_layer_violations(CORE)
    assert not violations, (
        "domain and ports must import only the standard library (ADR-001):\n  "
        + _fmt(violations)
    )


def test_internal_layering_arrow() -> None:
    """Engines never reach adapters/api/container; domain/ports reach nothing outward."""
    violations = [
        e for e in layering_violations(CORE) if (e.source, e.target) not in LAYER_ALLOWLIST
    ]
    assert not violations, (
        "aura_core layering violated (ADR-001; engines -> domain + ports only, container.py is "
        "the only wiring point):\n  " + _fmt(violations)
    )


def test_vendor_sdks_never_imported_at_module_scope() -> None:
    """Vendor SDKs are imported lazily inside functions, in adapters/api as everywhere else."""
    violations = vendor_sdk_violations(CORE)
    assert not violations, (
        "vendor SDKs must be imported lazily inside the function that needs them, never at "
        "module scope (CLAUDE.md, ADR-001):\n  " + _fmt(violations)
    )


def test_module_scope_third_party_imports_are_allowlisted() -> None:
    """Catches the vendor a denylist forgot to name."""
    violations = [
        e for e in module_scope_third_party(CORE)
        if (e.source, e.top) not in MODULE_SCOPE_THIRD_PARTY_ALLOWLIST
    ]
    assert not violations, (
        "third-party import at module scope; import it lazily, or add an allowlist entry with "
        "a reason:\n  " + _fmt(violations)
    )


def test_allowlists_have_no_stale_entries() -> None:
    """An allowlist entry that no longer matches anything is a hole waiting to be reused."""
    live_third_party = {(e.source, e.top) for e in module_scope_third_party(CORE)}
    stale = [k for k in MODULE_SCOPE_THIRD_PARTY_ALLOWLIST if k not in live_third_party]
    live_layering = {(e.source, e.target) for e in layering_violations(CORE)}
    stale += [k for k in LAYER_ALLOWLIST if k not in live_layering]
    assert not stale, f"remove stale allowlist entries: {stale}"
    for key, reason in {**MODULE_SCOPE_THIRD_PARTY_ALLOWLIST, **LAYER_ALLOWLIST}.items():
        assert reason.strip(), f"allowlist entry {key} needs a reason"


def test_core_never_imports_legacy_services() -> None:
    """ADR-002: the strangler dependency is one-directional.

    AST-based rather than textual: several modules *reference* the legacy package in prose
    (explaining which defect they replace), and a grep-style check would flag those comments
    while still missing an aliased import.
    """
    forbidden = {"services", "aura_backend", "main", "tasks", "celery_app", "infrastructure"}
    violations = [e for e in all_edges(CORE) if e.top in forbidden]
    assert not violations, (
        "aura_core must not import the legacy backend packages (ADR-002):\n  " + _fmt(violations)
    )


def test_no_import_time_network_connections() -> None:
    """No module-level construction of clients or engines (TD-01).

    The legacy code opens a Qdrant connection at import (``services/rag.py:287``), which is why
    that process cannot start without its dependencies reachable and why its tests must
    monkey-patch globals. This asserts the pattern is not reintroduced.
    """
    suspicious = ("QdrantClient(", "create_async_engine(", "redis.Redis(", "httpx.AsyncClient(")
    violations: list[str] = []

    for module in _modules(CORE):
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

    for module in _modules(CORE, "ports"):
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


# --------------------------------------------------------------------------------------------
# Self-tests: a planted violation of each shape AR-09 named must be caught
# --------------------------------------------------------------------------------------------


@pytest.fixture
def scratch_core(tmp_path: Path) -> Path:
    core = tmp_path / "aura_core"
    files = {
        "__init__.py": "",
        "container.py": "from aura_core.adapters.store import Store\n",
        "domain/__init__.py": "",
        "domain/entities.py": "import dataclasses\n",
        "ports/__init__.py": "from ..domain import entities\n",
        "adapters/__init__.py": "",
        "adapters/store.py": "def connect():\n    import asyncpg\n    return asyncpg\n",
        "api/__init__.py": "",
        "kernel/__init__.py": "",
        "kernel/clean.py": (
            "from typing import TYPE_CHECKING\n"
            "from aura_core.domain import entities\n"
            "from ..ports import x\n"
            "if TYPE_CHECKING:\n"
            "    import httpx\n"
            "def lazy():\n"
            "    import torch\n"
        ),
    }
    for rel, text in files.items():
        path = core / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return core


def _plant(core: Path, rel: str, text: str) -> None:
    path = core / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_selftest_clean_scratch_tree_passes(scratch_core: Path) -> None:
    assert layering_violations(scratch_core) == []
    assert vendor_sdk_violations(scratch_core) == []
    assert module_scope_third_party(scratch_core) == []
    assert pure_layer_violations(scratch_core) == []


@pytest.mark.parametrize(
    "source",
    [
        "from aura_core.adapters.store import Store\n",  # absolute (AR-09 gap 1)
        "from aura_core import adapters\n",  # absolute, submodule in alias list
        "import aura_core.adapters.store\n",
        "from ..adapters.store import Store\n",  # relative
        "from .. import adapters\n",
        "try:\n    from aura_core.adapters.store import Store\nexcept ImportError:\n    pass\n",
        "def f():\n    from aura_core.adapters.store import Store\n",  # lazy is still a dependency
        "from aura_core.container import build\n",
        "from aura_core.api import routes\n",
    ],
)
def test_selftest_engine_to_wiring_import_is_caught(scratch_core: Path, source: str) -> None:
    # A package the old hand-written list did not name: discovery must treat it as an engine.
    _plant(scratch_core, "brand_new_engine/__init__.py", "")
    _plant(scratch_core, "brand_new_engine/planted.py", source)
    found = layering_violations(scratch_core)
    assert [e.source for e in found] == ["brand_new_engine/planted.py"], found


@pytest.mark.parametrize(
    "source",
    [
        "import httpx\n",  # module scope (AR-09 gap 3)
        "import qdrant_client.http.models\n",
        "from sentence_transformers import SentenceTransformer\n",
        "try:\n    import redis\nexcept ImportError:\n    redis = None\n",  # AR-09 gap 2
        "if True:\n    from asyncpg import Pool\n",
        "class Holder:\n    import torch\n",
    ],
)
def test_selftest_module_scope_vendor_import_in_engine_is_caught(
    scratch_core: Path, source: str
) -> None:
    _plant(scratch_core, "llm/__init__.py", "")
    _plant(scratch_core, "llm/planted.py", source)
    vendor = vendor_sdk_violations(scratch_core)
    assert [e.source for e in vendor] == ["llm/planted.py"], vendor
    assert [e.source for e in module_scope_third_party(scratch_core)] == ["llm/planted.py"]


def test_selftest_module_scope_vendor_import_in_adapter_is_caught(scratch_core: Path) -> None:
    _plant(scratch_core, "adapters/eager.py", "import docker\n")
    assert [e.source for e in vendor_sdk_violations(scratch_core)] == ["adapters/eager.py"]


def test_selftest_unlisted_third_party_is_caught(scratch_core: Path) -> None:
    """Not in VENDOR_SDKS, still not allowed at module scope without an allowlist entry."""
    _plant(scratch_core, "kernel/numeric.py", "import numpy\n")
    assert vendor_sdk_violations(scratch_core) == []
    assert [e.source for e in module_scope_third_party(scratch_core)] == ["kernel/numeric.py"]


@pytest.mark.parametrize(
    "rel, source",
    [
        ("domain/bad.py", "def f():\n    import pydantic\n"),  # even lazily
        ("ports/bad.py", "import numpy\n"),
    ],
)
def test_selftest_pure_layer_third_party_is_caught(
    scratch_core: Path, rel: str, source: str
) -> None:
    _plant(scratch_core, rel, source)
    assert [e.source for e in pure_layer_violations(scratch_core)] == [rel]


@pytest.mark.parametrize(
    "rel, source",
    [
        ("domain/bad.py", "from aura_core.kernel import clean\n"),
        ("ports/bad.py", "from ..adapters import store\n"),
        ("config/__init__.py", "from aura_core.kernel import clean\n"),
        ("adapters/bad.py", "from aura_core.api import routes\n"),
    ],
)
def test_selftest_outward_edges_from_inner_layers_are_caught(
    scratch_core: Path, rel: str, source: str
) -> None:
    _plant(scratch_core, rel, source)
    assert [e.source for e in layering_violations(scratch_core)] == [rel]
