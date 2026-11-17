"""SaaS P1-A: the gateway's authenticator turns ``X-Aura-Workspace`` into a checked workspace.

Runs the real ``aura_service._build_authenticator`` over the container's real TenancyService and
in-memory store. Only the person identity boundary (``services.jwt_auth`` / ``services.rbac``) is
stood in for: the bearer token's text *is* the subject, and every person is a plain ``User``.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

import services.jwt_auth as jwt_auth
import services.rbac as rbac
from aura_core.adapters.tenancy import InMemoryTenancyStore
from aura_core.domain.errors import AuthorizationError
from aura_core.domain.identifiers import UserId
from aura_core.domain.tenancy import WorkspaceKind, WorkspaceRole, workspace_scope
from aura_service import _build_authenticator


class _Headers(dict):
    def get(self, key, default=None):  # case-insensitive, like Starlette's
        return super().get(key.lower(), default)


class _Request:
    def __init__(self, token: str, workspace: str | None = None) -> None:
        headers = {"authorization": f"Bearer {token}"}
        if workspace is not None:
            headers["x-aura-workspace"] = workspace
        self.headers = _Headers(headers)


class _CountingStore(InMemoryTenancyStore):
    def __init__(self) -> None:
        super().__init__()
        self.lookups = 0

    async def get_membership(self, workspace_id, user_id):
        self.lookups += 1
        return await super().get_membership(workspace_id, user_id)


@pytest.fixture(autouse=True)
def _people(monkeypatch):
    async def verify(request, credentials):
        return {"uid": credentials.credentials, "email": f"{credentials.credentials}@example.com"}

    monkeypatch.setattr(jwt_auth, "verify_access_token", verify)
    monkeypatch.setattr(rbac, "get_user_role", lambda claims: "User")


@pytest.fixture
def store(container) -> _CountingStore:
    counting = _CountingStore()
    container.tenancy.store = counting
    return counting


@pytest.fixture
def authenticate(container, store):
    return _build_authenticator(
        container.settings, service_keys=container.service_keys, tenancy=container.tenancy
    )


async def test_no_header_is_personal_scope_without_store_call(authenticate, store) -> None:
    principal = await authenticate(_Request("alice"))
    assert principal.workspace is None
    assert principal.knowledge_owner == UserId("alice")
    assert store.lookups == 0


async def test_team_header_sets_knowledge_owner(container, authenticate) -> None:
    team = await container.tenancy.create_team("alice", "Acme")
    principal = await authenticate(_Request("alice", team.id))
    assert principal.workspace.workspace_id == team.id
    assert principal.workspace.kind is WorkspaceKind.TEAM
    assert principal.workspace.role is WorkspaceRole.OWNER
    assert principal.knowledge_owner == UserId(workspace_scope(team.id))
    assert principal.user_id == UserId("alice")


async def test_personal_workspace_header_keeps_the_person_scope(container, authenticate) -> None:
    personal = await container.tenancy.ensure_personal("alice")
    principal = await authenticate(_Request("alice", personal.id))
    assert principal.workspace.kind is WorkspaceKind.PERSONAL
    assert principal.knowledge_owner == UserId("alice")


async def test_removed_member_403_on_next_request(container, authenticate) -> None:
    team = await container.tenancy.create_team("alice", "Acme")
    _invite, token = await container.tenancy.invite("alice", team.id, "bob@example.com", WorkspaceRole.MEMBER)
    await container.tenancy.accept("bob", token)
    assert (await authenticate(_Request("bob", team.id))).workspace.role is WorkspaceRole.MEMBER
    await container.tenancy.remove("alice", team.id, "bob")
    with pytest.raises(AuthorizationError):
        await authenticate(_Request("bob", team.id))


async def test_unknown_and_foreign_workspace_identical_403(container, authenticate) -> None:
    team = await container.tenancy.create_team("alice", "Acme")
    errors = []
    for workspace in (team.id, "0" * 32, "../../etc"):
        with pytest.raises(AuthorizationError) as caught:
            await authenticate(_Request("mallory", workspace))
        errors.append((caught.value.http_status, caught.value.to_dict()))
    assert errors[0][0] == 403 and errors[0] == errors[1] == errors[2]


async def test_workspace_owner_gets_no_admin_capability(container, authenticate) -> None:
    from fastapi.testclient import TestClient

    from aura_core.api import build_app

    team = await container.tenancy.create_team("alice", "Acme")
    principal = await authenticate(_Request("alice", team.id))
    assert not any(c.startswith("admin.") for c in principal.capabilities)
    assert principal.role == "User" and not principal.is_admin
    with TestClient(build_app(container, authenticator=authenticate)) as client:
        response = client.get(
            "/api/v3/admin/service-keys",
            headers={"authorization": "Bearer alice", "x-aura-workspace": team.id},
        )
    assert response.status_code == 403


async def test_service_key_with_workspace_header_refused(container, authenticate) -> None:
    team = await container.tenancy.create_team("alice", "Acme")
    _key, token = await container.service_keys.issue(name="svc-test", by="sa@example.com")
    with pytest.raises((AuthorizationError, HTTPException)):
        await authenticate(_Request(token, team.id))
