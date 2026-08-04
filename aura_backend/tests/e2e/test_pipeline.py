"""End-to-end tests through the full kernel and the HTTP gateway.

Everything is real except the storage adapters and the model. The pipeline, the guards, the
retrieval, the context assembly, the routing, and the telemetry are the same code that runs in
production -- which is what makes these assertions meaningful rather than a mock choreography.
"""

from __future__ import annotations

import pytest

from aura_core.domain import DocumentId, SessionId, UserId
from aura_core.kernel import TurnRequest


@pytest.fixture
async def ready(container, user, handbook):
    """A container with the handbook ingested and indexes warm."""
    await container.startup()
    await container.knowledge.ingest(
        content=handbook, title="Employee Handbook",
        document_id=DocumentId("handbook"), owner_id=user,
    )
    return container


class TestGroundedAnswers:
    async def test_a_grounded_turn_cites_its_source(self, ready, user) -> None:
        result = await ready.kernel.run(
            TurnRequest(message="How many days of annual leave do I get?", user_id=user)
        )
        assert result.citations
        assert "handbook@v1" in result.citations[0]
        assert result.telemetry.retrieval.returned > 0

    async def test_an_out_of_corpus_question_is_marked_degraded(self, ready, user) -> None:
        """Retrieval that searched and found nothing is a real quality signal, not silence."""
        result = await ready.kernel.run(
            TurnRequest(message="What is the melting point of tungsten?", user_id=user)
        )
        assert result.telemetry.retrieval.returned == 0
        assert result.telemetry.retrieval.returned_nothing
        assert result.degraded

    async def test_retrieval_can_be_disabled_per_turn(self, ready, user) -> None:
        result = await ready.kernel.run(
            TurnRequest(message="Say hello.", user_id=user, use_knowledge=False)
        )
        assert result.telemetry.retrieval.candidates == 0
        assert not result.citations


class TestTelemetry:
    async def test_every_turn_produces_a_complete_record(self, ready, user) -> None:
        """Telemetry is a return value, not a side effect, so it cannot be forgotten (ADR-009)."""
        result = await ready.kernel.run(
            TurnRequest(message="How many days of annual leave do I get?", user_id=user)
        )
        telemetry = result.telemetry

        assert telemetry.correlation_id.value
        assert telemetry.turn_id.value
        assert telemetry.provider and telemetry.model
        assert telemetry.total_ms > 0
        assert telemetry.prompt_tokens > 0
        assert telemetry.context_tokens > 0
        assert telemetry.timings.overhead > 0

    async def test_telemetry_contains_no_content(self, ready, user) -> None:
        """Prompts, documents, and answers never enter metrics, logs, or the telemetry table."""
        secret = "the mitochondria is the powerhouse of the cell"
        result = await ready.kernel.run(TurnRequest(message=secret, user_id=user))
        assert secret not in str(result.telemetry.as_dict())

    async def test_correlation_id_is_preserved_when_supplied(self, ready, user) -> None:
        from aura_core.domain import CorrelationId

        supplied = CorrelationId("trace-abc-123")
        result = await ready.kernel.run(
            TurnRequest(message="hello", user_id=user, correlation_id=supplied)
        )
        assert result.telemetry.correlation_id == supplied

    async def test_stage_timings_are_recorded_separately(self, ready, user) -> None:
        result = await ready.kernel.run(
            TurnRequest(message="How many days of annual leave do I get?", user_id=user)
        )
        timings = result.telemetry.timings.as_dict()
        assert timings["retrieval"] > 0
        assert timings["context"] > 0
        assert timings["generation"] > 0


class TestSessionMemory:
    async def test_conversation_persists_across_turns(self, ready, user) -> None:
        """The legacy system has no server-side conversation state at all."""
        session = SessionId("s-1")
        await ready.kernel.run(
            TurnRequest(message="My favourite colour is teal.", user_id=user, session_id=session)
        )
        history, _summary = await ready.episodic.history(session, user)
        assert any("teal" in m.content for m in history)

    async def test_a_session_id_is_not_a_capability(self, ready, user) -> None:
        """Guessing another user's session id must not surface their conversation."""
        session = SessionId("s-shared")
        await ready.kernel.run(
            TurnRequest(message="Private matter.", user_id=user, session_id=session)
        )
        history, _ = await ready.episodic.history(session, UserId("attacker"))
        assert not list(history)


class TestStreaming:
    async def test_frames_are_typed(self, ready, user) -> None:
        frames = [f async for f in ready.kernel.stream(
            TurnRequest(message="How many days of annual leave do I get?", user_id=user)
        )]
        kinds = [f["type"] for f in frames]
        assert "token" in kinds
        assert kinds[-1] == "done"

    async def test_citations_arrive_before_tokens(self, ready, user) -> None:
        """The client can render sources while the answer is still streaming."""
        kinds = [f["type"] async for f in ready.kernel.stream(
            TurnRequest(message="How many days of annual leave do I get?", user_id=user)
        )]
        assert kinds.index("citations") < kinds.index("token")

    async def test_done_frame_declares_whether_output_guards_completed(self, ready, user) -> None:
        """Honest about a real limitation: guards needing the whole answer cannot run before
        the first token, so the contract says so rather than implying they did."""
        frames = [f async for f in ready.kernel.stream(
            TurnRequest(message="Summarise the expense policy.", user_id=user)
        )]
        done = frames[-1]
        assert "guards_complete" in done
        assert done["guards_complete"] is True


class TestErrorHandling:
    async def test_provider_failure_surfaces_as_an_error_not_a_fabrication(
        self, container, user
    ) -> None:
        from aura_core.domain.errors import ProviderError
        from aura_core.llm import ProviderRouter
        from aura_core.llm.providers.echo import EchoProvider, ScriptedTurn

        broken = EchoProvider(
            name="broken", model="b",
            script=[ScriptedTurn(raise_error=ProviderError("down", provider="broken"))] * 10,
        )
        container.kernel._deps.router = ProviderRouter([broken])
        container.kernel._deps.reasoner = None
        container.kernel._deps.cache = None

        result = await container.kernel.run(TurnRequest(message="hello", user_id=user))
        assert result.text == ""
        assert result.telemetry.outcome.value == "error"
        assert result.telemetry.error_kind == "all_providers_failed"

    async def test_a_failed_turn_still_produces_telemetry(self, container, user) -> None:
        """Errors that leave no trace are how a failure rate becomes invisible."""
        from aura_core.domain.errors import ProviderError
        from aura_core.llm import ProviderRouter
        from aura_core.llm.providers.echo import EchoProvider, ScriptedTurn

        broken = EchoProvider(
            name="broken", model="b",
            script=[ScriptedTurn(raise_error=ProviderError("down", provider="broken"))] * 10,
        )
        container.kernel._deps.router = ProviderRouter([broken])
        container.kernel._deps.reasoner = None
        container.kernel._deps.cache = None

        result = await container.kernel.run(TurnRequest(message="hello", user_id=user))
        assert result.telemetry.total_ms > 0
        assert result.telemetry.correlation_id.value


class TestHttpGateway:
    @pytest.fixture
    def client(self, container, user):
        from fastapi.testclient import TestClient

        from aura_core.api import Principal, build_app

        async def authenticator(request):
            return Principal(user_id=user, email="u@example.com", role="Admin")

        with TestClient(build_app(container, authenticator=authenticator)) as test_client:
            yield test_client

    def test_liveness_checks_only_the_process(self, client) -> None:
        """A liveness probe that fails on a dependency outage causes the orchestrator to restart
        a healthy process, turning degradation into an outage."""
        assert client.get("/health/live").json() == {"status": "alive"}

    def test_readiness_reports_dependencies(self, client) -> None:
        body = client.get("/health/ready").json()
        assert "providers" in body and "index" in body

    def test_ingest_search_and_chat(self, client) -> None:
        ingest = client.post(
            "/api/v3/knowledge/documents",
            json={
                "title": "Handbook",
                "content": "# Leave\nAnnual leave is 21 days per year. Ticket AURA-4419 is open.",
                "document_id": "handbook",
            },
        )
        assert ingest.status_code == 200 and ingest.json()["created"]

        search = client.post("/api/v3/knowledge/search", json={"query": "AURA-4419"})
        assert search.status_code == 200 and search.json()["results"]

        chat = client.post("/api/v3/chat", json={"message": "How much annual leave do I get?"})
        assert chat.status_code == 200
        assert chat.json()["citations"]
        assert chat.headers["X-Correlation-Id"]

    def test_empty_search_is_reported_honestly(self, client) -> None:
        client.post(
            "/api/v3/knowledge/documents",
            json={"title": "H", "content": "# Leave\nAnnual leave is 21 days per year.",
                  "document_id": "h"},
        )
        body = client.post("/api/v3/knowledge/search", json={"query": "quantum chromodynamics"}).json()
        assert body["results"] == []
        assert body["searched_but_empty"] is True

    def test_streaming_emits_sse_frames(self, client) -> None:
        with client.stream("POST", "/api/v3/chat/stream", json={"message": "Hello there."}) as response:
            events = [line for line in response.iter_lines() if line.startswith("event:")]
        assert "event: done" in events

    def test_metrics_endpoint_exposes_the_catalogue(self, container, user) -> None:
        from fastapi.testclient import TestClient

        from aura_core.api import Principal, build_app
        from aura_core.observability.metrics import Metrics

        container.metrics = Metrics(enabled=True, namespace="auratest")
        container.kernel._metrics = container.metrics

        async def authenticator(request):
            return Principal(user_id=user, role="Admin")

        with TestClient(build_app(container, authenticator=authenticator)) as client:
            client.post("/api/v3/chat", json={"message": "hello"})
            body = client.get("/metrics").text

        assert "auratest_turns_total" in body
        assert "auratest_llm_tokens_total" in body

    def test_validation_errors_are_rejected(self, client) -> None:
        assert client.post("/api/v3/chat", json={"message": ""}).status_code == 422
        assert client.post("/api/v3/chat", json={}).status_code == 422

    def test_memory_round_trip(self, client) -> None:
        created = client.post(
            "/api/v3/memory", json={"content": "The user prefers metric units.", "subject": "units"}
        )
        assert created.status_code == 200
        memory_id = created.json()["id"]

        listed = client.get("/api/v3/memory").json()["memories"]
        assert any(m["id"] == memory_id for m in listed)

        assert client.delete(f"/api/v3/memory/{memory_id}").status_code == 200
        assert not client.get("/api/v3/memory").json()["memories"]

    def test_admin_routes_require_an_admin_role(self, container, user) -> None:
        from fastapi.testclient import TestClient

        from aura_core.api import Principal, build_app

        async def authenticator(request):
            return Principal(user_id=user, role="User")

        with TestClient(build_app(container, authenticator=authenticator)) as client:
            assert client.get("/api/v3/admin/status").status_code == 403

    def test_admin_status_never_leaks_secrets(self, client) -> None:
        body = client.get("/api/v3/admin/status").json()
        rendered = str(body)
        assert "api_key" not in rendered or "api_key_configured" in rendered
        for provider in body["settings"]["llm"]["providers"]:
            assert "api_key" not in provider or provider.get("api_key_env") is not None
