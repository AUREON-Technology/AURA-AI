"""SaaS P1-H: billing is observable -- notification outcomes, email sends, and the cycle heartbeat.

Readiness item 5 asks for an alert on payment webhook failures. An alert needs a series to watch,
so these tests pin the series. The alert rules themselves are pinned in
``tests/unit/test_gpu_pools_and_monitoring.py``.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest

import services.jwt_auth as jwt_auth
import services.rbac as rbac
from aura_core.api.v3 import refresh_scrape_gauges
from aura_core.billing.service import BILLING_HEARTBEAT_KEY
from aura_service import _build_authenticator


@pytest.fixture(autouse=True)
def _people(monkeypatch):
    async def verify(request, credentials):
        person = credentials.credentials
        return {"uid": person, "email": f"{person}@example.com", "email_verified": True}

    monkeypatch.setattr(jwt_auth, "verify_access_token", verify)
    monkeypatch.setattr(rbac, "get_user_role", lambda claims: "User")


@pytest.fixture
def enabled(container):
    """Billing on, a live metrics catalogue (the test settings turn metrics off) and a real Notifier
    on the in-memory sender (the test settings turn email off)."""
    from types import SimpleNamespace

    from aura_core.adapters.email import InMemoryEmailSender
    from aura_core.notifications.service import Notifier
    from aura_core.observability.metrics import Metrics

    container.metrics = Metrics(enabled=True)
    container.billing.settings = replace(container.billing.settings, enabled=True)
    container.settings = replace(container.settings, billing=container.billing.settings)
    container.billing.notifier = Notifier(
        sender=InMemoryEmailSender(), settings=SimpleNamespace(app_base_url="https://app.example"),
        audit=container.audit, clock=container.clock, metrics=container.metrics,
    )
    return container.billing


@pytest.fixture
def client(container):
    from fastapi.testclient import TestClient

    from aura_core.api import build_app

    authenticator = _build_authenticator(container.settings, service_keys=container.service_keys, tenancy=container.tenancy)
    with TestClient(build_app(container, authenticator=authenticator), raise_server_exceptions=False) as test_client:
        yield test_client


def _as(person: str) -> dict:
    return {"authorization": f"Bearer {person}"}


def _rendered(container) -> str:
    raw = container.metrics.render()
    return raw.decode() if isinstance(raw, bytes) else raw


def _value(text: str, series: str) -> float:
    for line in text.splitlines():
        if line.startswith(series + " "):
            return float(line.split()[1])
    raise AssertionError(f"{series} not rendered")


def _paid_note(client, enabled) -> bytes:
    ws = next(w["id"] for w in client.get("/api/v3/workspaces", headers=_as("ann")).json()["workspaces"]
              if w["kind"] == "personal")
    checkout = client.post(f"/api/v3/workspaces/{ws}/billing/checkout", headers=_as("ann"),
                           json={"plan": "pro", "seats": 1}).json()
    return enabled.provider.notification(checkout["payment_id"], "paid", 15000)


def test_notify_outcomes_are_counted_by_source_and_result(client, container, enabled) -> None:
    note = _paid_note(client, enabled)
    assert client.post("/api/v3/billing/fawry/notify", content=note).json()["result"] == "applied"
    assert client.post("/api/v3/billing/fawry/notify", content=note).json()["result"] == "duplicate"
    forged = json.loads(note)
    forged["messageSignature"] = "0" * 64
    assert client.post("/api/v3/billing/fawry/notify", content=json.dumps(forged)).status_code == 403
    text = _rendered(container)
    for result in ("applied", "duplicate", "invalid_signature"):
        assert f'aura_billing_notifications_total{{result="{result}",source="fawry"}} 1.0' in text, result


def test_a_metrics_failure_never_changes_the_notify_answer(client, container, enabled, monkeypatch) -> None:
    """Review H: the counter is in-process, so the failure to rule out is the metric itself raising."""
    note = _paid_note(client, enabled)

    def broken(**labels):
        raise RuntimeError("metrics broken")

    monkeypatch.setattr(container.metrics.billing_notifications_total, "labels", broken)
    answer = client.post("/api/v3/billing/fawry/notify", content=note)
    assert answer.status_code == 200 and answer.json()["result"] == "applied"


def test_alerting_series_exist_at_zero_before_the_first_event(container, enabled) -> None:
    """Review H#1: increase() cannot see a series' first sample, so the series an alert watches
    must already exist at 0 -- or the first crash on a customer's payment would never page."""
    text = _rendered(container)
    for source in ("fawry", "apple", "google"):
        for result in ("error", "invalid_signature", "malformed", "too_large", "store_unavailable"):
            assert f'aura_billing_notifications_total{{result="{result}",source="{source}"}} 0.0' in text


def test_a_cache_outage_at_scrape_keeps_billing_marked_enabled(container, enabled, monkeypatch) -> None:
    """Review H#2: a Redis blip must not set billing_enabled to 0, which would silence every rule."""
    async def broken(key):
        raise ConnectionError("redis down")

    monkeypatch.setattr(container.cache_backend, "get", broken)
    asyncio.run(refresh_scrape_gauges(container))
    assert "aura_billing_enabled 1.0" in _rendered(container)


def test_a_cycle_that_raises_still_reports_an_error_and_releases_the_lock(container, enabled, monkeypatch) -> None:
    """Review H#4: an aborted cycle is counted, so AuraBillingCycleErrors can see it."""
    async def poison(*args, **kwargs):
        raise RuntimeError("poison")

    monkeypatch.setattr(enabled.subscriptions, "due", poison)

    async def scenario():
        with pytest.raises(RuntimeError):
            await enabled.run_cycle()
        beat = json.loads(await container.cache_backend.get(BILLING_HEARTBEAT_KEY))
        assert beat["errors"] >= 1
        monkeypatch.undo()
        assert not (await enabled.run_cycle()).get("skipped"), "the cycle lock was not released"

    asyncio.run(scenario())


def test_a_client_disconnect_is_not_a_crash(client, container, enabled, monkeypatch) -> None:
    """Review H#5: a provider hanging up mid-body must not page as 'crashed the gateway'."""
    from starlette.requests import ClientDisconnect

    async def gone(body):
        raise ClientDisconnect()

    monkeypatch.setattr(enabled, "handle_notification", gone)
    client.post("/api/v3/billing/fawry/notify", content=b"{}")
    text = _rendered(container)
    assert 'aura_billing_notifications_total{result="error",source="fawry"} 0.0' in text
    assert 'aura_billing_notifications_total{result="client_disconnect",source="fawry"} 1.0' in text


def test_an_unexpected_error_is_counted_and_still_raises(client, container, enabled, monkeypatch) -> None:
    async def boom(body):
        raise RuntimeError("bug")

    monkeypatch.setattr(enabled, "handle_notification", boom)
    assert client.post("/api/v3/billing/fawry/notify", content=b"{}").status_code == 500
    assert 'aura_billing_notifications_total{result="error",source="fawry"} 1.0' in _rendered(container)


def test_the_cycle_writes_a_heartbeat_and_the_scrape_reads_it(container, enabled) -> None:
    async def scenario():
        counts = await enabled.run_cycle()
        assert not counts.get("skipped")
        raw = await container.cache_backend.get(BILLING_HEARTBEAT_KEY)
        beat = json.loads(raw)
        assert beat["at"] > 0 and beat["errors"] == 0 and beat["email_failures"] == 0
        await refresh_scrape_gauges(container)
        text = _rendered(container)
        assert "aura_billing_enabled 1.0" in text
        assert _value(text, "aura_billing_cycle_last_run_timestamp_seconds") == pytest.approx(beat["at"])

    asyncio.run(scenario())


def test_cycle_email_failures_reach_the_gauge(container, enabled, monkeypatch) -> None:
    """The cycle runs in a cron process nobody scrapes: its email failures travel in the heartbeat."""

    class FailingSender:
        async def send(self, message):
            raise OSError("smtp down")

    notifier = enabled.notifier
    assert notifier is not None
    notifier.sender = FailingSender()
    original_received = enabled.payments.received

    async def received_with_two_failed_mails():
        # Inside the cycle's run, as a renewal reminder and a past-due notice would be.
        await notifier.past_due("a@example.com", "A", None)
        await notifier.past_due("b@example.com", "B", None)
        return await original_received()

    monkeypatch.setattr(enabled.payments, "received", received_with_two_failed_mails)

    async def scenario():
        await notifier.past_due("before@example.com", "X", None)  # outside the run: not counted
        await enabled.run_cycle()
        beat = json.loads(await container.cache_backend.get(BILLING_HEARTBEAT_KEY))
        assert beat["email_failures"] == 2
        await refresh_scrape_gauges(container)
        assert "aura_billing_cycle_last_email_failures 2.0" in _rendered(container)

    asyncio.run(scenario())


def test_no_heartbeat_reads_as_zero_not_healthy(container, enabled) -> None:
    async def scenario():
        await container.cache_backend.delete(BILLING_HEARTBEAT_KEY)
        await refresh_scrape_gauges(container)
        text = _rendered(container)
        assert "aura_billing_enabled 1.0" in text
        assert "aura_billing_cycle_last_run_timestamp_seconds 0.0" in text

    asyncio.run(scenario())


def test_email_sends_are_counted_without_the_address(container, enabled) -> None:
    async def scenario():
        await enabled.notifier.past_due("someone@example.com", "W", None)

    asyncio.run(scenario())
    text = _rendered(container)
    assert 'aura_email_sends_total{result=' in text and 'tag="past_due"' in text
    assert "someone@example.com" not in text
