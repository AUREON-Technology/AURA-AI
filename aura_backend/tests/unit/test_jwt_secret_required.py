"""Staging/production refuses to start without a JWT signing secret.

Without one, ``services/jwt_auth.py`` signs with a random secret generated per process. Behind
``--workers 4`` that is four different keys: a token one worker issued is rejected by the other
three, so sign-in fails at random and every restart signs everyone out.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import aura_service
import services.jwt_auth as jwt_auth
from aura_core.domain.errors import ConfigurationError


def _settings(production_like: bool):
    return SimpleNamespace(environment=SimpleNamespace(is_production_like=production_like))


@pytest.fixture
def no_secret(monkeypatch):
    monkeypatch.setattr(jwt_auth.settings, "jwt_secret", "", raising=False)
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.delenv("AURA_JWT_SECRET", raising=False)


def test_production_without_a_secret_refuses_to_start(no_secret) -> None:
    with pytest.raises(ConfigurationError, match="JWT signing secret"):
        aura_service._require_jwt_secret(_settings(production_like=True))


def test_production_with_a_secret_starts(no_secret, monkeypatch) -> None:
    monkeypatch.setenv("AURA_JWT_SECRET", "x" * 64)
    aura_service._require_jwt_secret(_settings(production_like=True))


def test_a_short_secret_warns_but_starts(no_secret, monkeypatch, caplog) -> None:
    monkeypatch.setenv("AURA_JWT_SECRET", "short-secret")
    aura_service._require_jwt_secret(_settings(production_like=True))
    assert any("bytes" in r.getMessage() for r in caplog.records)


def test_local_development_keeps_the_random_fallback(no_secret) -> None:
    aura_service._require_jwt_secret(_settings(production_like=False))
