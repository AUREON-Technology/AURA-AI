"""``GET /api/v3/admin/scorecard`` (Phase G1): the roadmap's own scorecard fed by a real turn
history and real recorded eval baselines, not hand-maintained numbers.
"""

from __future__ import annotations

from aura_core.domain.identifiers import UserId
from aura_core.kernel import TurnRequest


def _client(container, *, role: str = "Admin"):
    from fastapi.testclient import TestClient

    from aura_core.api import Principal, build_app

    async def authenticator(request):
        return Principal(user_id=UserId("scorecard-caller"), role=role)

    return TestClient(build_app(container, authenticator=authenticator))


class TestScorecardEndpoint:
    async def test_reflects_a_real_turn_just_run(self, container, user) -> None:
        await container.startup()
        await container.kernel.run(TurnRequest(message="hello", user_id=user))

        with _client(container) as client:
            response = client.get("/api/v3/admin/scorecard")
        assert response.status_code == 200
        body = response.json()
        assert body["turns"]["turns"] >= 1
        assert "window_hours" in body and body["window_hours"] == 24

    async def test_the_docs_corpus_baseline_is_included(self, container) -> None:
        await container.startup()
        with _client(container) as client:
            response = client.get("/api/v3/admin/scorecard")
        body = response.json()
        assert "docs_corpus" in body["retrieval"]
        assert "recall_at_k" in body["retrieval"]["docs_corpus"]

    async def test_window_hours_is_configurable(self, container) -> None:
        await container.startup()
        with _client(container) as client:
            response = client.get("/api/v3/admin/scorecard", params={"hours": 1})
        assert response.status_code == 200
        assert response.json()["window_hours"] == 1

    async def test_a_non_admin_caller_is_refused(self, container) -> None:
        await container.startup()
        with _client(container, role="User") as client:
            response = client.get("/api/v3/admin/scorecard")
        assert response.status_code == 403
