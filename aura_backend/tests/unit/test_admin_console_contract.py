"""Operator-console contract fixes from the 2026-11-17 integration audit.

INT-006: approvals the design reserves for a Super Admin (connector registration, ADR-021;
blueprint deployment, ADR-024) cannot be decided by an Admin.
INT-030: a source id that cannot be addressed in a URL path is refused when it is created.
INT-031: a namespaced Ollama tag (``library/llama3:8b``) reaches the delete handler.
INT-034: the operator directory fails loudly instead of answering ``[]``.

Tested through the real ASGI app, like ``test_api_surface.py``.
"""

from __future__ import annotations

import asyncio

import pytest

from aura_core.api import Principal, build_app
from aura_core.domain.identifiers import UserId
from aura_core.learning.approval import ApprovalKind

pytest.importorskip("fastapi")


def _client(container, *, role: str):
    from fastapi.testclient import TestClient

    capabilities = {"Admin": ("admin.act",), "SuperAdmin": ("admin.act", "admin.super")}[role]

    async def authenticator(request):
        return Principal(
            user_id=UserId(f"{role.lower()}-user"), email=f"{role.lower()}@example.com",
            role=role, capabilities=capabilities,
        )

    return TestClient(build_app(container, authenticator=authenticator))


def _approval(container, kind: ApprovalKind) -> str:
    request = asyncio.run(container.approvals.request(
        kind=kind, summary=f"{kind} for the test", artefact_digest="sha256:abc",
    ))
    return request.id


class TestSuperAdminApprovalKinds:
    @pytest.mark.parametrize(
        "kind", [ApprovalKind.BLUEPRINT_DEPLOYMENT, ApprovalKind.CONNECTOR_REGISTRATION]
    )
    def test_an_admin_cannot_decide_a_super_admin_kind(self, container, kind) -> None:
        approval_id = _approval(container, kind)
        with _client(container, role="Admin") as client:
            response = client.post(
                f"/api/v3/admin/approvals/{approval_id}/decide", json={"approved": True},
            )
        assert response.status_code == 403
        assert response.json()["detail"]["error"] == "authorization_error"
        still = asyncio.run(container.approvals.get(approval_id))
        assert str(still.status) == "pending"

    def test_a_rejection_is_refused_too(self, container) -> None:
        """Rejecting is a decision as well: an Admin must not be able to block a deployment."""
        approval_id = _approval(container, ApprovalKind.BLUEPRINT_DEPLOYMENT)
        with _client(container, role="Admin") as client:
            response = client.post(
                f"/api/v3/admin/approvals/{approval_id}/decide", json={"approved": False},
            )
        assert response.status_code == 403

    def test_a_super_admin_can_decide_a_blueprint_deployment(self, container) -> None:
        approval_id = _approval(container, ApprovalKind.BLUEPRINT_DEPLOYMENT)
        with _client(container, role="SuperAdmin") as client:
            response = client.post(
                f"/api/v3/admin/approvals/{approval_id}/decide", json={"approved": True},
            )
        assert response.status_code == 200
        assert response.json()["approval"]["status"] == "approved"

    def test_an_admin_still_decides_a_model_promotion(self, container) -> None:
        approval_id = _approval(container, ApprovalKind.MODEL_PROMOTION)
        with _client(container, role="Admin") as client:
            response = client.post(
                f"/api/v3/admin/approvals/{approval_id}/decide", json={"approved": True},
            )
        assert response.status_code == 200

    def test_an_unknown_approval_is_still_404_for_an_admin(self, container) -> None:
        with _client(container, role="Admin") as client:
            response = client.post(
                "/api/v3/admin/approvals/does-not-exist/decide", json={"approved": True},
            )
        assert response.status_code == 404


class TestSourceIds:
    @pytest.mark.parametrize("bad", ["a/b", "a?b", "a#b", "a%2Fb", "..", " lead", "a b"])
    def test_an_id_that_cannot_be_a_path_segment_is_refused(self, container, bad) -> None:
        with _client(container, role="SuperAdmin") as client:
            response = client.post(
                "/api/v3/admin/sources",
                json={"id": bad, "type": "web", "uri": "https://example.com/feed"},
            )
        assert response.status_code == 422

    def test_an_ordinary_id_round_trips_through_patch_and_delete(self, container) -> None:
        with _client(container, role="SuperAdmin") as client:
            created = client.post(
                "/api/v3/admin/sources",
                json={"id": "docs.example_v2-a", "type": "web", "uri": "https://example.com/feed"},
            )
            assert created.status_code == 200
            patched = client.patch(
                "/api/v3/admin/sources/docs.example_v2-a", json={"enabled": False}
            )
            assert patched.status_code == 200
            assert patched.json()["source"]["enabled"] is False
            assert client.delete("/api/v3/admin/sources/docs.example_v2-a").json() == {
                "deleted": True
            }


class TestNamespacedLocalModels:
    def test_a_name_with_a_slash_reaches_the_handler(self, container) -> None:
        """Without ``{name:path}`` the router 404s before the handler runs. With no Ollama on
        the in-memory container the handler itself answers 503, which proves it was reached."""
        with _client(container, role="SuperAdmin") as client:
            response = client.delete("/api/v3/admin/local-models/library%2Fllama3%3A8b")
        assert response.status_code == 503

    def test_the_handler_receives_the_whole_name(self, container, monkeypatch) -> None:
        from aura_core.llm.providers.ollama import OllamaProvider

        deleted: list[str] = []

        class FakeOllama(OllamaProvider):
            def __init__(self) -> None:  # no network, no config
                self._name = "fake-ollama"

            async def close(self) -> None:
                return None

            async def delete_model(self, name: str) -> bool:
                deleted.append(name)
                return True

        # Read at request time by the handler (``_ollama``), so swapping it here is enough.
        monkeypatch.setattr(container, "providers", [*container.providers, FakeOllama()])
        with _client(container, role="SuperAdmin") as client:
            response = client.delete("/api/v3/admin/local-models/library%2Fllama3%3A8b")
        assert response.status_code == 200
        assert deleted == ["library/llama3:8b"]
