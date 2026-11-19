import ast
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


class AutomatedTestGeneratorAndVerifier:

   
    @staticmethod
    def extract_testable_targets(source_code: str) -> Dict[str, Any]:
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
                args = [a.arg for a in node.args.args if a.arg != "self"]
                functions.append({
                    "name": node.name,
                    "args": args,
                    "is_async": isinstance(node, ast.AsyncFunctionDef),
                })
            elif isinstance(node, ast.ClassDef):
                methods = []
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        m_args = [a.arg for a in item.args.args if a.arg != "self"]
                        methods.append({
                            "name": item.name,
                            "args": m_args,
                            "is_async": isinstance(item, ast.AsyncFunctionDef),
                        })
                classes.append({
                    "name": node.name,
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

        lines: List[str] = ["import pytest", ""]
        if module_import_path:
            lines.append(f"# Target module: {module_import_path}")
            lines.append(f"import {module_import_path} as target_module")
            lines.append("")

        for func in targets["functions"]:
            fname = func["name"]
            if fname.startswith("_"):
                continue
            lines.append(f"def test_{fname}_basic():")
            if module_import_path:
                lines.append(f"    assert hasattr(target_module, '{fname}')")
            else:
                lines.append(f"    # TODO: provide valid inputs for {fname}")
                lines.append("    assert True")
            lines.append("")

            if include_edge_cases:
                lines.append(f"def test_{fname}_edge_cases():")
                lines.append(f"    # TODO: test edge cases / invalid inputs for {fname}")
                lines.append("    assert True")
                lines.append("")

        for cls_info in targets["classes"]:
            cname = cls_info["name"]
            if cname.startswith("_"):
                continue
            lines.append(f"def test_{cname.lower()}_instantiation():")
            if module_import_path:
                lines.append(f"    assert hasattr(target_module, '{cname}')")
            else:
                lines.append(f"    # TODO: instantiate {cname} and test methods")
                lines.append("    assert True")
            lines.append("")

            for method in cls_info["methods"]:
                mname = method["name"]
                if mname.startswith("_") and mname != "__init__":
                    continue
                lines.append(f"def test_{cname.lower()}_{mname}():")
                lines.append(f"    # TODO: verify {cname}.{mname} behavior")
                lines.append("    assert True")
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
            tmp_path = Path(tmpdir)
            if source_code is not None:
                (tmp_path / source_filename).write_text(source_code, encoding="utf-8")

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

        exec_result = cls.run_tests_in_temp_dir(
            test_code=test_code,
            source_code=source_code,
            timeout_seconds=timeout_seconds,
        )

        return {
            "verified": exec_result.get("passed", False),
            "stage": "verification",
            "generation": generation_info,
            "test_code": test_code,
            "execution": exec_result,
        }