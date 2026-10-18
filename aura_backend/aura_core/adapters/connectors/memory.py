"""In-memory read replica: compiled SQL templates evaluated over plain rows (AP8).

The evaluation gate runs a candidate blueprint's tools against a replica of the connected system.
In production that replica is the registered connector itself (read-only, row-capped). This is the
substrate the default test suite uses instead -- the same role every in-memory storage adapter
plays: a real implementation of :class:`~aura_core.ports.connectors.ReadConnector`, not a mock, so
the gate exercises the code production runs.

It evaluates the template AST directly (``SqlSelect`` is small on purpose) with PostgreSQL's
semantics where they matter to a result: ``NULL`` never matches a comparison, an optional filter
with no argument matches everything, ``contains`` is a case-insensitive substring test over the
value's text, range filters with a ``timestamptz`` cast compare instants, ascending order puts
``NULL`` last and sorts an enum column by its declared label order (``enum_orders``), not
alphabetically, ``count(*)`` counts rows while the other aggregates skip ``NULL``, and an argument
whose type does not fit its column is refused the way the driver refuses it.
``tests/load/test_adaptation_gate_postgres.py`` holds it to that: every pilot template, run here and
against the loaded pilot database with the same arguments, returns the same rows.

Rows hold JSON values (what ``PostgresConnector.read`` returns: numbers, ISO text for times).
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Iterable, Mapping, Sequence

from ...domain.connectors import CallerIdentity, ConnectorDescriptor, ConnectorHealth, ConnectorKind, HealthStatus, IdentityMode
from ...domain.errors import ToolExecutionError
from ...domain.templates import AggregateFunction, FilterOp, ReadTemplate, SqlFilter, SqlSelect, TemplateKind

__all__ = ["InMemoryReadConnector"]

Rows = Sequence[Mapping[str, Any]]


class InMemoryReadConnector:
    """A read-only replica over ``tables``, keyed ``(schema, table)``."""

    def __init__(self, connector_id: str, tables: Mapping[tuple[str, str], Rows], *, row_cap: int = 1000,
                 enum_orders: Mapping[tuple[str, str, str], Sequence[str]] | None = None) -> None:
        self.descriptor = ConnectorDescriptor(
            id=connector_id, kind=ConnectorKind.POSTGRES, identity_mode=IdentityMode.SERVICE_ACCOUNT,
            display_name="In-memory replica", endpoint="postgresql://replica.invalid:5432/replica",
            row_cap=row_cap,
        )
        self._tables = {key: [dict(row) for row in rows] for key, rows in tables.items()}
        #: (schema, table, column) -> enum labels in declaration order (PostgreSQL sorts by it).
        self._enum_orders = {key: {label: i for i, label in enumerate(labels)}
                             for key, labels in (enum_orders or {}).items()}

    async def health(self) -> ConnectorHealth:
        return ConnectorHealth(HealthStatus.HEALTHY, write_capable=False)

    async def aclose(self) -> None:
        return None

    async def read(self, template: ReadTemplate, arguments: Mapping[str, Any],
                   identity: CallerIdentity | None = None) -> list[dict[str, Any]]:
        if template.kind is not TemplateKind.SQL or template.connector != self.descriptor.id:
            raise ToolExecutionError("this template does not belong to this replica", tool=template.id)
        select = template.sql
        rows = self._tables.get((select.schema, select.table))
        if rows is None:
            raise ToolExecutionError("the replica refused the read", tool=template.id,
                                     details={"sqlstate": "42P01"})
        ranks = {column: rank for (schema, table, column), rank in self._enum_orders.items()
                 if (schema, table) == (select.schema, select.table)}
        try:
            result = _evaluate(select, rows, arguments, ranks)
        except _Unfit:
            raise ToolExecutionError("an argument does not fit the column it filters", tool=template.id) from None
        except _Undefined:
            raise ToolExecutionError("the replica refused the read", tool=template.id,
                                     details={"sqlstate": "42703"}) from None
        return result[: self.descriptor.row_cap]


class _Unfit(Exception):
    """An argument the driver would refuse for this column (asyncpg ``DataError``)."""


class _Undefined(Exception):
    """A column the table does not have (``undefined_column``)."""


# ------------------------------------------------------------------------------- evaluation


def _evaluate(select: SqlSelect, rows: Rows, arguments: Mapping[str, Any],
              ranks: Mapping[str, Mapping[str, int]]) -> list[dict[str, Any]]:
    columns = _columns(rows)
    for name in _referenced(select):
        if columns and name not in columns:
            raise _Undefined(name)
    kept = [row for row in rows if _matches(select, row, arguments, rows)]
    if select.aggregates:
        out = _aggregate(select, kept, ranks)  # groups are ordered inside, by their group-by values
    else:
        out = _ordered(kept, select.order_by, ranks) if select.order_by else kept
    limit = select.max_rows
    if select.limit_param is not None:
        requested = arguments.get(select.limit_param)
        if requested is not None:
            if isinstance(requested, bool) or not isinstance(requested, int):
                raise _Unfit(select.limit_param)
            limit = min(requested, select.max_rows)
    out = out[: max(limit, 0)]
    if select.aggregates:
        return out
    return [{c.alias: row.get(c.name) for c in select.columns} for row in out]


def _columns(rows: Rows) -> set[str]:
    names: set[str] = set()
    for row in rows:
        names.update(row)
    return names


def _referenced(select: SqlSelect) -> Iterable[str]:
    yield from (c.name for c in select.columns)
    yield from (f.column for f in select.filters + select.any_of)
    yield from (a.column for a in select.aggregates if a.column is not None)
    yield from select.group_by
    yield from select.order_by


def _matches(select: SqlSelect, row: Mapping[str, Any], arguments: Mapping[str, Any], rows: Rows) -> bool:
    if not all(_predicate(f, row, arguments, rows) for f in select.filters):
        return False
    if select.any_of and not any(_predicate(f, row, arguments, rows) for f in select.any_of):
        return False
    return True


def _predicate(f: SqlFilter, row: Mapping[str, Any], arguments: Mapping[str, Any], rows: Rows) -> bool:
    value = arguments.get(f.param)
    _check_fit(f, value, rows)
    if value is None:
        # An optional filter renders as ``(test OR $n IS NULL)``; a required one compares with
        # NULL, which matches nothing.
        return f.optional
    cell = row.get(f.column)
    if cell is None:
        return False
    if f.op is FilterOp.CONTAINS:
        return str(value).lower() in _text(cell).lower()
    if f.cast is not None:
        left, right = _instant(cell), _instant(value)
        if left is None or right is None:
            raise _Unfit(f.param)
    else:
        left, right = cell, value
    if f.op is FilterOp.EQ:
        return left == right
    if f.op is FilterOp.GTE:
        return left >= right
    return left <= right


def _check_fit(f: SqlFilter, value: Any, rows: Rows) -> None:
    if value is None:
        return
    if f.op is FilterOp.CONTAINS or f.cast is not None:
        if not isinstance(value, str):
            raise _Unfit(f.param)  # ``$n::text`` binds text only
        return
    sample = next((row.get(f.column) for row in rows if row.get(f.column) is not None), None)
    if sample is None:
        return
    if isinstance(sample, bool) != isinstance(value, bool):
        raise _Unfit(f.param)
    if isinstance(sample, (int, float)) and not isinstance(sample, bool):
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise _Unfit(f.param)
        if isinstance(sample, int) and isinstance(value, float) and not value.is_integer():
            raise _Unfit(f.param)
    elif isinstance(sample, str) and not isinstance(value, str):
        raise _Unfit(f.param)


def _text(cell: Any) -> str:
    if isinstance(cell, bool):
        return "true" if cell else "false"
    return str(cell)


def _instant(value: Any) -> dt.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        try:
            parsed = dt.datetime.combine(dt.date.fromisoformat(value), dt.time())
        except ValueError:
            return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=dt.timezone.utc)


def _aggregate(select: SqlSelect, rows: Rows, ranks: Mapping[str, Mapping[str, int]]) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[Mapping[str, Any]]] = {}
    if select.group_by:
        for row in rows:
            groups.setdefault(tuple(row.get(c) for c in select.group_by), []).append(row)
    else:
        groups[()] = list(rows)  # an aggregate without GROUP BY returns one row, even over nothing
    out = []
    for key, members in groups.items():
        record: dict[str, Any] = dict(zip(select.group_by, key))
        projected = {c.alias: record.get(c.name) for c in select.columns}
        for agg in select.aggregates:
            projected[agg.alias] = _apply(agg.function, agg.column, members)
        out.append({**projected, **{f"\0{c}": record.get(c) for c in select.group_by}})
    if select.order_by:
        out = _ordered(out, tuple(f"\0{c}" for c in select.order_by),
                       {f"\0{c}": rank for c, rank in ranks.items()})
    return [{k: v for k, v in row.items() if not k.startswith("\0")} for row in out]


def _apply(function: AggregateFunction, column: str | None, rows: Rows) -> Any:
    if column is None:
        return len(rows)
    values = [row.get(column) for row in rows if row.get(column) is not None]
    if function is AggregateFunction.COUNT:
        return len(values)
    if not values:
        return None
    if function is AggregateFunction.MIN:
        return min(values)
    if function is AggregateFunction.MAX:
        return max(values)
    total = sum(values)
    if function is AggregateFunction.SUM:
        return total
    return float(total) / len(values)


def _ordered(rows: Sequence[Mapping[str, Any]], keys: Sequence[str],
             ranks: Mapping[str, Mapping[str, int]] | None = None) -> list[Mapping[str, Any]]:
    out = list(rows)
    for key in reversed(keys):  # stable sorts, least significant key first; NULLS LAST
        rank = (ranks or {}).get(key)

        def sort_key(row: Mapping[str, Any], key: str = key, rank: Mapping[str, int] | None = rank) -> tuple:
            value = row.get(key)
            if value is None:
                return (True, 0)
            return (False, rank.get(value, len(rank)) if rank is not None else value)

        out.sort(key=sort_key)
    return out
