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

    # --- F-17: "today" is the user's local day, not the UTC one -----------------------------

    @staticmethod
    def _user(name, tz=None):
        from infrastructure.db.models import User

        return User(
            id=uuid.uuid4(), auth_subject=f"{name}-{uuid.uuid4()}",
            email=f"{name}-{uuid.uuid4()}@example.com", display_name=name, timezone=tz,
        )

    @staticmethod
    def _arun(coro):
        """``asyncio.run`` for tests that need several: asyncpg connections belong to the loop
        that opened them, so each run gets a fresh engine and disposes of it on its own loop."""
        import asyncio

        import infrastructure.db.base as db_base

        async def run():
            try:
                return await coro
            finally:
                await db_base.get_engine().dispose()

        db_base._engine = None
        db_base._session_factory = None
        try:
            return asyncio.run(run())
        finally:
            db_base._engine = None
            db_base._session_factory = None

    def _hydration(self, tz, now, records):
        """Runs the insights rules for a fresh user in ``tz`` with ``(record_date, water_ml)``
        health records, returning the hydration payload (or None) and the record ids."""

        from infrastructure.db.base import get_session_factory
        from infrastructure.db.models import HealthRecord
        from services.insights import generate_insights_for_user

        async def scenario():
            async with get_session_factory()() as db:
                user = self._user("hydration", tz)
                db.add(user)
                await db.flush()
                ids = {}
                for record_date, water in records:
                    row = HealthRecord(
                        id=uuid.uuid4(), user_id=user.id, record_date=record_date, water_ml=water,
                    )
                    db.add(row)
                    ids[record_date] = str(row.id)
                await db.commit()
                created = await generate_insights_for_user(db, user.id, now=now)
                await db.commit()
                rows = {r.kind: json.loads(r.payload_json) for r in created}
                return rows.get("hydration"), ids

        return self._arun(scenario())

    def test_a_utc_plus_3_evening_sees_the_water_logged_today(self):
        """The audit's case. The client stores a day's record at local midnight, which for
        Riyadh is 21:00 UTC the day before; at 20:00 local (17:00 UTC) the UTC day's start is
        after it, so the user was told 0ml with the record right there."""
        riyadh_midnight = datetime(2026, 10, 30, 21, 0, tzinfo=timezone.utc)  # 31 Oct, local
        yesterday = riyadh_midnight - timedelta(days=1)
        hydration, ids = self._hydration(
            "Asia/Riyadh", datetime(2026, 10, 31, 17, 0, tzinfo=timezone.utc),
            [(yesterday, 1800), (riyadh_midnight, 500)],
        )
        self.assertIsNotNone(hydration)
        self.assertEqual(hydration["message"], "Only 500ml of water logged today.")
        self.assertEqual(hydration["citations"], [ids[riyadh_midnight]])

    def test_the_afternoon_nudge_uses_the_users_local_hour(self):
        # 13:00 UTC is 16:00 in Riyadh: past 3pm, so nudge.
        east, _ = self._hydration(
            "Asia/Riyadh", datetime(2026, 10, 31, 13, 0, tzinfo=timezone.utc), [],
        )
        self.assertIsNotNone(east)
        self.assertEqual(east["message"], "Only 0ml of water logged today.")
        # 16:00 UTC is 11:00 in Bogota: too early to nag.
        west, _ = self._hydration(
            "America/Bogota", datetime(2026, 10, 31, 16, 0, tzinfo=timezone.utc), [],
        )
        self.assertIsNone(west)

    def test_a_missing_or_invalid_timezone_means_utc(self):
        utc_midnight = datetime(2026, 10, 31, 0, 0, tzinfo=timezone.utc)
        for tz in (None, "Mars/Olympus_Mons"):
            with self.subTest(tz=tz):
                hydration, ids = self._hydration(
                    tz, datetime(2026, 10, 31, 17, 0, tzinfo=timezone.utc), [(utc_midnight, 500)],
                )
                self.assertEqual(hydration["message"], "Only 500ml of water logged today.")
                self.assertEqual(hydration["citations"], [ids[utc_midnight]])

    def test_the_briefing_counts_what_is_due_in_the_users_local_day(self):
        """06:00 in Bogota is 11:00 UTC. A task due at 20:00 local and an event at 19:30 local
        fall on the *next* UTC day, so the UTC day missed both."""
        from infrastructure.db.base import get_session_factory
        from infrastructure.db.models import CalendarEvent, Task
        from services.daily_briefing import generate_daily_briefing_for_user

        now = datetime(2026, 10, 31, 11, 0, tzinfo=timezone.utc)

        async def scenario():
            async with get_session_factory()() as db:
                user = self._user("briefing-local", "America/Bogota")
                db.add(user)
                await db.flush()
                task = Task(
                    id=uuid.uuid4(), user_id=user.id, title_enc="gcm:iv:t",
                    due_date=datetime(2026, 11, 1, 1, 0, tzinfo=timezone.utc),
                )
                tomorrow = Task(
                    id=uuid.uuid4(), user_id=user.id, title_enc="gcm:iv:t",
                    due_date=datetime(2026, 11, 1, 6, 0, tzinfo=timezone.utc),
                )
                event = CalendarEvent(
                    id=uuid.uuid4(), user_id=user.id, title_enc="gcm:iv:e",
                    start_time=datetime(2026, 11, 1, 0, 30, tzinfo=timezone.utc),
                    end_time=datetime(2026, 11, 1, 1, 30, tzinfo=timezone.utc),
                )
                db.add_all([task, tomorrow, event])
                await db.commit()
                row = await generate_daily_briefing_for_user(db, user.id, now=now)
                await db.commit()
                return str(task.id), str(tomorrow.id), str(event.id), row

        task_id, tomorrow_id, event_id, row = self._arun(scenario())
        payload = json.loads(row.payload_json)
        self.assertEqual(payload["explanation"], "1 task due today; 1 calendar event today.")
        self.assertIn(task_id, payload["citations"])
        self.assertIn(event_id, payload["citations"])
        self.assertNotIn(tomorrow_id, payload["citations"])

    def test_briefings_go_out_at_six_in_each_users_own_morning(self):
        """The beat runs hourly; each user gets one briefing a day, at or after 06:00 their
        time. 03:30 UTC is 06:30 in Riyadh and 20:30 the evening before in Los Angeles."""
        from sqlalchemy import select

        from infrastructure.db.base import get_session_factory
        from infrastructure.db.models import Insight
        from tasks import _generate_daily_briefings_for_all_users

        async def setup():
            async with get_session_factory()() as db:
                users = [self._user("riyadh", "Asia/Riyadh"),
                         self._user("la", "America/Los_Angeles"), self._user("unset")]
                db.add_all(users)
                await db.commit()
                return [u.id for u in users]

        async def briefings(user_id):
            async with get_session_factory()() as db:
                return len((await db.execute(select(Insight).where(
                    Insight.user_id == user_id, Insight.kind == "daily_briefing",
                ))).scalars().all())

        def run_at(hour, minute):
            now = datetime(2026, 10, 31, hour, minute, tzinfo=timezone.utc)
            self._arun(_generate_daily_briefings_for_all_users(now=now))
            return [self._arun(briefings(i)) for i in ids]

        ids = self._arun(setup())
        self.assertEqual(run_at(3, 30), [1, 0, 0])
        # The next hourly run must not send Riyadh a second one.
        self.assertEqual(run_at(4, 30), [1, 0, 0])
        # A user who never reported a zone gets it on the old UTC schedule.
        self.assertEqual(run_at(6, 30), [1, 0, 1])
        # 13:30 UTC is 06:30 in Los Angeles (on PDT until 1 November).
        self.assertEqual(run_at(13, 30), [1, 1, 1])

    def test_the_insights_endpoints_remember_the_clients_timezone(self):
        """``X-Client-Timezone`` is persisted, and a bad or missing value never overwrites a
        good one."""
        from sqlalchemy import select

        from api.v2.insights import generate_insights, list_insights
        from infrastructure.db.base import get_session_factory
        from infrastructure.db.models import User

        user_info = {"uid": f"tz-{uuid.uuid4()}", "email": f"tz-{uuid.uuid4()}@example.com"}

        def call(endpoint, tz):
            async def go():
                async with get_session_factory()() as db:
                    await endpoint(user_info=user_info, db=db, client_timezone=tz)
                async with get_session_factory()() as db:
                    return await db.scalar(
                        select(User.timezone).where(User.auth_subject == user_info["uid"])
                    )
            return self._arun(go())

        self.assertEqual(call(list_insights, "Asia/Riyadh"), "Asia/Riyadh")
        self.assertEqual(call(generate_insights, "../../etc/localtime"), "Asia/Riyadh")
        self.assertEqual(call(generate_insights, None), "Asia/Riyadh")
        self.assertEqual(call(generate_insights, "America/Bogota"), "America/Bogota")


if __name__ == "__main__":
    unittest.main()
