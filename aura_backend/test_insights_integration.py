"""Integration test for insight generation against a REAL Postgres (same testcontainers pattern
as ``test_sync_integration.py``): ``services/insights.py``'s rule engine and
``services/daily_briefing.py`` had zero test coverage before Phase E3 added citations to both --
this exercises the actual async engine and real ORM rows, not constructed objects, so the
citation ids are proven to be the real record ids, not merely plausible-looking strings.

Skips itself cleanly when Docker isn't reachable, exactly like ``test_sync_integration.py``.
"""
import json
import unittest
import uuid
from datetime import datetime, timedelta, timezone


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
    "Postgres 16 container.",
)
class TestInsightsIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from testcontainers.postgres import PostgresContainer

        cls.postgres = PostgresContainer("postgres:16-alpine")
        cls.postgres.start()

        sync_url = cls.postgres.get_connection_url()
        async_url = sync_url.replace("postgresql+psycopg2", "postgresql+asyncpg")

        from config import settings

        settings.database_url = async_url

        import infrastructure.db.base as db_base

        db_base._engine = None
        db_base._session_factory = None

    @classmethod
    def tearDownClass(cls):
        cls.postgres.stop()

    def setUp(self):
        import asyncio

        import infrastructure.db.base as db_base
        # Registers every model class onto Base.metadata -- without this import,
        # create_all() below has nothing to create (the table classes' class bodies, which
        # register them, have never executed). Whether this happens to already be true depends
        # on which other test modules `unittest discover` happened to import first, which is
        # exactly the kind of accidental ordering dependency a standalone `python -m unittest
        # test_insights_integration` run exposes and `discover` can luck past.
        import infrastructure.db.models  # noqa: F401
        from infrastructure.db.base import Base, get_engine

        db_base._engine = None
        db_base._session_factory = None

        async def _create_schema():
            engine = get_engine()
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            await engine.dispose()

        asyncio.run(_create_schema())
        # asyncio.run() above closed the loop the schema-creation engine was bound to --
        # reset the cache again so each test method's own asyncio.run(scenario()) call builds
        # a fresh engine on *its* loop instead of reusing one asyncpg can no longer use.
        db_base._engine = None
        db_base._session_factory = None

    def test_overdue_tasks_insight_cites_the_real_overdue_task_ids(self):
        import asyncio

        from infrastructure.db.base import get_session_factory
        from infrastructure.db.models import Task, User
        from services.insights import generate_insights_for_user

        async def scenario():
            factory = get_session_factory()
            async with factory() as db:
                user = User(
                    id=uuid.uuid4(), auth_subject="insights-it", email="insights-it@example.com",
                    display_name="Insights IT",
                )
                db.add(user)
                await db.flush()

                now = datetime.now(timezone.utc)
                overdue_ids = []
                for _ in range(2):
                    task = Task(
                        id=uuid.uuid4(), user_id=user.id, title_enc="gcm:iv:t",
                        due_date=now - timedelta(days=1),
                    )
                    db.add(task)
                    overdue_ids.append(str(task.id))
                # A completed task past its due date must NOT be counted or cited.
                db.add(Task(
                    id=uuid.uuid4(), user_id=user.id, title_enc="gcm:iv:t",
                    due_date=now - timedelta(days=1), completed_at=now,
                ))
                await db.commit()

                created = await generate_insights_for_user(db, user.id)
                await db.commit()
                return overdue_ids, created

        overdue_ids, created = asyncio.run(scenario())
        rows = {row.kind: json.loads(row.payload_json) for row in created}
        self.assertIn("overdue_tasks", rows)
        self.assertEqual(sorted(rows["overdue_tasks"]["citations"]), sorted(overdue_ids))
        self.assertEqual(len(rows["overdue_tasks"]["citations"]), 2)

    def test_daily_briefing_cites_overdue_tasks_and_near_target_goals(self):
        import asyncio

        from infrastructure.db.base import get_session_factory
        from infrastructure.db.models import Goal, Task, User
        from services.daily_briefing import generate_daily_briefing_for_user

        async def scenario():
            factory = get_session_factory()
            async with factory() as db:
                user = User(
                    id=uuid.uuid4(), auth_subject="briefing-it", email="briefing-it@example.com",
                    display_name="Briefing IT",
                )
                db.add(user)
                await db.flush()

                now = datetime.now(timezone.utc)
                task = Task(
                    id=uuid.uuid4(), user_id=user.id, title_enc="gcm:iv:t",
                    due_date=now - timedelta(days=1),
                )
                goal = Goal(
                    id=uuid.uuid4(), user_id=user.id, title_enc="gcm:iv:g", status="active",
                    priority="high", progress_pct=50, target_date=now + timedelta(days=2),
                )
                db.add_all([task, goal])
                await db.commit()

                row = await generate_daily_briefing_for_user(db, user.id)
                await db.commit()
                return str(task.id), str(goal.id), row

        task_id, goal_id, row = asyncio.run(scenario())
        payload = json.loads(row.payload_json)
        self.assertIn(task_id, payload["citations"])
        self.assertIn(goal_id, payload["citations"])
        self.assertIn("1 overdue task", payload["explanation"])


if __name__ == "__main__":
    unittest.main()
