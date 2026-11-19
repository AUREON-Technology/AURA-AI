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


# --- INT-112: routes whose advertised limits sit above the flat 5MB cap -----------------------

def _gateway_limited_app():
    from types import SimpleNamespace

    from aura_core.config.settings import KnowledgeSettings
    from aura_service import _apply_http_limits

    app = FastAPI()

    for path in ("/api/v3/knowledge/documents", "/api/v3/knowledge/documents/upload",
                 "/api/v3/knowledge/documents/doc-1/cag-pin", "/api/v3/voice/transcribe"):
        async def sink(request: Request) -> dict:
            return {"size": len(await request.body())}

        app.post(path)(sink)

    knowledge = KnowledgeSettings()
    _apply_http_limits(app, SimpleNamespace(knowledge=knowledge, vision=_vision(False)))
    return TestClient(app), knowledge


def test_a_text_document_the_engine_accepts_is_not_refused_at_the_gateway() -> None:
    """The JSON ingest route carries up to ``max_document_bytes`` of text; the flat 5MB cap
    refused 5-8MB documents the knowledge engine would have stored."""
    import json

    client, knowledge = _gateway_limited_app()
    content = "a" * (knowledge.max_document_bytes - 16)
    body = json.dumps({"title": "t", "content": content}).encode()
    assert client.post("/api/v3/knowledge/documents", content=body).status_code == 200
    # Still bounded: far past the document ceiling is refused before it is read.
    huge = b"x" * (3 * knowledge.max_document_bytes)
    assert client.post("/api/v3/knowledge/documents", content=huge).status_code == 413


def test_transcribe_accepts_the_audio_size_it_advertises() -> None:
    from aura_core.api.v3 import MAX_AUDIO_BYTES

    client, _knowledge = _gateway_limited_app()
    assert client.post("/api/v3/voice/transcribe", content=b"x" * MAX_AUDIO_BYTES).status_code == 200
    assert client.post("/api/v3/voice/transcribe", content=b"x" * (MAX_AUDIO_BYTES + 4 * 1024 * 1024)).status_code == 413


def test_the_wider_document_ceiling_does_not_leak_to_its_sub_routes() -> None:
    client, knowledge = _gateway_limited_app()
    six_mb = b"x" * (6 * 1024 * 1024)
    assert client.post("/api/v3/knowledge/documents/doc-1/cag-pin", content=six_mb).status_code == 413
    # The upload route keeps its own (narrower) ceiling, not the JSON route's.
    over_upload = b"x" * (knowledge.max_document_bytes + 2 * 1024 * 1024)
    assert client.post("/api/v3/knowledge/documents/upload", content=over_upload).status_code == 413


def test_ingest_request_content_is_bounded_like_the_engine(container) -> None:
    """The JSON model refuses text no engine would store, before hashing or reserving it."""
    from aura_core.api import build_app

    async def authenticator(request):  # pragma: no cover - not called
        raise AssertionError

    schema = build_app(container, authenticator=authenticator).openapi()
    content = schema["components"]["schemas"]["IngestRequest"]["properties"]["content"]
    assert content["maxLength"] == container.settings.knowledge.max_document_bytes


# --- INT-118: a browser may read Retry-After -------------------------------------------------

def test_cors_exposes_retry_after_to_web_clients(monkeypatch) -> None:
    import config
    from aura_service import _apply_cors

    monkeypatch.setattr(config.settings, "allowed_origins", "https://app.example")
    app = FastAPI()

    @app.get("/limited")
    async def limited():
        from fastapi.responses import JSONResponse

        return JSONResponse({"error": "rate_limited"}, status_code=429, headers={"Retry-After": "7"})

    _apply_cors(app)
    response = TestClient(app).get("/limited", headers={"Origin": "https://app.example"})
    exposed = {h.strip().lower() for h in response.headers["access-control-expose-headers"].split(",")}
    assert "retry-after" in exposed
    assert "x-aura-refusal" in exposed
