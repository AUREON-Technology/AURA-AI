"""Schema and HTTP routes read from Python source with ``ast`` -- never imported, never run (AP10).

A repository's Python is attacker-controllable text (ADR-022), so it is parsed, not executed:
``ast.parse`` builds a syntax tree and nothing in this module evaluates any part of it. Only
literal values (strings, numbers, booleans, tuples and lists of them) are read; a table name or
column type computed at runtime is simply not seen, and the model records an issue rather than
guessing.

Three readers, each adding to the shared :class:`~.code_schema.SchemaModel`:

* **SQLAlchemy** declarative models -- ``__tablename__``, ``Column(...)`` and 2.0-style
  ``Mapped[...] = mapped_column(...)``, ``ForeignKey``, ``__table_args__`` (schema, unique and
  check constraints), ``Enum`` over literals or a local ``enum.Enum`` class;
* **Django** models -- ``models.Model`` subclasses, their fields, ``ForeignKey``/``OneToOneField``
  as ``<name>_id`` columns, ``choices`` as a closed value set, ``Meta.db_table``;
* **Alembic** migrations -- ``op.create_table``, ``op.add_column``, ``op.drop_table``,
  ``op.drop_column``, ``op.create_foreign_key`` and ``op.create_unique_constraint``, applied in
  ``down_revision`` order.

:func:`routes` reads FastAPI (``@app.get``, ``@router.post``, ``APIRouter(prefix=...)``) and Flask
(``@app.route(..., methods=[...])``, ``@bp.get``, ``Blueprint(url_prefix=...)``) handlers as
HTTP operations. A prefix added elsewhere (``include_router(prefix=...)``) is not followed.

Types map to what PostgreSQL would create (``String(80)`` -> ``character varying(80)``,
``DateTime(timezone=True)`` -> ``timestamp with time zone``); Django's ``DateTimeField`` is taken
as time-zone aware (``USE_TZ`` defaults on) and its implicit key as ``bigint`` (``BigAutoField``,
the default for new projects). Both are stated assumptions of a *candidate* schema -- the twin
holds code-derived elements below full confidence for exactly this reason.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from .code_schema import DEFAULT_SCHEMA, PgType, SchemaModel, _Column, _Constraint, pg_type

__all__ = ["read_python_models", "resolve_foreign_keys", "read_alembic", "routes", "Route", "alembic_order"]

# ------------------------------------------------------------------------------- literals


def _literal(node: ast.AST | None) -> Any:
    """A literal value, or ``None`` for anything that would need evaluating."""
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return None


def _call_name(node: ast.AST) -> str | None:
    """``Column`` for ``Column(...)``, ``sa.Column(...)`` and ``models.CharField(...)``."""
    func = node.func if isinstance(node, ast.Call) else node
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _kw(call: ast.Call, name: str) -> ast.AST | None:
    return next((k.value for k in call.keywords if k.arg == name), None)


def _kw_literal(call: ast.Call, name: str, default: Any = None) -> Any:
    node = _kw(call, name)
    value = _literal(node)
    return default if node is None or value is None and not isinstance(node, ast.Constant) else value


# ------------------------------------------------------------------------------- SQLAlchemy

_SA_TYPES = {
    "Integer": "integer", "INTEGER": "integer", "INT": "integer", "BigInteger": "bigint", "BIGINT": "bigint",
    "SmallInteger": "smallint", "SMALLINT": "smallint", "String": "character varying", "VARCHAR": "character varying",
    "Unicode": "character varying", "NVARCHAR": "character varying", "CHAR": "character", "Text": "text",
    "TEXT": "text", "UnicodeText": "text", "Numeric": "numeric", "NUMERIC": "numeric", "DECIMAL": "numeric",
    "Float": "double precision", "FLOAT": "double precision", "Double": "double precision", "REAL": "real",
    "Boolean": "boolean", "BOOLEAN": "boolean", "Date": "date", "DATE": "date", "DateTime": "timestamp",
    "DATETIME": "timestamp", "TIMESTAMP": "timestamp", "Time": "time", "TIME": "time", "Interval": "interval",
    "JSON": "json", "JSONB": "jsonb", "UUID": "uuid", "Uuid": "uuid", "LargeBinary": "bytea", "BYTEA": "bytea",
}
#: Python annotation -> the column type SQLAlchemy 2.0 maps it to by default.
_ANNOTATION_TYPES = {
    "int": "integer", "str": "character varying", "bool": "boolean", "float": "double precision",
    "Decimal": "numeric", "datetime": "timestamp", "date": "date", "time": "time", "UUID": "uuid",
    "bytes": "bytea", "dict": "json",
}


def _sa_type(node: ast.AST | None, enums: dict[str, list[str]]) -> tuple[PgType | None, list[str] | None]:
    """A column type node -> (type, enum labels when it is an ``Enum``)."""
    if node is None:
        return None, None
    name = _call_name(node) if isinstance(node, (ast.Call, ast.Name, ast.Attribute)) else None
    if name is None:
        return None, None
    call = node if isinstance(node, ast.Call) else None
    if name == "Enum":
        labels: list[str] = []
        for arg in (call.args if call else ()):
            value = _literal(arg)
            if isinstance(value, str):
                labels.append(value)
            elif isinstance(arg, (ast.Name, ast.Attribute)) and _call_name(arg) in enums:
                labels.extend(enums[_call_name(arg)])
        return None, labels or None
    if name == "ARRAY" and call and call.args:
        inner, _ = _sa_type(call.args[0], enums)
        return (pg_type(inner.data_type, array=True) if inner else None), None
    spelling = _SA_TYPES.get(name)
    if spelling is None:
        return None, None
    modifiers: list[str] = []
    if call is not None:
        if spelling == "timestamp" and _kw_literal(call, "timezone") is True:
            spelling = "timestamptz"
        if spelling == "time" and _kw_literal(call, "timezone") is True:
            spelling = "timetz"
        args = [a for a in (_literal(x) for x in call.args) if isinstance(a, int) and not isinstance(a, bool)]
        length = _kw_literal(call, "length")
        precision, scale = _kw_literal(call, "precision"), _kw_literal(call, "scale")
        if spelling in ("character varying", "character"):
            n = args[0] if args else length
            modifiers = [str(n)] if isinstance(n, int) else []
        elif spelling == "numeric":
            p = args[0] if args else precision
            s = args[1] if len(args) > 1 else scale
            modifiers = [str(p)] + ([str(s)] if isinstance(s, int) else ["0"]) if isinstance(p, int) else []
    return pg_type(spelling, modifiers), None


def _annotation_type(annotation: ast.AST | None) -> tuple[str | None, bool]:
    """``Mapped[Optional[int]]`` -> ("integer", nullable=True)."""
    if not isinstance(annotation, ast.Subscript) or _call_name(annotation.value) != "Mapped":
        return None, False
    inner = annotation.slice
    nullable = False
    if isinstance(inner, ast.Subscript) and _call_name(inner.value) == "Optional":
        inner, nullable = inner.slice, True
    if isinstance(inner, ast.BinOp) and isinstance(inner.op, ast.BitOr):
        parts = [inner.left, inner.right]
        if any(isinstance(p, ast.Constant) and p.value is None for p in parts):
            nullable = True
            inner = next(p for p in parts if not (isinstance(p, ast.Constant) and p.value is None))
    name = _call_name(inner) if isinstance(inner, (ast.Name, ast.Attribute)) else None
    return _ANNOTATION_TYPES.get(name or ""), nullable


def _enum_classes(tree: ast.Module) -> dict[str, list[str]]:
    """Local ``enum.Enum`` / ``models.TextChoices`` classes -> their string values."""
    out: dict[str, list[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        bases = {_call_name(b) for b in node.bases}
        if not bases & {"Enum", "StrEnum", "TextChoices", "IntegerChoices", "Choices"}:
            continue
        values = []
        for stmt in node.body:
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
                value = _literal(stmt.value)
                if isinstance(value, tuple) and value:
                    value = value[0]
                if isinstance(value, (str, int)) and not isinstance(value, bool):
                    values.append(str(value))
        if values:
            out[node.name] = values
    return out


def _fk_target(node: ast.AST, schema_default: str) -> tuple[str, str, str] | None:
    """``ForeignKey("schema.table.column")`` -> (schema, table, column)."""
    value = _literal(node.args[0]) if isinstance(node, ast.Call) and node.args else None
    if not isinstance(value, str):
        return None
    parts = value.split(".")
    if len(parts) == 2:
        return schema_default, parts[0], parts[1]
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    return None


def _check_definition(column: str, labels: Sequence[str]) -> str:
    quoted = ", ".join("'" + v.replace("'", "''") + "'" for v in labels)
    return f"CHECK ({column} IN ({quoted}))"


@dataclass
class _Pending:
    """A foreign key whose referenced table may be declared later in the repository."""

    schema: str
    table: str
    columns: tuple[str, ...]
    ref: tuple[str, str, str | None]  # schema, table, column (None: its primary key)
    name: str


def _table_args(node: ast.ClassDef) -> tuple[str | None, list[ast.Call]]:
    for stmt in node.body:
        if isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__table_args__" for t in stmt.targets):
            value = stmt.value
            items = value.elts if isinstance(value, (ast.Tuple, ast.List)) else [value]
            schema = None
            calls = []
            for item in items:
                if isinstance(item, ast.Dict):
                    literal = _literal(item)
                    if isinstance(literal, dict) and isinstance(literal.get("schema"), str):
                        schema = literal["schema"]
                elif isinstance(item, ast.Call):
                    calls.append(item)
            return schema, calls
    return None, []


def _class_attr(node: ast.ClassDef, name: str) -> Any:
    for stmt in node.body:
        if isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in stmt.targets):
            return _literal(stmt.value)
    return None


def _sqlalchemy_model(model: SchemaModel, node: ast.ClassDef, path: str, enums: dict[str, list[str]],
                      pending: list[_Pending]) -> None:
    table = _class_attr(node, "__tablename__")
    if not isinstance(table, str):
        return
    schema_arg, constraint_calls = _table_args(node)
    schema = schema_arg or DEFAULT_SCHEMA
    relation = model.add_relation(schema, table, "r", origin="orm", path=path)
    if relation is None:
        return
    primary: list[str] = []
    for stmt in node.body:
        target, value, annotation = None, None, None
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            target, value = stmt.targets[0].id, stmt.value
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            target, value, annotation = stmt.target.id, stmt.value, stmt.annotation
        if target is None or target.startswith("__"):
            continue
        call = value if isinstance(value, ast.Call) else None
        if value is None and _annotation_type(annotation)[0] is not None:
            # 2.0 style with no `mapped_column(...)`: the annotation alone declares the column.
            call = ast.Call(func=ast.Name(id="mapped_column", ctx=ast.Load()), args=[], keywords=[])
        kind = _call_name(call) if call else None
        if kind not in ("Column", "mapped_column"):
            continue
        args = list(call.args)
        column_name = target
        if args and isinstance(_literal(args[0]), str):
            column_name = _literal(args.pop(0))
        column_name = _kw_literal(call, "name", column_name) or column_name
        pgt, labels = None, None
        foreign = None
        for arg in args:
            if isinstance(arg, ast.Call) and _call_name(arg) == "ForeignKey":
                foreign = _fk_target(arg, schema)
            elif pgt is None and labels is None:
                pgt, labels = _sa_type(arg, enums)
        type_kw = _kw(call, "type_")
        if pgt is None and labels is None and type_kw is not None:
            pgt, labels = _sa_type(type_kw, enums)
        annotated, optional = _annotation_type(annotation)
        is_pk = _kw_literal(call, "primary_key") is True
        if pgt is None and labels is None and annotated:
            pgt = pg_type(annotated)
        if labels is not None:
            enum_name = _kw_literal(_sa_enum_call(args, type_kw), "name") if _sa_enum_call(args, type_kw) else None
            enum_name = enum_name if isinstance(enum_name, str) else f"{table}_{column_name}"
            model.enums.setdefault((schema, enum_name), list(labels))
            pgt = model.enum_type(schema, enum_name)
        if pgt is None:
            pgt = pg_type("integer") if foreign else pg_type("unknown")
            if not foreign:
                model.issue("column_type_unresolved", f"column:{schema}.{table}.{column_name}")
        nullable = _kw_literal(call, "nullable")
        not_null = is_pk or nullable is False or (nullable is None and annotation is not None and not optional
                                                    and kind == "mapped_column")
        column = _Column(column_name, pgt, not_null=bool(not_null),
                         has_default=_kw(call, "server_default") is not None)
        relation.columns.append(column)
        if is_pk:
            primary.append(column_name)
        if _kw_literal(call, "unique") is True:
            model.add_constraint(_Constraint(schema, table, f"{table}_{column_name}_key", "u", (column_name,)))
        if foreign:
            pending.append(_Pending(schema, table, (column_name,), foreign, f"{table}_{column_name}_fkey"))
    if primary:
        model.add_constraint(_Constraint(schema, table, f"{table}_pkey", "p", tuple(primary)))
    for call in constraint_calls:
        kind = _call_name(call)
        columns = tuple(v for v in (_literal(a) for a in call.args) if isinstance(v, str))
        name = _kw_literal(call, "name")
        if kind == "UniqueConstraint" and columns:
            model.add_constraint(_Constraint(schema, table, name or f"{table}_{'_'.join(columns)}_key", "u", columns))
        elif kind == "CheckConstraint" and columns:
            text = columns[0]
            referenced = tuple(sorted(c.name for c in relation.columns if re.search(rf"\b{re.escape(c.name)}\b", text)))
            model.add_constraint(_Constraint(schema, table, name or f"{table}_check", "c", referenced,
                                             definition=f"CHECK ({text})"))
        elif kind == "PrimaryKeyConstraint" and columns:
            model.add_constraint(_Constraint(schema, table, name or f"{table}_pkey", "p", columns))


def _sa_enum_call(args: Sequence[ast.AST], type_kw: ast.AST | None) -> ast.Call | None:
    for node in [*args, type_kw]:
        if isinstance(node, ast.Call) and _call_name(node) == "Enum":
            return node
    return None


# ------------------------------------------------------------------------------- Django

_DJANGO_TYPES = {
    "AutoField": "integer", "BigAutoField": "bigint", "SmallAutoField": "smallint",
    "IntegerField": "integer", "BigIntegerField": "bigint", "SmallIntegerField": "smallint",
    "PositiveIntegerField": "integer", "PositiveBigIntegerField": "bigint", "PositiveSmallIntegerField": "smallint",
    "CharField": "character varying", "SlugField": "character varying", "EmailField": "character varying",
    "URLField": "character varying", "TextField": "text", "DecimalField": "numeric", "FloatField": "double precision",
    "BooleanField": "boolean", "NullBooleanField": "boolean", "DateField": "date", "DateTimeField": "timestamptz",
    "TimeField": "time", "DurationField": "interval", "UUIDField": "uuid", "JSONField": "jsonb",
    "BinaryField": "bytea", "GenericIPAddressField": "inet",
}
_DJANGO_DEFAULT_LENGTH = {"SlugField": 50, "EmailField": 254, "URLField": 200}
_DJANGO_RELATIONS = ("ForeignKey", "OneToOneField")


def _django_table(node: ast.ClassDef, app_label: str) -> str:
    for stmt in node.body:
        if isinstance(stmt, ast.ClassDef) and stmt.name == "Meta":
            db_table = _class_attr(stmt, "db_table")
            if isinstance(db_table, str):
                return db_table
    return f"{app_label}_{node.name.lower()}"


def _django_abstract(node: ast.ClassDef) -> bool:
    return any(isinstance(s, ast.ClassDef) and s.name == "Meta" and _class_attr(s, "abstract") is True for s in node.body)


def _django_models(tree: ast.Module, app_label: str) -> dict[str, ast.ClassDef]:
    return {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)
            and any(_call_name(b) == "Model" for b in n.bases) and not _django_abstract(n)}


def _django_model(model: SchemaModel, node: ast.ClassDef, app_label: str, path: str, local: dict[str, ast.ClassDef],
                  enums: dict[str, list[str]], pending: list[_Pending]) -> None:
    table = _django_table(node, app_label)
    relation = model.add_relation(DEFAULT_SCHEMA, table, "r", origin="orm", path=path)
    if relation is None:
        return
    explicit_pk = None
    fields: list[tuple[str, ast.Call]] = []
    for stmt in node.body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name) \
                and isinstance(stmt.value, ast.Call):
            fields.append((stmt.targets[0].id, stmt.value))
            if _kw_literal(stmt.value, "primary_key") is True:
                explicit_pk = stmt.targets[0].id
    if explicit_pk is None:
        relation.columns.append(_Column("id", pg_type("bigint"), not_null=True))
        model.add_constraint(_Constraint(DEFAULT_SCHEMA, table, f"{table}_pkey", "p", ("id",)))
    for attr, call in fields:
        kind = _call_name(call)
        if kind in _DJANGO_RELATIONS:
            target = call.args[0] if call.args else _kw(call, "to")
            ref = _django_ref(target, app_label, local, node.name)
            column_name = _kw_literal(call, "db_column") or f"{attr}_id"
            relation.columns.append(_Column(column_name, pg_type("bigint"), not_null=_kw_literal(call, "null") is not True))
            if ref is not None:
                pending.append(_Pending(DEFAULT_SCHEMA, table, (column_name,), (DEFAULT_SCHEMA, ref, None),
                                        f"{table}_{column_name}_fkey"))
            if kind == "OneToOneField":
                model.add_constraint(_Constraint(DEFAULT_SCHEMA, table, f"{table}_{column_name}_key", "u", (column_name,)))
            continue
        spelling = _DJANGO_TYPES.get(kind or "")
        if spelling is None:
            continue
        column_name = _kw_literal(call, "db_column") or attr
        modifiers: list[str] = []
        if spelling == "character varying":
            length = _kw_literal(call, "max_length", _DJANGO_DEFAULT_LENGTH.get(kind))
            modifiers = [str(length)] if isinstance(length, int) else []
        elif spelling == "numeric":
            digits, places = _kw_literal(call, "max_digits"), _kw_literal(call, "decimal_places")
            modifiers = [str(digits), str(places)] if isinstance(digits, int) and isinstance(places, int) else []
        is_pk = _kw_literal(call, "primary_key") is True
        relation.columns.append(_Column(column_name, pg_type(spelling, modifiers),
                                        not_null=is_pk or _kw_literal(call, "null") is not True))
        if is_pk:
            model.add_constraint(_Constraint(DEFAULT_SCHEMA, table, f"{table}_pkey", "p", (column_name,)))
        elif _kw_literal(call, "unique") is True:
            model.add_constraint(_Constraint(DEFAULT_SCHEMA, table, f"{table}_{column_name}_key", "u", (column_name,)))
        labels = _choices(_kw(call, "choices"), enums)
        if labels:
            model.add_constraint(_Constraint(DEFAULT_SCHEMA, table, f"{table}_{column_name}_check", "c", (column_name,),
                                             definition=_check_definition(column_name, labels)))


def _django_ref(target: ast.AST | None, app_label: str, local: dict[str, ast.ClassDef], self_name: str) -> str | None:
    value = _literal(target)
    if isinstance(value, str):
        if value == "self":
            value = self_name
        if "." in value:
            label, _, name = value.partition(".")
            return f"{label}_{name.lower()}"
        return _django_table(local[value], app_label) if value in local else f"{app_label}_{value.lower()}"
    if isinstance(target, ast.Name) and target.id in local:
        return _django_table(local[target.id], app_label)
    return None


def _choices(node: ast.AST | None, enums: dict[str, list[str]]) -> list[str]:
    if node is None:
        return []
    if isinstance(node, ast.Attribute) and node.attr == "choices" and _call_name(node.value) in enums:
        return enums[_call_name(node.value)]
    value = _literal(node)
    if isinstance(value, (list, tuple)):
        return [str(item[0]) for item in value if isinstance(item, (list, tuple)) and item and isinstance(item[0], str)]
    return []


# ------------------------------------------------------------------------------- entry points


def read_python_models(model: SchemaModel, source: str, *, path: str, app_label: str) -> list[_Pending]:
    """Add the SQLAlchemy and Django models in one module; returns its unresolved foreign keys."""
    try:
        tree = ast.parse(source, filename=path)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        model.issue("unparseable", f"file:{path}")
        return []
    enums = _enum_classes(tree)
    pending: list[_Pending] = []
    local = _django_models(tree, app_label)
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if node.name in local:
            _django_model(model, node, app_label, path, local, enums, pending)
        elif _class_attr(node, "__tablename__") is not None:
            _sqlalchemy_model(model, node, path, enums, pending)
    return pending


def resolve_foreign_keys(model: SchemaModel, pending: Iterable[_Pending]) -> None:
    """Foreign keys declared in code, once every model is known: unresolvable ones are issues."""
    for fk in pending:
        if not model.has(fk.schema, fk.table):
            continue
        ref_schema, ref_table, ref_column = fk.ref
        if not model.has(ref_schema, ref_table):
            model.issue("foreign_key_target_unknown", f"column:{fk.schema}.{fk.table}.{fk.columns[0]}")
            continue
        ref_columns = (ref_column,) if ref_column else next(
            (c.columns for c in model.constraints if (c.schema, c.table, c.type) == (ref_schema, ref_table, "p")), ())
        model.add_constraint(_Constraint(fk.schema, fk.table, fk.name, "f", fk.columns, ref_schema=ref_schema,
                                         ref_table=ref_table, ref_columns=tuple(ref_columns)))


# ------------------------------------------------------------------------------- Alembic


def alembic_order(sources: Sequence[tuple[str, str]]) -> list[tuple[str, str]]:
    """Migration files in ``down_revision`` order (path order where the chain is unreadable)."""
    revisions: dict[str, tuple[str, str]] = {}
    parent: dict[str, Any] = {}
    unordered: list[tuple[str, str]] = []
    for path, source in sorted(sources):
        try:
            tree = ast.parse(source, filename=path)
        except (SyntaxError, ValueError, RecursionError, MemoryError):
            unordered.append((path, source))
            continue
        values = {t.id: _literal(s.value) for s in tree.body if isinstance(s, (ast.Assign, ast.AnnAssign))
                  for t in ([s.target] if isinstance(s, ast.AnnAssign) else s.targets) if isinstance(t, ast.Name)}
        revision = values.get("revision")
        if not isinstance(revision, str) or revision in revisions:
            unordered.append((path, source))
            continue
        revisions[revision] = (path, source)
        parent[revision] = values.get("down_revision")
    ordered: list[tuple[str, str]] = []
    done: set[str] = set()

    def visit(rev: str, trail: frozenset[str]) -> None:
        if rev in done or rev in trail or rev not in revisions:
            return
        ups = parent[rev] if isinstance(parent[rev], (list, tuple)) else [parent[rev]]
        for up in ups:
            if isinstance(up, str):
                visit(up, trail | {rev})
        done.add(rev)
        ordered.append(revisions[rev])

    for rev in sorted(revisions):
        visit(rev, frozenset())
    return ordered + unordered


def _upgrade(tree: ast.Module) -> list[ast.Call]:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "upgrade":
            return [n.value for n in ast.walk(node) if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
                    and isinstance(n.value.func, ast.Attribute) and _call_name(n.value.func.value) == "op"]
    return []


def read_alembic(model: SchemaModel, source: str, *, path: str, pending: list[_Pending]) -> None:
    """Apply one Alembic migration's ``upgrade()`` to ``model``."""
    try:
        tree = ast.parse(source, filename=path)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        model.issue("unparseable", f"file:{path}")
        return
    enums = _enum_classes(tree)
    for call in sorted(_upgrade(tree), key=lambda c: (c.lineno, c.col_offset)):
        op = call.func.attr
        schema = _kw_literal(call, "schema") or DEFAULT_SCHEMA
        table = _literal(call.args[0]) if call.args else None
        if op == "create_table" and isinstance(table, str):
            relation = model.add_relation(schema, table, "r", origin="migration", path=path)
            if relation is None:
                continue
            for item in call.args[1:]:
                _alembic_item(model, relation, item, schema, enums, pending)
        elif op == "add_column" and isinstance(table, str) and len(call.args) > 1:
            relation = model.relations.get((schema, table))
            if relation is not None:
                _alembic_item(model, relation, call.args[1], schema, enums, pending)
        elif op == "drop_table" and isinstance(table, str):
            model.drop_relation(schema, table)
        elif op == "drop_column" and isinstance(table, str) and len(call.args) > 1:
            relation = model.relations.get((schema, table))
            column = _literal(call.args[1])
            if relation is not None and isinstance(column, str):
                relation.columns = [c for c in relation.columns if c.name != column]
        elif op == "create_foreign_key" and len(call.args) >= 5:
            name, source_table, ref_table, cols, ref_cols = (_literal(a) for a in call.args[:5])
            if isinstance(source_table, str) and isinstance(ref_table, str) and isinstance(cols, list):
                pending.append(_Pending(schema, source_table, tuple(cols),
                                        (_kw_literal(call, "referent_schema") or schema, ref_table,
                                         ref_cols[0] if isinstance(ref_cols, list) and len(ref_cols) == 1 else None),
                                        name or f"{source_table}_{'_'.join(cols)}_fkey"))
        elif op == "create_unique_constraint" and len(call.args) >= 3:
            name, table_name, cols = (_literal(a) for a in call.args[:3])
            if isinstance(table_name, str) and isinstance(cols, list):
                model.add_constraint(_Constraint(schema, table_name, name or f"{table_name}_{'_'.join(cols)}_key",
                                                 "u", tuple(cols)))


def _alembic_item(model: SchemaModel, relation: Any, item: ast.AST, schema: str, enums: dict[str, list[str]],
                  pending: list[_Pending]) -> None:
    if not isinstance(item, ast.Call):
        return
    kind = _call_name(item)
    table = relation.name
    if kind == "Column":
        args = list(item.args)
        name = _literal(args.pop(0)) if args else None
        if not isinstance(name, str) or relation.column(name) is not None:
            return
        pgt, labels, foreign = None, None, None
        for arg in args:
            if isinstance(arg, ast.Call) and _call_name(arg) == "ForeignKey":
                foreign = _fk_target(arg, schema)
            elif pgt is None and labels is None:
                pgt, labels = _sa_type(arg, enums)
        if labels is not None:
            enum_call = _sa_enum_call(args, None)
            enum_name = _kw_literal(enum_call, "name") if enum_call else None
            enum_name = enum_name if isinstance(enum_name, str) else f"{table}_{name}"
            model.enums.setdefault((schema, enum_name), list(labels))
            pgt = model.enum_type(schema, enum_name)
        is_pk = _kw_literal(item, "primary_key") is True
        relation.columns.append(_Column(name, pgt or pg_type("unknown"),
                                        not_null=is_pk or _kw_literal(item, "nullable") is False,
                                        has_default=_kw(item, "server_default") is not None))
        if is_pk:
            model.add_constraint(_Constraint(schema, table, f"{table}_pkey", "p", (name,)))
        if _kw_literal(item, "unique") is True:
            model.add_constraint(_Constraint(schema, table, f"{table}_{name}_key", "u", (name,)))
        if foreign:
            pending.append(_Pending(schema, table, (name,), foreign, f"{table}_{name}_fkey"))
        return
    columns = tuple(v for v in (_literal(a) for a in item.args) if isinstance(v, str))
    constraint_name = _kw_literal(item, "name")
    if kind == "PrimaryKeyConstraint" and columns:
        for column in columns:
            if (col := relation.column(column)) is not None:
                col.not_null = True
        model.add_constraint(_Constraint(schema, table, constraint_name or f"{table}_pkey", "p", columns))
    elif kind == "UniqueConstraint" and columns:
        model.add_constraint(_Constraint(schema, table, constraint_name or f"{table}_{'_'.join(columns)}_key", "u", columns))
    elif kind == "ForeignKeyConstraint" and len(item.args) >= 2:
        cols, refs = _literal(item.args[0]), _literal(item.args[1])
        if isinstance(cols, list) and isinstance(refs, list) and refs and all(isinstance(r, str) for r in refs):
            parts = refs[0].split(".")
            ref_schema, ref_table = (parts[0], parts[1]) if len(parts) == 3 else (schema, parts[0])
            ref_cols = tuple(r.split(".")[-1] for r in refs)
            model.add_constraint(_Constraint(schema, table, constraint_name or f"{table}_{'_'.join(cols)}_fkey", "f",
                                             tuple(cols), ref_schema=ref_schema, ref_table=ref_table,
                                             ref_columns=ref_cols))
    elif kind == "CheckConstraint" and columns:
        text = columns[0]
        referenced = tuple(sorted(c.name for c in relation.columns if re.search(rf"\b{re.escape(c.name)}\b", text)))
        model.add_constraint(_Constraint(schema, table, constraint_name or f"{table}_check", "c", referenced,
                                         definition=f"CHECK ({text})"))


# ------------------------------------------------------------------------------- routes

_HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")
_FLASK_PARAM = re.compile(r"<(?:[a-z_]+:)?([A-Za-z_][A-Za-z0-9_]*)>")
_PATH_PARAM = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass(frozen=True, slots=True)
class Route:
    method: str
    path: str
    handler: str
    summary: str | None
    path_parameters: tuple[str, ...]


def _prefixes(tree: ast.Module) -> dict[str, str]:
    """Module-level ``router = APIRouter(prefix=...)`` / ``bp = Blueprint(..., url_prefix=...)``."""
    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) \
                and isinstance(node.value, ast.Call):
            kind = _call_name(node.value)
            key = {"APIRouter": "prefix", "Blueprint": "url_prefix"}.get(kind or "")
            if key:
                prefix = _kw_literal(node.value, key)
                out[node.targets[0].id] = prefix if isinstance(prefix, str) else ""
            elif kind in ("FastAPI", "Flask"):
                out[node.targets[0].id] = ""
    return out


def routes(source: str, *, path: str) -> list[Route]:
    """The HTTP handlers a module declares with decorators. Unparseable source yields none."""
    try:
        tree = ast.parse(source, filename=path)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return []
    prefixes = _prefixes(tree)
    found: list[Route] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            owner = decorator.func.value
            if not isinstance(owner, ast.Name) or owner.id not in prefixes:
                continue
            verb = decorator.func.attr
            route_path = _literal(decorator.args[0]) if decorator.args else _kw_literal(decorator, "path")
            if not isinstance(route_path, str):
                continue
            if verb in _HTTP_METHODS:
                methods = [verb.upper()]
            elif verb in ("route", "api_route"):
                declared = _kw_literal(decorator, "methods")
                methods = [m.upper() for m in declared if isinstance(m, str)] if isinstance(declared, list) else ["GET"]
            else:
                continue
            tail = route_path.lstrip("/")
            joined = prefixes[owner.id].rstrip("/") + ("/" + tail if tail else "")
            full = "/" + _FLASK_PARAM.sub(r"{\1}", joined).lstrip("/")
            docstring = ast.get_docstring(node)
            summary = docstring.strip().splitlines()[0][:300] if docstring and docstring.strip() else None
            params = tuple(_PATH_PARAM.findall(full))
            found.extend(Route(m, full, node.name, summary, params) for m in methods if m.lower() in _HTTP_METHODS)
    return sorted(set(found), key=lambda r: (r.path, r.method, r.handler))
