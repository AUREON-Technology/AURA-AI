"""The control center's GPU Space stage (ADR-037, stage 3).

Through the real routes, against ``InMemorySpace`` (real state, no network): the Space is the
one its token was connected for, variable changes are refused or applied and then watched with
the Space's own window, a failing Space has its variables put back, secrets are write-only and
not restorable, and runtime actions are journaled. ``HfSpaceHost`` is checked against a stand-in
``HfApi`` for what it calls and how it maps Hub errors, without the network.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from cryptography.fernet import Fernet

from aura_core.adapters.hf_space import HfSpaceHost, InMemorySpace, space_health_of
from aura_core.adapters.runtime import FrozenClock
from aura_core.control.service import Reconciler
from aura_core.domain.control import ChangeStatus, Platform, PlatformCredential
from aura_core.domain.errors import ValidationError
from aura_core.ports.control_plane import DeploymentHealth, ReauthClaims

ROOT = "root@aura.ai"
SPACE = "aura-org/aura-inference"
CANARY = "hf-secret-zq7-never-shown"


@pytest.fixture(autouse=True)
def vault_key(monkeypatch):
    monkeypatch.setenv("AURA_SECRETS_ENCRYPTION_KEY", Fernet.generate_key().decode())


def _container():
    from aura_core.config import load_settings
    from aura_core.container import Container

    container = Container(load_settings(refresh=True, validate=False))
    space: InMemorySpace = container.space_api
    space.add_space(SPACE, variables={"MODEL_ID": "Qwen/Qwen2.5-7B-Instruct-AWQ", "MAX_MODEL_LEN": "8192"},
                    secrets={"HF_TOKEN": CANARY})
    container.control_verifier.accept(Platform.HF_SPACE, "hf-good")
    return container


def _client(container):
    from fastapi.testclient import TestClient

    from aura_core.api import Principal, build_app
    from aura_core.domain.identifiers import UserId

    async def authenticator(request):
        return Principal(user_id=UserId("root"), email=ROOT, role="SuperAdmin")

    return TestClient(build_app(container, authenticator=authenticator))


def _grant(client, container) -> str:
    nonce = client.post("/api/v3/admin/control/step-up/challenge").json()["nonce"]
    container.control_reverifier.register("reauth", ReauthClaims(
        subject="google-sub", email=ROOT, auth_time=datetime.now(timezone.utc), nonce=nonce,
    ))
    return client.post("/api/v3/admin/control/step-up", json={"id_token": "reauth", "nonce": nonce}).json()["grant"]


def _connected(client, container) -> str:
    grant = _grant(client, container)
    response = client.post("/api/v3/admin/control/platforms/hf_space/credential",
                           json={"token": "hf-good", "target": SPACE, "confirm": "hf_space"},
                           headers={"x-aura-step-up": grant})
    assert response.status_code == 200, response.text
    return grant


def _post(client, grant, path, body):
    return client.post(f"/api/v3/admin/control/space{path}", json=body, headers={"x-aura-step-up": grant})


def _reason(response) -> str:
    return response.json()["details"]["reason"]


class TestReading:
    def test_not_connected_is_a_404_that_says_where_to_connect(self) -> None:
        container = _container()
        with _client(container) as client:
            response = client.get("/api/v3/admin/control/space")
        assert response.status_code == 404
        assert "connect it in Settings" in response.json()["message"]

    def test_the_space_is_the_one_its_token_was_connected_for(self) -> None:
        container = _container()
        with _client(container) as client:
            _connected(client, container)
            body = client.get("/api/v3/admin/control/space").json()
            secrets = client.get("/api/v3/admin/control/space/secrets")
        assert body["space_id"] == SPACE and body["name"] == "aura-inference"
        assert body["stage"] == "RUNNING" and body["hardware"] == "l4x1" and body["watch_minutes"] == 20
        assert "a100-large" in body["hardware_options"]
        assert [s["name"] for s in secrets.json()["secrets"]] == ["HF_TOKEN"]
        assert CANARY not in secrets.text

    def test_logs_never_carry_the_token(self) -> None:
        container = _container()
        container.space_api.log_lines[f"run:{SPACE}"] = ["vLLM ready", "loaded"]
        with _client(container) as client:
            _connected(client, container)
            response = client.get("/api/v3/admin/control/space/logs?kind=run")
            bad = client.get("/api/v3/admin/control/space/logs?kind=other")
        assert response.json()["lines"] == ["vLLM ready", "loaded"]
        assert bad.status_code == 422


class TestVariables:
    @pytest.mark.parametrize(("changes", "reason"), [
        ({"MODEL_ID": "Qwen/Qwen2.5-7B-Instruct-AWQ"}, "no_changes"),
        ({"NOT_THERE": None}, "no_changes"),
        ({"bad name": "x"}, "bad_name"),
        ({"OPENAI_API_KEY": "x"}, "secret_as_variable"),
        ({"DATABASE": "postgres://u:pw@h/db"}, "secret_as_variable"),
    ])
    def test_refused_before_anything_is_written(self, changes, reason) -> None:
        container = _container()
        with _client(container) as client:
            grant = _connected(client, container)
            response = _post(client, grant, "/variables", {"changes": changes, "confirm": "aura-inference"})
        assert response.status_code == 422, response.text
        assert _reason(response) == reason
        assert container.space_api.events == []

    def test_the_space_name_must_be_typed(self) -> None:
        container = _container()
        with _client(container) as client:
            grant = _connected(client, container)
            response = _post(client, grant, "/variables", {"changes": {"MAX_MODEL_LEN": "4096"}, "confirm": SPACE + "x"})
        assert response.status_code == 422 and _reason(response) == "confirmation_required"

    def test_a_change_is_applied_and_watched_with_the_space_s_own_window(self) -> None:
        container = _container()
        with _client(container) as client:
            grant = _connected(client, container)
            dry = client.post("/api/v3/admin/control/space/variables/validate",
                              json={"changes": {"MAX_MODEL_LEN": "4096"}})
            response = _post(client, grant, "/variables", {"changes": {"MAX_MODEL_LEN": "4096"}, "confirm": "aura-inference"})
            again = _post(client, grant, "/restart", {"confirm": "aura-inference"})
        assert dry.status_code == 200
        change = response.json()
        assert change["status"] == "watching" and change["detail"]["watch_s"] == 1200
        assert container.space_api.vars[SPACE]["MAX_MODEL_LEN"] == "4096"
        assert again.status_code == 422 and _reason(again) == "change_in_progress"

    async def test_a_space_that_fails_gets_its_variables_back(self) -> None:
        container = _container()
        await container.control_store.put_credential(PlatformCredential(
            platform=Platform.HF_SPACE, secret_ref="ref", configured_by=ROOT,
            configured_at=datetime.now(timezone.utc), target=SPACE,
        ))
        space = container.space_api
        await container.space.apply_variables({"MAX_MODEL_LEN": "999999", "NEW_FLAG": "1"},
                                              actor=ROOT, confirm="aura-inference")
        space.set_stage(SPACE, "RUNTIME_ERROR")
        reconciler = Reconciler(store=container.control_store, vault=container.control_vault,
                                platforms={Platform.HF_SPACE: space}, watch_s=300,
                                now=FrozenClock(datetime.now(timezone.utc)).now)
        closed = await reconciler.tick()
        assert [c.status for c in closed] == [ChangeStatus.ROLLED_BACK]
        assert space.vars[SPACE]["MAX_MODEL_LEN"] == "8192" and "NEW_FLAG" not in space.vars[SPACE]

    async def test_a_slow_rebuild_is_not_rolled_back_inside_the_space_window(self) -> None:
        container = _container()
        await container.control_store.put_credential(PlatformCredential(
            platform=Platform.HF_SPACE, secret_ref="ref", configured_by=ROOT,
            configured_at=datetime.now(timezone.utc), target=SPACE,
        ))
        await container.space.apply_variables({"MAX_MODEL_LEN": "4096"}, actor=ROOT, confirm="aura-inference")
        clock = FrozenClock(datetime.now(timezone.utc))
        reconciler = Reconciler(store=container.control_store, vault=container.control_vault,
                                platforms={Platform.HF_SPACE: container.space_api}, watch_s=300, now=clock.now)
        clock.advance(600)  # past Railway's 5 minutes, inside the Space's 20
        assert await reconciler.tick() == []
        container.space_api.set_stage(SPACE, "RUNNING")
        clock.advance(700)
        assert [c.status for c in await reconciler.tick()] == [ChangeStatus.SUCCEEDED]


class TestSecretsAndRuntime:
    def test_secrets_are_write_only_and_journaled_as_not_restorable(self) -> None:
        container = _container()
        with _client(container) as client:
            grant = _connected(client, container)
            response = _post(client, grant, "/secrets", {"changes": {"HF_TOKEN": CANARY + "-new"}, "confirm": "aura-inference"})
            changes = client.get("/api/v3/admin/control/changes")
        assert response.status_code == 200
        assert response.json()["status"] == "recorded" and response.json()["detail"]["restorable"] is False
        assert container.space_api.secret_values[SPACE]["HF_TOKEN"] == CANARY + "-new"
        assert CANARY not in response.text and CANARY not in changes.text
        audit = json.dumps([getattr(e, "__dict__", str(e)) for e in container.audit._events], default=str)
        assert CANARY not in audit

    def test_an_empty_secret_is_refused(self) -> None:
        container = _container()
        with _client(container) as client:
            grant = _connected(client, container)
            response = _post(client, grant, "/secrets", {"changes": {"HF_TOKEN": ""}, "confirm": "aura-inference"})
        assert response.status_code == 422 and _reason(response) == "value_required"

    def test_pause_restart_rebuild_hardware_and_sleep_are_journaled(self) -> None:
        container = _container()
        space = container.space_api
        with _client(container) as client:
            grant = _connected(client, container)
            ok = lambda path, body: _post(client, grant, path, {**body, "confirm": "aura-inference"})  # noqa: E731
            assert ok("/pause", {}).json()["action"] == "space_pause"
            assert space.stage[SPACE] == "PAUSED"
            assert ok("/restart", {}).json()["action"] == "space_restart"
            assert ok("/restart", {"factory": True}).json()["action"] == "space_rebuild"
            hardware = ok("/hardware", {"hardware": "a10g-large"})
            same = ok("/hardware", {"hardware": "a10g-large"})
            unknown = ok("/hardware", {"hardware": "quantum-1"})
            sleep = ok("/sleep-time", {"seconds": 900})
            odd = ok("/sleep-time", {"seconds": 42})
        assert hardware.json()["detail"] == {"service": "aura-inference", "from": "l4x1", "to": "a10g-large"}
        assert _reason(same) == "no_changes" and _reason(unknown) == "bad_hardware"
        assert sleep.json()["detail"]["to"] == 900 and _reason(odd) == "bad_sleep_time"
        assert [e for e, _ in space.events] == ["pause", "restart", "factory", "hardware:a10g-large", "sleep:900"]


class TestStagesMapToHealth:
    @pytest.mark.parametrize(("stage", "health"), [
        ("RUNNING", DeploymentHealth.HEALTHY),
        ("RUNTIME_ERROR", DeploymentHealth.FAILED),
        ("BUILD_ERROR", DeploymentHealth.FAILED),
        ("APP_STARTING", DeploymentHealth.PENDING),
        ("RUNNING_BUILDING", DeploymentHealth.PENDING),
        ("PAUSED", DeploymentHealth.SUPERSEDED),
    ])
    def test_stage(self, stage, health) -> None:
        assert space_health_of(stage) is health


class TestHfSpaceHostContract:
    """``HfSpaceHost`` against a stand-in ``HfApi``: the calls it makes and how errors map."""

    def _host(self, monkeypatch, behaviour):
        import huggingface_hub

        calls: list[tuple] = []

        class FakeApi:
            def __getattr__(self, name):
                def method(*args, **kwargs):
                    calls.append((name, args, kwargs))
                    return behaviour(name, args, kwargs)
                return method

        monkeypatch.setattr(huggingface_hub, "HfApi", FakeApi)

        async def token():
            return "hf-stored-token"

        return HfSpaceHost(token_source=token), calls

    async def test_every_call_passes_the_stored_token_per_call(self, monkeypatch) -> None:
        from types import SimpleNamespace

        def behaviour(name, args, kwargs):
            if name == "get_space_runtime":
                return SimpleNamespace(stage="RUNNING", hardware="l4x1", requested_hardware="l4x1", sleep_time=3600)
            return None

        host, calls = self._host(monkeypatch, behaviour)
        status = await host.status(SPACE)
        await host.set_variables(SPACE, {"A": "1", "B": None})
        await host.restart(SPACE, factory=True)
        assert status.stage == "RUNNING" and status.hardware == "l4x1"
        assert [c[0] for c in calls] == ["get_space_runtime", "add_space_variable", "delete_space_variable", "restart_space"]
        assert all(c[2]["token"] == "hf-stored-token" for c in calls)
        assert calls[-1][2]["factory_reboot"] is True

    async def test_a_refused_token_is_a_validation_error_without_the_hub_text(self, monkeypatch) -> None:
        from types import SimpleNamespace

        class HubError(Exception):
            response = SimpleNamespace(status_code=401)

        def behaviour(name, args, kwargs):
            raise HubError("401 Unauthorized: token hf-stored-token is invalid")

        host, _ = self._host(monkeypatch, behaviour)
        with pytest.raises(ValidationError) as caught:
            await host.status(SPACE)
        assert caught.value.details["reason"] == "hf_token_refused"
        assert "hf-stored-token" not in str(caught.value)

    async def test_logs_mask_the_token_and_keep_the_last_lines(self, monkeypatch) -> None:
        def behaviour(name, args, kwargs):
            assert name == "fetch_space_logs" and kwargs["follow"] is False
            return iter([f"line {i}\n" for i in range(10)] + ["auth with hf-stored-token\n"])

        host, _ = self._host(monkeypatch, behaviour)
        lines = await host.logs(SPACE, kind="run", limit=3)
        assert lines == ["line 8", "line 9", "auth with [redacted]"]
