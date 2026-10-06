"""Tool factory: one capability selection -> one read template + one generated tool (AP6).

Every tool is read-only by construction (:class:`~aura_core.domain.blueprint.BlueprintTool` has no
write field; :class:`~aura_core.domain.templates.ReadTemplate` has no write node). Identifiers in a
template come from twin elements, never from text; parameters are values only. A selection that
cannot be compiled safely -- a composite key, an API entity with no matching read operation, a
primitive with no factory yet -- comes back as a reason code instead of a tool.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from ...domain.ausm import AusmElement, ElementKind
from ...domain.capabilities import PlannedCapability
from ...domain.connectors import ConnectorKind
from ...domain.blueprint import BlueprintTool
from ...domain.templates import (
    AggregateFunction,
    FilterOp,
    HttpGet,
    ReadTemplate,
    SqlAggregate,
    SqlColumn,
    SqlFilter,
    SqlSelect,
    TemplateKind,
    ValueCast,
)
from ...domain.tools import ParameterSpec, ToolSensitivity
from ...domain.untrusted import UntrustedText
from ..normalize import MAX_OPTIONAL_QUERY
from .common import MAX_COLUMNS, TwinView, is_textual, json_type, param_type, slug, unique_slug

__all__ = ["CompiledTool", "build_tool", "DEFAULT_MAX_ROWS"]

DEFAULT_MAX_ROWS = 50
_MAX_FILTERS = 5
_FILTERABLE = ("status", "category", "flag", "reference")


@dataclass(frozen=True, slots=True)
class CompiledTool:
    selection: PlannedCapability
    tool: BlueprintTool
    template: ReadTemplate
    view: str            # the ViewType value the UI factory binds this tool to
    entity: str | None   # the entity the tool is about (navigation groups by it)


class _Drop(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def build_tool(selection: PlannedCapability, view: TwinView, taken: set[str]) -> CompiledTool | str:
    """A compiled tool, or the reason code it could not be compiled."""
    target = view.get(selection.target)
    if target is None:
        return "target_missing"
    builder = _BUILDERS.get(selection.capability)
    if builder is None:
        return f"no_factory:{selection.capability}"
    try:
        return builder(selection, target, view, taken)
    except _Drop as drop:
        return drop.reason


# ------------------------------------------------------------------------------- helpers


def _sql_parts(entity: AusmElement, view: TwinView) -> tuple[str, str]:
    if view.kind_of(entity) is not ConnectorKind.POSTGRES or entity.properties.get("source_type") != "table":
        raise _Drop("not_a_sql_table")
    schema = entity.properties.get("schema")
    if not isinstance(schema, str):
        raise _Drop("schema_unknown")
    return schema, view.identifier(entity)


def _projection(attributes: list[AusmElement], view: TwinView) -> tuple[tuple[SqlColumn, ...], dict[str, Any]]:
    columns: list[SqlColumn] = []
    properties: dict[str, Any] = {}
    taken: set[str] = set()
    for attribute in attributes[:MAX_COLUMNS]:
        name = view.identifier(attribute)
        alias = unique_slug(slug(name, limit=64), taken, limit=64)
        columns.append(SqlColumn(name, alias))
        properties[alias] = json_type(attribute)
    return tuple(columns), properties


def _rows_schema(properties: dict[str, Any], *, max_items: int | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "array",
        "items": {"type": "object", "properties": properties, "additionalProperties": False},
    }
    if max_items is not None:
        schema["maxItems"] = max_items
    return schema


def _limit_param(max_rows: int) -> ParameterSpec:
    return ParameterSpec("limit", "integer", f"Maximum number of rows to return (1-{max_rows}).",
                         required=False, minimum=1.0, maximum=float(max_rows))


def _value_param(name: str, attribute: AusmElement, description: str, *, required: bool) -> ParameterSpec:
    kind = param_type(attribute)
    return ParameterSpec(name, kind, description, required=required,
                         max_length=256 if kind == "string" else None)


def _describe(text: str) -> UntrustedText:
    # The sentence is ours; the entity label inside it is the connected system's own text.
    return UntrustedText.of(text, max_chars=400)


def _tool(name: str, description: str, selection: PlannedCapability, connector: str, template_id: str,
          output_schema: dict[str, Any], parameters: tuple[ParameterSpec, ...]) -> BlueprintTool:
    return BlueprintTool(
        name=name, description=_describe(description), capability=selection.key, connector=connector,
        template=template_id, output_schema=output_schema, parameters=parameters,
        sensitivity=max(selection.risk, ToolSensitivity.PERSONAL),
    )


def _single_key(entity: AusmElement, view: TwinView) -> AusmElement:
    key = view.primary_key(entity.id)
    if len(key) != 1:
        raise _Drop("composite_key" if key else "no_primary_key")
    return key[0]


def _compiled(selection, name, description, connector, template, schema, params, view_type, entity):
    tool = _tool(name, description, selection, connector, template.id, schema, params)
    return CompiledTool(selection, tool, template, view_type, entity)


# ------------------------------------------------------------------------------- builders


def _lookup(selection, entity, view: TwinView, taken):
    label = entity.label.raw()
    if view.kind_of(entity) is ConnectorKind.OPENAPI:
        # The API's own GET-by-id is compiled once, by operation.read; a second template for the
        # same operation would only be a second name for one call.
        raise _Drop("api_entity_uses_operation_read")
    schema, table = _sql_parts(entity, view)
    key = _single_key(entity, view)
    columns, props = _projection(view.attributes(entity.id), view)
    param = slug(view.identifier(key))
    name = unique_slug(slug("lookup", table), taken)
    template = ReadTemplate(name, entity.source_id, TemplateKind.SQL, sql=SqlSelect(
        schema, table, columns=columns, filters=(SqlFilter(view.identifier(key), FilterOp.EQ, param),),
        max_rows=1, limit_param=None,
    ))
    params = (_value_param(param, key, f"The identifier ({view.identifier(key)}) of the record.", required=True),)
    return _compiled(selection, name, f"Look up one {label} record by its identifier.", entity.source_id,
                     template, _rows_schema(props, max_items=1), params, "entity_detail", entity.id)


def _search(selection, entity, view: TwinView, taken):
    if view.kind_of(entity) is ConnectorKind.OPENAPI:
        raise _Drop("no_search_operation")
    schema, table = _sql_parts(entity, view)
    attributes = view.attributes(entity.id)
    searchable = [a for a in attributes if is_textual(a)
                  and a.properties.get("semantic_type") in ("text", "email", "category", "status")][:6]
    if not searchable:
        raise _Drop("no_text_columns")
    columns, props = _projection(attributes, view)
    name = unique_slug(slug("search", table), taken)
    template = ReadTemplate(name, entity.source_id, TemplateKind.SQL, sql=SqlSelect(
        schema, table, columns=columns,
        any_of=tuple(SqlFilter(view.identifier(a), FilterOp.CONTAINS, "query") for a in searchable),
        order_by=tuple(view.identifier(a) for a in view.primary_key(entity.id)),
        max_rows=DEFAULT_MAX_ROWS,
    ))
    fields = ", ".join(view.identifier(a) for a in searchable)
    params = (
        ParameterSpec("query", "string", f"Text to find in: {fields}."[:400], max_length=200),
        _limit_param(DEFAULT_MAX_ROWS),
    )
    return _compiled(selection, name, f"Find {entity.label.raw()} records whose text contains a query.",
                     entity.source_id, template, _rows_schema(props), params, "search", entity.id)


def _list(selection, entity, view: TwinView, taken):
    if view.kind_of(entity) is ConnectorKind.OPENAPI:
        raise _Drop("api_entity_uses_operation_read")
    schema, table = _sql_parts(entity, view)
    attributes = view.attributes(entity.id)
    filters: list[SqlFilter] = []
    params: list[ParameterSpec] = []
    used: set[str] = {"limit"}
    for attribute in attributes:
        if len(filters) >= _MAX_FILTERS:
            break
        semantic = attribute.properties.get("semantic_type")
        column = view.identifier(attribute)
        if semantic in _FILTERABLE:
            name = unique_slug(slug(column), used)
            filters.append(SqlFilter(column, FilterOp.EQ, name, optional=True))
            values = [str(s.properties.get("value")) for s in view.states(attribute.id)]
            hint = f" One of: {', '.join(values)}." if values else ""
            params.append(_value_param(name, attribute, f"Only records whose {column} equals this.{hint}"[:400],
                                       required=False))
    dated = next((a for a in attributes if a.properties.get("semantic_type") in ("timestamp", "date")), None)
    if dated is not None:
        column = view.identifier(dated)
        for suffix, op, word in (("from", FilterOp.GTE, "on or after"), ("to", FilterOp.LTE, "on or before")):
            name = unique_slug(slug(column, suffix), used)
            filters.append(SqlFilter(column, op, name, optional=True, cast=ValueCast.TIMESTAMPTZ))
            params.append(ParameterSpec(name, "string", f"Only records with {column} {word} this ISO date.",
                                        required=False, max_length=40))
    if not filters:
        raise _Drop("no_filterable_columns")
    columns, props = _projection(attributes, view)
    name = unique_slug(slug("list", table), taken)
    template = ReadTemplate(name, entity.source_id, TemplateKind.SQL, sql=SqlSelect(
        schema, table, columns=columns, filters=tuple(filters),
        order_by=tuple(view.identifier(a) for a in view.primary_key(entity.id)), max_rows=DEFAULT_MAX_ROWS,
    ))
    params.append(_limit_param(DEFAULT_MAX_ROWS))
    return _compiled(selection, name, f"List {entity.label.raw()} records, optionally filtered.",
                     entity.source_id, template, _rows_schema(props), tuple(params), "entity_list", entity.id)


def _traverse(selection, relation, view: TwinView, taken):
    source = view.get(relation.refs.get("from", ""))
    target = view.get(relation.refs.get("to", ""))
    via = view.get(relation.refs.get("via", "")) if relation.refs.get("via") else None
    if source is None or target is None:
        raise _Drop("target_missing")
    if relation.properties.get("relation_type") != "foreign_key":
        raise _Drop("not_a_foreign_key")
    if via is None:
        raise _Drop("composite_key")
    schema, table = _sql_parts(source, view)
    column = view.identifier(via)
    columns, props = _projection(view.attributes(source.id), view)
    param = slug(column)
    name = unique_slug(slug("list", table, "by", column), taken)
    template = ReadTemplate(name, source.source_id, TemplateKind.SQL, sql=SqlSelect(
        schema, table, columns=columns, filters=(SqlFilter(column, FilterOp.EQ, param),),
        order_by=tuple(view.identifier(a) for a in view.primary_key(source.id)), max_rows=DEFAULT_MAX_ROWS,
    ))
    params = (
        _value_param(param, via, f"The {target.label.raw()} identifier to follow ({column}).", required=True),
        _limit_param(DEFAULT_MAX_ROWS),
    )
    return _compiled(selection, name, f"List the {source.label.raw()} records linked to one {target.label.raw()} "
                     f"record through {column}.", source.source_id, template, _rows_schema(props), params,
                     "entity_list", source.id)


def _aggregate(selection, metric, view: TwinView, taken):
    entity = view.get(metric.refs.get("entity", ""))
    if entity is None:
        raise _Drop("target_missing")
    if view.kind_of(entity) is ConnectorKind.OPENAPI:
        raise _Drop("no_aggregate_operation")
    schema, table = _sql_parts(entity, view)
    measure = metric.properties.get("measure")
    if not isinstance(measure, str):
        raise _Drop("measure_unknown")
    attributes = view.attributes(entity.id)
    status = next((a for a in attributes if a.properties.get("semantic_type") == "status"), None)
    filters, params = (), ()
    if status is not None:
        column = view.identifier(status)
        filters = (SqlFilter(column, FilterOp.EQ, slug(column), optional=True),)
        params = (_value_param(slug(column), status, f"Only records whose {column} equals this.", required=False),)
    measure_alias = slug(measure)
    template_name = unique_slug(slug("total", table, measure), taken)
    template = ReadTemplate(template_name, entity.source_id, TemplateKind.SQL, sql=SqlSelect(
        schema, table, filters=filters, limit_param=None, max_rows=1,
        aggregates=(
            SqlAggregate(AggregateFunction.COUNT, "count"),
            SqlAggregate(AggregateFunction.SUM, f"sum_{measure_alias}"[:64], measure),
            SqlAggregate(AggregateFunction.AVG, f"avg_{measure_alias}"[:64], measure),
            SqlAggregate(AggregateFunction.MIN, f"min_{measure_alias}"[:64], measure),
            SqlAggregate(AggregateFunction.MAX, f"max_{measure_alias}"[:64], measure),
        ),
    ))
    number = {"type": ["number", "null"]}
    props = {"count": {"type": "integer"}, f"sum_{measure_alias}"[:64]: number, f"avg_{measure_alias}"[:64]: number,
             f"min_{measure_alias}"[:64]: number, f"max_{measure_alias}"[:64]: number}
    return _compiled(selection, template_name, f"Count {entity.label.raw()} records and total their {measure}.",
                     entity.source_id, template, _rows_schema(props, max_items=1), params, "metric_card", entity.id)


def _timeline(selection, entity, view: TwinView, taken):
    if view.kind_of(entity) is ConnectorKind.OPENAPI:
        raise _Drop("no_timeline_operation")
    schema, table = _sql_parts(entity, view)
    key = _single_key(entity, view)
    events = [view.get(e) for e in selection.evidence]
    names = [e.properties.get("attribute") for e in events if e is not None and e.kind is ElementKind.EVENT]
    attributes = [a for a in view.attributes(entity.id) if view.identifier(a) in names]
    if not attributes:
        raise _Drop("no_event_columns")
    columns, props = _projection([key, *attributes], view)
    param = slug(view.identifier(key))
    name = unique_slug(slug("timeline", table), taken)
    template = ReadTemplate(name, entity.source_id, TemplateKind.SQL, sql=SqlSelect(
        schema, table, columns=columns, filters=(SqlFilter(view.identifier(key), FilterOp.EQ, param),),
        max_rows=1, limit_param=None,
    ))
    params = (_value_param(param, key, f"The identifier ({view.identifier(key)}) of the record.", required=True),)
    return _compiled(selection, name, f"Show when things happened to one {entity.label.raw()} record.",
                     entity.source_id, template, _rows_schema(props, max_items=1), params, "timeline", entity.id)


def _status(selection, attribute, view: TwinView, taken):
    entity = view.get(attribute.parent or "")
    if entity is None:
        raise _Drop("target_missing")
    if view.kind_of(entity) is ConnectorKind.OPENAPI:
        raise _Drop("no_aggregate_operation")
    schema, table = _sql_parts(entity, view)
    column = view.identifier(attribute)
    alias = slug(column, limit=64)
    if alias == "count":
        alias = "state"
    name = unique_slug(slug("status_counts", table, column), taken)
    template = ReadTemplate(name, entity.source_id, TemplateKind.SQL, sql=SqlSelect(
        schema, table, columns=(SqlColumn(column, alias),), aggregates=(SqlAggregate(AggregateFunction.COUNT, "count"),),
        group_by=(column,), order_by=(column,), max_rows=100, limit_param=None,
    ))
    props = {alias: json_type(attribute), "count": {"type": "integer"}}
    return _compiled(selection, name, f"Count {entity.label.raw()} records in each {column} state.",
                     entity.source_id, template, _rows_schema(props, max_items=100), (), "metric_card", entity.id)


def _operation(selection, action, view: TwinView, taken):
    props = action.properties
    path = props.get("path")
    if view.kind_of(action) is not ConnectorKind.OPENAPI or props.get("http_method") != "GET" or not isinstance(path, str):
        raise _Drop("not_a_get_operation")
    path_params: dict[str, str] = {}
    query_params: dict[str, str] = {}
    params: list[ParameterSpec] = []
    used: set[str] = set()
    for segment in path.split("/"):
        if segment.startswith("{") and segment.endswith("}"):
            placeholder = segment[1:-1]
            name = unique_slug(slug(placeholder), used)
            path_params[placeholder] = name
            params.append(ParameterSpec(name, "string", f"Value for {{{placeholder}}} in the path.", max_length=256))
    required = str(props.get("required_parameters") or "")
    for item in filter(None, required.split(",")):
        where, _, key = item.partition(":")
        if where == "query" and key:
            name = unique_slug(slug(key), used)
            query_params[key] = name
            params.append(ParameterSpec(name, "string", f"Value for the {key} query parameter.", max_length=256))
    for index in range(1, MAX_OPTIONAL_QUERY + 1):
        optional = _optional_query(props.get(f"query_param_{index}"), used, query_params)
        if optional is not None:
            key, spec = optional
            query_params[key] = spec.name
            params.append(spec)
    entity = view.get(action.refs.get("entity", ""))
    if entity is not None:
        attr_props: dict[str, Any] = {}
        alias_taken: set[str] = set()
        for attribute in view.attributes(entity.id)[:MAX_COLUMNS]:
            attr_props[unique_slug(slug(view.identifier(attribute), limit=64), alias_taken, limit=64)] = json_type(attribute)
        record = {"type": "object", "properties": attr_props}
        schema = {"type": "array", "items": record} if props.get("returns_list") else record
    else:
        schema = {"type": "object"}
    operation = props.get("operation_id") or props.get("identifier") or path
    name = unique_slug(slug("api", str(operation)), taken)
    template = ReadTemplate(name, action.source_id, TemplateKind.HTTP,
                            http=HttpGet(path, path_params=path_params, query_params=query_params))
    label = (entity.label.raw() if entity is not None else action.label.raw())
    description = f"Call the read-only API operation {operation} ({label})."
    if action.description is not None and action.description:
        description += " " + action.description.raw()[:200]
    view_type = "entity_list" if props.get("returns_list") else "entity_detail"
    return _compiled(selection, name, description, action.source_id, template, schema, tuple(params),
                     view_type, entity.id if entity is not None else None)


_QUERY_TYPES = frozenset({"string", "integer", "number", "boolean", "array"})
_QUERY_ITEM_TYPES = frozenset({"string", "integer", "number", "boolean"})


def _optional_query(raw: Any, used: set[str], bound: dict[str, str]) -> tuple[str, ParameterSpec] | None:
    """One optional query parameter from its discovery descriptor (AR-2), or None to skip it.

    The descriptor is the connected system's data: every field is re-checked here and anything
    malformed drops the parameter rather than producing a half-typed one. The server's default
    is described, never applied -- an omitted argument is omitted from the request.
    """
    if not isinstance(raw, str):
        return None
    try:
        desc = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(desc, dict):
        return None
    key, kind, item = desc.get("n"), desc.get("t"), desc.get("i")
    if (not isinstance(key, str) or not key or len(key) > 128 or key in bound
            or any(ord(c) < 33 or c in "&=#?" for c in key) or kind not in _QUERY_TYPES):
        return None
    if kind == "array" and item not in _QUERY_ITEM_TYPES:
        return None
    enum = desc.get("e")
    if not (kind == "string" and isinstance(enum, list) and enum and all(isinstance(v, str) for v in enum)):
        enum = None
    bounds = {}
    for short, field in (("lo", "minimum"), ("hi", "maximum")):
        value = desc.get(short)
        if kind in ("integer", "number") and isinstance(value, (int, float)) and not isinstance(value, bool):
            bounds[field] = value
    default = desc.get("d")
    hint = f" The server's default is {default}." if isinstance(default, (str, int, float)) and not isinstance(default, bool) else ""
    name = unique_slug(slug(key), used)
    spec = ParameterSpec(
        name, kind, f"Optional value for the {key} query parameter.{hint}"[:400], required=False,
        enum=tuple(enum) if enum else None, max_length=256 if kind == "string" else None,
        item_type=item if kind == "array" else None, **bounds,
    )
    return key, spec


_BUILDERS: dict[str, Callable[..., CompiledTool]] = {
    "entity.lookup": _lookup,
    "entity.search": _search,
    "entity.list_filtered": _list,
    "relation.traverse": _traverse,
    "metric.aggregate": _aggregate,
    "state.timeline": _timeline,
    "workflow.status": _status,
    "operation.read": _operation,
}
