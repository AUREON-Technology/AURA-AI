"""Admin erasure against a REAL Postgres (testcontainers, like ``test_insights_integration.py``).

``POST /api/v3/admin/users/{subject}/forget`` used to soft-delete six sync tables -- leaving the
ciphertext, projects, goals, insights and the GitHub link -- and answer ``forgotten: true``. It
now erases the aura_core stores, revokes sessions and devices, and deletes the ``users`` row,
which removes every v2 row through the schema's ``ON DELETE CASCADE``. This checks the cascade
on a real database, where it lives.

Skips itself cleanly when Docker isn't reachable.
"""
import asyncio
import os
import unittest

import legacy_postgres
import uuid
from datetime import datetime, timezone


ROOT = "root-erase@example.com"


@unittest.skipUnless(legacy_postgres.available(), legacy_postgres.SKIP_REASON)
class TestErasureIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.postgres = legacy_postgres.container()
        cls.postgres.start()
        # Registered as a cleanup, not left to tearDownClass: if setUpClass raises below, unittest
        # never runs tearDownClass and the database would stay behind on the server.
        cls.addClassCleanup(cls.postgres.stop)
        from config import settings

        settings.database_url = cls.postgres.get_connection_url().replace(
            "postgresql+psycopg2", "postgresql+asyncpg"
        )
        import infrastructure.db.base as db_base
        import infrastructure.db.models  # noqa: F401
        from infrastructure.db.base import Base, get_engine

        db_base._engine = None
        db_base._session_factory = None

        async def create():
            engine = get_engine()
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            await engine.dispose()

        asyncio.run(create())

    def setUp(self):
        import infrastructure.db.base as db_base
        import services.jwt_auth as jwt_auth

        db_base._engine = None
        db_base._session_factory = None
        self._env = os.environ.get("SUPERADMIN_EMAILS")
        os.environ["SUPERADMIN_EMAILS"] = ROOT
        self._redis = jwt_auth.get_redis_client
        jwt_auth.get_redis_client = lambda: None

    def tearDown(self):
        import services.jwt_auth as jwt_auth

        jwt_auth.get_redis_client = self._redis
        if self._env is None:
            os.environ.pop("SUPERADMIN_EMAILS", None)
        else:
            os.environ["SUPERADMIN_EMAILS"] = self._env

    def test_forget_deletes_the_user_and_everything_they_own(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from sqlalchemy import func, select

        from api.admin_platform import router
        from aura_core.config import load_settings
        from aura_core.container import Container
        from aura_core.domain.identifiers import UserId
        from infrastructure.db.base import get_session_factory
        from infrastructure.db.models import Goal, Insight, Message, Project, Task, User
        from services.jwt_auth import is_device_revoked, issue_device_token, issue_token_pair
        from services.rbac import stamp_resolved_role

        subject = f"forget-{uuid.uuid4().hex[:6]}"
        app = FastAPI()
        app.include_router(router)
        app.state.aura = Container(load_settings(refresh=True, validate=False))
        # A verified Google sign-in, as verify_identity_token reports it: since SEC-08 the
        # SUPERADMIN_EMAILS match needs both facts.
        root = issue_token_pair(stamp_resolved_role({
            "uid": "root", "email": ROOT, "email_verified": True, "sign_in_provider": "google.com",
        }))
        device = issue_device_token({"uid": subject, "email": f"{subject}@example.com"}, device_id="dev-x")

        with TestClient(app) as client:
            async def seed():
                await app.state.aura.memory.remember(
                    owner_id=UserId(subject), content="likes tea", subject="drink"
                )
                async with get_session_factory()() as db:
                    user = User(auth_subject=subject, email=f"{subject}@example.com")
                    db.add(user)
                    await db.flush()
                    project = Project(id=uuid.uuid4(), user_id=user.id, title_enc="gcm:p")
                    db.add(project)
                    await db.flush()
                    db.add_all([
                        Task(id=uuid.uuid4(), user_id=user.id, title_enc="gcm:t"),
                        Goal(id=uuid.uuid4(), user_id=user.id, title_enc="gcm:g",
                             project_id=project.id),
                        Message(id=uuid.uuid4(), user_id=user.id, conversation_id="c1",
                                role="user", content_enc="gcm:m"),
                        Insight(id=uuid.uuid4(), user_id=user.id, kind="hydration",
                                payload_json="{}"),
                    ])
                    await db.commit()
                    return user.id

            user_id = client.portal.call(seed)
            response = client.post(
                f"/api/v3/admin/users/{subject}/forget",
                headers={"Authorization": f"Bearer {root['access_token']}"},
            )
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()

            async def remaining():
                async with get_session_factory()() as db:
                    counts = {}
                    for model in (User, Task, Project, Goal, Message, Insight):
                        column = model.id if model is User else model.user_id
                        counts[model.__tablename__] = await db.scalar(
                            select(func.count()).select_from(model).where(column == user_id)
                        )
                    return counts

            left = client.portal.call(remaining)

        self.assertTrue(body["forgotten"], body)
        self.assertEqual(body["failed"], [])
        self.assertEqual(body["counts"]["memories"], 1)
        self.assertEqual(body["counts"]["devices"], 1)
        self.assertEqual(body["counts"]["user"], 1)
        self.assertTrue(body["not_erased"])
        self.assertEqual(set(left.values()), {0}, left)
        self.assertTrue(is_device_revoked(device["device_id"]))


if __name__ == "__main__":
    unittest.main()
