"""Populate ``AURA_*`` settings from Docker ``*_FILE`` secrets (SEC-08).

The gateway entrypoint (``aura_service``) and the Celery worker (``celery_app``)
both import this module rather than each other: importing ``aura_service`` from
a worker would construct the FastAPI app as a side effect.
"""

from __future__ import annotations

import os

__all__ = ["hydrate_secrets_from_files"]


def _read_secret_file(env_var: str) -> str | None:
    path = os.environ.get(f"{env_var}_FILE")
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as handle:
        return handle.read().strip()


def hydrate_secrets_from_files() -> None:
    """Populate settings variables from their ``*_FILE`` counterparts.

    Secrets arrive as files rather than environment values so they are not
    visible in ``docker inspect``, process listings, or a crash dump's
    environment block.
    """
    password = _read_secret_file("AURA_DATABASE_URL")
    if password and not os.environ.get("AURA_DATABASE_URL"):
        host = os.environ.get("POSTGRES_HOST", "postgres")
        os.environ["AURA_DATABASE_URL"] = (
            f"postgresql+asyncpg://aura:{password}@{host}:5432/aura"
        )

    redis_password = _read_secret_file("AURA_REDIS_PASSWORD")
    if redis_password and not os.environ.get("AURA_REDIS_URL"):
        host = os.environ.get("REDIS_HOST", "redis")
        os.environ["AURA_REDIS_URL"] = f"redis://:{redis_password}@{host}:6379/0"

    # AURA_DATA_ENCRYPTION_KEYS may hold several keys, one per line (newest first) -- the file's
    # internal newlines survive strip() and aura_core splits on them (config.settings).
    for variable in (
        "AURA_QDRANT_API_KEY",
        "AURA_JWT_SECRET",
        "AURA_SECRETS_ENCRYPTION_KEY",
        "AURA_DATA_ENCRYPTION_KEYS",
    ):
        value = _read_secret_file(variable)
        if value and not os.environ.get(variable):
            os.environ[variable] = value

    _hydrate_provider_api_keys()
    _hydrate_github_oauth_client_secret()
    _mirror_legacy_secret_env()


def _hydrate_provider_api_keys() -> None:
    """Hydrate any LLM provider's API key from a Docker secret file.

    ``AURA_PROVIDER_<NAME>_API_KEY_ENV`` names *another* env var that holds the key
    (``ProviderConfig.api_key`` reads it lazily -- see aura_core/config/settings.py). Generic over
    provider name so a new provider needs no change here, matching the "add a provider = config
    change" property the rest of the LLM settings already have.
    """
    for key, target in list(os.environ.items()):
        if not (key.startswith("AURA_PROVIDER_") and key.endswith("_API_KEY_ENV")):
            continue
        target = target.strip()
        if not target:
            continue
        value = _read_secret_file(target)
        if value and not os.environ.get(target):
            os.environ[target] = value


def _hydrate_github_oauth_client_secret() -> None:
    """Hydrate the per-user GitHub OAuth connect flow's client secret from a Docker secret file.

    ``AURA_GITHUB_OAUTH_CLIENT_SECRET_ENV`` names *another* env var that holds the secret
    (``GitHubSettings.oauth_client_secret_env`` -- aura_core/config/settings.py), the same
    one-more-indirection-for-a-secret-file shape ``_hydrate_provider_api_keys`` uses for LLM
    provider keys. Defaults to ``AURA_GITHUB_OAUTH_CLIENT_SECRET`` itself when unset, matching
    ``GitHubSettings``'s own default, so a deployment that never overrides the env var name still
    gets file-backed hydration for it.
    """
    target = os.environ.get(
        "AURA_GITHUB_OAUTH_CLIENT_SECRET_ENV", "AURA_GITHUB_OAUTH_CLIENT_SECRET"
    ).strip()
    if not target:
        return
    value = _read_secret_file(target)
    if value and not os.environ.get(target):
        os.environ[target] = value


def _mirror_legacy_secret_env() -> None:
    """Copy AURA_* secrets onto the unprefixed names legacy Settings expects."""
    for aura_name, legacy_name in (
        ("AURA_JWT_SECRET", "JWT_SECRET"),
        ("AURA_DATABASE_URL", "DATABASE_URL"),
        ("AURA_QDRANT_API_KEY", "QDRANT_API_KEY"),
        ("AURA_REDIS_URL", "REDIS_URL"),
    ):
        value = os.environ.get(aura_name)
        if value and not os.environ.get(legacy_name):
            os.environ[legacy_name] = value
