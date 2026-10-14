"""AP10: the Git connector -- what a repository's code declares, as twin evidence.

Three kinds of proof, none needing a database, a network or a model:

* **Parity with the live catalog.** Each pilot's ``schema.sql`` is read as code and compared with
  ``golden/catalog.json`` -- the catalog recorded from that same DDL applied to PostgreSQL 16. Every
  relation, column (type, nullability, default, comment), constraint (by PostgreSQL's own names),
  enum and grant must agree.
* **Goldens.** Each pilot's ``repo/`` -- an HR app in SQLAlchemy + Alembic + FastAPI, a shop in
  Django + Flask with an OpenAPI file -- renders exactly the committed ``golden/discovery_git.json``.
* **The twin uses code as evidence, never as a source of tools.** Adding the repository to the
  pilot's database twin proposes ``same_as`` matches but leaves the capability plan unchanged.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
from pathlib import Path

import pytest

from aura_core.adaptation.compiler import compile_blueprint
from aura_core.adaptation.normalize import CODE_CONFIDENCE
from aura_core.adaptation.planner import CapabilityRegistry, plan_capabilities
from aura_core.adaptation.twin import build_twin
from aura_core.adapters.connectors.code_python import read_python_models, routes
from aura_core.adapters.connectors.code_schema import SchemaModel, read_sql
from aura_core.adapters.connectors.git import GitConnectorFactory, scan_repository
from aura_core.adapters.connectors.postgres_catalog import artifacts_from_catalog
from aura_core.domain.ausm import ElementKind
from aura_core.domain.blueprint import ConnectorRef
from aura_core.domain.connectors import ConnectorDescriptor, ConnectorKind, HealthStatus, IdentityMode
from aura_core.domain.discovery import ArtifactKind, DiscoveryBudget
from aura_core.domain.errors import DependencyUnavailableError
from aura_core.domain.untrusted import UntrustedText

BACKEND = Path(__file__).resolve().parents[2]
PILOTS = BACKEND / "tests" / "fixtures" / "pilots"


def _goldens():
    spec = importlib.util.spec_from_file_location("pilot_goldens", BACKEND / "scripts" / "pilot_discovery_goldens.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GOLDENS = _goldens()


def _catalog(pilot: str) -> dict:
    return json.loads((PILOTS / pilot / "golden" / "catalog.json").read_text("utf-8"))


def _sql(text: str) -> dict:
    model = SchemaModel()
    read_sql(model, text, path="schema.sql")
    return model.snapshot()


def _descriptor(endpoint: str = "app") -> ConnectorDescriptor:
    return ConnectorDescriptor(id="hr-repo", kind=ConnectorKind.GIT, identity_mode=IdentityMode.SERVICE_ACCOUNT,
                               display_name="HR app", endpoint=endpoint)


# ------------------------------------------------------------------------------- SQL


class TestSqlParity:
    """A repository's DDL, read as text, equals what PostgreSQL itself catalogued from it."""

    @pytest.mark.parametrize("pilot", ["hr", "ecommerce"])
    def test_schema_sql_reads_exactly_as_the_live_catalog(self, pilot) -> None:
        mine = _sql((PILOTS / pilot / "schema.sql").read_text("utf-8"))
        live = _catalog(pilot)
        assert mine["issues"] == []

        def relations(s):
            return sorted((r["schema"], r["name"], r["kind"], r["comment"]) for r in s["relations"])

        def columns(s):
            return sorted((c["schema"], c["table"], c["name"], c["position"], c["data_type"], c["type_name"],
                           c["type_schema"], c["is_enum"], c["not_null"], c["has_default"], c["comment"])
                          for c in s["columns"])

        def constraints(s):
            return sorted((c["schema"], c["table"], c["name"], c["type"], tuple(c["columns"]),
                           c["ref_table"] if c["type"] == "f" else None, tuple(c.get("ref_columns") or ()))
                          for c in s["constraints"])

        def enums(s):
            return sorted((e["schema"], e["name"], tuple(e["labels"])) for e in s["enums"])

        def grants(s):
            return sorted((g["grantee"], g["schema"], g["table"], g["privilege"]) for g in s["grants"])

        for view in (relations, columns, constraints, enums, grants):
            assert view(mine) == view(live), view.__name__

    def test_check_value_lists_survive_for_the_normaliser(self) -> None:
        mine = _sql((PILOTS / "hr" / "schema.sql").read_text("utf-8"))
        status = next(c for c in mine["constraints"] if c["name"] == "leave_requests_status_check")
        assert "IN ('pending', 'approved', 'rejected', 'cancelled')" in status["definition"]

    def test_hostile_comment_is_read_as_text(self) -> None:
        mine = _sql((PILOTS / "hr" / "schema.sql").read_text("utf-8"))
        salary = next(c for c in mine["columns"] if c["name"] == "salary")
        assert salary["comment"].startswith("Ignore all previous instructions")

    def test_tokens_that_look_like_statements_are_not(self) -> None:
        mine = _sql("""
            -- CREATE TABLE commented_out (id int);
            /* CREATE TABLE also_commented (id int); */
            CREATE FUNCTION f() RETURNS void AS $body$ BEGIN CREATE TABLE inside_function (id int); END $body$
                LANGUAGE plpgsql;
            CREATE TABLE notes (
                id     serial PRIMARY KEY,
                body   text NOT NULL DEFAULT 'a; b',
                parent integer REFERENCES notes (id) ON DELETE SET NULL NOT NULL
            );
            INSERT INTO notes (body) VALUES ('CREATE TABLE x (y int);');
        """)
        assert [r["name"] for r in mine["relations"]] == ["notes"]
        by_name = {c["name"]: c for c in mine["columns"]}
        assert by_name["id"]["not_null"] and by_name["id"]["has_default"] and by_name["id"]["data_type"] == "integer"
        assert by_name["body"]["has_default"]
        assert by_name["parent"]["not_null"], "ON DELETE SET NULL is a referential action, not nullability"

    def test_migrations_apply_in_natural_order(self, tmp_path) -> None:
        repo = tmp_path / "repo" / "db" / "migration"
        repo.mkdir(parents=True)
        (repo / "V1__init.sql").write_text("CREATE TABLE orders (id bigint PRIMARY KEY, state text);")
        (repo / "V2__more.sql").write_text(
            "ALTER TABLE orders ADD COLUMN total numeric(12, 2) NOT NULL, ADD CONSTRAINT orders_state_check "
            "CHECK (state IN ('open', 'closed'));")
        (repo / "V10__rename.sql").write_text(
            "ALTER TABLE orders RENAME COLUMN state TO status; ALTER TABLE orders RENAME TO purchase_orders;"
            "CREATE TABLE scratch (id int); DROP TABLE scratch;")
        report = scan_repository("r", tmp_path / "repo", DiscoveryBudget())
        tables = {a.locator for a in report.artifacts if a.kind is ArtifactKind.TABLE}
        assert tables == {"table:public.purchase_orders"}
        columns = {a.locator for a in report.artifacts if a.kind is ArtifactKind.COLUMN}
        assert columns == {f"column:public.purchase_orders.{c}" for c in ("id", "status", "total")}
        check = next(a for a in report.artifacts if a.kind is ArtifactKind.CHECK)
        assert check.attributes["columns"] == ("status",)


# ------------------------------------------------------------------------------- Python


def _code_tables(report) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for a in report.artifacts:
        if a.kind is ArtifactKind.TABLE:
            out[a.name.raw()] = dict(a.attributes)
    return out


class TestPythonReaders:
    def test_hr_app_declares_the_databases_tables_and_keys(self) -> None:
        report = scan_repository("hr-repo", PILOTS / "hr" / "repo", DiscoveryBudget())
        live = _catalog("hr")
        base = {r["name"] for r in live["relations"] if r["kind"] == "r"}
        assert set(_code_tables(report)) == base
        code_columns = {(a.parent.split(".")[-1], a.name.raw()) for a in report.artifacts if a.kind is ArtifactKind.COLUMN}
        assert code_columns == {(c["table"], c["name"]) for c in live["columns"] if c["table"] in base}

        def keys(rows):
            return {(r[0], r[1], tuple(r[2]), r[3]) for r in rows}

        code_keys = keys((a.parent.split(".")[-1], a.kind.value, a.attributes["columns"],
                          a.attributes.get("ref_table", "").split(".")[-1] or None)
                         for a in report.artifacts if a.kind in (ArtifactKind.PRIMARY_KEY, ArtifactKind.FOREIGN_KEY,
                                                                  ArtifactKind.UNIQUE))
        kinds = {"p": "primary_key", "f": "foreign_key", "u": "unique"}
        live_keys = keys((c["table"], kinds[c["type"]], c["columns"], c["ref_table"] if c["type"] == "f" else None)
                         for c in live["constraints"] if c["type"] in kinds)
        assert code_keys == live_keys

    def test_migrations_win_over_models_and_follow_the_revision_chain(self) -> None:
        tables = _code_tables(scan_repository("hr-repo", PILOTS / "hr" / "repo", DiscoveryBudget()))
        assert tables["employees"]["declared_in"] == "migration"
        assert tables["employees"]["file"] == "migrations/versions/0001_initial.py"
        assert tables["assets"]["declared_in"] == "orm"
        # 0000_add_salary.py sorts first but depends on 0001: applied in down_revision order.
        report = scan_repository("hr-repo", PILOTS / "hr" / "repo", DiscoveryBudget())
        assert any(a.locator == "column:public.employees.salary" for a in report.artifacts)

    def test_code_and_database_drift_is_kept_not_smoothed(self) -> None:
        report = scan_repository("hr-repo", PILOTS / "hr" / "repo", DiscoveryBudget())
        types = {a.locator: a.attributes["data_type"] for a in report.artifacts if a.kind is ArtifactKind.COLUMN}
        assert types["column:public.assets.tag"] == "character varying(32)"  # the database has text
        assert types["column:public.attendance.id"] == "integer"  # the database has bigint

    def test_django_conventions(self) -> None:
        report = scan_repository("shop-repo", PILOTS / "ecommerce" / "repo", DiscoveryBudget())
        tables = _code_tables(report)
        assert set(tables) == {"customers", "orders", "products", "shipments", "shop_payment"}
        locators = {a.locator: a for a in report.artifacts}
        assert locators["pk:public.customers.customers_pkey"].attributes["columns"] == ("id",)
        assert locators["pk:public.products.products_pkey"].attributes["columns"] == ("sku",)
        assert locators["column:public.orders.customer_id"].attributes["data_type"] == "bigint"
        assert locators["fk:public.shop_payment.shop_payment_order_id_fkey"].attributes["ref_table"] == "table:public.orders"
        assert locators["unique:public.shipments.shipments_order_id_key"].attributes["columns"] == ("order_id",)
        assert "'returned'" in locators["check:public.orders.orders_status_check"].attributes["definition"]
        assert not any("abstract" in loc or "timestampedmodel" in loc for loc in locators)

    def test_nothing_in_the_repository_is_executed(self, tmp_path) -> None:
        canary = tmp_path / "executed"
        (tmp_path / "repo").mkdir()
        (tmp_path / "repo" / "models.py").write_text(
            f"open({str(canary)!r}, 'w').write('ran')\n"
            "import os\n"
            "class Computed:\n"
            "    __tablename__ = os.environ['TABLE']\n"
            "class Literal:\n"
            "    __tablename__ = 'kept'\n"
            f"    id = Column(Integer, primary_key=True, default=open({str(canary)!r}, 'w'))\n")
        report = scan_repository("r", tmp_path / "repo", DiscoveryBudget())
        assert not canary.exists()
        assert set(_code_tables(report)) == {"kept"}

    def test_unparseable_python_is_an_issue(self) -> None:
        model = SchemaModel()
        read_python_models(model, "class Broken(:\n    __tablename__ = 'x'\n", path="app/models.py", app_label="app")
        assert model.issues == [{"code": "unparseable", "locator": "file:app/models.py"}]


class TestScanBoundaries:
    def test_tests_dependencies_and_symlinks_are_not_read(self, tmp_path, tmp_path_factory) -> None:
        repo = tmp_path / "repo"
        shutil.copytree(PILOTS / "hr" / "repo", repo)
        (repo / "node_modules" / "pkg").mkdir(parents=True)
        (repo / "node_modules" / "pkg" / "models.py").write_text("class V:\n    __tablename__ = 'vendored'\n")
        outside = tmp_path_factory.mktemp("outside") / "secret.sql"
        outside.write_text("CREATE TABLE secret_table (id int);")
        report_issues = []
        if hasattr(os, "symlink"):
            (repo / "leak.sql").symlink_to(outside)
        report = scan_repository("r", repo, DiscoveryBudget())
        report_issues = {(i.code, i.locator) for i in report.issues}
        names = set(_code_tables(report))
        assert "scratch_should_not_appear" not in names and "vendored" not in names and "secret_table" not in names
        if hasattr(os, "symlink"):
            assert ("symlink_skipped", "file:leak.sql") in report_issues

    @pytest.mark.parametrize("endpoint", ["../outside", "/etc", "missing"])
    async def test_an_endpoint_outside_the_root_is_refused(self, tmp_path, endpoint) -> None:
        (tmp_path / "root").mkdir()
        (tmp_path / "outside").mkdir()
        connector = await GitConnectorFactory(root=str(tmp_path / "root")).open(_descriptor(endpoint), None)
        assert (await connector.health()).reason == "invalid_endpoint"
        with pytest.raises(DependencyUnavailableError):
            await connector.discover(DiscoveryBudget())

    async def test_no_root_means_the_kind_is_off_and_a_checkout_is_never_write_capable(self, tmp_path) -> None:
        assert not GitConnectorFactory(root=None).available
        assert (await (await GitConnectorFactory(root=None).open(_descriptor(), None)).health()).reason == "git_disabled"
        shutil.copytree(PILOTS / "hr" / "repo", tmp_path / "app")
        connector = await GitConnectorFactory(root=str(tmp_path)).open(_descriptor("app"), None)
        health = await connector.health()
        assert health.status is HealthStatus.HEALTHY and health.write_capable is False
        assert (await connector.discover(DiscoveryBudget())).source_id == "hr-repo"

    def test_budget_truncates_honestly(self) -> None:
        report = scan_repository("hr-repo", PILOTS / "hr" / "repo", DiscoveryBudget(max_objects=10))
        assert report.truncated and len(report.artifacts) <= 10


# ------------------------------------------------------------------------------- routes


class TestRoutes:
    def test_fastapi_prefixes_and_flask_converters(self) -> None:
        fastapi = routes((PILOTS / "hr" / "repo" / "app" / "api.py").read_text(), path="app/api.py")
        assert {(r.method, r.path) for r in fastapi} == {
            ("GET", "/employees"), ("GET", "/employees/{employee_id}"), ("POST", "/employees"),
            ("GET", "/leave-requests"), ("PATCH", "/leave-requests/{request_id}/approve"),
        }
        flask = routes((PILOTS / "ecommerce" / "repo" / "shop" / "views.py").read_text(), path="shop/views.py")
        assert {(r.method, r.path) for r in flask} == {
            ("GET", "/shop/customers"), ("GET", "/shop/orders"), ("POST", "/shop/orders"),
            ("GET", "/shop/orders/{order_id}"), ("POST", "/shop/orders/{order_id}/cancel"),
        }

    def test_an_api_description_wins_over_a_route_for_the_same_operation(self) -> None:
        report = scan_repository("shop-repo", PILOTS / "ecommerce" / "repo", DiscoveryBudget())
        ops = {a.locator: a for a in report.artifacts if a.kind is ArtifactKind.OPERATION}
        assert ops["op:GET /shop/customers"].extractor == "code_openapi"
        assert ops["op:GET /shop/orders"].extractor == "code_routes"
        assert ops["op:GET /shop/orders/{order_id}"].attributes["required_parameters"] == ("path:order_id",)

    def test_a_hostile_docstring_is_untrusted_text(self) -> None:
        report = scan_repository("hr-repo", PILOTS / "hr" / "repo", DiscoveryBudget())
        op = next(a for a in report.artifacts if a.locator == "op:GET /employees/{employee_id}")
        assert isinstance(op.description, UntrustedText)
        assert "Ignore previous instructions" in op.description.raw()


# ------------------------------------------------------------------------------- goldens


@pytest.mark.parametrize("pilot", ["hr", "ecommerce"])
def test_committed_git_golden_matches_the_scan(pilot) -> None:
    committed = (PILOTS / pilot / "golden" / "discovery_git.json").read_text("utf-8")
    assert GOLDENS.render_git(pilot) == committed, (
        f"{pilot}/golden/discovery_git.json is stale -- run `python aura_backend/scripts/pilot_discovery_goldens.py`")


# ------------------------------------------------------------------------------- twin and plan


def _db_report(pilot: str):
    return artifacts_from_catalog(f"{pilot}-db", _catalog(pilot), DiscoveryBudget())


class TestTwinAndPlan:
    def test_code_is_evidence_below_full_confidence_from_a_source_nothing_reads(self) -> None:
        twin = build_twin("p0", 1, [_db_report("hr"), scan_repository("hr-repo", PILOTS / "hr" / "repo",
                                                                       DiscoveryBudget())]).twin
        repo_source = next(e for e in twin.of_kind(ElementKind.SOURCE) if e.source_id == "hr-repo")
        db_source = next(e for e in twin.of_kind(ElementKind.SOURCE) if e.source_id == "hr-db")
        assert repo_source.properties["queryable"] is False and "queryable" not in db_source.properties
        code = [e for e in twin.elements if e.source_id == "hr-repo" and e.kind is not ElementKind.SOURCE]
        assert code and max(e.confidence for e in code) <= CODE_CONFIDENCE
        assert all(e.confidence == 1.0 for e in twin.elements
                   if e.source_id == "hr-db" and e.kind in (ElementKind.ENTITY, ElementKind.ATTRIBUTE))

    def test_every_table_is_proposed_as_the_same_as_its_model(self) -> None:
        twin = build_twin("p0", 1, [_db_report("hr"), scan_repository("hr-repo", PILOTS / "hr" / "repo",
                                                                       DiscoveryBudget())]).twin
        pairs = {tuple(sorted((r.refs["from"], r.refs["to"]))) for r in twin.of_kind(ElementKind.RELATION)
                 if r.properties.get("relation_type") == "same_as"}
        for table in ("departments", "employees", "leave_requests", "attendance", "assets"):
            assert (f"hr-db:table:public.{table}", f"hr-repo:table:public.{table}") in pairs, table

    def test_routes_attach_to_the_entities_they_serve(self) -> None:
        twin = build_twin("p0", 1, [scan_repository("hr-repo", PILOTS / "hr" / "repo", DiscoveryBudget())]).twin
        action = next(e for e in twin.of_kind(ElementKind.ACTION) if e.properties.get("path") == "/employees/{employee_id}")
        assert action.refs["entity"] == "hr-repo:table:public.employees"

    @pytest.mark.parametrize("pilot", ["hr", "ecommerce"])
    def test_the_repository_is_never_a_target_and_takes_nothing_away(self, pilot) -> None:
        registry = CapabilityRegistry.from_directory()
        db_only = build_twin("p0", 1, [_db_report(pilot)]).twin
        with_code = build_twin("p0", 1, [_db_report(pilot), scan_repository(f"{pilot}-repo", PILOTS / pilot / "repo",
                                                                            DiscoveryBudget())]).twin
        before, after = plan_capabilities(db_only, registry), plan_capabilities(with_code, registry)
        assert all(s.target.startswith(f"{pilot}-db:") for s in after.selected)
        assert {(s.capability, s.target) for s in before.selected} <= {(s.capability, s.target) for s in after.selected}
        assert not any(a.role and a.role.startswith(f"{pilot}-repo:") for a in after.agents)
        assert {a.id for a in after.agents} == {a.id for a in before.agents}

    def test_code_can_corroborate_a_database_process(self) -> None:
        """The one way code changes a plan: as evidence. The app's ``.../approve`` route, matched to
        the ``leave_requests`` table through ``same_as``, counts (at half weight) as a transition
        into ``approved`` -- enough to carry ``workflow.status`` on the *database* column."""
        registry = CapabilityRegistry.from_directory()
        twin = build_twin("p0", 1, [_db_report("hr"), scan_repository("hr-repo", PILOTS / "hr" / "repo",
                                                                       DiscoveryBudget())]).twin
        before = plan_capabilities(build_twin("p0", 1, [_db_report("hr")]).twin, registry)
        after = plan_capabilities(twin, registry)
        added = {(s.capability, s.target) for s in after.selected} - {(s.capability, s.target) for s in before.selected}
        assert added == {("workflow.status", "hr-db:column:public.leave_requests.status")}
        transition = next(e for e in twin.of_kind(ElementKind.WORKFLOW)
                          if e.locator.startswith("transition:") and "leave_requests" in e.locator
                          and e.locator.endswith("approved"))
        assert any(p.source_id == "hr-repo" for p in transition.provenance)

    def test_a_blueprint_compiles_with_the_repository_attached(self) -> None:
        twin = build_twin("p0", 1, [_db_report("hr"), scan_repository("hr-repo", PILOTS / "hr" / "repo",
                                                                       DiscoveryBudget())]).twin
        plan = plan_capabilities(twin, CapabilityRegistry.from_directory())
        compiled = compile_blueprint(twin, plan, version=1, connectors=[
            ConnectorRef("hr-db", ConnectorKind.POSTGRES, IdentityMode.SERVICE_ACCOUNT),
            ConnectorRef("hr-repo", ConnectorKind.GIT, IdentityMode.SERVICE_ACCOUNT)])
        bp = compiled.blueprint
        assert bp.templates and all(t.connector == "hr-db" for t in bp.templates)
        assert {c.id for c in bp.integrations} == {"hr-db", "hr-repo"}
