"""``Idempotency-Key`` over the real v3 ASGI app (roadmap P1.4).

Driven through ``build_app`` rather than by calling the dependency, because what goes wrong
lives in the wiring: a replay that runs the endpoint anyway, a claim the route class never
settles, a header that silently changes the behaviour of a request that did not send it.
"""

from __future__ import annotations

import asyncio

import pytest

from aura_core.api import Principal, build_app
from aura_core.domain.errors import DependencyUnavailableError
from aura_core.domain.identifiers import UserId

pytest.importorskip("fastapi")

MEMORY = "/api/v3/memory"


def _authenticator():
    async def authenticate(request):
        return Principal(
            user_id=UserId(request.headers.get("x-test-user", "alice")),
            email="u@example.com", role=request.headers.get("x-test-role", "User"),
        )

    return authenticate


@pytest.fixture
def client(container):
    from fastapi.testclient import TestClient

    with TestClient(build_app(container, authenticator=_authenticator())) as test_client:
        yield test_client


def _key(value: str, **extra: str) -> dict[str, str]:
    return {"Idempotency-Key": value, **extra}


async def _memories(container, user: str = "alice") -> list:
    return list(await container.memory_store.list_for_owner(UserId(user), limit=100))


class TestWithoutTheHeader:
    async def test_behaviour_is_unchanged(self, client, container) -> None:
        first = client.post(MEMORY, json={"content": "I take my tea with milk."})
        second = client.post(MEMORY, json={"content": "I prefer green tea."})
        assert first.status_code == second.status_code == 200
        assert "idempotent-replayed" not in first.headers
        assert "idempotent-replayed" not in second.headers
        assert len(await _memories(container)) == 2
        assert len(container.idempotency) == 0  # nothing claimed, nothing stored


class TestReplay:
    async def test_a_retry_replays_instead_of_writing_again(self, client, container) -> None:
        first = client.post(MEMORY, json={"content": "I take my tea with milk."}, headers=_key("k-1"))
        retry = client.post(MEMORY, json={"content": "I take my tea with milk."}, headers=_key("k-1"))

        assert first.status_code == retry.status_code == 200
        assert retry.json() == first.json()
        assert retry.headers["idempotent-replayed"] == "true"
        assert "idempotent-replayed" not in first.headers
        assert retry.headers["content-type"] == first.headers["content-type"]
        assert len(await _memories(container)) == 1

    async def test_a_reordered_json_body_is_the_same_request(self, client) -> None:
        first = client.post(
            MEMORY, content=b'{"content": "Likes jazz", "kind": "semantic"}',
            headers=_key("k-order", **{"content-type": "application/json"}),
        )
        retry = client.post(
            MEMORY, content=b'{"kind":"semantic","content":"Likes jazz"}',
            headers=_key("k-order", **{"content-type": "application/json"}),
        )
        assert retry.status_code == 200
        assert retry.headers.get("idempotent-replayed") == "true"
        assert retry.json() == first.json()

    async def test_a_quoted_structured_field_key_is_the_same_key(self, client) -> None:
        client.post(MEMORY, json={"content": "Prefers aisle seats."}, headers=_key("abc"))
        retry = client.post(MEMORY, json={"content": "Prefers aisle seats."}, headers=_key('"abc"'))
        assert retry.headers.get("idempotent-replayed") == "true"

    async def test_a_delete_retry_replays_the_first_answer_not_a_404(
        self, client, container
    ) -> None:
        memory_id = client.post(MEMORY, json={"content": "Allergic to peanuts."}).json()["id"]
        first = client.delete(f"{MEMORY}/{memory_id}", headers=_key("del-1"))
        retry = client.delete(f"{MEMORY}/{memory_id}", headers=_key("del-1"))
        assert first.status_code == 200
        # Without the key the second delete would 404; with it the client sees what the first
        # (possibly lost) response said.
        assert retry.status_code == 200 and retry.headers["idempotent-replayed"] == "true"
        unkeyed = client.delete(f"{MEMORY}/{memory_id}")
        assert unkeyed.status_code == 404

    async def test_a_chat_turn_is_replayed_not_rerun(self, client, container) -> None:
        body = {"message": "What is the leave policy?", "use_tools": False}
        first = client.post("/api/v3/chat", json=body, headers=_key("turn-1"))
        retry = client.post("/api/v3/chat", json=body, headers=_key("turn-1"))
        assert first.status_code == 200, first.text
        assert retry.headers["idempotent-replayed"] == "true"
        assert retry.json()["turn_id"] == first.json()["turn_id"]
        assert retry.headers["x-correlation-id"] == first.headers["x-correlation-id"]

    async def test_a_document_upload_is_replayed_and_indexed_once(
        self, client, container
    ) -> None:
        from tests.unit.test_document_extraction import _pdf

        await container.startup()
        pdf = _pdf([{"text": ["Refunds are processed within ten days."]}])
        files = {"file": ("refunds.pdf", pdf, "application/pdf")}
        first = client.post(
            "/api/v3/knowledge/documents/upload", files=files, data={"title": "Refunds"},
            headers=_key("up-1"),
        )
        retry = client.post(
            "/api/v3/knowledge/documents/upload", files=files, data={"title": "Refunds"},
            headers=_key("up-1"),
        )
        assert first.status_code == 200, first.text
        assert first.json()["created"] is True
        assert retry.headers["idempotent-replayed"] == "true"
        assert retry.json() == first.json()  # "created": true, not a skip
        documents = await container.documents.list_active(owner_id=UserId("alice"))
        assert len(documents) == 1

        different_file = client.post(
            "/api/v3/knowledge/documents/upload",
            files={"file": ("refunds.pdf", _pdf([{"text": ["Another text."]}]), "application/pdf")},
            data={"title": "Refunds"}, headers=_key("up-1"),
        )
        assert different_file.status_code == 422


class TestRejections:
    async def test_reusing_a_key_for_a_different_request_is_422(self, client, container) -> None:
        client.post(MEMORY, json={"content": "Lives in Cairo."}, headers=_key("k-2"))
        reused = client.post(MEMORY, json={"content": "Lives in Alexandria."}, headers=_key("k-2"))
        assert reused.status_code == 422
        assert reused.json()["detail"]["error"] == "idempotency_key_reused"
        assert len(await _memories(container)) == 1

    async def test_the_same_key_on_a_different_path_is_422(self, client) -> None:
        a = client.post(MEMORY, json={"content": "a"}).json()["id"]
        b = client.post(MEMORY, json={"content": "b"}).json()["id"]
        client.delete(f"{MEMORY}/{a}", headers=_key("del-2"))
        assert client.delete(f"{MEMORY}/{b}", headers=_key("del-2")).status_code == 422

    async def test_a_key_still_running_is_409(self, client, container) -> None:
        from datetime import timedelta

        # Claim the key exactly as an in-flight request would, then retry over HTTP.
        import hashlib
        body = b'{"content":"Runs marathons."}'
        digest = hashlib.sha256()
        for part in ("POST", MEMORY, ""):
            digest.update(part.encode())
            digest.update(b"\x00")
        digest.update(body)
        await container.idempotency.claim(
            user_id=UserId("alice"), operation=f"POST {MEMORY}", key="k-busy",
            request_hash=digest.hexdigest(), now=container.clock.now(),
            ttl=timedelta(hours=1), lease=timedelta(minutes=15),
        )
        busy = client.post(
            MEMORY, content=body, headers=_key("k-busy", **{"content-type": "application/json"}),
        )
        assert busy.status_code == 409
        assert busy.json()["detail"]["error"] == "idempotency_key_in_progress"
        assert busy.json()["detail"]["retryable"] is True
        assert busy.headers["retry-after"]
        assert await _memories(container) == []

    @pytest.mark.parametrize("bad", ["", "has space", "x" * 256, '""'])
    async def test_a_malformed_key_is_400(self, client, bad) -> None:
        response = client.post(
            MEMORY, json={"content": "irrelevant"}, headers={"Idempotency-Key": bad},
        )
        assert response.status_code == 400


class TestIsolation:
    async def test_two_users_with_the_same_key_each_get_their_own_write(
        self, client, container
    ) -> None:
        alice = client.post(MEMORY, json={"content": "Alice's secret."}, headers=_key("shared"))
        bob = client.post(
            MEMORY, json={"content": "Alice's secret."},
            headers=_key("shared", **{"x-test-user": "bob"}),
        )
        assert bob.status_code == 200
        assert "idempotent-replayed" not in bob.headers  # bob never sees alice's response
        assert bob.json()["id"] != alice.json()["id"]
        assert len(await _memories(container, "alice")) == 1
        assert len(await _memories(container, "bob")) == 1

    async def test_a_different_body_under_another_users_key_is_not_a_mismatch(
        self, client
    ) -> None:
        client.post(MEMORY, json={"content": "Alice's."}, headers=_key("shared-2"))
        bob = client.post(
            MEMORY, json={"content": "Bob's."}, headers=_key("shared-2", **{"x-test-user": "bob"}),
        )
        assert bob.status_code == 200


class TestFailures:
    async def test_a_server_error_is_not_replayed_and_the_retry_runs(
        self, client, container, monkeypatch
    ) -> None:
        real_remember = container.memory.remember
        calls = {"n": 0}

        async def flaky(**kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise DependencyUnavailableError("vector store blip", dependency="qdrant")
            return await real_remember(**kwargs)

        monkeypatch.setattr(container.memory, "remember", flaky)
        failed = client.post(MEMORY, json={"content": "Owns a bicycle."}, headers=_key("k-5xx"))
        retry = client.post(MEMORY, json={"content": "Owns a bicycle."}, headers=_key("k-5xx"))

        assert failed.status_code == 503
        assert retry.status_code == 200
        assert "idempotent-replayed" not in retry.headers
        assert calls["n"] == 2
        replay = client.post(MEMORY, json={"content": "Owns a bicycle."}, headers=_key("k-5xx"))
        assert replay.headers["idempotent-replayed"] == "true"
        assert calls["n"] == 2

    async def test_a_raised_error_releases_the_key_rather_than_being_replayed(
        self, client
    ) -> None:
        """Only a response the endpoint *returned* is stored. A raised error (here the typed 404
        for an unknown memory) marks the claim failed, so a retry re-runs -- which is what lets
        the same key succeed once the thing it names exists."""
        first = client.delete(f"{MEMORY}/never-existed", headers=_key("k-404"))
        retry = client.delete(f"{MEMORY}/never-existed", headers=_key("k-404"))
        assert first.status_code == retry.status_code == 404
        assert "idempotent-replayed" not in retry.headers

    async def test_a_body_validation_error_releases_the_key(self, client) -> None:
        invalid = client.post(MEMORY, json={"content": ""}, headers=_key("k-invalid"))
        assert invalid.status_code == 422
        again = client.post(MEMORY, json={"content": ""}, headers=_key("k-invalid"))
        assert again.status_code == 422
        assert "idempotent-replayed" not in again.headers


class TestConcurrentDuplicates:
    async def test_concurrent_requests_with_one_key_execute_exactly_once(
        self, container, monkeypatch
    ) -> None:
        import httpx

        real_remember = container.memory.remember
        executions = {"n": 0}

        async def slow_remember(**kwargs):
            executions["n"] += 1
            await asyncio.sleep(0.05)  # the window a real embedding call holds open
            return await real_remember(**kwargs)

        monkeypatch.setattr(container.memory, "remember", slow_remember)
        app = build_app(container, authenticator=_authenticator())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            responses = await asyncio.gather(*(
                http.post(MEMORY, json={"content": "Speaks Arabic."}, headers=_key("burst"))
                for _ in range(10)
            ))
            statuses = sorted(r.status_code for r in responses)
            assert statuses == [200] + [409] * 9
            assert executions["n"] == 1

            after = await http.post(MEMORY, json={"content": "Speaks Arabic."}, headers=_key("burst"))
            assert after.status_code == 200 and after.headers["idempotent-replayed"] == "true"

        assert executions["n"] == 1
        assert len(await _memories(container)) == 1


class TestAdminRoutes:
    async def test_an_opted_in_admin_route_honours_the_key(self, client) -> None:
        admin = {"x-test-role": "SuperAdmin"}
        body = {"id": "docs-src", "type": "web", "uri": "https://example.com/docs"}
        first = client.post("/api/v3/admin/sources", json=body, headers=_key("src-1", **admin))
        retry = client.post("/api/v3/admin/sources", json=body, headers=_key("src-1", **admin))
        assert first.status_code == 200, first.text
        assert retry.headers["idempotent-replayed"] == "true"
        assert retry.json() == first.json()
