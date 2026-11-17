"""Client/server contract fixes from the 2026-11-17 integration audit.

- INT-026: ``/capabilities`` advertises the per-request image total the chat route enforces, so
  the client can check the running total instead of finding out from a 422.
- INT-039: ``POST /learning/log`` no longer lets any signed-in user write arbitrary, unbound
  records into the training corpus.
- INT-043: ``GET /memory`` honours the ``offset`` the client sends.
"""

from __future__ import annotations

import pytest

from aura_core.api import Principal, build_app
from aura_core.domain.identifiers import UserId

pytest.importorskip("fastapi")


def _client(container):
    from fastapi.testclient import TestClient

    async def authenticate(request):
        return Principal(user_id=UserId("pager"), email="p@example.com")

    return TestClient(build_app(container, authenticator=authenticate))


class TestCapabilitiesAdvertiseTheRequestTotal:
    def test_max_request_image_bytes_sits_next_to_the_per_image_limit(self, container) -> None:
        with _client(container) as client:
            vision = client.get("/api/v3/capabilities").json()["vision"]
        assert vision["max_request_image_bytes"] == container.settings.vision.max_request_image_bytes
        assert vision["max_request_image_bytes"] >= vision["max_image_bytes"]


class TestCapabilitiesAdvertiseTheKnowledgeUploadLimit:
    """The advertised upload limit is the one ``/knowledge/documents/upload`` enforces, read from
    the same setting -- so a configured limit, not the 8 MiB default, is what the client sees."""

    def test_advertised_equals_enforced(self, monkeypatch) -> None:
        from aura_core.config.settings import load_settings
        from aura_core.container import Container

        monkeypatch.setenv("AURA_KNOWLEDGE_MAX_DOC_BYTES", "4096")
        container = Container(load_settings(refresh=True, validate=False))
        with _client(container) as client:
            advertised = client.get("/api/v3/capabilities").json()["knowledge"]["max_upload_bytes"]
            refused = client.post(
                "/api/v3/knowledge/documents/upload",
                data={"title": "too big"},
                files={"file": ("big.pdf", b"%PDF-" + b"x" * 5000, "application/pdf")},
            )
            accepted_size = client.post(
                "/api/v3/knowledge/documents/upload",
                data={"title": "fits"},
                files={"file": ("small.pdf", b"x" * advertised, "application/pdf")},
            )
        assert advertised == 4096
        assert refused.status_code == 422, refused.text
        assert refused.json()["details"]["limit_bytes"] == advertised
        # Exactly the advertised size is not refused for size (it fails later, as a bad PDF).
        assert "limit_bytes" not in accepted_size.text


class TestMemoryOffset:
    @pytest.fixture
    def client(self, container):
        with _client(container) as client:
            for i in range(5):
                created = client.post("/api/v3/memory", json={
                    "content": f"Distinct fact number {i} about the user's {['tea', 'cats', 'running', 'jazz', 'chess'][i]}.",
                })
                assert created.status_code == 200, created.text
            yield client

    def test_offset_pages_through_the_list_without_overlap(self, client) -> None:
        pages = [
            [m["id"] for m in client.get(
                "/api/v3/memory", params={"limit": 2, "offset": offset}
            ).json()["memories"]]
            for offset in (0, 2, 4)
        ]
        assert [len(p) for p in pages] == [2, 2, 1]
        flat = [i for page in pages for i in page]
        assert len(set(flat)) == 5
        everything = [m["id"] for m in client.get(
            "/api/v3/memory", params={"limit": 10}).json()["memories"]]
        assert flat == everything

    def test_an_offset_past_the_end_is_empty_not_the_first_page(self, client) -> None:
        response = client.get("/api/v3/memory", params={"limit": 2, "offset": 50})
        assert response.status_code == 200
        assert response.json()["memories"] == []

    def test_a_negative_offset_is_rejected(self, client) -> None:
        assert client.get("/api/v3/memory", params={"offset": -1}).status_code == 422

    def test_a_search_query_honours_offset_too(self, client) -> None:
        params = {"query": "distinct fact about the user", "limit": 10}
        ranked = [m["id"] for m in client.get("/api/v3/memory", params=params).json()["memories"]]
        assert len(ranked) >= 2
        skipped = [m["id"] for m in client.get(
            "/api/v3/memory", params={**params, "offset": 1}).json()["memories"]]
        assert skipped == ranked[1:]


class TestLearningLogIsNotAnOpenTrainingInlet:
    RECORD = {
        "id": "l1", "prompt": "p", "response": "r", "rating": 5,
        "timestamp": "2026-08-21T00:00:00Z",
    }

    @pytest.fixture
    def app_and_writes(self, monkeypatch):
        import infrastructure.http_limits as http_limits
        from aura_service import app
        from infrastructure.http_limits import RateLimitMiddleware
        from services.learning_pipeline import learning_pipeline

        RateLimitMiddleware.request_history.clear()
        monkeypatch.setattr(http_limits, "_rate_limit_redis", None)
        writes: list[dict] = []
        monkeypatch.setattr(
            learning_pipeline, "log_feedback_record", lambda record: writes.append(record) or True
        )
        try:
            yield app, writes
        finally:
            app.dependency_overrides.clear()

    def _as(self, app, role: str, uid: str = "u-1"):
        from fastapi.testclient import TestClient

        from services.jwt_auth import verify_access_token

        app.dependency_overrides[verify_access_token] = lambda: {
            "uid": uid, "email": f"{uid}@aura.ai", "role": role,
        }
        return TestClient(app)

    def test_a_plain_user_cannot_write_training_records(self, app_and_writes) -> None:
        app, writes = app_and_writes
        response = self._as(app, "User").post("/api/v3/learning/log", json=self.RECORD)
        assert response.status_code == 403
        assert writes == []

    def test_an_admin_record_is_bound_to_the_caller(self, app_and_writes) -> None:
        app, writes = app_and_writes
        response = self._as(app, "Admin", uid="admin-7").post(
            "/api/v3/learning/log",
            json={**self.RECORD, "user_id": "someone-else", "submitted_by": "forged"},
        )
        assert response.status_code == 200, response.text
        (record,) = writes
        assert record["submitted_by"] == "admin-7"
        assert "user_id" not in record or record["user_id"] == "admin-7"
        assert record["prompt"] == "p"
