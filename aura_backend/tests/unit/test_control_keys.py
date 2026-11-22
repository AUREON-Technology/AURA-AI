"""The control center's provider keys (ADR-037, stage 4).

Through the real routes against in-memory Railway, Space and Cloudflare (real state, no network):
a key change is a watched gateway variable change; rotating the GPU Space's key sets both sides
and, when the gateway change is undone, gives the Space its old key back; a new Cloudflare key is
created for the slot's account and the old one can be revoked only once nothing uses it; no key
value ever leaves the server. ``HttpCloudflareTokens`` is checked against recorded exchanges.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from cryptography.fernet import Fernet

from aura_core.adapters.cloudflare_tokens import HttpCloudflareTokens, InMemoryCloudflareTokens
from aura_core.adapters.runtime import FrozenClock
from aura_core.control.keys import SPACE_KEY_SECRET, KeysService
from aura_core.control.service import Reconciler
from aura_core.control.wiring import key_slots
from aura_core.domain.control import ChangeStatus, KeySlot, Platform, PlatformCredential
from aura_core.domain.errors import ValidationError
from aura_core.ports.control_plane import ReauthClaims

ROOT = "root@aura.ai"
SPACE = "aura-org/aura-inference"
OLD_HF = "old-hf-key-zq19-never-shown"
OLD_CF = "old-cf-key-zq19-never-shown"
NEW_GROQ = "new-groq-key-zq19-never-shown"
SLOTS = [
    KeySlot(name="hfspace", kind="openai_compatible", api_key_env="AURA_PROVIDER_HFSPACE_API_KEY",
            host="aura-org-aura-inference.hf.space", remote_host=True),
    KeySlot(name="workersai", kind="openai_compatible", api_key_env="AURA_CF_KEY", host="cf",
            account_id="acct-123"),
    KeySlot(name="groq", kind="openai_compatible", api_key_env="AURA_GROQ_KEY", host="groq"),
]


@pytest.fixture(autouse=True)
def vault_key(monkeypatch):
    monkeypatch.setenv("AURA_SECRETS_ENCRYPTION_KEY", Fernet.generate_key().decode())


async def _connect(container, platform: Platform, target: str = "") -> None:
    await container.control_store.put_credential(PlatformCredential(
        platform=platform, secret_ref=f"ref-{platform.value}", configured_by=ROOT,
        configured_at=datetime.now(timezone.utc), target=target,
    ))


def _container():
    from aura_core.config import load_settings
    from aura_core.container import Container

    container = Container(load_settings(refresh=True, validate=False))
    container.railway_api.add_service("svc-gw", "gateway", {
        "AURA_ENVIRONMENT": "production", "AURA_PROVIDER_HFSPACE_API_KEY": OLD_HF,
        "AURA_CF_KEY": OLD_CF, "AURA_GROQ_KEY": "old-groq",
    })
    container.railway.self_service_id = "svc-gw"
    container.space_api.add_space(SPACE, secrets={SPACE_KEY_SECRET: OLD_HF})
    cloudflare: InMemoryCloudflareTokens = container.cloudflare_tokens
    container.old_cf_id = cloudflare.add("aura-workersai-old", OLD_CF)
    container.keys = KeysService(
        control=container.control, store=container.control_store, railway=container.railway,
        space=container.space, space_api=container.space_api, cloudflare=cloudflare, slots=SLOTS,
        provider_health=lambda: [{"provider": "hfspace", "state": "closed", "healthy": True},
                                 {"provider": "groq", "state": "open", "healthy": False}],
    )
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


def _post(client, grant, path, body):
    return client.post(f"/api/v3/admin/control/keys{path}", json=body, headers={"x-aura-step-up": grant})


def _reason(response) -> str:
    return response.json()["details"]["reason"]


def _never_leaked(container, *responses) -> None:
    audit = json.dumps([getattr(e, "__dict__", str(e)) for e in container.audit._events], default=str)
    for secret in (OLD_HF, OLD_CF, NEW_GROQ):
        assert secret not in audit
        for response in responses:
            assert secret not in response.text


class TestSlots:
    def test_slots_come_from_providers_that_read_a_key_variable(self) -> None:
        from aura_core.config.external_inference import HOSTED_OPEN_WEIGHT_HOSTS

        workers_host = next(h for h in HOSTED_OPEN_WEIGHT_HOSTS if "cloudflare" in h)
        providers = [
            SimpleNamespace(name="hfspace", kind="openai_compatible", api_key_env="AURA_PROVIDER_HFSPACE_API_KEY",
                            base_url="https://aura-org-aura-inference.hf.space/v1"),
            SimpleNamespace(name="workersai", kind="openai_compatible", api_key_env="AURA_CF_KEY",
                            base_url=f"https://{workers_host}/client/v4/accounts/acct-9/ai/v1"),
            SimpleNamespace(name="local", kind="ollama", api_key_env="", base_url="http://ollama:11434"),
        ]
        slots = {s.name: s for s in key_slots(providers)}
        assert set(slots) == {"hfspace", "workersai"}
        assert slots["hfspace"].remote_host is True and slots["hfspace"].account_id == ""
        assert slots["workersai"].account_id == "acct-9" and slots["workersai"].remote_host is False


class TestOverview:
    async def test_what_each_slot_can_do_depends_on_what_is_connected(self) -> None:
        container = _container()
        before = await container.keys.overview()
        assert before["gateway_ready"] is False
        assert {s["name"]: s["rotation"] for s in before["slots"]} == {
            "hfspace": "replace", "workersai": "replace", "groq": "replace",
        }
        for platform, target in ((Platform.RAILWAY, ""), (Platform.HF_SPACE, SPACE), (Platform.CLOUDFLARE, "")):
            await _connect(container, platform, target)
        after = await container.keys.overview()
        rows = {s["name"]: s for s in after["slots"]}
        assert after["gateway_ready"] is True and after["space_id"] == SPACE
        assert rows["hfspace"]["rotation"] == "both_sides" and rows["workersai"]["rotation"] == "create"
        assert rows["groq"]["rotation"] == "replace" and rows["groq"]["healthy"] is False
        assert rows["workersai"]["cloudflare_token_id"] == container.old_cf_id
        assert all(r["key_set"] is True for r in rows.values())
        assert OLD_HF not in json.dumps(after) and OLD_CF not in json.dumps(after)


class TestReplace:
    def test_without_railway_a_key_cannot_be_written(self) -> None:
        container = _container()
        with _client(container) as client:
            response = _post(client, _grant(client, container), "/groq/replace", {"value": NEW_GROQ, "confirm": "groq"})
        assert response.status_code == 422 and _reason(response) == "railway_not_connected"

    async def test_a_replaced_key_is_a_watched_gateway_change_and_never_echoed(self) -> None:
        container = _container()
        await _connect(container, Platform.RAILWAY)
        with _client(container) as client:
            grant = _grant(client, container)
            wrong = _post(client, grant, "/groq/replace", {"value": NEW_GROQ, "confirm": "gateway"})
            response = _post(client, grant, "/groq/replace", {"value": NEW_GROQ, "confirm": "groq"})
            changes = client.get("/api/v3/admin/control/changes")
        assert wrong.status_code == 422 and _reason(wrong) == "confirmation_required"
        assert response.status_code == 200, response.text
        change = response.json()
        assert change["action"] == "replace_key" and change["status"] == "watching"
        assert change["detail"]["slot"] == "groq"
        assert container.railway_api.raw["svc-gw"]["AURA_GROQ_KEY"] == NEW_GROQ
        _never_leaked(container, response, changes)


class TestRotateBothSides:
    async def _ready(self):
        container = _container()
        await _connect(container, Platform.RAILWAY)
        await _connect(container, Platform.HF_SPACE, SPACE)
        return container

    async def test_both_sides_get_the_same_new_key(self) -> None:
        container = await self._ready()
        with _client(container) as client:
            response = _post(client, _grant(client, container), "/hfspace/rotate", {"confirm": "hfspace"})
        assert response.status_code == 200, response.text
        new = container.railway_api.raw["svc-gw"]["AURA_PROVIDER_HFSPACE_API_KEY"]
        assert new != OLD_HF and len(new) >= 48
        assert container.space_api.secret_values[SPACE][SPACE_KEY_SECRET] == new
        assert response.json()["detail"]["both_sides"] is True
        assert new not in response.text
        _never_leaked(container, response)

    async def test_a_rolled_back_gateway_gives_the_space_its_old_key_back(self) -> None:
        container = await self._ready()
        change = await container.keys.rotate("hfspace", actor=ROOT, confirm="hfspace")
        container.railway_api.set_status(change["deployment_id"], "CRASHED")
        reconciler = Reconciler(
            store=container.control_store, vault=container.control_vault,
            platforms={Platform.RAILWAY: container.railway_api, Platform.HF_SPACE: container.space_api},
            watch_s=300, now=FrozenClock(datetime.now(timezone.utc)).now,
        )
        closed = await reconciler.tick()
        assert [c.status for c in closed] == [ChangeStatus.ROLLED_BACK]
        assert container.railway_api.raw["svc-gw"]["AURA_PROVIDER_HFSPACE_API_KEY"] == OLD_HF
        assert container.space_api.secret_values[SPACE][SPACE_KEY_SECRET] == OLD_HF

    async def test_a_gateway_change_that_is_refused_puts_the_space_key_back_at_once(self) -> None:
        container = await self._ready()
        container.settings_validator.refuse_when(lambda env: "nope" if "AURA_PROVIDER_HFSPACE_API_KEY" in env else None)
        with pytest.raises(ValidationError):
            await container.keys.rotate("hfspace", actor=ROOT, confirm="hfspace")
        assert container.space_api.secret_values[SPACE][SPACE_KEY_SECRET] == OLD_HF
        assert container.railway_api.raw["svc-gw"]["AURA_PROVIDER_HFSPACE_API_KEY"] == OLD_HF

    async def test_a_slot_without_a_key_api_can_only_be_replaced(self) -> None:
        container = await self._ready()
        with _client(container) as client:
            response = _post(client, _grant(client, container), "/groq/rotate", {"confirm": "groq"})
        assert response.status_code == 422 and _reason(response) == "replace_only"


class TestCloudflare:
    async def _ready(self):
        container = _container()
        for platform in (Platform.RAILWAY, Platform.CLOUDFLARE):
            await _connect(container, platform)
        return container

    async def test_a_new_key_is_created_for_the_account_and_the_old_one_revoked_after(self) -> None:
        container = await self._ready()
        cloudflare = container.cloudflare_tokens
        with _client(container) as client:
            grant = _grant(client, container)
            rotated = _post(client, grant, "/workersai/rotate", {"confirm": "workersai"})
            tokens = client.get("/api/v3/admin/control/keys/cloudflare/tokens").json()["tokens"]
            in_use = next(t for t in tokens if t["in_use"])
            refused = _post(client, grant, f"/cloudflare/tokens/{in_use['token_id']}/revoke", {"confirm": in_use["name"]})
            revoked = _post(client, grant, f"/cloudflare/tokens/{container.old_cf_id}/revoke",
                            {"confirm": "aura-workersai-old"})
        assert rotated.status_code == 200, rotated.text
        new_id = rotated.json()["detail"]["cloudflare_token_id"]
        assert cloudflare.created == [(new_id, "acct-123")]
        assert in_use["token_id"] == new_id
        assert refused.status_code == 422 and _reason(refused) == "token_in_use"
        assert revoked.status_code == 200 and cloudflare.revoked == [container.old_cf_id]
        _never_leaked(container, rotated, revoked)

    async def test_a_switch_that_cannot_start_revokes_the_token_it_made(self) -> None:
        container = await self._ready()
        container.settings_validator.refuse_when(lambda env: "nope" if env.get("AURA_CF_KEY", "").startswith("cfv-") else None)
        with pytest.raises(ValidationError):
            await container.keys.rotate("workersai", actor=ROOT, confirm="workersai")
        assert len(container.cloudflare_tokens.created) == 1
        assert container.cloudflare_tokens.revoked == [container.cloudflare_tokens.created[0][0]]
        assert container.railway_api.raw["svc-gw"]["AURA_CF_KEY"] == OLD_CF


class TestHttpCloudflareTokensContract:
    def _tokens(self, handler, root="https://cf.example.test/client/v4"):
        seen: list[httpx.Request] = []

        def record(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return handler(request)

        async def token():
            return "management-token"

        return HttpCloudflareTokens(token_source=token, api_root=root, transport=httpx.MockTransport(record)), seen

    @staticmethod
    def _answer(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/permission_groups"):
            return httpx.Response(200, json={"success": True, "result": [
                {"id": "g-read", "name": "Workers AI Read"}, {"id": "g-write", "name": "Workers AI Write"},
                {"id": "g-dns", "name": "DNS Write"},
            ]})
        if request.method == "POST" and path.endswith("/user/tokens"):
            return httpx.Response(200, json={"success": True, "result": {"id": "t-new", "value": "secret-value"}})
        if path.endswith("/user/tokens/verify"):
            return httpx.Response(200, json={"success": True, "result": {"id": "t-old", "status": "active"}})
        if request.method == "DELETE":
            return httpx.Response(200, json={"success": True, "result": {"id": "t-old"}})
        return httpx.Response(404, json={"success": False})

    async def test_a_new_token_is_scoped_to_the_account_with_only_the_workers_ai_groups(self) -> None:
        tokens, seen = self._tokens(self._answer)
        assert await tokens.create(name="aura-x", account_id="acct-9") == ("t-new", "secret-value")
        body = json.loads(seen[-1].content)
        policy = body["policies"][0]
        assert policy["resources"] == {"com.cloudflare.api.account.acct-9": "*"}
        assert {g["id"] for g in policy["permission_groups"]} == {"g-read", "g-write"}
        assert seen[-1].headers["Authorization"] == "Bearer management-token"

    async def test_identify_uses_the_key_itself_and_revoke_deletes_by_id(self) -> None:
        tokens, seen = self._tokens(self._answer)
        assert await tokens.identify("some-key") == "t-old"
        assert seen[-1].headers["Authorization"] == "Bearer some-key"
        await tokens.revoke("t-old")
        assert seen[-1].method == "DELETE" and seen[-1].url.path.endswith("/user/tokens/t-old")

    async def test_a_refused_management_token_says_so_without_the_vendor_text(self) -> None:
        tokens, _ = self._tokens(lambda r: httpx.Response(403, json={"errors": [{"message": "token management-token bad"}]}))
        with pytest.raises(ValidationError) as caught:
            await tokens.list()
        assert caught.value.details["reason"] == "cloudflare_refused"
        assert "management-token" not in str(caught.value)

    async def test_without_a_workers_ai_provider_there_is_nothing_to_manage(self) -> None:
        tokens, _ = self._tokens(self._answer, root="")
        with pytest.raises(ValidationError) as caught:
            await tokens.list()
        assert caught.value.details["reason"] == "cloudflare_not_configured"
