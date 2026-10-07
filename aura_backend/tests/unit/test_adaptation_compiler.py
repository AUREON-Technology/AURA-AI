"""AP6: tool output schemas, read templates, the factories and the blueprint compiler.

No database, no network, no model. The exit criterion -- both pilots compile schema-valid
blueprints -- is ``test_both_pilots_compile_schema_valid_blueprints``; the live half (every
compiled SQL template executed on a real Postgres, results checked against their output schema)
is ``tests/load/test_adaptation_compiler_postgres.py``.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import random
from pathlib import Path
from typing import Any

import pytest

from aura_core.adaptation.compiler import CompilerService, compile_blueprint
from aura_core.adaptation.labeling import LabelingPass
from aura_core.adaptation.planner import CapabilityRegistry, PlanningService, plan_capabilities
from aura_core.adaptation.twin import TwinService
from aura_core.adapters.memory_adaptation import InMemoryBlueprintStore, InMemoryTwinStore
from aura_core.domain.blueprint import Blueprint, BlueprintState, check_against_twin
from aura_core.domain.compilation import BlueprintRecord, EvalCaseSpec
from aura_core.domain.discovery import DiscoveryReport
from aura_core.domain.errors import ConflictError, ValidationError
from aura_core.domain.identifiers import ToolCallId, UserId
from aura_core.domain.messages import ToolCall
from aura_core.domain.output_schema import check_output_schema, output_errors
from aura_core.domain.serialization import from_data, to_data
from aura_core.domain.templates import (
    FilterOp,
    HttpGet,
    ReadTemplate,
    SqlColumn,
    SqlFilter,
    SqlSelect,
    TemplateKind,
    render_http,
    render_sql,
)
from aura_core.domain.tools import ParameterSpec, ToolSensitivity, ToolSpec
from aura_core.domain.untrusted import UntrustedText
from aura_core.ports.coding import McpToolDescriptor
from aura_core.ports.runtime import ToolContext
from aura_core.tools.base import FunctionTool
from aura_core.tools.coding import build_mcp_bridge_tools
from aura_core.tools.policy import ToolPolicy
from aura_core.tools.registry import ToolRegistry

BACKEND = Path(__file__).resolve().parents[2]
PILOTS = BACKEND / "tests" / "fixtures" / "pilots"
SCHEMA = BACKEND.parent / "docs" / "schemas" / "blueprint.v1.json"


def _goldens():
    spec = importlib.util.spec_from_file_location("pilot_twin_goldens", BACKEND / "scripts" / "pilot_twin_goldens.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GOLDENS = _goldens()


@pytest.fixture(scope="module")
def compiled():
    return {pilot: GOLDENS.compile_pilot(pilot) for pilot in ("hr", "ecommerce")}


# ------------------------------------------------------------------------------- output schema


class TestOutputSchema:
    def test_the_subset_is_closed(self) -> None:
        check_output_schema({"type": "array", "items": {"type": "object", "properties": {"a": {"type": ["string", "null"]}}}})
        for bad in ({"type": "object", "$ref": "#/x"}, {"oneOf": []}, {"type": "string", "pattern": ".*"},
                    {"type": "thing"}, {"type": "object", "required": ["a"]}):
            with pytest.raises(ValidationError):
                check_output_schema(bad)

    def test_errors_name_paths_not_values(self) -> None:
        schema = {"type": "array", "maxItems": 1, "items": {
            "type": "object", "properties": {"n": {"type": "integer"}}, "additionalProperties": False}}
        assert output_errors(schema, [{"n": 1}]) == []
        problems = output_errors(schema, [{"n": "secret-value"}, {"x": 2}])
        assert problems and not any("secret-value" in p for p in problems)
        assert "$: more than 1 items" in problems

    def test_toolspec_accepts_and_freezes_a_schema(self) -> None:
        spec = ToolSpec("t", "d", output_schema={"type": "object", "properties": {}})
        assert spec.output_schema["type"] == "object"
        with pytest.raises(ValueError):
            ToolSpec("t", "d", output_schema={"type": "string"})
        assert ToolSpec("t", "d").output_schema is None

    @pytest.mark.parametrize("result, ok, kind", [
        ('{"n": 1}', True, None),
        ('{"n": "one"}', False, "invalid_output"),
        ("not json", False, "invalid_output"),
    ])
    async def test_the_registry_checks_results(self, audit, result: str, ok: bool, kind) -> None:
        registry = ToolRegistry(policy=ToolPolicy(), audit=audit)

        async def handler() -> str:
            return result

        registry.register(FunctionTool(ToolSpec(
            "shaped", "A tool with a declared result.",
            output_schema={"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]},
        ), handler))
        out, report = await registry.execute(
            ToolCall(ToolCallId("c1"), "shaped", {}), ToolContext(user_id=UserId("u"), correlation_id="c", deadline_s=10.0)
        )
        assert out.ok is ok and out.error_kind == kind and report.ok is ok
        if not ok:
            assert result not in out.content or result == "not json"

    async def test_tools_without_a_schema_are_unchanged(self, audit) -> None:
        registry = ToolRegistry(policy=ToolPolicy(), audit=audit)

        async def handler() -> str:
            return "free text"

        registry.register(FunctionTool(ToolSpec("plain", "Free text."), handler))
        out, _ = await registry.execute(ToolCall(ToolCallId("c1"), "plain", {}), ToolContext(user_id=UserId("u"), correlation_id="c", deadline_s=10.0))
        assert out.ok and out.content == "free text"

    async def test_mcp_output_schemas_are_mapped_when_supported(self) -> None:
        class Client:
            async def list_tools(self):
                return [
                    McpToolDescriptor("srv", "good", "d", {}, {"type": "object", "properties": {"x": {"type": "string"}}}),
                    McpToolDescriptor("srv", "fancy", "d", {}, {"type": "object", "oneOf": []}),
                    McpToolDescriptor("srv", "none", "d", {}),
                ]

        tools = {t.spec.name: t.spec for t in await build_mcp_bridge_tools(Client())}
        assert tools["mcp_srv_good"].output_schema["type"] == "object"
        assert tools["mcp_srv_fancy"].output_schema is None  # outside the subset: dropped, not trusted
        assert tools["mcp_srv_none"].output_schema is None


# ------------------------------------------------------------------------------- templates


def _search_template() -> ReadTemplate:
    return ReadTemplate("search_people", "hr-db", TemplateKind.SQL, sql=SqlSelect(
        "public", 'odd"name', columns=(SqlColumn("full_name", "full_name"), SqlColumn("id", "id")),
        filters=(SqlFilter("status", FilterOp.EQ, "status", optional=True),),
        any_of=(SqlFilter("full_name", FilterOp.CONTAINS, "query"), SqlFilter("email", FilterOp.CONTAINS, "query")),
        order_by=("id",), max_rows=50,
    ))


HOSTILE = [
    "'; DROP TABLE employees; --", '" OR 1=1 --', "$1", "%_%", "\\x00", "名前", "a" * 300, "../../etc/passwd",
    "?admin=true#x", "/x/y", 0, -1, 10**12, None, True, 3.5,
]


class TestTemplates:
    def test_sql_is_quoted_bound_and_bounded(self) -> None:
        rendered = render_sql(_search_template(), {"query": "ann", "limit": 5})
        assert rendered.text == (
            'SELECT "full_name" AS "full_name", "id" AS "id" FROM "public"."odd""name" '
            'WHERE ("status" = $1 OR $1 IS NULL) AND (strpos(lower("full_name"::text), lower($2::text)) > 0 '
            'OR strpos(lower("email"::text), lower($2::text)) > 0) ORDER BY "id" '
            "LIMIT LEAST(COALESCE($3::int, 50), 50)"
        )
        assert rendered.values == (None, "ann", 5)

    def test_fuzzed_arguments_never_change_the_statement(self) -> None:
        template = _search_template()
        baseline = render_sql(template, {}).text
        rng = random.Random(6)
        for _ in range(500):
            args = {name: rng.choice(HOSTILE) for name in ("status", "query", "limit") if rng.random() < 0.8}
            rendered = render_sql(template, args)
            assert rendered.text == baseline
            assert rendered.values == tuple(args.get(n) for n in ("status", "query", "limit"))

    def test_fuzzed_arguments_never_change_the_request_path(self) -> None:
        template = ReadTemplate("get_one", "hr-api", TemplateKind.HTTP, http=HttpGet(
            "/employees/{employeeId}/leave", path_params={"employeeId": "employee_id"}, query_params={"from": "since"},
        ))
        rng = random.Random(7)
        for _ in range(500):
            value, since = rng.choice([v for v in HOSTILE if v is not None]), rng.choice(HOSTILE)
            target = render_http(template, {"employee_id": value, "since": since})
            path, _, query = target.partition("?")
            segments = path.split("/")
            assert segments[:2] == ["", "employees"] and segments[3] == "leave" and len(segments) == 4
            assert ".." not in segments and "#" not in target
            assert (query.startswith("from=") and query.count("&") == 0) if since is not None else query == ""

    def test_http_templates_refuse_unsafe_paths(self) -> None:
        for path in ("employees", "/a/../b", "/a?x=1", "/a#f", "/a/x{id}", "/a b"):
            with pytest.raises(ValidationError):
                HttpGet(path)
        with pytest.raises(ValidationError):
            HttpGet("/a/{id}")  # placeholder without a parameter
        template = ReadTemplate("t", "c", TemplateKind.HTTP, http=HttpGet("/a/{id}", path_params={"id": "id"}))
        with pytest.raises(ValidationError, match="missing"):
            render_http(template, {})

    def test_template_shape_rules(self) -> None:
        with pytest.raises(ValidationError):
            ReadTemplate("t", "c", TemplateKind.SQL, http=HttpGet("/x"))
        with pytest.raises(ValidationError):
            SqlSelect("s", "t")  # nothing selected
        with pytest.raises(ValidationError):
            SqlSelect("s", "t", columns=(SqlColumn("a", "a"),), max_rows=10_000)
        with pytest.raises(ValidationError):
            SqlColumn("bad\x00name", "x")


# ------------------------------------------------------------------------------- compiler


@pytest.mark.parametrize("pilot", ["hr", "ecommerce"])
def test_committed_blueprint_golden_matches(pilot: str) -> None:
    committed = (PILOTS / pilot / "golden" / "blueprint.json").read_text(encoding="utf-8")
    assert committed == GOLDENS.render_blueprint(pilot), "run `python aura_backend/scripts/pilot_twin_goldens.py`"


@pytest.mark.parametrize("pilot", ["hr", "ecommerce"])
def test_both_pilots_compile_schema_valid_blueprints(pilot: str, compiled) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    result = compiled[pilot]
    data = to_data(result.blueprint)
    jsonschema.validate(data, json.loads(SCHEMA.read_text()))
    decoded = from_data(Blueprint, data)
    assert decoded == result.blueprint  # strict decode round-trips
    # ...and so does the digest approvals bind to. Equality alone would miss an int stored in a
    # float field (1 == 1.0, but "1" != "1.0" in canonical JSON) -- a bug this caught once.
    assert decoded.digest == result.blueprint.digest
    check_against_twin(result.blueprint, GOLDENS.twin_for(pilot))
    assert result.blueprint.tools and result.blueprint.agents and result.suites


def test_compilation_is_deterministic(compiled) -> None:
    again = GOLDENS.compile_pilot("hr")
    assert again.blueprint.digest == compiled["hr"].blueprint.digest and again.dropped == compiled["hr"].dropped


def test_an_enveloped_api_response_satisfies_its_compiled_output_schema() -> None:
    """Responses wrapped as ``{data, meta}`` with nested ``$ref`` and nullable 3.1 members are
    the common API shape. Before nested properties carried a JSON type they compiled as
    ``string``, and the registry rejected every real response as ``invalid_output``."""
    from aura_core.adaptation.twin import build_twin
    from aura_core.adapters.connectors.openapi import artifacts_from_openapi
    from aura_core.domain.blueprint import ConnectorRef
    from aura_core.domain.connectors import ConnectorKind, IdentityMode
    from aura_core.domain.discovery import DiscoveryBudget

    spec = {
        "openapi": "3.1.0", "info": {"title": "Experiments", "version": "1"},
        "paths": {"/experiments/{experiment_id}": {"get": {
            "operationId": "getExperiment",
            "parameters": [{"name": "experiment_id", "in": "path", "required": True, "schema": {"type": "string"}}],
            "responses": {"200": {"description": "ok", "content": {"application/json": {
                "schema": {"$ref": "#/components/schemas/ExperimentResponse"}}}}},
        }}},
        "components": {"schemas": {
            "ExperimentResponse": {"type": "object", "required": ["experiment", "meta"], "properties": {
                "experiment": {"$ref": "#/components/schemas/Experiment"},
                "meta": {"allOf": [{"$ref": "#/components/schemas/Meta"}]},
                "warning": {"anyOf": [{"$ref": "#/components/schemas/Meta"}, {"type": "null"}]},
                "count": {"type": ["integer", "null"]},
            }},
            "Experiment": {"type": "object", "properties": {"experiment_id": {"type": "string"}}},
            "Meta": {"type": "object", "properties": {"request_id": {"type": "string"}}},
        }},
    }
    report = artifacts_from_openapi("lab-api", spec, DiscoveryBudget())
    twin = build_twin("p0", 1, [report]).twin
    result = compile_blueprint(twin, plan_capabilities(twin, CapabilityRegistry.from_directory()),
                               connectors=[ConnectorRef("lab-api", ConnectorKind.OPENAPI, IdentityMode.DELEGATED)],
                               version=1)
    (tool,) = [t for t in result.blueprint.tools if t.name == "api_get_experiment"]
    response = {"experiment": {"experiment_id": "01J"}, "meta": {"request_id": "r1"}, "warning": None, "count": 3}
    assert output_errors(dict(tool.output_schema), response) == []
    assert output_errors(dict(tool.output_schema), {**response, "meta": "r1"}) != []  # still typed, not open


class TestCompiledShape:
    def test_every_tool_is_a_bounded_read_with_matching_parameters(self, compiled) -> None:
        for result in compiled.values():
            templates = {t.id: t for t in result.blueprint.templates}
            for tool in result.blueprint.tools:
                template = templates[tool.template]
                assert {p.name for p in tool.parameters} == set(template.params())
                if template.kind is TemplateKind.SQL:
                    assert render_sql(template, {}).text.startswith("SELECT ")
                    assert "LIMIT" in render_sql(template, {}).text
                assert tool.sensitivity >= ToolSensitivity.PERSONAL
                assert tool.output_schema["type"] in ("object", "array")

    def test_output_is_inert_data(self, compiled) -> None:
        def walk(value: Any) -> None:
            if isinstance(value, dict):
                for item in value.values():
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)
            else:
                assert value is None or isinstance(value, (str, int, float, bool)), type(value)
                if isinstance(value, str):
                    lowered = value.lower()
                    assert not lowered.startswith(("select ", "insert ", "update ", "delete ", "drop ")), value[:60]
                    assert "lambda" not in lowered and "import " not in lowered and "def " not in lowered

        for result in compiled.values():
            walk(to_data(result.blueprint))
            walk(to_data(result.suites))

    def test_agents_have_explicit_grants_within_their_ceiling(self, compiled) -> None:
        for result in compiled.values():
            tools = {t.name: t for t in result.blueprint.tools}
            for agent in result.blueprint.agents:
                assert agent.tools and all(tools[n].sensitivity <= agent.risk_ceiling for n in agent.tools)
        hr = compiled["hr"].blueprint
        manager = next(a for a in hr.agents if a.id == "role_hr_manager")
        assert "lookup_assets" not in manager.tools  # hr_manager has no grant on assets

    def test_unbuildable_selections_are_dropped_with_a_reason(self, compiled) -> None:
        dropped = {(d.key, d.reason) for d in compiled["ecommerce"].dropped}
        assert ("relation.traverse@ecommerce-db:fk:shop.returns.returns_item_fk", "composite_key") in dropped
        assert ("notification.draft@ecommerce-db:table:shop.customers", "no_factory:notification.draft") in dropped
        selected = {s.key for s in compiled["ecommerce"].blueprint.capabilities}
        assert not selected & {d.key for d in compiled["ecommerce"].dropped}

    def test_workflows_compile_only_strong_transitions(self, compiled) -> None:
        (orders,) = compiled["ecommerce"].blueprint.workflows
        assert orders.state_attribute == "ecommerce-db:column:shop.orders.status"
        assert len(orders.states) == 6 and len(orders.transitions) == 5
        (leave,) = compiled["hr"].blueprint.workflows
        assert len(leave.transitions) == 1  # pending -> approved; the 0.25 guesses stay in the twin

    def test_views_bind_declared_fields_and_navigation_resolves(self, compiled) -> None:
        bp = compiled["hr"].blueprint
        assert {v.tool for v in bp.ui.views if v.tool} == {t.name for t in bp.tools}
        assert bp.ui.navigation[-1].view == "assistant"

    def test_generated_suites(self, compiled) -> None:
        suites = {s.name: s for s in compiled["hr"].suites}
        assert set(suites) == set(compiled["hr"].blueprint.evaluation.suites)
        assert len(suites["tool_contract"].cases) == len(compiled["hr"].blueprint.tools)
        assert all(c.should_refuse for c in suites["out_of_scope"].cases)
        (denied,) = suites["permission_denied"].cases
        assert denied.agent == "role_hr_manager" and denied.forbidden_tools
        with pytest.raises(ValidationError):
            EvalCaseSpec("x", "tool_contract", "a", UntrustedText("q"))  # no tool


class TestTwinAllowlist:
    def _tampered(self, compiled, change) -> None:
        bp = compiled["hr"].blueprint
        template = next(t for t in bp.templates if t.kind is TemplateKind.SQL and t.sql.columns)
        tampered = dataclasses.replace(bp, templates=tuple(change(t) if t.id == template.id else t for t in bp.templates))
        with pytest.raises(ValidationError):
            check_against_twin(tampered, GOLDENS.twin_for("hr"))

    def test_a_column_outside_the_twin_is_refused(self, compiled) -> None:
        self._tampered(compiled, lambda t: dataclasses.replace(t, sql=dataclasses.replace(
            t.sql, columns=t.sql.columns + (SqlColumn("password_hash", "password_hash"),))))

    def test_a_table_outside_the_twin_is_refused(self, compiled) -> None:
        self._tampered(compiled, lambda t: dataclasses.replace(t, sql=dataclasses.replace(t.sql, table="pg_authid")))

    def test_an_http_path_outside_the_twin_is_refused(self, compiled) -> None:
        bp = compiled["hr"].blueprint
        template = next(t for t in bp.templates if t.kind is TemplateKind.HTTP)
        forged = dataclasses.replace(template, http=HttpGet("/admin/users"))
        tool = next(t for t in bp.tools if t.template == template.id)
        tampered = dataclasses.replace(
            bp, templates=tuple(forged if t.id == template.id else t for t in bp.templates),
            tools=tuple(dataclasses.replace(x, parameters=()) if x.name == tool.name else x for x in bp.tools),
        )
        with pytest.raises(ValidationError):
            check_against_twin(tampered, GOLDENS.twin_for("hr"))

    def test_a_tool_cannot_take_parameters_its_template_does_not_bind(self, compiled) -> None:
        bp = compiled["hr"].blueprint
        tool = bp.tools[0]
        extra = tool.parameters + (ParameterSpec("sql", "string", "Free text."),)
        with pytest.raises(ValidationError, match="exactly its template"):
            dataclasses.replace(bp, tools=(dataclasses.replace(tool, parameters=extra),) + bp.tools[1:])


def test_a_plan_from_another_twin_is_refused(compiled) -> None:
    twin = GOLDENS.twin_for("hr")
    plan = plan_capabilities(GOLDENS.twin_for("ecommerce"), CapabilityRegistry.from_directory())
    with pytest.raises(ValidationError, match="different twin"):
        compile_blueprint(twin, plan, connectors=GOLDENS.connector_refs("hr"), version=1)


def test_every_source_needs_its_connector() -> None:
    twin = GOLDENS.twin_for("hr")
    plan = plan_capabilities(twin, CapabilityRegistry.from_directory())
    with pytest.raises(ValidationError, match="connector"):
        compile_blueprint(twin, plan, connectors=GOLDENS.connector_refs("hr")[:1], version=1)


# ------------------------------------------------------------------------------- service, storage


class _Records:
    def __init__(self, pilot: str) -> None:
        self._refs = GOLDENS.connector_refs(pilot)

    async def list(self, **_: Any):
        return [type("R", (), {"descriptor": ref})() for ref in self._refs]


def _reports(pilot: str) -> list[DiscoveryReport]:
    return [
        from_data(DiscoveryReport, json.loads((PILOTS / pilot / "golden" / f"discovery_{k}.json").read_text()))
        for k in ("postgres", "openapi")
    ]


async def test_compiler_service_appends_proposed_versions(audit, clock) -> None:
    twins = TwinService(project_id="p0", registry=None, discovery=None, store=InMemoryTwinStore(),
                        labeling=LabelingPass(None), clock=clock, audit=audit)
    planner = PlanningService(registry=CapabilityRegistry.from_directory(), twins=twins)
    store = InMemoryBlueprintStore()
    service = CompilerService(twins=twins, planner=planner, connectors=_Records("hr"), store=store,
                              clock=clock, audit=audit)
    await twins.snapshot_reports(_reports("hr"), by="sa@example.com")
    first = await service.compile(1, by="sa@example.com")
    second = await service.compile(1, by="sa@example.com")
    assert (first.version, second.version) == (1, 2) and first.state is BlueprintState.PROPOSED
    assert first.blueprint.tools == second.blueprint.tools  # same twin, same plan, same tools
    assert from_data(BlueprintRecord, to_data(first)) == first
    with pytest.raises(ConflictError):
        await store.append(first)
    (event, *_) = await audit.query(action="adaptation.blueprint.compile", limit=5)
    assert event.outcome == "proposed" and event.detail["tools"] == len(first.blueprint.tools)


def test_a_record_must_carry_exactly_its_declared_suites(compiled) -> None:
    result = compiled["hr"]
    with pytest.raises(ValidationError, match="suites"):
        BlueprintRecord("p0", 1, BlueprintState.PROPOSED, result.blueprint.digest, "2026-10-01T00:00:00+00:00",
                        "sa", "sha256:" + "0" * 64, "sha256:" + "0" * 64, result.blueprint, suites=result.suites[:1])


def test_postgres_schema_matches_its_migration() -> None:
    from aura_core.adapters.postgres_adaptation import BLUEPRINT_SCHEMA

    spec = importlib.util.spec_from_file_location("m0011", BACKEND / "alembic" / "versions" / "0011_adaptation_blueprints.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert tuple(module.UPGRADE_STATEMENTS) == BLUEPRINT_SCHEMA


def test_super_admin_compiles_and_reads_a_blueprint(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from aura_core.api import Principal, build_app
    from aura_core.config.settings import load_settings
    from aura_core.container import Container

    monkeypatch.setenv("AURA_ADAPTATION_ENABLED", "true")
    container = Container(load_settings(refresh=True, validate=False))
    reports = {r.source_id: r for r in _reports("hr")}

    class Discovery:
        async def discover(self, connector_id, *, budget, by):
            return reports[connector_id]

    container.twins._discovery = Discovery()
    container.blueprint_compiler._connectors = _Records("hr")

    async def authenticator(request):
        return Principal(user_id=UserId("op-1"), email="op@example.com", role="SuperAdmin", capabilities=())

    with TestClient(build_app(container, authenticator=authenticator)) as client:
        assert client.post("/api/v3/admin/twin/versions/1/compile").status_code == 404
        client.post("/api/v3/admin/twin/snapshot", json={"connector_ids": ["hr-db", "hr-api"]})
        created = client.post("/api/v3/admin/twin/versions/1/compile")
        assert created.status_code == 201, created.text
        summary = created.json()["summary"]
        assert summary["state"] == "proposed" and summary["tools"] > 0
        listed = client.get("/api/v3/admin/blueprints").json()["blueprints"]
        assert [b["version"] for b in listed] == [1]
        full = client.get("/api/v3/admin/blueprints/1").json()["record"]
        assert full["blueprint"]["templates"] and full["suites"]
        assert client.get("/api/v3/admin/blueprints/9").status_code == 404


# ----------------------------------------------------------- AR-2: optional query parameters


def _search_spec(extra: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """An OpenAPI 3.1 search operation shaped like Flame in Freefall's ``GET /api/search``."""
    params = [
        {"name": "q", "in": "query", "required": True, "schema": {"type": "string", "maxLength": 500}},
        {"name": "type", "in": "query", "schema": {"type": "string", "enum": ["document", "experiment"]}},
        {"name": "domain", "in": "query", "schema": {"type": "string", "enum": ["combustion", "all"],
                                                     "default": "combustion"}},
        {"name": "source_id", "in": "query", "style": "form", "explode": True,
         "schema": {"type": "array", "items": {"type": "string"}, "maxItems": 20}},
        {"name": "page_size", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20}},
        {"name": "exact", "in": "query", "schema": {"type": "boolean"}},
        {"name": "filter", "in": "query", "schema": {"type": "object"}},          # no query-string form
        {"name": "ids", "in": "query", "explode": False,                           # comma style: unsupported
         "schema": {"type": "array", "items": {"type": "string"}}},
        *(extra or []),
    ]
    return {
        "openapi": "3.1.0", "info": {"title": "Search", "version": "1"},
        "paths": {"/api/search": {"get": {
            "operationId": "search", "parameters": params,
            "responses": {"200": {"description": "ok", "content": {"application/json": {
                "schema": {"$ref": "#/components/schemas/SearchResponse"}}}}},
        }}},
        "components": {"schemas": {"SearchResponse": {"type": "object", "properties": {
            "data": {"type": "array", "items": {"type": "object"}},
        }}}},
    }


def _compile_search(spec: dict[str, Any]):
    from aura_core.adaptation.twin import build_twin
    from aura_core.adapters.connectors.openapi import artifacts_from_openapi
    from aura_core.domain.blueprint import ConnectorRef
    from aura_core.domain.connectors import ConnectorKind, IdentityMode
    from aura_core.domain.discovery import DiscoveryBudget

    report = artifacts_from_openapi("lab-api", spec, DiscoveryBudget())
    twin = build_twin("p0", 1, [report]).twin
    result = compile_blueprint(twin, plan_capabilities(twin, CapabilityRegistry.from_directory()),
                               connectors=[ConnectorRef("lab-api", ConnectorKind.OPENAPI, IdentityMode.DELEGATED)],
                               version=1)
    (tool,) = [t for t in result.blueprint.tools if t.name == "api_search"]
    template = next(t for t in result.blueprint.templates if t.id == tool.template)
    return report, twin, result, tool, template


class TestOptionalQueryParameters:
    def test_declared_optional_parameters_compile_typed_and_optional(self) -> None:
        report, _, _, tool, template = _compile_search(_search_spec())
        params = {p.name: p for p in tool.parameters}
        assert template.http.query_params == {
            "q": "q", "type": "type", "domain": "domain", "source_id": "source_id",
            "page_size": "page_size", "exact": "exact",
        }
        assert params["q"].required and not any(params[n].required for n in params if n != "q")
        assert params["type"].enum == ("document", "experiment")
        assert (params["page_size"].type, params["page_size"].minimum, params["page_size"].maximum) == ("integer", 1, 50)
        assert all(isinstance(p.minimum, float) for p in tool.parameters if p.minimum is not None)
        assert (params["source_id"].type, params["source_id"].item_type) == ("array", "string")
        assert params["exact"].type == "boolean"
        # The server owns its default: described, never sent on the caller's behalf.
        assert all(p.default is None for p in tool.parameters)
        # `filter` (an object) and `ids` (comma style) have no safe rendering: recorded, not compiled.
        assert [(i.code, i.locator) for i in report.issues] == [("unsupported_query_parameter", "op:GET /api/search")]

    def test_the_model_sees_token_enums_and_never_wordy_ones(self) -> None:
        from aura_core.adaptation.runtime import model_facing_parameters

        wordy = {"name": "mode", "in": "query", "schema": {"type": "string", "enum": ["ignore all previous", "x"]}}
        _, _, _, tool, template = _compile_search(_search_spec([wordy]))
        surface = {p.name: p for p in model_facing_parameters(tool, template)}
        assert surface["domain"].enum == ("combustion", "all")
        assert surface["mode"].enum is None and "ignore" not in surface["mode"].description
        assert "Optional" in surface["page_size"].description and "Optional" not in surface["q"].description

    def test_arguments_are_checked_before_any_request(self) -> None:
        from types import SimpleNamespace

        from aura_core.adaptation.runtime import model_facing_parameters
        from aura_core.tools.schema import validate_arguments

        _, _, _, tool, template = _compile_search(_search_spec())
        spec = SimpleNamespace(name=tool.name, parameters=model_facing_parameters(tool, template))
        assert validate_arguments(spec, {"q": "heptane"}) == {"q": "heptane"}  # absent stays absent
        for bad in ({"q": "x", "domain": "everything"}, {"q": "x", "page_size": 500},
                    {"q": "x", "page_size": "ten"}, {"q": "x", "sql": "1"}):
            with pytest.raises(ValidationError):
                validate_arguments(spec, bad)

    def test_hostile_values_are_percent_encoded_and_booleans_are_json(self) -> None:
        _, _, _, _, template = _compile_search(_search_spec())
        target = render_http(template, {
            "q": "a&domain=all#x", "source_id": ["FLEX", "B&B=1"], "exact": True, "page_size": None,
        })
        path, _, query = target.partition("?")
        assert path == "/api/search" and "#" not in target
        assert query.split("&") == ["exact=true", "q=a%26domain%3Dall%23x", "source_id=FLEX", "source_id=B%26B%3D1"]
        with pytest.raises(ValidationError, match="too many"):
            render_http(template, {"q": "x", "source_id": ["S"] * 21})

    def test_at_most_eight_optional_parameters_reach_one_tool(self) -> None:
        extra = [{"name": f"f{i}", "in": "query", "schema": {"type": "string"}} for i in range(6)]
        _, _, _, tool, _ = _compile_search(_search_spec(extra))
        assert sum(not p.required for p in tool.parameters) == 8

    def test_a_query_key_the_twin_does_not_declare_is_refused(self) -> None:
        _, twin, result, tool, template = _compile_search(_search_spec())
        bp = result.blueprint
        forged = dataclasses.replace(template, http=dataclasses.replace(
            template.http, query_params={**template.http.query_params, "admin": "admin"}))
        tampered = dataclasses.replace(
            bp, templates=tuple(forged if t.id == template.id else t for t in bp.templates),
            tools=tuple(dataclasses.replace(t, parameters=t.parameters + (ParameterSpec("admin", "string", "x", required=False),))
                        if t.name == tool.name else t for t in bp.tools),
        )
        with pytest.raises(ValidationError, match="not declared"):
            check_against_twin(tampered, twin)
