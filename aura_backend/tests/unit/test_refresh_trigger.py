"""An operator can start the knowledge refresh and read its outcome (admin trigger route).

Railway runs the gateway only, with no Celery worker or beat, so nothing there ever ran the nightly
refresh. ``POST /api/v3/admin/refresh`` starts the same pipeline in the background and returns at
once; its state (a lock and the latest run) lives in the shared cache so that any gateway worker can
answer ``GET /api/v3/admin/refresh/latest`` and no two runs overlap.
"""

from __future__ import annotations

import time

import pytest

from aura_core.domain import DocumentId, UserId
from aura_core.domain.knowledge import KnowledgeSource, LicenseStatus, SourceType
from aura_core.learning.refresh_runs import RefreshRunTracker
from aura_core.learning.sources import FetchedDocument, StaticSourceFetcher

pytest.importorskip("fastapi")

LOCK_KEY = "aura:knowledge:refresh:lock"


class TestTracker:
    async def _tracker(self, **kw):
        from aura_core.llm.cache import InMemoryCache

        return RefreshRunTracker(InMemoryCache(), **kw)

    async def test_one_run_at_a_time(self) -> None:
        tracker = await self._tracker()
        first = await tracker.start(dry_run=True, triggered_by="op")
        assert first is not None and first.status == "running"
        assert await tracker.start(dry_run=True, triggered_by="op") is None

    async def test_finishing_releases_the_lock_and_records_the_outcome(self) -> None:
        tracker = await self._tracker()
        run = await tracker.start(dry_run=False, triggered_by="op")
        await tracker.finish(run, report={"totals": {"ingested": 3}})
        latest = await tracker.latest()
        assert (latest.status, latest.report["totals"]["ingested"]) == ("succeeded", 3)
        assert latest.finished_at is not None
        assert await tracker.start(dry_run=True, triggered_by="op") is not None

    async def test_a_failed_run_records_the_error_and_still_releases_the_lock(self) -> None:
        tracker = await self._tracker()
        run = await tracker.start(dry_run=True, triggered_by="op")
        await tracker.finish(run, error="RuntimeError")
        latest = await tracker.latest()
        assert (latest.status, latest.error) == ("failed", "RuntimeError")
        assert await tracker.start(dry_run=True, triggered_by="op") is not None

    async def test_a_run_whose_worker_died_is_not_reported_as_running_forever(self) -> None:
        tracker = await self._tracker(lock_ttl_s=0)  # anything already started counts as overdue
        await tracker.start(dry_run=True, triggered_by="op")
        latest = await tracker.latest()
        assert latest.status == "failed" and "abandoned" in latest.error

    async def test_nothing_recorded_is_none(self) -> None:
        assert await (await self._tracker()).latest() is None


def _client(container, *, role: str):
    from fastapi.testclient import TestClient

    from aura_core.api import Principal, build_app

    async def authenticator(request):
        return Principal(
            user_id=UserId("op-1"), email="op@example.com", role=role,
            capabilities=("admin.act", "admin.super") if role == "SuperAdmin" else ("admin.act",),
        )

    return TestClient(build_app(container, authenticator=authenticator))


def _wait_until_done(client, timeout_s: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        run = client.get("/api/v3/admin/refresh/latest").json()["run"]
        if run is not None and run["status"] != "running":
            return run
        time.sleep(0.05)
    raise AssertionError("the refresh did not finish")


async def _one_licensed_source(container) -> None:
    source = KnowledgeSource(
        id="docs", type=SourceType.INTERNAL, uri="internal://docs",
        license_spdx="CC-BY-4.0", license_status=LicenseStatus.APPROVED,
    )
    await container.sources.register(source)
    fetcher = StaticSourceFetcher()
    fetcher.set("docs", [FetchedDocument(
        title="Zebra migration", document_id=DocumentId("docs:zebra"),
        content="# Zebra migration\nZebras migrate across the savannah every year following the rains.",
    )])
    container.refresh_pipeline._fetcher = fetcher


class TestTriggerRoute:
    async def test_the_default_is_a_dry_run_that_writes_nothing(self, container) -> None:
        await container.startup()
        await _one_licensed_source(container)
        with _client(container, role="Admin") as client:
            response = client.post("/api/v3/admin/refresh", json={})
            assert response.status_code == 202, response.text
            assert response.json()["run"]["dry_run"] is True
            done = _wait_until_done(client)
        assert done["status"] == "succeeded"
        assert done["triggered_by"] == "op@example.com"
        assert done["report"]["totals"]["ingested"] == 0
        assert [s["would_apply"] for s in done["report"]["sources"]] != []
        assert await container.documents.get(DocumentId("docs:zebra")) is None

    async def test_a_real_run_changes_the_corpus(self, container) -> None:
        await container.startup()
        await _one_licensed_source(container)
        with _client(container, role="SuperAdmin") as client:
            response = client.post("/api/v3/admin/refresh", json={"dry_run": False})
            assert response.status_code == 202, response.text
            done = _wait_until_done(client)
        assert done["status"] == "succeeded", done
        assert done["report"]["totals"]["ingested"] == 1
        assert (await container.documents.get(DocumentId("docs:zebra"))) is not None

    async def test_only_a_super_admin_may_start_a_real_run(self, container) -> None:
        await container.startup()
        await _one_licensed_source(container)
        with _client(container, role="Admin") as client:
            response = client.post("/api/v3/admin/refresh", json={"dry_run": False})
        assert response.status_code == 403, response.text
        assert await container.documents.get(DocumentId("docs:zebra")) is None

    async def test_a_user_who_is_not_an_admin_may_not_start_anything(self, container) -> None:
        await container.startup()
        with _client(container, role="User") as client:
            response = client.post("/api/v3/admin/refresh", json={})
        assert response.status_code == 403, response.text

    async def test_a_second_request_while_one_runs_is_refused_with_the_run(self, container) -> None:
        await container.startup()
        await container.cache_backend.incr(LOCK_KEY, ttl_s=600)  # another worker holds the lock
        with _client(container, role="Admin") as client:
            response = client.post("/api/v3/admin/refresh", json={})
        assert response.status_code == 409, response.text

    async def test_the_lock_is_released_when_the_run_ends(self, container) -> None:
        await container.startup()
        with _client(container, role="Admin") as client:
            client.post("/api/v3/admin/refresh", json={})
            _wait_until_done(client)
            again = client.post("/api/v3/admin/refresh", json={})
            _wait_until_done(client)
        assert again.status_code == 202, again.text

    async def test_a_failing_pipeline_is_recorded_not_lost(self, container) -> None:
        await container.startup()

        class _Broken:
            async def run(self, *, dry_run=False):
                raise RuntimeError("fetcher exploded")

        container.refresh_pipeline = _Broken()
        with _client(container, role="Admin") as client:
            client.post("/api/v3/admin/refresh", json={})
            done = _wait_until_done(client)
        assert (done["status"], done["error"]) == ("failed", "RuntimeError")

    async def test_the_trigger_is_audited(self, container) -> None:
        await container.startup()
        with _client(container, role="Admin") as client:
            client.post("/api/v3/admin/refresh", json={})
            _wait_until_done(client)
            events = client.get("/api/v3/admin/audit?action=admin.refresh.trigger").json()["events"]
        assert len(events) == 1 and events[0]["actor"] == "op@example.com"

    async def test_latest_before_any_run_has_no_run(self, container) -> None:
        await container.startup()
        with _client(container, role="Admin") as client:
            body = client.get("/api/v3/admin/refresh/latest").json()
        assert body["run"] is None
