"""Behaviour tests for ``generate_and_run_tests`` (ADR-044, row 6).

The structural generator proves a module has its declared *shape*. This adds tests that assert what
the code *does*, derived from two things the author already wrote down:

* **Docstring examples** (``>>> f(2)`` / ``4``). Each function that has them gets one test that runs
  exactly its examples through :mod:`doctest` and fails with the doctest diff, so the failing
  function and the expected/actual values are named.
* **Type hints**. For a module-level function that looks pure (every parameter annotated with a
  simple type, no I/O, no global state, no randomness or clock) the generated test calls it with a
  small grid of values chosen for each parameter's type and asserts four contracts:
  the return value conforms to the return annotation; two calls with equal arguments return equal
  values (determinism); the arguments are not mutated; and it raises nothing the function does not
  itself ``raise`` or declare in its docstring.

What this is not: a proof of correctness. The contracts catch a function that lies about its type,
mutates its input or misses a doctested case; they cannot know what the result *should* be beyond
the docstring. ``assertions`` in the result says how many of each kind were generated, and a
function with no examples and no simple hints gets nothing (reported under ``skipped``).
"""

from __future__ import annotations

import ast
import itertools
import re
from typing import Any

__all__ = ["behaviour_test_code", "generate_and_verify_with_behaviour"]

_IMPURE_CALLS = frozenset({
    "open", "print", "input", "exec", "eval", "compile", "__import__", "setattr", "delattr", "globals",
    "locals", "breakpoint", "exit", "quit",
})
_IMPURE_MODULES = frozenset({
    "os", "sys", "subprocess", "socket", "random", "time", "datetime", "secrets", "uuid", "requests",
    "httpx", "urllib", "shutil", "pathlib", "threading", "asyncio", "logging", "tempfile", "pickle",
})
_MAX_COMBOS = 12
_MAX_FUNCS = 40

_SAMPLES: dict[str, list[str]] = {
    "int": ["0", "1", "-1", "7"],
    "float": ["0.0", "1.5", "-2.25"],
    "str": ["''", "'a'", "'Hello World'"],
    "bool": ["True", "False"],
    "bytes": ["b''", "b'ab'"],
}
_DECLARED_RAISES = re.compile(r"(?:Raises?:|:raises?\s+)([\s\S]*)", re.IGNORECASE)

_HELPERS = '''

def _aura_matches(value, annotation):
    """Does ``value`` conform to the annotation source text (unknown annotations always conform)?"""
    import ast as _ast
    try:
        node = _ast.parse(annotation, mode="eval").body
    except SyntaxError:
        return True

    def ok(v, n):
        if isinstance(n, _ast.Constant):
            if n.value is None:
                return v is None
            if isinstance(n.value, str):
                return True
            return v == n.value
        if isinstance(n, _ast.Name):
            simple = {"int": int, "float": (int, float), "str": str, "bool": bool, "bytes": bytes,
                      "list": list, "dict": dict, "set": set, "tuple": tuple, "frozenset": frozenset}
            if n.id == "int":
                return isinstance(v, int)
            if n.id in simple:
                return isinstance(v, simple[n.id])
            return True
        if isinstance(n, _ast.BinOp) and isinstance(n.op, _ast.BitOr):
            return ok(v, n.left) or ok(v, n.right)
        if isinstance(n, _ast.Subscript) and isinstance(n.value, _ast.Name):
            head = n.value.id
            inner = n.slice
            args = list(inner.elts) if isinstance(inner, _ast.Tuple) else [inner]
            if head in ("Optional",):
                return v is None or ok(v, args[0])
            if head == "Union":
                return any(ok(v, a) for a in args)
            if head in ("list", "List", "set", "Set", "frozenset", "Sequence", "Iterable"):
                container = {"list": list, "List": list, "set": set, "Set": set, "frozenset": frozenset}.get(head)
                if container is not None and not isinstance(v, container):
                    return False
                return all(ok(item, args[0]) for item in v) if isinstance(v, (list, set, frozenset)) else True
            if head in ("dict", "Dict", "Mapping"):
                if not isinstance(v, dict):
                    return False
                return all(ok(k, args[0]) and ok(val, args[-1]) for k, val in v.items())
            if head in ("tuple", "Tuple"):
                if not isinstance(v, tuple):
                    return False
                if len(args) == 2 and isinstance(args[1], _ast.Constant) and args[1].value is Ellipsis:
                    return all(ok(i, args[0]) for i in v)
                return len(v) == len(args) and all(ok(i, a) for i, a in zip(v, args))
        return True

    return ok(value, node)


def _aura_run_doctests(name):
    import doctest
    fn = getattr(target_module, name)
    tests = doctest.DocTestFinder(recurse=False).find(fn, name, globs=dict(vars(target_module)))
    runner = doctest.DocTestRunner(optionflags=doctest.ELLIPSIS | doctest.NORMALIZE_WHITESPACE)
    report = []
    for t in tests:
        runner.run(t, out=report.append)
    failed, tried = runner.summarize(verbose=False)[:2]
    assert tried > 0, "no docstring examples were found for " + name
    assert failed == 0, "".join(report)


def _aura_check_contracts(name, grid, return_annotation, declared):
    import copy
    import inspect
    fn = getattr(target_module, name)
    for args in grid:
        before = copy.deepcopy(args)
        try:
            first = fn(*args)
        except Exception as exc:
            import builtins
            allowed = tuple(t for t in (getattr(target_module, d, None) or getattr(builtins, d, None)
                                        for d in declared) if isinstance(t, type))
            if isinstance(exc, allowed):
                continue
            raise AssertionError("%s%r raised %s: %s, which the function neither raises itself nor "
                                 "documents" % (name, tuple(args), type(exc).__name__, exc))
        assert args == before, "%s mutated its arguments: %r became %r" % (name, before, args)
        if return_annotation is not None:
            assert _aura_matches(first, return_annotation), (
                "%s%r returned %r, which is not %s" % (name, tuple(args), first, return_annotation))
        second = fn(*copy.deepcopy(before))
        same = first == second or (first != first and second != second)
        assert same, "%s%r is not deterministic: %r then %r" % (name, tuple(args), first, second)
'''


def _is_pure(fn: ast.FunctionDef) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, (ast.Global, ast.Nonlocal, ast.Await, ast.Yield, ast.YieldFrom)):
            return False
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in _IMPURE_CALLS:
                return False
        if isinstance(node, ast.Name) and node.id in _IMPURE_MODULES:
            return False
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            return False
    return True


def _samples_for(annotation: ast.expr | None, depth: int = 0) -> list[str] | None:
    """Python source of sample values for an annotation, or None when it cannot be sampled."""
    if annotation is None or depth > 2:
        return None
    if isinstance(annotation, ast.Constant) and annotation.value is None:
        return ["None"]
    if isinstance(annotation, ast.Name):
        return _SAMPLES.get(annotation.id) or (
            {"list": ["[]", "[1, 2, 3]"], "dict": ["{}", "{'a': 1}"], "set": ["set()", "{1, 2}"],
             "tuple": ["()", "(1, 2)"]}.get(annotation.id))
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        left, right = _samples_for(annotation.left, depth + 1), _samples_for(annotation.right, depth + 1)
        return (left + right[:1]) if left and right else None
    if isinstance(annotation, ast.Subscript) and isinstance(annotation.value, ast.Name):
        head = annotation.value.id
        args = list(annotation.slice.elts) if isinstance(annotation.slice, ast.Tuple) else [annotation.slice]
        if head == "Optional":
            inner = _samples_for(args[0], depth + 1)
            return (["None"] + inner[:2]) if inner else None
        if head in ("list", "List", "Sequence", "Iterable"):
            inner = _samples_for(args[0], depth + 1)
            return ["[]", f"[{', '.join(inner[:3])}]"] if inner else None
        if head in ("set", "Set"):
            inner = _samples_for(args[0], depth + 1)
            return ["set()", f"{{{', '.join(inner[:2])}}}"] if inner else None
        if head in ("dict", "Dict", "Mapping") and len(args) == 2:
            keys, vals = _samples_for(args[0], depth + 1), _samples_for(args[1], depth + 1)
            return ["{}", f"{{{keys[1] if len(keys) > 1 else keys[0]}: {vals[0]}}}"] if keys and vals else None
    return None


def _grid(fn: ast.FunctionDef) -> list[str] | None:
    """Source text of argument tuples for a function whose every parameter is sampleable."""
    args = fn.args
    if args.vararg or args.kwarg or args.kwonlyargs or args.posonlyargs:
        return None
    positional = list(args.args)
    if positional and positional[0].arg in ("self", "cls"):
        return None
    if not positional:
        return None
    pools: list[list[str]] = []
    for argument in positional:
        pool = _samples_for(argument.annotation)
        if not pool:
            return None
        pools.append(pool)
    combos = list(itertools.product(*pools))
    if len(combos) > _MAX_COMBOS:
        # keep a spread: the first, the last and an even stride between them
        stride = max(1, len(combos) // _MAX_COMBOS)
        combos = combos[::stride][:_MAX_COMBOS]
    return ["[" + ", ".join(c) + "]" for c in combos]


def _declared_exceptions(fn: ast.FunctionDef) -> list[str]:
    names: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Raise) and node.exc is not None:
            target = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
            if isinstance(target, ast.Name):
                names.add(target.id)
            elif isinstance(target, ast.Attribute):
                names.add(target.attr)
    doc = ast.get_docstring(fn) or ""
    match = _DECLARED_RAISES.search(doc)
    if match:
        names.update(re.findall(r"\b([A-Z][A-Za-z]*(?:Error|Exception|Warning))\b", match.group(1)))
    return sorted(names)


def behaviour_test_code(source_code: str) -> dict[str, Any]:
    """``{"code", "assertions", "skipped"}``: test text to append to the structural tests.

    ``code`` is empty when the source does not parse or nothing could be derived.
    """
    try:
        tree = ast.parse(source_code)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return {"code": "", "assertions": {"doctest": 0, "type_contract": 0}, "skipped": []}
    functions: dict[str, ast.FunctionDef] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            functions[node.name] = node  # the last definition wins, as at import time
    body: list[str] = []
    counts = {"doctest": 0, "type_contract": 0}
    skipped: list[dict[str, str]] = []
    for index, (name, fn) in enumerate(functions.items()):
        if index >= _MAX_FUNCS:
            skipped.append({"function": name, "reason": f"more than {_MAX_FUNCS} functions"})
            continue
        derived = False
        doc = ast.get_docstring(fn) or ""
        if re.search(r"^\s*>>> ", doc, re.MULTILINE):
            body += ["", "", f"def test_behaviour_{name}_docstring_examples():",
                     f"    _aura_run_doctests({name!r})"]
            counts["doctest"] += 1
            derived = True
        grid = _grid(fn) if _is_pure(fn) else None
        if grid:
            returns = ast.unparse(fn.returns) if fn.returns is not None else None
            body += ["", "", f"def test_behaviour_{name}_type_contract():",
                     f"    _aura_check_contracts({name!r}, [{', '.join(grid)}], {returns!r}, {_declared_exceptions(fn)!r})"]
            counts["type_contract"] += 1
            derived = True
        if not derived:
            reason = ("not pure or not fully annotated with simple types, and no docstring examples")
            skipped.append({"function": name, "reason": reason})
    if not body:
        return {"code": "", "assertions": counts, "skipped": skipped}
    return {"code": _HELPERS + "\n".join(body) + "\n", "assertions": counts, "skipped": skipped}


def generate_and_verify_with_behaviour(
    source_code: str, *, module_import_path: str | None, include_call_smoke: bool, timeout_seconds: int,
) -> dict[str, Any]:
    """The structural tests plus the behaviour tests, run together; same result shape as
    ``AutomatedTestGeneratorAndVerifier.generate_and_verify`` with ``behaviour`` added."""
    from .automated_test_generator_and_verifier import AutomatedTestGeneratorAndVerifier as G

    base = G.generate_pytest_code(
        source_code=source_code, module_import_path=module_import_path, include_call_smoke=include_call_smoke)
    if not base["generated"]:
        return {"verified": False, "stage": "generation", "error": base.get("error"), "test_code": "",
                "execution": None}
    extra = behaviour_test_code(source_code)
    combined = base["test_code"] + extra["code"]
    result = G.generate_and_verify(
        source_code, custom_test_code=combined, module_import_path=module_import_path,
        timeout_seconds=timeout_seconds)
    derived = bool(extra["code"])
    result["verification_level"] = (
        ("structural+call_smoke" if include_call_smoke else "structural") + ("+behaviour" if derived else ""))
    result["generation"] = {
        "generated": True, "source": "auto_generated", "targets_summary": base.get("targets_summary"),
        "checks": {**(base.get("checks") or {}), "behaviour": sum(extra["assertions"].values())},
    }
    result["behaviour"] = {"assertions": extra["assertions"], "skipped": extra["skipped"]}
    return result
