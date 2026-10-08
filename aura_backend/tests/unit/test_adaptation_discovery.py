"""AP3: deterministic discovery of the reference pilots, the OpenAPI connector's SSRF rules,
and the discovery service.

The golden tests replay recorded catalog snapshots and the committed API descriptions through
the pure mappings, so they need no database and no network. The connector tests stand in for the
*network* (httpx ``MockTransport`` and a stub DNS resolver) -- the external boundary -- and run the
real connector code against it. ``tests/load/test_adaptation_discovery_postgres.py`` proves the
recorded snapshots still match a live capture.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from aura_core.adaptation.connectors import ConnectorRegistry
from aura_core.adaptation.discovery import DiscoveryService
from aura_core.adapters.connectors.openapi import OpenApiConnectorFactory, artifacts_from_openapi, parse_spec
from aura_core.adapters.connectors.postgres_catalog import artifacts_from_catalog
from aura_core.adapters.credential_vault import EncryptedCredentialVault, InMemoryCiphertextStore
from aura_core.adapters.field_crypto import FieldCipher
from aura_core.adapters.memory_adaptation import InMemoryConnectorStore
from aura_core.domain.connectors import (
    ConnectorDescriptor,
    ConnectorHealth,
    ConnectorKind,
    Credential,
    HealthStatus,
    IdentityMode,
)
from aura_core.domain.discovery import (
    ArtifactKind,
    DiscoveryBudget,
    DiscoveryReport,
    RawArtifact,
    quote_identifier,
)
from aura_core.domain.errors import ConflictError, DependencyUnavailableError, ValidationError
from aura_core.domain.serialization import from_data, to_data
from aura_core.domain.untrusted import UntrustedText
from aura_core.learning.approval import ApprovalService, InMemoryApprovalStore

BACKEND = Path(__file__).resolve().parents[2]
PILOTS = BACKEND / "tests" / "fixtures" / "pilots"


def _goldens_module():
    spec = importlib.util.spec_from_file_location("pilot_goldens", BACKEND / "scripts" / "pilot_discovery_goldens.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GOLDENS = _goldens_module()


def _catalog(pilot: str) -> dict[str, Any]:
    return json.loads((PILOTS / pilot / "golden" / "catalog.json").read_text(encoding="utf-8"))


def _hr_report() -> DiscoveryReport:
    return artifacts_from_catalog("hr-db", _catalog("hr"), DiscoveryBudget())


# ------------------------------------------------------------------------------------- goldens


@pytest.mark.parametrize("pilot", GOLDENS.PILOTS)
@pytest.mark.parametrize("which", ["postgres", "openapi"])
def test_committed_golden_matches_the_mapping(pilot: str, which: str) -> None:
    rendered = GOLDENS.render_postgres(pilot) if which == "postgres" else GOLDENS.render_openapi(pilot)
    committed = (PILOTS / pilot / "golden" / f"discovery_{which}.json").read_text(encoding="utf-8")
    assert committed == rendered, (
        f"{pilot}/golden/discovery_{which}.json is stale -- run "
        "`python aura_backend/scripts/pilot_discovery_goldens.py` from the repo root"
    )


@pytest.mark.parametrize("pilot", GOLDENS.PILOTS)
def test_reports_round_trip_and_digests_are_stable(pilot: str) -> None:
    report = artifacts_from_catalog(f"{pilot}-db", _catalog(pilot), DiscoveryBudget())
    again = from_data(DiscoveryReport, json.loads(json.dumps(to_data(report))))
    assert again == report and again.digest == report.digest
    assert artifacts_from_catalog(f"{pilot}-db", _catalog(pilot), DiscoveryBudget()).digest == report.digest


# ------------------------------------------------------------------------------------- catalog


class TestCatalogMapping:
    def test_hr_structure_is_discovered(self) -> None:
        report = _hr_report()
        assert report.issues == () and not report.truncated
        fk = report.get("fk:public.employees.employees_manager_id_fkey")
        assert fk.attributes["ref_table"] == "table:public.employees"  # self-reference
        assert report.get("column:public.leave_requests.leave_type").attributes["enum_type"] == "enum:public.leave_type"
        assert report.get("enum:public.leave_type").attributes["labels"] == ("annual", "sick", "unpaid", "parental")
        assert report.get("view:public.active_employees") is not None
        assert report.get("profile:public.active_employees.id") is None  # views are not sampled
        grant = report.get("grant:hr_manager@public.leave_requests")
        assert grant.attributes["privileges"] == ("INSERT", "SELECT", "UPDATE")

    def test_profiles_classify_without_carrying_values(self) -> None:
        report = _hr_report()
        assert report.get("profile:public.employees.email").attributes["pattern"] == "email"
        status = report.get("profile:public.employees.status").attributes
        assert status["pattern"] == "categorical" and "values" not in status
        golden = (PILOTS / "hr" / "golden" / "discovery_postgres.json").read_text(encoding="utf-8")
        # Seeded row values never reach the report; schema-declared values (CHECK, enum) do.
        assert "@hr.example" not in golden and "Employee 1" not in golden

    def test_discovered_text_stays_untrusted(self) -> None:
        salary = _hr_report().get("column:public.employees.salary")
        assert isinstance(salary.description, UntrustedText)
        assert "Ignore" not in str(salary.description) and "Ignore" not in f"{salary.description}"
        assert "Ignore all previous instructions" in salary.description.raw()

    def test_quoted_identifiers_are_unambiguous(self) -> None:
        report = artifacts_from_catalog("ecommerce-db", _catalog("ecommerce"), DiscoveryBudget())
        assert report.get('table:shop."SupplierContacts"') is not None
        assert report.get("fk:shop.returns.returns_item_fk").attributes["columns"] == ("order_id", "sku")
        assert quote_identifier("employees") == "employees"
        assert quote_identifier('Odd "name"') == '"Odd ""name"""'

    def test_sample_values_are_opt_in(self) -> None:
        snapshot = _catalog("hr")
        snapshot["profiles"] = [dict(p) for p in snapshot["profiles"]]
        target = next(p for p in snapshot["profiles"] if p["table"] == "employees" and p["column"] == "status")
        target["values"] = ["active", "on_leave", "terminated"]
        report = artifacts_from_catalog("hr-db", snapshot, DiscoveryBudget(sample_values=True))
        assert report.get("profile:public.employees.status").attributes["values"] == ("active", "on_leave", "terminated")

    def test_budget_truncates_honestly(self) -> None:
        report = artifacts_from_catalog("hr-db", _catalog("hr"), DiscoveryBudget(max_objects=10))
        assert len(report.artifacts) == 10 and report.truncated
        assert any(i.code == "budget_exhausted" for i in report.issues)

    def test_capture_issues_are_carried(self) -> None:
        snapshot = {**_catalog("hr"), "issues": [{"code": "not_readable", "locator": "table:public.assets"}]}
        report = artifacts_from_catalog("hr-db", snapshot, DiscoveryBudget())
        assert [(i.code, i.locator) for i in report.issues] == [("not_readable", "table:public.assets")]


class TestRawArtifact:
    def test_lists_become_tuples_and_bad_text_is_refused(self) -> None:
        artifact = RawArtifact(ArtifactKind.ROLE, "role:x", UntrustedText.of("x"), "pg_catalog", attributes={"a": ["b"]})
        assert artifact.attributes["a"] == ("b",)
        with pytest.raises(ValidationError):
            RawArtifact(ArtifactKind.ROLE, "role:x", UntrustedText.of("x"), "pg_catalog", attributes={"a": "bad\x1b[0m"})
        with pytest.raises(ValidationError):
            RawArtifact(ArtifactKind.ROLE, "role:x", UntrustedText.of("x"), "pg_catalog", attributes={"a": {"nested": 1}})

    def test_reports_must_be_canonical(self) -> None:
        a = RawArtifact(ArtifactKind.ROLE, "role:b", UntrustedText.of("b"), "x")
        b = RawArtifact(ArtifactKind.ROLE, "role:a", UntrustedText.of("a"), "x")
        with pytest.raises(ValidationError, match="sorted"):
            DiscoveryReport("s", "x", (a, b))
        assert [x.locator for x in DiscoveryReport.build("s", "x", [a, b]).artifacts] == ["role:a", "role:b"]


# ------------------------------------------------------------------------------------- openapi


class TestOpenApiMapping:
    def test_hr_operations(self) -> None:
        report = artifacts_from_openapi("hr-api", parse_spec((PILOTS / "hr" / "openapi.yaml").read_bytes()), DiscoveryBudget())
        listing = report.get("op:GET /employees").attributes
        assert listing["response_schema"] == 'schema:"Employee"' and listing["response_is_array"] is True
        assert report.get("op:GET /employees/{employeeId}").attributes["parameters"] == ("path:employeeId",)
        # AR-2: optional query parameters keep their type and enum; their summaries are never kept.
        assert [json.loads(d) for d in listing["query_parameters"]] == [
            {"n": "departmentId", "t": "integer"},
            {"n": "status", "t": "string", "e": ["active", "on_leave", "terminated"]},
        ]
        assert report.get("op:POST /leave-requests/{requestId}/approve").attributes["security"] == ("oauth",)
        assert report.get("op:GET /departments").attributes["security"] == ("bearerAuth",)
        assert [(i.code, i.locator) for i in report.issues] == [("external_ref_ignored", 'prop:"Employee"."homeAddress"')]
        injected = report.get("op:GET /leave-requests").description
        assert isinstance(injected, UntrustedText) and "Ignore" not in str(injected)

    @pytest.mark.parametrize(("prop", "expected"), [
        ({"type": "string"}, ("string", False, None)),
        ({"type": "string", "nullable": True}, ("string", True, None)),
        ({"type": ["integer", "null"]}, ("integer", True, None)),           # OpenAPI 3.1
        ({"type": ["integer", "string"]}, (None, False, None)),             # genuinely ambiguous
        ({"$ref": "#/components/schemas/Meta"}, ("object", False, 'schema:"Meta"')),
        ({"$ref": "#/components/schemas/Status"}, ("string", False, 'schema:"Status"')),
        ({"allOf": [{"$ref": "#/components/schemas/Meta"}]}, ("object", False, 'schema:"Meta"')),
        ({"anyOf": [{"$ref": "#/components/schemas/Meta"}, {"type": "null"}]}, ("object", True, 'schema:"Meta"')),
        ({"oneOf": [{"type": "string"}, {"type": "number"}]}, (None, False, None)),
        ({"anyOf": [{"$ref": "#/components/schemas/Loop"}, {"type": "null"}]}, (None, False, 'schema:"Loop"')),
        ({"properties": {"a": {"type": "string"}}}, ("object", False, None)),
        ({"items": {"type": "string"}}, ("array", False, None)),
    ])
    def test_property_types_resolve_through_refs_and_unions(self, prop, expected) -> None:
        """A property typed only through $ref/allOf/anyOf/oneOf or a 3.1 type list is recorded
        with the JSON type its values have; otherwise the compiler typed nested objects as
        strings and every real response failed its tool's output schema (ADR-025)."""
        spec = {"openapi": "3.1.0", "info": {"title": "t", "version": "1"}, "paths": {},
                "components": {"schemas": {
                    "Meta": {"type": "object", "properties": {"request_id": {"type": "string"}}},
                    "Status": {"type": "string", "enum": ["up", "down"]},
                    "Loop": {"$ref": "#/components/schemas/Loop"},
                    "Envelope": {"type": "object", "properties": {"field": prop}},
                }}}
        report = artifacts_from_openapi("api", spec, DiscoveryBudget())
        attributes = report.get('prop:"Envelope".field').attributes
        assert (attributes["type"], attributes["nullable"], attributes["ref"]) == expected
        assert report.issues == ()

    def test_an_external_ref_is_reported_once_and_left_untyped(self) -> None:
        spec = {"openapi": "3.0.3", "info": {"title": "t", "version": "1"}, "paths": {},
                "components": {"schemas": {"Envelope": {"type": "object", "properties": {
                    "field": {"$ref": "https://evil.example/schema.json"}}}}}}
        report = artifacts_from_openapi("api", spec, DiscoveryBudget())
        assert report.get('prop:"Envelope".field').attributes["type"] is None
        assert [(i.code, i.locator) for i in report.issues] == [("external_ref_ignored", 'prop:"Envelope".field')]

    @pytest.mark.parametrize("body", [b'{"swagger": "2.0"}', b"- not a mapping", b"{not json", b"openapi: 2.0"])
    def test_only_openapi_3_is_accepted(self, body: bytes) -> None:
        with pytest.raises(ValidationError):
            parse_spec(body)


_HR_SPEC = (PILOTS / "hr" / "openapi.yaml").read_bytes()


def _openapi_descriptor(endpoint: str = "https://hr.example.com/openapi.yaml", **options: Any) -> ConnectorDescriptor:
    return ConnectorDescriptor(
        id="hr-api", kind=ConnectorKind.OPENAPI, identity_mode=IdentityMode.DELEGATED,
        display_name="HR API", endpoint=endpoint, options=options, timeout_s=5.0,
    )


def _resolver(*addresses: str):
    async def resolve(host: str) -> list[str]:
        return list(addresses)
    return resolve


def _transport(handler):
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return httpx.MockTransport(wrapped), seen


async def _health(descriptor, *, handler=lambda r: httpx.Response(200, content=_HR_SPEC),
                  resolver=_resolver("93.184.216.34"), credential: Credential | None = None):
    transport, seen = _transport(handler)
    factory = OpenApiConnectorFactory(transport=transport, resolver=resolver)
    connector = await factory.open(descriptor, credential)
    return await connector.health(), seen


class TestOpenApiConnector:
    async def test_public_spec_is_healthy_and_cannot_write(self) -> None:
        health, seen = await _health(_openapi_descriptor())
        assert health.status is HealthStatus.HEALTHY and health.write_capable is False
        assert "authorization" not in seen[0].headers

    @pytest.mark.parametrize(
        "addresses, options, allowed",
        [
            (("10.0.0.5",), {}, False),
            (("10.0.0.5",), {"allow_private_network": True}, True),
            (("127.0.0.1",), {"allow_private_network": True}, False),
            (("169.254.169.254",), {"allow_private_network": True}, False),
            (("100.100.100.200",), {"allow_private_network": True}, False),
            (("93.184.216.34", "10.0.0.5"), {}, False),
            (("::ffff:127.0.0.1",), {"allow_private_network": True}, False),
        ],
    )
    async def test_destination_policy(self, addresses, options, allowed) -> None:
        health, seen = await _health(_openapi_descriptor(**options), resolver=_resolver(*addresses))
        assert (health.reason == "ok") is allowed
        if not allowed:
            assert health.reason == "destination_refused" and seen == []  # nothing was sent

    async def test_ip_literal_endpoints_are_checked_too(self) -> None:
        health, seen = await _health(_openapi_descriptor("http://169.254.169.254/latest/meta-data"))
        assert health.reason == "destination_refused" and seen == []

    @pytest.mark.parametrize(
        "response, reason",
        [
            (httpx.Response(302, headers={"location": "http://169.254.169.254/"}), "redirect_refused"),
            (httpx.Response(401), "auth_failed"),
            (httpx.Response(500), "http_error"),
            (httpx.Response(200, content=b"x" * (5 * 1024 * 1024 + 1)), "spec_too_large"),
            (httpx.Response(200, content=b'{"swagger": "2.0"}'), "invalid_spec"),
        ],
    )
    async def test_failures_have_reasons(self, response, reason) -> None:
        health, _ = await _health(_openapi_descriptor(), handler=lambda r: response)
        assert health.status is HealthStatus.UNHEALTHY and health.reason == reason

    async def test_credentials_only_over_https_and_their_scope_is_unknown(self) -> None:
        plain, seen = await _health(_openapi_descriptor("http://hr.example.com/openapi.yaml"), credential=Credential("tok"))
        assert plain.reason == "credential_requires_https" and seen == []
        tls, seen = await _health(_openapi_descriptor(), credential=Credential("tok"))
        assert seen[0].headers["authorization"] == "Bearer tok"
        assert tls.write_capable is None and tls.findings == ("credential_scope_unknown",)
        # AR-3: a Super Admin's attestation (bound into the approved digest) makes it read-only.
        attested, seen = await _health(_openapi_descriptor(read_only_attested=True), credential=Credential("tok"))
        assert seen[0].headers["authorization"] == "Bearer tok"
        assert attested.write_capable is False and attested.findings == ("credential_scope_attested",)
        public, _ = await _health(_openapi_descriptor(read_only_attested=True))
        assert public.write_capable is False and public.findings == ()  # nothing to attest without a token

    async def test_discover_matches_the_golden(self) -> None:
        transport, _ = _transport(lambda r: httpx.Response(200, content=_HR_SPEC))
        connector = await OpenApiConnectorFactory(transport=transport, resolver=_resolver("93.184.216.34")).open(
            _openapi_descriptor(), None
        )
        report = await connector.discover(DiscoveryBudget())
        assert to_data(report) == json.loads((PILOTS / "hr" / "golden" / "discovery_openapi.json").read_text())


# ----------------------------------------------------------------------------------- service


class _DiscoveringStub:
    def __init__(self, descriptor, behaviour) -> None:
        self.descriptor = descriptor
        self._behaviour = behaviour

    async def health(self) -> ConnectorHealth:
        return ConnectorHealth(HealthStatus.HEALTHY, write_capable=False)

    async def discover(self, budget: DiscoveryBudget) -> DiscoveryReport:
        if self._behaviour == "hang":
            await asyncio.sleep(60)
        if isinstance(self._behaviour, BaseException):
            raise self._behaviour
        return self._behaviour

    async def aclose(self) -> None:
        return None


class _HealthOnlyStub(_DiscoveringStub):
    discover = None  # type: ignore[assignment]


class _Factory:
    def __init__(self, behaviour, cls=_DiscoveringStub) -> None:
        self.behaviour, self.cls = behaviour, cls

    async def open(self, descriptor, credential):
        return self.cls(descriptor, self.behaviour)


class _Metrics:
    def __init__(self) -> None:
        self.runs: list[tuple[str, str]] = []
        self.artifacts: dict[str, int] = {}

    def set_connector_health(self, *args) -> None: ...

    def observe_discovery(self, connector, kind, outcome, seconds) -> None:
        self.runs.append((kind, outcome))

    def set_discovery_artifacts(self, connector, count) -> None:
        self.artifacts[connector] = count


async def _service(audit, clock, behaviour, *, cls=_DiscoveringStub, timeout_s: float = 5.0, activate: bool = True):
    from cryptography.fernet import Fernet

    approvals = ApprovalService(store=InMemoryApprovalStore(), audit=audit)
    registry = ConnectorRegistry(
        store=InMemoryConnectorStore(),
        vault=EncryptedCredentialVault(cipher=FieldCipher([Fernet.generate_key().decode()]), store=InMemoryCiphertextStore()),
        approvals=approvals, factories={ConnectorKind.OPENAPI: _Factory(behaviour, cls)}, clock=clock, audit=audit,
    )
    descriptor = ConnectorDescriptor(id="hr-api", kind=ConnectorKind.OPENAPI, identity_mode=IdentityMode.DELEGATED,
                                     display_name="HR API", endpoint="https://hr.example.com/openapi.yaml", timeout_s=timeout_s)
    _, approval = await registry.register(descriptor, secret=None, requested_by="sa@example.com")
    if activate:
        await approvals.decide(approval.id, approved=True, by="reviewer@example.com")
        await registry.activate("hr-api", approval_id=approval.id, by="sa@example.com")
    metrics = _Metrics()
    return DiscoveryService(registry=registry, clock=clock, audit=audit, metrics=metrics), metrics


class TestDiscoveryService:
    async def test_success_is_audited_by_digest_not_content(self, audit, clock) -> None:
        report = artifacts_from_openapi("hr-api", parse_spec(_HR_SPEC), DiscoveryBudget())
        service, metrics = await _service(audit, clock, report)
        assert await service.discover("hr-api", budget=DiscoveryBudget(), by="sa@example.com") == report
        (event,) = await audit.query(action="adaptation.connector.discover", limit=5)
        assert event.outcome == "ok" and event.detail["digest"] == report.digest
        assert "Ignore" not in repr(event.as_dict())
        assert metrics.runs == [("openapi", "ok")] and metrics.artifacts == {"hr-api": len(report.artifacts)}

    async def test_only_active_connectors_are_discovered(self, audit, clock) -> None:
        service, _ = await _service(audit, clock, None, activate=False)
        with pytest.raises(ConflictError, match="pending_approval"):
            await service.discover("hr-api", budget=DiscoveryBudget(), by="sa@example.com")

    async def test_a_connector_that_cannot_discover_is_refused(self, audit, clock) -> None:
        service, metrics = await _service(audit, clock, None, cls=_HealthOnlyStub)
        with pytest.raises(ValidationError, match="cannot discover"):
            await service.discover("hr-api", budget=DiscoveryBudget(), by="sa@example.com")
        assert metrics.runs == [("openapi", "unsupported")]

    async def test_a_raising_connector_leaks_nothing(self, audit, clock, caplog) -> None:
        service, _ = await _service(audit, clock, RuntimeError("password=hunter2"))
        with pytest.raises(DependencyUnavailableError) as caught:
            await service.discover("hr-api", budget=DiscoveryBudget(), by="sa@example.com")
        assert "hunter2" not in str(caught.value.to_dict()) and "hunter2" not in caplog.text

    async def test_a_hung_connector_times_out(self, audit, clock) -> None:
        service, metrics = await _service(audit, clock, "hang", timeout_s=0.5)
        with pytest.raises(DependencyUnavailableError, match="budget"):
            await service.discover("hr-api", budget=DiscoveryBudget(max_seconds=1.0), by="sa@example.com")
        assert metrics.runs == [("openapi", "timeout")]


# -------------------------------------------------------------------------------------- HTTP


def test_super_admin_discovers_an_approved_api(monkeypatch) -> None:
    from cryptography.fernet import Fernet
    from fastapi.testclient import TestClient

    from aura_core.api import Principal, build_app
    from aura_core.config.settings import load_settings
    from aura_core.container import Container
    from aura_core.domain.identifiers import UserId

    monkeypatch.setenv("AURA_ADAPTATION_ENABLED", "true")
    monkeypatch.setenv("AURA_SECRETS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    container = Container(load_settings(refresh=True, validate=False))
    transport, _ = _transport(lambda r: httpx.Response(200, content=_HR_SPEC))
    container.connectors.register_factory(
        ConnectorKind.OPENAPI, OpenApiConnectorFactory(transport=transport, resolver=_resolver("93.184.216.34"))
    )

    async def authenticator(request):
        return Principal(user_id=UserId("op-1"), email="op@example.com", role="SuperAdmin", capabilities=())

    with TestClient(build_app(container, authenticator=authenticator)) as client:
        created = client.post("/api/v3/admin/connectors", json={
            "id": "hr-api", "kind": "openapi", "identity_mode": "delegated", "display_name": "HR API",
            "endpoint": "https://hr.example.com/openapi.yaml",
        })
        assert created.status_code == 201, created.text
        approval_id = created.json()["approval"]["id"]
        assert client.post("/api/v3/admin/connectors/hr-api/discover").status_code == 409  # not active yet
        client.post(f"/api/v3/admin/approvals/{approval_id}/decide", json={"approved": True})
        assert client.post("/api/v3/admin/connectors/hr-api/activate", json={"approval_id": approval_id}).status_code == 200
        response = client.post("/api/v3/admin/connectors/hr-api/discover", json={"max_objects": 500})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["summary"]["counts"]["operation"] == 8
        assert body["report"]["source_id"] == "hr-api"
