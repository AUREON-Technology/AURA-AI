"""INT-107 / INT-106 (stream B3): the Access page's mutations are audited, and the operator directory
answers "could anyone else approve this?".

* INT-107 -- ``assign_role``, ``revoke_sessions`` and ``forget_user`` changed who can do what (or
  erased a person) and left no audit event, unlike every admin route in ``aura_core/api/v3.py``
  (``audit_admin``). Each now records one, with the operator as actor and the target named by auth
  subject -- or, for an erasure, by the same pseudonym the erasure gives the erased person's own
  events, so the record of erasing someone does not keep their identity.
* INT-106 -- ``other_approver_exists`` is the directory check ``ApprovalService`` takes: whether
  anyone besides the requester holds the role that may decide an approval of a given kind.

The erasure and env-list cases run on the in-memory substrate. Role assignment and session
revocation need the ``users`` table, so those run against a real Postgres built by
``alembic upgrade head`` when ``AURA_TEST_DATABASE_URL`` is set, and skip otherwise.
"""

from __future__ import annotations

import hashlib
import os
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import services.jwt_auth as jwt_auth
from api import admin_platform

ROOT = "root@example.com"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SUPERADMIN_EMAILS", ROOT)
    monkeypatch.delenv("ADMIN_EMAILS", raising=False)
    monkeypatch.delenv("DEVELOPER_EMAILS", raising=False)
    monkeypatch.setattr(jwt_auth, "get_redis_client", lambda: None)


@pytest.fixture
def no_database(monkeypatch):
    import infrastructure.db.base as db_base

    monkeypatch.setattr(db_base, "is_configured", lambda: False)


def _app(container) -> TestClient:
    app = FastAPI()
    app.include_router(admin_platform.router)
    app.state.aura = container
    return TestClient(app)


def _root_headers() -> dict:
    pair = jwt_auth.issue_token_pair({
        "uid": "uid-root", "email": ROOT, "email_verified": True, "role": "SuperAdmin",
    })
    return {"Authorization": f"Bearer {pair['access_token']}", "X-Correlation-ID": "corr-1"}


# --- INT-107: forget ------------------------------------------------------------------------------


class TestForgetIsAudited:
    async def test_an_erasure_records_who_erased_whom_without_naming_them(self, container, no_database) -> None:
        subject = f"gone-{uuid.uuid4().hex[:6]}"

        response = _app(container).post(f"/api/v3/admin/users/{subject}/forget", headers=_root_headers())

        assert response.status_code == 200, response.text
        [event] = await container.audit.query(action="admin.user.forget")
        assert event.actor == ROOT
        assert event.actor_role == "SuperAdmin"
        pseudonym = "erased:" + hashlib.sha256(subject.encode("utf-8")).hexdigest()[:12]
        assert event.resource == f"user:{pseudonym}"
        assert subject not in repr(vars_of(event)), "the erased subject must not survive in its own audit record"
        assert event.outcome == "ok"
        assert event.correlation_id == "corr-1"
        assert event.detail["forgotten"] is True

    async def test_a_partial_erasure_is_recorded_as_one(self, container, no_database, monkeypatch) -> None:
        async def broken(_owner):
            raise RuntimeError("vector store down")

        monkeypatch.setattr(container.memory, "forget_owner", broken)

        response = _app(container).post("/api/v3/admin/users/someone/forget", headers=_root_headers())

        assert response.status_code == 200, response.text
        [event] = await container.audit.query(action="admin.user.forget")
        assert event.outcome == "partial"
        assert "memories" in event.detail["failed"]


def vars_of(event) -> dict:
    return {name: getattr(event, name) for name in event.__slots__}


# --- INT-106: who else could approve ----------------------------------------------------------


def test_the_superadmin_kinds_match_the_decide_routes_gate() -> None:
    """The directory's notion of who may decide what is the decide route's own (no drift)."""
    from aura_core.api import admin

    assert {str(kind) for kind in admin._SUPER_ADMIN_APPROVAL_KINDS} == admin_platform._SUPER_ADMIN_APPROVAL_KINDS


class TestOtherApproverExistsFromTheEnvLists:
    async def test_a_sole_superadmin_has_nobody_else(self, no_database) -> None:
        assert await admin_platform.other_approver_exists(ROOT, "model_promotion") is False

    async def test_a_second_superadmin_is_someone_else(self, no_database, monkeypatch) -> None:
        monkeypatch.setenv("SUPERADMIN_EMAILS", f"{ROOT},peer@example.com")
        assert await admin_platform.other_approver_exists(ROOT, "connector_registration") is True

    async def test_an_admin_counts_for_admin_kinds_but_not_superadmin_kinds(self, no_database, monkeypatch) -> None:
        monkeypatch.setenv("ADMIN_EMAILS", "ops@example.com")
        assert await admin_platform.other_approver_exists(ROOT, "model_promotion") is True
        assert await admin_platform.other_approver_exists(ROOT, "blueprint_deployment") is False

    async def test_a_developer_is_not_an_approver(self, no_database, monkeypatch) -> None:
        monkeypatch.setenv("DEVELOPER_EMAILS", "dev@example.com")
        assert await admin_platform.other_approver_exists(ROOT, "finetune") is False


# --- INT-107: assign and revoke, against a real Postgres -------------------------------------------

PG_URL = os.environ.get("AURA_TEST_DATABASE_URL", "")
needs_postgres = pytest.mark.skipif(not PG_URL, reason="AURA_TEST_DATABASE_URL not set (needs a throwaway Postgres)")


@pytest.fixture
def database(monkeypatch):
    """A fresh database migrated to head, as the legacy SQLAlchemy layer's ``DATABASE_URL``."""
    import asyncio

    import asyncpg

    import infrastructure.db.base as db_base
    from config import settings
    from tests.postgres_migrations import migrate, with_database

    name = f"b3_admin_audit_{uuid.uuid4().hex[:10]}"
    admin_url = PG_URL.replace("postgresql+asyncpg://", "postgresql://", 1)

    async def admin(sql: str) -> None:
        conn = await asyncpg.connect(admin_url)
        try:
            await conn.execute(sql)
        finally:
            await conn.close()

    asyncio.run(admin(f'CREATE DATABASE "{name}"'))
    url = with_database(admin_url, name)
    try:
        migrate(url)
        monkeypatch.setattr(settings, "database_url", url.replace("postgresql://", "postgresql+asyncpg://", 1))
        monkeypatch.setattr(db_base, "_engine", None)
        monkeypatch.setattr(db_base, "_session_factory", None)
        yield url
    finally:
        db_base._engine = None
        db_base._session_factory = None
        asyncio.run(admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def _add_user(client: TestClient, email: str, subject: str, *, role: str = "User") -> None:
    from datetime import datetime, timezone

    from infrastructure.db.base import get_session_factory
    from infrastructure.db.models import User

    async def run():
        async with get_session_factory()() as db:
            db.add(User(auth_subject=subject, email=email, display_name=email, role=role,
                        role_assigned_at=datetime.now(timezone.utc) if role != "User" else None))
            await db.commit()

    client.portal.call(run)


@needs_postgres
class TestAccessPageMutationsAreAudited:
    async def test_assigning_a_role_is_recorded(self, container, database) -> None:
        with _app(container) as client:
            _add_user(client, "dev@example.com", "uid-dev")
            response = client.patch("/api/v3/admin/operators/dev@example.com/role",
                                    json={"role": "Admin"}, headers=_root_headers())
        assert response.status_code == 200, response.text
        [event] = await container.audit.query(action="admin.operator.role")
        assert event.actor == ROOT
        assert event.resource == "user:uid-dev"
        assert event.outcome == "ok"
        assert event.detail["from"] == "User" and event.detail["to"] == "Admin"

    async def test_a_demotion_records_the_sessions_it_ended(self, container, database) -> None:
        with _app(container) as client:
            _add_user(client, "ops@example.com", "uid-ops", role="Admin")
            jwt_auth.issue_token_pair({"uid": "uid-ops", "email": "ops@example.com"})
            response = client.patch("/api/v3/admin/operators/ops@example.com/role",
                                    json={"role": "User"}, headers=_root_headers())
        assert response.status_code == 200, response.text
        [event] = await container.audit.query(action="admin.operator.role")
        assert event.detail["to"] == "User"
        assert event.detail["revoked"]["sessions"] >= 1

    async def test_revoking_sessions_is_recorded(self, container, database) -> None:
        with _app(container) as client:
            _add_user(client, "ops@example.com", "uid-ops")
            jwt_auth.issue_token_pair({"uid": "uid-ops", "email": "ops@example.com"})
            response = client.post("/api/v3/admin/operators/ops@example.com/revoke-sessions",
                                   headers=_root_headers())
        assert response.status_code == 200, response.text
        [event] = await container.audit.query(action="admin.operator.revoke_sessions")
        assert event.actor == ROOT
        assert event.resource == "user:uid-ops"
        assert event.detail == {"revoked": 1}

    async def test_a_refused_assignment_records_nothing(self, container, database) -> None:
        with _app(container) as client:
            response = client.patch("/api/v3/admin/operators/nobody@example.com/role",
                                    json={"role": "Admin"}, headers=_root_headers())
        assert response.status_code == 404
        assert await container.audit.query(action="admin.operator") == []

    async def test_an_assigned_admin_is_another_approver(self, container, database) -> None:
        with _app(container) as client:
            _add_user(client, "ops@example.com", "uid-ops", role="Admin")
            others = client.portal.call(admin_platform.other_approver_exists, ROOT, "model_promotion")
            only_self = client.portal.call(admin_platform.other_approver_exists, "ops@example.com",
                                           "blueprint_deployment")
        assert others is True
        assert only_self is True, "the env-list SuperAdmin can decide what ops@ requested"
