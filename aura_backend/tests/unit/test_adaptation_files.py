"""AP10: the Files connector -- CSV and JSON exports as tables, through the whole Track AP path.

The HR pilot's recorded rows are written out as CSV files, then discovered, normalised into a twin,
planned, compiled and evaluated by the AP8 gate with the Files connector as the live system. That
is the claim the connector makes: a spreadsheet export is attached exactly like a database table.

No database, no network: files in a temporary directory.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import os
from pathlib import Path

import pytest

from aura_core.adaptation.compiler import compile_blueprint
from aura_core.adaptation.gate import BlueprintGate
from aura_core.adaptation.planner import CapabilityRegistry, plan_capabilities
from aura_core.adaptation.twin import build_twin
from aura_core.adapters.connectors.files import FILES_SCHEMA, FilesConnectorFactory, resolve_directory
from aura_core.domain.blueprint import BlueprintState, ConnectorRef
from aura_core.domain.compilation import BlueprintRecord
from aura_core.domain.connectors import ConnectorDescriptor, ConnectorKind, HealthStatus, IdentityMode
from aura_core.domain.discovery import ArtifactKind, DiscoveryBudget
from aura_core.domain.errors import DependencyUnavailableError, ToolExecutionError
from aura_core.domain.templates import ReadTemplate, SqlColumn, SqlFilter, SqlSelect, FilterOp, TemplateKind
from aura_core.tools.policy import ToolPolicy
from aura_core.tools.registry import ToolRegistry

BACKEND = Path(__file__).resolve().parents[2]
ROWS = json.loads((BACKEND / "tests" / "fixtures" / "pilots" / "hr" / "golden" / "rows.json").read_text("utf-8"))


def _descriptor(endpoint: str = "hr-exports", connector_id: str = "hr-files") -> ConnectorDescriptor:
    return ConnectorDescriptor(id=connector_id, kind=ConnectorKind.FILES, identity_mode=IdentityMode.SERVICE_ACCOUNT,
                               display_name="HR exports", endpoint=endpoint)


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _write_csv(path: Path, rows: list[dict]) -> None:
    columns = list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([_cell(row[c]) for c in columns])


@pytest.fixture
def root(tmp_path: Path) -> Path:
    exports = tmp_path / "hr-exports"
    exports.mkdir()
    for key, rows in ROWS.items():
        _write_csv(exports / f"{key.split('.', 1)[1]}.csv", rows)
    return tmp_path


async def _discover(root: Path, budget: DiscoveryBudget | None = None):
    connector = await FilesConnectorFactory(root=str(root)).open(_descriptor(), None)
    return connector, await connector.discover(budget or DiscoveryBudget())


class TestDiscovery:
    async def test_each_file_is_a_table_with_inferred_types(self, root) -> None:
        _, report = await _discover(root)
        tables = {a.locator for a in report.artifacts if a.kind is ArtifactKind.TABLE}
        assert tables == {f"table:{FILES_SCHEMA}.{name}" for name in
                          ("assets", "attendance", "departments", "employees", "leave_requests")}
        types = {a.locator: a.attributes["data_type"] for a in report.artifacts if a.kind is ArtifactKind.COLUMN}
        assert types["column:files.employees.id"] == "integer"
        assert types["column:files.employees.salary"] == "numeric"
        assert types["column:files.employees.hired_on"] == "date"
        assert types["column:files.attendance.check_in"] == "timestamp with time zone"
        assert types["column:files.employees.email"] == "text"
        assert report.extractor == "files"
        assert {a.extractor for a in report.artifacts} == {"files_catalog", "files_profile"}

    async def test_keys_are_inferred_only_from_evidence(self, root) -> None:
        _, report = await _discover(root)
        pks = {a.parent: a.attributes["columns"] for a in report.artifacts if a.kind is ArtifactKind.PRIMARY_KEY}
        assert pks["table:files.employees"] == ("id",)
        fks = {(a.parent, a.attributes["columns"], a.attributes["ref_table"])
               for a in report.artifacts if a.kind is ArtifactKind.FOREIGN_KEY}
        assert fks == {
            ("table:files.attendance", ("employee_id",), "table:files.employees"),
            ("table:files.employees", ("department_id",), "table:files.departments"),
            ("table:files.leave_requests", ("employee_id",), "table:files.employees"),
        }

    async def test_a_dangling_reference_is_not_claimed_as_a_key(self, root) -> None:
        rows = [dict(r) for r in ROWS["public.attendance"]]
        rows[0]["employee_id"] = 999  # no such employee
        _write_csv(root / "hr-exports" / "attendance.csv", rows)
        _, report = await _discover(root)
        assert not any(a.kind is ArtifactKind.FOREIGN_KEY and a.parent == "table:files.attendance"
                       for a in report.artifacts)

    async def test_profiles_classify_like_the_postgres_connector(self, root) -> None:
        _, report = await _discover(root)
        profiles = {a.parent: a.attributes for a in report.artifacts if a.kind is ArtifactKind.COLUMN_PROFILE}
        assert profiles["column:files.employees.email"]["pattern"] == "email"
        assert profiles["column:files.employees.id"]["unique_in_sample"] is True
        assert "values" not in profiles["column:files.employees.status"]  # no row values unless opted in

    async def test_rediscovery_of_unchanged_files_is_identical(self, root) -> None:
        _, first = await _discover(root)
        _, second = await _discover(root)
        assert first == second

    async def test_json_arrays_of_objects_are_tables_too(self, root) -> None:
        (root / "hr-exports" / "teams.json").write_text(json.dumps([{"id": 1, "name": "Core", "tags": ["a"]},
                                                                    {"id": 2, "name": "Edge", "tags": []}]))
        (root / "hr-exports" / "broken.json").write_text('{"not": "an array"}')
        _, report = await _discover(root)
        types = {a.locator: a.attributes["data_type"] for a in report.artifacts if a.kind is ArtifactKind.COLUMN}
        assert types["column:files.teams.id"] == "integer" and types["column:files.teams.tags"] == "text"
        assert ("unparseable", "table:files.broken") in {(i.code, i.locator) for i in report.issues}


class TestContainment:
    @pytest.mark.parametrize("endpoint", ["../outside", "/etc", "hr-exports/../../outside", "missing"])
    async def test_an_endpoint_outside_the_root_is_refused(self, root, endpoint) -> None:
        (root.parent / "outside").mkdir(exist_ok=True)
        connector = await FilesConnectorFactory(root=str(root)).open(_descriptor(endpoint), None)
        health = await connector.health()
        assert health.status is HealthStatus.UNHEALTHY and health.reason == "invalid_endpoint"
        with pytest.raises(DependencyUnavailableError):
            await connector.discover(DiscoveryBudget())

    async def test_no_root_means_the_kind_is_off(self, root) -> None:
        factory = FilesConnectorFactory(root=None)
        assert not factory.available
        health = await (await factory.open(_descriptor(), None)).health()
        assert health.reason == "files_disabled"

    @pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symlinks")
    async def test_a_symlinked_file_is_skipped(self, root, tmp_path_factory) -> None:
        secret = tmp_path_factory.mktemp("secret") / "secret.csv"
        secret.write_text("id,token\n1,hunter2\n")
        (root / "hr-exports" / "leak.csv").symlink_to(secret)
        _, report = await _discover(root)
        assert not any(a.locator.startswith("table:files.leak") for a in report.artifacts)
        assert ("symlink_skipped", "table:files.leak") in {(i.code, i.locator) for i in report.issues}

    async def test_a_symlinked_directory_cannot_escape_the_root(self, root, tmp_path_factory) -> None:
        elsewhere = tmp_path_factory.mktemp("elsewhere")
        (root / "link").symlink_to(elsewhere, target_is_directory=True)
        assert resolve_directory(str(root), "link") is None

    async def test_healthy_and_never_write_capable(self, root) -> None:
        connector = await FilesConnectorFactory(root=str(root)).open(_descriptor(), None)
        health = await connector.health()
        assert health.ok and health.write_capable is False


class TestReads:
    async def test_a_template_reads_the_typed_rows(self, root) -> None:
        connector, _ = await _discover(root)
        template = ReadTemplate("t1", "hr-files", TemplateKind.SQL, sql=SqlSelect(
            FILES_SCHEMA, "employees", columns=(SqlColumn("id", "id"), SqlColumn("salary", "salary")),
            filters=(SqlFilter("id", FilterOp.EQ, "id"),), order_by=("id",),
        ))
        assert await connector.read(template, {"id": 3}) == [
            {"id": 3, "salary": next(r["salary"] for r in ROWS["public.employees"] if r["id"] == 3)}]

    async def test_another_connectors_template_is_refused(self, root) -> None:
        connector, _ = await _discover(root)
        template = ReadTemplate("t1", "other", TemplateKind.SQL, sql=SqlSelect(
            FILES_SCHEMA, "employees", columns=(SqlColumn("id", "id"),)))
        with pytest.raises(ToolExecutionError):
            await connector.read(template, {})


class TestEndToEnd:
    async def test_csv_exports_compile_and_pass_the_gate(self, root, clock) -> None:
        """Discovery -> twin -> plan -> compile -> AP8 gate, with the files as the live system."""
        factory = FilesConnectorFactory(root=str(root))
        connector, report = await _discover(root)
        twin = build_twin("p0", 1, [report]).twin
        plan = plan_capabilities(twin, CapabilityRegistry.from_directory())
        compiled = compile_blueprint(twin, plan, version=1, name="HR exports",
                                     connectors=[ConnectorRef("hr-files", ConnectorKind.FILES,
                                                              IdentityMode.SERVICE_ACCOUNT)])
        bp = compiled.blueprint
        sql_templates = [t for t in bp.templates if t.kind is TemplateKind.SQL]
        assert sql_templates and all(t.sql.schema == FILES_SCHEMA for t in sql_templates)

        async def open_connector(connector_id: str):
            assert connector_id == "hr-files"
            return await factory.open(_descriptor(), None)

        gate = BlueprintGate(open_connector=open_connector, new_registry=lambda: ToolRegistry(policy=ToolPolicy()),
                             clock=clock)
        record = BlueprintRecord(
            project_id="p0", version=1, state=BlueprintState.PROPOSED, digest=bp.digest,
            created_at="2026-10-12T00:00:00+00:00", created_by="sa@example.com",
            plan_digest="sha256:" + "1" * 64, registry_digest="sha256:" + "2" * 64,
            blueprint=bp, suites=compiled.suites, dropped=compiled.dropped,
        )
        result = await gate.evaluate(record, twin, by="sa@example.com")
        failed = {c.id: c.reason for c in result.cases if c.outcome.value == "failed"}
        assert result.passed, (result.blocking(), failed)
        contract = {s.name: s for s in result.suites}["tool_contract"]
        assert contract.ran == len(sql_templates) and contract.passed == contract.ran
