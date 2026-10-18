"""AP10: identity modes -- delegated token passthrough and service-account role bindings (ADR-021).

No network, no model. What is proven here:

* **Delegated.** A ``DELEGATED`` OpenAPI connector reads with the caller's own token and nothing
  else: a missing, expired or rejected token is refused with a re-authentication hint, nothing is
  sent for a missing or expired one, a rejected one is not retried, and no stored or anonymous
  credential ever stands in. A token reaches only the connector it was sent for.
* **The token's path.** ``X-Aura-Connector-Token`` -> ``TurnRequest.connector_tokens`` ->
  ``ToolContext.credentials`` -> that one connector's ``read``; redacted at every step.
* **Service account.** With ``role_bindings`` in the blueprint, an AURA role gets exactly the
  tools its bound source roles were granted, through the existing ``ToolPolicy`` and agent gates,
  and a role agent only for its own role. Without bindings nothing changes, digest included.
"""

from __future__ import annotations

import base64
import dataclasses
import importlib.util
import json
import time
from pathlib import Path
from typing import Any
import httpx
import pytest

from aura_core.adaptation.access import (
    PROJECT_DATA_CAPABILITY,
    agent_capabilities,
    all_grants,
    expand_capabilities,
    grants_by_aura_role,
    tool_capability,
)
from aura_core.adaptation.compiler import compile_blueprint
from aura_core.adaptation.planner import CapabilityRegistry, plan_capabilities
from aura_core.adaptation.runtime import (
    ActiveBlueprint,
    BlueprintToolMaterializer,
    GeneratedReadTool,
    caller_identity,
    generated_agent_spec,
)
from aura_core.adapters.connectors.openapi import OpenApiConnectorFactory
from aura_core.agents.registry import build_default_agent_registry
from aura_core.domain.blueprint import RoleBinding, SecuritySection
from aura_core.domain.connectors import (
    CallerIdentity,
    ConnectorDescriptor,
    ConnectorKind,
    Credential,
    IdentityMode,
    bearer_token_expiry,
    connector_token_key,
)
from aura_core.domain.errors import ToolExecutionError, ValidationError
from aura_core.domain.identifiers import ToolCallId, UserId
from aura_core.domain.messages import ToolCall
from aura_core.domain.templates import HttpGet, ReadTemplate, TemplateKind
from aura_core.ports.runtime import ToolContext
from aura_core.tools.policy import ToolPolicy
from aura_core.tools.registry import ToolRegistry

BACKEND = Path(__file__).resolve().parents[2]


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, BACKEND / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TWINS = _script("pilot_twin_goldens")
HR_TWIN = TWINS.twin_for("hr")
HR_PLAN = plan_capabilities(HR_TWIN, CapabilityRegistry.from_directory())
MANAGER, VIEWER = "hr-db:role:hr_manager", "hr-db:role:hr_viewer"


def _compile(role_bindings=None):
    return compile_blueprint(HR_TWIN, HR_PLAN, connectors=TWINS.connector_refs("hr"), version=1,
                             name="Reference hr", role_bindings=role_bindings).blueprint


def _jwt(exp: float | None) -> str:
    def part(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    claims = {"sub": "alice"} if exp is None else {"sub": "alice", "exp": exp}
    return f"{part({'alg': 'RS256'})}.{part(claims)}.c2lnbmF0dXJl"


# ------------------------------------------------------------------------------- delegated reads


_TEMPLATE = ReadTemplate("api_get_employee", "hr-api", TemplateKind.HTTP,
                         http=HttpGet("/employees/{employee_id}", path_params={"employee_id": "employee_id"}))


def _descriptor(mode: IdentityMode = IdentityMode.DELEGATED, base: str = "https://hr.example.com/api") -> ConnectorDescriptor:
    return ConnectorDescriptor(id="hr-api", kind=ConnectorKind.OPENAPI, identity_mode=mode, display_name="HR API",
                               endpoint="https://hr.example.com/openapi.yaml", options={"base_url": base})


async def _read(identity: CallerIdentity | None, *, status: int = 200, mode: IdentityMode = IdentityMode.DELEGATED,
                credential: Credential | None = None, base: str = "https://hr.example.com/api"):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json={"id": 7, "full_name": "Employee 7"})

    async def resolve(host: str) -> list[str]:
        return ["93.184.216.34"]

    factory = OpenApiConnectorFactory(transport=httpx.MockTransport(handler), resolver=resolve)
    connector = await factory.open(_descriptor(mode, base), credential)
    try:
        return await connector.read(_TEMPLATE, {"employee_id": "7"}, identity=identity), seen
    except ToolExecutionError as exc:
        return exc, seen


def _person(token: str | None) -> CallerIdentity:
    return CallerIdentity(UserId("alice"), delegated_token=Credential(token) if token else None)


class TestDelegatedReads:
    async def test_the_callers_own_token_is_sent(self) -> None:
        token = _jwt(time.time() + 3600)
        data, seen = await _read(_person(token))
        assert data == {"id": 7, "full_name": "Employee 7"}
        assert len(seen) == 1 and seen[0].headers["authorization"] == f"Bearer {token}"

    async def test_an_opaque_token_is_left_to_the_target(self) -> None:
        data, seen = await _read(_person("opaque-token-abc"))
        assert data["id"] == 7 and seen[0].headers["authorization"] == "Bearer opaque-token-abc"

    @pytest.mark.parametrize("identity, reason", [
        (None, "delegated_token_missing"),
        (CallerIdentity(UserId("alice")), "delegated_token_missing"),
        (_person(_jwt(time.time() - 60)), "delegated_token_expired"),
        (_person(_jwt(time.time() + 5)), "delegated_token_expired"),  # inside the leeway
    ])
    async def test_no_usable_token_is_refused_before_anything_is_sent(self, identity, reason) -> None:
        error, seen = await _read(identity)
        assert isinstance(error, ToolExecutionError) and seen == []
        assert error.details["reason"] == reason and error.details["reauthenticate"] is True
        assert "sign in to it again" in str(error)

    @pytest.mark.parametrize("status", [401, 403])
    async def test_a_rejected_token_is_refused_and_never_retried(self, status) -> None:
        error, seen = await _read(_person(_jwt(time.time() + 3600)), status=status)
        assert isinstance(error, ToolExecutionError)
        assert error.details["reason"] == "delegated_token_rejected" and error.details["reauthenticate"]
        assert len(seen) == 1  # no anonymous or service-account second attempt

    async def test_a_delegated_connector_never_uses_a_stored_credential(self) -> None:
        error, seen = await _read(_person("caller-token"), credential=Credential("service-secret"))
        assert isinstance(error, ToolExecutionError) and error.details["reason"] == "delegated_misconfigured"
        assert seen == []

    async def test_a_token_is_never_sent_in_clear(self) -> None:
        error, seen = await _read(_person("caller-token"), base="http://hr.example.com/api")
        assert isinstance(error, ToolExecutionError) and error.details["reason"] == "credential_requires_https"
        assert seen == []

    async def test_a_service_account_connector_ignores_the_callers_token(self) -> None:
        data, seen = await _read(_person("caller-token"), mode=IdentityMode.SERVICE_ACCOUNT,
                                 credential=Credential("service-secret"))
        assert data["id"] == 7 and seen[0].headers["authorization"] == "Bearer service-secret"

    @pytest.mark.parametrize("token, expiry", [
        (_jwt(1_700_000_000), 1_700_000_000.0),
        (_jwt(None), None),
        ("opaque", None),
        ("a.%%%.c", None),
        ("a.e30.c", None),  # "{}"
        (_jwt(True), None),
    ])
    def test_expiry_is_read_only_from_a_jwt_exp(self, token, expiry) -> None:
        assert bearer_token_expiry(token) == expiry


# ------------------------------------------------------------------------------- the token's path


class _Recording:
    descriptor = None

    def __init__(self) -> None:
        self.identities: list[CallerIdentity | None] = []

    async def health(self):
        from aura_core.domain.connectors import ConnectorHealth, HealthStatus

        return ConnectorHealth(HealthStatus.HEALTHY, write_capable=False)

    async def read(self, template, arguments, identity=None):
        self.identities.append(identity)
        return [{"id": 1}]

    async def aclose(self) -> None:
        return None


class TestTokenPath:
    def test_a_token_reaches_only_its_own_connector(self) -> None:
        context = ToolContext(user_id=UserId("alice"), correlation_id="c", deadline_s=5.0,
                              capabilities=("project_data", f"project_role:{VIEWER}"),
                              credentials={connector_token_key("hr-api"): Credential("tok-api")})
        mine, other = caller_identity(context, "hr-api"), caller_identity(context, "crm-api")
        assert mine.delegated_token.reveal() == "tok-api" and other.delegated_token is None
        assert mine.roles == frozenset({VIEWER}) and mine.user_id == UserId("alice")
        assert "tok-api" not in repr(mine) and "tok-api" not in repr(context.credentials)

    async def test_a_generated_tool_passes_the_identity_to_the_read(self) -> None:
        connector = _Recording()

        async def open_connector(connector_id: str):
            return connector

        from aura_core.domain.tools import ToolSpec

        tool = GeneratedReadTool(ToolSpec(name="bp1_api_get_employee", description="d"), _TEMPLATE, open_connector)
        context = ToolContext(user_id=UserId("alice"), correlation_id="c", deadline_s=5.0,
                              credentials={connector_token_key("hr-api"): Credential("tok-api")})
        await tool.execute({"employee_id": "7"}, context)
        assert connector.identities[0].delegated_token.reveal() == "tok-api"

    def test_the_kernel_carries_tokens_into_the_tool_context_only(self, container) -> None:
        from aura_core.kernel.kernel import TurnRequest

        request = TurnRequest(message="hi", user_id=UserId("alice"),
                              connector_tokens={"hr-api": Credential("tok-api")})
        context = container.kernel._tool_context(request, False)
        assert context.credentials[connector_token_key("hr-api")].reveal() == "tok-api"
        assert "tok-api" not in repr(request)

    @pytest.mark.parametrize("headers, expected", [
        ([], {}),
        (["hr-api=tok1"], {"hr-api": "tok1"}),
        (["hr-api=tok1, crm=a.b.c=="], {"hr-api": "tok1", "crm": "a.b.c=="}),
        (["hr-api=tok1", "crm=tok2"], {"hr-api": "tok1", "crm": "tok2"}),
    ])
    def test_the_header_is_parsed(self, headers, expected) -> None:
        from aura_core.api.v3 import _connector_tokens

        parsed = _connector_tokens(_Headers(headers))
        assert {k: v.reveal() for k, v in parsed.items()} == expected

    @pytest.mark.parametrize("headers", [
        ["no-separator"], ["Bad Id=tok"], ["hr-api="], ["hr-api=tok with space"], ["hr-api=a", "hr-api=b"],
        [",".join(f"c{i}=t" for i in range(9))], ["hr-api=" + "x" * 9000],
    ])
    def test_a_bad_header_is_refused_without_echoing_the_token(self, headers) -> None:
        from aura_core.api.v3 import _connector_tokens

        with pytest.raises(ValidationError) as excinfo:
            _connector_tokens(_Headers(headers))
        assert "tok with space" not in str(excinfo.value.to_dict()) and "x" * 50 not in str(excinfo.value.to_dict())


class _Headers:
    def __init__(self, values: list[str]) -> None:
        self.headers = self
        self._values = values

    def getlist(self, name: str) -> list[str]:
        assert name == "x-aura-connector-token"
        return list(self._values)


# ------------------------------------------------------------------------------- role bindings


class TestRoleBindings:
    def test_no_bindings_changes_nothing(self) -> None:
        bp = _compile()
        assert bp.security.role_bindings == () and "role_bindings" not in json.dumps(bp.to_data())
        assert bp.digest == TWINS.compile_pilot("hr").blueprint.digest
        assert {tool_capability(bp, t) for t in bp.tools} == {PROJECT_DATA_CAPABILITY}
        assert all(agent_capabilities(bp, a) == {PROJECT_DATA_CAPABILITY} for a in bp.agents)
        assert grants_by_aura_role(bp) == {}

    def test_bindings_are_compiled_into_the_signed_blueprint(self) -> None:
        bp = _compile({"HR-Manager": [MANAGER], "HR-Staff": [VIEWER]})
        assert set(bp.security.role_bindings) == {RoleBinding("HR-Manager", MANAGER), RoleBinding("HR-Staff", VIEWER)}
        assert bp.digest != _compile().digest
        assert type(bp).from_data(bp.to_data()) == bp

    @pytest.mark.parametrize("bindings, message", [
        ({"HR-Manager": ["hr-db:role:payroll"]}, "no discovered role"),
        ({"HR-Manager": []}, "non-empty list"),
        ({"HR-Manager": MANAGER}, "non-empty list"),
        ({"bad role!": [MANAGER]}, "AURA role name"),
    ])
    def test_a_bad_binding_is_refused(self, bindings, message) -> None:
        with pytest.raises(ValidationError, match=message):
            _compile(bindings)

    def test_a_binding_to_an_unused_role_is_refused_by_the_blueprint_too(self) -> None:
        bp = _compile({"HR-Manager": [MANAGER]})
        with pytest.raises(ValidationError):
            dataclasses.replace(bp, security=SecuritySection(bp.security.permission_map,
                                                             (RoleBinding("X", "hr-db:role:ghost"),)))

    def test_each_aura_role_gets_its_source_roles_grants(self) -> None:
        bp = _compile({"HR-Manager": [MANAGER], "HR-Staff": [VIEWER]})
        grants = grants_by_aura_role(bp)
        manager = {c for c in grants["HR-Manager"] if c.startswith("project_data:")}
        viewer = {c for c in grants["HR-Staff"] if c.startswith("project_data:")}
        assert manager == {f"project_data:{g.capability}" for g in bp.security.permission_map if g.role == MANAGER}
        assert manager < viewer  # the viewer role reads more tables than the manager role
        assert f"project_role:{MANAGER}" in grants["HR-Manager"] and f"project_role:{VIEWER}" not in grants["HR-Manager"]

    @pytest.mark.parametrize("capabilities, role, expected_extra", [
        ((), "HR-Manager", False),                                   # no project_data: nothing added
        (("project_data",), "Somebody", False),                      # unbound role: nothing added
        (("project_data",), "HR-Manager", True),
    ])
    def test_expansion_needs_project_data_and_a_binding(self, capabilities, role, expected_extra) -> None:
        grants = grants_by_aura_role(_compile({"HR-Manager": [MANAGER]}))
        expanded = expand_capabilities(capabilities, role, grants)
        assert (len(expanded) > len(capabilities)) is expected_extra

    def test_a_self_asserted_role_capability_is_stripped(self) -> None:
        grants = grants_by_aura_role(_compile({"HR-Manager": [MANAGER]}))
        forged = ("project_data", f"project_role:{VIEWER}", "project_data:entity.lookup@hr-db:table:public.assets")
        expanded = expand_capabilities(forged, "Nobody", grants)
        assert expanded == ("project_data",)

    def _runtime(self, bp):
        tools = ToolRegistry(policy=ToolPolicy())
        connector = _Recording()

        async def open_connector(connector_id: str):
            return connector

        BlueprintToolMaterializer(tools=tools, open_connector=open_connector, grace_s=0).register(bp)
        return tools, connector

    async def test_a_tool_outside_the_bound_roles_grant_is_refused(self) -> None:
        bp = _compile({"HR-Manager": [MANAGER], "HR-Staff": [VIEWER]})
        tools, connector = self._runtime(bp)
        grants = grants_by_aura_role(bp)
        manager = expand_capabilities(("project_data",), "HR-Manager", grants)
        viewer = expand_capabilities(("project_data",), "HR-Staff", grants)
        names = bp.tool_registry_names()
        managers = {g.capability for g in bp.security.permission_map if g.role == MANAGER}
        viewer_only = next(t for t in bp.tools if t.capability not in managers and t.connector == "hr-db"
                           and not any(p.required for p in t.parameters))
        call = ToolCall(ToolCallId("c1"), names[viewer_only.name], {})

        def context(caps):
            return ToolContext(user_id=UserId("u"), correlation_id="c", deadline_s=5.0, capabilities=caps)

        refused, _ = await tools.execute(call, context(manager), capabilities=manager)
        allowed, _ = await tools.execute(call, context(viewer), capabilities=viewer)
        assert refused.ok is False and refused.metadata.get("allowed") is False
        assert allowed.ok is True and len(connector.identities) == 1
        assert names[viewer_only.name] not in {s.name for s in tools.available(capabilities=manager)}
        # A project_data holder with no binding sees no role-governed tool at all.
        governed = {names[t.name] for t in bp.tools
                    if t.capability in {g.capability for g in bp.security.permission_map}}
        assert governed and not governed & {s.name for s in tools.available(capabilities=("project_data",))}

    def test_a_role_agent_is_only_for_its_role(self) -> None:
        bp = _compile({"HR-Manager": [MANAGER], "HR-Staff": [VIEWER]})
        names = bp.tool_registry_names()
        registry = ActiveBlueprint(build_default_agent_registry())
        registry.swap([generated_agent_spec(a, names, blueprint=bp) for a in bp.agents], version=1, digest=bp.digest)
        grants = grants_by_aura_role(bp)
        manager = expand_capabilities(("project_data",), "HR-Manager", grants)
        viewer = expand_capabilities(("project_data",), "HR-Staff", grants)
        assert registry.resolve("bp_role_hr_manager", capabilities=manager).id == "bp_role_hr_manager"
        assert registry.resolve("bp_role_hr_manager", capabilities=viewer).id == "orchestrator"
        assert registry.resolve("bp_role_hr_viewer", capabilities=viewer).id == "bp_role_hr_viewer"
        assert registry.resolve("bp_project", capabilities=("project_data",)).id == "bp_project"

    def test_the_gate_acts_with_every_grant(self) -> None:
        bp = _compile({"HR-Manager": [MANAGER]})
        grants = set(all_grants(bp))
        assert {tool_capability(bp, t) for t in bp.tools} <= grants
        assert all(agent_capabilities(bp, a) <= grants for a in bp.agents)
        assert all_grants(_compile()) == ("project_data",)


async def test_the_deployment_grants_from_the_version_it_serves(audit, clock) -> None:
    """``BlueprintDeployment.caller_capabilities`` reads the snapshot this process serves: nothing
    before a blueprint is swapped in, the bound grants after it."""
    from aura_core.adaptation.deployment import BlueprintDeployment
    from aura_core.adapters.memory_adaptation import InMemoryBlueprintStore
    from aura_core.domain.blueprint import BlueprintState
    from aura_core.domain.compilation import BlueprintRecord
    from aura_core.learning.approval import ApprovalService, InMemoryApprovalStore

    compiled = compile_blueprint(HR_TWIN, HR_PLAN, connectors=TWINS.connector_refs("hr"), version=1,
                                 role_bindings={"HR-Manager": [MANAGER]})
    bp = compiled.blueprint
    tools = ToolRegistry(policy=ToolPolicy())

    async def open_connector(connector_id: str):
        return _Recording()

    store = InMemoryBlueprintStore()
    deployment = BlueprintDeployment(
        project_id="p0", store=store, approvals=ApprovalService(store=InMemoryApprovalStore(), audit=audit),
        agents=ActiveBlueprint(build_default_agent_registry()), clock=clock, audit=audit,
        materializer=BlueprintToolMaterializer(tools=tools, open_connector=open_connector, grace_s=0),
        gate=None, twins=None)
    assert deployment.caller_capabilities(("project_data",), "HR-Manager") == ("project_data",)
    record = BlueprintRecord(project_id=bp.project.id, version=1, state=BlueprintState.PROPOSED, digest=bp.digest,
                             created_at="2026-10-18T00:00:00+00:00", created_by="sa@example.com",
                             plan_digest="sha256:" + "1" * 64, registry_digest="sha256:" + "2" * 64, blueprint=bp,
                             suites=compiled.suites, dropped=compiled.dropped)
    deployment._swap_agents(record)  # what a deploy and a worker's sync both end in
    granted = deployment.caller_capabilities(("project_data",), "HR-Manager")
    assert f"project_role:{MANAGER}" in granted
    assert deployment.caller_capabilities(("project_data",), "HR-Staff") == ("project_data",)


def test_tool_capability_is_unchanged_for_capabilities_no_role_governs() -> None:
    bp = _compile({"HR-Manager": [MANAGER]})
    trimmed = dataclasses.replace(bp, security=SecuritySection(
        tuple(g for g in bp.security.permission_map if not g.capability.startswith("operation.read")),
        bp.security.role_bindings))
    for tool in trimmed.tools:
        governed = any(g.capability == tool.capability for g in trimmed.security.permission_map)
        assert (tool_capability(trimmed, tool) == PROJECT_DATA_CAPABILITY) is (not governed)



# ------------------------------------------------------------------------------- over HTTP


class TestOverHttp:
    """Through the real gateway authenticator and the v3 chat route."""

    @pytest.fixture
    def client(self, container, monkeypatch):
        from fastapi.testclient import TestClient
        from services import jwt_auth, rbac  # type: ignore

        from aura_core.api import build_app
        from aura_service import _build_authenticator

        async def verify(request, credentials):
            return {"uid": "alice", "email": "alice@example.com"}

        monkeypatch.setattr(jwt_auth, "verify_access_token", verify)
        monkeypatch.setattr(rbac, "get_user_role", lambda claims: "HR-Manager")
        container.settings = dataclasses.replace(container.settings, security=dataclasses.replace(
            container.settings.security, project_data_roles=("HR-Manager",)))
        seen: list[Any] = []
        real = container.kernel.run

        async def run(turn):
            seen.append(turn)
            return await real(turn)

        monkeypatch.setattr(container.kernel, "run", run)
        authenticator = _build_authenticator(container.settings, service_keys=container.service_keys)
        with TestClient(build_app(container, authenticator=authenticator)) as test_client:
            yield test_client, seen

    def test_the_header_reaches_the_turn_and_no_response_echoes_it(self, client) -> None:
        test_client, seen = client
        token = _jwt(time.time() + 3600)
        response = test_client.post("/api/v3/chat", json={"message": "hello", "use_tools": False},
                                    headers={"authorization": "Bearer person", "x-aura-connector-token": f"hr-api={token}"})
        assert response.status_code == 200, response.text
        assert seen[0].connector_tokens["hr-api"].reveal() == token
        assert "project_data" in seen[0].capabilities
        assert token not in response.text and token not in repr(seen[0])

    def test_a_malformed_header_is_a_422_that_names_no_token(self, client) -> None:
        test_client, _ = client
        response = test_client.post("/api/v3/chat", json={"message": "hello"},
                                    headers={"authorization": "Bearer person", "x-aura-connector-token": "hr-api=a b"})
        assert response.status_code == 422 and "a b" not in response.text


class _Body:
    def __init__(self, raw: bytes) -> None:
        self._raw = raw

    async def body(self) -> bytes:
        return self._raw


@pytest.mark.parametrize("raw, expected", [
    (b"", None),
    (b"{}", None),
    (b'{"role_bindings": {}}', None),
    (b'{"role_bindings": {"HR-Manager": ["hr-db:role:hr_manager"]}}', {"HR-Manager": ["hr-db:role:hr_manager"]}),
])
async def test_the_compile_body_takes_role_bindings(raw, expected) -> None:
    from aura_core.api.admin_adaptation import _role_bindings_body

    assert await _role_bindings_body(_Body(raw)) == expected


@pytest.mark.parametrize("raw", [b"[]", b"not json", b'{"other": 1}', b'{"role_bindings": {"A": "x"}}',
                                 b'{"role_bindings": {"A": [1]}}', b'{"role_bindings": ["A"]}'])
async def test_a_bad_compile_body_is_refused(raw) -> None:
    from aura_core.api.admin_adaptation import _role_bindings_body

    with pytest.raises(ValidationError):
        await _role_bindings_body(_Body(raw))
