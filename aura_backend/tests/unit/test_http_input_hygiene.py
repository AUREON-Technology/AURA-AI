"""F43: request fields a client controls must not mint metric series or turn into 500s.

* The HTTP method is a client-chosen token (any RFC 9110 token parses), so labelling metrics with
  it raw lets a scanner create one series per made-up verb. It is allow-listed, the rest "OTHER".
* A malformed ``session_id`` or ``X-Correlation-Id`` raised ``ValueError`` from the identifier's
  own validation inside ``to_turn`` -- a 500 for what is a bad request.
"""

from __future__ import annotations

import pytest

from aura_core.api.http_metrics import HttpMetricsMiddleware, method_label

pytest.importorskip("fastapi")


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def observe_http(self, method, route, status, seconds) -> None:
        self.calls.append((method, route, status))


async def _call(method: str) -> list[tuple]:
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 405, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    recorder = _Recorder()
    middleware = HttpMetricsMiddleware(app, metrics=recorder)

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(_message):
        return None

    await middleware({"type": "http", "method": method, "path": "/x"}, receive, send)
    return recorder.calls


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
async def test_known_methods_keep_their_label(method) -> None:
    assert (await _call(method))[0][0] == method


@pytest.mark.parametrize("method", ["BREW", "PROPFIND", "X" * 200, "get", ""])
async def test_any_other_method_is_one_label(method) -> None:
    assert (await _call(method))[0][0] == "OTHER"


def test_method_label_is_total() -> None:
    assert method_label({}) == "OTHER"
    assert method_label({"method": None}) == "OTHER"


@pytest.fixture
def client(container):
    from fastapi.testclient import TestClient

    from aura_core.api import Principal, build_app
    from aura_core.domain.identifiers import UserId

    async def authenticator(request):
        return Principal(user_id=UserId("hygiene"), role="Developer")

    with TestClient(build_app(container, authenticator=authenticator), raise_server_exceptions=False) as c:
        yield c


def test_a_malformed_session_id_is_422_not_500(client) -> None:
    response = client.post("/api/v3/chat", json={"message": "hi", "session_id": "bad id/../x"})
    assert response.status_code == 422, response.text
    assert response.json()["error"] == "validation_error"


def test_a_malformed_correlation_id_is_422_not_500(client) -> None:
    response = client.post(
        "/api/v3/chat", json={"message": "hi"}, headers={"x-correlation-id": "has spaces <script>"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"] == "validation_error"


def test_the_stream_refuses_a_malformed_session_id_the_same_way(client) -> None:
    response = client.post("/api/v3/chat/stream", json={"message": "hi", "session_id": "a b"})
    assert response.status_code == 422, response.text


async def test_live_voice_refuses_a_malformed_session_id_as_a_bad_request(container) -> None:
    """The voice socket's start frame: a validation error and 4400, not an internal error."""
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from aura_core.adapters.in_memory_speech import InMemoryStt, InMemoryTts
    from aura_core.api import Principal, build_app
    from aura_core.domain.identifiers import UserId
    from aura_core.voice import LiveVoiceConfig

    container.live_voice = {
        "enabled": True, "config": LiveVoiceConfig(), "stt": InMemoryStt(default="x"),
        "tts": InMemoryTts(), "nlp": container.nlp, "max_sessions": 2,
        "kernel": container.kernel, "cache": container.cache_backend,
    }

    async def authenticator(request):
        return Principal(user_id=UserId("speaker"))

    await container.startup()
    with TestClient(build_app(container, authenticator=authenticator)) as tc:
        with pytest.raises(WebSocketDisconnect) as closed:
            with tc.websocket_connect("/api/v3/voice/live") as ws:
                ws.send_json({"type": "auth", "token": "t"})
                ws.send_json({"type": "start", "session_id": "not valid/..", "language": "en"})
                frame = ws.receive_json()
                assert frame["type"] == "error" and frame["error"] == "validation"
                ws.receive_json()
        assert closed.value.code == 4400
