"""Tools that run project code in a child process of the gateway (ADR-036, group B).

``generate_and_run_tests`` and ``analyze_test_quality`` write the text the caller supplies into a
throwaway directory and run pytest (and coverage, and a boolean-mutation pass) against it. That is
code execution with no container boundary under it, which is why this group is the opposite of
``tools/analysis.py`` on every axis:

* **Off by default** (``AURA_OPEN_EXEC_TOOLS_ENABLED``) and registered only when an operator turns
  it on -- this is an accepted risk to be taken on purpose (GAP-066), not a feature that ships live.
* **``engineer_agent`` capability** and ``side_effecting`` at ``CONFIDENTIAL``, so ``ToolSpec``
  forces a confirmation on every call. Not ``workspace_local``: nothing here is confined to a
  scratch container.
* **Tagged ``untrusted_content``**: pytest output echoes the code it ran, so a turn that called
  these is tainted exactly as one that called ``run_command``.
* **Inline text only.** No parameter is a path, so a caller cannot point the runner at a file on
  the gateway, and the child's working directory is a fresh temp dir. The environment the child
  sees is a short allowlist (``tools/open/_process.py``), so the keys are not *handed* to it. That
  is hygiene, not a boundary: the child runs as the gateway's own user with no filesystem or
  network confinement, so code it runs can still read ``/proc/<gateway pid>/environ`` or the
  gateway's files. Enable the group only where that is an accepted risk.
* **At most two children at once**, so a burst of calls cannot exhaust the host.

The honest summary is in the ADR: this is containment by hygiene, not isolation. A Docker-backed
runner (``SandboxRunner``) is the proper home for this and replaces it when one is wired.
"""

from __future__ import annotations

import asyncio
import json
import keyword
import re
import tempfile
import threading
from pathlib import Path
from typing import Any

from ..domain.errors import ToolExecutionError
from ..domain.tools import ToolCategory, ToolSensitivity
from ..ports.runtime import Tool
from .base import param, tool
from .open.automated_test_generator_and_verifier import AutomatedTestGeneratorAndVerifier
from .open.test_coverage_and_mutation_analyzer import TestCoverageAndMutationAnalyzer
from .open.quality_ext import (
    KINDS as _MUTATION_KINDS, changed_line_coverage, changed_lines, evaluate_operator_mutants,
)
from .open.testgen_ext import generate_and_verify_with_behaviour

__all__ = ["build_open_exec_tools", "OPEN_EXEC_TOOL_NAMES"]

_ENGINEER_CAPABILITY = "engineer_agent"
_MAX_SOURCE = 100_000
_MODULE_PATH = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
_OUTPUT_CAP = 3_000
_RESERVED_MODULE_NAMES = frozenset({"test_generated", "conftest"})

#: Concurrent child processes across the whole gateway. A threading primitive on purpose: the work
#: happens in ``asyncio.to_thread`` workers, and an asyncio lock would bind to one event loop.
_SLOTS = threading.BoundedSemaphore(2)
_SLOT_WAIT_S = 30.0

_COMMON = dict(
    category=ToolCategory.DEVELOPMENT,
    sensitivity=ToolSensitivity.CONFIDENTIAL,
    side_effecting=True,
    required_capability=_ENGINEER_CAPABILITY,
    max_result_chars=16_000,
    tags=("untrusted_content",),
)


def _trim(value: Any) -> Any:
    """Cap every string in a result so the JSON stays valid instead of being cut mid-token."""
    if isinstance(value, str):
        return value if len(value) <= _OUTPUT_CAP else value[:_OUTPUT_CAP] + "…[truncated]"
    if isinstance(value, dict):
        return {k: _trim(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_trim(v) for v in value]
    return value


def _in_slot(name: str, fn: Any, *args: Any, **kwargs: Any) -> str:
    if not _SLOTS.acquire(timeout=_SLOT_WAIT_S):
        raise ToolExecutionError(
            "the test runner is busy with other calls; try again shortly", tool=name
        )
    try:
        return json.dumps(_trim(fn(*args, **kwargs)), ensure_ascii=False, default=str)
    finally:
        _SLOTS.release()


def _module_path(name: str, value: str | None) -> str | None:
    if value is None:
        return None
    if not _MODULE_PATH.fullmatch(value):
        raise ToolExecutionError(
            f"module_import_path must be a dotted Python identifier, got {value!r}", tool=name
        )
    return value


@tool(
    "generate_and_run_tests",
    "Generate structural pytest checks for Python source (it imports; each public function, class "
    "and method exists with the declared parameters) -- or run your own `custom_test_code` -- and "
    "execute them against the source in a throwaway directory. RUNS CODE as the gateway's own user "
    "(no sandbox). A pass means the module "
    "has its declared shape, not that it behaves correctly; custom tests mean what they assert.",
    param("source_code", "string", "The module under test.", max_length=_MAX_SOURCE),
    param("module_import_path", "string", "Dotted module name the tests import it as "
          "(default module_under_test).", required=False, max_length=200),
    param("custom_test_code", "string", "Your own pytest file instead of generated checks.",
          required=False, max_length=_MAX_SOURCE),
    param("include_call_smoke", "boolean", "Also CALL every no-argument function and instantiate "
          "no-argument classes (default false; only for code with no side effects).",
          required=False, default=False),
    param("include_behavior_tests", "boolean", "Also assert behaviour (default true): run each "
          "function's docstring examples, and for pure functions with simple type hints check the "
          "return type, determinism, no argument mutation and that only declared exceptions escape.",
          required=False, default=True),
    param("timeout_s", "number", "Hard limit in seconds for the test run (default 30).",
          required=False, default=30, minimum=5, maximum=60),
    timeout_s=95.0,
    **_COMMON,
)
async def generate_and_run_tests(
    source_code: str,
    module_import_path: str | None = None,
    custom_test_code: str | None = None,
    include_call_smoke: bool = False,
    timeout_s: float = 30,
    include_behavior_tests: bool = True,
) -> str:
    name = "generate_and_run_tests"
    module = _module_path(name, module_import_path)
    if include_behavior_tests and custom_test_code is None:
        return await asyncio.to_thread(
            _in_slot,
            name,
            generate_and_verify_with_behaviour,
            source_code,
            module_import_path=module,
            include_call_smoke=include_call_smoke,
            timeout_seconds=int(timeout_s),
        )
    return await asyncio.to_thread(
        _in_slot,
        name,
        AutomatedTestGeneratorAndVerifier.generate_and_verify,
        source_code,
        custom_test_code=custom_test_code,
        module_import_path=module,
        include_call_smoke=include_call_smoke,
        timeout_seconds=int(timeout_s),
    )


def _quality(
    source_code: str,
    test_code: str,
    module_name: str,
    operations: str,
    threshold: float,
    timeout_seconds: int,
    diff_text: str | None = None,
    mutation_kinds: tuple[str, ...] = _MUTATION_KINDS,
    max_mutants: int = 10,
) -> dict[str, Any]:
    result: dict[str, Any] = {"module": module_name}
    changed: set[int] | None = None
    if diff_text is not None:
        per_file = changed_lines(diff_text)
        if not per_file:
            return {**result, "error": "diff_text has no changed lines (is it a unified diff?)"}
        picked = per_file.get(f"{module_name}.py") or next(
            (v for k, v in per_file.items() if k.rsplit("/", 1)[-1] == f"{module_name}.py"), None)
        if picked is None and len(per_file) == 1:
            picked = next(iter(per_file.values()))
        if picked is None:
            return {**result, "error": f"diff_text names {sorted(per_file)}; none is {module_name}.py "
                                       "(name the module to match, or pass a one-file diff)"}
        changed = picked
        result["changed_lines"] = sorted(changed)
    with tempfile.TemporaryDirectory(prefix="aura-quality-") as tmp:
        root = Path(tmp)
        (root / f"{module_name}.py").write_text(source_code, encoding="utf-8")
        (root / "test_generated.py").write_text(test_code, encoding="utf-8")
        if operations in ("coverage", "both"):
            result["coverage"] = TestCoverageAndMutationAnalyzer.run_pytest_with_coverage(
                "test_generated.py",
                source_package=module_name,
                working_dir=str(root),
                threshold=threshold,
                timeout_seconds=timeout_seconds,
            )
            if changed is not None:
                scoped = changed_line_coverage(root, module_name, changed, timeout_seconds)
                pct = scoped.get("percent_covered")
                scoped["threshold"] = threshold
                scoped["passed_threshold"] = pct is not None and pct >= threshold
                result["changed_line_coverage"] = scoped
        if operations in ("mutation", "both"):
            result["mutation"] = evaluate_operator_mutants(
                root, f"{module_name}.py", mutation_kinds, changed, timeout_seconds, max_mutants)
    return result


@tool(
    "analyze_test_quality",
    "Measure how well a pytest file tests a Python module: line coverage under coverage.py and/or "
    "a mutation score (True/False, comparison, arithmetic and and/or operators flipped in turn, up to "
    "10 mutants, counting the ones the tests catch; give diff_text to scope both to the added lines). RUNS CODE, once for coverage and up to 11 more times for mutation; "
    "the tests must already pass on the unmodified source.",
    param("source_code", "string", "The module under test.", max_length=_MAX_SOURCE),
    param("test_code", "string", "The pytest file. Import the module by `module_name`.",
          max_length=_MAX_SOURCE),
    param("module_name", "string", "Importable name for the source (default module_under_test).",
          required=False, default="module_under_test", max_length=80),
    param("operations", "string", "What to measure (default both).", required=False,
          enum=("coverage", "mutation", "both"), default="both"),
    param("coverage_threshold", "number", "Coverage percentage that counts as passing "
          "(default 80).", required=False, default=80.0, minimum=0, maximum=100),
    param("diff_text", "string", "A unified diff of the module: coverage and mutants are then "
          "also scoped to its added lines.", required=False, max_length=_MAX_SOURCE),
    param("mutation_kinds", "array", "Mutant kinds: boolean, comparison, arithmetic, logical "
          "(default all).", required=False, item_type="string"),
    param("max_mutants", "integer", "Mutants to run (default 10, at most 20).", required=False,
          default=10, minimum=1, maximum=20),
    param("timeout_s", "number", "Hard limit in seconds for EACH pytest run (default 15).",
          required=False, default=15, minimum=5, maximum=20),
    timeout_s=270.0,
    **_COMMON,
)
async def analyze_test_quality(
    source_code: str,
    test_code: str,
    module_name: str = "module_under_test",
    operations: str = "both",
    coverage_threshold: float = 80.0,
    timeout_s: float = 15,
    diff_text: str | None = None,
    mutation_kinds: list | None = None,
    max_mutants: int = 10,
) -> str:
    name = "analyze_test_quality"
    kinds = tuple(str(k) for k in (mutation_kinds or _MUTATION_KINDS))
    unknown = [k for k in kinds if k not in _MUTATION_KINDS]
    if unknown:
        raise ToolExecutionError(f"unknown mutation kind(s) {unknown}; use {list(_MUTATION_KINDS)}", tool=name)
    if "." in module_name or _module_path(name, module_name) is None:
        raise ToolExecutionError("module_name must be a single Python identifier", tool=name)
    if module_name in _RESERVED_MODULE_NAMES or keyword.iskeyword(module_name):
        # `test_generated` is the file the tests are written to, so naming the module that would
        # overwrite the source with the tests and then measure the tests against themselves.
        raise ToolExecutionError(
            f"module_name {module_name!r} is reserved (it collides with the test file, pytest's "
            "conftest or a Python keyword); choose another name",
            tool=name,
        )
    return await asyncio.to_thread(
        _in_slot,
        name,
        _quality,
        source_code,
        test_code,
        module_name,
        operations,
        coverage_threshold,
        int(timeout_s),
        diff_text,
        kinds,
        int(max_mutants),
    )


_OPEN_EXEC_TOOLS: tuple[Tool, ...] = (generate_and_run_tests, analyze_test_quality)

OPEN_EXEC_TOOL_NAMES: frozenset[str] = frozenset(t.spec.name for t in _OPEN_EXEC_TOOLS)


def build_open_exec_tools() -> list[Tool]:
    """The project-code-running tools. Only registered when the operator enables the group."""
    return list(_OPEN_EXEC_TOOLS)
