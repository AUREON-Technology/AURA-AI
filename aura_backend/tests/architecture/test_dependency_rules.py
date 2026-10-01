"""Architectural fitness functions.

These enforce ADR-001 mechanically. A layering rule that lives only in a document decays within
weeks; one that fails the build does not.

Three rules:

1. ``domain/`` and ``ports/`` import **only** the standard library. This is what lets the whole
   cognitive core be unit-tested on bare CPython -- the property that makes this test suite
   runnable at all.
2. Engines never import adapters. Policy must not depend on infrastructure, or the dependency
   inversion is decorative.
3. ``aura_core`` never imports ``aura_backend.services`` or ``infrastructure`` (ADR-002's
   one-directional rule during the strangler migration; v2 SQLAlchemy stays outside the core).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

CORE = Path(__file__).resolve().parents[2] / "aura_core"

#: Modules that ship with CPython. ``sys.stdlib_module_names`` is authoritative on 3.10+ and
#: avoids maintaining a hand-written list that silently rots.
STDLIB = set(sys.stdlib_module_names) | {"__future__"}

#: Packages whose contents must not reach into infrastructure.
ENGINE_PACKAGES = (
    "prompts", "context", "memory", "knowledge", "tools", "safety",
    "planning", "reasoning", "execution", "kernel", "evaluation", "learning",
    "mlops", "security", "rag", "nlp", "voice", "cag", "adaptation",
)

#: Where vendor SDKs are permitted.
ADAPTER_PACKAGES = ("adapters", "api")


def _modules(package: str) -> list[Path]:
    root = CORE / package if package else CORE
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _imports(path: Path) -> list[tuple[str, int]]:
    """Top-level module names imported by a file, with line numbers.

    Imports inside a function body are excluded: lazy vendor imports are the pattern the
    architecture *requires* of adapters, and flagging them would penalise the correct design.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    found: list[tuple[str, int]] = []

    def visit(node: ast.AST, inside_function: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, True)
                continue
            if not inside_function:
                if isinstance(child, ast.Import):
                    for alias in child.names:
                        found.append((alias.name.split(".")[0], child.lineno))
                elif isinstance(child, ast.ImportFrom):
                    if child.level == 0 and child.module:
                        found.append((child.module.split(".")[0], child.lineno))
            visit(child, inside_function)

    visit(tree, False)
    return found


@pytest.mark.parametrize("package", ["domain", "ports"])
def test_core_layers_import_only_stdlib(package: str) -> None:
    """The rule that makes the core testable without infrastructure."""
    violations: list[str] = []
    for module in _modules(package):
        for name, line in _imports(module):
            if name in STDLIB or name == "aura_core":
                continue
            violations.append(f"{module.relative_to(CORE)}:{line} imports {name!r}")
    assert not violations, (
        "domain and ports must import only the standard library (ADR-001):\n  "
        + "\n  ".join(violations)
    )


@pytest.mark.parametrize("package", ENGINE_PACKAGES)
def test_engines_do_not_import_adapters(package: str) -> None:
    """Policy must not depend on infrastructure at module scope."""
    violations: list[str] = []
    for module in _modules(package):
        source = module.read_text(encoding="utf-8")
        tree = ast.parse(source, str(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level > 0 and node.module:
                head = node.module.split(".")[0]
                if head in ADAPTER_PACKAGES:
                    # Only module-scope imports violate the rule; a lazy import inside a
                    # function is how optional infrastructure is meant to be reached.
                    if node.col_offset == 0:
                        violations.append(
                            f"{module.relative_to(CORE)}:{node.lineno} imports {node.module}"
                        )
    assert not violations, (
        "engines must not import adapters at module scope (ADR-001):\n  " + "\n  ".join(violations)
    )


def test_core_never_imports_legacy_services() -> None:
    """ADR-002: the strangler dependency is one-directional.

    AST-based rather than textual: several modules *reference* the legacy package in prose
    (explaining which defect they replace), and a grep-style check would flag those comments
    while still missing an aliased import.
    """
    forbidden = {"services", "aura_backend", "main", "tasks", "celery_app", "infrastructure"}
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
