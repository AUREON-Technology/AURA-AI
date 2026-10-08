"""CORS defaults must include local Flutter web, not only bare localhost."""

from __future__ import annotations

from config import Settings


def test_cors_default_includes_flutter_web_ports():
    default = Settings.model_fields["allowed_origins"].default
    assert "http://localhost:8080" in default
    assert "http://127.0.0.1:8080" in default
    assert "http://localhost:3000" in default
    assert "*" not in default
