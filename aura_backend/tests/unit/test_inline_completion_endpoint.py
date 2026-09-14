"""D6 Part D: ``POST /api/v3/complete``, the inline code-completion endpoint.

This bypasses ``AIKernel`` entirely and calls ``container.router.complete(...)`` directly, so it
is tested through the real ASGI app (like ``test_api_surface.py``) rather than by calling the
handler function, to catch wiring mistakes (route registration, request validation, response
shape) that a direct call would hide.

``require_local=True`` is unconditional in this endpoint (D6 plan Part D, matching the
self-hosting invariant every other completion call respects). The default ``container`` fixture's
fallback echo provider is *not* marked ``is_local`` (``LLMSettings.ECHO_PROVIDER`` leaves it at
its dataclass default of ``False`` -- only a real deploy's env vars set it ``true``, per
``deploy/docker-compose.prod.yml``), so a plain ``container`` fixture would make every request
here fail with 503. This module builds its own container with the echo provider declared local,
the same way a real deployment's ``AURA_PROVIDER_<NAME>_IS_LOCAL=true`` does for its inference
node.
"""

from __future__ import annotations

import pytest

from aura_core.api import Principal, build_app
from aura_core.config.settings import load_settings
from aura_core.container import Container
from aura_core.domain.identifiers import UserId
from aura_core.llm.providers.echo import EchoProvider, ScriptedTurn

pytest.importorskip("fastapi")


@pytest.fixture
def local_echo_container(monkeypatch):
    """A container whose sole provider is an ``EchoProvider`` declared local, so
    ``require_local=True`` requests -- what ``/complete`` always sends -- actually route
    somewhere instead of raising ``AllProvidersFailedError``."""
    monkeypatch.setenv("AURA_LLM_PROVIDERS", "echo")
    monkeypatch.setenv("AURA_PROVIDER_ECHO_KIND", "echo")
    monkeypatch.setenv("AURA_PROVIDER_ECHO_IS_LOCAL", "true")
    return Container(load_settings(refresh=True, validate=False))


@pytest.fixture
def echo(local_echo_container) -> EchoProvider:
    provider = local_echo_container.providers[0]
    assert isinstance(provider, EchoProvider)
    return provider


def _client(container):
    from fastapi.testclient import TestClient

    async def authenticator(request):
        return Principal(user_id=UserId("test-user"), email="u@example.com", role="User")

    return TestClient(build_app(container, authenticator=authenticator, rate_limiter=None))


class TestInlineCompletion:
    def test_a_scripted_completion_round_trips(self, local_echo_container, echo) -> None:
        echo.queue(ScriptedTurn(text="return a + b"))

        with _client(local_echo_container) as client:
            response = client.post(
                "/api/v3/complete",
                json={
                    "prefix": "def add(a, b):\n    ",
                    "suffix": "\n",
                    "language": "python",
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["text"] == "return a + b"
        assert body["provider"] == "echo"
        assert body["truncated"] is False

        # The prompt handed to the provider is the actual contract this endpoint has with the
        # model -- assert on it directly rather than trusting the response alone, so a future
        # change to the fill-in-the-middle template shows up here.
        assert len(echo.calls) == 1
        sent = echo.calls[0].conversation.last_user_message.content
        assert "<CURSOR>" in sent
        assert "def add(a, b):" in sent

    def test_a_fenced_response_is_stripped_of_its_code_fence(self, local_echo_container, echo) -> None:
        echo.queue(ScriptedTurn(text="```python\nreturn a + b\n```"))

        with _client(local_echo_container) as client:
            response = client.post("/api/v3/complete", json={"prefix": "def add(a, b):\n    "})

        assert response.status_code == 200
        assert response.json()["text"] == "return a + b"

    def test_default_completion_request_needs_no_fields(self, local_echo_container, echo) -> None:
        """Every field is optional -- a client can always send an empty object and get the
        model's unprompted completion rather than a 422."""
        echo.queue(ScriptedTurn(text="pass"))

        with _client(local_echo_container) as client:
            response = client.post("/api/v3/complete", json={})

        assert response.status_code == 200
        assert response.json()["text"] == "pass"

    def test_an_oversized_prefix_is_rejected_before_reaching_the_model(self, local_echo_container, echo) -> None:
        with _client(local_echo_container) as client:
            response = client.post("/api/v3/complete", json={"prefix": "x" * 8_001})

        assert response.status_code == 422
        assert echo.call_count == 0

    def test_a_provider_failure_becomes_a_typed_503_not_a_500(self, local_echo_container, echo) -> None:
        echo.queue(ScriptedTurn(raise_error=RuntimeError("model process crashed")))

        with _client(local_echo_container) as client:
            response = client.post("/api/v3/complete", json={"prefix": "def add(a, b):\n    "})

        assert response.status_code == 503
        assert response.json()["error"] == "completion_unavailable"

    def test_requires_authentication_like_every_other_v3_route(self, local_echo_container) -> None:
        from fastapi.testclient import TestClient

        from aura_core.domain.errors import AuthenticationError

        async def refusing(request):
            raise AuthenticationError("token has expired")

        client = TestClient(build_app(local_echo_container, authenticator=refusing, rate_limiter=None))
        response = client.post("/api/v3/complete", json={"prefix": "x"})

        assert response.status_code == 401
