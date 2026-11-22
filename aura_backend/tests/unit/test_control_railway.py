"""The control center's Railway stage (ADR-037, stage 2).

Through the real routes, against ``InMemoryRailway`` (real state, no network): what a variable
change must pass before anything is written, what it leaves behind for the reconciler, how the
reconciler undoes a bad one (references restored as written), and that no secret value leaves
the server. ``RailwayPlatform`` itself is checked against recorded GraphQL exchanges
(``httpx.MockTransport``), and the settings check runs the real child process once.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest
from cryptography.fernet import Fernet

from aura_core.adapters.railway_platform import InMemoryRailway, RailwayPlatform
from aura_core.adapters.runtime import FrozenClock
from aura_core.adapters.settings_probe import SubprocessSettingsValidator
from aura_core.control.service import Reconciler
from aura_core.domain.control import ChangeStatus, Platform
from aura_core.domain.errors import ValidationError
from aura_core.ports.control_plane import DeploymentHealth, ReauthClaims

ROOT = "root@aura.ai"
CANARY = "tok-rw7q-never-shown"
GATEWAY_VARS = {
    "AURA_ENVIRONMENT": "production",
    "AURA_KNOWLEDGE_TOP_K": "8",
    "AURA_JWT_SECRET": CANARY + "-jwt",
    "AURA_DATABASE_URL": "${{Postgres.DATABASE_URL}}",
    "RAILWAY_SERVICE_NAME": "gateway",
}


@pytest.fixture(autouse=True)
def vault_key(monkeypatch):
    monkeypatch.setenv("AURA_SECRETS_ENCRYPTION_KEY", Fernet.generate_key().decode())


def _container():
    from aura_core.config import load_settings
    from aura_core.container import Container

    container = Container(load_settings(refresh=True, validate=False))
    railway: InMemoryRailway = container.railway_api
    railway.add_service("svc-gw", "gateway", GATEWAY_VARS)
    railway.add_service("svc-console", "console", {"NEXT_PUBLIC_BASE_PATH": "/console"},
                        healthcheck_path="")
    container.railway.self_service_id = "svc-gw"
    return container


def _client(container, *, role: str = "SuperAdmin"):
    from fastapi.testclient import TestClient

    from aura_core.api import Principal, build_app
    from aura_core.domain.identifiers import UserId

    async def authenticator(request):
        return Principal(user_id=UserId("root"), email=ROOT, role=role)

    return TestClient(build_app(container, authenticator=authenticator))


def _grant(client, container) -> str:
    nonce = client.post("/api/v3/admin/control/step-up/challenge").json()["nonce"]
    container.control_reverifier.register("reauth", ReauthClaims(
        subject="google-sub", email=ROOT, auth_time=datetime.now(timezone.utc), nonce=nonce,
    ))
    response = client.post("/api/v3/admin/control/step-up", json={"id_token": "reauth", "nonce": nonce})
    assert response.status_code == 200, response.text
    return response.json()["grant"]


def _set(client, grant, changes, *, service="svc-gw", confirm="gateway"):
    return client.post(
        f"/api/v3/admin/control/railway/services/{service}/variables",
        json={"changes": changes, "confirm": confirm}, headers={"x-aura-step-up": grant},
    )


def _reason(response) -> str:
    return response.json()["details"]["reason"]


class TestReading:
    def test_services_show_which_one_is_this_gateway(self) -> None:
        container = _container()
        with _client(container) as client:
            body = client.get("/api/v3/admin/control/railway").json()
        assert body["environment"]["name"] == "production"
        by_name = {s["name"]: s for s in body["services"]}
        assert by_name["gateway"]["is_gateway"] is True
        assert by_name["console"]["is_gateway"] is False
        assert by_name["console"]["healthcheck_path"] == ""

    def test_variables_never_carry_a_secret_value(self) -> None:
        container = _container()
        with _client(container) as client:
            response = client.get("/api/v3/admin/control/railway/services/svc-gw/variables")
        rows = {r["name"]: r for r in response.json()["variables"]}
        assert CANARY not in response.text
        assert rows["AURA_JWT_SECRET"]["secret"] is True and rows["AURA_JWT_SECRET"]["value"] is None
        assert rows["AURA_KNOWLEDGE_TOP_K"]["value"] == "8"
        assert rows["AURA_DATABASE_URL"]["reference"] is True and rows["AURA_DATABASE_URL"]["protected"] is True
        assert rows["RAILWAY_SERVICE_NAME"]["provided"] is True

    def test_logs_mask_every_secret_value_of_the_service(self) -> None:
        from aura_core.ports.control_plane import LogLine

        container = _container()
        railway = container.railway_api
        deployment = railway.history["svc-gw"][0].deployment_id
        railway.log_lines[f"deploy:{deployment}"] = [
            LogLine("t1", f"connecting with {CANARY}-jwt", "info"), LogLine("t2", "ready", "info"),
        ]
        with _client(container) as client:
            response = client.get(f"/api/v3/admin/control/railway/services/svc-gw/deployments/{deployment}/logs")
        assert response.status_code == 200
        assert CANARY not in response.text
        assert [l["message"] for l in response.json()["lines"]] == ["connecting with [redacted]", "ready"]

    def test_the_settings_view_reads_the_gateway_s_railway_variables_once_connected(self) -> None:
        container = _container()
        container.control_verifier.accept(Platform.RAILWAY, "railway-good")
        with _client(container) as client:
            before = client.get("/api/v3/admin/control/settings").json()
            grant = _grant(client, container)
            client.post("/api/v3/admin/control/platforms/railway/credential",
                        json={"token": "railway-good", "confirm": "railway"}, headers={"x-aura-step-up": grant})
            after = client.get("/api/v3/admin/control/settings")
        assert before["source"] == "process" and before["editable"] is False
        body = after.json()
        assert body["source"] == "railway" and body["editable"] is True and body["service_id"] == "svc-gw"
        assert body["service_name"] == "gateway"
        entries = {e["variable"]: e for g in body["groups"] for e in g["settings"]}
        assert entries["AURA_KNOWLEDGE_TOP_K"]["set"] is True
        assert CANARY not in after.text


class TestWhatAChangeMustPassFirst:
    @pytest.mark.parametrize(("changes", "reason"), [
        ({"AURA_SECRETS_ENCRYPTION_KEY": "x"}, "protected_variable"),
        ({"AURA_DATABASE_URL": None}, "protected_variable"),
        ({"RAILWAY_SERVICE_NAME": "x"}, "provided_variable"),
        ({"AURA_REDIS_URL": "${{Redis.REDIS_URL}}"}, "reference_not_supported"),
        ({"AURA_KNOWLEDGE_TOP_K": "8"}, "no_changes"),
        ({"NOT_THERE": None}, "no_changes"),
        ({"bad name": "x"}, "bad_name"),
    ])
    def test_refused_before_anything_is_written(self, changes, reason) -> None:
        container = _container()
        before = dict(container.railway_api.raw["svc-gw"])
        with _client(container) as client:
            response = _set(client, _grant(client, container), changes)
        assert response.status_code == 422, response.text
        assert _reason(response) == reason
        assert container.railway_api.raw["svc-gw"] == before
        assert len(container.railway_api.history["svc-gw"]) == 1

    def test_a_change_the_gateway_could_not_boot_with_is_refused(self) -> None:
        container = _container()
        container.settings_validator.refuse_when(
            lambda env: "AURA_KNOWLEDGE_TOP_K must be <= 50" if env.get("AURA_KNOWLEDGE_TOP_K") == "999" else None
        )
        with _client(container) as client:
            grant = _grant(client, container)
            dry = client.post("/api/v3/admin/control/railway/services/svc-gw/variables/validate",
                              json={"changes": {"AURA_KNOWLEDGE_TOP_K": "999"}})
            response = _set(client, grant, {"AURA_KNOWLEDGE_TOP_K": "999"})
        assert dry.status_code == 422 and _reason(dry) == "settings_invalid"
        assert response.status_code == 422 and "AURA_KNOWLEDGE_TOP_K must be <= 50" in response.json()["message"]
        assert container.railway_api.raw["svc-gw"]["AURA_KNOWLEDGE_TOP_K"] == "8"

    def test_the_check_sees_the_whole_candidate_environment(self) -> None:
        container = _container()
        with _client(container) as client:
            response = client.post("/api/v3/admin/control/railway/services/svc-gw/variables/validate",
                                   json={"changes": {"AURA_KNOWLEDGE_TOP_K": "12", "AURA_JWT_SECRET": None}})
        assert response.status_code == 200 and response.json()["settings_checked"] is True
        candidate = container.settings_validator.calls[-1]
        assert candidate["AURA_KNOWLEDGE_TOP_K"] == "12"
        assert "AURA_JWT_SECRET" not in candidate
        assert candidate["AURA_DATABASE_URL"] == "rendered-Postgres.DATABASE_URL"

    def test_an_aura_service_without_a_health_check_is_refused(self) -> None:
        container = _container()
        container.railway_api.healthcheck["svc-gw"] = ""
        with _client(container) as client:
            response = _set(client, _grant(client, container), {"AURA_KNOWLEDGE_TOP_K": "12"})
        assert response.status_code == 422 and _reason(response) == "healthcheck_not_configured"

    def test_a_service_that_does_not_run_aura_skips_the_settings_check(self) -> None:
        container = _container()
        with _client(container) as client:
            response = _set(client, _grant(client, container), {"NEXT_PUBLIC_BASE_PATH": ""},
                            service="svc-console", confirm="console")
        assert response.status_code == 200, response.text
        assert container.settings_validator.calls == []

    def test_the_service_name_must_be_typed(self) -> None:
        container = _container()
        with _client(container) as client:
            response = _set(client, _grant(client, container), {"AURA_KNOWLEDGE_TOP_K": "12"}, confirm="yes")
        assert response.status_code == 422 and _reason(response) == "confirmation_required"
        assert container.railway_api.raw["svc-gw"]["AURA_KNOWLEDGE_TOP_K"] == "8"

    def test_a_write_without_a_fresh_sign_in_is_refused(self) -> None:
        container = _container()
        with _client(container) as client:
            response = client.post("/api/v3/admin/control/railway/services/svc-gw/redeploy",
                                   json={"confirm": "gateway"})
        assert response.status_code == 403 and _reason(response) == "step_up_required"


class TestApplying:
    def test_a_change_is_written_redeployed_once_and_watched(self) -> None:
        container = _container()
        railway = container.railway_api
        previous = railway.history["svc-gw"][0].deployment_id
        with _client(container) as client:
            response = _set(client, _grant(client, container),
                            {"AURA_KNOWLEDGE_TOP_K": "12", "AURA_JWT_SECRET": CANARY + "-new"})
        assert response.status_code == 200, response.text
        change = response.json()
        assert CANARY not in response.text
        assert change["status"] == "watching" and change["previous_deployment_id"] == previous
        assert change["deployment_id"] == railway.history["svc-gw"][0].deployment_id != previous
        assert railway.raw["svc-gw"]["AURA_KNOWLEDGE_TOP_K"] == "12"
        assert {v["name"]: v["secret"] for v in change["detail"]["variables"]} == {
            "AURA_JWT_SECRET": True, "AURA_KNOWLEDGE_TOP_K": False,
        }
        audit = json.dumps([e.__dict__ if hasattr(e, "__dict__") else str(e) for e in container.audit._events], default=str)
        assert CANARY not in audit

    def test_a_second_change_waits_for_the_first(self) -> None:
        container = _container()
        with _client(container) as client:
            grant = _grant(client, container)
            first = _set(client, grant, {"AURA_KNOWLEDGE_TOP_K": "12"})
            second = client.post("/api/v3/admin/control/railway/services/svc-gw/redeploy",
                                 json={"confirm": "gateway"}, headers={"x-aura-step-up": grant})
        assert first.status_code == 200
        assert second.status_code == 422 and _reason(second) == "change_in_progress"

    def test_a_redeploy_that_cannot_start_puts_the_variables_back(self) -> None:
        container = _container()
        railway = container.railway_api

        async def refuse(service_id):
            raise RuntimeError("railway is down")

        railway.redeploy = refuse
        with _client(container, ) as client:
            grant = _grant(client, container)
            with pytest.raises(RuntimeError):
                _set(client, grant, {"AURA_KNOWLEDGE_TOP_K": "12"})
            changes = client.get("/api/v3/admin/control/changes").json()["changes"]
        assert railway.raw["svc-gw"]["AURA_KNOWLEDGE_TOP_K"] == "8"
        assert changes[0]["status"] == "failed" and "put back" in changes[0]["reason"]

    def test_restart_and_a_chosen_rollback_are_journaled(self) -> None:
        container = _container()
        railway = container.railway_api
        first = railway.history["svc-gw"][0].deployment_id
        with _client(container) as client:
            grant = _grant(client, container)
            restart = client.post("/api/v3/admin/control/railway/services/svc-gw/restart",
                                  json={"confirm": "gateway"}, headers={"x-aura-step-up": grant})
            back = client.post("/api/v3/admin/control/railway/services/svc-gw/rollback",
                               json={"deployment_id": first, "confirm": "gateway"}, headers={"x-aura-step-up": grant})
        assert restart.json()["status"] == "recorded" and railway.restarts == [first]
        assert back.json()["action"] == "rollback" and railway.rollbacks == [("svc-gw", first)]


class TestAutomaticRollback:
    async def _apply(self, container, changes):
        return await container.railway.apply_variables("svc-gw", changes, actor=ROOT, confirm="gateway")

    def _reconciler(self, container, clock):
        return Reconciler(
            store=container.control_store, vault=container.control_vault,
            platforms={Platform.RAILWAY: container.railway_api}, watch_s=300, now=clock.now,
        )

    async def test_a_crashed_deployment_is_rolled_back_with_references_restored_as_written(self) -> None:
        container = _container()
        railway = container.railway_api
        good = railway.history["svc-gw"][0].deployment_id
        change = await self._apply(container, {"AURA_KNOWLEDGE_TOP_K": "12", "AURA_DATABASE_URL_RO": "x"})
        railway.set_status(change["deployment_id"], "CRASHED")
        clock = FrozenClock(datetime.now(timezone.utc))
        closed = await self._reconciler(container, clock).tick()
        assert [c.status for c in closed] == [ChangeStatus.ROLLED_BACK]
        assert railway.rollbacks == [("svc-gw", good)]
        assert railway.raw["svc-gw"]["AURA_KNOWLEDGE_TOP_K"] == "8"
        assert "AURA_DATABASE_URL_RO" not in railway.raw["svc-gw"]
        assert railway.raw["svc-gw"]["AURA_DATABASE_URL"] == "${{Postgres.DATABASE_URL}}"
        assert closed[0].restore_ref is None or (await container.control_store.get_change(closed[0].change_id)).restore_ref is None

    async def test_a_healthy_deployment_closes_after_the_watch(self) -> None:
        container = _container()
        change = await self._apply(container, {"AURA_KNOWLEDGE_TOP_K": "12"})
        container.railway_api.set_status(change["deployment_id"], "SUCCESS")
        clock = FrozenClock(datetime.now(timezone.utc))
        reconciler = self._reconciler(container, clock)
        assert await reconciler.tick() == []  # healthy, but not for five minutes yet
        clock.advance(301)
        closed = await reconciler.tick()
        assert [c.status for c in closed] == [ChangeStatus.SUCCEEDED]
        assert container.railway_api.raw["svc-gw"]["AURA_KNOWLEDGE_TOP_K"] == "12"

    async def test_a_deployment_replaced_by_a_newer_one_is_not_rolled_back(self) -> None:
        container = _container()
        change = await self._apply(container, {"AURA_KNOWLEDGE_TOP_K": "12"})
        container.railway_api.set_status(change["deployment_id"], "REMOVED")
        closed = await self._reconciler(container, FrozenClock(datetime.now(timezone.utc))).tick()
        assert [c.status for c in closed] == [ChangeStatus.SUCCEEDED]
        assert "newer deployment" in closed[0].reason
        assert container.railway_api.rollbacks == []


class TestRailwayPlatformContract:
    """``RailwayPlatform`` against recorded exchanges: what it sends, and what it makes of answers."""

    def _platform(self, handler):
        seen: list[dict] = []

        def record(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            seen.append({"token": request.headers.get("Project-Access-Token"), **body})
            return handler(body)

        async def token():
            return "project-token"

        return RailwayPlatform(token_source=token, transport=httpx.MockTransport(record), poll_s=0), seen

    @staticmethod
    def _answer(body):
        query = body["query"]
        if "projectToken" in query:
            data = {"projectToken": {"projectId": "p1", "environmentId": "e1",
                                     "project": {"name": "aura"}, "environment": {"name": "production"}}}
        elif "variables(" in query:
            data = {"variables": {"A": "1", "B_TOKEN": "s3cret"}}
        elif "deployments(" in query:
            data = {"deployments": {"edges": [{"node": {"id": "d9", "status": "SUCCESS", "createdAt": "2026-11-21T10:00:00Z",
                                                         "canRollback": True, "canRedeploy": True,
                                                         "meta": {"commitHash": "abcdef1234567890", "commitMessage": "fix: x\nbody"}}}]}}
        elif "deploymentRedeploy" in query:
            data = {"deploymentRedeploy": {"id": "d10", "status": "INITIALIZING"}}
        elif "deployment(id" in query:
            data = {"deployment": {"id": body["variables"]["id"], "status": "CRASHED", "serviceId": "s1"}}
        else:
            data = {}
        return httpx.Response(200, json={"data": data})

    async def test_every_call_carries_the_project_token_and_the_token_s_scope(self) -> None:
        platform, seen = self._platform(self._answer)
        assert await platform.variables("s1") == {"A": "1", "B_TOKEN": "s3cret"}
        call = seen[-1]
        assert call["token"] == "project-token"
        assert call["variables"] == {"projectId": "p1", "environmentId": "e1", "serviceId": "s1", "unrendered": False}

    async def test_redeploy_reuses_the_image_of_the_latest_deployment(self) -> None:
        platform, seen = self._platform(self._answer)
        assert await platform.redeploy("s1") == "d10"
        assert "usePreviousImageTag: true" in seen[-1]["query"] and seen[-1]["variables"] == {"id": "d9"}

    async def test_variable_writes_skip_the_deploy_and_deletes_are_separate(self) -> None:
        platform, seen = self._platform(self._answer)
        await platform.set_variables("s1", {"A": "2", "OLD": None})
        upsert = next(c for c in seen if "variableCollectionUpsert" in c["query"])
        delete = next(c for c in seen if "variableDelete" in c["query"])
        assert upsert["variables"]["input"]["skipDeploys"] is True
        assert upsert["variables"]["input"]["variables"] == {"A": "2"}
        assert delete["variables"]["input"]["name"] == "OLD"

    async def test_statuses_map_to_health(self) -> None:
        platform, _ = self._platform(self._answer)
        assert await platform.deployment_health("s1", "d9") is DeploymentHealth.FAILED
        deployments = await platform.deployments("s1")
        assert deployments[0].commit == "abcdef123456" and deployments[0].message == "fix: x"

    async def test_a_refused_token_is_a_validation_error_without_the_vendor_text(self) -> None:
        platform, _ = self._platform(
            lambda body: httpx.Response(200, json={"errors": [{"message": "Project Token not found: secret-echo"}]})
        )
        with pytest.raises(ValidationError) as caught:
            await platform.scope()
        assert caught.value.details["reason"] == "railway_token_refused"
        assert "secret-echo" not in str(caught.value)


class TestTheRealSettingsCheck:
    async def test_the_child_process_refuses_an_out_of_range_value_and_masks_secrets(self) -> None:
        validator = SubprocessSettingsValidator()
        ok = await validator.validate({"AURA_ENVIRONMENT": "local", "AURA_KNOWLEDGE_TOP_K": "12"})
        bad = await validator.validate({
            "AURA_ENVIRONMENT": "local", "AURA_KNOWLEDGE_TOP_K": "999",
            "AURA_JWT_SECRET": CANARY,
        })
        assert ok is None
        assert bad is not None and "AURA_KNOWLEDGE_TOP_K" in bad and CANARY not in bad
