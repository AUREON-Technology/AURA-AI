"""The Cloudflare tab's routes, through the real app and the container's real wiring (stage 6, B5).

Who may call them (the role sweep in ``test_control_center.py`` covers every route), what a write
needs (a step-up grant and the provider's name typed), how a body is validated, that nothing secret
leaves the server, that the audit trail records the consent, and that a removal ends with the
container's own reconciler revoking the token. In-memory Railway, tokens and catalog: no network.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from aura_core.config.external_inference import HOSTED_OPEN_WEIGHT_HOSTS, WORKERS_AI_API_ROOT
from aura_core.config.settings import ProviderConfig
from aura_core.control.service import Reconciler
from aura_core.control.wiring import cloudflare_api_root
from aura_core.domain.cloudflare_provider import key_variable, provider_prefix, provider_variables
from aura_core.domain.cloudflare_provider import CloudflareProviderSpec
from aura_core.domain.control import ChangeStatus, Platform, PlatformCredential
from aura_core.domain.errors import ConfigurationError
from aura_core.ports.control_plane import CloudflareModel, ReauthClaims

ROOT = "root@aura.ai"
ACCOUNT = "0123456789abcdef0123456789abcdef"
MODEL = "@cf/openai/gpt-oss-20b"
OLD_CF = "old-cf-key-zq19-never-shown"
GROQ = "groq-key-zq19-never-shown"
PASTED = "pasted-cf-key-zq19-never-shown"
GROQ_HOST = next(host for host in HOSTED_OPEN_WEIGHT_HOSTS if "groq" in host)
BASE = "/api/v3/admin/control/cloudflare"


@pytest.fixture(autouse=True)
def vault_key(monkeypatch):
    monkeypatch.setenv("AURA_SECRETS_ENCRYPTION_KEY", Fernet.generate_key().decode())


def _spec(name: str, **overrides) -> CloudflareProviderSpec:
    return CloudflareProviderSpec(name=name, model=overrides.pop("model", MODEL), context_window=32768, **overrides)


def _gateway_env() -> dict[str, str]:
    env = {
        "AURA_ENVIRONMENT": "production",
        "AURA_ALLOW_HOSTED_INFERENCE": "true",
        "AURA_LLM_PROVIDERS": "hosted1,cf-old,local",
        "AURA_PROVIDER_HOSTED1_BASE_URL": f"https://{GROQ_HOST}/openai/v1",
        "AURA_PROVIDER_HOSTED1_MODEL": "openai/gpt-oss-120b",
        "AURA_PROVIDER_HOSTED1_API_KEY_ENV": "AURA_PROVIDER_HOSTED1_API_KEY",
        "AURA_PROVIDER_HOSTED1_API_KEY": GROQ,
        "AURA_PROVIDER_LOCAL_KIND": "ollama",
        "AURA_PROVIDER_LOCAL_BASE_URL": "http://ollama:11434",
        "AURA_PROVIDER_LOCAL_MODEL": "qwen2.5:3b",
    }
    env.update(provider_variables(_spec("cf-old", rpm=300), api_root=WORKERS_AI_API_ROOT, account_id=ACCOUNT))
    env[key_variable("cf-old")] = OLD_CF
    return env


def _real_loader_rule(environ) -> str | None:
    names = [n.strip() for n in environ.get("AURA_LLM_PROVIDERS", "").split(",") if n.strip()]
    with patch.dict(os.environ, dict(environ), clear=True):
        for name in names:
            if not environ.get(f"{provider_prefix(name)}_BASE_URL", "").startswith(WORKERS_AI_API_ROOT):
                continue
            try:
                ProviderConfig.from_env(name)
            except ConfigurationError as exc:
                return str(exc)
    return None


async def _connect(container, platform: Platform, target: str = "") -> None:
    await container.control_store.put_credential(PlatformCredential(
        platform=platform, secret_ref=f"ref-{platform.value}", configured_by=ROOT,
        configured_at=datetime.now(timezone.utc), target=target,
    ))


async def _container(*, connected: bool = True):
    from aura_core.config import load_settings
    from aura_core.container import Container

    container = Container(load_settings(refresh=True, validate=False))
    container.railway_api.add_service("svc-gw", "gateway", _gateway_env())
    container.railway.self_service_id = "svc-gw"
    container.old_cf_id = container.cloudflare_tokens.add("aura-cf-old", OLD_CF)
    container.settings_validator.refuse_when(_real_loader_rule)
    container.cloudflare_account.add_model(CloudflareModel(model=MODEL, task="Text Generation", context_window=128000))
    container.cloudflare_account.add_model(CloudflareModel(model="@cf/cloudflare/clairic", task="Text Generation"))
    container.cloudflare_account.add_model(CloudflareModel(model="@cf/baai/bge-m3", task="Text Embeddings"))
    if connected:
        await _connect(container, Platform.RAILWAY)
        await _connect(container, Platform.CLOUDFLARE, ACCOUNT)
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
    return client.post("/api/v3/admin/control/step-up", json={"id_token": "reauth", "nonce": nonce}).json()["grant"]


def _post(client, path, body, grant=None):
    headers = {"x-aura-step-up": grant} if grant else {}
    return client.post(f"{BASE}{path}", json=body, headers=headers)


def _env(container) -> dict[str, str]:
    return container.railway_api.raw["svc-gw"]


def _reason(response) -> str:
    return response.json()["details"]["reason"]


def _audit(container, action: str) -> list[str]:
    # AuditEvent uses __slots__: it has no __dict__, so read it through as_dict().
    dumped = [json.dumps(e.as_dict(), default=str) for e in container.audit._events]
    return [d for d in dumped if f'"action": "{action}"' in d]


def _never_leaked(container, *responses) -> None:
    journal = json.dumps(
        [dict(c.detail) | {"reason": c.reason} for c in container.control_store._changes.values()], default=str,
    )
    audit = journal + json.dumps([e.as_dict() for e in container.audit._events], default=str)
    for secret in (OLD_CF, GROQ, PASTED):
        assert secret not in audit
        for response in responses:
            assert secret not in response.text


ADD = {"name": "cf-new", "model": MODEL, "context_window": 32768, "confirm": "cf-new"}


class TestReading:
    async def test_the_overview_lists_the_gateways_cloudflare_providers(self) -> None:
        container = await _container()
        with _client(container) as client:
            response = client.get(BASE)
        body = response.json()
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        assert body["connected"] and body["account_id"] == ACCOUNT and body["gateway_ready"]
        assert [p["name"] for p in body["providers"]] == ["cf-old"]
        _never_leaked(container, response)

    async def test_models_pass_their_filters_to_the_catalog_for_the_connected_account(self) -> None:
        container = await _container()
        with _client(container) as client:
            response = client.get(f"{BASE}/models", params={"task": "", "search": "gpt", "page": 1, "per_page": 50})
        body = response.json()
        assert response.status_code == 200 and [m["model"] for m in body["models"]] == [MODEL]
        assert body["models"][0]["can_add"] is True
        assert container.cloudflare_account.calls[-1] == {
            "account_id": ACCOUNT, "task": "", "search": "gpt", "page": 1, "per_page": 50,
        }

    async def test_models_say_what_aura_will_not_use(self) -> None:
        container = await _container()
        with _client(container) as client:
            rows = {m["model"]: m for m in client.get(f"{BASE}/models", params={"task": ""}).json()["models"]}
        assert rows["@cf/cloudflare/clairic"]["refusal"] == "not_open_weight"
        assert rows["@cf/baai/bge-m3"]["refusal"] == "not_chat"

    @pytest.mark.parametrize("query", ["per_page=1000", "per_page=0", "page=0", "page=-1", "search=" + "x" * 101])
    async def test_a_query_outside_its_bounds_is_refused(self, query: str) -> None:
        container = await _container()
        with _client(container) as client:
            assert client.get(f"{BASE}/models?{query}").status_code == 422

    async def test_models_need_cloudflare_connected(self) -> None:
        container = await _container(connected=False)
        with _client(container) as client:
            response = client.get(f"{BASE}/models")
        assert response.status_code == 422 and _reason(response) == "cloudflare_not_connected"


class TestAWriteNeedsAGrantAndTheTypedName:
    async def test_no_grant_is_403_and_nothing_changes(self) -> None:
        container = await _container()
        before = dict(_env(container))
        with _client(container) as client:
            responses = [
                _post(client, "/providers", ADD),
                _post(client, "/providers/cf-old/replace", {"model": MODEL, "context_window": 32768, "confirm": "cf-old"}),
                _post(client, "/providers/cf-old/remove", {"confirm": "cf-old"}),
            ]
        assert [r.status_code for r in responses] == [403, 403, 403]
        assert _env(container) == before and container.cloudflare_tokens.created == []

    async def test_with_a_grant_the_wrong_name_is_refused(self) -> None:
        container = await _container()
        before = dict(_env(container))
        with _client(container) as client:
            grant = _grant(client, container)
            responses = [
                _post(client, "/providers", {**ADD, "confirm": "gateway"}, grant),
                _post(client, "/providers/cf-old/remove", {"confirm": "gateway"}, grant),
            ]
        assert [r.status_code for r in responses] == [422, 422]
        assert all(_reason(r) == "confirmation_required" for r in responses)
        assert _env(container) == before


class TestAdd:
    async def test_add_writes_one_watched_change_and_audits_it(self) -> None:
        container = await _container()
        with _client(container) as client:
            response = _post(client, "/providers", ADD, _grant(client, container))
        body = response.json()
        assert response.status_code == 200, response.text
        assert body["action"] == "add_cloudflare_provider" and body["status"] == "watching"
        assert _env(container)["AURA_LLM_PROVIDERS"] == "hosted1,cf-old,cf-new,local"
        assert _env(container)[key_variable("cf-new")]  # the key was made, and never returned
        (event,) = _audit(container, "admin.control.cloudflare.add")
        assert '"cf-new"' in event and "consent_enabled" in event and "success" in event
        _never_leaked(container, response)
        assert _env(container)[key_variable("cf-new")] not in response.text

    async def test_a_whole_number_speed_and_every_quota_arrive_intact(self) -> None:
        container = await _container()
        body = {**ADD, "tokens_per_second": 150, "rpm": 300, "rpd": 5000, "tpm": 40000, "tpd": 900000,
                "json_mode": True, "vision": True, "quality_tier": 3, "max_output_tokens": 4096, "enabled": False}
        with _client(container) as client:
            response = _post(client, "/providers", body, _grant(client, container))
        assert response.status_code == 200, response.text
        env = _env(container)
        assert env["AURA_PROVIDER_CF_NEW_TOKENS_PER_SECOND"] == "150.0"
        assert (env["AURA_PROVIDER_CF_NEW_RPM"], env["AURA_PROVIDER_CF_NEW_TPD"]) == ("300", "900000")
        assert env["AURA_PROVIDER_CF_NEW_SUPPORTS_JSON_MODE"] == "true"
        assert env["AURA_PROVIDER_CF_NEW_MODALITIES"] == "text,image" and env["AURA_PROVIDER_CF_NEW_ENABLED"] == "false"

    async def test_a_pasted_key_is_used_and_never_echoed(self) -> None:
        container = await _container()
        with _client(container) as client:
            response = _post(client, "/providers", {**ADD, "key": "paste", "key_value": PASTED}, _grant(client, container))
        assert response.status_code == 200 and _env(container)[key_variable("cf-new")] == PASTED
        _never_leaked(container, response)

    async def test_consent_given_in_the_dialog_is_set_and_audited(self) -> None:
        container = await _container()
        _env(container).pop("AURA_ALLOW_HOSTED_INFERENCE")
        with _client(container) as client:
            response = _post(client, "/providers", {**ADD, "enable_consent": True}, _grant(client, container))
        assert response.status_code == 200 and _env(container)["AURA_ALLOW_HOSTED_INFERENCE"] == "true"
        assert response.json()["detail"]["consent_enabled"] is True
        (event,) = _audit(container, "admin.control.cloudflare.add")
        assert '"consent_enabled": true' in event

    async def test_without_consent_it_is_refused_with_its_reason_and_audited_as_refused(self) -> None:
        container = await _container()
        _env(container).pop("AURA_ALLOW_HOSTED_INFERENCE")
        with _client(container) as client:
            response = _post(client, "/providers", ADD, _grant(client, container))
        assert response.status_code == 422 and _reason(response) == "hosted_inference_not_consented"
        (event,) = _audit(container, "admin.control.cloudflare.add")
        assert "refused" in event and "hosted_inference_not_consented" in event

    async def test_a_model_aura_will_not_host_is_refused(self) -> None:
        container = await _container()
        with _client(container) as client:
            response = _post(client, "/providers", {**ADD, "model": "@cf/cloudflare/clairic"}, _grant(client, container))
        assert response.status_code == 422 and _reason(response) == "not_open_weight"
        assert container.cloudflare_tokens.created == []


class TestBodiesAreValidatedStrictly:
    @pytest.mark.parametrize(
        "patch_body",
        [
            {"extra_field": 1},
            {"context_window": "32768"},
            {"quality_tier": True},
            {"enabled": "yes"},
            {"rpm": 1.5},
            {"key": 7},
            {"name": "x" * 65},
        ],
    )
    async def test_a_malformed_body_is_422_and_changes_nothing(self, patch_body) -> None:
        container = await _container()
        before = dict(_env(container))
        with _client(container) as client:
            response = _post(client, "/providers", {**ADD, **patch_body}, _grant(client, container))
        assert response.status_code == 422
        assert _env(container) == before

    @pytest.mark.parametrize(
        "patch_body, reason",
        [
            ({"name": "Bad Name", "confirm": "Bad Name"}, None),
            ({"quality_tier": 9}, None),
            ({"context_window": 100}, None),
            ({"key": "keep"}, "bad_key_mode"),
            ({"key": "paste"}, "value_required"),
        ],
    )
    async def test_a_value_the_domain_refuses_is_422(self, patch_body, reason) -> None:
        container = await _container()
        with _client(container) as client:
            response = _post(client, "/providers", {**ADD, **patch_body}, _grant(client, container))
        assert response.status_code == 422
        if reason:
            assert _reason(response) == reason


class TestReplaceAndRemove:
    async def test_replace_changes_the_model_and_keeps_the_key(self) -> None:
        container = await _container()
        body = {"model": "@cf/qwen/qwen3-30b-a3b-fp8", "context_window": 32768, "confirm": "cf-old"}
        with _client(container) as client:
            response = _post(client, "/providers/cf-old/replace", body, _grant(client, container))
        assert response.status_code == 200, response.text
        assert response.json()["action"] == "replace_cloudflare_provider"
        assert _env(container)["AURA_PROVIDER_CF_OLD_MODEL"] == "@cf/qwen/qwen3-30b-a3b-fp8"
        assert _env(container)[key_variable("cf-old")] == OLD_CF
        assert "AURA_PROVIDER_CF_OLD_RPM" not in _env(container)
        _never_leaked(container, response)

    async def test_an_unknown_provider_is_404_and_a_foreign_one_is_422(self) -> None:
        container = await _container()
        body = {"model": MODEL, "context_window": 32768}
        with _client(container) as client:
            grant = _grant(client, container)
            missing = _post(client, "/providers/cf-missing/replace", {**body, "confirm": "cf-missing"}, grant)
            foreign = _post(client, "/providers/hosted1/remove", {"confirm": "hosted1"}, grant)
        assert missing.status_code == 404
        assert foreign.status_code == 422 and _reason(foreign) == "not_a_cloudflare_provider"

    async def test_remove_names_the_token_and_the_containers_reconciler_revokes_it_after_the_watch(self) -> None:
        container = await _container()
        with _client(container) as client:
            response = _post(client, "/providers/cf-old/remove", {"confirm": "cf-old"}, _grant(client, container))
        change = response.json()
        assert response.status_code == 200 and change["action"] == "remove_cloudflare_provider"
        assert change["detail"]["revoke_token_id"] == container.old_cf_id
        assert _env(container)["AURA_LLM_PROVIDERS"] == "hosted1,local"
        assert container.cloudflare_tokens.revoked == []  # not before the change has proved itself
        _never_leaked(container, response)

        # The container's own follow-up handlers, driven past the five-minute watch.
        assert "cloudflare_revoke" in container.control_reconciler._hooks
        container.railway_api.set_status(change["deployment_id"], "SUCCESS")
        later = datetime.now(timezone.utc) + timedelta(seconds=400)
        reconciler = Reconciler(
            store=container.control_store, vault=container.control_vault,
            platforms={Platform.RAILWAY: container.railway_api}, watch_s=300, now=lambda: later,
            success_hooks=container.control_reconciler._hooks,
        )
        (closed,) = await reconciler.tick()
        assert closed.status is ChangeStatus.SUCCEEDED
        assert container.cloudflare_tokens.revoked == [container.old_cf_id]


class TestValidateIsADryRun:
    async def test_it_needs_no_step_up_and_writes_nothing(self) -> None:
        container = await _container()
        before = dict(_env(container))
        with _client(container) as client:
            add = _post(client, "/providers/validate", {
                "operation": "add", "name": "cf-new", "model": MODEL, "context_window": 32768, "key": "paste",
            })
            replace = _post(client, "/providers/validate", {
                "operation": "replace", "name": "cf-old", "model": "@cf/qwen/qwen3-30b-a3b-fp8", "context_window": 32768,
            })
            remove = _post(client, "/providers/validate", {"operation": "remove", "name": "cf-old"})
        assert [r.status_code for r in (add, replace, remove)] == [200, 200, 200]
        assert add.json()["ok"] is True and add.json()["enables_consent"] is False
        assert remove.json()["revoke_token_id"] == container.old_cf_id
        assert {v["name"] for v in add.json()["variables"]} >= {"AURA_LLM_PROVIDERS", "AURA_PROVIDER_CF_NEW_MODEL"}
        assert _env(container) == before and container.cloudflare_tokens.created == []

    async def test_it_reports_what_the_real_write_would_be_refused_for(self) -> None:
        container = await _container()
        _env(container).pop("AURA_ALLOW_HOSTED_INFERENCE")
        with _client(container) as client:
            response = _post(client, "/providers/validate", {
                "operation": "add", "name": "cf-new", "model": MODEL, "context_window": 32768,
            })
        assert response.status_code == 422 and _reason(response) == "hosted_inference_not_consented"

    async def test_an_unknown_operation_is_422(self) -> None:
        container = await _container()
        with _client(container) as client:
            response = _post(client, "/providers/validate", {"operation": "destroy", "name": "cf-old"})
        assert response.status_code == 422


class TestConnectingCloudflareWithAnAccount:
    async def test_the_account_id_is_stored_with_the_token_and_the_tab_uses_it(self) -> None:
        container = await _container(connected=False)
        await _connect(container, Platform.RAILWAY)
        container.control_verifier.accept(Platform.CLOUDFLARE, "cfut-good-token")
        with _client(container) as client:
            grant = _grant(client, container)
            saved = client.post(
                "/api/v3/admin/control/platforms/cloudflare/credential",
                json={"token": "cfut-good-token", "target": ACCOUNT, "confirm": "cloudflare"},
                headers={"x-aura-step-up": grant},
            )
            overview = client.get(BASE)
        assert saved.status_code == 200 and saved.json()["verified"] is True
        assert overview.json()["connected"] is True and overview.json()["account_id"] == ACCOUNT
        assert "cfut-good-token" not in overview.text and "cfut-good-token" not in saved.text


class TestWiring:
    def test_with_no_workers_ai_provider_the_root_is_the_registry_constant(self) -> None:
        assert cloudflare_api_root([]) == WORKERS_AI_API_ROOT

    def test_a_provider_on_the_same_host_gives_the_same_root(self) -> None:
        provider = SimpleNamespace(base_url=f"{WORKERS_AI_API_ROOT}/accounts/{ACCOUNT}/ai/v1")
        assert cloudflare_api_root([provider]) == WORKERS_AI_API_ROOT

    def test_a_provider_that_is_not_cloudflare_is_ignored(self) -> None:
        assert cloudflare_api_root([SimpleNamespace(base_url="http://ollama:11434")]) == WORKERS_AI_API_ROOT
