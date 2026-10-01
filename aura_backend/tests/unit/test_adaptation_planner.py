"""AP5: the capability registry and the evidence-based planner (ADR-023).

Everything runs on the committed pilot twins: no database, no network, no model. The exit
criterion -- both pilots planned from the *same* primitive set -- is
``test_both_pilots_are_planned_from_the_same_registry``.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
from pathlib import Path

import pytest

from aura_core.adaptation.planner import (
    CAPABILITIES_DIR,
    CapabilityRegistry,
    PlanningService,
    plan_capabilities,
)
from aura_core.adaptation.twin import TwinService, build_twin
from aura_core.adaptation.labeling import LabelingPass
from aura_core.adapters.memory_adaptation import InMemoryTwinStore
from aura_core.domain.ausm import SOURCE_LOCATOR, AusmElement, ElementKind, ProjectTwin, Provenance
from aura_core.domain.capabilities import (
    CapabilitySpec,
    ElementFilter,
    Requirement,
    RequirementRelation,
)
from aura_core.domain.discovery import DiscoveryReport
from aura_core.domain.errors import NotFoundError, ValidationError
from aura_core.domain.serialization import from_data, to_data
from aura_core.domain.tools import ToolSensitivity
from aura_core.domain.untrusted import UntrustedText

BACKEND = Path(__file__).resolve().parents[2]
PILOTS = BACKEND / "tests" / "fixtures" / "pilots"


def _goldens():
    spec = importlib.util.spec_from_file_location("pilot_twin_goldens", BACKEND / "scripts" / "pilot_twin_goldens.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GOLDENS = _goldens()


@pytest.fixture(scope="module")
def registry() -> CapabilityRegistry:
    return CapabilityRegistry.from_directory()


def _twin(pilot: str) -> ProjectTwin:
    return from_data(ProjectTwin, json.loads((PILOTS / pilot / "golden" / "twin.json").read_text()))


@pytest.fixture(scope="module")
def hr_plan(registry):
    return plan_capabilities(_twin("hr"), registry)


@pytest.fixture(scope="module")
def shop_plan(registry):
    return plan_capabilities(_twin("ecommerce"), registry)


# ------------------------------------------------------------------------------- exit criteria


@pytest.mark.parametrize("pilot", ["hr", "ecommerce"])
def test_committed_plan_golden_matches(pilot: str) -> None:
    committed = (PILOTS / pilot / "golden" / "plan.json").read_text(encoding="utf-8")
    assert committed == GOLDENS.render_plan(pilot), "run `python aura_backend/scripts/pilot_twin_goldens.py`"


def test_both_pilots_are_planned_from_the_same_registry(hr_plan, shop_plan, registry) -> None:
    assert hr_plan.registry_digest == shop_plan.registry_digest == registry.digest
    shared = {s.capability for s in hr_plan.selected} & {s.capability for s in shop_plan.selected}
    assert {"entity.lookup", "entity.search", "entity.list_filtered", "relation.traverse",
            "state.timeline", "workflow.status", "metric.aggregate", "operation.read"} <= shared


@pytest.mark.parametrize("pilot", ["hr", "ecommerce"])
def test_every_selection_is_evidenced(pilot: str, registry) -> None:
    twin = _twin(pilot)
    plan = plan_capabilities(twin, registry)
    ids = twin.ids()
    assert plan.selected
    for selection in plan.selected:
        assert selection.target in selection.evidence and len(selection.evidence) >= 2
        assert set(selection.evidence) <= ids, selection.key
    for agent in plan.agents:
        assert set(agent.evidence) <= ids and set(agent.entities) <= ids
        assert set(agent.capabilities) <= {s.key for s in plan.selected}


def test_the_plan_is_deterministic(registry) -> None:
    twin = _twin("hr")
    first = plan_capabilities(twin, registry)
    again = plan_capabilities(dataclasses.replace(twin, version=9), registry)
    assert first.selected == again.selected and first.rejected == again.rejected and first.agents == again.agents
    assert first.digest == plan_capabilities(twin, CapabilityRegistry.from_directory()).digest


# ------------------------------------------------------------------------------- matching


class TestSelections:
    def test_identifier_lookup_and_its_evidence(self, hr_plan) -> None:
        by_key = {s.key: s for s in hr_plan.selected}
        lookup = by_key["entity.lookup@hr-db:table:public.employees"]
        assert "hr-db:column:public.employees.id" in lookup.evidence
        assert lookup.risk is ToolSensitivity.PERSONAL
        assert set(lookup.roles) == {"hr-db:role:hr_manager", "hr-db:role:hr_viewer"}

    def test_workflow_status_rests_on_states_and_a_process(self, shop_plan) -> None:
        by_key = {s.key: s for s in shop_plan.selected}
        status = by_key["workflow.status@ecommerce-db:column:shop.orders.status"]
        assert "ecommerce-db:workflow:shop.orders.status" in status.evidence
        assert sum(1 for e in status.evidence if e.startswith("ecommerce-db:state:")) == 6
        assert status.confidence < 1.0  # the process is an inferred candidate

    def test_a_categorical_attribute_is_not_a_workflow(self, hr_plan) -> None:
        targets = {s.target for s in hr_plan.selected if s.capability == "workflow.status"}
        assert "hr-db:column:public.leave_requests.leave_type" not in targets

    def test_writes_are_rejected_as_deferred(self, hr_plan) -> None:
        writes = [r for r in hr_plan.rejected if r.capability == "operation.write"]
        assert len(writes) == 4 and all(r.reasons == ("unavailable:writes_deferred",) for r in writes)
        assert not any(s.capability == "operation.write" for s in hr_plan.selected)

    def test_capabilities_without_candidates_are_reported(self, hr_plan) -> None:
        assert any(r.capability == "document.qa" and r.target == "" and r.reasons == ("no_candidates",)
                   for r in hr_plan.rejected)

    def test_unmet_requirements_are_named(self, hr_plan) -> None:
        rejected = {(r.capability, r.target): r.reasons for r in hr_plan.rejected}
        assert rejected[("notification.draft", "hr-db:table:public.assets")] == ("unmet:contact", "unmet:events")

    def test_risk_is_the_capability_floor(self, shop_plan) -> None:
        (draft,) = [s for s in shop_plan.selected if s.capability == "notification.draft"]
        assert draft.target == "ecommerce-db:table:shop.customers"
        assert draft.risk is ToolSensitivity.CONFIDENTIAL

    def test_proposals_and_weak_inferences_cannot_carry_a_capability(self, registry) -> None:
        twin = _twin("ecommerce")
        weakened = [
            dataclasses.replace(e, provenance=tuple(dataclasses.replace(p, confidence=0.2) for p in e.provenance))
            if e.kind is ElementKind.WORKFLOW else e
            for e in twin.elements
        ]
        plan = plan_capabilities(ProjectTwin.build("p0", 1, weakened), registry)
        assert not any(s.capability == "workflow.status" for s in plan.selected)
        assert ("workflow.status", "ecommerce-db:column:shop.orders.status") in {
            (r.capability, r.target) for r in plan.rejected if "unmet:process" in r.reasons
        }


# ------------------------------------------------------------------------------- agents


class TestAgents:
    def test_one_project_agent_and_one_per_reading_role(self, hr_plan) -> None:
        ids = [a.id for a in hr_plan.agents]
        assert ids == ["agent.project", "agent.role.hr_manager", "agent.role.hr_viewer"]
        project = hr_plan.agents[0]
        assert project.role is None and len(project.capabilities) == len(hr_plan.selected)

    def test_a_role_agent_covers_what_the_role_can_read(self, hr_plan) -> None:
        manager = next(a for a in hr_plan.agents if a.id == "agent.role.hr_manager")
        assert manager.role == "hr-db:role:hr_manager"
        assert "hr-db:table:public.assets" not in manager.entities  # no grant on it
        assert "hr-db:table:public.employees" in manager.entities
        # extended through the proposed same_as match to the API's schema
        assert 'hr-api:schema:"Employee"' in manager.entities
        assert 'hr-api:same_as:hr-db:table:public.employees' in manager.evidence
        assert "hr-db:grant:hr_manager@public.employees" in manager.evidence

    def test_agent_risk_ceiling_is_its_riskiest_capability(self, shop_plan) -> None:
        project = shop_plan.agents[0]
        assert project.risk_ceiling is ToolSensitivity.CONFIDENTIAL  # notification.draft is in it
        support = next(a for a in shop_plan.agents if a.id == "agent.role.shop_support")
        assert support.risk_ceiling is ToolSensitivity.CONFIDENTIAL  # can read customers
        ops = next(a for a in shop_plan.agents if a.id == "agent.role.shop_ops")
        assert ops.risk_ceiling is ToolSensitivity.PERSONAL


# ------------------------------------------------------------------------------- failure cases


def test_an_empty_twin_gets_an_empty_plan_with_a_reason(registry) -> None:
    evidence = "sha256:" + "0" * 64
    source = AusmElement("src", SOURCE_LOCATOR, ElementKind.SOURCE, UntrustedText("Src"),
                         (Provenance("src", SOURCE_LOCATOR, "pg_catalog", 1.0, evidence),))
    plan = plan_capabilities(ProjectTwin.build("p0", 1, [source]), registry)
    assert plan.reason == "empty_twin" and not plan.selected and not plan.agents
    assert plan_capabilities(ProjectTwin.build("p0", 1, []), registry).reason == "empty_twin"


def test_a_twin_with_nothing_readable_selects_nothing_and_says_why(registry) -> None:
    twin = _twin("hr")
    hidden = [
        dataclasses.replace(e, properties={**e.properties, "readable": False}) if e.kind is ElementKind.ENTITY else e
        for e in twin.elements if e.source_id == "hr-db"
    ]
    plan = plan_capabilities(ProjectTwin.build("p0", 1, hidden), registry)
    assert not any(s.capability.startswith("entity.") for s in plan.selected)
    assert any(r.capability == "entity.lookup" and r.reasons == ("no_candidates",) for r in plan.rejected)


# ------------------------------------------------------------------------------- registry


class TestRegistry:
    def test_files_load_and_are_named_after_their_ids(self, registry) -> None:
        assert len(registry) == len(list(CAPABILITIES_DIR.glob("*.yaml"))) >= 10
        assert registry.get("entity.lookup").produces.tool == "query.lookup"

    def test_unknown_keys_are_refused(self, tmp_path) -> None:
        (tmp_path / "entity.lookup.yaml").write_text(
            "id: entity.lookup\nversion: 1\nsummary: x\ntarget: entity\nexecute: rm -rf /\n"
        )
        with pytest.raises(ValidationError):
            CapabilityRegistry.from_directory(tmp_path)

    def test_a_file_must_be_named_after_its_id(self, tmp_path) -> None:
        (tmp_path / "other.name.yaml").write_text("id: entity.lookup\nversion: 1\nsummary: x\ntarget: entity\n")
        with pytest.raises(ValidationError, match="named after"):
            CapabilityRegistry.from_directory(tmp_path)

    def test_spec_invariants(self) -> None:
        with pytest.raises(ValidationError):
            CapabilitySpec("entity.x", 1, "s", ElementKind.ENTITY, available=False)  # no reason
        with pytest.raises(ValidationError):
            CapabilitySpec("notdotted", 1, "s", ElementKind.ENTITY)
        with pytest.raises(ValidationError):
            Requirement("r", RequirementRelation.REF, ElementKind.ENTITY)  # ref needs a ref name
        with pytest.raises(ValidationError):
            ElementFilter(where={"x": ()})

    def test_the_registry_digest_pins_the_primitives(self, registry) -> None:
        extra = CapabilitySpec("entity.export", 1, "Export records.", ElementKind.ENTITY)
        assert CapabilityRegistry([*registry, extra]).digest != registry.digest
        with pytest.raises(ValidationError):
            CapabilityRegistry([*registry, registry.get("entity.lookup")])

    def test_specs_round_trip(self, registry) -> None:
        for spec in registry:
            assert from_data(CapabilitySpec, to_data(spec)) == spec


# ------------------------------------------------------------------------------- service + HTTP


def _reports(pilot: str) -> list[DiscoveryReport]:
    return [
        from_data(DiscoveryReport, json.loads((PILOTS / pilot / "golden" / f"discovery_{k}.json").read_text()))
        for k in ("postgres", "openapi")
    ]


async def test_planning_service_plans_the_latest_version(audit, clock, registry) -> None:
    twins = TwinService(project_id="p0", registry=None, discovery=None, store=InMemoryTwinStore(),
                        labeling=LabelingPass(None), clock=clock, audit=audit)

    class Metrics:
        plans: list = []

        def observe_capability_plan(self, plan) -> None:
            self.plans.append(plan)

    metrics = Metrics()
    service = PlanningService(registry=registry, twins=twins, metrics=metrics)
    with pytest.raises(NotFoundError):
        await service.plan()
    await twins.snapshot_reports(_reports("hr"), by="sa@example.com")
    plan = await service.plan()
    assert plan.twin_version == 1 and plan.twin_digest == build_twin("p0", 1, _reports("hr")).twin.digest
    assert metrics.plans == [plan]
    with pytest.raises(NotFoundError):
        await service.plan(5)


def test_super_admin_gets_a_plan(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from aura_core.api import Principal, build_app
    from aura_core.config.settings import load_settings
    from aura_core.container import Container
    from aura_core.domain.identifiers import UserId

    monkeypatch.setenv("AURA_ADAPTATION_ENABLED", "true")
    container = Container(load_settings(refresh=True, validate=False))
    reports = {r.source_id: r for r in _reports("ecommerce")}

    class Discovery:
        async def discover(self, connector_id, *, budget, by):
            return reports[connector_id]

    container.twins._discovery = Discovery()

    async def authenticator(request):
        return Principal(user_id=UserId("op-1"), email="op@example.com", role="SuperAdmin", capabilities=())

    with TestClient(build_app(container, authenticator=authenticator)) as client:
        assert client.get("/api/v3/admin/twin/versions/1/plan").status_code == 404
        snap = client.post("/api/v3/admin/twin/snapshot", json={"connector_ids": ["ecommerce-db", "ecommerce-api"]})
        assert snap.status_code == 200, snap.text
        response = client.get("/api/v3/admin/twin/versions/1/plan")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["summary"]["selected"] == len(body["plan"]["selected"]) > 0
        assert body["plan"]["registry_digest"] == container.capability_planner.registry.digest
        assert body["plan"]["agents"][0]["id"] == "agent.project"
