"""Legacy Ollama client must use the compose provider URL, not localhost.

``/health`` probes this client. Inside Docker, ``127.0.0.1:11434`` is the
gateway container itself — the daemon is the ``ollama`` service, already
configured as ``AURA_PROVIDER_LOCAL_BASE_URL``.
"""

from __future__ import annotations

import httpx
import pytest

from config import native_ollama_base_url
from services.ollama import OllamaService


def test_provider_base_url_strips_openai_v1_suffix(monkeypatch):
    monkeypatch.setenv("AURA_PROVIDER_LOCAL_BASE_URL", "http://ollama:11434/v1")
    assert native_ollama_base_url("http://localhost:11434") == "http://ollama:11434"


def test_provider_base_url_strips_trailing_slash(monkeypatch):
    monkeypatch.setenv("AURA_PROVIDER_LOCAL_BASE_URL", "http://ollama:11434/v1/")
    assert native_ollama_base_url() == "http://ollama:11434"


def test_falls_back_to_configured_host_when_provider_unset(monkeypatch):
    monkeypatch.delenv("AURA_PROVIDER_LOCAL_BASE_URL", raising=False)
    assert native_ollama_base_url("http://localhost:11434") == "http://localhost:11434"


def test_service_uses_compose_host_not_loopback(monkeypatch):
    monkeypatch.setenv("AURA_PROVIDER_LOCAL_BASE_URL", "http://ollama:11434/v1")
    service = OllamaService()
    assert service.host == "http://ollama:11434"
    assert "localhost" not in service.host
    assert "127.0.0.1" not in service.host


@pytest.mark.asyncio
async def test_health_probes_api_tags_on_compose_host(monkeypatch):
    monkeypatch.setenv("AURA_PROVIDER_LOCAL_BASE_URL", "http://ollama:11434/v1")
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"models": []})

    service = OllamaService()
    original = httpx.AsyncClient

    class _Client(original):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    assert await service.check_health() is True
    assert seen["url"].startswith("http://ollama:11434/api/tags")
