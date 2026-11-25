"""The control center's Grafana Cloud and Qdrant Cloud stage (ADR-037, stage 5).

Through the real routes against in-memory adapters (real state, no network), and the HTTP
adapters against recorded exchanges: the alert rules applied are exactly the repository's, each
group's result is reported, status reads are honest about what they could not read, a Qdrant
restore keeps the state it replaces, and no token reaches a response.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet

from aura_core.adapters.alert_rules import ALERT_RULES_PATH, load_alert_groups
from aura_core.adapters.grafana_cloud import HttpGrafanaCloud, stack_slug
from aura_core.adapters.qdrant_admin import HttpQdrantAdmin
from aura_core.domain.control import Platform, PlatformCredential
from aura_core.domain.errors import ValidationError
from aura_core.ports.control_plane import ReauthClaims

ROOT = "root@aura.ai"
REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def vault_key(monkeypatch):
    monkeypatch.setenv("AURA_SECRETS_ENCRYPTION_KEY", Fernet.generate_key().decode())


def _container():
    from aura_core.config import load_settings
    from aura_core.container import Container

    container = Container(load_settings(refresh=True, validate=False))
    container.vector_api.add_collection("aura-knowledge", 1200)
    return container


async def _connect(container, platform: Platform, target: str = "") -> None:
    await container.control_store.put_credential(PlatformCredential(
        platform=platform, secret_ref=f"ref-{platform.value}", configured_by=ROOT,
        configured_at=datetime.now(timezone.utc), target=target,
    ))


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


class TestTheRulesAreTheRepositorys:
    def test_the_packaged_copy_is_byte_identical_to_the_deploy_file(self) -> None:
        """The gateway image is built from aura_backend/ alone, so it carries a copy. A rule
        changed in deploy/ and not here would be silently left out of Grafana Cloud."""
        deploy = REPO / "deploy" / "monitoring" / "alerts" / "aura.yml"
        assert ALERT_RULES_PATH.read_bytes() == deploy.read_bytes(), (
            "copy deploy/monitoring/alerts/aura.yml to aura_backend/aura_core/control/assets/aura-alerts.yml"
        )

    def test_every_group_has_a_name_and_rules(self) -> None:
        groups = load_alert_groups()
        assert groups and all(g["name"].startswith("aura-") and g.get("rules") for g in groups)


class TestMonitoring:
    def test_not_connected_says_where_to_connect(self) -> None:
        container = _container()
        with _client(container) as client:
            response = client.get("/api/v3/admin/control/monitoring")
        assert response.status_code == 404 and "connect it in Settings" in response.json()["message"]

    async def test_apply_loads_every_group_and_status_reads_them_back(self, monkeypatch) -> None:
        monkeypatch.setenv("AURA_CONTROL_PUBLIC_API_URL", "https://api.example.test")
        container = _container()
        await _connect(container, Platform.GRAFANA, "https://aurastack.grafana.net")
        grafana = container.grafana_api
        grafana.answers = {
            'up{job="aura-gateway"}': [{"metric": {"job": "aura-gateway"}, "value": 1.0}],
            "ALERTS": [{"metric": {"alertname": "AuraHighErrorRate", "severity": "warning"}, "value": 1.0}],
            "probe_all_success": [{"metric": {"job": "AURA liveness", "instance": "https://api.example.test/health/live"},
                                   "value": 1.0}],
        }
        with _client(container) as client:
            before = client.get("/api/v3/admin/control/monitoring").json()
            wrong = client.post("/api/v3/admin/control/monitoring/apply", json={"confirm": "yes"},
                                headers={"x-aura-step-up": _grant(client, container)})
            applied = client.post("/api/v3/admin/control/monitoring/apply", json={"confirm": "grafana"},
                                  headers={"x-aura-step-up": _grant(client, container)})
            after = client.get("/api/v3/admin/control/monitoring").json()
        names = [g["name"] for g in load_alert_groups()]
        assert before["rules"]["all_loaded"] is False
        assert wrong.status_code == 422
        assert applied.status_code == 200 and applied.json()["status"] == "recorded"
        assert [r["group"] for r in applied.json()["results"]] == names
        assert sorted(grafana.groups["aura"]) == sorted(names)
        assert after["rules"]["all_loaded"] is True
        assert after["scrape"]["state"] == "up" and after["scrape"]["url"] == "https://api.example.test/metrics"
        assert after["scrape"]["job"] == "aura-gateway"
        assert after["firing"] == [{"alertname": "AuraHighErrorRate", "severity": "warning"}]
        assert after["uptime"]["observed"][0]["success_ratio"] == 1.0
        assert after["errors"] == []

    async def test_a_group_the_ruler_refuses_fails_the_apply_and_names_the_group(self) -> None:
        container = _container()
        await _connect(container, Platform.GRAFANA, "https://aurastack.grafana.net")
        container.grafana_api.fail_group = "aura-billing"
        result = await container.monitoring.apply(actor=ROOT, confirm="grafana")
        assert result["change"]["status"] == "failed"
        assert "aura-billing" in result["change"]["reason"]
        assert [r["ok"] for r in result["results"] if r["group"] == "aura-billing"] == [False]

    async def test_a_query_that_cannot_run_is_reported_not_read_as_all_clear(self) -> None:
        container = _container()
        await _connect(container, Platform.GRAFANA, "https://aurastack.grafana.net")

        async def refused(promql):
            raise ValidationError("Grafana Cloud refused the token (it needs metrics:read)")

        container.grafana_api.query = refused
        overview = await container.monitoring.overview(public_url="https://gw")
        assert overview["scrape"]["state"] == "missing"
        assert any("metrics:read" in e for e in overview["errors"])


class TestVector:
    def test_not_connected_says_where_to_connect(self) -> None:
        container = _container()
        with _client(container) as client:
            response = client.get("/api/v3/admin/control/vector")
        assert response.status_code == 404

    async def test_snapshot_then_restore_keeps_the_state_it_replaces(self) -> None:
        container = _container()
        await _connect(container, Platform.QDRANT)
        qdrant = container.vector_api
        with _client(container) as client:
            grant = _grant(client, container)
            overview = client.get("/api/v3/admin/control/vector").json()
            made = client.post("/api/v3/admin/control/vector/collections/aura-knowledge/snapshots",
                               json={"confirm": "aura-knowledge"}, headers={"x-aura-step-up": grant})
            qdrant.points["aura-knowledge"] = 1500  # the collection changes after the snapshot
            wrong = client.post("/api/v3/admin/control/vector/collections/aura-knowledge/restore",
                                json={"snapshot": made.json()["snapshot"]["name"], "confirm": "yes"},
                                headers={"x-aura-step-up": grant})
            restored = client.post("/api/v3/admin/control/vector/collections/aura-knowledge/restore",
                                   json={"snapshot": made.json()["snapshot"]["name"], "confirm": "aura-knowledge"},
                                   headers={"x-aura-step-up": grant})
            listing = client.get("/api/v3/admin/control/vector/collections/aura-knowledge/snapshots").json()
            missing = client.post("/api/v3/admin/control/vector/collections/aura-knowledge/restore",
                                  json={"snapshot": "nope.snapshot", "confirm": "aura-knowledge"},
                                  headers={"x-aura-step-up": grant})
        assert overview["healthy"] is True and overview["collections"][0]["points_count"] == 1200
        assert made.status_code == 200 and made.json()["action"] == "vector_snapshot"
        assert wrong.status_code == 422
        assert restored.status_code == 200
        assert qdrant.points["aura-knowledge"] == 1200
        safety = restored.json()["safety_snapshot"]
        assert safety in {s["name"] for s in listing["snapshots"]}
        assert restored.json()["detail"]["safety_snapshot"] == safety
        assert missing.status_code == 404


class TestHttpQdrantAdminContract:
    def _admin(self, handler):
        seen: list[httpx.Request] = []

        def record(request):
            seen.append(request)
            return handler(request)

        async def key():
            return "qdrant-key"

        async def url():
            return "https://cluster.example.test:6333"

        return HttpQdrantAdmin(token_source=key, url_source=url, transport=httpx.MockTransport(record)), seen

    async def test_collections_parse_single_and_named_vectors(self) -> None:
        def answer(request):
            path = request.url.path
            if path == "/collections":
                return httpx.Response(200, json={"result": {"collections": [{"name": "a"}, {"name": "b"}]}})
            if path == "/collections/a":
                return httpx.Response(200, json={"result": {"status": "green", "points_count": 5, "segments_count": 2,
                                                            "config": {"params": {"vectors": {"size": 384, "distance": "Cosine"}}}}})
            return httpx.Response(200, json={"result": {"status": "yellow", "points_count": 1, "segments_count": 1,
                                                        "config": {"params": {"vectors": {"dense": {"size": 768, "distance": "Dot"}}}}}})

        admin, seen = self._admin(answer)
        found = {c.name: c for c in await admin.collections()}
        assert (found["a"].vector_size, found["a"].distance) == (384, "Cosine")
        assert (found["b"].vector_size, found["b"].distance, found["b"].status) == (768, "Dot", "yellow")
        assert all(r.headers["api-key"] == "qdrant-key" for r in seen)

    async def test_restore_recovers_from_the_snapshot_file_on_the_node(self) -> None:
        admin, seen = self._admin(lambda request: httpx.Response(200, json={"result": True, "status": "ok"}))
        await admin.restore_snapshot("aura-knowledge", "aura-knowledge-1.snapshot")
        request = seen[-1]
        assert request.method == "PUT" and request.url.path == "/collections/aura-knowledge/snapshots/recover"
        assert json.loads(request.content) == {
            "location": "file:///qdrant/snapshots/aura-knowledge/aura-knowledge-1.snapshot", "priority": "snapshot",
        }

    async def test_a_refused_key_is_a_validation_error(self) -> None:
        admin, _ = self._admin(lambda request: httpx.Response(401, text="bad key qdrant-key"))
        with pytest.raises(ValidationError) as caught:
            await admin.collections()
        assert caught.value.details["reason"] == "qdrant_refused" and "qdrant-key" not in str(caught.value)


class TestHttpGrafanaCloudContract:
    def _cloud(self, handler):
        seen: list[httpx.Request] = []

        def record(request):
            seen.append(request)
            return handler(request)

        async def token():
            return "glc-token"

        async def stack():
            return "https://aurastack.grafana.net"

        return HttpGrafanaCloud(token_source=token, stack_source=stack, transport=httpx.MockTransport(record)), seen

    @staticmethod
    def _stack(request):
        return httpx.Response(200, json={"slug": "aurastack", "url": "https://aurastack.grafana.net",
                                         "hmInstancePromUrl": "https://prometheus-prod-01.grafana.net",
                                         "hmInstancePromId": "123456"})

    def test_the_stack_slug_comes_from_its_url(self) -> None:
        assert stack_slug("https://aurastack.grafana.net") == "aurastack"
        assert stack_slug("aurastack") == "aurastack"
        with pytest.raises(ValidationError):
            stack_slug("")

    async def test_a_rule_group_goes_to_the_first_ruler_path_that_exists_and_is_remembered(self) -> None:
        def answer(request):
            if request.url.host == "grafana.com":
                return self._stack(request)
            if request.url.path.startswith("/api/prom/config/v1/rules"):
                return httpx.Response(404)
            return httpx.Response(202)

        cloud, seen = self._cloud(answer)
        await cloud.load_rule_group("aura", {"name": "aura-x", "rules": [{"alert": "A", "expr": "up == 0"}]})
        await cloud.load_rule_group("aura", {"name": "aura-y", "rules": []})
        posts = [r for r in seen if r.method == "POST"]
        assert [r.url.path for r in posts] == [
            "/api/prom/config/v1/rules/aura", "/prometheus/config/v1/rules/aura", "/prometheus/config/v1/rules/aura",
        ]
        assert posts[-1].headers["content-type"] == "application/yaml"
        assert "name: aura-y" in posts[-1].content.decode()
        lookup = next(r for r in seen if r.url.host == "grafana.com")
        assert lookup.url.path == "/api/instances/aurastack" and lookup.headers["Authorization"] == "Bearer glc-token"
        assert posts[0].headers["Authorization"].startswith("Basic ")

    async def test_queries_use_the_prometheus_api_under_api_prom(self) -> None:
        def answer(request):
            if request.url.host == "grafana.com":
                return self._stack(request)
            return httpx.Response(200, json={"data": {"result": [{"metric": {"job": "aura-gateway"}, "value": [1, "1"]}]}})

        cloud, seen = self._cloud(answer)
        rows = await cloud.query('up{job="aura-gateway"}')
        assert rows == [{"metric": {"job": "aura-gateway"}, "value": 1.0}]
        assert seen[-1].url.path == "/api/prom/api/v1/query"

    async def test_a_refused_token_names_the_scope_it_lacks(self) -> None:
        def answer(request):
            if request.url.host == "grafana.com":
                return self._stack(request)
            return httpx.Response(403, text="forbidden for glc-token")

        cloud, _ = self._cloud(answer)
        with pytest.raises(ValidationError) as caught:
            await cloud.query("up")
        assert "metrics:read" in caught.value.message and "glc-token" not in caught.value.message

    async def test_each_usual_token_mistake_gets_its_own_reason(self) -> None:
        """Found on the first live Connect (2026-11-22): one message for 401 and 403 could not
        tell a wrong token type from a policy without stacks:read."""
        from aura_core.adapters.grafana_cloud import grafana_stack

        with pytest.raises(ValidationError) as wrong_type:
            await grafana_stack("glsa_from_the_stack", "aurastack",
                                transport=httpx.MockTransport(lambda r: pytest.fail("no call expected")))
        assert wrong_type.value.details["reason"] == "grafana_wrong_token_type"
        for status, reason, words in ((401, "grafana_token_invalid", "glc_"), (403, "grafana_refused", "stacks:read")):
            with pytest.raises(ValidationError) as caught:
                await grafana_stack("glc_token", "aurastack",
                                    transport=httpx.MockTransport(lambda r, s=status: httpx.Response(s)))
            assert caught.value.details["reason"] == reason and words in caught.value.message
            assert "glc_token" not in caught.value.message

