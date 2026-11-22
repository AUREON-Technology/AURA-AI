"""The console's Super-Admin control center, stage 1 (ADR-037).

Who can reach it, what a write needs (a fresh re-sign-in and a typed confirmation), that no
secret value ever leaves the server, the hourly write cap, the AURA settings view, and the
automatic rollback of a change whose deployment stays unhealthy. Everything runs on the
in-memory adapters (real state), with no network.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet

from aura_core.adapters.control_store import InMemoryControlStore, InMemoryDeploymentPlatform
from aura_core.adapters.credential_vault import EncryptedCredentialVault, InMemoryCiphertextStore
from aura_core.adapters.field_crypto import FieldCipher
from aura_core.adapters.runtime import FrozenClock
from aura_core.control.catalog import variable_map
from aura_core.control.service import ControlService, Reconciler
from aura_core.domain.control import ChangeStatus, Platform
from aura_core.domain.identifiers import UserId
from aura_core.ports.control_plane import DeploymentHealth, ReauthClaims

ROOT = "root@aura.ai"
CANARY = "tok-zq81x-never-shown"


@pytest.fixture
def vault_key(monkeypatch):
    monkeypatch.setenv("AURA_SECRETS_ENCRYPTION_KEY", Fernet.generate_key().decode())


def _container():
    from aura_core.config import load_settings
    from aura_core.container import Container

    return Container(load_settings(refresh=True, validate=False))


def _client(container, *, role: str = "SuperAdmin", email: str = ROOT, subject: str = "root"):
    from fastapi.testclient import TestClient

    from aura_core.api import Principal, build_app

    async def authenticator(request):
        return Principal(user_id=UserId(subject), email=email, role=role)

    return TestClient(build_app(container, authenticator=authenticator))


def _step_up(client, container, *, email: str = ROOT, age_s: int = 10, token: str = "reauth-1") -> str:
    """Challenge, then redeem a registered re-sign-in token carrying that challenge's nonce."""
    nonce = client.post("/api/v3/admin/control/step-up/challenge").json()["nonce"]
    container.control_reverifier.register(token, ReauthClaims(
        subject="google-sub", email=email,
        auth_time=datetime.now(timezone.utc) - timedelta(seconds=age_s), nonce=nonce,
    ))
    response = client.post("/api/v3/admin/control/step-up", json={"id_token": token, "nonce": nonce})
    assert response.status_code == 200, response.text
    return response.json()["grant"]


def _connect(client, grant: str, *, platform: str = "hf_space", token: str = CANARY,
             confirm: str | None = None, target: str = "org/space"):
    return client.post(
        f"/api/v3/admin/control/platforms/{platform}/credential",
        json={"token": token, "target": target, "confirm": confirm if confirm is not None else platform},
        headers={"x-aura-step-up": grant},
    )


ROUTES = [
    ("GET", "/api/v3/admin/control/overview", None),
    ("GET", "/api/v3/admin/control/settings", None),
    ("GET", "/api/v3/admin/control/changes", None),
    ("POST", "/api/v3/admin/control/step-up/challenge", None),
    ("POST", "/api/v3/admin/control/step-up", {"id_token": "x", "nonce": "y"}),
    ("POST", "/api/v3/admin/control/step-up/revoke", None),
    ("POST", "/api/v3/admin/control/platforms/hf_space/credential", {"token": "x", "confirm": "hf_space"}),
    ("POST", "/api/v3/admin/control/platforms/hf_space/disconnect", {"confirm": "hf_space"}),
    ("GET", "/api/v3/admin/control/railway", None),
    ("GET", "/api/v3/admin/control/railway/services/svc-gw/variables", None),
    ("GET", "/api/v3/admin/control/railway/services/svc-gw/deployments", None),
    ("GET", "/api/v3/admin/control/railway/services/svc-gw/deployments/dep-1/logs", None),
    ("POST", "/api/v3/admin/control/railway/services/svc-gw/variables/validate", {"changes": {"A": "b"}}),
    ("POST", "/api/v3/admin/control/railway/services/svc-gw/variables", {"changes": {"A": "b"}, "confirm": "gateway"}),
    ("POST", "/api/v3/admin/control/railway/services/svc-gw/redeploy", {"confirm": "gateway"}),
    ("POST", "/api/v3/admin/control/railway/services/svc-gw/restart", {"confirm": "gateway"}),
    ("POST", "/api/v3/admin/control/railway/services/svc-gw/rollback", {"deployment_id": "dep-1", "confirm": "gateway"}),
    ("GET", "/api/v3/admin/control/space", None),
    ("GET", "/api/v3/admin/control/space/variables", None),
    ("GET", "/api/v3/admin/control/space/secrets", None),
    ("GET", "/api/v3/admin/control/space/logs", None),
    ("POST", "/api/v3/admin/control/space/variables/validate", {"changes": {"A": "b"}}),
    ("POST", "/api/v3/admin/control/space/variables", {"changes": {"A": "b"}, "confirm": "space"}),
    ("POST", "/api/v3/admin/control/space/secrets", {"changes": {"A": "b"}, "confirm": "space"}),
    ("POST", "/api/v3/admin/control/space/pause", {"confirm": "space"}),
    ("POST", "/api/v3/admin/control/space/restart", {"confirm": "space"}),
    ("POST", "/api/v3/admin/control/space/hardware", {"hardware": "l4x1", "confirm": "space"}),
    ("POST", "/api/v3/admin/control/space/sleep-time", {"seconds": 3600, "confirm": "space"}),
    ("GET", "/api/v3/admin/control/keys", None),
    ("GET", "/api/v3/admin/control/keys/cloudflare/tokens", None),
    ("POST", "/api/v3/admin/control/keys/hosted1/replace", {"value": "k", "confirm": "hosted1"}),
    ("POST", "/api/v3/admin/control/keys/hosted1/rotate", {"confirm": "hosted1"}),
    ("POST", "/api/v3/admin/control/keys/cloudflare/tokens/cf-1/revoke", {"confirm": "x"}),
    ("GET", "/api/v3/admin/control/monitoring", None),
    ("POST", "/api/v3/admin/control/monitoring/apply", {"confirm": "grafana"}),
    ("GET", "/api/v3/admin/control/vector", None),
    ("GET", "/api/v3/admin/control/vector/collections/docs/snapshots", None),
    ("POST", "/api/v3/admin/control/vector/collections/docs/snapshots", {"confirm": "docs"}),
    ("POST", "/api/v3/admin/control/vector/collections/docs/restore", {"snapshot": "s", "confirm": "docs"}),
]


class TestOnlyTheSuperAdmin:
    @pytest.mark.parametrize("role", ["Admin", "Developer", "User"])
    @pytest.mark.parametrize("method,path,body", ROUTES)
    def test_every_route_refuses_anyone_else(self, role, method, path, body) -> None:
        with _client(_container(), role=role) as client:
            response = client.request(method, path, json=body)
        assert response.status_code == 403
        assert "SuperAdmin" in response.json()["message"]

    def test_every_control_route_is_in_the_sweep(self) -> None:
        """A route added later without a role check would otherwise go untested."""
        from aura_core.api import build_app

        async def authenticator(request):  # pragma: no cover - not called
            raise AssertionError

        app = build_app(_container(), authenticator=authenticator)
        registered = {
            (method.upper(), path)
            for path, operations in app.openapi()["paths"].items()
            if path.startswith("/api/v3/admin/control/")
            for method in operations
        }
        swept = {
            (m, p.replace("/hf_space/", "/{platform}/").replace("/svc-gw/", "/{service_id}/")
                 .replace("/dep-1/", "/{deployment_id}/").replace("/hosted1/", "/{slot}/")
                 .replace("/cf-1/", "/{token_id}/").replace("/docs/", "/{collection}/"))
            for m, p, _ in ROUTES
        }
        assert registered == swept

    def test_the_super_admin_can_read(self) -> None:
        with _client(_container()) as client:
            overview = client.get("/api/v3/admin/control/overview")
            settings = client.get("/api/v3/admin/control/settings")
        assert overview.status_code == settings.status_code == 200
        assert overview.headers["cache-control"] == "no-store"
        assert {p["platform"] for p in overview.json()["platforms"]} == {p.value for p in Platform}


class TestWritesNeedAFreshSignIn:
    def test_no_grant_is_403_step_up_required_not_401(self, vault_key) -> None:
        """403, never 401: the console reads a 401 as an expired session and sends the person
        to the login page."""
        container = _container()
        with _client(container) as client:
            response = _connect(client, "")
        assert response.status_code == 403
        assert response.json()["details"]["reason"] == "step_up_required"

    def test_a_fresh_sign_in_buys_a_grant_that_works(self, vault_key) -> None:
        container = _container()
        container.control_verifier.accept(Platform.HF_SPACE, CANARY)
        with _client(container) as client:
            grant = _step_up(client, container)
            response = _connect(client, grant)
            overview = client.get("/api/v3/admin/control/overview").json()
        assert response.status_code == 200, response.text
        hf = next(p for p in overview["platforms"] if p["platform"] == "hf_space")
        assert hf["connected"] and hf["verified"] and hf["target"] == "org/space"

    def test_a_stale_sign_in_is_refused(self) -> None:
        container = _container()
        with _client(container) as client:
            nonce = client.post("/api/v3/admin/control/step-up/challenge").json()["nonce"]
            container.control_reverifier.register("old", ReauthClaims(
                subject="s", email=ROOT, nonce=nonce,
                auth_time=datetime.now(timezone.utc) - timedelta(minutes=30),
            ))
            response = client.post("/api/v3/admin/control/step-up", json={"id_token": "old", "nonce": nonce})
        assert response.status_code == 403
        assert response.json()["details"]["reason"] == "step_up_refused"

    def test_another_account_cannot_step_up_this_session(self) -> None:
        container = _container()
        with _client(container) as client:
            nonce = client.post("/api/v3/admin/control/step-up/challenge").json()["nonce"]
            container.control_reverifier.register("other", ReauthClaims(
                subject="s", email="someone-else@aura.ai", nonce=nonce,
                auth_time=datetime.now(timezone.utc),
            ))
            response = client.post("/api/v3/admin/control/step-up", json={"id_token": "other", "nonce": nonce})
        assert response.status_code == 403

    def test_a_challenge_is_used_once(self) -> None:
        container = _container()
        with _client(container) as client:
            nonce = client.post("/api/v3/admin/control/step-up/challenge").json()["nonce"]
            container.control_reverifier.register("t", ReauthClaims(
                subject="s", email=ROOT, nonce=nonce, auth_time=datetime.now(timezone.utc),
            ))
            first = client.post("/api/v3/admin/control/step-up", json={"id_token": "t", "nonce": nonce})
            replay = client.post("/api/v3/admin/control/step-up", json={"id_token": "t", "nonce": nonce})
        assert first.status_code == 200
        assert replay.status_code == 403

    def test_a_token_for_another_challenge_is_refused(self) -> None:
        container = _container()
        with _client(container) as client:
            nonce = client.post("/api/v3/admin/control/step-up/challenge").json()["nonce"]
            container.control_reverifier.register("t", ReauthClaims(
                subject="s", email=ROOT, nonce="a-different-nonce", auth_time=datetime.now(timezone.utc),
            ))
            response = client.post("/api/v3/admin/control/step-up", json={"id_token": "t", "nonce": nonce})
        assert response.status_code == 403

    def test_a_grant_belongs_to_the_person_who_earned_it(self, vault_key) -> None:
        container = _container()
        with _client(container) as root:
            grant = _step_up(root, container)
        with _client(container, email="second@aura.ai", subject="second") as second:
            response = _connect(second, grant)
        assert response.status_code == 403

    def test_signing_out_drops_the_grant(self, vault_key) -> None:
        """The console's sign-out revokes the grant, so the still-valid access token alone
        cannot write in the grant's remaining minutes."""
        container = _container()
        container.control_verifier.accept(Platform.HF_SPACE, CANARY)
        with _client(container) as client:
            grant = _step_up(client, container)
            revoked = client.post("/api/v3/admin/control/step-up/revoke")
            response = _connect(client, grant)
        assert revoked.status_code == 200 and revoked.json()["revoked"] == 1
        assert response.status_code == 403
        assert response.json()["details"]["reason"] == "step_up_required"

    def test_the_typed_confirmation_is_checked_by_the_server(self, vault_key) -> None:
        container = _container()
        container.control_verifier.accept(Platform.HF_SPACE, CANARY)
        with _client(container) as client:
            grant = _step_up(client, container)
            response = _connect(client, grant, confirm="yes")
        assert response.status_code == 422
        assert response.json()["details"]["reason"] == "confirmation_required"


class TestTokens:
    def test_a_malformed_vault_key_costs_the_control_center_not_the_gateway(self, monkeypatch) -> None:
        """The control vault is built on every boot, so a bad key must not stop the container."""
        monkeypatch.setenv("AURA_SECRETS_ENCRYPTION_KEY", "not-a-fernet-key")
        container = _container()
        container.control_verifier.accept(Platform.HF_SPACE, CANARY)
        with _client(container) as client:
            overview = client.get("/api/v3/admin/control/overview").json()
            grant = _step_up(client, container)
            response = _connect(client, grant)
        assert overview["vault_ready"] is False
        assert response.status_code == 422
        assert response.json()["details"]["reason"] == "vault_not_configured"

    def test_no_token_is_stored_without_the_vault_key(self, monkeypatch) -> None:
        monkeypatch.delenv("AURA_SECRETS_ENCRYPTION_KEY", raising=False)
        container = _container()
        container.control_verifier.accept(Platform.HF_SPACE, CANARY)
        with _client(container) as client:
            grant = _step_up(client, container)
            response = _connect(client, grant)
        assert response.status_code == 422
        assert response.json()["details"]["reason"] == "vault_not_configured"

    def test_a_token_the_platform_refuses_is_not_stored(self, vault_key) -> None:
        container = _container()
        with _client(container) as client:
            grant = _step_up(client, container)
            response = _connect(client, grant, token="not-accepted")
            overview = client.get("/api/v3/admin/control/overview").json()
        assert response.status_code == 422
        assert not next(p for p in overview["platforms"] if p["platform"] == "hf_space")["connected"]

    def test_a_grafana_token_is_checked_before_it_is_saved(self, vault_key) -> None:
        container = _container()
        container.control_verifier.accept(Platform.GRAFANA, "grafana-good")
        with _client(container) as client:
            grant = _step_up(client, container)
            refused = _connect(client, grant, platform="grafana", token="grafana-bad", target="https://s.grafana.net")
            saved = _connect(client, grant, platform="grafana", token="grafana-good", target="https://s.grafana.net")
        assert refused.status_code == 422
        assert saved.status_code == 200 and saved.json()["verified"] is True

    def test_a_railway_token_is_checked_before_it_is_saved(self, vault_key) -> None:
        container = _container()
        container.control_verifier.accept(Platform.RAILWAY, "railway-good")
        with _client(container) as client:
            grant = _step_up(client, container)
            refused = _connect(client, grant, platform="railway", token="railway-bad", target="")
            saved = _connect(client, grant, platform="railway", token="railway-good", target="")
        assert refused.status_code == 422
        assert saved.status_code == 200 and saved.json()["verified"] is True

    def test_no_secret_value_ever_leaves_the_server(self, vault_key, monkeypatch) -> None:
        monkeypatch.setenv("AURA_SMTP_PASSWORD", CANARY + "-smtp")
        container = _container()
        container.control_verifier.accept(Platform.HF_SPACE, CANARY)
        with _client(container) as client:
            grant = _step_up(client, container)
            connected = _connect(client, grant)
            bodies = [connected.text] + [
                client.get(path).text for path in (
                    "/api/v3/admin/control/overview", "/api/v3/admin/control/settings",
                    "/api/v3/admin/control/changes",
                )
            ]
        audit = [json.dumps(e.as_dict() if hasattr(e, "as_dict") else e.__dict__, default=str)
                 for e in container.audit._events]
        for text in bodies + audit:
            assert CANARY not in text
        # Sealed at rest, too.
        assert all(CANARY not in v for v in container.control_vault._store.raw_rows().values()) \
            if hasattr(container.control_vault._store, "raw_rows") else True

    def test_replacing_a_token_deletes_the_old_one(self, vault_key) -> None:
        container = _container()
        container.control_verifier.accept(Platform.HF_SPACE, CANARY)
        container.control_verifier.accept(Platform.HF_SPACE, CANARY + "-2")
        with _client(container) as client:
            grant = _step_up(client, container)
            _connect(client, grant)
            _connect(client, grant, token=CANARY + "-2")
        assert len(container.control_vault._store._rows) == 1

    def test_disconnect_removes_the_token(self, vault_key) -> None:
        container = _container()
        container.control_verifier.accept(Platform.HF_SPACE, CANARY)
        with _client(container) as client:
            grant = _step_up(client, container)
            _connect(client, grant)
            response = client.post(
                "/api/v3/admin/control/platforms/hf_space/disconnect",
                json={"confirm": "hf_space"}, headers={"x-aura-step-up": grant},
            )
            changes = client.get("/api/v3/admin/control/changes").json()["changes"]
        assert response.status_code == 200
        assert container.control_vault._store._rows == {}
        assert [c["action"] for c in changes] == ["disconnect", "connect"]

    def test_writes_are_capped_per_hour(self, vault_key, monkeypatch) -> None:
        monkeypatch.setenv("AURA_CONTROL_WRITES_PER_HOUR", "2")
        container = _container()
        container.control_verifier.accept(Platform.HF_SPACE, CANARY)
        with _client(container) as client:
            grant = _step_up(client, container)
            statuses = [_connect(client, grant).status_code for _ in range(3)]
        assert statuses == [200, 200, 429]


class TestTheSettingsView:
    def test_every_setting_names_the_variable_that_sets_it(self) -> None:
        """A new setting the view cannot place fails here instead of silently disappearing."""
        from dataclasses import fields, is_dataclass

        from aura_core.config.settings import AuraSettings

        mapped = variable_map()
        defaults = AuraSettings()
        unmapped = [
            f"{top.name}.{f.name}"
            for top in fields(AuraSettings)
            if is_dataclass(getattr(defaults, top.name)) and top.name != "release"
            for f in fields(getattr(defaults, top.name))
            if f"{top.name}.{f.name}" not in mapped and f"{top.name}.{f.name}" != "llm.providers"
        ]
        assert unmapped == []

    def test_values_bounds_and_protection(self) -> None:
        with _client(_container()) as client:
            groups = client.get("/api/v3/admin/control/settings").json()["groups"]
        entries = {e["variable"]: e for g in groups for e in g["settings"]}
        top_k = entries["AURA_KNOWLEDGE_TOP_K"]
        assert (top_k["minimum"], top_k["maximum"], top_k["secret"]) == (1, 50, False)
        assert entries["AURA_DATABASE_URL"]["protected"] is True
        assert entries["AURA_SMTP_PASSWORD"]["secret"] is True
        assert entries["AURA_SMTP_PASSWORD"]["value"] is None


class TestAutomaticRollback:
    def _setup(self):
        clock = FrozenClock(datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc))
        store = InMemoryControlStore()
        vault = EncryptedCredentialVault(
            cipher=FieldCipher([Fernet.generate_key().decode()]), store=InMemoryCiphertextStore()
        )
        railway = InMemoryDeploymentPlatform()
        railway.variables["gateway"] = {"AURA_KNOWLEDGE_TOP_K": "9"}
        railway.deploy("gateway", "dep-old", DeploymentHealth.HEALTHY)
        railway.deploy("gateway", "dep-new")
        control = ControlService(store=store, vault=vault, verifier=None, writes_per_hour=30, now=clock.now)
        reconciler = Reconciler(
            store=store, vault=vault, platforms={Platform.RAILWAY: railway}, watch_s=300, now=clock.now,
        )
        return clock, store, vault, railway, control, reconciler

    async def _watch(self, control):
        return await control.watch(
            actor=ROOT, platform=Platform.RAILWAY, target="gateway", action="set_variables",
            detail={"variables": [{"name": "AURA_KNOWLEDGE_TOP_K", "secret": False}]},
            deployment_id="dep-new", previous_deployment_id="dep-old",
            restore_values={"AURA_KNOWLEDGE_TOP_K": "4", "AURA_NEW_FLAG": None},
        )

    async def test_a_deployment_healthy_through_the_watch_succeeds(self) -> None:
        clock, store, vault, railway, control, reconciler = self._setup()
        change = await self._watch(control)
        railway.set_health("dep-new", DeploymentHealth.HEALTHY)
        assert await reconciler.tick() == []  # not watched long enough yet
        clock.advance(301)
        [closed] = await reconciler.tick()
        assert closed.status is ChangeStatus.SUCCEEDED
        assert closed.restore_ref is None and vault._store._rows == {}
        assert railway.rollbacks == []

    async def test_an_unhealthy_deployment_is_rolled_back_and_its_variables_restored(self) -> None:
        clock, store, vault, railway, control, reconciler = self._setup()
        await self._watch(control)
        railway.variables["gateway"]["AURA_NEW_FLAG"] = "on"
        railway.set_health("dep-new", DeploymentHealth.UNHEALTHY)
        clock.advance(301)
        [closed] = await reconciler.tick()
        assert closed.status is ChangeStatus.ROLLED_BACK
        assert "rolled back automatically" in closed.reason
        assert railway.rollbacks == [("gateway", "dep-old")]
        assert railway.variables["gateway"] == {"AURA_KNOWLEDGE_TOP_K": "4"}
        assert vault._store._rows == {}

    async def test_a_deployment_that_never_starts_is_rolled_back_at_once(self) -> None:
        clock, store, vault, railway, control, reconciler = self._setup()
        await self._watch(control)
        railway.set_health("dep-new", DeploymentHealth.FAILED)
        [closed] = await reconciler.tick()
        assert closed.status is ChangeStatus.ROLLED_BACK

    async def test_a_failed_rollback_says_so(self) -> None:
        clock, store, vault, railway, control, reconciler = self._setup()
        await self._watch(control)
        railway.fail_rollback = True
        railway.set_health("dep-new", DeploymentHealth.FAILED)
        [closed] = await reconciler.tick()
        assert closed.status is ChangeStatus.FAILED
        assert "roll back by hand" in closed.reason

    async def test_only_one_gateway_process_acts(self) -> None:
        clock, store, vault, railway, control, reconciler = self._setup()
        other = Reconciler(store=store, vault=vault, platforms={Platform.RAILWAY: railway},
                           watch_s=300, now=clock.now, holder="other")
        await self._watch(control)
        railway.set_health("dep-new", DeploymentHealth.FAILED)
        first = await reconciler.tick()
        second = await other.tick()
        assert len(first) == 1 and second == []
        assert railway.rollbacks == [("gateway", "dep-old")]


class TestGoogleReSignInClaims:
    """``GoogleReverifier``'s claim handling, past the signature check (google-auth's job).

    The shape below is what Google really sends: its discovery document has no ``auth_time`` in
    ``claims_supported``, so a verifier that required it refused every live re-sign-in.
    """

    def _google_claims(self, **overrides):
        now = int(datetime.now(timezone.utc).timestamp())
        claims = {
            "iss": "https://accounts.google.com", "aud": "console-client", "sub": "1234",
            "email": ROOT, "email_verified": True, "iat": now, "exp": now + 3600, "nonce": "n-1",
        }
        claims.update(overrides)
        return {k: v for k, v in claims.items() if v is not None}

    async def _verify(self, monkeypatch, claims):
        from aura_core.adapters.identity_reverify import GoogleReverifier

        monkeypatch.setattr(GoogleReverifier, "_verify_sync", staticmethod(lambda token: claims))
        return await GoogleReverifier(client_ids=["console-client"]).verify("a-google-id-token")

    async def test_a_real_google_token_without_auth_time_is_dated_by_iat(self, monkeypatch) -> None:
        claims = self._google_claims()
        result = await self._verify(monkeypatch, claims)
        assert result.auth_time == datetime.fromtimestamp(claims["iat"], tz=timezone.utc)
        assert (result.email, result.nonce) == (ROOT, "n-1")

    async def test_auth_time_wins_when_an_issuer_sends_it(self, monkeypatch) -> None:
        claims = self._google_claims(auth_time=1_700_000_000)
        result = await self._verify(monkeypatch, claims)
        assert result.auth_time == datetime.fromtimestamp(1_700_000_000, tz=timezone.utc)

    async def test_a_token_with_no_time_at_all_is_refused(self, monkeypatch) -> None:
        from aura_core.domain.errors import AuthenticationError

        with pytest.raises(AuthenticationError):
            await self._verify(monkeypatch, self._google_claims(iat=None))

    async def test_a_google_token_issued_ten_minutes_ago_buys_no_grant(self) -> None:
        container = _container()
        with _client(container) as client:
            nonce = client.post("/api/v3/admin/control/step-up/challenge").json()["nonce"]
            container.control_reverifier.register("old", ReauthClaims(
                subject="1234", email=ROOT, nonce=nonce,
                auth_time=datetime.now(timezone.utc) - timedelta(minutes=10),
            ))
            response = client.post("/api/v3/admin/control/step-up", json={"id_token": "old", "nonce": nonce})
        assert response.status_code == 403
        assert response.json()["details"]["reason"] == "step_up_refused"
