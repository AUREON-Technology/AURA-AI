"""CORS defaults must include local Flutter web, not only bare localhost -- in a local environment.
In a production-like one the default is empty (audit FSA-010; tests/unit/test_compose_security_parity.py)."""

from __future__ import annotations

from config import Settings


def test_cors_default_includes_flutter_web_ports(monkeypatch):
    monkeypatch.setenv("AURA_ENVIRONMENT", "local")
    monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
    default = Settings().allowed_origins
    assert "http://localhost:8080" in default
    assert "http://127.0.0.1:8080" in default
    assert "http://localhost:3000" in default
    assert "*" not in default
