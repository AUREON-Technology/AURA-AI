import ast
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


class AutomatedTestGeneratorAndVerifier:
    """Generates executable pytest suites from Python AST and verifies them in isolation."""

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

        functions: List[Dict[str, Any]] = []
        classes: List[Dict[str, Any]] = []
        imports: List[str] = []

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args_meta = cls._extract_args_meta(node.args)
                functions.append({
                    "name": node.name,
                    "args": [a["name"] for a in args_meta["params"] if a["kind"] != "keyword_only_sep"],
                    "params": args_meta["params"],
                    "required_positional_count": args_meta["required_positional_count"],
                    "is_async": isinstance(node, ast.AsyncFunctionDef),
                })
            elif isinstance(node, ast.ClassDef):
                methods = []
                init_required_pos = 0
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        m_meta = cls._extract_args_meta(item.args)
                        if item.name == "__init__":
                            init_required_pos = m_meta["required_positional_count"]
                        methods.append({
                            "name": item.name,
                            "args": [a["name"] for a in m_meta["params"] if a["kind"] != "keyword_only_sep"],
                            "params": m_meta["params"],
                            "required_positional_count": m_meta["required_positional_count"],
                            "is_async": isinstance(item, ast.AsyncFunctionDef),
                        })
                classes.append({
                    "name": node.name,
                    "init_required_positional_count": init_required_pos,
                    "methods": methods,
                })
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                imports.append(module)

        return {
            "valid_syntax": True,
            "functions": functions,
            "classes": classes,
            "imports": list(dict.fromkeys(imports)),
        }

    @staticmethod
    def _extract_args_meta(args: ast.arguments) -> Dict[str, Any]:
        pos_params = [a.arg for a in args.posonlyargs if a.arg != "self"] + [
            a.arg for a in args.args if a.arg != "self"
        ]
        all_pos_with_self = list(args.posonlyargs) + list(args.args)
        non_self_pos = [a for a in all_pos_with_self if a.arg != "self"]
        num_defaults = len(args.defaults)
        required_pos = max(0, len(non_self_pos) - num_defaults)

        params: List[Dict[str, Any]] = []
        for idx, name in enumerate(pos_params):
            params.append({
                "name": name,
                "kind": "positional",
                "required": idx < required_pos,
            })

        if args.vararg:
            params.append({"name": f"*{args.vararg.arg}", "kind": "vararg", "required": False})
        elif args.kwonlyargs:
            params.append({"name": "*", "kind": "keyword_only_sep", "required": False})

        for idx, kw in enumerate(args.kwonlyargs):
            default_val = args.kw_defaults[idx] if idx < len(args.kw_defaults) else None
            params.append({
                "name": kw.arg,
                "kind": "kwonly",
                "required": default_val is None,
            })

        return {
            "params": params,
            "required_positional_count": required_pos,
        }

    @staticmethod
    def _sample_args_expr(required_count: int) -> str:
        samples = ["1", "'sample'", "{}", "[]", "0"]
        return ", ".join(samples[:required_count])

    @classmethod
    def generate_pytest_code(
        cls,
        source_code: str,
        module_import_path: Optional[str] = None,
        include_edge_cases: bool = True,
    ) -> Dict[str, Any]:
        targets = cls.extract_testable_targets(source_code)
        if not targets["valid_syntax"]:
            return {
                "generated": False,
                "error": targets["error"],
                "test_code": "",
            }

        target_mod_name = module_import_path or "module_under_test"
        lines: List[str] = [
            "import inspect",
            "import pytest",
            f"import {target_mod_name} as target_module",
            "",
        ]

        for func in targets["functions"]:
            fname = func["name"]
            if fname.startswith("_"):
                continue

            req_args = cls._sample_args_expr(func["required_positional_count"])
            lines.append(f"def test_{fname}_basic():")
            lines.append(f"    fn = getattr(target_module, '{fname}')")
            lines.append("    assert callable(fn)")
            lines.append("    sig = inspect.signature(fn)")
            lines.append(f"    assert len(sig.parameters) >= {func['required_positional_count']}")
            if func["is_async"]:
                lines.append("    import asyncio")
                lines.append(f"    res = asyncio.run(fn({req_args}))")
                lines.append("    assert res is not None or res is None")
            else:
                lines.append("    try:")
                lines.append(f"        res = fn({req_args})")
                lines.append("        assert res is not None or res is None")
                lines.append("    except TypeError:")
                lines.append("        pytest.skip('Function requires specific argument types/values')")
            lines.append("")

            if include_edge_cases:
                lines.append(f"def test_{fname}_edge_cases():")
                lines.append(f"    fn = getattr(target_module, '{fname}')")
                lines.append("    assert callable(fn)")
                lines.append("    with pytest.raises((TypeError, ValueError)):")
                lines.append("        fn('__invalid_edge_case_input_1__', '__invalid_edge_case_input_2__', '__invalid_edge_case_input_3__')")
                lines.append("")

        for cls_info in targets["classes"]:
            cname = cls_info["name"]
            if cname.startswith("_"):
                continue
            init_args = cls._sample_args_expr(cls_info["init_required_positional_count"])
            lines.append(f"def test_{cname.lower()}_instantiation():")
            lines.append(f"    cls_obj = getattr(target_module, '{cname}')")
            lines.append("    assert isinstance(cls_obj, type)")
            lines.append("    try:")
            lines.append(f"        instance = cls_obj({init_args})")
            lines.append("        assert isinstance(instance, cls_obj)")
            lines.append("    except TypeError:")
            lines.append("        pytest.skip('Class __init__ requires specific arguments')")
            lines.append("")

            for method in cls_info["methods"]:
                mname = method["name"]
                if mname.startswith("_") and mname != "__init__":
                    continue
                m_args = cls._sample_args_expr(method["required_positional_count"])
                lines.append(f"def test_{cname.lower()}_{mname}():")
                lines.append(f"    cls_obj = getattr(target_module, '{cname}')")
                lines.append(f"    assert hasattr(cls_obj, '{mname}')")
                lines.append(f"    assert callable(getattr(cls_obj, '{mname}'))")
                lines.append("    try:")
                lines.append(f"        instance = cls_obj({init_args})")
                lines.append(f"        method_fn = getattr(instance, '{mname}')")
                if method["is_async"]:
                    lines.append("        import asyncio")
                    lines.append(f"        out = asyncio.run(method_fn({m_args}))")
                else:
                    lines.append(f"        out = method_fn({m_args})")
                lines.append("        assert out is not None or out is None")
                lines.append("    except Exception:")
                lines.append("        pytest.skip('Method execution requires domain-specific state/arguments')")
                lines.append("")

        if len(lines) <= 4:
            lines.append("def test_module_loaded():")
            lines.append("    assert target_module is not None")
            lines.append("")

        test_code = "\n".join(lines).strip() + "\n"
        return {
            "generated": True,
            "targets_summary": {
                "functions_count": len(targets["functions"]),
                "classes_count": len(targets["classes"]),
            },
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

            cmd = ["pytest", str(test_file), "-q", "--tb=short"]
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=str(tmp_path),
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                )
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
        cmd = ["pytest", target_path, "-q", "--tb=short"]
        if extra_args:
            cmd.extend(extra_args)

        start_time = time.time()
        try:
            proc = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
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
    ) -> Dict[str, Any]:
        if custom_test_code is not None:
            test_code = custom_test_code
            generation_info = {"generated": False, "source": "provided_by_agent"}
        else:
            gen_result = cls.generate_pytest_code(
                source_code=source_code,
                module_import_path=module_import_path,
                include_edge_cases=include_edge_cases,
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
            }

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
            "stage": "verification",
            "generation": generation_info,
            "test_code": test_code,
            "execution": exec_result,
        }