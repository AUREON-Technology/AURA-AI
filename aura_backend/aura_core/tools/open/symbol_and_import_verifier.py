import ast
import importlib
import importlib.util
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


class SymbolAndImportVerifier:
    """AST-based verifier for imports, symbol definitions, referenced names, and signatures."""

    @staticmethod
    def parse_ast(source_code: str) -> Dict[str, Any]:
        """Parse source code into an AST and return syntax status."""
        try:
            tree = ast.parse(source_code)
            return {"valid_syntax": True, "tree": tree, "error": None}
        except SyntaxError as exc:
            return {
                "valid_syntax": False,
                "tree": None,
                "error": f"SyntaxError at line {exc.lineno}: {exc.msg}",
            }

    @classmethod
    def extract_imports(cls, source_code: str) -> Dict[str, Any]:
        """Extract standard and from-imports with aliases and line numbers."""
        parsed = cls.parse_ast(source_code)
        if not parsed["valid_syntax"]:
            return {
                "valid_syntax": False,
                "error": parsed["error"],
                "imports": [],
                "from_imports": [],
            }

        tree: ast.AST = parsed["tree"]
        imports: List[Dict[str, Any]] = []
        from_imports: List[Dict[str, Any]] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    bound_name = alias.asname or alias.name.split(".")[0]
                    imports.append({
                        "module": alias.name,
                        "name": alias.name,
                        "asname": alias.asname,
                        "bound_name": bound_name,
                        "line": getattr(node, "lineno", 0),
                    })
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    bound_name = alias.asname or alias.name
                    from_imports.append({
                        "module": node.module or "",
                        "level": node.level,
                        "name": alias.name,
                        "asname": alias.asname,
                        "bound_name": bound_name,
                        "line": getattr(node, "lineno", 0),
                    })

        return {
            "valid_syntax": True,
            "imports": imports,
            "from_imports": from_imports,
        }

    @classmethod
    def extract_defined_symbols(cls, source_code: str) -> Dict[str, Any]:
        """Extract top-level and nested functions, async functions, classes, and assigned names."""
        parsed = cls.parse_ast(source_code)
        if not parsed["valid_syntax"]:
            return {
                "valid_syntax": False,
                "error": parsed["error"],
                "functions": [],
                "classes": [],
                "assigned_names": [],
            }

        tree: ast.AST = parsed["tree"]
        functions: List[Dict[str, Any]] = []
        classes: List[Dict[str, Any]] = []
        assigned: Set[str] = set()

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                pos_args = [a.arg for a in node.args.posonlyargs]
                regular_args = [a.arg for a in node.args.args]
                kwonly_args = [a.arg for a in node.args.kwonlyargs]
                functions.append({
                    "name": node.name,
                    "is_async": isinstance(node, ast.AsyncFunctionDef),
                    "lineno": getattr(node, "lineno", 0),
                    "posonly_args": pos_args,
                    "args": regular_args,
                    "kwonly_args": kwonly_args,
                    "has_vararg": node.args.vararg is not None,
                    "has_kwarg": node.args.kwarg is not None,
                    "defaults_count": len(node.args.defaults),
                })
            elif isinstance(node, ast.ClassDef):
                methods = [
                    item.name
                    for item in node.body
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                ]
                classes.append({
                    "name": node.name,
                    "lineno": getattr(node, "lineno", 0),
                    "bases": [ast.unparse(b) for b in node.bases],
                    "methods": methods,
                })
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                assigned.add(node.id)

        return {
            "valid_syntax": True,
            "functions": functions,
            "classes": classes,
            "assigned_names": sorted(assigned),
        }

    @classmethod
    def extract_referenced_symbols(cls, source_code: str) -> Dict[str, Any]:
        """Extract loaded names and attribute accesses from the code."""
        parsed = cls.parse_ast(source_code)
        if not parsed["valid_syntax"]:
            return {
                "valid_syntax": False,
                "error": parsed["error"],
                "loaded_names": [],
                "attributes": [],
            }

        tree: ast.AST = parsed["tree"]
        loaded_names: List[Dict[str, Any]] = []
        attributes: List[Dict[str, Any]] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                loaded_names.append({
                    "name": node.id,
                    "line": getattr(node, "lineno", 0),
                })
            elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                root = cls._extract_root_name(node.value)
                attributes.append({
                    "root": root,
                    "attr": node.attr,
                    "line": getattr(node, "lineno", 0),
                })

        return {
            "valid_syntax": True,
            "loaded_names": loaded_names,
            "attributes": attributes,
        }

    @staticmethod
    def _extract_root_name(node: ast.AST) -> Optional[str]:
        """Resolve the base identifier of a chained attribute expression."""
        curr = node
        while isinstance(curr, ast.Attribute):
            curr = curr.value
        if isinstance(curr, ast.Name):
            return curr.id
        return None

    @staticmethod
    def check_module_resolvable(module_name: str, search_paths: Optional[List[str]] = None) -> Dict[str, Any]:
        """Check whether a Python module can be found in sys.path or extra search paths."""
        if not module_name:
            return {"module": module_name, "resolvable": False, "origin": None}

        try:
            spec = importlib.util.find_spec(module_name)
            if spec is not None:
                return {
                    "module": module_name,
                    "resolvable": True,
                    "origin": spec.origin,
                }
        except (ModuleNotFoundError, ValueError):
            pass

        for sp in search_paths or []:
            candidate_file = Path(sp) / Path(*module_name.split(".")).with_suffix(".py")
            candidate_pkg = Path(sp) / Path(*module_name.split(".")) / "__init__.py"
            if candidate_file.exists():
                return {"module": module_name, "resolvable": True, "origin": str(candidate_file)}
            if candidate_pkg.exists():
                return {"module": module_name, "resolvable": True, "origin": str(candidate_pkg)}

        return {"module": module_name, "resolvable": False, "origin": None}

    @classmethod
    def verify_imported_symbols_exist_in_env(
        cls,
        source_code: str,
        search_paths: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Verify imported modules and imported attributes actually exist in the runtime environment."""
        imp_data = cls.extract_imports(source_code)
        if not imp_data["valid_syntax"]:
            return {
                "valid": False,
                "error": imp_data["error"],
                "missing_modules": [],
                "missing_symbols": [],
            }

        missing_modules: List[Dict[str, Any]] = []
        missing_symbols: List[Dict[str, Any]] = []

        for item in imp_data["imports"]:
            mod_name = item["module"]
            res = cls.check_module_resolvable(mod_name, search_paths=search_paths)
            if not res["resolvable"]:
                missing_modules.append({
                    "module": mod_name,
                    "line": item["line"],
                })

        for item in imp_data["from_imports"]:
            if item["level"] > 0 or item["name"] == "*":
                continue
            mod_name = item["module"]
            sym_name = item["name"]
            res = cls.check_module_resolvable(mod_name, search_paths=search_paths)
            if not res["resolvable"]:
                missing_modules.append({
                    "module": mod_name,
                    "line": item["line"],
                })
                continue

            try:
                mod_obj = importlib.import_module(mod_name)
                if not hasattr(mod_obj, sym_name):
                    missing_symbols.append({
                        "module": mod_name,
                        "symbol": sym_name,
                        "line": item["line"],
                    })
            except Exception as exc:
                missing_symbols.append({
                    "module": mod_name,
                    "symbol": sym_name,
                    "line": item["line"],
                    "import_error": str(exc),
                })

        return {
            "valid": len(missing_modules) == 0 and len(missing_symbols) == 0,
            "missing_modules": missing_modules,
            "missing_symbols": missing_symbols,
        }

    @classmethod
    def verify_call_argument_counts(
        cls,
        source_code: str,
        expected_arities: Optional[Dict[str, Dict[str, int]]] = None,
    ) -> Dict[str, Any]:
        """
        Verify direct function call argument counts against expected arity specs.
        expected_arities format: {"func_name": {"min_args": 1, "max_args": 3}}
        """
        parsed = cls.parse_ast(source_code)
        if not parsed["valid_syntax"]:
            return {"valid": False, "error": parsed["error"], "mismatches": []}

        specs = expected_arities or {}
        mismatches: List[Dict[str, Any]] = []
        tree: ast.AST = parsed["tree"]

        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                fname = node.func.id
                if fname in specs:
                    positional_count = len(node.args)
                    min_args = specs[fname].get("min_args", 0)
                    max_args = specs[fname].get("max_args")
                    if positional_count < min_args or (
                        max_args is not None and positional_count > max_args
                    ):
                        mismatches.append({
                            "function": fname,
                            "line": getattr(node, "lineno", 0),
                            "positional_args_passed": positional_count,
                            "expected_min": min_args,
                            "expected_max": max_args,
                        })

        return {
            "valid": len(mismatches) == 0,
            "mismatches": mismatches,
        }

    @classmethod
    def verify_source(
        cls,
        source_code: str,
        check_runtime_imports: bool = False,
        search_paths: Optional[List[str]] = None,
        expected_arities: Optional[Dict[str, Dict[str, int]]] = None,
    ) -> Dict[str, Any]:
        """Unified verification entry point for syntax, symbols, imports, and call arities."""
        defs = cls.extract_defined_symbols(source_code)
        if not defs["valid_syntax"]:
            return {
                "valid": False,
                "error": defs["error"],
                "defined_functions": [],
                "defined_classes": [],
                "runtime_imports": None,
                "arity_check": None,
            }

        runtime_res = None
        if check_runtime_imports:
            runtime_res = cls.verify_imported_symbols_exist_in_env(
                source_code,
                search_paths=search_paths,
            )

        arity_res = None
        if expected_arities is not None:
            arity_res = cls.verify_call_argument_counts(
                source_code,
                expected_arities=expected_arities,
            )

        is_valid = True
        if runtime_res is not None and not runtime_res["valid"]:
            is_valid = False
        if arity_res is not None and not arity_res["valid"]:
            is_valid = False

        return {
            "valid": is_valid,
            "defined_functions": [f["name"] for f in defs["functions"]],
            "defined_classes": [c["name"] for c in defs["classes"]],
            "runtime_imports": runtime_res,
            "arity_check": arity_res,
        }