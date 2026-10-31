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

    def test_hourly_insights_do_not_delete_the_daily_briefing(self):
        """Insights regenerate hourly and replace their own stale rows. The replacement query
        had no ``kind`` filter, so it also deleted the 06:00 daily briefing: the briefing lived
        at most until the next hourly run."""
        import asyncio

        from sqlalchemy import select

        from infrastructure.db.base import get_session_factory
        from infrastructure.db.models import Insight, Task, User
        from services.daily_briefing import generate_daily_briefing_for_user
        from services.insights import generate_insights_for_user

        async def scenario():
            factory = get_session_factory()
            async with factory() as db:
                user = User(
                    id=uuid.uuid4(), auth_subject="briefing-survives",
                    email="briefing-survives@example.com", display_name="Briefing Survives",
                )
                db.add(user)
                await db.flush()
                db.add(Task(
                    id=uuid.uuid4(), user_id=user.id, title_enc="gcm:iv:t",
                    due_date=datetime.now(timezone.utc) - timedelta(days=1),
                ))
                await db.commit()

                briefing = await generate_daily_briefing_for_user(db, user.id)
                await db.commit()
                await generate_insights_for_user(db, user.id)
                await db.commit()

                kinds = (await db.execute(
                    select(Insight.kind).where(Insight.user_id == user.id)
                )).scalars().all()
                survivor = await db.get(Insight, briefing.id)
                return kinds, survivor

        kinds, survivor = asyncio.run(scenario())
        self.assertIsNotNone(survivor, "the daily briefing was deleted by the insights run")
        self.assertIn("daily_briefing", kinds)
        self.assertIn("overdue_tasks", kinds)

    # -- "today" is the user's day (F-17) ---------------------------------------------------

    def _hydration_for(self, subject, tz_name, record_local_midnight, now, water_ml=300):
        """Store a health record where the client puts it (local midnight, as UTC) and run the
        hourly rules at ``now`` for a user in ``tz_name``."""
        import asyncio
        from zoneinfo import ZoneInfo

        from infrastructure.db.base import get_session_factory
        from infrastructure.db.models import HealthRecord, User
        from services.insights import generate_insights_for_user

        async def scenario():
            factory = get_session_factory()
            async with factory() as db:
                user = User(
                    id=uuid.uuid4(), auth_subject=subject, email=f"{subject}@example.com",
                    display_name=subject,
                )
                db.add(user)
                await db.flush()
                record = HealthRecord(
                    id=uuid.uuid4(), user_id=user.id, water_ml=water_ml,
                    record_date=record_local_midnight.astimezone(timezone.utc),
                )
                db.add(record)
                await db.commit()
                created = await generate_insights_for_user(
                    db, user.id, tz=ZoneInfo(tz_name) if tz_name else None, now=now,
                )
                await db.commit()
                return str(record.id), {r.kind: json.loads(r.payload_json) for r in created}

        return asyncio.run(scenario())

    def test_a_utc_plus_3_evening_sees_todays_water(self):
        """18:30 in Riyadh is 15:30Z. Today's record sits at 21:00Z yesterday, which the UTC
        day excluded: the user was told "0ml logged today" every evening."""
        from zoneinfo import ZoneInfo

        riyadh = ZoneInfo("Asia/Riyadh")
        record_id, rows = self._hydration_for(
            "tz-plus-3", "Asia/Riyadh",
            datetime(2026, 10, 31, tzinfo=riyadh),
            now=datetime(2026, 10, 31, 15, 30, tzinfo=timezone.utc),
        )
        self.assertIn("hydration", rows)
        self.assertEqual(rows["hydration"]["message"], "Only 300ml of water logged today.")
        self.assertEqual(rows["hydration"]["citations"], [record_id])

    def test_a_utc_minus_5_evening_sees_todays_water(self):
        """22:00 in Bogota on 31 Oct is 03:00Z on 1 Nov: the UTC day had already moved on."""
        from zoneinfo import ZoneInfo

        bogota = ZoneInfo("America/Bogota")
        record_id, rows = self._hydration_for(
            "tz-minus-5", "America/Bogota",
            datetime(2026, 10, 31, tzinfo=bogota),
            now=datetime(2026, 11, 1, 3, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(rows["hydration"]["message"], "Only 300ml of water logged today.")
        self.assertEqual(rows["hydration"]["citations"], [record_id])

    def test_the_3pm_cutoff_is_local(self):
        """16:00Z is past 3 pm UTC but 11:00 in Bogota: too early to nag."""
        from zoneinfo import ZoneInfo

        _, rows = self._hydration_for(
            "tz-cutoff", "America/Bogota",
            datetime(2026, 10, 31, tzinfo=ZoneInfo("America/Bogota")),
            now=datetime(2026, 10, 31, 16, 0, tzinfo=timezone.utc),
        )
        self.assertNotIn("hydration", rows)

    def test_yesterdays_record_is_not_todays(self):
        """Bounded on both sides: at 22:30 Riyadh time on 31 Oct (19:30Z), the 30 Oct record
        must not count as today's."""
        from zoneinfo import ZoneInfo

        _, rows = self._hydration_for(
            "tz-yesterday", "Asia/Riyadh",
            datetime(2026, 10, 30, tzinfo=ZoneInfo("Asia/Riyadh")),
            now=datetime(2026, 10, 31, 19, 30, tzinfo=timezone.utc),
        )
        self.assertEqual(rows["hydration"]["message"], "Only 0ml of water logged today.")
        self.assertEqual(rows["hydration"]["citations"], [])

    def test_without_a_timezone_the_day_is_still_the_utc_day(self):
        _, rows = self._hydration_for(
            "tz-none", None,
            datetime(2026, 10, 31, tzinfo=timezone.utc),
            now=datetime(2026, 10, 31, 16, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(rows["hydration"]["message"], "Only 300ml of water logged today.")

    def test_the_briefing_counts_what_is_due_on_the_users_day(self):
        """For a UTC-5 user at 06:00Z (01:00 local on 31 Oct), a task due at 20:00 local on
        31 Oct (01:00Z on 1 Nov) is due today. The UTC day ended at 00:00Z and missed it."""
        import asyncio
        from zoneinfo import ZoneInfo

        from infrastructure.db.base import get_session_factory
        from infrastructure.db.models import Task, User
        from services.daily_briefing import generate_daily_briefing_for_user

        bogota = ZoneInfo("America/Bogota")
        now = datetime(2026, 10, 31, 6, 0, tzinfo=timezone.utc)

        async def scenario():
            factory = get_session_factory()
            async with factory() as db:
                user = User(
                    id=uuid.uuid4(), auth_subject="tz-briefing",
                    email="tz-briefing@example.com", display_name="TZ Briefing",
                )
                db.add(user)
                await db.flush()
                tonight = Task(
                    id=uuid.uuid4(), user_id=user.id, title_enc="gcm:iv:t",
                    due_date=datetime(2026, 10, 31, 20, 0, tzinfo=bogota),
                )
                db.add(tonight)
                await db.commit()
                row = await generate_daily_briefing_for_user(db, user.id, tz=bogota, now=now)
                await db.commit()
                return str(tonight.id), json.loads(row.payload_json)

        tonight_id, payload = asyncio.run(scenario())
        self.assertIn(tonight_id, payload["citations"])
        self.assertIn("1 task due today", payload["explanation"])


if __name__ == "__main__":
    unittest.main()
