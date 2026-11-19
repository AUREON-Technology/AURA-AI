import ast
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


class ASTCodeAnalyzer:
    

    @staticmethod
    def _format_node(node: Optional[ast.AST]) -> Optional[str]:
        if node is None:
            return None
        try:
            return ast.unparse(node)
        except Exception:
            return None

    @classmethod
    def extract_dependencies(cls, tree: ast.AST) -> List[Dict[str, Any]]:
        dependencies: List[Dict[str, Any]] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    dependencies.append({
                        "type": "import",
                        "module": alias.name,
                        "name": alias.name,
                        "asname": alias.asname,
                        "level": 0,
                        "line": node.lineno,
                    })
            elif isinstance(node, ast.ImportFrom):
                module_name = node.module or ""
                for alias in node.names:
                    dependencies.append({
                        "type": "from_import",
                        "module": module_name,
                        "name": alias.name,
                        "asname": alias.asname,
                        "level": node.level,
                        "line": node.lineno,
                    })

        return dependencies

    @classmethod
    def extract_constants_and_globals(cls, tree: ast.Module) -> List[Dict[str, Any]]:
        globals_list: List[Dict[str, Any]] = []

        for stmt in tree.body:
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        name = target.id
                        globals_list.append({
                            "name": name,
                            "is_constant": name.isupper(),
                            "annotation": None,
                            "value": cls._format_node(stmt.value),
                            "lineno": stmt.lineno,
                        })
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                name = stmt.target.id
                globals_list.append({
                    "name": name,
                    "is_constant": name.isupper(),
                    "annotation": cls._format_node(stmt.annotation),
                    "value": cls._format_node(stmt.value),
                    "lineno": stmt.lineno,
                })

        return globals_list

    @classmethod
    def extract_calls(cls, node: ast.AST) -> List[Dict[str, Any]]:
        calls: List[Dict[str, Any]] = []
        seen = set()

        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                call_name = cls._format_node(sub.func)
                if call_name:
                    key = (call_name, sub.lineno)
                    if key not in seen:
                        seen.add(key)
                        calls.append({
                            "call": call_name,
                            "args_count": len(sub.args),
                            "kwargs_count": len(sub.keywords),
                            "lineno": sub.lineno,
                        })

        return calls

    @classmethod
    def extract_function_signature(
        cls,
        node: Union[ast.FunctionDef, ast.AsyncFunctionDef],
        class_name: Optional[str] = None,
        include_calls: bool = True,
    ) -> Dict[str, Any]:
        args = node.args
        params: List[Dict[str, Any]] = []

        all_positional = list(args.posonlyargs) + list(args.args)
        defaults_start_idx = len(all_positional) - len(args.defaults)

        for idx, arg in enumerate(all_positional):
            default_val = None
            if idx >= defaults_start_idx:
                default_node = args.defaults[idx - defaults_start_idx]
                default_val = cls._format_node(default_node)

            params.append({
                "name": arg.arg,
                "kind": "positional_only" if idx < len(args.posonlyargs) else "positional_or_keyword",
                "annotation": cls._format_node(arg.annotation),
                "default": default_val,
            })

        if args.vararg:
            params.append({
                "name": f"*{args.vararg.arg}",
                "kind": "var_positional",
                "annotation": cls._format_node(args.vararg.annotation),
                "default": None,
            })

        for idx, kw_arg in enumerate(args.kwonlyargs):
            kw_default_node = args.kw_defaults[idx] if idx < len(args.kw_defaults) else None
            params.append({
                "name": kw_arg.arg,
                "kind": "keyword_only",
                "annotation": cls._format_node(kw_arg.annotation),
                "default": cls._format_node(kw_default_node),
            })

        if args.kwarg:
            params.append({
                "name": f"**{args.kwarg.arg}",
                "kind": "var_keyword",
                "annotation": cls._format_node(args.kwarg.annotation),
                "default": None,
            })

        formatted_params: List[str] = []
        for p in params:
            part = p["name"]
            if p["annotation"]:
                part += f": {p['annotation']}"
            if p["default"] is not None and p["kind"] not in ("var_positional", "var_keyword"):
                part += f" = {p['default']}"
            formatted_params.append(part)

        return_annotation = cls._format_node(node.returns)
        prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        signature_str = f"{prefix} {node.name}({', '.join(formatted_params)})"
        if return_annotation:
            signature_str += f" -> {return_annotation}"

        result: Dict[str, Any] = {
            "name": node.name,
            "class_name": class_name,
            "is_async": isinstance(node, ast.AsyncFunctionDef),
            "is_method": class_name is not None,
            "signature": signature_str,
            "parameters": params,
            "return_annotation": return_annotation,
            "decorators": [
                d_str for d in node.decorator_list if (d_str := cls._format_node(d)) is not None
            ],
            "docstring": ast.get_docstring(node),
            "lineno": node.lineno,
            "end_lineno": getattr(node, "end_lineno", node.lineno),
        }

        if include_calls:
            result["calls"] = cls.extract_calls(node)

        return result

    @classmethod
    def extract_classes(cls, tree: ast.Module, include_calls: bool = True) -> List[Dict[str, Any]]:
        """استخراج الكلاسات، الوراثة، والـ methods والـ attributes الخاصة بكل كلاس."""
        classes: List[Dict[str, Any]] = []

        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                bases = [
                    b_str for b in node.bases if (b_str := cls._format_node(b)) is not None
                ]
                keywords = [
                    f"{kw.arg}={cls._format_node(kw.value)}"
                    for kw in node.keywords
                    if kw.arg
                ]
                methods: List[Dict[str, Any]] = []
                class_attributes: List[Dict[str, Any]] = []

                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        methods.append(
                            cls.extract_function_signature(
                                item, class_name=node.name, include_calls=include_calls
                            )
                        )
                    elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                        class_attributes.append({
                            "name": item.target.id,
                            "annotation": cls._format_node(item.annotation),
                            "value": cls._format_node(item.value),
                            "lineno": item.lineno,
                        })
                    elif isinstance(item, ast.Assign):
                        for target in item.targets:
                            if isinstance(target, ast.Name):
                                class_attributes.append({
                                    "name": target.id,
                                    "annotation": None,
                                    "value": cls._format_node(item.value),
                                    "lineno": item.lineno,
                                })

                classes.append({
                    "name": node.name,
                    "bases": bases + keywords,
                    "decorators": [
                        d_str for d in node.decorator_list if (d_str := cls._format_node(d)) is not None
                    ],
                    "methods": methods,
                    "attributes": class_attributes,
                    "docstring": ast.get_docstring(node),
                    "lineno": node.lineno,
                    "end_lineno": getattr(node, "end_lineno", node.lineno),
                })

        return classes

    @classmethod
    def extract_top_level_functions(
        cls, tree: ast.Module, include_calls: bool = True
    ) -> List[Dict[str, Any]]:
        functions: List[Dict[str, Any]] = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.append(
                    cls.extract_function_signature(node, class_name=None, include_calls=include_calls)
                )
        return functions

    @classmethod
    def to_compact_summary(cls, analysis: Dict[str, Any]) -> Dict[str, Any]:
        if analysis.get("status") != "success":
            return analysis

        compact_classes = [
            {
                "name": c["name"],
                "bases": c["bases"],
                "methods": [m["signature"] for m in c["methods"]],
            }
            for c in analysis.get("classes", [])
        ]

        compact_functions = [
            f["signature"] for f in analysis.get("functions", [])
        ]

        return {
            "status": "success",
            "file_path": analysis.get("file_path"),
            "dependencies": analysis.get("dependency_modules", []),
            "constants": [g["name"] for g in analysis.get("globals", []) if g.get("is_constant")],
            "classes": compact_classes,
            "functions": compact_functions,
        }

    @classmethod
    def ast_code_analyzer(
        cls,
        source_code: str,
        include_methods_in_functions: bool = False,
        include_calls: bool = True,
        compact: bool = False,
        file_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            tree = ast.parse(source_code)
        except SyntaxError as e:
            return {
                "status": "error",
                "error": f"Syntax error in source code: {e.msg}",
                "line": e.lineno,
                "offset": e.offset,
                "file_path": file_path,
            }

        dependencies = cls.extract_dependencies(tree)
        globals_list = cls.extract_constants_and_globals(tree)
        classes = cls.extract_classes(tree, include_calls=include_calls)
        functions = cls.extract_top_level_functions(tree, include_calls=include_calls)

        if include_methods_in_functions:
            for cls_info in classes:
                functions.extend(cls_info["methods"])

        dependency_modules = sorted({
            dep["module"] for dep in dependencies if dep.get("module")
        })

        full_result: Dict[str, Any] = {
            "status": "success",
            "file_path": file_path,
            "classes": classes,
            "functions": functions,
            "globals": globals_list,
            "dependencies": dependencies,
            "dependency_modules": dependency_modules,
            "summary": {
                "class_count": len(classes),
                "function_count": len(functions),
                "globals_count": len(globals_list),
                "dependency_count": len(dependencies),
            },
        }

        return cls.to_compact_summary(full_result) if compact else full_result

    @classmethod
    def analyze_file(
        cls,
        file_path: Union[str, Path],
        include_methods_in_functions: bool = False,
        include_calls: bool = True,
        compact: bool = False,
    ) -> Dict[str, Any]:
        path = Path(file_path)
        if not path.exists():
            return {"status": "error", "error": f"File not found: {path}"}
        if not path.is_file():
            return {"status": "error", "error": f"Path is not a file: {path}"}

        try:
            source_code = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            source_code = path.read_text(encoding="latin-1")
        except Exception as e:
            return {"status": "error", "error": f"Failed to read file: {str(e)}"}

        return cls.ast_code_analyzer(
            source_code=source_code,
            include_methods_in_functions=include_methods_in_functions,
            include_calls=include_calls,
            compact=compact,
            file_path=str(path),
        )