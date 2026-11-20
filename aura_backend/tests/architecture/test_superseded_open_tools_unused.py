"""Fitness: the six ``tools/open`` classes ADR-036 marks *superseded* stay out of the system.

Each duplicates an engine the platform already has, in a weaker form (no owner scoping, no
approval gate, no real device, ...). They are kept as reference code, and what keeps them harmless
is that nothing wires them in: not the tool registry, not the container, not another module. This
test makes "kept for reference" a checked claim -- the day one of them is imported from
``aura_core``, somebody has to read the note at the top of the file and the ADR first.

Tests may import them (they are plain classes); ``aura_core`` may not.
"""

from __future__ import annotations

import ast
from pathlib import Path

AURA_CORE = Path(__file__).resolve().parents[2] / "aura_core"

SUPERSEDED_MODULES = {
    "knowledge_graph_manager",
    "model_registry_and_evaluation_manager",
    "workflow_dependency_validator",
    "agent_tool_test_harness",
    "data_lineage_provenance_tracker",
    "gpu_resource_scheduler",
}

#: Directories that are not project source (a stray interpreter install, caches, virtualenvs).
_SKIP_PARTS = {"__pycache__", ".venv", "venv", "Python", "node_modules", "site-packages"}


def _project_files() -> list[Path]:
    return [p for p in AURA_CORE.rglob("*.py") if not _SKIP_PARTS & set(p.relative_to(AURA_CORE).parts)]


def _imported_module_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            names.add(base)
            names.update(f"{base}.{alias.name}" if base else alias.name for alias in node.names)
    return names


def test_the_superseded_modules_exist_so_this_test_is_not_vacuous() -> None:
    present = {p.stem for p in (AURA_CORE / "tools" / "open").glob("*.py")}
    assert SUPERSEDED_MODULES <= present


def test_nothing_under_aura_core_imports_a_superseded_module() -> None:
    offenders: list[str] = []
    for path in _project_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for imported in _imported_module_names(tree):
            if SUPERSEDED_MODULES & set(imported.split(".")):
                offenders.append(f"{path.relative_to(AURA_CORE).as_posix()} imports {imported}")
    assert not offenders, (
        "a superseded tools/open class was wired into aura_core (ADR-036 keeps them as reference "
        "only; use the engine named in the note at the top of the file instead):\n  "
        + "\n  ".join(sorted(offenders))
    )


def test_each_superseded_module_says_so_and_names_what_replaces_it() -> None:
    for name in sorted(SUPERSEDED_MODULES):
        head = (AURA_CORE / "tools" / "open" / f"{name}.py").read_text(encoding="utf-8")[:1200]
        assert head.lstrip().startswith('"""Superseded'), f"{name}.py lost its Superseded note"
        assert "ADR-036" in head, f"{name}.py does not cite ADR-036"
        assert "`" in head, f"{name}.py names no replacement"


def test_no_superseded_class_is_a_registered_tool_name() -> None:
    from aura_core.adapters.coding_fakes import InMemorySandboxRunner
    from aura_core.tools.builtin import default_tools

    class _Desktop:
        async def resolve(self, user_id):
            return object()

    names = {
        t.spec.name
        for t in default_tools(
            sandbox_runner=InMemorySandboxRunner(),
            host_agent_registry=_Desktop(),
            enable_analysis_tools=True,
            enable_open_exec_tools=True,
            enable_desktop_ops_tools=True,
        )
    }
    forbidden_fragments = ("knowledge_graph", "model_registry", "gpu_", "lineage", "workflow_dependency", "tool_harness")
    assert not [n for n in names if any(f in n for f in forbidden_fragments)]
