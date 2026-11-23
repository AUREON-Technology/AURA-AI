"""Diagrams derived from project text rather than written by hand (ADR-044, row 8).

``MermaidDiagramGenerator`` renders whatever spec it is given and, from code, only class diagrams.
These functions *derive* the spec from what the caller supplies:

* ``er_from_sql``  -- an ER diagram from SQL DDL or Alembic migration text (tables, columns, PK/FK
  markers, one relationship per foreign key), using the same parser as ``inspect_sql``.
* ``flowchart_from_source`` -- the control flow of one Python function: statements, branches (yes/no
  edges), loops (with a back edge), returns and raises.
* ``sequence_from_source`` -- who calls whom, starting from one function: calls to functions in the
  same module and to ``obj.method()`` receivers, in source order, to a bounded depth.
* ``architecture_from_modules`` -- the import graph of several supplied files, grouped by top-level
  package, with external libraries collapsed into one node per package.
* ``validate_deep`` -- ``validate_mermaid_syntax`` plus the structural mistakes that make Mermaid
  refuse a diagram (unbalanced brackets and quotes, relations to undeclared entities, messages
  between undeclared participants, edges to nodes never defined).

Nothing is executed or imported; each function only parses text. Output sizes are capped and a
capped diagram says so (``truncated``).
"""

from __future__ import annotations

import ast
import re
from typing import Any

from .mermaid_diagram_generator import MermaidDiagramGenerator as M
from .sql_schema_ext import parse_schema

__all__ = ["er_from_sql", "flowchart_from_source", "sequence_from_source", "architecture_from_modules",
           "validate_deep"]

_MAX_NODES = 60
_MAX_MESSAGES = 80
_MAX_MODULES = 60
_LABEL = 60


def er_from_sql(text: str) -> dict[str, Any]:
    schema = parse_schema(text)
    if not schema["tables"]:
        return {"error": "no tables found in the supplied text", "notes": schema["notes"]}
    entities: dict[str, list[dict[str, str]]] = {}
    for name, table in schema["tables"].items():
        fk_cols = {c for fk in table["foreign_keys"] for c in fk["columns"]}
        fields = []
        for column in table["columns"]:
            key = "PK" if column["name"] in table["primary_key"] else "FK" if column["name"] in fk_cols \
                else "UK" if column["unique"] else ""
            ctype = re.sub(r"\(.*", "", column["type"]).strip().replace(" ", "_") or "unknown"
            fields.append({"name": column["name"], "type": ctype, **({"key": key} if key else {})})
        entities[name] = fields
    relationships = []
    for name, table in schema["tables"].items():
        for fk in table["foreign_keys"]:
            if fk["ref_table"] in schema["tables"]:
                nullable = any(c["nullable"] for c in table["columns"] if c["name"] in fk["columns"])
                relationships.append({"left": fk["ref_table"], "right": name,
                                      "cardinality": "|o--o{" if nullable else "||--o{",
                                      "label": ", ".join(fk["columns"])})
    return {"mermaid": M.generate_er_diagram(entities, relationships), "tables": len(entities),
            "relationships": len(relationships), "source_format": schema["source_format"]}


def _short(node: ast.AST) -> str:
    text = " ".join(ast.unparse(node).split())
    return text if len(text) <= _LABEL else text[: _LABEL - 1] + "..."


class _Flow:
    def __init__(self) -> None:
        self.nodes: list[dict[str, str]] = []
        self.edges: list[dict[str, str]] = []
        self.truncated = False
        self.loops: list[tuple[str, list[tuple[str, str]]]] = []

    def node(self, label: str, shape: str = "rect") -> str | None:
        if len(self.nodes) >= _MAX_NODES:
            self.truncated = True
            return None
        node_id = f"n{len(self.nodes) + 1}"
        self.nodes.append({"id": node_id, "label": label, "shape": shape})
        return node_id

    def link(self, sources: list[tuple[str, str]], target: str) -> None:
        for source, label in sources:
            self.edges.append({"from": source, "to": target, **({"label": label} if label else {})})

    def block(self, stmts: list[ast.stmt], entry: list[tuple[str, str]]) -> list[tuple[str, str]]:
        """Add ``stmts`` after ``entry`` (dangling edges); return the dangling exits."""
        current = entry
        for stmt in stmts:
            if not current:
                break  # unreachable code after return/raise: leave it out of the flow
            if isinstance(stmt, ast.If):
                test = self.node(f"{_short(stmt.test)}?", "diamond")
                if test is None:
                    return current
                self.link(current, test)
                then_exits = self.block(stmt.body, [(test, "yes")])
                else_exits = self.block(stmt.orelse, [(test, "no")]) if stmt.orelse else [(test, "no")]
                current = then_exits + else_exits
            elif isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)):
                head = f"for {_short(stmt.target)} in {_short(stmt.iter)}" if not isinstance(stmt, ast.While) \
                    else f"while {_short(stmt.test)}"
                loop = self.node(head + "?", "diamond")
                if loop is None:
                    return current
                self.link(current, loop)
                self.loops.append((loop, []))
                body_exits = self.block(stmt.body, [(loop, "yes")])
                self.link(body_exits, loop)
                _, breaks = self.loops.pop()
                current = [(loop, "done")] + breaks
                if stmt.orelse:
                    current = self.block(stmt.orelse, current)
            elif isinstance(stmt, ast.Continue) and self.loops:
                self.link(current, self.loops[-1][0])
                current = []
            elif isinstance(stmt, ast.Break) and self.loops:
                self.loops[-1][1].extend(current)
                current = []
            elif isinstance(stmt, ast.Return):
                end = self.node(f"return {_short(stmt.value)}" if stmt.value else "return", "stadium")
                if end is not None:
                    self.link(current, end)
                current = []
            elif isinstance(stmt, ast.Raise):
                end = self.node(f"raise {_short(stmt.exc)}" if stmt.exc else "raise", "stadium")
                if end is not None:
                    self.link(current, end)
                current = []
            elif isinstance(stmt, (ast.Try, getattr(ast, "TryStar", ast.Try))):
                label = self.node("try", "round")
                if label is None:
                    return current
                self.link(current, label)
                exits = self.block(stmt.body, [(label, "")])
                for handler in stmt.handlers:
                    caught = _short(handler.type) if handler.type else "any error"
                    exits += self.block(handler.body, [(label, f"except {caught}")])
                if stmt.orelse:
                    exits = self.block(stmt.orelse, exits) if exits else exits
                current = self.block(stmt.finalbody, exits) if stmt.finalbody and exits else exits
            elif isinstance(stmt, (ast.With, ast.AsyncWith)):
                head = self.node("with " + ", ".join(_short(i.context_expr) for i in stmt.items))
                if head is None:
                    return current
                self.link(current, head)
                current = self.block(stmt.body, [(head, "")])
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            else:
                simple = self.node(_short(stmt))
                if simple is None:
                    return current
                self.link(current, simple)
                current = [(simple, "")]
        return current


def _find_function(tree: ast.AST, name: str | None) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    if name:
        return next((f for f in functions if f.name == name), None)
    return functions[0] if functions else None


def flowchart_from_source(source: str, function: str | None = None, direction: str = "TD") -> dict[str, Any]:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return {"error": f"SyntaxError: {exc}"}
    fn = _find_function(tree, function)
    if fn is None:
        return {"error": f"function {function!r} not found" if function else "no function in the source"}
    flow = _Flow()
    args = ", ".join(a.arg for a in [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs])
    start = flow.node(f"{fn.name}({args})", "stadium")
    exits = flow.block(fn.body, [(start, "")]) if start else []
    if exits:
        end = flow.node("end", "stadium")
        if end:
            flow.link(exits, end)
    return {"mermaid": M.generate_flowchart(flow.nodes, flow.edges, direction=direction), "function": fn.name,
            "nodes": len(flow.nodes), "edges": len(flow.edges), "truncated": flow.truncated}


def sequence_from_source(source: str, entry: str | None = None, depth: int = 3) -> dict[str, Any]:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return {"error": f"SyntaxError: {exc}"}
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    owners: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions[node.name] = node
            owners[node.name] = "module"
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    functions.setdefault(item.name, item)
                    owners.setdefault(item.name, node.name)
    root = entry or next(iter(functions), None)
    if root is None or root not in functions:
        return {"error": f"entry {entry!r} not found" if entry else "no function in the source"}
    participants: list[str] = []
    messages: list[dict[str, str]] = []
    truncated = False

    def party(name: str) -> str:
        if name not in participants:
            participants.append(name)
        return name

    def calls_in(fn: ast.AST) -> list[ast.Call]:
        found = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
        return sorted(found, key=lambda c: (c.lineno, c.col_offset))

    def walk(name: str, caller: str, level: int, seen: tuple[str, ...]) -> None:
        nonlocal truncated
        fn = functions[name]
        for call in calls_in(fn):
            if len(messages) >= _MAX_MESSAGES:
                truncated = True
                return
            func = call.func
            target: str | None = None
            callee: str | None = None
            if isinstance(func, ast.Name) and func.id in functions:
                callee, target = func.id, owners[func.id]
            elif isinstance(func, ast.Attribute):
                receiver = func.value
                if isinstance(receiver, ast.Name):
                    if receiver.id in ("self", "cls") and func.attr in functions:
                        callee, target = func.attr, owners[func.attr]
                    elif receiver.id != "self":
                        callee, target = None, receiver.id
            if target is None:
                continue
            shown = ", ".join(_short(a)[:15] for a in call.args[:3])
            label = f"{callee or func.attr}({shown})"
            messages.append({"from": party(caller), "to": party(target), "text": label})
            if callee and callee not in seen and level < depth:
                walk(callee, target, level + 1, (*seen, callee))

    party(owners[root] if owners[root] != "module" else root)
    walk(root, owners[root] if owners[root] != "module" else root, 1, (root,))
    if not messages:
        return {"error": f"'{root}' makes no calls to other functions in this module or to named objects",
                "mermaid": M.generate_sequence_diagram(participants, [])}
    return {"mermaid": M.generate_sequence_diagram(participants, messages), "entry": root,
            "participants": participants, "messages": len(messages), "truncated": truncated}


def architecture_from_modules(modules: dict[str, str], direction: str = "LR") -> dict[str, Any]:
    if len(modules) > _MAX_MODULES:
        return {"error": f"{len(modules)} modules; the limit is {_MAX_MODULES}"}
    from .review_ext import module_name_from_path

    names = {module_name_from_path(path) or re.sub(r"\.py$", "", path).replace("/", "."): path for path in modules}
    ids = M._Ids()
    edges: set[tuple[str, str]] = set()
    external: set[str] = set()
    parse_errors: list[str] = []
    for module, path in names.items():
        try:
            tree = ast.parse(modules[path])
        except SyntaxError as exc:
            parse_errors.append(f"{path}: {exc.msg} (line {exc.lineno})")
            continue
        for node in ast.walk(tree):
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                targets = [node.module]
            for target in targets:
                match = next((m for m in sorted(names, key=len, reverse=True)
                              if target == m or target.startswith(m + ".") or m.startswith(target + ".")), None)
                if match and match != module:
                    edges.add((module, match))
                elif not match:
                    top = target.split(".")[0]
                    if top not in {m.split(".")[0] for m in names}:
                        external.add(top)
                        edges.add((module, f"ext:{top}"))
    lines = [f"flowchart {direction if direction in ('TD', 'TB', 'BT', 'LR', 'RL') else 'LR'}"]
    groups: dict[str, list[str]] = {}
    for module in names:
        groups.setdefault(module.split(".")[0] if "." in module else "(root)", []).append(module)
    for group, members in groups.items():
        if len(groups) > 1:
            lines.append(f'    subgraph {ids.get("g_" + group)}["{M.escape_label(group)}"]')
        for module in members:
            lines.append(f'    {ids.get(module)}["{M.escape_label(module)}"]')
        if len(groups) > 1:
            lines.append("    end")
    for lib in sorted(external):
        lines.append(f'    {ids.get("ext:" + lib)}(["{M.escape_label(lib)}"])')
    for src, dst in sorted(edges):
        lines.append(f"    {ids.get(src)} --> {ids.get(dst)}")
    return {"mermaid": "\n".join(lines), "modules": len(names), "dependencies": len(edges),
            "external_packages": sorted(external), "parse_errors": parse_errors}


def validate_deep(code: str) -> dict[str, Any]:
    """``validate_mermaid_syntax`` plus structural checks; ``valid`` is false for any ``errors``."""
    base = M.validate_mermaid_syntax(code)
    errors: list[str] = [] if base.get("valid") else [base.get("error", "invalid")]
    warnings: list[str] = []
    lines = [ln for ln in code.strip().splitlines()]
    header = lines[0].strip() if lines else ""
    kind = next((k for k in ("classDiagram", "sequenceDiagram", "erDiagram", "flowchart", "graph", "stateDiagram")
                 if header.startswith(k)), "unknown")
    for number, line in enumerate(lines, start=1):
        if line.count('"') % 2:
            errors.append(f"line {number}: unbalanced double quote")
        for open_, close in (("[", "]"), ("(", ")"), ("{", "}")):
            if kind in ("flowchart", "graph") and line.count(open_) != line.count(close):
                errors.append(f"line {number}: unbalanced '{open_}{close}'")
    declared: set[str] = set()
    used: set[str] = set()
    if kind == "sequenceDiagram":
        for ln in lines[1:]:
            s = ln.strip()
            if s.startswith(("participant ", "actor ")):
                declared.add(s.split()[1])
            m = re.match(r"([\w]+)\s*(?:-->>|->>|-->|->|--x|-x|--\)|-\))\+?-?\s*([\w]+)\s*:", s)
            if m:
                used.update(m.groups())
        undeclared = sorted(used - declared) if declared else []
        if undeclared:
            warnings.append(f"messages mention participants never declared: {', '.join(undeclared)}")
    elif kind == "erDiagram":
        entity_pattern = re.compile(r"^\s*([\w]+)\s*\{")
        declared = {m.group(1) for ln in lines[1:] if (m := entity_pattern.match(ln))}
        for number, ln in enumerate(lines[1:], start=2):
            m = re.match(r"\s*([\w]+)\s+[|}o][|o]?(?:--|\.\.)[|o]?[|o{]\s+([\w]+)\s*:", ln)
            if m:
                for side in m.groups():
                    if side not in declared:
                        errors.append(f"line {number}: relationship uses entity '{side}' that has no block")
    elif kind in ("flowchart", "graph"):
        defined = {m.group(1) for ln in lines[1:] if (m := re.match(r"^\s*([\w]+)\s*[\[({]", ln))}
        for number, ln in enumerate(lines[1:], start=2):
            m = re.match(r"^\s*([\w]+)\s*-->(?:\|[^|]*\|)?\s*([\w]+)\s*$", ln)
            if m:
                for side in m.groups():
                    if side not in defined:
                        warnings.append(f"line {number}: edge uses '{side}', which is never given a label/shape")
    return {"valid": not errors, "diagram_type": kind, "errors": errors, "warnings": warnings[:20],
            "checked": "header, braces, quotes, brackets and entity/participant/node references; not a "
                       "render (no Mermaid engine runs here)"}
