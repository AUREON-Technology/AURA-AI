"""Model management for a person: ``GET /api/v3/models``, ``GET /models/{id}`` and the standing
preferences at ``/me/model-settings`` (migration 0038).

A person chooses a routing policy, not a model: which model may see a piece of content is the data
classification's decision (ADR-030). So the tests assert two things beyond the shapes: the catalog
never carries what only an operator may see, and a stored preference is a *default* that a request
overrides, never one that overrides the request.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aura_core.adapters.model_settings import InMemoryModelSettingsStore
from aura_core.domain import ProviderCapabilities
from aura_core.domain.errors import ValidationError
from aura_core.domain.identifiers import UserId
from aura_core.domain.llm import DataClassification, Modality
from aura_core.domain.model_settings import ModelSettings
from aura_core.llm import ProviderRouter
from aura_core.llm.providers.echo import EchoProvider

pytest.importorskip("fastapi")

ALICE, BOB = UserId("alice"), UserId("bob")
NOW = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)


def _local() -> EchoProvider:
    return EchoProvider(name="local", capabilities=ProviderCapabilities(
        model_id="qwen2.5:3b", context_window=8192, max_output_tokens=512, supports_tools=True,
        supports_json_mode=True, quality_tier=1, speed_tier=1, is_local=True, languages=("en", "ar"),
        memory_cost=4.0, gpu_cost=1.0, cost_per_1k_input_usd=0.0, capacity_group="ollama-box",
    ))


def _hosted() -> EchoProvider:
    return EchoProvider(name="hosted1", capabilities=ProviderCapabilities(
        model_id="open-weights-70b", context_window=131072, max_output_tokens=8192, supports_tools=True,
        supports_vision=True, supports_reasoning=True, quality_tier=3, speed_tier=3, is_local=False,
        third_party=True, max_data_classification=DataClassification.INTERNAL, may_train_on_prompts=False,
        modalities=(Modality.TEXT, Modality.IMAGE), cost_per_1k_input_usd=0.0009, cost_per_1k_output_usd=0.0027,
        model_revision="deadbeefcafe",
    ))


def _with(container, *providers) -> ProviderRouter:
    container.router = ProviderRouter(list(providers), circuit_failure_threshold=1)
    return container.router


def _app(container):
    from aura_core.api import Principal, build_app
    from aura_core.domain.errors import AuthenticationError

    async def authenticate(request):
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer "):
            raise AuthenticationError("missing")
        return Principal(user_id=UserId(header[7:]), email="", role="User")

    return build_app(container, authenticator=authenticate)


def _as(user: str) -> dict:
    return {"Authorization": f"Bearer {user}"}


# ---------------------------------------------------------------------------------------------
# the catalog
# ---------------------------------------------------------------------------------------------


class TestCatalog:
    def test_it_lists_every_model_with_what_it_can_do_and_who_runs_it(self, container) -> None:
        from fastapi.testclient import TestClient

        _with(container, _local(), _hosted())
        with TestClient(_app(container)) as client:
            body = client.get("/api/v3/models", headers=_as("alice")).json()

        by_id = {m["id"]: m for m in body["models"]}
        assert set(by_id) == {"local", "hosted1"}
        local, hosted = by_id["local"], by_id["hosted1"]
        assert (local["model"], local["local"], local["context_window"], local["max_output_tokens"]) == ("qwen2.5:3b", True, 8192, 512)
        assert local["supports"] == {"streaming": True, "tools": True, "json_mode": True, "vision": False, "reasoning": False}
        assert local["languages"] == ["en", "ar"] and (local["quality_tier"], local["speed_tier"]) == (1, 1)
        assert hosted["supports"]["vision"] and hosted["supports"]["reasoning"] and hosted["modalities"] == ["text", "image"]
        assert hosted["privacy"] == {"third_party": True, "max_data_classification": "internal", "may_train_on_prompts": False}
        assert local["privacy"]["third_party"] is False and local["privacy"]["max_data_classification"] == "sensitive"
        assert body["routing_policies"] == ["local_first", "cheapest", "fastest", "quality_first", "balanced"]

    def test_it_never_carries_what_only_an_operator_may_see(self, container) -> None:
        """No endpoint, key, cost, capacity group, pool or weights revision: those are administrators'."""
        from fastapi.testclient import TestClient

        _with(container, _local(), _hosted())
        with TestClient(_app(container)) as client:
            listing = client.get("/api/v3/models", headers=_as("alice"))
            one = client.get("/api/v3/models/hosted1", headers=_as("alice"))

        blob = (listing.text + one.text).lower()
        for secret in ("deadbeefcafe", "ollama-box", "0.0009", "0.0027", "cost", "base_url", "api_key", "capacity", "pool", "revision", "memory_cost", "gpu"):
            assert secret not in blob, f"the catalog leaked {secret!r}"

    def test_a_provider_whose_circuit_is_open_is_marked_unavailable(self, container) -> None:
        from fastapi.testclient import TestClient

        router = _with(container, _local(), _hosted())
        router._stats["hosted1"].breaker.record_failure()  # threshold 1: opens the circuit
        with TestClient(_app(container)) as client:
            by_id = {m["id"]: m for m in client.get("/api/v3/models", headers=_as("alice")).json()["models"]}
        assert (by_id["hosted1"]["available"], by_id["hosted1"]["state"]) == (False, "open")
        assert (by_id["local"]["available"], by_id["local"]["state"]) == (True, "closed")

    def test_one_model_by_id_and_an_unknown_id_is_a_404(self, container) -> None:
        from fastapi.testclient import TestClient

        _with(container, _local(), _hosted())
        with TestClient(_app(container)) as client:
            found = client.get("/api/v3/models/local", headers=_as("alice"))
            missing = client.get("/api/v3/models/nope", headers=_as("alice"))
        assert found.status_code == 200 and found.json()["id"] == "local"
        assert missing.status_code == 404

    def test_capabilities_says_what_a_client_may_offer_for_choosing(self, container) -> None:
        from fastapi.testclient import TestClient

        router = _with(container, _local(), _hosted())
        with TestClient(_app(container)) as client:
            before = client.get("/api/v3/capabilities", headers=_as("alice")).json()
            router._stats["local"].breaker.record_failure()
            after = client.get("/api/v3/capabilities", headers=_as("alice")).json()
        assert before["models"]["routing_policies"] == ["local_first", "cheapest", "fastest", "quality_first", "balanced"]
        assert before["models"]["local_available"] is True and after["models"]["local_available"] is False
        assert before["models"]["settings_endpoint"] == "/api/v3/me/model-settings"
        assert "vision" in before and "knowledge" in before, "the existing keys must stay"

    def test_the_catalog_needs_a_signed_in_caller(self, container) -> None:
        from fastapi.testclient import TestClient

        with TestClient(_app(container)) as client:
            assert client.get("/api/v3/models").status_code == 401


# ---------------------------------------------------------------------------------------------
# the settings: domain and store
# ---------------------------------------------------------------------------------------------


class TestSettingsRules:
    @pytest.mark.parametrize("kwargs", [
        {"routing_policy": "fastest-ish"}, {"routing_policy": ""}, {"temperature": -0.1}, {"temperature": 2.1},
        {"max_output_tokens": 15}, {"max_output_tokens": 8193},
    ])
    def test_out_of_range_values_are_refused(self, kwargs) -> None:
        with pytest.raises(ValidationError):
            ModelSettings(**kwargs)

    @pytest.mark.parametrize("kwargs", [
        {"routing_policy": "local_first"}, {"temperature": 0.0}, {"temperature": 2.0},
        {"max_output_tokens": 16}, {"max_output_tokens": 8192}, {"require_local": False},
    ])
    def test_the_edges_are_allowed(self, kwargs) -> None:
        ModelSettings(**kwargs)

    async def test_the_store_is_scoped_to_the_owner_and_replaces_whole(self) -> None:
        store = InMemoryModelSettingsStore()
        await store.put(ALICE, ModelSettings(routing_policy="cheapest", temperature=0.9), at=NOW)
        assert await store.get(BOB) is None
        assert await store.delete(BOB) is False and (await store.get(ALICE)).routing_policy == "cheapest"

        await store.put(ALICE, ModelSettings(max_output_tokens=300), at=NOW)
        after = await store.get(ALICE)
        assert (after.routing_policy, after.temperature, after.max_output_tokens) == (None, None, 300), "a put kept old fields"
        assert await store.delete(ALICE) is True and await store.get(ALICE) is None


# ---------------------------------------------------------------------------------------------
# the settings routes
# ---------------------------------------------------------------------------------------------


class TestSettingsRoutes:
    def test_nothing_stored_shows_the_server_defaults(self, container) -> None:
        from fastapi.testclient import TestClient

        with TestClient(_app(container)) as client:
            body = client.get("/api/v3/me/model-settings", headers=_as("alice")).json()
        assert body["settings"]["routing_policy"] is None and body["settings"]["updated_at"] is None
        assert body["effective"] == {"routing_policy": None, "require_local": False, "temperature": 0.3, "max_output_tokens": 1024}

    def test_put_stores_replaces_and_delete_forgets(self, container) -> None:
        from fastapi.testclient import TestClient

        with TestClient(_app(container)) as client:
            first = client.put("/api/v3/me/model-settings", json={"routing_policy": "cheapest", "temperature": 0.9}, headers=_as("alice"))
            second = client.put("/api/v3/me/model-settings", json={"max_output_tokens": 300}, headers=_as("alice"))
            read = client.get("/api/v3/me/model-settings", headers=_as("alice")).json()
            gone = client.delete("/api/v3/me/model-settings", headers=_as("alice"))
            again = client.get("/api/v3/me/model-settings", headers=_as("alice")).json()
            nothing = client.delete("/api/v3/me/model-settings", headers=_as("alice"))

        assert first.status_code == 200 and first.json()["effective"]["routing_policy"] == "cheapest"
        assert second.json()["settings"]["routing_policy"] is None, "a PUT must replace, not merge"
        assert read["settings"]["max_output_tokens"] == 300 and read["effective"]["temperature"] == 0.3
        assert read["settings"]["updated_at"]
        assert gone.json()["deleted"] is True and again["settings"]["max_output_tokens"] is None
        assert nothing.json()["deleted"] is False

    @pytest.mark.parametrize("body", [
        {"routing_policy": "fastest-ish"}, {"temperature": 3}, {"temperature": -1}, {"max_output_tokens": 5},
        {"max_output_tokens": 99999}, {"require_local": "perhaps"},
    ])
    def test_bad_values_are_422_and_store_nothing(self, container, body) -> None:
        import asyncio

        from fastapi.testclient import TestClient

        with TestClient(_app(container)) as client:
            assert client.put("/api/v3/me/model-settings", json=body, headers=_as("alice")).status_code == 422
        assert asyncio.run(container.model_settings.get(ALICE)) is None

    def test_require_local_is_refused_while_no_local_model_exists(self, container) -> None:
        import asyncio

        from fastapi.testclient import TestClient

        _with(container, _hosted())
        with TestClient(_app(container)) as client:
            refused = client.put("/api/v3/me/model-settings", json={"require_local": True}, headers=_as("alice"))
            allowed = client.put("/api/v3/me/model-settings", json={"require_local": False}, headers=_as("alice"))
        assert refused.status_code == 422 and refused.json()["details"]["field"] == "require_local"
        assert allowed.status_code == 200 and asyncio.run(container.model_settings.get(ALICE)).require_local is False

    def test_one_persons_settings_are_not_anothers(self, container) -> None:
        from fastapi.testclient import TestClient

        with TestClient(_app(container)) as client:
            client.put("/api/v3/me/model-settings", json={"routing_policy": "fastest"}, headers=_as("alice"))
            bob = client.get("/api/v3/me/model-settings", headers=_as("bob")).json()
            client.delete("/api/v3/me/model-settings", headers=_as("bob"))
            alice = client.get("/api/v3/me/model-settings", headers=_as("alice")).json()
        assert bob["settings"]["routing_policy"] is None
        assert alice["settings"]["routing_policy"] == "fastest", "Bob's delete removed Alice's settings"


# ---------------------------------------------------------------------------------------------
# applying them to a chat
# ---------------------------------------------------------------------------------------------


class TestApplied:
    @staticmethod
    def _capture(container):
        seen: list = []

        async def capture(request):
            seen.append(request)
            raise RuntimeError("stop")

        container.kernel.run = capture  # type: ignore[method-assign]
        return seen

    def _chat(self, client, user: str, **body):
        return client.post("/api/v3/chat", json={"message": "hello", **body}, headers=_as(user))

    def test_a_request_that_names_nothing_gets_the_server_defaults(self, container) -> None:
        from fastapi.testclient import TestClient

        seen = self._capture(container)
        with TestClient(_app(container), raise_server_exceptions=False) as client:
            self._chat(client, "alice")
        turn = seen[0]
        assert (turn.routing_policy, turn.require_local, turn.temperature, turn.max_output_tokens) == (None, False, 0.3, 1024)

    def test_stored_preferences_apply_when_the_request_names_nothing(self, container) -> None:
        from fastapi.testclient import TestClient

        _with(container, _local())
        seen = self._capture(container)
        with TestClient(_app(container), raise_server_exceptions=False) as client:
            stored = client.put("/api/v3/me/model-settings", json={
                "routing_policy": "quality_first", "require_local": True, "temperature": 1.2, "max_output_tokens": 2000,
            }, headers=_as("alice"))
            assert stored.status_code == 200, stored.text
            self._chat(client, "alice")
        turn = seen[0]
        assert turn.routing_policy.value == "quality_first"
        assert (turn.require_local, turn.temperature, turn.max_output_tokens) == (True, 1.2, 2000)

    def test_what_the_request_names_wins_even_when_it_is_false_or_low(self, container) -> None:
        """``False`` and ``0.0`` are values, not "unset": a request may switch a preference off."""
        from fastapi.testclient import TestClient

        _with(container, _local())
        seen = self._capture(container)
        with TestClient(_app(container), raise_server_exceptions=False) as client:
            stored = client.put("/api/v3/me/model-settings", json={
                "routing_policy": "quality_first", "require_local": True, "temperature": 1.2, "max_output_tokens": 2000,
            }, headers=_as("alice"))
            assert stored.status_code == 200, stored.text
            self._chat(client, "alice", routing_policy="cheapest", require_local=False, temperature=0.0, max_output_tokens=64)
        turn = seen[0]
        assert turn.routing_policy.value == "cheapest"
        assert (turn.require_local, turn.temperature, turn.max_output_tokens) == (False, 0.0, 64)

    def test_each_field_falls_back_on_its_own(self, container) -> None:
        from fastapi.testclient import TestClient

        seen = self._capture(container)
        with TestClient(_app(container), raise_server_exceptions=False) as client:
            client.put("/api/v3/me/model-settings", json={"temperature": 1.5, "max_output_tokens": 500}, headers=_as("alice"))
            self._chat(client, "alice", temperature=0.5)
        turn = seen[0]
        assert (turn.temperature, turn.max_output_tokens, turn.require_local) == (0.5, 500, False)

    def test_one_persons_preferences_never_reach_anothers_chat(self, container) -> None:
        from fastapi.testclient import TestClient

        seen = self._capture(container)
        with TestClient(_app(container), raise_server_exceptions=False) as client:
            client.put("/api/v3/me/model-settings", json={"routing_policy": "fastest", "temperature": 1.9}, headers=_as("alice"))
            self._chat(client, "bob")
        assert (seen[0].routing_policy, seen[0].temperature) == (None, 0.3)

    def test_deleting_the_preferences_restores_the_defaults(self, container) -> None:
        from fastapi.testclient import TestClient

        seen = self._capture(container)
        with TestClient(_app(container), raise_server_exceptions=False) as client:
            client.put("/api/v3/me/model-settings", json={"temperature": 1.9}, headers=_as("alice"))
            client.delete("/api/v3/me/model-settings", headers=_as("alice"))
            self._chat(client, "alice")
        assert seen[0].temperature == 0.3

    def test_a_store_that_is_down_never_stops_a_chat(self, container) -> None:
        from fastapi.testclient import TestClient

        class Down:
            async def get(self, owner_id):
                raise ConnectionError("db down")

        container.model_settings = Down()
        seen = self._capture(container)
        with TestClient(_app(container), raise_server_exceptions=False) as client:
            self._chat(client, "alice")
        assert seen and seen[0].temperature == 0.3, "the chat did not go on with the defaults"

    def test_the_stream_route_applies_them_too(self, container) -> None:
        from fastapi.testclient import TestClient

        seen: list = []

        async def capture(request):
            seen.append(request)
            if False:
                yield {}
            raise RuntimeError("stop")

        container.kernel.stream = capture  # type: ignore[method-assign]
        with TestClient(_app(container), raise_server_exceptions=False) as client:
            client.put("/api/v3/me/model-settings", json={"temperature": 1.7, "max_output_tokens": 777}, headers=_as("alice"))
            client.post("/api/v3/chat/stream", json={"message": "hi"}, headers=_as("alice"))
        assert seen and (seen[0].temperature, seen[0].max_output_tokens) == (1.7, 777)


class TestAccountErasure:
    async def test_erasing_an_account_deletes_its_settings_and_only_its_own(self, container) -> None:
        from api.admin_platform import _erase_core

        await container.model_settings.put(ALICE, ModelSettings(temperature=1.0), at=NOW)
        await container.model_settings.put(BOB, ModelSettings(temperature=0.5), at=NOW)

        counts, failed = await _erase_core(container, ALICE.value, set())

        assert counts["model_settings"] is True and "model_settings" not in failed
        assert await container.model_settings.get(ALICE) is None
        assert (await container.model_settings.get(BOB)).temperature == 0.5, "erasure reached another account"
