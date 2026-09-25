"""``infrastructure/secret_hydration.py`` -- populating ``AURA_*`` env vars from Docker
``*_FILE`` secrets before settings are read. Covers the two additions the per-user GitHub OAuth
connect flow needed: a plain secret (``AURA_SECRETS_ENCRYPTION_KEY``) and an indirected one
(``AURA_GITHUB_OAUTH_CLIENT_SECRET``, named by ``AURA_GITHUB_OAUTH_CLIENT_SECRET_ENV``), the same
shape ``_hydrate_provider_api_keys`` already uses for LLM provider keys.
"""

from __future__ import annotations

import os

import pytest

from infrastructure.secret_hydration import hydrate_secrets_from_files

_ENV_KEYS = (
    "AURA_SECRETS_ENCRYPTION_KEY",
    "AURA_SECRETS_ENCRYPTION_KEY_FILE",
    "AURA_DATA_ENCRYPTION_KEYS",
    "AURA_DATA_ENCRYPTION_KEYS_FILE",
    "AURA_GITHUB_OAUTH_CLIENT_SECRET",
    "AURA_GITHUB_OAUTH_CLIENT_SECRET_FILE",
    "AURA_GITHUB_OAUTH_CLIENT_SECRET_ENV",
    "MY_CUSTOM_GITHUB_SECRET",
    "MY_CUSTOM_GITHUB_SECRET_FILE",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_secrets_encryption_key_hydrated_from_file(monkeypatch, tmp_path) -> None:
    secret_file = tmp_path / "secrets_encryption_key"
    secret_file.write_text("a-fernet-key\n")
    monkeypatch.setenv("AURA_SECRETS_ENCRYPTION_KEY_FILE", str(secret_file))

    hydrate_secrets_from_files()

    assert os.environ["AURA_SECRETS_ENCRYPTION_KEY"] == "a-fernet-key"


def test_secrets_encryption_key_env_value_wins_over_file(monkeypatch, tmp_path) -> None:
    secret_file = tmp_path / "secrets_encryption_key"
    secret_file.write_text("from-file")
    monkeypatch.setenv("AURA_SECRETS_ENCRYPTION_KEY_FILE", str(secret_file))
    monkeypatch.setenv("AURA_SECRETS_ENCRYPTION_KEY", "from-env")

    hydrate_secrets_from_files()

    assert os.environ["AURA_SECRETS_ENCRYPTION_KEY"] == "from-env"


def test_data_encryption_keys_hydrated_from_a_multi_key_file(monkeypatch, tmp_path) -> None:
    """The data-at-rest key file may hold several keys, newest first, one per line (rotation).
    Hydration keeps the inner newline; ``data_encryption_keys()`` splits on it."""
    from aura_core.config.settings import data_encryption_keys

    secret_file = tmp_path / "data_encryption_keys"
    secret_file.write_text("new-key\nold-key\n")
    monkeypatch.setenv("AURA_DATA_ENCRYPTION_KEYS_FILE", str(secret_file))

    hydrate_secrets_from_files()

    assert data_encryption_keys() == ["new-key", "old-key"]


def test_github_oauth_client_secret_hydrated_from_default_target(monkeypatch, tmp_path) -> None:
    """No ``_ENV`` override set -- hydration falls back to the default target name
    ``GitHubSettings.oauth_client_secret_env`` itself defaults to."""
    secret_file = tmp_path / "github_oauth_client_secret"
    secret_file.write_text("ghp_client_secret")
    monkeypatch.setenv("AURA_GITHUB_OAUTH_CLIENT_SECRET_FILE", str(secret_file))

    hydrate_secrets_from_files()

    assert os.environ["AURA_GITHUB_OAUTH_CLIENT_SECRET"] == "ghp_client_secret"


def test_github_oauth_client_secret_follows_custom_env_indirection(monkeypatch, tmp_path) -> None:
    secret_file = tmp_path / "custom_secret"
    secret_file.write_text("custom-value")
    monkeypatch.setenv("AURA_GITHUB_OAUTH_CLIENT_SECRET_ENV", "MY_CUSTOM_GITHUB_SECRET")
    monkeypatch.setenv("MY_CUSTOM_GITHUB_SECRET_FILE", str(secret_file))

    hydrate_secrets_from_files()

    assert os.environ["MY_CUSTOM_GITHUB_SECRET"] == "custom-value"
    # Never written under the un-redirected default name.
    assert "AURA_GITHUB_OAUTH_CLIENT_SECRET" not in os.environ


def test_github_oauth_client_secret_is_a_noop_without_a_file(monkeypatch) -> None:
    hydrate_secrets_from_files()
    assert "AURA_GITHUB_OAUTH_CLIENT_SECRET" not in os.environ
