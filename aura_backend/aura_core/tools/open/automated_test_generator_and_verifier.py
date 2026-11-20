import ast
import keyword
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ._process import PYTEST_ENV, run_bounded


class AutomatedTestGeneratorAndVerifier:
    """Generates pytest suites from Python AST and verifies them in a temporary directory.

    Generated tests assert only what can be known from the source without guessing inputs:
    that the module imports, and that each public function, class and method exists with the
    parameter names and async-ness the source declares. Edge-case tests bind arguments to the
    signature instead of calling the function, so they never execute it. Calling code with
    made-up inputs cannot tell a real failure from a wrong guess, so the only execution is the
    opt-in ``include_call_smoke`` check, limited to callables that need no arguments, where any
    exception is a real failure.
    """

    #: Decorators that leave a callable's runtime signature equal to the one in the source.
    SIGNATURE_PRESERVING_DECORATORS = frozenset({
        "staticmethod",
        "classmethod",
        "abstractmethod",
        "abc.abstractmethod",
    })
    PROPERTY_DECORATORS = frozenset({"property", "builtins.property"})

    _MODULE_PATH_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")

    @staticmethod
    def _decorator_names(node: ast.AST) -> List[str]:
        names: List[str] = []
        for dec in getattr(node, "decorator_list", []):
            target = dec.func if isinstance(dec, ast.Call) else dec
            try:
                names.append(ast.unparse(target))
            except Exception:
                names.append("<unknown>")
        return names

    @classmethod
    def _callable_info(cls, node: ast.AST, *, in_class: bool) -> Dict[str, Any]:
        decorators = cls._decorator_names(node)
        if not in_class:
            kind = "function"
        elif "staticmethod" in decorators:
            kind = "staticmethod"
        elif "classmethod" in decorators:
            kind = "classmethod"
        elif any(d in cls.PROPERTY_DECORATORS for d in decorators):
            kind = "property"
        else:
            kind = "instance"
        unknown_decorators = [
            d for d in decorators
            if d not in cls.SIGNATURE_PRESERVING_DECORATORS and d not in cls.PROPERTY_DECORATORS
        ]
        meta = cls._extract_args_meta(node.args, skip_first=kind in ("instance", "classmethod", "property"))
        return {
            "name": node.name,
            "kind": kind,
            "args": [a["name"] for a in meta["params"] if a["kind"] != "keyword_only_sep"],
            "params": meta["params"],
            "param_names": meta["param_names"],
            "positional_count": meta["positional_count"],
            "required_positional_count": meta["required_positional_count"],
            "has_required_kwonly": meta["has_required_kwonly"],
            "has_vararg": node.args.vararg is not None,
            "has_kwarg": node.args.kwarg is not None,
            "is_async": isinstance(node, ast.AsyncFunctionDef),
            "decorators": decorators,
            "signature_checkable": not unknown_decorators and kind != "property",
        }

    @staticmethod
    def _last_definitions(nodes: List[ast.AST], types: tuple) -> List[ast.AST]:
        """Keep only the last definition of each name: that is the one bound at runtime
        (``@overload`` stubs and conditional redefinitions come before it)."""
        latest: Dict[str, ast.AST] = {}
        for node in nodes:
            if isinstance(node, types):
                latest.pop(node.name, None)
                latest[node.name] = node
        return list(latest.values())

    @classmethod
    def extract_testable_targets(cls, source_code: str) -> Dict[str, Any]:
        try:
            tree = ast.parse(source_code)
        except SyntaxError as e:
            return {
                "valid_syntax": False,
                "error": f"SyntaxError: {e}",
                "functions": [],
                "classes": [],
                "imports": [],
            }

        func_types = (ast.FunctionDef, ast.AsyncFunctionDef)
        functions = [
            cls._callable_info(node, in_class=False)
            for node in cls._last_definitions(tree.body, func_types)
        ]

        classes: List[Dict[str, Any]] = []
        for node in cls._last_definitions(tree.body, (ast.ClassDef,)):
            methods = [
                cls._callable_info(item, in_class=True)
                for item in cls._last_definitions(node.body, func_types)
            ]
            init = next((m for m in methods if m["name"] == "__init__"), None)
            classes.append({
                "name": node.name,
                "bases": [ast.unparse(b) for b in node.bases],
                "decorators": cls._decorator_names(node),
                "defines_init": init is not None,
                "init_required_positional_count": init["required_positional_count"] if init else 0,
                "init_has_required_kwonly": init["has_required_kwonly"] if init else False,
                "methods": methods,
            })

        imports: List[str] = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")

        return {
            "valid_syntax": True,
            "functions": functions,
            "classes": classes,
            "imports": list(dict.fromkeys(imports)),
        }

    @staticmethod
    def _extract_args_meta(args: ast.arguments, skip_first: bool = False) -> Dict[str, Any]:
        """Describe a parameter list. ``skip_first`` drops the receiver (``self``/``cls``) from
        the *required* counts and ``params``; ``param_names`` always lists every parameter in the
        order ``inspect.signature`` reports them."""
        positional = list(args.posonlyargs) + list(args.args)
        param_names = [a.arg for a in positional]
        if args.vararg:
            param_names.append(args.vararg.arg)
        param_names.extend(a.arg for a in args.kwonlyargs)
        if args.kwarg:
            param_names.append(args.kwarg.arg)

        first_default = len(positional) - len(args.defaults)
        callable_positional = positional[1:] if skip_first and positional else positional
        offset = len(positional) - len(callable_positional)
        required_pos = sum(1 for i in range(offset, len(positional)) if i < first_default)

        params: List[Dict[str, Any]] = [
            {"name": a.arg, "kind": "positional", "required": (i + offset) < first_default}
            for i, a in enumerate(callable_positional)
        ]
        if args.vararg:
            params.append({"name": f"*{args.vararg.arg}", "kind": "vararg", "required": False})
        elif args.kwonlyargs:
            params.append({"name": "*", "kind": "keyword_only_sep", "required": False})

        has_required_kwonly = False
        for idx, kw in enumerate(args.kwonlyargs):
            required = args.kw_defaults[idx] is None
            has_required_kwonly = has_required_kwonly or required
            params.append({"name": kw.arg, "kind": "kwonly", "required": required})
        if args.kwarg:
            params.append({"name": f"**{args.kwarg.arg}", "kind": "varkw", "required": False})

        return {
            "params": params,
            "param_names": param_names,
            "positional_count": len(callable_positional),
            "required_positional_count": required_pos,
            "has_required_kwonly": has_required_kwonly,
        }

    @classmethod
    def _valid_module_path(cls, module_import_path: str) -> bool:
        if not cls._MODULE_PATH_RE.fullmatch(module_import_path):
            return False
        return not any(keyword.iskeyword(part) for part in module_import_path.split("."))

    @classmethod
    def generate_pytest_code(
        cls,
        source_code: str,
        module_import_path: Optional[str] = None,
        include_edge_cases: bool = True,
        include_call_smoke: bool = False,
    ) -> Dict[str, Any]:
        """Generate structural pytest checks for ``source_code``.

        ``include_call_smoke`` additionally *calls* every public function that needs no
        arguments and instantiates every class whose ``__init__`` needs none -- real code runs,
        so leave it off unless the module is known to be free of side effects.
        """
        targets = cls.extract_testable_targets(source_code)
        if not targets["valid_syntax"]:
            return {
                "generated": False,
                "error": targets["error"],
                "test_code": "",
            }

        target_mod_name = module_import_path or "module_under_test"
        if not cls._valid_module_path(target_mod_name):
            return {
                "generated": False,
                "error": f"module_import_path must be a dotted Python identifier: {target_mod_name!r}",
                "test_code": "",
            }

        lines: List[str] = [
            "import asyncio",
            "import inspect",
            "",
            "import pytest",
            "",
            f"import {target_mod_name} as target_module",
            "",
            "",
            "def _param_names(fn):",
            "    return [p.name for p in inspect.signature(fn).parameters.values()]",
            "",
            "",
            "def _static_member(owner, name):",
            "    raw = inspect.getattr_static(owner, name)",
            "    if isinstance(raw, (staticmethod, classmethod)):",
            "        return raw.__func__",
            "    return raw",
            "",
            "",
            "def test_module_imports():",
            "    assert target_module is not None",
            "",
        ]
        checks = {"signature": 0, "existence": 0, "edge_case": 0, "call_smoke": 0}
        used_names = {"test_module_imports"}

        def add_test(base: str, body: List[str], category: str) -> None:
            name = f"test_{base}"
            suffix = 2
            while name in used_names:
                name = f"test_{base}_{suffix}"
                suffix += 1
            used_names.add(name)
            lines.append("")
            lines.append(f"def {name}():")
            lines.extend(f"    {b}" for b in body)
            lines.append("")
            checks[category] += 1

        for func in targets["functions"]:
            fname = func["name"]
            if fname.startswith("_"):
                continue
            getter = f"fn = getattr(target_module, {fname!r})"
            if not func["signature_checkable"]:
                add_test(f"{fname}_exists", [getter, "assert callable(fn)"], "existence")
                continue

            add_test(f"{fname}_signature", [
                getter,
                "assert callable(fn)",
                f"assert inspect.iscoroutinefunction(fn) is {func['is_async']}",
                f"assert _param_names(fn) == {func['param_names']!r}",
            ], "signature")

            if include_edge_cases:
                sig = f"sig = inspect.signature(getattr(target_module, {fname!r}))"
                if func["required_positional_count"] or func["has_required_kwonly"]:
                    add_test(f"{fname}_requires_its_arguments", [
                        sig, "with pytest.raises(TypeError):", "    sig.bind()",
                    ], "edge_case")
                if not func["has_vararg"]:
                    add_test(f"{fname}_rejects_extra_positional", [
                        sig,
                        "with pytest.raises(TypeError):",
                        f"    sig.bind_partial(*([None] * {func['positional_count'] + 1}))",
                    ], "edge_case")
                if not func["has_kwarg"]:
                    add_test(f"{fname}_rejects_unknown_keyword", [
                        sig,
                        "with pytest.raises(TypeError):",
                        "    sig.bind_partial(__aura_unknown_keyword__=None)",
                    ], "edge_case")

            if include_call_smoke and not func["required_positional_count"] and not func["has_required_kwonly"]:
                add_test(f"{fname}_runs_without_arguments", [
                    getter,
                    "result = fn()",
                    "if inspect.isawaitable(result):",
                    "    asyncio.run(result)",
                ], "call_smoke")

        for cls_info in targets["classes"]:
            cname = cls_info["name"]
            if cname.startswith("_"):
                continue
            owner = f"owner = getattr(target_module, {cname!r})"
            add_test(f"{cname}_is_a_class", [owner, "assert inspect.isclass(owner)"], "existence")

            for method in cls_info["methods"]:
                mname = method["name"]
                if mname.startswith("_") and mname != "__init__":
                    continue
                if method["kind"] == "property":
                    add_test(f"{cname}_{mname}_is_a_property", [
                        owner,
                        f"assert isinstance(inspect.getattr_static(owner, {mname!r}), property)",
                    ], "existence")
                elif not method["signature_checkable"]:
                    add_test(f"{cname}_{mname}_exists", [
                        owner, f"assert hasattr(owner, {mname!r})",
                    ], "existence")
                else:
                    add_test(f"{cname}_{mname}_signature", [
                        owner,
                        f"member = _static_member(owner, {mname!r})",
                        "assert callable(member)",
                        f"assert inspect.iscoroutinefunction(member) is {method['is_async']}",
                        f"assert _param_names(member) == {method['param_names']!r}",
                    ], "signature")

            # Instantiate only when it provably needs no arguments: a class without its own
            # __init__ may inherit one (or get one from @dataclass) that does.
            no_arg_init = (
                cls_info["init_required_positional_count"] == 0
                and not cls_info["init_has_required_kwonly"]
                and (cls_info["defines_init"] or (not cls_info["bases"] and not cls_info["decorators"]))
                and not any(
                    d in ("abstractmethod", "abc.abstractmethod")
                    for m in cls_info["methods"]
                    for d in m["decorators"]
                )
            )
            if include_call_smoke and no_arg_init:
                add_test(f"{cname}_instantiates_without_arguments", [
                    owner, "assert isinstance(owner(), owner)",
                ], "call_smoke")

        test_code = "\n".join(lines).strip() + "\n"
        return {
            "generated": True,
            "targets_summary": {
                "functions_count": len(targets["functions"]),
                "classes_count": len(targets["classes"]),
            },
            "checks": checks,
            "verification_level": "structural+call_smoke" if include_call_smoke else "structural",
            "test_code": test_code,
        }

    @staticmethod
    def validate_test_syntax(test_code: str) -> Dict[str, Any]:
        try:
            tree = ast.parse(test_code)
        except SyntaxError as e:
            return {
                "valid": False,
                "error": f"SyntaxError in test code: {e}",
                "line": e.lineno,
                "test_functions": [],
            }

        test_funcs = [
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test_")
        ]

        return {
            "valid": len(test_funcs) > 0,
            "test_functions": test_funcs,
            "warning": None if test_funcs else "No test_* functions found in test code.",
        }

    @staticmethod
    def _module_path_to_rel_file(module_import_path: str) -> Path:
        parts = [p for p in module_import_path.split(".") if p]
        if not parts:
            return Path("module_under_test.py")
        return Path(*parts).with_suffix(".py")

    @classmethod
    def run_tests_in_temp_dir(
        cls,
        test_code: str,
        source_code: Optional[str] = None,
        source_filename: str = "module_under_test.py",
        timeout_seconds: int = 30,
    ) -> Dict[str, Any]:
        syntax_check = cls.validate_test_syntax(test_code)
        if not syntax_check["valid"]:
            return {
                "passed": False,
                "exit_code": -1,
                "error": syntax_check.get("error") or syntax_check.get("warning"),
                "stdout": "",
                "stderr": "",
                "duration_ms": 0,
            }

        start_time = time.time()
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir).resolve()
            if source_code is not None:
                target_file = (tmp_path / source_filename).resolve()
                if tmp_path not in target_file.parents and target_file.parent != tmp_path:
                    return {
                        "passed": False,
                        "exit_code": -1,
                        "error": f"Invalid source_filename outside temp directory: {source_filename}",
                        "stdout": "",
                        "stderr": "",
                        "duration_ms": 0,
                    }
                target_file.parent.mkdir(parents=True, exist_ok=True)
                curr = target_file.parent
                while curr != tmp_path and tmp_path in curr.parents:
                    init_file = curr / "__init__.py"
                    if not init_file.exists():
                        init_file.write_text("", encoding="utf-8")
                    curr = curr.parent
                target_file.write_text(source_code, encoding="utf-8")

            test_file = tmp_path / "test_generated.py"
            test_file.write_text(test_code, encoding="utf-8")

            cmd = [sys.executable, "-m", "pytest", str(test_file), "-q", "--tb=short", "-p", "no:cacheprovider"]
            try:
                proc = run_bounded(cmd, cwd=tmp_path, timeout_s=timeout_seconds, extra_env=PYTEST_ENV)
                duration_ms = round((time.time() - start_time) * 1000, 2)
                parsed = cls.parse_pytest_output(proc.stdout, proc.stderr, proc.returncode)
                parsed["duration_ms"] = duration_ms
                return parsed
            except subprocess.TimeoutExpired:
                duration_ms = round((time.time() - start_time) * 1000, 2)
                return {
                    "passed": False,
                    "exit_code": 124,
                    "error": f"Test execution timed out after {timeout_seconds}s",
                    "stdout": "",
                    "stderr": "",
                    "duration_ms": duration_ms,
                }
            except FileNotFoundError:
                return {
                    "passed": False,
                    "exit_code": 127,
                    "error": "pytest is not installed in the current environment.",
                    "stdout": "",
                    "stderr": "",
                    "duration_ms": 0,
                }

    @classmethod
    def run_existing_tests(
        cls,
        target_path: str,
        working_dir: Optional[str] = None,
        extra_args: Optional[List[str]] = None,
        timeout_seconds: int = 60,
    ) -> Dict[str, Any]:
        cwd = working_dir or os.getcwd()
        cmd = [sys.executable, "-m", "pytest", target_path, "-q", "--tb=short"]
        if extra_args:
            cmd.extend(extra_args)

        start_time = time.time()
        try:
            proc = run_bounded(cmd, cwd=cwd, timeout_s=timeout_seconds, extra_env=PYTEST_ENV)
            duration_ms = round((time.time() - start_time) * 1000, 2)
            parsed = cls.parse_pytest_output(proc.stdout, proc.stderr, proc.returncode)
            parsed["duration_ms"] = duration_ms
            parsed["target_path"] = target_path
            return parsed
        except subprocess.TimeoutExpired:
            return {
                "passed": False,
                "exit_code": 124,
                "target_path": target_path,
                "error": f"Pytest timed out after {timeout_seconds}s",
                "stdout": "",
                "stderr": "",
                "duration_ms": round((time.time() - start_time) * 1000, 2),
            }
        except FileNotFoundError:
            return {
                "passed": False,
                "exit_code": 127,
                "target_path": target_path,
                "error": "pytest not found in PATH.",
                "stdout": "",
                "stderr": "",
                "duration_ms": 0,
            }

    @staticmethod
    def parse_pytest_output(stdout: str, stderr: str, exit_code: int) -> Dict[str, Any]:
        combined = f"{stdout}\n{stderr}"
        if exit_code != 0 and "No module named pytest" in combined:
            return {
                "passed": False,
                "exit_code": 127,
                "error": "pytest is not installed for this interpreter.",
                "counts": {"passed": 0, "failed": 0, "errors": 0, "skipped": 0},
                "failed_tests": [],
                "stdout": stdout.strip(),
                "stderr": stderr.strip(),
            }

        passed_match = re.search(r"(\d+)\s+passed", combined)
        failed_match = re.search(r"(\d+)\s+failed", combined)
        error_match = re.search(r"(\d+)\s+error", combined)
        skipped_match = re.search(r"(\d+)\s+skipped", combined)

        failed_tests = re.findall(r"FAILED\s+([^\s]+)", combined)

        return {
            "passed": exit_code == 0,
            "exit_code": exit_code,
            "counts": {
                "passed": int(passed_match.group(1)) if passed_match else 0,
                "failed": int(failed_match.group(1)) if failed_match else 0,
                "errors": int(error_match.group(1)) if error_match else 0,
                "skipped": int(skipped_match.group(1)) if skipped_match else 0,
            },
            "failed_tests": failed_tests,
            "stdout": stdout.strip(),
            "stderr": stderr.strip(),
        }

    @classmethod
    def generate_and_verify(
        cls,
        source_code: str,
        custom_test_code: Optional[str] = None,
        module_import_path: Optional[str] = None,
        include_edge_cases: bool = True,
        timeout_seconds: int = 30,
        include_call_smoke: bool = False,
    ) -> Dict[str, Any]:
        """Generate (or take) tests and run them against ``source_code``.

        ``verification_level`` says what a ``verified: True`` means: ``structural`` -- the module
        imports and matches its declared shape, not that it behaves correctly;
        ``structural+call_smoke`` -- also that no-argument callables run without raising;
        ``custom`` -- whatever the supplied tests assert.
        """
        if custom_test_code is not None:
            test_code = custom_test_code
            generation_info = {"generated": False, "source": "provided_by_agent"}
            verification_level = "custom"
        else:
            gen_result = cls.generate_pytest_code(
                source_code=source_code,
                module_import_path=module_import_path,
                include_edge_cases=include_edge_cases,
                include_call_smoke=include_call_smoke,
            )
            if not gen_result["generated"]:
                return {
                    "verified": False,
                    "stage": "generation",
                    "error": gen_result.get("error"),
                    "test_code": "",
                    "execution": None,
                }
            test_code = gen_result["test_code"]
            generation_info = {
                "generated": True,
                "source": "auto_generated",
                "targets_summary": gen_result.get("targets_summary"),
                "checks": gen_result.get("checks"),
            }
            verification_level = gen_result["verification_level"]

        rel_source_filename = (
            str(cls._module_path_to_rel_file(module_import_path))
            if module_import_path
            else "module_under_test.py"
        )

        exec_result = cls.run_tests_in_temp_dir(
            test_code=test_code,
            source_code=source_code,
            source_filename=rel_source_filename,
            timeout_seconds=timeout_seconds,
        )

        return {
            "verified": exec_result.get("passed", False),
            "verification_level": verification_level,
            "stage": "verification",
            "generation": generation_info,
            "test_code": test_code,
            "execution": exec_result,
        }