import os

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings

from aura_core.config.settings import Environment
from aura_core.domain.errors import ConfigurationError


def native_ollama_base_url(configured_host: str = "") -> str:
    """Native Ollama API origin (no OpenAI ``/v1`` suffix).

    Production compose already sets ``AURA_PROVIDER_LOCAL_BASE_URL`` to the
    OpenAI-compatible endpoint on the compose service (``http://ollama:11434/v1``).
    The legacy client and ``/api/tags`` health probe talk to the native API, so
    that URL is reused with ``/v1`` stripped rather than falling back to
    localhost — which is unreachable from a Docker service.
    """
    provider = os.environ.get("AURA_PROVIDER_LOCAL_BASE_URL", "").strip()
    if provider:
        host = provider.rstrip("/")
        if host.endswith("/v1"):
            host = host[:-3].rstrip("/")
        return host
    host = (configured_host or "").strip() or "http://localhost:11434"
    return host.rstrip("/")


def environment_is_production_like() -> bool:
    """Same staging/production gate v3 ``Environment.is_production_like`` uses."""
    raw = (
        os.environ.get("AURA_ENVIRONMENT")
        or os.environ.get("ENVIRONMENT")
        or "local"
    ).strip().lower()
    try:
        return Environment(raw).is_production_like
    except ValueError:
        return False


def debug_bypass_allowed(debug_mode: bool) -> bool:
    """Mock-auth / attestation bypass is only for tests and local/dev.

    Production-like environments ignore ``DEBUG_MODE`` even if someone flipped
    the singleton after boot. ``TESTING=True`` remains the unittest escape hatch.
    """
    if os.environ.get("TESTING") == "True":
        return True
    if environment_is_production_like():
        return False
    return bool(debug_mode)


class Settings(BaseSettings):
    port: int = 8000
    # `gemini_api_key` used to live here. Removed with the provider: a settings field for a
    # banned API is an invitation to wire it back up, and the /health endpoint was reporting
    # `gemini: {configured: ...}` off the back of it long after nothing read the value.
    qdrant_host: str = ""
    qdrant_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("QDRANT_API_KEY", "AURA_QDRANT_API_KEY"),
    )
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "llama3"
    local_embedding_model: str = "all-MiniLM-L6-v2"
    # Native Android/iOS ignore CORS. Flutter web (local :8080 and production
    # https://$AURA_DOMAIN) does not. Compose overrides this with ALLOWED_ORIGINS.
    allowed_origins: str = (
        "http://localhost,http://127.0.0.1,"
        "http://localhost:8080,http://127.0.0.1:8080"
    )
    
    # Production Authentication Settings
    google_client_id: str = "87789110323-5picq501c0834g6g0amjqqpk628jaoh2.apps.googleusercontent.com"
    # Must match aura_client/android/app/build.gradle.kts `applicationId`.
    # Play Integrity rejects any token whose requestDetails.packageName differs,
    # so a drift between these two values silently breaks all Android auth —
    # single-sourced here rather than hardcoded at the call sites.
    android_package_name: str = "com.auraai.aura_client"
    # Must match aura_client/ios PRODUCT_BUNDLE_IDENTIFIER.
    ios_bundle_id: str = Field(
        default="com.auraai.auraClient",
        validation_alias=AliasChoices("IOS_BUNDLE_ID", "AURA_IOS_BUNDLE_ID"),
    )
    # Apple Developer Team ID used as the App Attest app ID prefix (TEAMID.bundle).
    apple_team_id: str = Field(
        default="",
        validation_alias=AliasChoices("APPLE_TEAM_ID", "AURA_APPLE_TEAM_ID"),
    )
    firebase_service_account_path: str = "credentials.json"
    firebase_credentials_json: str = ""
    debug_mode: bool = False
    # Env: ALLOW_MOCK_ATTESTATION. Default False for production safety.
    # When true, verify_device_integrity accepts only the known mock-token
    # allowlist — not arbitrary tokens, and not an auth bypass (use DEBUG_MODE
    # for that, which must stay false in production).
    allow_mock_attestation: bool = False

    # V2: Postgres is the relational source of truth for tasks/calendar/
    # finance/health/memories (see Doc/V2_BLUEPRINT.md §6). Empty by default
    # so the app still boots without Postgres configured — the /api/v2/sync
    # router degrades to a clear 503 rather than crashing at import time.
    database_url: str = Field(
        default="",
        validation_alias=AliasChoices("DATABASE_URL", "AURA_DATABASE_URL"),
    )

    # V2: signs access/refresh JWTs issued by /api/v2/auth/token (§9).
    # MUST be set to a stable, random value in production — if left empty,
    # services/jwt_auth.py generates a random one at process startup as a
    # dev-mode convenience, which invalidates every session on each
    # restart/redeploy (acceptable for local dev, not for production).
    # Compose hydrates AURA_JWT_SECRET from the Docker secret file; accept
    # that name too so v2 jwt_auth and v3 share one signing key across workers.
    jwt_secret: str = Field(
        default="",
        validation_alias=AliasChoices("JWT_SECRET", "AURA_JWT_SECRET"),
    )

    # Writable directory for the JSON-file state the learning pipeline keeps
    # (feedback logs, compiled LoRA datasets, telemetry). Defaults to the app
    # working directory, which is INSIDE the container image — anything written
    # there is destroyed by `docker compose up --force-recreate`. Point this at
    # a mounted volume in any deployment where the feedback corpus matters.
    data_dir: str = Field(
        default=".",
        validation_alias=AliasChoices("DATA_DIR", "AURA_DATA_DIR"),
    )

    # V2 observability (§10). Both no-op when unset — safe defaults for
    # local dev, wire real values in the target deploy environment.
    sentry_dsn: str = ""
    otel_exporter_otlp_endpoint: str = ""
    otel_service_name: str = "aura-backend"
    log_format: str = "text"  # "json" for structured logs (Cloud Run/any log aggregator)

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"
        populate_by_name = True

    @model_validator(mode="after")
    def _refuse_debug_mode_in_production(self) -> "Settings":
        if self.debug_mode and environment_is_production_like() and os.environ.get("TESTING") != "True":
            raise ConfigurationError(
                "DEBUG_MODE must be false in staging/production",
                details={"problems": ["debug_mode must be false in staging/production"]},
            )
        return self

settings = Settings()

