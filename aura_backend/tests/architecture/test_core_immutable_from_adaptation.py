"""The adaptation layer never reaches into the core it adapts (ADR-018, AP7).

A deployed blueprint changes *data* the core already executes -- an agent snapshot, registered
tools -- and nothing else. This test reads the code (AST) to keep it that way:

* ``aura_core/adaptation/`` imports only the domain, ports, observability, its own package, the
  agent registry's data types and the approval service. Never the kernel, the tool policy or
  registry implementation, safety, the API, the container or an adapter at module scope.
* Nothing in it assigns to a policy, a kernel dependency or a registry's internals.
* A generated agent can never be unrestricted: :func:`generated_agent_spec` and
  :meth:`ActiveBlueprint.swap` both refuse ``allowed_tools=None``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

CORE = Path(__file__).resolve().parents[2] / "aura_core"
ADAPTATION = CORE / "adaptation"

#: What the adaptation layer may import from the rest of aura_core (module paths, prefix match).
ALLOWED = (
    "domain", "ports", "observability", "adaptation",
    "agents.registry",      # AgentSpec / AgentRegistry -- data types, for ActiveBlueprint
    "learning.approval",    # the approval service the lifecycle consumes
)
FORBIDDEN_ATTRIBUTES = {"_policy", "policy", "_deps", "deps", "_tools", "_by_id"}


def _relative_imports(path: Path, source: str | None = None):
    tree = ast.parse(source if source is not None else path.read_text(encoding="utf-8"))
    package = path.relative_to(CORE).parts[:-1]
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level > 0:
            base = list(package)[: len(package) - (node.level - 1)] if node.level > 1 else list(package)
            module = ".".join([*base, node.module] if node.module else base)
            yield module, node.lineno
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("aura_core"):
            yield node.module.removeprefix("aura_core."), node.lineno


def test_adaptation_imports_only_what_it_may() -> None:
    offenders = []
    for path in sorted(ADAPTATION.rglob("*.py")):
        for module, line in _relative_imports(path):
            if not module.startswith(ALLOWED):
                offenders.append(f"{path.relative_to(CORE)}:{line} imports {module}")
    assert not offenders, "the adaptation layer may not reach into the core (ADR-018):\n  " + "\n  ".join(offenders)


def test_adaptation_never_writes_core_internals() -> None:
    offenders = []
    for path in sorted(ADAPTATION.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, (ast.AugAssign, ast.AnnAssign)) else []
            for target in targets:
                # `self._tools = tools` in a constructor is holding a reference, not writing into it.
                if isinstance(target, ast.Attribute) and target.attr in FORBIDDEN_ATTRIBUTES:
                    owner = target.value
                    if not (isinstance(owner, ast.Name) and owner.id == "self"):
                        offenders.append(f"{path.relative_to(CORE)}:{node.lineno} writes .{target.attr}")
                if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Attribute) \
                        and target.value.attr in FORBIDDEN_ATTRIBUTES:
                    offenders.append(f"{path.relative_to(CORE)}:{node.lineno} writes into .{target.value.attr}")
    assert not offenders, "\n  ".join(offenders)


def test_the_checker_flags_a_kernel_import() -> None:
    source = "from ...kernel.kernel import AIKernel\nfrom ...tools.policy import ToolPolicy\n"
    found = [m for m, _ in _relative_imports(ADAPTATION / "factories" / "probe.py", source)]
    assert found == ["kernel.kernel", "tools.policy"]
    assert not any(m.startswith(ALLOWED) for m in found)


def test_generated_agents_are_never_unrestricted() -> None:
    from aura_core.adaptation.runtime import ActiveBlueprint
    from aura_core.agents.registry import AgentSpec, build_default_agent_registry
    from aura_core.domain.errors import ValidationError

    agents = ActiveBlueprint(build_default_agent_registry())
    with pytest.raises(ValidationError):
        agents.swap([AgentSpec("bp_all", "All", "system.blueprint_agent", allowed_tools=None)],
                    version=1, digest="sha256:" + "0" * 64)
