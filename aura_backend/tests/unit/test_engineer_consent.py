"""The Engineer's Anthropic consent (module readiness WP1; D-03, SEC-02, DATA-01).

Owner decision D-02 serves Engineer turns from Claude, so a person's code, file contents and
command output go to Anthropic. D-03: nothing goes until they agree, the agreement is held by the
server (a client flag would let any client skip it), and they can withdraw. The kernel refuses an
Engineer turn without a current consent *before planning*, so a refused turn sends nothing to any
provider -- asserted below by the echo provider's call count.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from aura_core.adapters.engineer_consent import InMemoryEngineerConsentStore
from aura_core.api import Principal, build_app
from aura_core.domain.engineering import ENGINEER_CONSENT_NOTICE_VERSION, EngineerConsent
from aura_core.domain.identifiers import UserId
from aura_core.llm.providers.echo import ScriptedTurn

ENGINEER = UserId("eng-1")
CAPS = ("engineer_agent",)
T0 = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 11, 17, 13, 0, tzinfo=timezone.utc)


def _plan_nothing() -> ScriptedTurn:
    return ScriptedTurn(text=json.dumps({"steps": []}))


class TestTheRecord:
    def test_current_only_for_this_version_and_not_withdrawn(self) -> None:
        consent = EngineerConsent(user_id=ENGINEER, notice_version=ENGINEER_CONSENT_NOTICE_VERSION,
                                  accepted_at=T0)
        assert consent.is_current()
        assert not consent.is_current("2099-01-01")
        assert not EngineerConsent(user_id=ENGINEER, notice_version="2025-01-01",
                                   accepted_at=T0).is_current()
        assert not EngineerConsent(user_id=ENGINEER, notice_version=ENGINEER_CONSENT_NOTICE_VERSION,
                                   accepted_at=T0, withdrawn_at=T1).is_current()


class TestInMemoryStore:
    async def test_accept_withdraw_and_accept_again(self) -> None:
        store = InMemoryEngineerConsentStore()
        assert await store.get(ENGINEER) is None
        await store.accept(ENGINEER, ENGINEER_CONSENT_NOTICE_VERSION, at=T0)
        assert (await store.get(ENGINEER)).is_current()

        withdrawn = await store.withdraw(ENGINEER, at=T1)
        assert withdrawn.withdrawn_at == T1 and not withdrawn.is_current()
        # Withdrawing again keeps the first withdrawal time.
        assert (await store.withdraw(ENGINEER, at=datetime(2027, 1, 1, tzinfo=timezone.utc))).withdrawn_at == T1

        again = await store.accept(ENGINEER, ENGINEER_CONSENT_NOTICE_VERSION, at=T1)
        assert again.is_current() and again.withdrawn_at is None

    async def test_withdraw_without_an_answer_is_harmless(self) -> None:
        assert await InMemoryEngineerConsentStore().withdraw(ENGINEER, at=T0) is None

    async def test_people_are_independent(self) -> None:
        store = InMemoryEngineerConsentStore()
        await store.accept(ENGINEER, ENGINEER_CONSENT_NOTICE_VERSION, at=T0)
        assert await store.get(UserId("someone-else")) is None

    async def test_erasure_deletes_the_row(self) -> None:
        store = InMemoryEngineerConsentStore()
        await store.accept(ENGINEER, ENGINEER_CONSENT_NOTICE_VERSION, at=T0)
        assert await store.delete_for_owner(ENGINEER) == 1
        assert await store.get(ENGINEER) is None
        assert await store.delete_for_owner(ENGINEER) == 0


class TestTheKernelRefuses:
    async def _engineer_turn(self, container, **extra):
        from aura_core.kernel import TurnRequest

        return await container.kernel.run(TurnRequest(
            message="fix the failing test", user_id=ENGINEER, agent="engineer",
            capabilities=CAPS, plan=True, **extra,
        ))

    async def test_an_engineer_turn_without_consent_sends_nothing(self, container) -> None:
        await container.startup()
        echo = container.router.get("echo")
        echo.queue(_plan_nothing(), ScriptedTurn(text="done"))

        result = await self._engineer_turn(container)

        assert result.telemetry.error_kind == "consent_required"
        assert result.error_details == {"current_version": ENGINEER_CONSENT_NOTICE_VERSION}
        assert echo.call_count == 0, "a refused Engineer turn must not reach any provider"

    async def test_with_consent_it_runs(self, container) -> None:
        await container.startup()
        await container.engineer_consent.accept(ENGINEER, ENGINEER_CONSENT_NOTICE_VERSION, at=T0)
        echo = container.router.get("echo")
        echo.queue(_plan_nothing(), ScriptedTurn(text="done"))

        result = await self._engineer_turn(container)

        assert result.telemetry.error_kind is None
        assert echo.call_count >= 1

    async def test_withdrawn_consent_is_refused_again(self, container) -> None:
        await container.startup()
        await container.engineer_consent.accept(ENGINEER, ENGINEER_CONSENT_NOTICE_VERSION, at=T0)
        await container.engineer_consent.withdraw(ENGINEER, at=T1)

        result = await self._engineer_turn(container)

        assert result.telemetry.error_kind == "consent_required"

    async def test_consent_to_an_older_notice_does_not_count(self, container) -> None:
        await container.startup()
        await container.engineer_consent.accept(ENGINEER, "2025-01-01", at=T0)

        result = await self._engineer_turn(container)

        assert result.telemetry.error_kind == "consent_required"

    async def test_other_personas_never_ask(self, container) -> None:
        from aura_core.kernel import TurnRequest

        await container.startup()
        echo = container.router.get("echo")
        echo.queue(ScriptedTurn(text="hello"))
        result = await container.kernel.run(TurnRequest(
            message="hello", user_id=ENGINEER, capabilities=CAPS, plan=False, use_tools=False,
        ))
        assert result.telemetry.error_kind is None

    async def test_engineer_without_the_capability_is_the_orchestrator_and_never_asks(
        self, container
    ) -> None:
        """No capability resolves to the orchestrator (AgentRegistry.resolve): not an Engineer turn,
        nothing goes to the Engineer's provider, so there is nothing to consent to."""
        from aura_core.kernel import TurnRequest

        await container.startup()
        container.router.get("echo").queue(ScriptedTurn(text="hello"))
        result = await container.kernel.run(TurnRequest(
            message="hello", user_id=ENGINEER, agent="engineer", plan=False, use_tools=False,
        ))
        assert result.telemetry.error_kind is None

    async def test_the_stream_refuses_with_a_non_retryable_frame(self, container) -> None:
        from aura_core.kernel import TurnRequest

        await container.startup()
        echo = container.router.get("echo")
        frames = [
            frame async for frame in container.kernel.stream(TurnRequest(
                message="fix it", user_id=ENGINEER, agent="engineer", capabilities=CAPS, plan=True,
            ))
        ]
        [error] = [f for f in frames if f.get("type") == "error"]
        assert error["error"] == "consent_required"
        assert error["retryable"] is False
        assert error["details"] == {"current_version": ENGINEER_CONSENT_NOTICE_VERSION}
        assert echo.call_count == 0

    async def test_a_delegated_engineer_sub_turn_is_refused_too(self, container) -> None:
        """Delegation runs the kernel again with the Engineer as the agent, so the same check
        applies -- the orchestrator cannot be used to route around it."""
        from aura_core.kernel import TurnRequest

        await container.startup()
        # The child request `delegate_to_agent` builds (tools/delegation.py): agent=engineer,
        # no session, one hop deep.
        result = await container.kernel.run(TurnRequest(
            message="fix it", user_id=ENGINEER, agent="engineer", capabilities=CAPS,
            plan=True, delegation_depth=1,
        ))
        assert result.telemetry.error_kind == "consent_required"


def _service_key():
    from aura_core.domain.service_keys import ServiceKey, token_digest

    return ServiceKey(key_id="0123456789abcdef", name="svc-test", digest=token_digest("t"),
                      created_at=T0, created_by="ops")


def _client(container, user: str = "eng-1", *, service: bool = False) -> TestClient:
    async def authenticator(_request):
        return Principal(
            user_id=UserId(user), role="Developer", capabilities=CAPS,
            service_key=_service_key() if service else None,
        )

    return TestClient(build_app(container, authenticator=authenticator))


class TestTheRoutes:
    def test_get_accept_withdraw(self, container) -> None:
        with _client(container) as client:
            assert client.get("/api/v3/engineering/consent").json() == {
                "accepted": False, "notice_version": None,
                "current_version": ENGINEER_CONSENT_NOTICE_VERSION, "accepted_at": None,
            }
            accepted = client.post("/api/v3/engineering/consent",
                                   json={"notice_version": ENGINEER_CONSENT_NOTICE_VERSION})
            assert accepted.status_code == 200
            assert accepted.json()["accepted"] is True
            assert accepted.json()["notice_version"] == ENGINEER_CONSENT_NOTICE_VERSION

            withdrawn = client.delete("/api/v3/engineering/consent")
            assert withdrawn.status_code == 200 and withdrawn.json()["accepted"] is False
            assert client.delete("/api/v3/engineering/consent").status_code == 200  # idempotent

    def test_an_older_notice_is_refused_with_409(self, container) -> None:
        with _client(container) as client:
            response = client.post("/api/v3/engineering/consent", json={"notice_version": "2025-01-01"})
            assert response.status_code == 409
            assert response.json()["error"] == "consent_version_mismatch"
            assert client.get("/api/v3/engineering/consent").json()["accepted"] is False

    def test_one_person_s_consent_is_not_another_s(self, container) -> None:
        with _client(container, "alice") as alice, _client(container, "bob") as bob:
            alice.post("/api/v3/engineering/consent",
                       json={"notice_version": ENGINEER_CONSENT_NOTICE_VERSION})
            assert bob.get("/api/v3/engineering/consent").json()["accepted"] is False
            bob.delete("/api/v3/engineering/consent")
            assert alice.get("/api/v3/engineering/consent").json()["accepted"] is True

    def test_a_service_key_cannot_consent(self, container) -> None:
        with _client(container, service=True) as client:
            response = client.post("/api/v3/engineering/consent",
                                   json={"notice_version": ENGINEER_CONSENT_NOTICE_VERSION})
            assert response.status_code in (403, 404)

    def test_agents_say_when_the_notice_is_needed(self, container) -> None:
        with _client(container) as client:
            rows = {a["id"]: a for a in client.get("/api/v3/agents").json()["agents"]}
            assert rows["engineer"]["consent_required"] is True
            assert rows["orchestrator"]["consent_required"] is False
            client.post("/api/v3/engineering/consent",
                        json={"notice_version": ENGINEER_CONSENT_NOTICE_VERSION})
            rows = {a["id"]: a for a in client.get("/api/v3/agents").json()["agents"]}
            assert rows["engineer"]["consent_required"] is False

    def test_non_stream_chat_refuses_with_403(self, container) -> None:
        with _client(container) as client:
            response = client.post("/api/v3/chat", json={"message": "fix it", "agent": "engineer"})
            assert response.status_code == 403
            body = response.json()
            assert body["error"] == "consent_required" and body["retryable"] is False
            assert body["details"] == {"current_version": ENGINEER_CONSENT_NOTICE_VERSION}
