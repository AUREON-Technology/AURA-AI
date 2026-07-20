"""Integration test for /api/v2/sync against a REAL Postgres (Doc/
V2_BLUEPRINT.md §11: "pytest + testcontainers-python spinning up real
Postgres/Redis in CI, not mocks"). Every other backend test in this suite
either needs no database (SSE splitter, JWT auth with the in-process
refresh-token fallback) or constructs SQLAlchemy model objects directly
without a session (the encryption-boundary tests) — this is the one test
that exercises the actual async engine, a real migration-equivalent
schema, and the full FastAPI request/response cycle together.

Needs Docker to spin up the Postgres container, which is not available in
the sandbox that wrote this file (confirmed via `docker ps` failing) — it
is designed to run in backend-ci.yml's GitHub Actions runner, which does
have Docker. Skips itself cleanly (not an error, not a silent no-op) when
Docker isn't reachable, so `python -m unittest discover` stays green
locally without ever having actually run this test's assertions.
"""
import unittest
import uuid


def _docker_available() -> bool:
    try:
        import docker

        client = docker.from_env()
        client.ping()
        return True
    except Exception:
        return False


DOCKER_AVAILABLE = _docker_available()


@unittest.skipUnless(
    DOCKER_AVAILABLE,
    "Docker is not available — this integration test needs it to run a real "
    "Postgres 16 container. Runs in CI (backend-ci.yml); skipped locally here.",
)
class TestSyncApiIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from testcontainers.postgres import PostgresContainer

        cls.postgres = PostgresContainer("postgres:16-alpine")
        cls.postgres.start()

        # testcontainers gives a psycopg2 URL by default; the app's async
        # engine (infrastructure/db/base.py) needs the asyncpg dialect.
        sync_url = cls.postgres.get_connection_url()
        async_url = sync_url.replace("postgresql+psycopg2", "postgresql+asyncpg")

        from config import settings

        settings.database_url = async_url

        # infrastructure.db.base lazily caches the engine on first use —
        # reset that cache so it picks up the container's URL instead of
        # whatever (if anything) DATABASE_URL was set to before this test ran.
        import infrastructure.db.base as db_base

        db_base._engine = None
        db_base._session_factory = None

    @classmethod
    def tearDownClass(cls):
        cls.postgres.stop()

    def setUp(self):
        import asyncio

        from infrastructure.db.base import Base, get_engine

        async def _create_schema():
            engine = get_engine()
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        asyncio.run(_create_schema())

    def test_push_then_pull_round_trip(self):
        from fastapi.testclient import TestClient

        from main import app
        from services.jwt_auth import verify_access_token

        app.dependency_overrides[verify_access_token] = lambda: {
            "uid": "integration_test_user",
            "email": "it@aura.ai",
            "name": "Integration Test",
            "picture": None,
            "role": "User",
        }
        client = TestClient(app)
        task_id = str(uuid.uuid4())

        try:
            push_response = client.post(
                "/api/v2/sync",
                json={
                    "mutations": [
                        {
                            "table": "tasks",
                            "op": "upsert",
                            "id": task_id,
                            "client_updated_at": "2026-01-01T00:00:00Z",
                            "payload": {
                                "title_enc": "gcm:fake_iv:fake_ciphertext",
                                "description_enc": None,
                                "priority": "high",
                                "category": "personal",
                                "due_date": None,
                                "completed_at": None,
                                "google_task_id": None,
                            },
                        }
                    ]
                },
            )
            self.assertEqual(push_response.status_code, 200)
            self.assertEqual(push_response.json()[0]["status"], "applied")

            pull_response = client.get("/api/v2/sync")
            self.assertEqual(pull_response.status_code, 200)
            tasks = pull_response.json()["data"]["tasks"]
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0]["id"], task_id)
            # Round-trips as the exact ciphertext the client sent — the
            # same zero-knowledge property test_sync_encryption_boundary.py
            # asserts at the unit level, now proven against a real DB too.
            self.assertEqual(tasks[0]["title_enc"], "gcm:fake_iv:fake_ciphertext")
            self.assertIsNone(tasks[0]["deleted_at"])

            # A second push marking it deleted should soft-delete, and the
            # deletion should be visible on the next pull.
            delete_response = client.post(
                "/api/v2/sync",
                json={
                    "mutations": [
                        {
                            "table": "tasks",
                            "op": "delete",
                            "id": task_id,
                            "client_updated_at": "2026-01-01T00:01:00Z",
                            "payload": None,
                        }
                    ]
                },
            )
            self.assertEqual(delete_response.status_code, 200)

            pull_after_delete = client.get("/api/v2/sync")
            tasks_after_delete = pull_after_delete.json()["data"]["tasks"]
            self.assertEqual(len(tasks_after_delete), 1)
            self.assertIsNotNone(tasks_after_delete[0]["deleted_at"])
        finally:
            app.dependency_overrides.clear()

    def test_conflicting_push_is_reported_not_silently_overwritten(self):
        from fastapi.testclient import TestClient

        from main import app
        from services.jwt_auth import verify_access_token

        app.dependency_overrides[verify_access_token] = lambda: {
            "uid": "integration_test_user_2",
            "email": "it2@aura.ai",
            "name": "Integration Test 2",
            "picture": None,
            "role": "User",
        }
        client = TestClient(app)
        task_id = str(uuid.uuid4())

        try:
            base_mutation = {
                "table": "tasks",
                "id": task_id,
                "op": "upsert",
                "payload": {
                    "title_enc": "gcm:a:a",
                    "description_enc": None,
                    "priority": "medium",
                    "category": "personal",
                    "due_date": None,
                    "completed_at": None,
                    "google_task_id": None,
                },
            }

            # First write establishes the row with a later timestamp...
            client.post("/api/v2/sync", json={"mutations": [{**base_mutation, "client_updated_at": "2026-01-02T00:00:00Z"}]})

            # ...a second push claiming an EARLIER client clock must be
            # reported as a conflict, not silently accepted and overwritten.
            stale_response = client.post("/api/v2/sync", json={"mutations": [{**base_mutation, "client_updated_at": "2026-01-01T00:00:00Z"}]})
            self.assertEqual(stale_response.json()[0]["status"], "conflict")
        finally:
            app.dependency_overrides.clear()


if __name__ == "__main__":
    unittest.main()
