"""Celery's per-process event loop against a REAL Postgres (testcontainers, like
``test_insights_integration.py``).

``tasks.py`` used to run every task with ``asyncio.run``. The asyncpg pool is created once per
worker process and binds to the first task's loop; from the second task on every query failed
("another operation is in progress"), and the tasks swallowed it into ``{"status": "error"}`` --
the outbox drain every 60 s, nightly memory consolidation, the hourly insights.

Skips itself cleanly when Docker isn't reachable.
"""
import asyncio
import unittest


def _docker_available() -> bool:
    try:
        import docker

        docker.from_env().ping()
        return True
    except Exception:
        return False


@unittest.skipUnless(_docker_available(), "Docker is not available -- needs a real Postgres 16.")
class TestCeleryLoopIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from testcontainers.postgres import PostgresContainer

        cls.postgres = PostgresContainer("postgres:16-alpine")
        cls.postgres.start()
        cls.url = cls.postgres.get_connection_url().replace("postgresql+psycopg2", "postgresql")

    @classmethod
    def tearDownClass(cls):
        cls.postgres.stop()

    def _three_tasks(self, runner):
        from aura_core.adapters.postgres_store import PostgresPool

        pool = PostgresPool(self.url)

        async def task():
            pg = await pool._get_pool()
            async with pg.acquire() as conn:
                return await conn.fetchval("SELECT 1")

        results = []
        for _ in range(3):
            try:
                results.append(runner(task()))
            except Exception as exc:  # noqa: BLE001 -- recorded, asserted below
                results.append(type(exc).__name__)
        return results

    def test_a_cached_pool_serves_every_task_in_the_process(self):
        import tasks

        self.assertEqual(self._three_tasks(tasks._run_async), [1, 1, 1])

    def test_asyncio_run_per_task_is_what_broke_it(self):
        results = self._three_tasks(asyncio.run)
        self.assertEqual(results[0], 1)
        self.assertNotIn(1, results[1:])


if __name__ == "__main__":
    unittest.main()
