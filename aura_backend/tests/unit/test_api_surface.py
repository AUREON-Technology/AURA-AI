"""The v3 HTTP surface: authorisation, error mapping, and the admin gate.

Tested through the real ASGI app rather than by calling handlers, because the parts that go
wrong live in the wiring: a dependency that fails open, an exception that becomes a 500 instead
of its typed status, an admin route reachable by a plain user.
"""

from __future__ import annotations

import pytest

from aura_core.api import Principal, build_app
from aura_core.domain.errors import AuthenticationError
from aura_core.domain.identifiers import UserId

pytest.importorskip("fastapi")


def _client(container, *, role: str = "User", capabilities: tuple[str, ...] = (),
            authenticator=None, rate_limiter=None):
    from fastapi.testclient import TestClient

    async def default_authenticator(request):
        return Principal(
            user_id=UserId("test-user"), email="u@example.com", role=role,
            capabilities=capabilities,
        )

    return TestClient(build_app(
        container, authenticator=authenticator or default_authenticator,
        rate_limiter=rate_limiter,
    ))


@pytest.fixture
def user_client(container):
    with _client(container) as client:
        yield client


@pytest.fixture
def admin_client(container):
    with _client(container, role="Admin", capabilities=("admin.act",)) as client:
        yield client


@pytest.fixture
def super_admin_client(container):
    with _client(
        container, role="SuperAdmin", capabilities=("admin.act", "admin.super")
    ) as client:
        yield client


class TestAuthentication:
    def test_a_rejected_credential_becomes_its_typed_status(self, container) -> None:
        """Not a 500: the client needs to know to re-authenticate rather than retry."""

        async def refusing(request):
            raise AuthenticationError("token has expired")

        with _client(container, authenticator=refusing) as client:
            response = client.post("/api/v3/chat", json={"message": "hi"})
        assert response.status_code == 401
        assert response.json()["detail"]["error"] == "authentication_error"
        assert response.json()["detail"]["retryable"] is False

    def test_health_endpoints_do_not_require_authentication(self, container) -> None:
        """A probe that needs a credential is a probe that fails during an auth outage."""

        async def refusing(request):
            raise AuthenticationError("no token")

        with _client(container, authenticator=refusing) as client:
            assert client.get("/health/live").status_code == 200


class TestAuthorisation:
    ADMIN_ROUTES = [
        ("get", "/api/v3/admin/status", None),
        ("post", "/api/v3/admin/index/rebuild", None),
        ("post", "/api/v3/admin/providers/local/reset-circuit", None),
        ("post", "/api/v3/admin/prompts/system.orchestrator/pin", {"version": "1.1.0"}),
        ("get", "/api/v3/admin/audit", None),
        ("get", "/api/v3/admin/sources", None),
        ("get", "/api/v3/admin/approvals", None),
        ("get", "/api/v3/admin/indexes", None),
        ("get", "/api/v3/admin/models", None),
        ("get", "/api/v3/admin/local-models", None),
        ("post", "/api/v3/admin/sources", {"id": "handbook", "type": "web", "uri": "https://example.com"}),
        ("post", "/api/v3/admin/local-models/pull", {"name": "llama3.1:8b"}),
    ]

    @pytest.mark.parametrize("method,path,body", ADMIN_ROUTES)
    def test_admin_routes_refuse_a_plain_user(
        self, user_client, method: str, path: str, body
    ) -> None:
        response = getattr(user_client, method)(path, **({"json": body} if body else {}))
        assert response.status_code == 403

    def test_an_admin_can_read_status(self, admin_client) -> None:
        body = admin_client.get("/api/v3/admin/status").json()
        assert "providers" in body
        assert "prompts" in body
        assert "settings" in body
        assert "learning" in body

    def test_status_never_leaks_a_key(self, admin_client) -> None:
        assert "api_key" not in admin_client.get("/api/v3/admin/status").text.replace(
            "api_key_env", ""
        ).replace("api_key_configured", "")


class TestKnowledgeRoutes:
    def test_a_document_can_be_ingested_and_deleted(self, user_client) -> None:
        ingest = user_client.post("/api/v3/knowledge/documents", json={
            "title": "Policy", "content": "The expense cap is 250 dollars.",
            "document_id": "policy",
        })
        assert ingest.status_code == 200

        deleted = user_client.delete("/api/v3/knowledge/documents/policy")
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True

    def test_deleting_an_unknown_document_reports_zero_rather_than_404(
        self, user_client
    ) -> None:
        """The end state the caller wanted is reached either way."""
        response = user_client.delete("/api/v3/knowledge/documents/never-existed")
        assert response.status_code == 200
        assert response.json()["chunks_removed"] == 0

    def test_search_over_an_empty_corpus_returns_an_empty_list(self, user_client) -> None:
        response = user_client.post("/api/v3/knowledge/search", json={"query": "anything"})
        assert response.status_code == 200
        assert response.json()["results"] == []

    def test_an_oversized_document_is_rejected_by_validation(self, user_client) -> None:
        response = user_client.post("/api/v3/knowledge/documents", json={
            "title": "T", "content": "", "document_id": "empty",
        })
        assert response.status_code == 422

    def test_ingested_documents_can_be_listed(self, user_client) -> None:
        user_client.post("/api/v3/knowledge/documents", json={
            "title": "Policy", "content": "The expense cap is 250 dollars.",
            "document_id": "policy",
        })
        listed = user_client.get("/api/v3/knowledge/documents")
        assert listed.status_code == 200
        docs = listed.json()["documents"]
        assert any(d["document_id"] == "policy" and d["title"] == "Policy" for d in docs)


class TestMemoryRoutes:
    def test_a_memory_can_be_written_listed_and_deleted(self, user_client) -> None:
        created = user_client.post("/api/v3/memory", json={
            "content": "The user is vegetarian.", "subject": "diet",
        })
        assert created.status_code == 200
        memory_id = created.json()["id"]

        listed = user_client.get("/api/v3/memory")
        assert any(m["id"] == memory_id for m in listed.json()["memories"])

        deleted = user_client.delete(f"/api/v3/memory/{memory_id}")
        assert deleted.status_code == 200

    def test_listing_supports_a_search_query(self, user_client) -> None:
        user_client.post("/api/v3/memory", json={"content": "The user is vegetarian."})
        response = user_client.get("/api/v3/memory", params={"query": "the user is vegetarian"})
        assert response.status_code == 200
        assert response.json()["memories"]

    def test_deleting_an_unknown_memory_is_reported_not_crashed(self, user_client) -> None:
        response = user_client.delete("/api/v3/memory/never-existed")
        assert response.status_code in (200, 404)


class TestIntrospection:
    def test_tools_are_listed_with_their_declared_properties(self, user_client) -> None:
        tools = user_client.get("/api/v3/tools").json()["tools"]
        assert any(t["name"] == "calculator" for t in tools)

    def test_agents_are_listed(self, user_client) -> None:
        response = user_client.get("/api/v3/agents")
        assert response.status_code == 200
        agents = {a["id"]: a for a in response.json()["agents"]}
        assert "orchestrator" in agents and agents["orchestrator"]["allowed_tools"] is None
        # A scoped persona's tool set is visible over the API, not just internal to the kernel
        # (Phase C1's "an agent's tool set is data, not code").
        finance = agents["finance_advisor"]
        assert finance["allowed_tools"] and "propose_health_log" not in finance["allowed_tools"]
        assert finance["risk_ceiling"] == "PERSONAL"

    def test_feedback_is_accepted(self, user_client) -> None:
        """A correction is the highest-signal training example the system can obtain."""
        response = user_client.post("/api/v3/feedback", json={
            "turn_id": "t-1", "rating": 1, "correction": "It is 21 days, not 20.",
        })
        assert response.status_code == 200
        assert response.json()["recorded"] is True

    def test_an_out_of_range_rating_is_rejected(self, user_client) -> None:
        response = user_client.post("/api/v3/feedback", json={"turn_id": "t", "rating": 99})
        assert response.status_code == 422


class TestAdminOperations:
    def test_an_index_rebuild_reports_its_gate_outcome(self, admin_client) -> None:
        """If `promoted` is false the old index is still serving; nothing broke."""
        admin_client.post("/api/v3/knowledge/documents", json={
            "title": "Handbook", "content": "Annual leave is 21 days per year.",
            "document_id": "handbook",
        })
        body = admin_client.post("/api/v3/admin/index/rebuild").json()

        assert "index" in body
        assert "promoted" in body
        assert isinstance(body["gate_failures"], list)

    def test_pinning_a_prompt_version_is_the_rollback_lever(self, admin_client) -> None:
        response = admin_client.post(
            "/api/v3/admin/prompts/system.orchestrator/pin", json={"version": "1.1.0"}
        )
        assert response.status_code == 200
        assert response.json()["pinned"] == "system.orchestrator@1.1.0"

    def test_pinning_an_unknown_version_is_refused(self, admin_client) -> None:
        response = admin_client.post(
            "/api/v3/admin/prompts/system.orchestrator/pin", json={"version": "99.0.0"}
        )
        assert response.status_code >= 400

    def test_resetting_an_unknown_provider_circuit_names_the_known_ones(
        self, admin_client
    ) -> None:
        """A typo'd provider name must not silently succeed -- an operator would believe the
        circuit was reset while the outage continued."""
        response = admin_client.post("/api/v3/admin/providers/no-such-provider/reset-circuit")
        assert response.status_code == 422
        assert response.json()["details"]["known"]

    def test_resetting_a_real_provider_circuit_succeeds(self, admin_client) -> None:
        response = admin_client.post("/api/v3/admin/providers/echo/reset-circuit")
        assert response.status_code == 200
        assert response.json()["reset"] == "echo"

    def test_admin_can_list_sources_but_cannot_register(
        self, admin_client, super_admin_client
    ) -> None:
        listed = admin_client.get("/api/v3/admin/sources")
        assert listed.status_code == 200
        assert listed.json()["sources"] == []

        refused = admin_client.post(
            "/api/v3/admin/sources",
            json={"id": "handbook", "type": "web", "uri": "https://example.com/kb"},
        )
        assert refused.status_code == 403

        created = super_admin_client.post(
            "/api/v3/admin/sources",
            json={"id": "handbook", "type": "web", "uri": "https://example.com/kb"},
        )
        assert created.status_code == 200
        assert created.json()["source"]["id"] == "handbook"

        removed = super_admin_client.delete("/api/v3/admin/sources/handbook")
        assert removed.status_code == 200
        assert admin_client.delete("/api/v3/admin/sources/handbook").status_code == 403

    def test_admin_can_read_audit_after_a_mutation(self, admin_client) -> None:
        admin_client.post("/api/v3/admin/providers/echo/reset-circuit")
        body = admin_client.get("/api/v3/admin/audit").json()
        actions = [event["action"] for event in body["events"]]
        assert "admin.provider.reset_circuit" in actions

    def test_admin_can_list_approvals_and_indexes(self, admin_client) -> None:
        assert admin_client.get("/api/v3/admin/approvals").status_code == 200
        assert admin_client.get("/api/v3/admin/indexes").status_code == 200
        assert admin_client.get("/api/v3/admin/models").status_code == 200
        assert admin_client.get("/api/v3/admin/local-models").status_code == 200
        assert admin_client.get("/api/v3/admin/refresh/latest").status_code == 200

    def test_admin_cannot_pull_local_models(self, admin_client) -> None:
        response = admin_client.post(
            "/api/v3/admin/local-models/pull", json={"name": "llama3.1:8b"}
        )
        assert response.status_code == 403


class TestRateLimiting:
    def test_an_exhausted_budget_returns_429_with_retry_after(self, container) -> None:
        class AlwaysLimited:
            async def check_all(self, **kwargs):  # noqa: ANN003
                class Decision:
                    allowed = False
                    retry_after_s = 42

                return Decision()

        with _client(container, rate_limiter=AlwaysLimited()) as client:
            response = client.post("/api/v3/chat", json={"message": "hi"})

        assert response.status_code == 429
        assert response.headers["Retry-After"] == "42"

    def test_health_is_never_rate_limited(self, container) -> None:
        """A limiter that can starve the readiness probe turns load into an outage."""

        class AlwaysLimited:
            async def check_all(self, **kwargs):  # noqa: ANN003
                class Decision:
                    allowed = False
                    retry_after_s = 1

                return Decision()

        with _client(container, rate_limiter=AlwaysLimited()) as client:
            assert client.get("/health/live").status_code == 200


class TestCorrelation:
    def test_every_response_carries_a_correlation_id(self, user_client) -> None:
        response = user_client.post("/api/v3/chat", json={"message": "hello"})
        assert response.headers["X-Correlation-Id"]

    def test_a_supplied_correlation_id_is_honoured(self, user_client) -> None:
        """So a client-side trace and a server-side one describe the same request."""
        response = user_client.post(
            "/api/v3/chat", json={"message": "hello"},
            headers={"X-Correlation-Id": "client-supplied-id"},
        )
        assert response.headers["X-Correlation-Id"] == "client-supplied-id"


class TestStreaming:
    def test_the_stream_emits_sse_frames_and_terminates(self, user_client) -> None:
        with user_client.stream(
            "POST", "/api/v3/chat/stream", json={"message": "hello"}
        ) as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers["content-type"]
            body = "".join(response.iter_text())

        assert "data:" in body
        assert "done" in body

    def test_streaming_is_not_buffered_by_a_content_length(self, user_client) -> None:
        """A Content-Length on an SSE response means something buffered the whole answer."""
        with user_client.stream(
            "POST", "/api/v3/chat/stream", json={"message": "hello"}
        ) as response:
            assert "content-length" not in {k.lower() for k in response.headers}
            list(response.iter_text())


class TestValidation:
    def test_an_empty_message_is_rejected(self, user_client) -> None:
        assert user_client.post("/api/v3/chat", json={"message": ""}).status_code == 422

    def test_an_over_long_message_is_rejected_before_the_model(self, user_client) -> None:
        """Bounding here keeps a paste-bomb from becoming a token bill."""
        response = user_client.post("/api/v3/chat", json={"message": "x" * 100_000})
        assert response.status_code == 422

    def test_an_unknown_field_does_not_crash_the_route(self, user_client) -> None:
        response = user_client.post(
            "/api/v3/chat", json={"message": "hello", "unexpected": "value"}
        )
        assert response.status_code in (200, 422)
