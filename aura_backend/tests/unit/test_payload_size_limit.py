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


# --- Chat with images (ADR-031) --------------------------------------------------------------
# /capabilities advertises max_image_bytes / max_request_image_bytes; the images go base64 in
# the JSON body, so the chat routes need a ceiling above the flat 5MB cap, or a typical phone
# photo was refused with a 413 the vision engine would have accepted.

def _vision(enabled: bool = True):
    from dataclasses import replace

    from aura_core.config.settings import VisionSettings

    return replace(VisionSettings(), enabled=enabled)


def test_chat_keeps_the_default_cap_while_vision_is_off() -> None:
    from aura_service import chat_body_limit

    assert chat_body_limit(_vision(enabled=False)) is None


def test_chat_ceiling_fits_every_image_the_server_advertises_once_encoded() -> None:
    from aura_service import chat_body_limit

    vision = _vision()
    limit = chat_body_limit(vision)
    assert limit is not None
    assert limit >= vision.max_request_image_bytes * 4 / 3, "base64 of the advertised maximum must fit"


def test_chat_ceiling_with_the_defaults_stays_under_nginx() -> None:
    import re
    from pathlib import Path

    from aura_service import chat_body_limit

    template = Path(__file__).resolve().parents[3] / "deploy/nginx/templates/aura.conf.template"
    sizes = {int(m) for m in re.findall(r"client_max_body_size\s+(\d+)m;", template.read_text())}
    assert sizes, "nginx body cap not found"
    assert chat_body_limit(_vision()) < min(sizes) * 1024 * 1024


def test_the_gateway_applies_the_chat_ceiling_only_with_vision_on() -> None:
    from types import SimpleNamespace

    from aura_core.config.settings import KnowledgeSettings
    from aura_service import _apply_http_limits

    for enabled in (True, False):
        app = FastAPI()

        @app.post("/api/v3/chat/stream")
        async def stream(request: Request) -> dict:
            return {"size": len(await request.body())}

        _apply_http_limits(app, SimpleNamespace(knowledge=KnowledgeSettings(), vision=_vision(enabled)))
        six_mb = b"x" * (6 * 1024 * 1024)
        status = TestClient(app).post("/api/v3/chat/stream", content=six_mb).status_code
        assert status == (200 if enabled else 413), enabled
