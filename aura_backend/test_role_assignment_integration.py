"""Access-page role assignment against a REAL Postgres (same testcontainers pattern as
``test_insights_integration.py``).

``users.role`` used to be written on every sign-in from the token's resolved role and never read
back, so a SuperAdmin assigning or removing a role on the Access page changed nothing. This drives
the real routes -- assign, refresh, list -- through the real ORM and checks the role a refreshed
token actually carries.

Skips itself cleanly when Docker isn't reachable.
"""
import asyncio
import os
import unittest

import legacy_postgres
import uuid


ROOT = "root@example.com"
ENV_ADMIN = "env-admin@example.com"


@unittest.skipUnless(legacy_postgres.available(), legacy_postgres.SKIP_REASON)
class TestRoleAssignmentIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.postgres = legacy_postgres.container()
        cls.postgres.start()
        # Registered as a cleanup, not left to tearDownClass: if setUpClass raises below, unittest
        # never runs tearDownClass and the database would stay behind on the server.
        cls.addClassCleanup(cls.postgres.stop)
        async_url = cls.postgres.get_connection_url().replace(
            "postgresql+psycopg2", "postgresql+asyncpg"
        )

        from config import settings

        settings.database_url = async_url

        import infrastructure.db.base as db_base
        import infrastructure.db.models  # noqa: F401 -- registers the tables on Base.metadata
        from infrastructure.db.base import Base, get_engine

        db_base._engine = None
        db_base._session_factory = None

        async def _create_schema():
            engine = get_engine()
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            await engine.dispose()

        asyncio.run(_create_schema())
        db_base._engine = None
        db_base._session_factory = None

    def setUp(self):
        import infrastructure.db.base as db_base
        import services.jwt_auth as jwt_auth

        # Each test's TestClient runs its own event loop; an engine cached from the previous
        # test is bound to a loop that is gone.
        db_base._engine = None
        db_base._session_factory = None

        self._env = {k: os.environ.get(k) for k in ("SUPERADMIN_EMAILS", "ADMIN_EMAILS")}
        os.environ["SUPERADMIN_EMAILS"] = ROOT
        os.environ["ADMIN_EMAILS"] = ENV_ADMIN
        self._redis = jwt_auth.get_redis_client
        jwt_auth.get_redis_client = lambda: None

    def tearDown(self):
        import services.jwt_auth as jwt_auth

        jwt_auth.get_redis_client = self._redis
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from api.admin_platform import router as admin_router
        from api.v2.auth import router as auth_router

        app = FastAPI()
        app.include_router(auth_router)
        app.include_router(admin_router)
        return TestClient(app)

    def _sign_in(self, client, email):
        """What every login route does: persist the row, resolve the role, issue the pair."""
        from infrastructure.db.base import get_session_factory
        from infrastructure.db.user_repo import get_or_create_user
        from services.jwt_auth import issue_token_pair
        from services.rbac import stamp_role

        # What services.security.verify_identity_token reports for a Google sign-in: since
        # SEC-08 an email-list role needs a verified address from Google sign-in.
        user_info = {
            "uid": f"uid-{email}", "email": email, "name": email,
            "email_verified": True, "sign_in_provider": "google.com",
        }

        async def run():
            async with get_session_factory()() as db:
                await get_or_create_user(db, user_info)
                await db.commit()
            return issue_token_pair(await stamp_role(dict(user_info)))

        return client.portal.call(run)

    @staticmethod
    def _role(pair):
        import jwt

        return jwt.decode(pair["access_token"], options={"verify_signature": False})["role"]

    def _refresh(self, client, pair):
        response = client.post("/api/v3/auth/refresh", json={"refresh_token": pair["refresh_token"]})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _assign(self, client, root, email, role):
        return client.patch(
            f"/api/v3/admin/operators/{email}/role",
            json={"role": role},
            headers={"Authorization": f"Bearer {root['access_token']}"},
        )

    def test_an_assigned_role_takes_effect_at_refresh_and_a_removal_does_too(self):
        email = f"dev-{uuid.uuid4().hex[:6]}@example.com"
        with self._client() as client:
            root = self._sign_in(client, ROOT)
            pair = self._sign_in(client, email)
            self.assertEqual(self._role(pair), "User")

            self.assertEqual(self._assign(client, root, email, "Developer").status_code, 200)
            pair = self._refresh(client, pair)
            self.assertEqual(self._role(pair), "Developer")

            listed = client.get(
                "/api/v3/admin/operators", headers={"Authorization": f"Bearer {root['access_token']}"}
            ).json()["operators"]
            row = next(o for o in listed if o["email"] == email)
            self.assertEqual((row["role"], row["granted_by"]), ("Developer", "Access page"))

            # Demotion ends the session rather than waiting for its next refresh...
            from services.jwt_auth import is_device_revoked, issue_device_token

            device = issue_device_token({"uid": f"uid-{email}", "email": email}, device_id="d-1")
            response = self._assign(client, root, email, "User")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["revoked"], {"sessions": 1, "devices": 1})
            stale = client.post(
                "/api/v3/auth/refresh", json={"refresh_token": pair["refresh_token"]}
            )
            self.assertEqual(stale.status_code, 401)
            self.assertTrue(is_device_revoked(device["device_id"]))

            # ...and signing in again resolves the lowered role.
            self.assertEqual(self._role(self._sign_in(client, email)), "User")

    def test_promotion_keeps_the_session(self):
        email = f"promo-{uuid.uuid4().hex[:6]}@example.com"
        with self._client() as client:
            root = self._sign_in(client, ROOT)
            pair = self._sign_in(client, email)
            response = self._assign(client, root, email, "Admin")
            self.assertEqual(response.json()["revoked"], {"sessions": 0, "devices": 0})
            self.assertEqual(self._role(self._refresh(client, pair)), "Admin")

    def test_a_role_an_old_sign_in_copied_into_the_row_is_not_a_grant(self):
        """Rows written before migration 0015 hold env-derived roles with no assignment stamp."""
        from sqlalchemy import update

        from infrastructure.db.base import get_session_factory
        from infrastructure.db.models import User

        email = f"legacy-{uuid.uuid4().hex[:6]}@example.com"
        with self._client() as client:
            pair = self._sign_in(client, email)

            async def copy_role_like_the_old_sign_in():
                async with get_session_factory()() as db:
                    await db.execute(update(User).where(User.email == email).values(role="Admin"))
                    await db.commit()

            client.portal.call(copy_role_like_the_old_sign_in)
            self.assertEqual(self._role(self._refresh(client, pair)), "User")

    def test_signing_in_never_writes_the_role_column(self):
        from sqlalchemy import select

        from infrastructure.db.base import get_session_factory
        from infrastructure.db.models import User

        with self._client() as client:
            self._sign_in(client, ENV_ADMIN)

            async def stored_role():
                async with get_session_factory()() as db:
                    return await db.scalar(select(User.role).where(User.email == ENV_ADMIN))

            self.assertEqual(client.portal.call(stored_role), "User")

    def test_the_page_cannot_lower_a_role_the_env_grants(self):
        with self._client() as client:
            root = self._sign_in(client, ROOT)
            self._sign_in(client, ENV_ADMIN)
            response = self._assign(client, root, ENV_ADMIN, "Developer")
            self.assertEqual(response.status_code, 409)
            self.assertIn("ADMIN_EMAILS", response.text)

            listed = client.get(
                "/api/v3/admin/operators", headers={"Authorization": f"Bearer {root['access_token']}"}
            ).json()["operators"]
            row = next(o for o in listed if o["email"] == ENV_ADMIN)
            self.assertEqual((row["role"], row["granted_by"]), ("Admin", "ADMIN_EMAILS"))

    def test_assigning_someone_who_never_signed_in_says_so(self):
        with self._client() as client:
            root = self._sign_in(client, ROOT)
            response = self._assign(client, root, "nobody@example.com", "Admin")
            self.assertEqual(response.status_code, 404)
            self.assertIn("sign in once", response.text)


if __name__ == "__main__":
    unittest.main()
