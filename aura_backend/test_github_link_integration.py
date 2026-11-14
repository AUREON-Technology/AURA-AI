"""GitHub account linking (F-08) against a REAL Postgres (testcontainers).

The flow is started by whoever holds a JWT (``/connect``) but finished by whichever browser opens
GitHub's redirect. Anyone could call ``/connect`` with their own session and send the
``authorize_url`` to a victim already signed in to GitHub; the callback then attached the
victim's GitHub token to the attacker's AURA account. The callback now links nothing: it names
the AURA account and waits for this browser to confirm, and a GitHub account already linked to
another AURA account is refused.

GitHub itself is the one thing replaced: its token exchange and user lookup are network calls.
Skips itself cleanly when Docker isn't reachable.
"""
import asyncio
import os
import re
import unittest

import legacy_postgres
import uuid


_ENV = {
    "AURA_GITHUB_OAUTH_ENABLED": "true",
    "AURA_GITHUB_OAUTH_CLIENT_ID": "client-id",
    "AURA_GITHUB_OAUTH_CLIENT_SECRET": "client-secret",
    "AURA_GITHUB_OAUTH_CALLBACK_URL": "https://aura.example/api/v3/integrations/github/callback",
}


@unittest.skipUnless(legacy_postgres.available(), legacy_postgres.SKIP_REASON)
class TestGitHubLinkIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cryptography.fernet import Fernet
        cls.postgres = legacy_postgres.container()
        cls.postgres.start()
        # Registered as a cleanup, not left to tearDownClass: if setUpClass raises below, unittest
        # never runs tearDownClass and the database would stay behind on the server.
        cls.addClassCleanup(cls.postgres.stop)
        from config import settings

        settings.database_url = cls.postgres.get_connection_url().replace(
            "postgresql+psycopg2", "postgresql+asyncpg"
        )
        cls._saved = {k: os.environ.get(k) for k in [*_ENV, "AURA_SECRETS_ENCRYPTION_KEY"]}
        os.environ.update(_ENV)
        os.environ["AURA_SECRETS_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

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

    @classmethod
    def tearDownClass(cls):
        for key, value in cls._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def setUp(self):
        import api.v2.github_integration as gh
        import infrastructure.db.base as db_base
        import services.github_oauth_state as oauth_state
        import services.jwt_auth as jwt_auth
        from aura_core.adapters.github_oauth import GitHubOAuthToken

        db_base._engine = None
        db_base._session_factory = None
        self._patches = [
            (jwt_auth, "get_redis_client", jwt_auth.get_redis_client),
            (oauth_state, "get_redis_client", oauth_state.get_redis_client),
            (gh, "exchange_code", gh.exchange_code),
            (gh, "fetch_authenticated_user", gh.fetch_authenticated_user),
        ]
        jwt_auth.get_redis_client = lambda: None
        oauth_state.get_redis_client = lambda: None
        self.github_user = {"id": 4242, "login": "victim-gh"}

        async def exchange_code(**_):
            return GitHubOAuthToken(
                access_token="gho_victim", refresh_token=None, expires_in=None,
                refresh_token_expires_in=None, scope="",
            )

        async def fetch_user(_token):
            return self.github_user

        gh.exchange_code = exchange_code
        gh.fetch_authenticated_user = fetch_user

    def tearDown(self):
        for module, name, original in self._patches:
            setattr(module, name, original)

    def _client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from api.v2.github_integration import router

        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def _account(self, client, email):
        from infrastructure.db.base import get_session_factory
        from infrastructure.db.models import User
        from services.jwt_auth import issue_token_pair

        subject = f"sub-{uuid.uuid4().hex[:8]}"

        async def create():
            async with get_session_factory()() as db:
                db.add(User(auth_subject=subject, email=email))
                await db.commit()

        client.portal.call(create)
        token = issue_token_pair({"uid": subject, "email": email})["access_token"]
        return subject, {"Authorization": f"Bearer {token}"}

    def _linked_to(self, client, subject):
        from sqlalchemy import select

        from infrastructure.db.base import get_session_factory
        from infrastructure.db.models import GitHubIdentity, User

        async def lookup():
            async with get_session_factory()() as db:
                return await db.scalar(
                    select(GitHubIdentity.github_user_id)
                    .join(User, User.id == GitHubIdentity.user_id)
                    .where(User.auth_subject == subject)
                )

        return client.portal.call(lookup)

    @staticmethod
    def _state(authorize_url):
        return re.search(r"state=([^&]+)", authorize_url).group(1)

    def test_the_callback_alone_links_nothing_and_names_the_account(self):
        """The attack: the attacker's link opened in the victim's browser."""
        with self._client() as client:
            attacker, auth = self._account(client, f"attacker-{uuid.uuid4().hex[:4]}@example.com")
            state = self._state(client.get("/api/v3/integrations/github/connect", headers=auth).json()["authorize_url"])

            page = client.get(f"/api/v3/integrations/github/callback?code=victim-code&state={state}")

            self.assertEqual(page.status_code, 200)
            self.assertIn("attacker-", page.text, "the page must say which AURA account is linking")
            self.assertIsNone(self._linked_to(client, attacker))

    def test_confirming_links_the_account_once(self):
        with self._client() as client:
            subject, auth = self._account(client, f"owner-{uuid.uuid4().hex[:4]}@example.com")
            state = self._state(client.get("/api/v3/integrations/github/connect", headers=auth).json()["authorize_url"])
            client.get(f"/api/v3/integrations/github/callback?code=c&state={state}")

            done = client.post(
                "/api/v3/integrations/github/callback/confirm", data={"state": state, "code": "c"}
            )
            again = client.post(
                "/api/v3/integrations/github/callback/confirm", data={"state": state, "code": "c"}
            )

            self.assertEqual(done.status_code, 200, done.text)
            self.assertEqual(self._linked_to(client, subject), 4242)
            self.assertEqual(again.status_code, 400, "the state is single-use")

    def test_a_github_account_linked_elsewhere_is_refused(self):
        self.github_user = {"id": 777, "login": "taken"}
        with self._client() as client:
            first, first_auth = self._account(client, f"a-{uuid.uuid4().hex[:4]}@example.com")
            second, second_auth = self._account(client, f"b-{uuid.uuid4().hex[:4]}@example.com")
            for auth in (first_auth, second_auth):
                state = self._state(
                    client.get("/api/v3/integrations/github/connect", headers=auth).json()["authorize_url"]
                )
                response = client.post(
                    "/api/v3/integrations/github/callback/confirm", data={"state": state, "code": "c"}
                )

            self.assertEqual(response.status_code, 409)
            self.assertEqual(self._linked_to(client, first), 777)
            self.assertIsNone(self._linked_to(client, second))

    def test_page_content_is_escaped(self):
        with self._client() as client:
            _, auth = self._account(client, f"<script>x</script>{uuid.uuid4().hex[:4]}@example.com")
            state = self._state(client.get("/api/v3/integrations/github/connect", headers=auth).json()["authorize_url"])
            page = client.get(f"/api/v3/integrations/github/callback?code=%22%3E%3Cb%3E&state={state}")

            self.assertNotIn("<script>x</script>", page.text)
            self.assertNotIn('"><b>', page.text)


if __name__ == "__main__":
    unittest.main()
