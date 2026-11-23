"""The Cloudflare tab's two read-only views over HTTP: usage and R2 (console plan, stage 6, C3).

Next to ``test_control_cloudflare_api.py``: the same real app and container wiring, an in-memory
Cloudflare account. The role sweep in ``test_control_center.py`` covers who may call them (it lists
both routes); this file covers what they answer. Every Cloudflare-side outcome is a named ``state`` in
a 200 response, never an error, and neither route needs a step-up grant (they change nothing).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from cryptography.fernet import Fernet

from aura_core.domain.control import Platform, PlatformCredential
from aura_core.ports.control_plane import CloudflareBucket

ROOT = "root@aura.ai"
ACCOUNT = "0123456789abcdef0123456789abcdef"
BASE = "/api/v3/admin/control/cloudflare"


@pytest.fixture(autouse=True)
def vault_key(monkeypatch):
    monkeypatch.setenv("AURA_SECRETS_ENCRYPTION_KEY", Fernet.generate_key().decode())


async def _container(*, connected: bool = True):
    from aura_core.config import load_settings
    from aura_core.container import Container

    container = Container(load_settings(refresh=True, validate=False))
    if connected:
        await container.control_store.put_credential(PlatformCredential(
            platform=Platform.CLOUDFLARE, secret_ref="ref-cloudflare", configured_by=ROOT,
            configured_at=datetime.now(timezone.utc), target=ACCOUNT,
        ))
    return container


def _client(container, *, role: str = "SuperAdmin"):
    from fastapi.testclient import TestClient

    from aura_core.api import Principal, build_app
    from aura_core.domain.identifiers import UserId

    async def authenticator(request):
        return Principal(user_id=UserId("root"), email=ROOT, role=role)

    return TestClient(build_app(container, authenticator=authenticator))


def _reason(response) -> str:
    return response.json()["details"]["reason"]


class TestUsage:
    async def test_it_reports_today_against_the_allowance_without_a_step_up(self) -> None:
        container = await _container()
        container.cloudflare_account.record_usage(datetime.now(timezone.utc).date(), requests=12, neurons=345.5)
        with _client(container) as client:
            response = client.get(f"{BASE}/usage")
        body = response.json()
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        assert (body["state"], body["requests"], body["neurons"], body["neurons_available"]) == ("ok", 12, 345.5, True)
        assert (body["allowance_neurons"], body["allowance_basis"]) == (10000, "free_plan")
        assert body["account_id"] == ACCOUNT and body["resets_at"].endswith("T00:00:00Z")

    async def test_a_missing_permission_is_a_state_with_the_permission_named(self) -> None:
        container = await _container()
        container.cloudflare_account.analytics_permission = False
        with _client(container) as client:
            response = client.get(f"{BASE}/usage")
        body = response.json()
        assert response.status_code == 200
        assert (body["state"], body["permission"], body["requests"]) == ("no_permission", "Account Analytics: Read", None)

    async def test_an_unreachable_cloudflare_is_a_state_not_a_500(self) -> None:
        container = await _container()
        container.cloudflare_account.available = False
        with _client(container) as client:
            response = client.get(f"{BASE}/usage")
        assert response.status_code == 200 and response.json()["state"] == "unavailable"

    async def test_no_neuron_figure_is_said_plainly(self) -> None:
        container = await _container()
        container.cloudflare_account.neurons_reported = False
        with _client(container) as client:
            body = client.get(f"{BASE}/usage").json()
        assert (body["state"], body["neurons"], body["neurons_available"]) == ("ok", None, False)

    async def test_it_needs_cloudflare_connected(self) -> None:
        container = await _container(connected=False)
        with _client(container) as client:
            response = client.get(f"{BASE}/usage")
        assert response.status_code == 422 and _reason(response) == "cloudflare_not_connected"

    async def test_only_a_super_admin_may_read_it(self) -> None:
        container = await _container()
        with _client(container, role="Admin") as client:
            assert client.get(f"{BASE}/usage").status_code == 403
        assert container.cloudflare_account.usage_calls == []


class TestR2:
    async def test_it_lists_the_buckets(self) -> None:
        container = await _container()
        container.cloudflare_account.add_bucket(
            CloudflareBucket(name="aura-backups", created="2026-10-01T09:30:00.000Z", location="WEUR", lock_rules=1))
        container.cloudflare_account.add_bucket(CloudflareBucket(name="scratch", created="2026-11-02T00:00:00.000Z"))
        with _client(container) as client:
            response = client.get(f"{BASE}/r2")
        body = response.json()
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        assert body["state"] == "ok" and body["account_id"] == ACCOUNT
        assert [(b["name"], b["lock_rules"]) for b in body["buckets"]] == [("aura-backups", 1), ("scratch", None)]
        assert body["buckets"][0]["location"] == "WEUR"

    async def test_r2_not_enabled_is_a_state_not_an_error(self) -> None:
        container = await _container()
        container.cloudflare_account.r2_enabled = False
        with _client(container) as client:
            response = client.get(f"{BASE}/r2")
        body = response.json()
        assert response.status_code == 200 and (body["state"], body["buckets"]) == ("not_enabled", [])

    async def test_a_missing_permission_names_it(self) -> None:
        container = await _container()
        container.cloudflare_account.r2_permission = False
        with _client(container) as client:
            body = client.get(f"{BASE}/r2").json()
        assert (body["state"], body["permission"]) == ("no_permission", "Workers R2 Storage: Read")

    async def test_an_unreachable_cloudflare_is_a_state(self) -> None:
        container = await _container()
        container.cloudflare_account.available = False
        with _client(container) as client:
            assert client.get(f"{BASE}/r2").json()["state"] == "unavailable"

    async def test_it_needs_cloudflare_connected(self) -> None:
        container = await _container(connected=False)
        with _client(container) as client:
            response = client.get(f"{BASE}/r2")
        assert response.status_code == 422 and _reason(response) == "cloudflare_not_connected"

    async def test_only_a_super_admin_may_read_it(self) -> None:
        container = await _container()
        with _client(container, role="Admin") as client:
            assert client.get(f"{BASE}/r2").status_code == 403
        assert container.cloudflare_account.r2_calls == []
