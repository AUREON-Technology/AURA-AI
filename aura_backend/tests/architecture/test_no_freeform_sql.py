"""No free-form SQL reaches a connected system (ADR-022, AP6).

A generated tool reads through a compiled :class:`~aura_core.domain.templates.ReadTemplate`,
rendered by :func:`~aura_core.domain.templates.render_sql`: identifiers from the twin, values
bound. Nothing -- above all no model output -- may build SQL text any other way. This test reads
the code (AST) rather than trusting review:

* the adaptation engine (``aura_core/adaptation/``) makes no driver call at all -- it produces
  data; executing it is an adapter's job. The one method-name collision is listed in
  ``NOT_DRIVER_CALLS``: the evaluation gate (AP8) dispatching a candidate's tool through
  ``ToolRegistry.execute``, which reaches a system only via ``ReadConnector.read``;
* in ``aura_core/adapters/connectors/``, the statement passed to a driver call is a string
  literal, an UPPER_CASE module constant, or the ``.text`` of ``render_sql(...)`` -- except in
  the functions listed in ``REVIEWED_BUILDERS``, each of which interpolates only quoted catalog
  identifiers (``_sql_ident``) and integers, never caller-supplied text. Adding a function to
  that list is a security review, not a test fix.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "aura_core"
DRIVER_METHODS = {"fetch", "fetchrow", "fetchval", "execute", "executemany", "prepare", "cursor", "copy_from_query"}
_CONSTANT = re.compile(r"^[A-Z][A-Z0-9_]*$")

#: (file, function) -> why it may build SQL text. Reviewed: interpolations are `_sql_ident(...)`
#: of catalog names the same transaction just read, or integers -- see postgres_catalog.py.
REVIEWED_BUILDERS = {
    ("postgres_catalog.py", "capture_catalog"): "SET LOCAL statement_timeout from an int()",
    ("postgres_catalog.py", "_profile_table"): "profiling aggregates over _sql_ident-quoted catalog names",
}


#: (file, function) -> why a call named like a driver method is not one. Reviewed like the above.
NOT_DRIVER_CALLS = {
    ("gate.py", "_execute"): "ToolRegistry.execute: validation, ToolPolicy, taint, audit; the tool "
                             "reads only through ReadConnector.read with a compiled template",
}


def _driver_calls(tree: ast.AST):
    """(enclosing function name, call node) for every `<x>.<driver method>(...)` call."""
    def visit(node: ast.AST, function: str):
        for child in ast.iter_child_nodes(node):
            name = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else function
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) \
                    and child.func.attr in DRIVER_METHODS:
                yield function, child
            yield from visit(child, name)
    yield from visit(tree, "<module>")


def _rendered_names(tree: ast.AST) -> set[str]:
    """Names assigned from `render_sql(...)` anywhere in the module."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            func = node.value.func
            called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if called == "render_sql":
                names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return names


def _loop_constant_names(function_body: ast.AST) -> set[str]:
    """`for key, sql in ((..., CONST), ...)` -- a loop variable that only ever holds constants."""
    names: set[str] = set()
    for node in ast.walk(function_body):
        if isinstance(node, ast.For) and isinstance(node.iter, (ast.Tuple, ast.List)):
            items = node.iter.elts
            targets = node.target.elts if isinstance(node.target, ast.Tuple) else [node.target]
            for position, target in enumerate(targets):
                if not isinstance(target, ast.Name):
                    continue
                values = [
                    (item.elts[position] if isinstance(item, ast.Tuple) and len(item.elts) > position else item)
                    for item in items
                ]
                if all(isinstance(v, ast.Name) and _CONSTANT.match(v.id) for v in values):
                    names.add(target.id)
    return names


def _safe_statement(arg: ast.expr, rendered: set[str], constant_loops: set[str]) -> bool:
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return True
    if isinstance(arg, ast.Name) and (_CONSTANT.match(arg.id) or arg.id in constant_loops):
        return True
    if isinstance(arg, ast.Attribute) and arg.attr == "text":
        base = arg.value
        if isinstance(base, ast.Name) and base.id in rendered:
            return True
        if isinstance(base, ast.Call) and getattr(base.func, "id", getattr(base.func, "attr", "")) == "render_sql":
            return True
    return False


def test_the_adaptation_engine_never_calls_a_driver() -> None:
    offenders = []
    for path in sorted((CORE / "adaptation").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for function, call in _driver_calls(tree):
            if (path.name, function) in NOT_DRIVER_CALLS:
                continue
            offenders.append(f"{path.relative_to(CORE)}:{call.lineno} ({function}) calls .{call.func.attr}()")
    assert not offenders, "the adaptation engine produces data; adapters execute it:\n  " + "\n  ".join(offenders)


def test_connectors_only_execute_literal_or_rendered_sql() -> None:
    offenders = []
    seen_builders = set()
    for path in sorted((CORE / "adapters" / "connectors").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rendered = _rendered_names(tree)
        functions = {
            node.name: node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for function, call in _driver_calls(tree):
            if not call.args:
                continue
            if (path.name, function) in REVIEWED_BUILDERS:
                seen_builders.add((path.name, function))
                continue
            loops = _loop_constant_names(functions[function]) if function in functions else set()
            if not _safe_statement(call.args[0], rendered, loops):
                offenders.append(f"{path.relative_to(CORE)}:{call.lineno} ({function})")
    assert not offenders, (
        "SQL reaching a connected system must be a literal, an UPPER_CASE constant or render_sql() "
        "output (ADR-022):\n  " + "\n  ".join(offenders)
    )
    # A reviewed builder that no longer exists should leave the allowlist with it.
    assert seen_builders == set(REVIEWED_BUILDERS), sorted(set(REVIEWED_BUILDERS) - seen_builders)


def test_the_checker_catches_an_interpolated_statement() -> None:
    sample = ast.parse(
        "async def bad(conn, name):\n"
        "    await conn.fetch(f'SELECT * FROM {name}')\n"
        "async def good(conn, template, args):\n"
        "    rendered = render_sql(template, args)\n"
        "    await conn.fetch(rendered.text, *rendered.values)\n"
        "    await conn.fetch('SELECT 1')\n"
    )
    rendered = _rendered_names(sample)
    verdicts = {fn: _safe_statement(call.args[0], rendered, set()) for fn, call in _driver_calls(sample)
                if fn == "bad"}
    assert verdicts == {"bad": False}
    assert all(_safe_statement(call.args[0], rendered, set()) for fn, call in _driver_calls(sample) if fn == "good")


def test_the_not_driver_exemption_stays_a_tool_registry_call() -> None:
    """The exemption names a function, so pin what the function's driver-named calls are: the
    gate's throwaway ToolRegistry (``self.tools``) and nothing else."""
    for (file, function), _why in NOT_DRIVER_CALLS.items():
        path = next((CORE / "adaptation").rglob(file))
        calls = [call for name, call in _driver_calls(ast.parse(path.read_text(encoding="utf-8"))) if name == function]
        assert calls, f"{file}:{function} no longer exists; drop its exemption"
        for call in calls:
            receiver = call.func.value
            assert isinstance(receiver, ast.Attribute) and receiver.attr == "tools" \
                and isinstance(receiver.value, ast.Name) and receiver.value.id == "self", \
                f"{file}:{call.lineno} calls .{call.func.attr}() on something other than self.tools"
