"""Read templates: the only way a generated tool reaches a connected system (ADR-022, AP6).

A generated tool never carries query text. It names a :class:`ReadTemplate` -- a small AST
compiled from the Project Twin -- and supplies parameter *values*. Two properties make that safe,
and both are enforced by construction here rather than by review:

* **Identifiers come from the twin, values come from parameters.** A template's table, columns
  and path are fixed when the blueprint is compiled (from twin-allowlisted names) and approved by
  digest. :func:`render_sql` quotes every identifier and binds every value as a ``$n`` parameter;
  :func:`render_http` percent-encodes every value into its own path segment or query value. No
  argument can change the statement's shape -- the SQL text depends on the template alone, which
  ``tests/unit/test_adaptation_compiler.py`` fuzzes.
* **Reads only, always bounded.** SQL is a single ``SELECT`` with a ``LIMIT`` no argument can
  exceed; HTTP is ``GET`` only. There is no write node in the AST to compile to.

The AST is deliberately small: one table, optional equality / containment / range filters (each
optional filter renders as ``($n IS NULL OR ...)`` so omitting it does not change the text),
aggregates with ``GROUP BY``, ``ORDER BY`` and the limit.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import quote, urlencode

from .errors import ValidationError

__all__ = [
    "TemplateKind",
    "FilterOp",
    "AggregateFunction",
    "ValueCast",
    "SqlColumn",
    "SqlFilter",
    "SqlAggregate",
    "SqlSelect",
    "HttpGet",
    "ReadTemplate",
    "RenderedSql",
    "render_sql",
    "render_http",
    "MAX_TEMPLATE_ROWS",
]

MAX_TEMPLATE_ROWS = 200
_SLUG = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
_PARAM = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_CONNECTOR = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_PLACEHOLDER = re.compile(r"\{([^{}/]+)\}")
_MAX_IDENT = 63


def _ident(value: str, what: str) -> None:
    """A catalog identifier: any characters Postgres allows, bounded, no NUL or control chars."""
    if not isinstance(value, str) or not value or len(value) > _MAX_IDENT or any(ord(c) < 32 for c in value):
        raise ValidationError(f"{what} must be a 1-{_MAX_IDENT} character identifier", details={what: value})


def _param(value: str) -> None:
    if not _PARAM.match(value):
        raise ValidationError("parameter names are snake_case", details={"param": value})


class TemplateKind(str, enum.Enum):
    SQL = "sql"
    HTTP = "http"

    def __str__(self) -> str:
        return self.value


class FilterOp(str, enum.Enum):
    EQ = "eq"
    CONTAINS = "contains"  # case-insensitive substring; no LIKE wildcards reach the database
    GTE = "gte"
    LTE = "lte"

    def __str__(self) -> str:
        return self.value


class ValueCast(str, enum.Enum):
    """How a bound value is read server-side. Closed: a cast is chosen at compile time from the
    column's type, never from an argument. ``TIMESTAMPTZ`` takes ISO text (``$n::text::timestamptz``)
    so a tool argument stays a string and the database parses it."""

    TIMESTAMPTZ = "timestamptz"

    def __str__(self) -> str:
        return self.value


class AggregateFunction(str, enum.Enum):
    COUNT = "count"
    SUM = "sum"
    AVG = "avg"
    MIN = "min"
    MAX = "max"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class SqlColumn:
    """A projected column. ``alias`` is the output field name the tool's schema declares."""

    name: str
    alias: str

    def __post_init__(self) -> None:
        _ident(self.name, "column")
        _param(self.alias)


@dataclass(frozen=True, slots=True)
class SqlFilter:
    column: str
    op: FilterOp
    param: str
    optional: bool = False
    cast: ValueCast | None = None

    def __post_init__(self) -> None:
        _ident(self.column, "column")
        _param(self.param)
        if self.cast is not None and self.op not in (FilterOp.GTE, FilterOp.LTE):
            raise ValidationError("only range filters take a cast", details={"param": self.param})


@dataclass(frozen=True, slots=True)
class SqlAggregate:
    function: AggregateFunction
    alias: str
    column: str | None = None  # None only for count(*)

    def __post_init__(self) -> None:
        _param(self.alias)
        if self.column is None:
            if self.function is not AggregateFunction.COUNT:
                raise ValidationError("only count may omit its column", details={"alias": self.alias})
        else:
            _ident(self.column, "column")


@dataclass(frozen=True, slots=True)
class SqlSelect:
    schema: str
    table: str
    columns: tuple[SqlColumn, ...] = ()
    filters: tuple[SqlFilter, ...] = ()
    #: An OR-group: a row matches if any of these holds (search over several text columns).
    any_of: tuple[SqlFilter, ...] = ()
    aggregates: tuple[SqlAggregate, ...] = ()
    group_by: tuple[str, ...] = ()
    order_by: tuple[str, ...] = ()
    max_rows: int = 50
    limit_param: str | None = "limit"

    def __post_init__(self) -> None:
        _ident(self.schema, "schema")
        _ident(self.table, "table")
        if not self.columns and not self.aggregates:
            raise ValidationError("a select needs columns or aggregates")
        if self.aggregates:
            projected = {c.name for c in self.columns}
            if projected != set(self.group_by):
                raise ValidationError("with aggregates, the projected columns must be exactly the group-by")
        elif self.group_by:
            raise ValidationError("group_by without aggregates")
        for name in self.group_by + self.order_by:
            _ident(name, "column")
        aliases = [c.alias for c in self.columns] + [a.alias for a in self.aggregates]
        if len(set(aliases)) != len(aliases):
            raise ValidationError("duplicate output field names")
        if not 1 <= self.max_rows <= MAX_TEMPLATE_ROWS:
            raise ValidationError(f"max_rows must be 1..{MAX_TEMPLATE_ROWS}")
        if self.limit_param is not None:
            _param(self.limit_param)
        if any(f.op is not FilterOp.CONTAINS for f in self.any_of):
            raise ValidationError("an any_of group holds containment filters only")
        if len({f.param for f in self.any_of}) > 1:
            raise ValidationError("an any_of group shares one parameter")

    def params(self) -> tuple[str, ...]:
        names = [f.param for f in self.filters + self.any_of]
        if self.limit_param:
            names.append(self.limit_param)
        return tuple(dict.fromkeys(names))


@dataclass(frozen=True, slots=True)
class HttpGet:
    """``GET path`` with ``{placeholder}`` segments and query parameters, each bound to a param."""

    path: str
    path_params: Mapping[str, str] = field(default_factory=dict)
    query_params: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.path.startswith("/") or any(c in self.path for c in "?#\\") or "/../" in f"{self.path}/" \
                or len(self.path) > 512 or any(ord(c) < 33 for c in self.path):
            raise ValidationError("path must be an absolute path without query, fragment or dot segments")
        placeholders = set(_PLACEHOLDER.findall(self.path))
        if placeholders != set(self.path_params):
            raise ValidationError("every path placeholder needs exactly one parameter",
                                  details={"placeholders": sorted(placeholders)})
        for segment in self.path.split("/"):
            if "{" in segment and not _PLACEHOLDER.fullmatch(segment):
                raise ValidationError("a placeholder must be a whole path segment", details={"segment": segment})
        for value in (*self.path_params.values(), *self.query_params.values()):
            _param(value)
        for key in self.query_params:
            if not key or len(key) > 128 or any(ord(c) < 33 or c in "&=#?" for c in key):
                raise ValidationError("bad query parameter key", details={"key": key})

    def params(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys([*self.path_params.values(), *self.query_params.values()]))


@dataclass(frozen=True, slots=True)
class ReadTemplate:
    id: str
    connector: str
    kind: TemplateKind
    sql: SqlSelect | None = None
    http: HttpGet | None = None

    def __post_init__(self) -> None:
        if not _SLUG.match(self.id):
            raise ValidationError("template ids are slugs", details={"id": self.id})
        if not _CONNECTOR.match(self.connector):
            raise ValidationError("connector ids are slugs", details={"connector": self.connector})
        if (self.kind is TemplateKind.SQL) != (self.sql is not None) or \
                (self.kind is TemplateKind.HTTP) != (self.http is not None):
            raise ValidationError("a template carries exactly the body its kind names", details={"id": self.id})

    def params(self) -> tuple[str, ...]:
        body: Any = self.sql if self.sql is not None else self.http
        return body.params()


# ------------------------------------------------------------------------------- rendering


@dataclass(frozen=True, slots=True)
class RenderedSql:
    text: str
    values: tuple[Any, ...]


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def render_sql(template: ReadTemplate, arguments: Mapping[str, Any]) -> RenderedSql:
    """Statement text from the template alone; every argument becomes a bound value."""
    select = template.sql
    if select is None:
        raise ValidationError("not an SQL template", details={"id": template.id})
    order = select.params()
    index = {name: i + 1 for i, name in enumerate(order)}

    def predicate(f: SqlFilter) -> str:
        column, n = _q(f.column), index[f.param]
        value = f"${n}::text::{f.cast.value}" if f.cast is not None else f"${n}"
        if f.op is FilterOp.EQ:
            test = f"{column} = {value}"
        elif f.op is FilterOp.CONTAINS:
            test = f"strpos(lower({column}::text), lower(${n}::text)) > 0"
        elif f.op is FilterOp.GTE:
            test = f"{column} >= {value}"
        else:
            test = f"{column} <= {value}"
        # The comparison comes first so it types $n before `IS NULL` sees it (PostgreSQL infers a
        # parameter's type at its first use; `$n IS NULL` alone cannot).
        return f"({test} OR ${n} IS NULL)" if f.optional else test

    projections = [f"{_q(c.name)} AS {_q(c.alias)}" for c in select.columns]
    for agg in select.aggregates:
        target = "*" if agg.column is None else _q(agg.column)
        projections.append(f"{agg.function.value}({target}) AS {_q(agg.alias)}")
    text = f"SELECT {', '.join(projections)} FROM {_q(select.schema)}.{_q(select.table)}"
    where = [predicate(f) for f in select.filters]
    if select.any_of:
        group = " OR ".join(predicate(f) for f in select.any_of)
        where.append(f"({group})")
    if where:
        text += " WHERE " + " AND ".join(where)
    if select.group_by:
        text += " GROUP BY " + ", ".join(_q(c) for c in select.group_by)
    if select.order_by:
        text += " ORDER BY " + ", ".join(_q(c) for c in select.order_by)
    if select.limit_param:
        n = index[select.limit_param]
        text += f" LIMIT LEAST(COALESCE(${n}::int, {select.max_rows}), {select.max_rows})"
    else:
        text += f" LIMIT {select.max_rows}"
    return RenderedSql(text, tuple(arguments.get(name) for name in order))


def render_http(template: ReadTemplate, arguments: Mapping[str, Any]) -> str:
    """The request target: each value percent-encoded into its own segment or query value."""
    get = template.http
    if get is None:
        raise ValidationError("not an HTTP template", details={"id": template.id})

    def segment(match: re.Match) -> str:
        value = arguments.get(get.path_params[match.group(1)])
        if value is None:
            raise ValidationError("missing path parameter", details={"param": get.path_params[match.group(1)]})
        return quote(str(value), safe="")

    path = _PLACEHOLDER.sub(segment, get.path)
    query = [(key, str(arguments[param])) for key, param in sorted(get.query_params.items())
             if arguments.get(param) is not None]
    return f"{path}?{urlencode(query)}" if query else path
