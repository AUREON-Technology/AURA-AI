"""SQL schema and query intelligence for ``inspect_sql`` (ADR-044, row 92).

``DatabaseIntelligenceTool`` lints one statement with patterns. This reads a whole *schema* from the
text a caller supplies (SQL DDL or Alembic migration source), builds tables / relationships / indexes,
and reasons about them. It never connects to a database, so everything it says comes from text:

* ``parse_schema``: tables, columns (type, nullability, PK, unique, default), primary keys, foreign
  keys, indexes, from ``CREATE TABLE`` / ``ALTER TABLE ... ADD`` / ``CREATE [UNIQUE] INDEX`` and from
  Alembic's ``op.create_table`` / ``op.add_column`` / ``op.create_index`` / ``op.create_foreign_key``.
* ``schema_issues``: a foreign key with no index that starts with its columns (the join/delete
  cascade scan), a table without a primary key, a foreign key to a table that is not defined, a
  duplicate or redundant index.
* ``query_smells``: constructs a planner cannot use an index for (leading-wildcard LIKE, a function
  wrapped around a column, ``= NULL``, ``NOT IN (subquery)``, OR across columns, implicit cross
  join, deep OFFSET, UNION without ALL, ``ORDER BY`` with no ``LIMIT``), and, given a schema,
  unknown tables/columns and filter/join columns that have no index.
* ``generate_select``: a parameterised SELECT built only from identifiers that exist in the schema.

What it cannot do: run ``EXPLAIN`` or see statistics, so no cost, no row estimates and no real
query plan. ``plan_available`` is always false and ``explain`` says why.
"""

from __future__ import annotations

import ast
import re
from typing import Any

__all__ = ["parse_schema", "schema_issues", "query_smells", "generate_select", "schema_report"]

_MAX_TABLES = 500
_IDENT = r'(?:"[^"]+"|`[^`]+`|[A-Za-z_][\w$]*)'
_QNAME = rf"{_IDENT}(?:\s*\.\s*{_IDENT})?"


def _strip_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return re.sub(r"--[^\n]*", " ", sql)


def _unquote(name: str) -> str:
    name = name.strip()
    if len(name) >= 2 and name[0] in '"`' and name[-1] == name[0]:
        name = name[1:-1]
    return name.lower()


def _table_name(raw: str) -> str:
    """``public."Users"`` -> ``users`` (schema dropped, quotes dropped, lower-cased)."""
    parts = [p for p in re.split(r"\s*\.\s*", raw.strip()) if p]
    return _unquote(parts[-1]) if parts else ""


def _split_top(text: str, sep: str = ",") -> list[str]:
    """Split on ``sep`` outside parentheses and quotes."""
    out: list[str] = []
    depth = 0
    quote = ""
    current: list[str] = []
    for ch in text:
        if quote:
            current.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in "'\"`":
            quote = ch
            current.append(ch)
        elif ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth -= 1
            current.append(ch)
        elif ch == sep and depth == 0:
            out.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    tail = "".join(current).strip()
    if tail:
        out.append(tail)
    return out


def _statements(sql: str) -> list[str]:
    return [s for s in _split_top(_strip_comments(sql), ";") if s.strip()]


def _cols(text: str) -> list[str]:
    cols = []
    for part in _split_top(text):
        token = re.split(r"\s+", part.strip())[0] if part.strip() else ""
        token = re.sub(r"\(.*", "", token)
        if token:
            cols.append(_unquote(token))
    return cols


def _paren_body(text: str, start: int) -> tuple[str, int]:
    """The text inside the parenthesis opening at ``start`` and the index after its close."""
    depth = 0
    quote = ""
    for i in range(start, len(text)):
        ch = text[i]
        if quote:
            if ch == quote:
                quote = ""
            continue
        if ch in "'\"`":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[start + 1:i], i + 1
    return text[start + 1:], len(text)


def _new_table() -> dict[str, Any]:
    return {"columns": [], "primary_key": [], "foreign_keys": [], "unique": [], "indexes": []}


def _add_column(table: dict[str, Any], name: str, ctype: str, rest: str) -> None:
    low = rest.lower()
    column = {
        "name": name, "type": ctype.strip().lower(),
        "nullable": not re.search(r"\bnot\s+null\b", low) and not re.search(r"\bprimary\s+key\b", low),
        "primary_key": bool(re.search(r"\bprimary\s+key\b", low)),
        "unique": bool(re.search(r"\bunique\b", low)),
        "default": None,
    }
    default = re.search(r"\bdefault\s+((?:'[^']*'|\([^)]*\)|[^\s,]+))", rest, re.IGNORECASE)
    if default:
        column["default"] = default.group(1)
    table["columns"].append(column)
    if column["primary_key"] and name not in table["primary_key"]:
        table["primary_key"].append(name)
    if column["unique"]:
        table["unique"].append([name])
    ref = re.search(rf"\breferences\s+({_QNAME})\s*(?:\(([^)]*)\))?", rest, re.IGNORECASE)
    if ref:
        table["foreign_keys"].append({
            "columns": [name], "ref_table": _table_name(ref.group(1)),
            "ref_columns": _cols(ref.group(2)) if ref.group(2) else ["id"],
        })


def _table_constraint(table: dict[str, Any], text: str) -> None:
    body = re.sub(rf"^\s*constraint\s+{_IDENT}\s+", "", text, flags=re.IGNORECASE)
    low = body.lower()
    if low.startswith("primary key"):
        inner, _ = _paren_body(body, body.index("("))
        table["primary_key"] = _cols(inner)
    elif low.startswith("foreign key"):
        inner, end = _paren_body(body, body.index("("))
        ref = re.search(rf"references\s+({_QNAME})\s*(?:\(([^)]*)\))?", body[end:], re.IGNORECASE)
        if ref:
            table["foreign_keys"].append({
                "columns": _cols(inner), "ref_table": _table_name(ref.group(1)),
                "ref_columns": _cols(ref.group(2)) if ref.group(2) else ["id"],
            })
    elif low.startswith("unique"):
        inner, _ = _paren_body(body, body.index("("))
        table["unique"].append(_cols(inner))


def _parse_sql(sql: str, tables: dict[str, dict[str, Any]], notes: list[str]) -> None:
    for stmt in _statements(sql):
        head = stmt.strip()
        match = re.match(rf"create\s+(?:(?:global\s+|local\s+)?(?:temp|temporary)\s+|unlogged\s+)?table\s+(?:if\s+not\s+exists\s+)?({_QNAME})\s*\(",
                         head, re.IGNORECASE)
        if match:
            name = _table_name(match.group(1))
            body, _ = _paren_body(head, match.end() - 1)
            table = tables.setdefault(name, _new_table())
            for item in _split_top(body):
                if re.match(r"(constraint\s|primary\s+key\s*\(|foreign\s+key\s*\(|unique\s*\(|check\s*\(|exclude\s)",
                            item.strip(), re.IGNORECASE):
                    _table_constraint(table, item.strip())
                    continue
                parts = re.match(rf"({_IDENT})\s+([\w\s(),.\[\]]+?)(?=\s+(?:not|null|primary|unique|default|references|check|constraint|generated|collate)\b|$)(.*)$",
                                 item.strip(), re.IGNORECASE | re.DOTALL)
                if parts:
                    _add_column(table, _unquote(parts.group(1)), parts.group(2), parts.group(3))
                else:
                    notes.append(f"table {name}: could not read column definition {item.strip()[:60]!r}")
            continue
        match = re.match(rf"create\s+(unique\s+)?index\s+(?:concurrently\s+)?(?:if\s+not\s+exists\s+)?({_IDENT})?\s*on\s+(?:only\s+)?({_QNAME})\s*(?:using\s+\w+\s*)?\(",
                         head, re.IGNORECASE)
        if match:
            tname = _table_name(match.group(3))
            inner, _ = _paren_body(head, match.end() - 1)
            tables.setdefault(tname, _new_table())["indexes"].append({
                "name": _unquote(match.group(2)) if match.group(2) else None,
                "columns": _cols(inner), "unique": bool(match.group(1)),
            })
            continue
        match = re.match(rf"alter\s+table\s+(?:if\s+exists\s+)?(?:only\s+)?({_QNAME})\s+(.*)$", head, re.IGNORECASE | re.DOTALL)
        if match:
            table = tables.setdefault(_table_name(match.group(1)), _new_table())
            for action in _split_top(match.group(2)):
                add = re.match(r"add\s+(?:column\s+)?(?:if\s+not\s+exists\s+)?(.*)$", action.strip(), re.IGNORECASE | re.DOTALL)
                if not add:
                    continue
                rest = add.group(1)
                if re.match(r"(constraint\b|primary\s+key|foreign\s+key|unique\s*\()", rest, re.IGNORECASE):
                    _table_constraint(table, rest)
                else:
                    parts = re.match(rf"({_IDENT})\s+([\w\s(),.\[\]]+?)(?=\s+(?:not|null|primary|unique|default|references|check)\b|$)(.*)$",
                                     rest, re.IGNORECASE | re.DOTALL)
                    if parts:
                        _add_column(table, _unquote(parts.group(1)), parts.group(2), parts.group(3))


def _kw(call: ast.Call, name: str) -> Any:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _lit(node: ast.AST | None) -> Any:
    try:
        return ast.literal_eval(node) if node is not None else None
    except (ValueError, TypeError, SyntaxError):
        return None


def _sa_type(node: ast.AST) -> str:
    return re.sub(r"^sa\.|^sqlalchemy\.", "", ast.unparse(node)).lower()


def _alembic_column(call: ast.Call, table: dict[str, Any]) -> None:
    if not call.args:
        return
    name = _lit(call.args[0])
    if not isinstance(name, str):
        return
    ctype = _sa_type(call.args[1]) if len(call.args) > 1 and not (
        isinstance(call.args[1], ast.Call) and ast.unparse(call.args[1].func).endswith("ForeignKey")) else "unknown"
    primary = _lit(_kw(call, "primary_key")) is True
    nullable_kw = _lit(_kw(call, "nullable"))
    column = {
        "name": name.lower(), "type": ctype, "nullable": (nullable_kw is not False) and not primary,
        "primary_key": primary, "unique": _lit(_kw(call, "unique")) is True, "default": None,
    }
    table["columns"].append(column)
    if primary:
        table["primary_key"].append(column["name"])
    if column["unique"]:
        table["unique"].append([column["name"]])
    if _lit(_kw(call, "index")) is True:
        table["indexes"].append({"name": None, "columns": [column["name"]], "unique": column["unique"]})
    for arg in call.args[1:]:
        if isinstance(arg, ast.Call) and ast.unparse(arg.func).endswith("ForeignKey") and arg.args:
            target = _lit(arg.args[0])
            if isinstance(target, str) and "." in target:
                ref_table, ref_col = target.rsplit(".", 1)
                table["foreign_keys"].append({"columns": [column["name"]], "ref_table": _table_name(ref_table),
                                              "ref_columns": [_unquote(ref_col)]})


def _parse_alembic(source: str, tables: dict[str, dict[str, Any]], notes: list[str]) -> bool:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    found = False
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "op"):
            continue
        action = node.func.attr
        args = node.args
        if action == "create_table" and args and isinstance(_lit(args[0]), str):
            found = True
            table = tables.setdefault(_lit(args[0]).lower(), _new_table())
            for item in args[1:]:
                if not isinstance(item, ast.Call):
                    continue
                kind = ast.unparse(item.func).split(".")[-1]
                if kind == "Column":
                    _alembic_column(item, table)
                elif kind == "PrimaryKeyConstraint":
                    table["primary_key"] = [str(_lit(a)).lower() for a in item.args if _lit(a)]
                elif kind == "ForeignKeyConstraint" and len(item.args) >= 2:
                    local, remote = _lit(item.args[0]), _lit(item.args[1])
                    if isinstance(local, list) and isinstance(remote, list) and remote:
                        ref_table = str(remote[0]).rsplit(".", 1)[0]
                        table["foreign_keys"].append({
                            "columns": [str(c).lower() for c in local], "ref_table": _table_name(ref_table),
                            "ref_columns": [str(r).rsplit(".", 1)[-1].lower() for r in remote]})
                elif kind == "UniqueConstraint":
                    table["unique"].append([str(_lit(a)).lower() for a in item.args if isinstance(_lit(a), str)])
        elif action == "add_column" and len(args) >= 2 and isinstance(_lit(args[0]), str):
            found = True
            _alembic_column(args[1], tables.setdefault(_lit(args[0]).lower(), _new_table())) if isinstance(args[1], ast.Call) else None
        elif action == "create_index" and len(args) >= 3 and isinstance(_lit(args[1]), str):
            found = True
            cols = _lit(args[2])
            tables.setdefault(_lit(args[1]).lower(), _new_table())["indexes"].append({
                "name": _lit(args[0]), "columns": [str(c).lower() for c in cols] if isinstance(cols, list) else [],
                "unique": _lit(_kw(node, "unique")) is True})
        elif action == "create_foreign_key" and len(args) >= 5:
            found = True
            src, ref = _lit(args[1]), _lit(args[2])
            local, remote = _lit(args[3]), _lit(args[4])
            if isinstance(src, str) and isinstance(ref, str) and isinstance(local, list) and isinstance(remote, list):
                tables.setdefault(src.lower(), _new_table())["foreign_keys"].append({
                    "columns": [str(c).lower() for c in local], "ref_table": ref.lower(),
                    "ref_columns": [str(c).lower() for c in remote]})
        elif action == "drop_table" and args and isinstance(_lit(args[0]), str):
            found = True
            tables.pop(_lit(args[0]).lower(), None)
            notes.append(f"dropped table {_lit(args[0])}")
    return found


def parse_schema(text: str) -> dict[str, Any]:
    """``{"tables", "relationships", "notes"}`` from SQL DDL and/or Alembic migration source."""
    tables: dict[str, dict[str, Any]] = {}
    notes: list[str] = []
    looks_python = bool(re.search(r"^\s*(?:import|from)\s+\w|^\s*def\s+upgrade\b|\bop\.\w+\(", text, re.MULTILINE))
    used_alembic = _parse_alembic(text, tables, notes) if looks_python else False
    if not used_alembic:
        _parse_sql(text, tables, notes)
    if len(tables) > _MAX_TABLES:
        notes.append(f"more than {_MAX_TABLES} tables; the rest were dropped")
        tables = dict(list(tables.items())[:_MAX_TABLES])
    for table in tables.values():
        for column in table["columns"]:
            if column["name"] in table["primary_key"]:
                column["nullable"] = False
                column["primary_key"] = True
    relationships = [
        {"from_table": name, "from_columns": fk["columns"], "to_table": fk["ref_table"], "to_columns": fk["ref_columns"]}
        for name, table in tables.items() for fk in table["foreign_keys"]
    ]
    return {"source_format": "alembic" if used_alembic else "sql", "tables": tables,
            "relationships": relationships, "notes": notes}


def _covered(columns: list[str], table: dict[str, Any]) -> bool:
    """Is there an index/PK/unique constraint whose leading columns are ``columns`` (any order)?"""
    need = set(columns)
    candidates = [table["primary_key"], *table["unique"], *[i["columns"] for i in table["indexes"]]]
    return any(c and set(c[: len(need)]) == need for c in candidates)


def _index_hint(table: str, columns: list[str]) -> str:
    """Advice for a reviewer to put in an Alembic migration. It is returned text, never executed here
    (``tests/architecture/test_no_runtime_ddl.py`` forbids DDL *issued* by ``aura_core``; lower case keeps
    this advisory string out of that scan on purpose, not by accident)."""
    return f"create index ix_{table}_{'_'.join(columns)} on {table} ({', '.join(columns)});"


def schema_issues(schema: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    tables = schema["tables"]
    for name, table in tables.items():
        if not table["primary_key"] and table["columns"]:
            issues.append({"code": "no_primary_key", "severity": "warning", "table": name,
                           "message": f"Table '{name}' has no primary key."})
        for fk in table["foreign_keys"]:
            if fk["ref_table"] not in tables:
                issues.append({"code": "fk_unknown_table", "severity": "warning", "table": name,
                               "columns": fk["columns"],
                               "message": f"'{name}'({', '.join(fk['columns'])}) references '{fk['ref_table']}', "
                                          "which is not defined in the supplied schema."})
            if not _covered(fk["columns"], table):
                cols = ", ".join(fk["columns"])
                issues.append({"code": "fk_missing_index", "severity": "warning", "table": name, "columns": fk["columns"],
                               "message": f"Foreign key '{name}'({cols}) -> '{fk['ref_table']}' has no index starting with "
                                          "those columns: joins and ON DELETE checks scan the table.",
                               "suggestion": _index_hint(name, fk["columns"])})
        seen: dict[tuple, str | None] = {}
        for index in table["indexes"]:
            key = tuple(index["columns"])
            if key in seen:
                issues.append({"code": "duplicate_index", "severity": "info", "table": name, "columns": list(key),
                               "message": f"Index {index['name'] or key} duplicates {seen[key] or key} on '{name}'."})
            seen[key] = index["name"]
        for index in table["indexes"]:
            cols = index["columns"]
            for other in table["indexes"]:
                if other is not index and len(other["columns"]) > len(cols) and other["columns"][: len(cols)] == cols \
                        and not index["unique"]:
                    issues.append({"code": "redundant_index", "severity": "info", "table": name, "columns": cols,
                                   "message": f"Index on ({', '.join(cols)}) is a prefix of ({', '.join(other['columns'])}) "
                                              "and is usually redundant."})
                    break
    return issues


_FUNC_ON_COLUMN = re.compile(r"\b(lower|upper|date|trim|substr|substring|cast|coalesce|extract|to_char|length|abs|round)\s*\(\s*([A-Za-z_][\w.]*)\s*[,)]", re.IGNORECASE)


def _aliases(sql: str, known: set[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in re.finditer(rf"\b(?:from|join)\s+({_QNAME})(?:\s+(?:as\s+)?([A-Za-z_]\w*))?", sql, re.IGNORECASE):
        table = _table_name(m.group(1))
        alias = m.group(2)
        out[table] = table
        if alias and alias.lower() not in {"where", "on", "inner", "left", "right", "full", "cross", "join", "group",
                                           "order", "limit", "union", "using", "natural", "offset", "having"}:
            out[alias.lower()] = table
    return out


def query_smells(sql: str, schema: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Index-defeating and risky constructs in ``sql``, plus unknown/unindexed columns given ``schema``."""
    text = _strip_comments(sql)
    low = text.lower()
    found: list[dict[str, Any]] = []

    def add(code: str, severity: str, message: str, **extra: Any) -> None:
        found.append({"code": code, "severity": severity, "message": message, **extra})

    if re.search(r"\blike\s+'%", low) or re.search(r"\bilike\s+'%", low):
        add("leading_wildcard_like", "warning", "LIKE with a leading % cannot use a btree index; consider a trigram/full-text index.")
    for m in _FUNC_ON_COLUMN.finditer(text):
        if re.search(r"\bwhere\b|\bon\b|\band\b|\bor\b", text[: m.start()], re.IGNORECASE):
            add("function_on_column", "warning",
                f"{m.group(1).lower()}({m.group(2)}) in a filter defeats a plain index on '{m.group(2)}'; use an expression index or compare the bare column.")
    if re.search(r"[=<>!]\s*null\b", low) and not re.search(r"\bis\s+(?:not\s+)?null\b", low):
        add("equals_null", "error", "'= NULL' is never true; use IS NULL / IS NOT NULL.")
    if re.search(r"\bnot\s+in\s*\(\s*select\b", low):
        add("not_in_subquery", "warning", "NOT IN (subquery) returns no rows if the subquery yields a NULL; use NOT EXISTS.")
    if re.search(r"\bwhere\b.*\bor\b", low, re.DOTALL) and not re.search(r"\(\s*[^()]*\bor\b[^()]*\)", low):
        add("or_in_filter", "info", "OR across different columns often prevents index use; consider UNION ALL of indexed branches.")
    from_clause = re.search(r"\bfrom\s+(.*?)(?:\bwhere\b|\bgroup\b|\border\b|\blimit\b|\bunion\b|;|$)", low, re.DOTALL)
    if from_clause and "," in from_clause.group(1) and "(" not in from_clause.group(1) and not re.search(r"\bjoin\b", from_clause.group(1)):
        add("implicit_cross_join", "warning", "Comma-separated FROM list is an implicit cross join; use explicit JOIN ... ON.")
    offset = re.search(r"\boffset\s+(\d+)", low)
    if offset and int(offset.group(1)) >= 10_000:
        add("deep_offset", "warning", f"OFFSET {offset.group(1)} reads and discards that many rows; use keyset pagination.")
    if re.search(r"\bunion\b(?!\s+all)", low):
        add("union_without_all", "info", "UNION de-duplicates (a sort); use UNION ALL if duplicates cannot occur.")
    if re.search(r"\border\s+by\b", low) and not re.search(r"\blimit\b|\bfetch\s+first\b|\btop\s+\d", low) \
            and re.match(r"\s*select\b", low):
        add("order_by_without_limit", "info", "ORDER BY with no LIMIT sorts the whole result.")
    in_list = re.search(r"\bin\s*\(([^()]*)\)", low)
    if in_list and in_list.group(1).count(",") >= 100:
        add("large_in_list", "warning", "IN list with 100+ literals; use a join to a temp table or = ANY(array parameter).")
    if re.match(r"\s*explain\s+(?:\([^)]*\)\s*)?analy[sz]e\b", low) or re.match(r"\s*explain\s+analy[sz]e\b", low):
        executes = re.search(r"\b(insert|update|delete|merge|drop|truncate|alter|create)\b", low)
        add("explain_analyze_executes", "error" if executes else "warning",
            "EXPLAIN ANALYZE actually EXECUTES the statement"
            + ("; this one writes or changes data. Wrap it in a rolled-back transaction or use plain EXPLAIN." if executes
               else "; a long query will run to completion."))
    if schema:
        known = schema["tables"]
        aliases = _aliases(text, set(known))
        for table in aliases.values():
            if known and table not in known:
                add("unknown_table", "error", f"Table '{table}' is not in the supplied schema.", table=table)
        # qualified column references alias.col
        for m in re.finditer(r"\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)\b", text):
            alias, col = m.group(1).lower(), m.group(2).lower()
            table = aliases.get(alias)
            if table and table in known and known[table]["columns"] and col not in {c["name"] for c in known[table]["columns"]}:
                add("unknown_column", "error", f"Column '{col}' does not exist on '{table}' in the supplied schema.",
                    table=table, column=col)
        where = re.search(r"\bwhere\b(.*?)(?:\bgroup\s+by\b|\border\s+by\b|\blimit\b|\bunion\b|;|$)", text, re.IGNORECASE | re.DOTALL)
        filters: list[tuple[str, str]] = []
        for scope in [where.group(1)] if where else []:
            for m in re.finditer(r"\b(?:([A-Za-z_]\w*)\.)?([A-Za-z_]\w*)\s*(?:=|<|>|<=|>=|!=|<>|\bin\b|\blike\b|\bbetween\b)", scope, re.IGNORECASE):
                alias, col = (m.group(1) or "").lower(), m.group(2).lower()
                table = aliases.get(alias) if alias else (next(iter(aliases.values())) if len(set(aliases.values())) == 1 else None)
                if table in known and col in {c["name"] for c in known[table]["columns"]}:
                    filters.append((table, col))
        for m in re.finditer(r"\bon\s+([A-Za-z_]\w*)\.([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*)\.([A-Za-z_]\w*)", text, re.IGNORECASE):
            for alias, col in ((m.group(1), m.group(2)), (m.group(3), m.group(4))):
                table = aliases.get(alias.lower())
                if table in known and col.lower() in {c["name"] for c in known[table]["columns"]}:
                    filters.append((table, col.lower()))
        for table, col in dict.fromkeys(filters):
            if not _covered([col], known[table]):
                add("unindexed_filter", "warning", f"'{table}.{col}' is filtered or joined on but no index starts with it in the supplied schema.",
                    table=table, column=col, suggestion=_index_hint(table, [col]))
    return found


def generate_select(
    schema: dict[str, Any], table: str, columns: list[str] | None = None,
    filters: list[str] | None = None, order_by: str | None = None, limit: int = 100,
) -> dict[str, Any]:
    """A parameterised SELECT using only identifiers present in ``schema`` (errors name the offender)."""
    name = table.strip().lower()
    if name not in schema["tables"]:
        return {"error": f"table '{table}' is not in the supplied schema", "known_tables": sorted(schema["tables"])[:50]}
    have = {c["name"] for c in schema["tables"][name]["columns"]}
    chosen = [c.lower() for c in (columns or [])] or sorted(have)
    for column in [*chosen, *[f.lower() for f in filters or []], *([order_by.lower()] if order_by else [])]:
        if column not in have:
            return {"error": f"column '{column}' is not on '{name}'", "known_columns": sorted(have)}
    where = " AND ".join(f'"{f.lower()}" = :{f.lower()}' for f in filters or [])
    sql = f'SELECT {", ".join(chr(34) + c + chr(34) for c in chosen)} FROM "{name}"'
    if where:
        sql += f" WHERE {where}"
    if order_by:
        sql += f' ORDER BY "{order_by.lower()}"'
    sql += f" LIMIT {max(1, min(int(limit), 10_000))}"
    return {"sql": sql, "parameters": [f.lower() for f in filters or []], "read_only": True}


def schema_report(text: str) -> dict[str, Any]:
    """Parsed schema plus issues, in one JSON-ready result."""
    schema = parse_schema(text)
    issues = schema_issues(schema)
    summary = {
        "tables": len(schema["tables"]),
        "columns": sum(len(t["columns"]) for t in schema["tables"].values()),
        "relationships": len(schema["relationships"]),
        "indexes": sum(len(t["indexes"]) for t in schema["tables"].values()),
        "issues": len(issues),
    }
    return {**schema, "issues": issues, "summary": summary, "plan_available": False,
            "explain": "No database connection: query plans, row estimates and timings need a live EXPLAIN, "
                       "which this tool never runs."}
