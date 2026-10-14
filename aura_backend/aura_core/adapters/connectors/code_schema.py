"""A relational schema read from source code, in the PostgreSQL connector's snapshot shape (AP10).

The Git connector (``git.py``) finds schema definitions in a repository -- SQL DDL and migrations
here, ORM models and Alembic migrations in ``code_python.py`` -- and accumulates them in one
:class:`SchemaModel`. :meth:`SchemaModel.snapshot` then returns exactly the dictionary
``postgres_catalog.artifacts_from_catalog`` maps for a live database, so code-derived tables reach
the normaliser through the same path as catalogued ones.

Fidelity is deliberately PostgreSQL's own: unquoted identifiers fold to lower case, ``serial`` is
an integer with a default, a ``PRIMARY KEY`` or identity column is ``NOT NULL``, data types print
the way ``format_type`` prints them (``numeric(10,2)``, ``character varying(255)``, an enum outside
``public`` schema-qualified), and unnamed constraints get the names PostgreSQL would give them
(``employees_pkey``, ``employees_email_key``, ``employees_department_id_fkey``,
``employees_status_check``). That is what lets the pilot goldens compare a repository's
``schema.sql`` with the catalog recorded from the same DDL applied to a real server.

Nothing here executes anything. Text is tokenised -- strings, quoted identifiers, dollar-quoted
bodies and comments are recognised so a ``;`` inside one never splits a statement -- and only the
statements that declare schema are interpreted. Everything else (``INSERT``, ``DO`` blocks,
indexes, functions) is skipped. A statement this reader cannot follow is an issue, never a guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

__all__ = ["SchemaModel", "read_sql", "DEFAULT_SCHEMA", "pg_type"]

DEFAULT_SCHEMA = "public"
_MAX_NAME = 63
_ISSUE_LOCATOR = 512

# ------------------------------------------------------------------------------- types

#: Spelling -> the canonical name ``format_type`` prints.
_TYPE_ALIASES = {
    "int": "integer", "int4": "integer", "integer": "integer", "serial": "integer", "serial4": "integer",
    "int8": "bigint", "bigint": "bigint", "bigserial": "bigint", "serial8": "bigint",
    "int2": "smallint", "smallint": "smallint", "smallserial": "smallint", "serial2": "smallint",
    "float4": "real", "real": "real", "float8": "double precision", "double precision": "double precision",
    "float": "double precision", "bool": "boolean", "boolean": "boolean", "text": "text",
    "varchar": "character varying", "character varying": "character varying", "char": "character",
    "character": "character", "bpchar": "character", "numeric": "numeric", "decimal": "numeric",
    "date": "date", "timestamptz": "timestamp with time zone", "timestamp with time zone": "timestamp with time zone",
    "timestamp": "timestamp without time zone", "timestamp without time zone": "timestamp without time zone",
    "time": "time without time zone", "time without time zone": "time without time zone",
    "timetz": "time with time zone", "time with time zone": "time with time zone",
    "uuid": "uuid", "json": "json", "jsonb": "jsonb", "bytea": "bytea", "interval": "interval",
    "inet": "inet", "cidr": "cidr", "citext": "citext", "money": "money", "xml": "xml",
}
#: Canonical name -> the catalog's ``udt_name``.
_UDT = {
    "integer": "int4", "bigint": "int8", "smallint": "int2", "real": "float4", "double precision": "float8",
    "boolean": "bool", "character varying": "varchar", "character": "bpchar",
    "timestamp with time zone": "timestamptz", "timestamp without time zone": "timestamp",
    "time without time zone": "time", "time with time zone": "timetz",
}
_SERIALS = frozenset({"serial", "serial4", "bigserial", "serial8", "smallserial", "serial2"})
_MODIFIABLE = frozenset({"numeric", "character varying", "character"})


@dataclass(frozen=True, slots=True)
class PgType:
    data_type: str
    type_name: str
    type_schema: str
    is_enum: bool = False
    serial: bool = False


def pg_type(spelling: str, modifiers: Sequence[str] = (), *, array: bool = False) -> PgType:
    """A built-in type spelling as the catalog reports it; unknown names pass through lower-cased."""
    base = " ".join(spelling.lower().split())
    canonical = _TYPE_ALIASES.get(base, base)
    data_type = canonical
    if modifiers and canonical in _MODIFIABLE:
        data_type = f"{canonical}({','.join(m.strip() for m in modifiers)})"
    udt = _UDT.get(canonical, canonical)
    if array:
        data_type, udt = f"{data_type}[]", f"_{udt}"
    return PgType(data_type, udt, "pg_catalog", serial=base in _SERIALS)


def _q(name: str) -> str:
    return name if re.fullmatch(r"[a-z_][a-z0-9_$]*", name) else '"' + name.replace('"', '""') + '"'


# ------------------------------------------------------------------------------- model


@dataclass
class _Column:
    name: str
    type: PgType
    not_null: bool = False
    has_default: bool = False
    comment: str | None = None


@dataclass
class _Relation:
    schema: str
    name: str
    kind: str  # r | v | m
    origin: str
    path: str
    columns: list[_Column] = field(default_factory=list)
    comment: str | None = None

    def column(self, name: str) -> _Column | None:
        return next((c for c in self.columns if c.name == name), None)


@dataclass
class _Constraint:
    schema: str
    table: str
    name: str
    type: str  # p u f c
    columns: tuple[str, ...]
    ref_schema: str | None = None
    ref_table: str | None = None
    ref_columns: tuple[str, ...] = ()
    definition: str | None = None


class SchemaModel:
    """Relations, constraints, enums and grants declared across a repository, in declaration order.

    Later declarations amend earlier ones the way the database would (``ALTER TABLE ... ADD
    COLUMN``, ``DROP TABLE``), and a relation declared twice -- the same table in a migration and
    an ORM model -- keeps its first declaration; :attr:`declared_again` counts the rest.
    """

    def __init__(self) -> None:
        self.relations: dict[tuple[str, str], _Relation] = {}
        self.constraints: list[_Constraint] = []
        self.enums: dict[tuple[str, str], list[str]] = {}
        self.grants: set[tuple[str, str, str, str]] = set()  # grantee, schema, table, privilege
        self.issues: list[dict[str, str]] = []
        self.declared_again = 0

    # -- building ---------------------------------------------------------------------------

    def issue(self, code: str, locator: str) -> None:
        self.issues.append({"code": code, "locator": locator[:_ISSUE_LOCATOR]})

    def has(self, schema: str, name: str) -> bool:
        return (schema, name) in self.relations

    def add_relation(self, schema: str, name: str, kind: str, *, origin: str, path: str) -> _Relation | None:
        if not 0 < len(name) <= _MAX_NAME or not 0 < len(schema) <= _MAX_NAME:
            self.issue("name_skipped", f"file:{path}")
            return None
        if (schema, name) in self.relations:
            self.declared_again += 1
            return None
        relation = _Relation(schema, name, kind, origin, path)
        self.relations[(schema, name)] = relation
        return relation

    def drop_relation(self, schema: str, name: str) -> None:
        self.relations.pop((schema, name), None)
        self.constraints = [c for c in self.constraints
                            if (c.schema, c.table) != (schema, name) and (c.ref_schema, c.ref_table) != (schema, name)]
        self.grants = {g for g in self.grants if (g[1], g[2]) != (schema, name)}

    def add_constraint(self, constraint: _Constraint) -> None:
        taken = {c.name for c in self.constraints if c.schema == constraint.schema}
        name, n = constraint.name[:_MAX_NAME], 1
        while name in taken:
            name = f"{constraint.name[:_MAX_NAME - len(str(n))]}{n}"
            n += 1
        constraint.name = name
        self.constraints.append(constraint)

    def enum_type(self, schema: str | None, name: str) -> PgType | None:
        """A declared enum, printed the way ``format_type`` prints it: bare in ``public``, else
        schema-qualified. An unqualified name outside ``public`` resolves only when unambiguous."""
        key = (schema or DEFAULT_SCHEMA, name)
        if key not in self.enums and schema is None:
            matches = [k for k in self.enums if k[1] == name]
            key = matches[0] if len(matches) == 1 else key
        if key not in self.enums:
            return None
        enum_schema, enum_name = key
        shown = _q(enum_name) if enum_schema == DEFAULT_SCHEMA else f"{_q(enum_schema)}.{_q(enum_name)}"
        return PgType(shown, enum_name, enum_schema, is_enum=True)

    def origin_of(self, schema: str, name: str) -> tuple[str, str] | None:
        relation = self.relations.get((schema, name))
        return (relation.origin, relation.path) if relation else None

    # -- output -----------------------------------------------------------------------------

    def snapshot(self, *, extra_issues: Iterable[dict[str, str]] = ()) -> dict[str, Any]:
        """The PostgreSQL connector's snapshot shape. No profiles: code carries no rows."""
        relations = sorted(self.relations.values(), key=lambda r: (r.schema, r.name))
        known = {(r.schema, r.name) for r in relations}
        constraints = [c for c in self.constraints if (c.schema, c.table) in known]
        return {
            "relations": [{"schema": r.schema, "name": r.name, "kind": r.kind, "comment": r.comment,
                           "readable": False} for r in relations],
            "columns": [{"schema": r.schema, "table": r.name, "name": c.name, "position": i + 1,
                         "data_type": c.type.data_type, "not_null": c.not_null, "comment": c.comment,
                         "is_enum": c.type.is_enum, "type_schema": c.type.type_schema,
                         "type_name": c.type.type_name, "has_default": c.has_default}
                        for r in relations for i, c in enumerate(r.columns)],
            "constraints": [{"schema": c.schema, "table": c.table, "name": c.name, "type": c.type,
                             "columns": list(c.columns), "ref_schema": c.ref_schema, "ref_table": c.ref_table,
                             "ref_columns": list(c.ref_columns), "definition": c.definition, "comment": None}
                            for c in sorted(constraints, key=lambda c: (c.schema, c.table, c.name))],
            "enums": [{"schema": s, "name": n, "labels": list(labels)} for (s, n), labels in sorted(self.enums.items())],
            "grants": [{"grantee": g, "schema": s, "table": t, "privilege": p}
                       for g, s, t, p in sorted(self.grants) if (s, t) in known],
            "profiles": [],
            "issues": list(self.issues) + list(extra_issues),
            "truncated": False,
        }


# ------------------------------------------------------------------------------- tokens


@dataclass(frozen=True, slots=True)
class _Tok:
    kind: str  # ident | qident | str | num | op | dollar
    value: str

    def kw(self, *words: str) -> bool:
        return self.kind == "ident" and self.value.lower() in words

    @property
    def name(self) -> str:
        """An identifier as the database stores it: unquoted names fold to lower case."""
        return self.value if self.kind == "qident" else self.value.lower()

    def render(self) -> str:
        if self.kind == "str":
            return "'" + self.value.replace("'", "''") + "'"
        if self.kind == "qident":
            return '"' + self.value.replace('"', '""') + '"'
        if self.kind == "dollar":
            return "$$" + self.value + "$$"
        return self.value


_IDENT = re.compile(r"[A-Za-z_\u0080-￿][A-Za-z0-9_$\u0080-￿]*")
_NUMBER = re.compile(r"[0-9]+(\.[0-9]+)?([eE][-+]?[0-9]+)?")
_DOLLAR = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$")
_OPS = ("::", ">=", "<=", "<>", "!=", "||", "->>", "->")


def _tokens(sql: str) -> list[_Tok]:
    out: list[_Tok] = []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        if c.isspace():
            i += 1
        elif sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j < 0 else j + 1
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif c in ("'", '"'):
            j, buf = i + 1, []
            while j < n:
                if sql[j] == c:
                    if j + 1 < n and sql[j + 1] == c:
                        buf.append(c)
                        j += 2
                        continue
                    break
                buf.append(sql[j])
                j += 1
            out.append(_Tok("str" if c == "'" else "qident", "".join(buf)))
            i = j + 1
        elif c == "$" and (m := _DOLLAR.match(sql, i)):
            tag = m.group(0)
            j = sql.find(tag, m.end())
            end = n if j < 0 else j
            out.append(_Tok("dollar", sql[m.end():end]))
            i = n if j < 0 else j + len(tag)
        elif m := _IDENT.match(sql, i):
            out.append(_Tok("ident", m.group(0)))
            i = m.end()
        elif m := _NUMBER.match(sql, i):
            out.append(_Tok("num", m.group(0)))
            i = m.end()
        else:
            op = next((o for o in _OPS if sql.startswith(o, i)), c)
            out.append(_Tok("op", op))
            i += len(op)
    return out


def _statements(tokens: list[_Tok]) -> list[list[_Tok]]:
    statements, current = [], []
    for tok in tokens:
        if tok.kind == "op" and tok.value == ";":
            if current:
                statements.append(current)
            current = []
        else:
            current.append(tok)
    if current:
        statements.append(current)
    return statements


class _Cursor:
    def __init__(self, tokens: Sequence[_Tok]) -> None:
        self.t = list(tokens)
        self.i = 0

    def peek(self, k: int = 0) -> _Tok | None:
        j = self.i + k
        return self.t[j] if j < len(self.t) else None

    def done(self) -> bool:
        return self.i >= len(self.t)

    def take(self) -> _Tok | None:
        tok = self.peek()
        self.i += 1
        return tok

    def kw(self, *words: str) -> bool:
        tok = self.peek()
        return tok is not None and tok.kw(*words)

    def accept(self, *words: str) -> bool:
        """Consume the keyword sequence ``words`` if it is next; all or nothing."""
        for k, word in enumerate(words):
            tok = self.peek(k)
            if tok is None or not tok.kw(word):
                return False
        self.i += len(words)
        return True

    def op(self, value: str) -> bool:
        tok = self.peek()
        return tok is not None and tok.kind == "op" and tok.value == value

    def accept_op(self, value: str) -> bool:
        if self.op(value):
            self.i += 1
            return True
        return False

    def name(self) -> tuple[str | None, str] | None:
        """``[schema.]name``; ``None`` when the next token is not an identifier."""
        tok = self.peek()
        if tok is None or tok.kind not in ("ident", "qident"):
            return None
        self.i += 1
        first = tok.name
        if self.op(".") and (nxt := self.peek(1)) is not None and nxt.kind in ("ident", "qident"):
            self.i += 2
            return first, nxt.name
        return None, first

    def group(self) -> list[_Tok] | None:
        """The tokens inside the parenthesised group that starts here, consuming it."""
        if not self.op("("):
            return None
        depth, start = 0, self.i
        while self.i < len(self.t):
            tok = self.t[self.i]
            if tok.kind == "op" and tok.value == "(":
                depth += 1
            elif tok.kind == "op" and tok.value == ")":
                depth -= 1
                if depth == 0:
                    self.i += 1
                    return self.t[start + 1:self.i - 1]
            self.i += 1
        return None


def _split(tokens: Sequence[_Tok]) -> list[list[_Tok]]:
    """Split on top-level commas."""
    parts, current, depth = [], [], 0
    for tok in tokens:
        if tok.kind == "op" and tok.value in "([":
            depth += 1
        elif tok.kind == "op" and tok.value in ")]":
            depth -= 1
        if depth == 0 and tok.kind == "op" and tok.value == ",":
            parts.append(current)
            current = []
        else:
            current.append(tok)
    if current:
        parts.append(current)
    return parts


def _render(tokens: Sequence[_Tok]) -> str:
    text = " ".join(t.render() for t in tokens)
    return re.sub(r"\( ", "(", re.sub(r" \)", ")", re.sub(r" ,", ",", text)))


def _names(tokens: Sequence[_Tok] | None) -> tuple[str, ...]:
    return tuple(part[0].name for part in _split(tokens or ()) if part and part[0].kind in ("ident", "qident"))


# ------------------------------------------------------------------------------- statements

_COLUMN_STOP = ("constraint", "not", "null", "primary", "unique", "references", "check", "default",
                "generated", "collate")
_TABLE_CONSTRAINT = ("constraint", "primary", "unique", "foreign", "check", "exclude", "like")
_PRIVILEGES = ("select", "insert", "update", "delete", "truncate", "references", "trigger")


class _SqlReader:
    def __init__(self, model: SchemaModel, path: str, origin: str) -> None:
        self.model = model
        self.path = path
        self.origin = origin
        self.default_schema = DEFAULT_SCHEMA

    def schema_of(self, schema: str | None) -> str:
        return schema or self.default_schema

    def run(self, sql: str) -> None:
        for statement in _statements(_tokens(sql)):
            cur = _Cursor(statement)
            try:
                self.statement(cur)
            except (IndexError, AttributeError, TypeError, ValueError):
                self.model.issue("statement_unparsed", f"file:{self.path}")

    def statement(self, cur: _Cursor) -> None:
        if cur.accept("create"):
            cur.accept("or", "replace")
            for word in ("global", "local", "temp", "temporary", "unlogged"):
                cur.accept(word)
            if cur.accept("table"):
                self.create_table(cur)
            elif cur.accept("type"):
                self.create_type(cur)
            elif cur.accept("materialized", "view"):
                self.create_view(cur, "m")
            elif cur.accept("view") or cur.accept("recursive", "view"):
                self.create_view(cur, "v")
        elif cur.accept("alter", "table"):
            self.alter_table(cur)
        elif cur.accept("alter", "type"):
            self.alter_type(cur)
        elif cur.accept("drop", "table") or cur.accept("drop", "view") or cur.accept("drop", "materialized", "view"):
            cur.accept("if", "exists")
            for part in _split(cur.t[cur.i:]):
                sub = _Cursor(part)
                if (found := sub.name()) is not None:
                    self.model.drop_relation(self.schema_of(found[0]), found[1])
        elif cur.accept("drop", "type"):
            cur.accept("if", "exists")
            for part in _split(cur.t[cur.i:]):
                sub = _Cursor(part)
                if (found := sub.name()) is not None:
                    self.model.enums.pop((self.schema_of(found[0]), found[1]), None)
        elif cur.accept("comment", "on"):
            self.comment(cur)
        elif cur.accept("grant"):
            self.grant(cur)
        elif cur.accept("set", "search_path") or cur.accept("set", "schema"):
            cur.accept("to") or cur.accept_op("=")
            tok = cur.peek()
            if tok is not None and tok.kind in ("ident", "qident", "str"):
                self.default_schema = tok.value if tok.kind != "ident" else tok.value.lower()

    # -- types --------------------------------------------------------------------------------

    def create_type(self, cur: _Cursor) -> None:
        found = cur.name()
        if found is None or not cur.accept("as", "enum"):
            return
        labels = [t.value for t in (cur.group() or ()) if t.kind == "str"]
        self.model.enums[(self.schema_of(found[0]), found[1])] = labels

    def alter_type(self, cur: _Cursor) -> None:
        found = cur.name()
        if found is None or not cur.accept("add", "value"):
            return
        cur.accept("if", "not", "exists")
        tok = cur.take()
        labels = self.model.enums.get((self.schema_of(found[0]), found[1]))
        if labels is None or tok is None or tok.kind != "str" or tok.value in labels:
            return
        position = len(labels)
        if cur.kw("before", "after"):
            where = cur.take().value.lower()
            anchor = cur.take()
            if anchor is not None and anchor.value in labels:
                position = labels.index(anchor.value) + (1 if where == "after" else 0)
        labels.insert(position, tok.value)

    def column_type(self, tokens: Sequence[_Tok]) -> PgType:
        cur = _Cursor(tokens)
        if (found := cur.name()) is None:
            return pg_type("unknown")
        schema, first = found
        words = [first]
        if schema is None:
            while (tok := cur.peek()) is not None and tok.kind == "ident" and tok.value.lower() in (
                    "precision", "varying", "with", "without", "time", "zone"):
                words.append(cur.take().value.lower())
        modifiers = [_render(part) for part in _split(cur.group() or ())]
        if cur.kw("with", "without"):  # timestamp(3) with time zone
            while (tok := cur.peek()) is not None and tok.kind == "ident":
                words.append(cur.take().value.lower())
        array = False
        while cur.op("["):
            array = True
            cur.take()
            while not cur.done() and not cur.op("]"):
                cur.take()
            cur.accept_op("]")
        spelling = " ".join(words)
        enum = self.model.enum_type(schema, first)
        if enum is not None:
            return enum if not array else PgType(enum.data_type + "[]", "_" + enum.type_name, enum.type_schema, True)
        if schema is not None and schema != "pg_catalog":
            return PgType(f"{_q(schema)}.{_q(first)}", first, schema)
        if spelling in ("timestamp", "time") and len(words) == 1 and modifiers:
            modifiers = []
        return pg_type(spelling, modifiers, array=array)

    # -- tables -------------------------------------------------------------------------------

    def create_table(self, cur: _Cursor) -> None:
        cur.accept("if", "not", "exists")
        found = cur.name()
        if found is None:
            return
        schema, name = self.schema_of(found[0]), found[1]
        body = cur.group()
        if body is None or cur.kw("partition") and cur.accept("partition", "of"):
            self.model.issue("table_form_unsupported", f"table:{_q(schema)}.{_q(name)}")
            return
        relation = self.model.add_relation(schema, name, "r", origin=self.origin, path=self.path)
        if relation is None:
            return
        for element in _split(body):
            if not element:
                continue
            if element[0].kw(*_TABLE_CONSTRAINT):
                self.table_constraint(relation, _Cursor(element))
            else:
                self.column(relation, _Cursor(element))

    def column(self, relation: _Relation, cur: _Cursor) -> None:
        tok = cur.take()
        if tok is None or tok.kind not in ("ident", "qident"):
            return
        start = cur.i
        depth = 0
        while not cur.done():
            t = cur.peek()
            if t.kind == "op" and t.value in "([":
                depth += 1
            elif t.kind == "op" and t.value in ")]":
                depth -= 1
            elif depth == 0 and t.kw(*_COLUMN_STOP):
                break
            cur.take()
        pgt = self.column_type(cur.t[start:cur.i])
        column = _Column(tok.name, pgt, not_null=pgt.serial, has_default=pgt.serial)
        if relation.column(column.name) is not None:
            return
        relation.columns.append(column)
        self.column_constraints(relation, column, cur)

    def column_constraints(self, relation: _Relation, column: _Column, cur: _Cursor) -> None:
        name: str | None = None
        table = relation.name
        while not cur.done():
            if cur.accept("constraint"):
                name = cur.take().name
                continue
            if cur.accept("not", "null"):
                column.not_null = True
            elif cur.accept("null"):
                column.not_null = False
            elif cur.accept("primary", "key"):
                column.not_null = True
                self.model.add_constraint(_Constraint(relation.schema, table, name or f"{table}_pkey", "p", (column.name,)))
            elif cur.accept("unique"):
                cur.accept("nulls", "not", "distinct") or cur.accept("nulls", "distinct")
                self.model.add_constraint(_Constraint(relation.schema, table, name or f"{table}_{column.name}_key", "u",
                                                      (column.name,)))
            elif cur.accept("references"):
                self.references(relation, (column.name,), cur, name)
            elif cur.accept("check"):
                expr = cur.group() or []
                self.model.add_constraint(_Constraint(relation.schema, table, name or f"{table}_{column.name}_check",
                                                      "c", (column.name,), definition=f"CHECK ({_render(expr)})"))
            elif cur.accept("default"):
                column.has_default = True
                self._skip_expression(cur)
            elif cur.accept("generated"):
                if cur.accept("always", "as", "identity") or cur.accept("by", "default", "as", "identity"):
                    column.not_null = True
                    cur.group()
                else:  # GENERATED ALWAYS AS (expr) STORED
                    cur.accept("always", "as")
                    cur.group()
                    cur.accept("stored")
            elif cur.accept("collate"):
                cur.name()
            else:
                cur.take()
                continue
            name = None

    def _skip_expression(self, cur: _Cursor) -> None:
        depth = 0
        while not cur.done():
            t = cur.peek()
            if t.kind == "op" and t.value == "(":
                depth += 1
            elif t.kind == "op" and t.value == ")":
                depth -= 1
            elif depth == 0 and t.kw(*_COLUMN_STOP) and not t.kw("null"):
                return
            cur.take()

    def references(self, relation: _Relation, columns: tuple[str, ...], cur: _Cursor, name: str | None) -> None:
        found = cur.name()
        if found is None:
            return
        ref_schema, ref_table = self.schema_of(found[0]), found[1]
        ref_columns = _names(cur.group())
        if not ref_columns:
            ref_columns = self.primary_key_of(ref_schema, ref_table)
        while True:  # the referential tail: never mistaken for a column's NULL / NOT NULL
            if cur.accept("on", "delete") or cur.accept("on", "update"):
                cur.accept("no", "action") or cur.accept("set", "null") or cur.accept("set", "default") \
                    or cur.accept("cascade") or cur.accept("restrict")
                cur.group()
            elif cur.accept("match"):
                cur.take()
            elif cur.accept("not", "deferrable") or cur.accept("deferrable"):
                pass
            elif cur.accept("initially"):
                cur.take()
            else:
                break
        table = relation.name
        self.model.add_constraint(_Constraint(
            relation.schema, table, name or f"{table}_{'_'.join(columns)}_fkey", "f", columns,
            ref_schema=ref_schema, ref_table=ref_table, ref_columns=ref_columns,
        ))

    def primary_key_of(self, schema: str, table: str) -> tuple[str, ...]:
        return next((c.columns for c in self.model.constraints
                     if (c.schema, c.table, c.type) == (schema, table, "p")), ())

    def table_constraint(self, relation: _Relation, cur: _Cursor) -> None:
        name = cur.take().name if cur.accept("constraint") else None
        table = relation.name
        if cur.accept("primary", "key"):
            columns = _names(cur.group())
            for column in columns:
                if (col := relation.column(column)) is not None:
                    col.not_null = True
            self.model.add_constraint(_Constraint(relation.schema, table, name or f"{table}_pkey", "p", columns))
        elif cur.accept("unique"):
            cur.accept("nulls", "not", "distinct") or cur.accept("nulls", "distinct")
            columns = _names(cur.group())
            self.model.add_constraint(_Constraint(relation.schema, table, name or f"{table}_{'_'.join(columns)}_key",
                                                  "u", columns))
        elif cur.accept("foreign", "key"):
            columns = _names(cur.group())
            if cur.accept("references"):
                self.references(relation, columns, cur, name)
        elif cur.accept("check"):
            expr = cur.group() or []
            referenced = sorted({t.name for t in expr if t.kind in ("ident", "qident") and relation.column(t.name)})
            self.model.add_constraint(_Constraint(relation.schema, table, name or f"{table}_check", "c",
                                                  tuple(referenced), definition=f"CHECK ({_render(expr)})"))

    def alter_table(self, cur: _Cursor) -> None:
        cur.accept("if", "exists")
        cur.accept("only")
        found = cur.name()
        if found is None:
            return
        schema, name = self.schema_of(found[0]), found[1]
        relation = self.model.relations.get((schema, name))
        if relation is None:
            self.model.issue("alter_unknown_table", f"table:{_q(schema)}.{_q(name)}")
            return
        if cur.accept("rename", "to"):
            new = cur.take().name
            if not self.model.has(schema, new):
                self.model.relations.pop((schema, name))
                relation.name = new
                self.model.relations[(schema, new)] = relation
                for c in self.model.constraints:
                    if (c.schema, c.table) == (schema, name):
                        c.table = new
                    if (c.ref_schema, c.ref_table) == (schema, name):
                        c.ref_table = new
            return
        for action in _split(cur.t[cur.i:]):
            self.alter_action(relation, _Cursor(action))

    def alter_action(self, relation: _Relation, cur: _Cursor) -> None:
        if cur.accept("add"):
            if cur.kw(*_TABLE_CONSTRAINT) and not cur.kw("like"):
                self.table_constraint(relation, cur)
            else:
                cur.accept("column")
                cur.accept("if", "not", "exists")
                self.column(relation, cur)
        elif cur.accept("drop", "constraint"):
            cur.accept("if", "exists")
            gone = cur.take().name
            self.model.constraints = [c for c in self.model.constraints
                                      if not (c.schema == relation.schema and c.table == relation.name and c.name == gone)]
        elif cur.accept("drop"):
            cur.accept("column")
            cur.accept("if", "exists")
            gone = cur.take().name
            relation.columns = [c for c in relation.columns if c.name != gone]
            self.model.constraints = [c for c in self.model.constraints
                                      if not ((c.schema, c.table) == (relation.schema, relation.name) and gone in c.columns)]
        elif cur.accept("rename"):
            if cur.accept("constraint"):
                old = cur.take().name
                cur.accept("to")
                new = cur.take().name
                for c in self.model.constraints:
                    if (c.schema, c.table, c.name) == (relation.schema, relation.name, old):
                        c.name = new
                return
            cur.accept("column")
            old = cur.take().name
            cur.accept("to")
            new = cur.take().name
            if (col := relation.column(old)) is not None and relation.column(new) is None:
                col.name = new
                for c in self.model.constraints:
                    if (c.schema, c.table) == (relation.schema, relation.name):
                        c.columns = tuple(new if x == old else x for x in c.columns)
        elif cur.accept("alter"):
            cur.accept("column")
            col = relation.column(cur.take().name)
            if col is None:
                return
            if cur.accept("set", "not", "null"):
                col.not_null = True
            elif cur.accept("drop", "not", "null"):
                col.not_null = False
            elif cur.accept("set", "default"):
                col.has_default = True
            elif cur.accept("drop", "default"):
                col.has_default = False
            elif cur.accept("set", "data", "type") or cur.accept("type"):
                start = cur.i
                while not cur.done() and not cur.kw("using", "collate"):
                    cur.take()
                col.type = self.column_type(cur.t[start:cur.i])

    # -- views --------------------------------------------------------------------------------

    def create_view(self, cur: _Cursor, kind: str) -> None:
        cur.accept("if", "not", "exists")
        found = cur.name()
        if found is None:
            return
        schema, name = self.schema_of(found[0]), found[1]
        declared = _names(cur.group()) if cur.op("(") else ()
        while not cur.done() and not cur.kw("as"):
            cur.take()
        if not cur.accept("as"):
            return
        relation = self.model.add_relation(schema, name, kind, origin=self.origin, path=self.path)
        if relation is None:
            return
        columns = self.view_columns(cur)
        if columns is None:
            self.model.issue("view_columns_unresolved", f"view:{_q(schema)}.{_q(name)}")
            return
        if declared and len(declared) == len(columns):
            columns = [_Column(n, c.type) for n, c in zip(declared, columns)]
        relation.columns = columns

    def view_columns(self, cur: _Cursor) -> list[_Column] | None:
        """``SELECT a, b AS c FROM t [alias]`` over one known relation; anything else: ``None``."""
        if not cur.accept("select"):
            return None
        cur.accept("distinct")
        start, depth = cur.i, 0
        while not cur.done():
            t = cur.peek()
            if t.kind == "op" and t.value == "(":
                depth += 1
            elif t.kind == "op" and t.value == ")":
                depth -= 1
            elif depth == 0 and t.kw("from"):
                break
            cur.take()
        items = _split(cur.t[start:cur.i])
        if not cur.accept("from") or (found := cur.name()) is None:
            return None
        base = self.model.relations.get((self.schema_of(found[0]), found[1]))
        if base is None:
            return None
        alias = None
        cur.accept("as")
        if (tok := cur.peek()) is not None and tok.kind in ("ident", "qident") and not tok.kw(
                "where", "group", "order", "limit", "join", "inner", "left", "right", "full", "cross", "union", "with"):
            alias = cur.take().name
        if not cur.done() and cur.kw("join", "inner", "left", "right", "full", "cross", "union"):
            return None
        columns: list[_Column] = []
        for item in items:
            if len(item) == 1 and item[0].kind == "op" and item[0].value == "*":
                columns.extend(_Column(c.name, c.type) for c in base.columns)
                continue
            source, rest = item, []
            if len(item) >= 3 and item[1].kind == "op" and item[1].value == "." and item[0].name in (base.name, alias):
                source = item[2:]
            if len(source) >= 3 and source[1].kw("as"):
                source, rest = source[:1], source[2:]
            elif len(source) == 2:
                source, rest = source[:1], source[1:]
            if len(source) != 1 or source[0].kind not in ("ident", "qident"):
                return None
            col = base.column(source[0].name)
            if col is None:
                return None
            out_name = rest[0].name if rest and rest[0].kind in ("ident", "qident") else col.name
            columns.append(_Column(out_name, col.type))
        return columns

    # -- comments and grants ------------------------------------------------------------------

    def comment(self, cur: _Cursor) -> None:
        target = "column" if cur.accept("column") else "table" if (
            cur.accept("table") or cur.accept("view") or cur.accept("materialized", "view")) else None
        if target is None:
            return
        parts: list[str] = []
        while (tok := cur.peek()) is not None and tok.kind in ("ident", "qident"):
            parts.append(cur.take().name)
            if not cur.accept_op("."):
                break
        if not cur.accept("is"):
            return
        tok = cur.take()
        text = tok.value if tok is not None and tok.kind in ("str", "dollar") else None
        if target == "table" and parts:
            schema, name = (parts[0], parts[1]) if len(parts) == 2 else (self.default_schema, parts[0])
            if (relation := self.model.relations.get((schema, name))) is not None:
                relation.comment = text
        elif target == "column" and len(parts) >= 2:
            schema, name, column = (parts[0], parts[1], parts[2]) if len(parts) == 3 else (self.default_schema, *parts)
            relation = self.model.relations.get((schema, name))
            if relation is not None and (col := relation.column(column)) is not None:
                col.comment = text

    def grant(self, cur: _Cursor) -> None:
        privileges: list[str] = []
        while not cur.done() and not cur.kw("on"):
            tok = cur.take()
            if tok.kw("all"):
                cur.accept("privileges")
                privileges.extend(_PRIVILEGES)
            elif tok.kw(*_PRIVILEGES):
                if cur.op("("):  # column privileges are not table privileges
                    cur.group()
                    continue
                privileges.append(tok.value.lower())
        if not cur.accept("on"):
            return
        targets: list[tuple[str, str]] = []
        if cur.accept("all", "tables", "in", "schema"):
            schemas: set[str] = set()
            while not cur.done() and not cur.kw("to"):
                tok = cur.take()
                if tok.kind in ("ident", "qident"):
                    schemas.add(tok.name)
            targets = [k for k in self.model.relations if k[0] in schemas]
        else:
            if cur.kw("schema", "sequence", "function", "database", "type", "domain", "all", "foreign", "language"):
                return
            cur.accept("table")
            start = cur.i
            while not cur.done() and not cur.kw("to"):
                cur.take()
            for part in _split(cur.t[start:cur.i]):
                sub = _Cursor(part)
                if (found := sub.name()) is not None:
                    targets.append((self.schema_of(found[0]), found[1]))
        if not cur.accept("to"):
            return
        roles = [part[0].name for part in _split(cur.t[cur.i:]) if part and part[0].kind in ("ident", "qident")
                 and not part[0].kw("public", "with")]
        for schema, table in targets:
            if not self.model.has(schema, table):
                continue
            for role in roles:
                for privilege in privileges:
                    self.model.grants.add((role, schema, table, privilege.upper()))


def read_sql(model: SchemaModel, sql: str, *, path: str, origin: str = "ddl") -> None:
    """Apply the schema statements in ``sql`` to ``model``, in order."""
    _SqlReader(model, path, origin).run(sql)
