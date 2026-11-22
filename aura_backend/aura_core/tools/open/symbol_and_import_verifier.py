import ast
import builtins
import importlib.machinery
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

#: Parse failures that must come back as a result, never as an exception (NUL bytes raise
#: ValueError before Python 3.12; pathological nesting raises RecursionError).
_PARSE_ERRORS = (SyntaxError, ValueError, RecursionError, MemoryError)


class SymbolAndImportVerifier:
    """AST-based verifier for imports, symbol definitions, referenced names, and signatures."""

    MODULE_IMPLICIT_NAMES: Set[str] = {
        "__file__",
        "__name__",
        "__doc__",
        "__package__",
        "__spec__",
        "__loader__",
        "__annotations__",
        "__builtins__",
    }

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
        except (ValueError, RecursionError, MemoryError) as exc:
            return {
                "valid_syntax": False,
                "tree": None,
                "error": f"Source code could not be parsed: {type(exc).__name__}",
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
                assigned.add(node.name)
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
                assigned.add(node.name)
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

    @classmethod
    def _collect_bound_in_pattern(cls, pattern: ast.AST, bound: Set[str]) -> None:
        if isinstance(pattern, ast.MatchAs):
            if pattern.name:
                bound.add(pattern.name)
            if pattern.pattern:
                cls._collect_bound_in_pattern(pattern.pattern, bound)
        elif isinstance(pattern, ast.MatchStar):
            if pattern.name:
                bound.add(pattern.name)
        elif isinstance(pattern, ast.MatchMapping):
            if pattern.rest:
                bound.add(pattern.rest)
            for sub_p in pattern.patterns:
                cls._collect_bound_in_pattern(sub_p, bound)
        elif isinstance(pattern, (ast.MatchSequence, ast.MatchOr)):
            for sub_p in pattern.patterns:
                cls._collect_bound_in_pattern(sub_p, bound)
        elif isinstance(pattern, ast.MatchClass):
            for sub_p in pattern.patterns + pattern.kwd_patterns:
                cls._collect_bound_in_pattern(sub_p, bound)

    @classmethod
    def _collect_local_bindings(cls, nodes: List[ast.AST]) -> tuple[Set[str], bool]:
        local_bound: Set[str] = set()
        has_star_import = False

        def visit_stmt_or_expr(n: ast.AST) -> None:
            nonlocal has_star_import
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    local_bound.add(n.name)
                return
            if isinstance(n, ast.Import):
                for alias in n.names:
                    local_bound.add(alias.asname or alias.name.split(".")[0])
            elif isinstance(n, ast.ImportFrom):
                for alias in n.names:
                    if alias.name == "*":
                        has_star_import = True
                    else:
                        local_bound.add(alias.asname or alias.name)
            elif isinstance(n, ast.ExceptHandler) and n.name:
                local_bound.add(n.name)
            elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
                local_bound.add(n.id)
            elif isinstance(n, ast.match_case):
                cls._collect_bound_in_pattern(n.pattern, local_bound)
                if n.guard:
                    visit_stmt_or_expr(n.guard)
                for body_stmt in n.body:
                    visit_stmt_or_expr(body_stmt)
                return

            for child in ast.iter_child_nodes(n):
                if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                    visit_stmt_or_expr(child)

        for root_n in nodes:
            visit_stmt_or_expr(root_n)

        return local_bound, has_star_import

    @staticmethod
    def _type_param_names(node: ast.AST) -> Set[str]:
        """Names of PEP 695 type parameters (``def f[T]``, ``class C[T]``, ``type A[T] = ...``).
        Python 3.11's AST has no ``type_params``, so this is empty there."""
        names: Set[str] = set()
        for tp in getattr(node, "type_params", None) or []:
            name = getattr(tp, "name", None)
            if name:
                names.add(name)
        return names

    @staticmethod
    def _parameter_names(args: ast.arguments) -> Set[str]:
        names = {a.arg for a in args.posonlyargs + args.args + args.kwonlyargs}
        if args.vararg:
            names.add(args.vararg.arg)
        if args.kwarg:
            names.add(args.kwarg.arg)
        return names

    @classmethod
    def verify_undefined_names(cls, source_code: str) -> Dict[str, Any]:
        """Scope-aware check for referenced names that nothing binds.

        Follows Python's resolution rules: a function sees its own names, the names of every
        enclosing *function* (closures, ``nonlocal``), module globals and builtins. A class body
        sees the function it is defined in, but its own names are not visible inside its
        methods -- an unqualified ``LIMIT`` in a method is reported even when the class defines
        ``LIMIT``. Functions defined in a class get the implicit ``__class__`` cell, and PEP 695
        type parameters are visible where Python makes them visible. Any ``import *`` in scope
        disables reporting there, since its names cannot be known statically.
        """
        parsed = cls.parse_ast(source_code)
        if not parsed["valid_syntax"]:
            return {
                "valid": False,
                "error": parsed["error"],
                "undefined_names": [],
            }

        tree: ast.Module = parsed["tree"]
        module_bound, module_star_import = cls._collect_local_bindings(tree.body)
        module_names: Set[str] = set(dir(builtins)) | set(cls.MODULE_IMPLICIT_NAMES) | module_bound

        undefined: List[Dict[str, Any]] = []
        seen: Set[tuple] = set()

        def record(name: str, lineno: int, visible: Set[str], star_imported: bool) -> None:
            if star_imported or name in visible or name in module_names:
                return
            key = (name, lineno)
            if key not in seen:
                seen.add(key)
                undefined.append({"name": name, "line": lineno})

        def scan_scope(
            body_nodes: List[ast.AST],
            own_names: Set[str],
            enclosing: Set[str],
            kind: str,
            star_above: bool,
        ) -> None:
            """``kind`` is ``module``, ``class`` or ``function``; ``enclosing`` holds the names
            of the enclosing function scopes (never class scopes)."""
            local_names, local_star = cls._collect_local_bindings(body_nodes)
            scope_names = own_names | local_names
            star = star_above or local_star
            visible = scope_names | enclosing
            # What a function, lambda or class nested here can see from outside itself: a class
            # body is not an enclosing scope for closures, a function body is.
            child_enclosing = enclosing | scope_names if kind == "function" else enclosing

            def walk(n: ast.AST, extra: Set[str]) -> None:
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    type_params = cls._type_param_names(n)
                    for dec in n.decorator_list:
                        walk(dec, extra)
                    for default in n.args.defaults + [d for d in n.args.kw_defaults if d is not None]:
                        walk(default, extra)
                    annotation_extra = extra | type_params
                    for a in n.args.posonlyargs + n.args.args + n.args.kwonlyargs + [
                        x for x in (n.args.vararg, n.args.kwarg) if x is not None
                    ]:
                        if a.annotation:
                            walk(a.annotation, annotation_extra)
                    if n.returns:
                        walk(n.returns, annotation_extra)
                    params = cls._parameter_names(n.args) | type_params
                    if kind == "class":
                        params.add("__class__")
                    scan_scope(n.body, params, child_enclosing | extra, "function", star)
                    return

                if isinstance(n, ast.Lambda):
                    for default in n.args.defaults + [d for d in n.args.kw_defaults if d is not None]:
                        walk(default, extra)
                    scan_scope([n.body], cls._parameter_names(n.args), child_enclosing | extra, "function", star)
                    return

                if isinstance(n, ast.ClassDef):
                    type_params = cls._type_param_names(n)
                    for dec in n.decorator_list:
                        walk(dec, extra)
                    for base in n.bases:
                        walk(base, extra | type_params)
                    for kw in n.keywords:
                        walk(kw.value, extra | type_params)
                    scan_scope(n.body, set(), child_enclosing | extra | type_params, "class", star)
                    return

                type_alias = getattr(ast, "TypeAlias", None)
                if type_alias is not None and isinstance(n, type_alias):
                    walk(n.value, extra | cls._type_param_names(n))
                    return

                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
                    record(n.id, getattr(n, "lineno", 0), visible | extra, star)
                    return

                for child in ast.iter_child_nodes(n):
                    walk(child, extra)

            for stmt in body_nodes:
                walk(stmt, set())

        scan_scope(tree.body, set(), set(), "module", module_star_import)

        return {
            "valid": len(undefined) == 0,
            "undefined_names": undefined,
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
    def _locate(
        module_name: str, search_paths: Optional[List[str]] = None
    ) -> Optional[importlib.machinery.ModuleSpec]:
        """Find ``module_name``'s spec WITHOUT importing it or any parent package.

        ``importlib.util.find_spec("a.b")`` imports ``a`` to read its ``__path__``, i.e. it runs
        third-party ``__init__`` code on behalf of whatever source is being verified. Here each
        segment is located with the meta-path finders directly, passing the parent's
        ``submodule_search_locations`` instead.
        """
        parts = module_name.split(".")
        if not all(p.isidentifier() for p in parts):
            return None

        spec: Optional[importlib.machinery.ModuleSpec] = None
        for depth in range(1, len(parts) + 1):
            fullname = ".".join(parts[:depth])
            path = None if depth == 1 else spec.submodule_search_locations  # type: ignore[union-attr]
            if depth > 1 and path is None:
                return None  # parent is a plain module, so it has no submodules
            found = None
            for finder in sys.meta_path:
                find = getattr(finder, "find_spec", None)
                if find is None:
                    continue
                try:
                    found = find(fullname, path)
                except Exception:
                    found = None
                if found is not None:
                    break
            if found is None and depth == 1 and search_paths:
                found = importlib.machinery.PathFinder.find_spec(fullname, list(search_paths))
            elif found is None and depth > 1 and search_paths:
                found = importlib.machinery.PathFinder.find_spec(fullname, path)
            if found is None:
                return None
            if found.origin in ("frozen", "built-in") and fullname == module_name:
                # Frozen stdlib modules (``os`` on 3.11+) have no file in their spec, but the same
                # source sits on disk; prefer it so the module's names can be read.
                on_disk = importlib.machinery.PathFinder.find_spec(fullname, path)
                if on_disk is not None and (on_disk.origin or "").endswith(".py"):
                    found = on_disk
            spec = found
        return spec

    @classmethod
    def check_module_resolvable(cls, module_name: str, search_paths: Optional[List[str]] = None) -> Dict[str, Any]:
        """Check whether a module can be found in sys.path or extra search paths (never imports it)."""
        if not module_name:
            return {"module": module_name, "resolvable": False, "origin": None}

        try:
            spec = cls._locate(module_name, search_paths)
        except (ValueError, ImportError, OSError):
            spec = None
        if spec is not None:
            return {"module": module_name, "resolvable": True, "origin": spec.origin}
        return {"module": module_name, "resolvable": False, "origin": None}

    @classmethod
    def _module_names(cls, origin: Optional[str]) -> Optional[Tuple[Set[str], bool]]:
        """Names a ``.py`` module defines, read statically. ``None`` when that cannot be known.

        Returns ``(names, has_star_import)``. A module that re-exports with ``from x import *`` or
        has no Python source (builtin, C extension) cannot be judged without executing it, and
        executing arbitrary modules to answer a lint question is not acceptable, so those are
        reported as unverifiable rather than as missing.
        """
        if not origin or not origin.endswith(".py"):
            return None
        try:
            tree = ast.parse(Path(origin).read_text(encoding="utf-8", errors="ignore"))
        except (OSError, *_PARSE_ERRORS):
            return None

        names: Set[str] = set()
        star = False
        # Module scope only: descend through if/try/with/for/while (conditional definitions are
        # still module attributes) but not into function or class bodies, whose names are local.
        pending: List[ast.AST] = list(tree.body)
        while pending:
            node = pending.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
                continue
            if isinstance(node, ast.Lambda):
                continue
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                names.add(node.id)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    names.add(alias.asname or alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == "*":
                        star = True
                    else:
                        names.add(alias.asname or alias.name)
            elif isinstance(node, ast.Global):
                names.update(node.names)
            pending.extend(ast.iter_child_nodes(node))
        return names, star

    @classmethod
    def verify_imported_symbols_exist_in_env(
        cls,
        source_code: str,
        search_paths: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Verify imported modules and imported names exist, WITHOUT importing or executing anything.

        Modules are located on disk and their source is read with ``ast``. A name that cannot be
        checked statically (C extension, builtin, ``import *`` re-export) is listed in
        ``unverified_symbols`` and does not make the result invalid.
        """
        imp_data = cls.extract_imports(source_code)
        if not imp_data["valid_syntax"]:
            return {
                "valid": False,
                "error": imp_data["error"],
                "missing_modules": [],
                "missing_symbols": [],
                "unverified_symbols": [],
            }

        missing_modules: List[Dict[str, Any]] = []
        missing_symbols: List[Dict[str, Any]] = []
        unverified: List[Dict[str, Any]] = []
        names_cache: Dict[str, Optional[Tuple[Set[str], bool]]] = {}

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

            # ``from pkg import submodule`` is valid without the package defining the name.
            if cls.check_module_resolvable(f"{mod_name}.{sym_name}", search_paths=search_paths)["resolvable"]:
                continue

            origin = res["origin"]
            if origin not in names_cache:
                names_cache[origin] = cls._module_names(origin)
            known = names_cache[origin]
            if known is None:
                unverified.append({
                    "module": mod_name,
                    "symbol": sym_name,
                    "line": item["line"],
                    "reason": "module has no Python source to inspect",
                })
                continue
            names, star = known
            if sym_name in names:
                continue
            if star or "__getattr__" in names:
                unverified.append({
                    "module": mod_name,
                    "symbol": sym_name,
                    "line": item["line"],
                    "reason": "module resolves names dynamically ('import *' or module __getattr__)",
                })
                continue
            missing_symbols.append({
                "module": mod_name,
                "symbol": sym_name,
                "line": item["line"],
            })

        return {
            "valid": len(missing_modules) == 0 and len(missing_symbols) == 0,
            "missing_modules": missing_modules,
            "missing_symbols": missing_symbols,
            "unverified_symbols": unverified,
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
                    # ``f(*a)`` / ``f(**kw)`` pass a number of arguments nobody can know statically.
                    if any(isinstance(a, ast.Starred) for a in node.args) or any(
                        kw.arg is None for kw in node.keywords
                    ):
                        continue
                    positional_count = len(node.args)
                    # Keyword arguments fill parameters too, so the spec bounds the TOTAL passed.
                    total_count = positional_count + len(node.keywords)
                    min_args = specs[fname].get("min_args", 0)
                    max_args = specs[fname].get("max_args")
                    if total_count < min_args or (
                        max_args is not None and total_count > max_args
                    ):
                        mismatches.append({
                            "function": fname,
                            "line": getattr(node, "lineno", 0),
                            "total_args_passed": total_count,
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
        check_undefined_names: bool = False,
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
                "undefined_names_check": None,
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

        undef_res = None
        if check_undefined_names:
            undef_res = cls.verify_undefined_names(source_code)

        is_valid = True
        if runtime_res is not None and not runtime_res["valid"]:
            is_valid = False
        if arity_res is not None and not arity_res["valid"]:
            is_valid = False
        if undef_res is not None and not undef_res["valid"]:
            is_valid = False

        return {
            "valid": is_valid,
            "defined_functions": [f["name"] for f in defs["functions"]],
            "defined_classes": [c["name"] for c in defs["classes"]],
            "runtime_imports": runtime_res,
            "arity_check": arity_res,
            "undefined_names_check": undef_res,
        }