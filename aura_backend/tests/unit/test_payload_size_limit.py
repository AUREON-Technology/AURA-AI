"""Request-body limits count the bytes that arrive, not the header that claims them.

The middleware used to read only ``Content-Length``. A body sent chunked (no length header) was
never measured, and every route shared one 5MB cap, so documents between 5MB and the knowledge
engine's own 8MB ceiling were refused before reaching it.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from infrastructure.http_limits import PayloadSizeLimitMiddleware

LIMIT = 1024
UPLOAD = "/upload"


def _client() -> TestClient:
    app = FastAPI()

    @app.post("/echo")
    async def echo(request: Request) -> dict:
        return {"size": len(await request.body())}

    @app.post(UPLOAD)
    async def upload(request: Request) -> dict:
        return {"size": len(await request.body())}

    app.add_middleware(
        PayloadSizeLimitMiddleware, max_bytes=LIMIT, route_limits={UPLOAD: 4 * LIMIT}
    )
    return TestClient(app)


def _chunked(total: int):
    def gen():
        sent = 0
        while sent < total:
            piece = min(256, total - sent)
            sent += piece
            yield b"x" * piece

    return gen()


def test_a_body_within_the_limit_passes() -> None:
    response = _client().post("/echo", content=b"x" * LIMIT)
    assert response.status_code == 200
    assert response.json() == {"size": LIMIT}


def test_an_oversized_declared_length_is_refused_before_reading() -> None:
    response = _client().post("/echo", content=b"x" * (LIMIT + 1))
    assert response.status_code == 413


def test_a_chunked_body_without_content_length_is_still_measured() -> None:
    response = _client().post("/echo", content=_chunked(LIMIT * 3))
    assert response.status_code == 413


def test_a_chunked_body_within_the_limit_passes() -> None:
    response = _client().post("/echo", content=_chunked(LIMIT - 10))
    assert response.status_code == 200
    assert response.json() == {"size": LIMIT - 10}


def test_a_route_limit_raises_the_ceiling_for_that_route_only() -> None:
    client = _client()
    assert client.post(UPLOAD, content=b"x" * (3 * LIMIT)).status_code == 200
    assert client.post("/echo", content=b"x" * (3 * LIMIT)).status_code == 413
    assert client.post(UPLOAD, content=_chunked(5 * LIMIT)).status_code == 413
