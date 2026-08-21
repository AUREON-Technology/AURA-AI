"""Legacy config.py must see the AURA_* secrets compose hydrates.

Production compose injects AURA_JWT_SECRET_FILE / AURA_DATABASE_URL_FILE.
aura_service hydrates those into AURA_* env vars. jwt_auth and /api/v2/sync
read config.Settings, which historically only looked at JWT_SECRET /
DATABASE_URL — so every uvicorn worker minted a random JWT secret and sync
raised 500 (DATABASE_URL unset) even though the Docker secrets were present.
"""
from __future__ import annotations

import pytest

from config import Settings


@pytest.fixture
def clean_legacy_env(monkeypatch):
    for key in (
        "JWT_SECRET",
        "AURA_JWT_SECRET",
        "DATABASE_URL",
        "AURA_DATABASE_URL",
        "QDRANT_API_KEY",
        "AURA_QDRANT_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def test_settings_reads_aura_jwt_secret(clean_legacy_env):
    clean_legacy_env.setenv("AURA_JWT_SECRET", "from-aura-prefix")
    settings = Settings(_env_file=None)
    assert settings.jwt_secret == "from-aura-prefix"


def test_settings_prefers_unprefixed_jwt_secret(clean_legacy_env):
    clean_legacy_env.setenv("JWT_SECRET", "unprefixed")
    clean_legacy_env.setenv("AURA_JWT_SECRET", "aura-prefixed")
    settings = Settings(_env_file=None)
    assert settings.jwt_secret == "unprefixed"


def test_settings_reads_aura_database_url(clean_legacy_env):
    clean_legacy_env.setenv("AURA_DATABASE_URL", "postgresql+asyncpg://aura@postgres/aura")
    settings = Settings(_env_file=None)
    assert settings.database_url == "postgresql+asyncpg://aura@postgres/aura"
