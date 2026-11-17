"""Prompt pins are shared state, not one worker's memory (INT-004).

The gateway runs several uvicorn workers (``--workers ${AURA_WORKERS:-4}``), each with its own
``Container`` and so its own ``PromptRegistry``. A pin kept only in the registry that served the
admin request reached roughly a quarter of traffic and vanished on restart -- the console's
"rollback, no deploy required" was not one. These tests stand two registries (and two whole
containers / apps) on one pin store, the in-memory stand-in for the shared Postgres table, and
require a pin made through one to be what the other resolves.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from aura_core.adapters.prompt_pins import InMemoryPromptPinStore
from aura_core.api import Principal, build_app
from aura_core.config import load_settings
from aura_core.container import Container
from aura_core.domain.errors import NotFoundError
from aura_core.domain.identifiers import UserId
from aura_core.prompts import PromptRegistry, PromptTemplate, build_default_registry

TEMPLATE = "system.orchestrator"


def _run(coro):
    return asyncio.run(coro)


OLDER = "0.9.0"


def _with_older(registry: PromptRegistry) -> PromptRegistry:
    """Give ``TEMPLATE`` an earlier version to roll back to, as a release that bumped it would.
    Every worker runs the same build, so each registry in a test gets the same two versions."""
    current = registry.get(TEMPLATE)
    registry.register(replace(current, version=OLDER, body=current.body + "\n(previous wording)"))
    return registry


def _registry(store) -> PromptRegistry:
    return _with_older(build_default_registry(pins=store))


def _older_version(registry: PromptRegistry, template_id: str = TEMPLATE) -> str:
    assert OLDER in registry.versions(template_id)
    return OLDER


class TestRegistriesOnOneStore:
    def test_a_pin_made_through_one_worker_is_resolved_by_another(self) -> None:
        store = InMemoryPromptPinStore()
        worker_a, worker_b = _registry(store), _registry(store)
        older = _older_version(worker_a)
        latest = worker_b.get(TEMPLATE).version
        assert older != latest

        _run(worker_a.set_pin(TEMPLATE, older, pinned_by="admin"))
        # The writing worker sees it at once, without waiting for a sync.
        assert worker_a.get(TEMPLATE).version == older

        _run(worker_b.refresh_pins())
        assert worker_b.get(TEMPLATE).version == older
        described = {row["id"]: row for row in worker_b.describe()}
        assert described[TEMPLATE]["pinned"] == older
        assert described[TEMPLATE]["active"] == older

    def test_an_unpin_through_one_worker_releases_every_worker(self) -> None:
        store = InMemoryPromptPinStore()
        worker_a, worker_b = _registry(store), _registry(store)
        older = _older_version(worker_a)
        latest = worker_a.get(TEMPLATE).version

        _run(worker_a.set_pin(TEMPLATE, older, pinned_by="admin"))
        _run(worker_b.refresh_pins())
        assert worker_b.get(TEMPLATE).version == older

        _run(worker_a.clear_pin(TEMPLATE))
        _run(worker_b.refresh_pins())
        assert worker_b.get(TEMPLATE).version == latest
        assert {r["id"]: r for r in worker_b.describe()}[TEMPLATE]["pinned"] is None

    def test_a_pin_survives_a_restart(self) -> None:
        store = InMemoryPromptPinStore()
        before = _registry(store)
        older = _older_version(before)
        _run(before.set_pin(TEMPLATE, older, pinned_by="admin"))

        after_restart = _registry(store)
        _run(after_restart.refresh_pins())
        assert after_restart.get(TEMPLATE).version == older

    def test_pinning_an_unknown_version_writes_nothing(self) -> None:
        store = InMemoryPromptPinStore()
        registry = _registry(store)
        with pytest.raises(NotFoundError):
            _run(registry.set_pin(TEMPLATE, "99.0.0", pinned_by="admin"))
        assert _run(store.load_all()) == {}

    def test_a_stored_pin_this_worker_does_not_have_is_ignored_not_fatal(self) -> None:
        """A pin to a version another build registered (a rolling deploy, a removed version) must
        not make every turn on this worker fail with NotFoundError -- it serves the default."""
        store = InMemoryPromptPinStore()
        _run(store.set(TEMPLATE, "42.0.0", pinned_by="admin"))
        _run(store.set("no.such.template", "1.0.0", pinned_by="admin"))
        registry = _registry(store)
        latest = registry.get(TEMPLATE).version

        _run(registry.refresh_pins())
        assert registry.get(TEMPLATE).version == latest
        assert {r["id"]: r for r in registry.describe()}[TEMPLATE]["pinned"] is None

    def test_a_refresh_that_read_before_a_local_write_does_not_undo_it(self) -> None:
        """Interleaving guard: a sync whose read started before this worker's own pin landed must
        not overwrite the newer local state with its older snapshot."""
        store = InMemoryPromptPinStore()
        registry = _registry(store)
        older = _older_version(registry)

        class SlowStore(InMemoryPromptPinStore):
            def __init__(self, inner) -> None:
                super().__init__()
                self.inner = inner
                self.release = asyncio.Event()

            async def load_all(self):
                snapshot = await self.inner.load_all()
                await self.release.wait()
                return snapshot

            async def set(self, template_id, version, *, pinned_by=None):
                await self.inner.set(template_id, version, pinned_by=pinned_by)

        async def scenario() -> str:
            slow = SlowStore(store)
            reg = _with_older(build_default_registry(pins=slow))
            refresh = asyncio.ensure_future(reg.refresh_pins())
            await asyncio.sleep(0)
            await reg.set_pin(TEMPLATE, older, pinned_by="admin")
            slow.release.set()
            await refresh
            return reg.get(TEMPLATE).version

        assert _run(scenario()) == older

    def test_a_registry_without_a_store_keeps_the_local_pin_api(self) -> None:
        registry = PromptRegistry()
        registry.register(PromptTemplate(id="t", version="1.0.0", body="a"))
        registry.register(PromptTemplate(id="t", version="1.1.0", body="b"))
        _run(registry.set_pin("t", "1.0.0"))
        assert registry.get("t").version == "1.0.0"
        _run(registry.refresh_pins())  # no store: a no-op, never clears the local pin
        assert registry.get("t").version == "1.0.0"
        _run(registry.clear_pin("t"))
        assert registry.get("t").version == "1.1.0"


def _admin_client(container):
    from fastapi.testclient import TestClient

    async def admin(request):
        return Principal(
            user_id=UserId("ops"), email="ops@example.com", role="Admin",
            capabilities=("admin.act",),
        )

    return TestClient(build_app(container, authenticator=admin))


class TestTwoGatewayWorkers:
    """Two containers -- two workers -- whose pin store is the same shared table."""

    @pytest.fixture
    def workers(self, monkeypatch):
        pytest.importorskip("fastapi")
        shared = InMemoryPromptPinStore()
        monkeypatch.setattr(Container, "_build_prompt_pins", lambda self: shared)
        settings = load_settings(refresh=True, validate=False)
        a, b = Container(settings), Container(settings)
        _with_older(a.prompts), _with_older(b.prompts)
        return a, b

    def test_pin_through_worker_a_is_reported_and_resolved_by_worker_b(self, workers) -> None:
        a, b = workers
        older = _older_version(a.prompts)
        with _admin_client(a) as client_a, _admin_client(b) as client_b:
            pinned = client_a.post(f"/api/v3/admin/prompts/{TEMPLATE}/pin", json={"version": older})
            assert pinned.status_code == 200, pinned.text
            assert pinned.json()["pinned"] == f"{TEMPLATE}@{older}"

            # /admin/status on the other worker reads the shared pin, not its own stale copy.
            status = client_b.get("/api/v3/admin/status").json()
            prompts = {row["id"]: row for row in status["prompts"]}
            assert prompts[TEMPLATE]["pinned"] == older
            assert b.prompts.get(TEMPLATE).version == older

            released = client_b.delete(f"/api/v3/admin/prompts/{TEMPLATE}/pin")
            assert released.status_code == 200, released.text
            assert released.json()["unpinned"] == TEMPLATE
            status_a = client_a.get("/api/v3/admin/status").json()
            assert {r["id"]: r for r in status_a["prompts"]}[TEMPLATE]["pinned"] is None

    def test_a_restarted_worker_starts_with_the_pin(self, workers) -> None:
        a, _ = workers
        older = _older_version(a.prompts)
        _run(a.prompts.set_pin(TEMPLATE, older, pinned_by="ops"))
        fresh = Container(load_settings(refresh=True, validate=False))
        _with_older(fresh.prompts)
        with _admin_client(fresh):  # lifespan runs startup(), which loads the shared pins
            assert fresh.prompts.get(TEMPLATE).version == older


class TestCircuitResetScope:
    def test_reset_response_and_status_say_the_breaker_is_per_worker(self, container) -> None:
        pytest.importorskip("fastapi")
        with _admin_client(container) as client:
            body = client.post("/api/v3/admin/providers/echo/reset-circuit").json()
            assert body["reset"] == "echo"
            assert body["scope"] == "worker"
            assert isinstance(body["worker_pid"], int)
            status = client.get("/api/v3/admin/status").json()
            assert status["providers_scope"] == "worker"
            assert isinstance(status["worker_pid"], int)
